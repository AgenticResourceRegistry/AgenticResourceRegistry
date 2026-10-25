# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Privacy-bounded collection and delivery of aggregate installation usage."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
from urllib.parse import urlparse

import httpx
from loguru import logger as optic
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

import services.dynamic_settings as ds
from config import settings
from models.agent import Agent
from models.download import AgentDownloadRecord
from models.hook import HookListing
from models.mcp import McpListing
from models.prompt import PromptListing
from models.sandbox import SandboxListing
from models.skill import SkillListing
from models.team import Team
from models.usage_ping import UsagePingState
from models.user import User
from schemas.usage_ping import (
    UsagePingActivity,
    UsagePingCounts,
    UsagePingFrequency,
    UsagePingIdentity,
    UsagePingInstance,
    UsagePingPayload,
    UsagePingStatus,
)
from services.ssrf_guard import is_private_url
from version import get_server_version

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_SCHEMA_VERSION = 2
_STATE_ID = 1
_PRODUCTION_COLLECTOR_URL = "https://usage.observal.io/api/v1/usage-pings"
_LOCAL_COLLECTOR_HOSTS = {"localhost", "127.0.0.1", "telemetry-api"}
_FREQUENCIES: set[str] = {"every_6_hours", "daily", "weekly"}
_WORKER_HOURS = (0, 6, 12, 18)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _scheduled_at_or_before(frequency: UsagePingFrequency, now: datetime) -> datetime:
    current = _as_utc(now)
    if frequency == "weekly":
        candidate = (current - timedelta(days=current.weekday())).replace(hour=6, minute=30, second=0, microsecond=0)
        return candidate if candidate <= current else candidate - timedelta(days=7)
    if frequency == "daily":
        candidate = current.replace(hour=6, minute=30, second=0, microsecond=0)
        return candidate if candidate <= current else candidate - timedelta(days=1)
    for hour in reversed(_WORKER_HOURS):
        candidate = current.replace(hour=hour, minute=30, second=0, microsecond=0)
        if candidate <= current:
            return candidate
    return (current - timedelta(days=1)).replace(hour=18, minute=30, second=0, microsecond=0)


def next_scheduled_at(now: datetime | None = None, *, frequency: UsagePingFrequency = "weekly") -> datetime:
    """Return the next configured delivery boundary in UTC."""
    current = _as_utc(now or datetime.now(UTC))
    if frequency == "weekly":
        days = (7 - current.weekday()) % 7
        candidate = (current + timedelta(days=days)).replace(hour=6, minute=30, second=0, microsecond=0)
        return candidate if candidate > current else candidate + timedelta(days=7)
    if frequency == "daily":
        candidate = current.replace(hour=6, minute=30, second=0, microsecond=0)
        return candidate if candidate > current else candidate + timedelta(days=1)
    for hour in _WORKER_HOURS:
        candidate = current.replace(hour=hour, minute=30, second=0, microsecond=0)
        if candidate > current:
            return candidate
    return (current + timedelta(days=1)).replace(hour=0, minute=30, second=0, microsecond=0)


def _next_worker_at(now: datetime) -> datetime:
    return next_scheduled_at(now, frequency="every_6_hours")


def _next_delivery_at(
    frequency: UsagePingFrequency, last_success_at: datetime | None, now: datetime | None = None
) -> datetime:
    current = _as_utc(now or datetime.now(UTC))
    due_at = _scheduled_at_or_before(frequency, current)
    if last_success_at is None or _as_utc(last_success_at) < due_at:
        return _next_worker_at(current)
    return next_scheduled_at(current, frequency=frequency)


async def _usage_ping_frequency() -> UsagePingFrequency:
    value = (await ds.get("usage_ping.frequency", "every_6_hours")).strip().lower()
    return cast("UsagePingFrequency", value) if value in _FREQUENCIES else "every_6_hours"


def _reporting_week(value: datetime) -> str:
    monday = value.date() - timedelta(days=value.weekday())
    return monday.isoformat()


def fallback_company_name(installation_id: uuid.UUID) -> str:
    """Stable placeholder identity for installations that have not named themselves."""
    return f"unknown-{installation_id.int % 1_000_000:06d}"


def _hostname(public_url: str) -> str:
    # The collector rejects "not-configured" identities, so an unset public URL reports "unknown".
    value = public_url.strip()
    if not value:
        return "unknown"
    parsed = urlparse(value if "://" in value else f"//{value}")
    return (parsed.hostname or "unknown").lower()


def _deployment_type() -> str:
    if settings.USAGE_PING_DEPLOYMENT_TYPE:
        return settings.USAGE_PING_DEPLOYMENT_TYPE
    return "development" if get_server_version() == "dev" else "self-managed"


async def _state(db: AsyncSession) -> UsagePingState:
    state = await db.get(UsagePingState, _STATE_ID)
    if state is None:
        state = UsagePingState(id=_STATE_ID)
        db.add(state)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            state = await db.get(UsagePingState, _STATE_ID)
            if state is None:
                raise
    return state


async def _safe_count(db: AsyncSession, model, *conditions) -> int:
    try:
        statement = select(func.count()).select_from(model)
        if conditions:
            statement = statement.where(*conditions)
        async with db.begin_nested():
            return int(await db.scalar(statement) or 0)
    except Exception as exc:
        optic.warning("usage ping counter failed table={}: {}", model.__tablename__, exc)
        return 0


async def _postgres_counts(db: AsyncSession) -> dict[str, int]:
    return {
        "users": await _safe_count(db, User, User.is_demo.is_(False)),
        "teams": await _safe_count(db, Team, Team.is_personal.is_(False)),
        "agents": await _safe_count(db, Agent, Agent.deleted_at.is_(None)),
        "mcp_servers": await _safe_count(db, McpListing),
        "skills": await _safe_count(db, SkillListing),
        "hooks": await _safe_count(db, HookListing),
        "prompts": await _safe_count(db, PromptListing),
        "sandboxes": await _safe_count(db, SandboxListing),
        "agent_installs": await _safe_count(db, AgentDownloadRecord),
    }


_ACTIVITY_INTEGER_FIELDS = (
    "active_users_7d",
    "active_users_30d",
    "active_agents_7d",
    "active_agents_30d",
    "events_7d",
    "events_30d",
    "prompts_7d",
    "prompts_30d",
    "tool_calls_7d",
    "tool_calls_30d",
    "tool_results_7d",
    "tool_results_30d",
    "input_tokens_7d",
    "input_tokens_30d",
    "output_tokens_7d",
    "output_tokens_30d",
    "cache_read_tokens_7d",
    "cache_read_tokens_30d",
    "cache_write_tokens_7d",
    "cache_write_tokens_30d",
    "sessions_with_tools_30d",
    "sessions_with_tokens_30d",
    "registered_agent_sessions_30d",
    "unregistered_agent_sessions_30d",
    "top_level_sessions_30d",
    "subagent_sessions_30d",
    "distinct_agent_versions_30d",
    "distinct_models_30d",
    "parse_errors_30d",
    "truncated_events_30d",
)


async def _session_metrics() -> tuple[dict[str, int], dict[str, int | float], dict[str, int]]:
    from services.clickhouse.client import _query

    totals = {"sessions_total": 0, "sessions_7d": 0, "sessions_30d": 0}
    activity: dict[str, int | float] = dict.fromkeys(_ACTIVITY_INTEGER_FIELDS, 0)
    activity.update(
        {
            "credits_7d": 0.0,
            "credits_30d": 0.0,
            "average_session_duration_seconds_30d": 0.0,
            "average_prompts_per_session_30d": 0.0,
            "average_tool_calls_per_session_30d": 0.0,
        }
    )
    harnesses: dict[str, int] = {}
    try:
        response = await _query(
            "SELECT "
            "uniqExact(session_id) AS sessions_total, "
            "uniqExactIf(session_id, last_event_time >= now() - INTERVAL 7 DAY) AS sessions_7d, "
            "uniqExactIf(session_id, last_event_time >= now() - INTERVAL 30 DAY) AS sessions_30d, "
            "uniqExactIf(user_id, user_id != '' AND last_event_time >= now() - INTERVAL 7 DAY) AS active_users_7d, "
            "uniqExactIf(user_id, user_id != '' AND last_event_time >= now() - INTERVAL 30 DAY) AS active_users_30d, "
            "uniqExactIf(agent_id, agent_id != '' AND last_event_time >= now() - INTERVAL 7 DAY) AS active_agents_7d, "
            "uniqExactIf(agent_id, agent_id != '' AND last_event_time >= now() - INTERVAL 30 DAY) AS active_agents_30d, "
            "sumIf(event_count, last_event_time >= now() - INTERVAL 7 DAY) AS events_7d, "
            "sumIf(event_count, last_event_time >= now() - INTERVAL 30 DAY) AS events_30d, "
            "sumIf(prompt_count, last_event_time >= now() - INTERVAL 7 DAY) AS prompts_7d, "
            "sumIf(prompt_count, last_event_time >= now() - INTERVAL 30 DAY) AS prompts_30d, "
            "sumIf(tool_call_count, last_event_time >= now() - INTERVAL 7 DAY) AS tool_calls_7d, "
            "sumIf(tool_call_count, last_event_time >= now() - INTERVAL 30 DAY) AS tool_calls_30d, "
            "sumIf(tool_result_count, last_event_time >= now() - INTERVAL 7 DAY) AS tool_results_7d, "
            "sumIf(tool_result_count, last_event_time >= now() - INTERVAL 30 DAY) AS tool_results_30d, "
            "sumIf(input_tokens, last_event_time >= now() - INTERVAL 7 DAY) AS input_tokens_7d, "
            "sumIf(input_tokens, last_event_time >= now() - INTERVAL 30 DAY) AS input_tokens_30d, "
            "sumIf(output_tokens, last_event_time >= now() - INTERVAL 7 DAY) AS output_tokens_7d, "
            "sumIf(output_tokens, last_event_time >= now() - INTERVAL 30 DAY) AS output_tokens_30d, "
            "sumIf(cache_read_tokens, last_event_time >= now() - INTERVAL 7 DAY) AS cache_read_tokens_7d, "
            "sumIf(cache_read_tokens, last_event_time >= now() - INTERVAL 30 DAY) AS cache_read_tokens_30d, "
            "sumIf(cache_write_tokens, last_event_time >= now() - INTERVAL 7 DAY) AS cache_write_tokens_7d, "
            "sumIf(cache_write_tokens, last_event_time >= now() - INTERVAL 30 DAY) AS cache_write_tokens_30d, "
            "sumIf(total_credits, last_event_time >= now() - INTERVAL 7 DAY) AS credits_7d, "
            "sumIf(total_credits, last_event_time >= now() - INTERVAL 30 DAY) AS credits_30d, "
            "sumIf(greatest(dateDiff('second', first_event_time, last_event_time), 0), "
            "last_event_time >= now() - INTERVAL 30 DAY) AS session_duration_seconds_30d, "
            "countIf(last_event_time >= now() - INTERVAL 30 DAY AND tool_call_count > 0) AS sessions_with_tools_30d, "
            "countIf(last_event_time >= now() - INTERVAL 30 DAY AND input_tokens + output_tokens > 0) "
            "AS sessions_with_tokens_30d, "
            "countIf(last_event_time >= now() - INTERVAL 30 DAY AND agent_id != '') "
            "AS registered_agent_sessions_30d, "
            "countIf(last_event_time >= now() - INTERVAL 30 DAY AND agent_id = '') "
            "AS unregistered_agent_sessions_30d, "
            "countIf(last_event_time >= now() - INTERVAL 30 DAY AND parent_session_id = '') "
            "AS top_level_sessions_30d, "
            "countIf(last_event_time >= now() - INTERVAL 30 DAY AND parent_session_id != '') "
            "AS subagent_sessions_30d, "
            "uniqExactIf(agent_version, agent_version != '' AND last_event_time >= now() - INTERVAL 30 DAY) "
            "AS distinct_agent_versions_30d, "
            "uniqExactIf(model, model != '' AND last_event_time >= now() - INTERVAL 30 DAY) "
            "AS distinct_models_30d "
            "FROM session_stats_agg FINAL FORMAT JSON"
        )
        response.raise_for_status()
        rows = response.json().get("data", [])
        if rows:
            row = rows[0]
            totals = {key: max(0, int(row.get(key, 0) or 0)) for key in totals}
            for key in _ACTIVITY_INTEGER_FIELDS:
                if key not in {"parse_errors_30d", "truncated_events_30d"}:
                    activity[key] = max(0, int(row.get(key, 0) or 0))
            activity["credits_7d"] = max(0.0, float(row.get("credits_7d", 0) or 0))
            activity["credits_30d"] = max(0.0, float(row.get("credits_30d", 0) or 0))
            sessions_30d = totals["sessions_30d"]
            if sessions_30d:
                activity["average_session_duration_seconds_30d"] = round(
                    max(0.0, float(row.get("session_duration_seconds_30d", 0) or 0)) / sessions_30d, 2
                )
                activity["average_prompts_per_session_30d"] = round(int(activity["prompts_30d"]) / sessions_30d, 2)
                activity["average_tool_calls_per_session_30d"] = round(
                    int(activity["tool_calls_30d"]) / sessions_30d, 2
                )
    except Exception as exc:
        optic.warning("usage ping session metrics unavailable: {}", exc)

    try:
        response = await _query(
            "SELECT countIf(event_type = '_parse_error') AS parse_errors_30d, "
            "countIf(raw_line_truncated = 1) AS truncated_events_30d "
            "FROM session_events FINAL WHERE timestamp >= now() - INTERVAL 30 DAY FORMAT JSON"
        )
        response.raise_for_status()
        rows = response.json().get("data", [])
        if rows:
            activity["parse_errors_30d"] = max(0, int(rows[0].get("parse_errors_30d", 0) or 0))
            activity["truncated_events_30d"] = max(0, int(rows[0].get("truncated_events_30d", 0) or 0))
    except Exception as exc:
        optic.warning("usage ping ingestion health metrics unavailable: {}", exc)

    try:
        response = await _query(
            "SELECT harness, uniqExact(session_id) AS sessions "
            "FROM session_stats_agg FINAL WHERE harness != '' GROUP BY harness "
            "ORDER BY sessions DESC LIMIT 32 FORMAT JSON"
        )
        response.raise_for_status()
        for row in response.json().get("data", [])[:32]:
            name = str(row.get("harness", ""))[:50]
            if name:
                harnesses[name] = max(0, int(row.get("sessions", 0)))
    except Exception as exc:
        optic.warning("usage ping harness counters unavailable: {}", exc)
    return totals, activity, harnesses


async def build_usage_ping(db: AsyncSession, *, now: datetime | None = None) -> UsagePingPayload:
    """Build the exact aggregate payload shown in preview and sent upstream."""
    sent_at = (now or datetime.now(UTC)).astimezone(UTC)
    state = await _state(db)
    company_name = (await ds.get("usage_ping.company_name")).strip()
    public_url = await ds.get("deployment.public_url")
    postgres_counts = await _postgres_counts(db)
    session_counts, activity, harnesses = await _session_metrics()
    features = {
        "insights": await ds.get_bool("insights.batch_enabled"),
        "retention": await ds.get_bool("retention.enabled"),
        "sso": bool(
            await ds.get("oauth.client_id")
            or await ds.get("saml.idp_entity_id")
            or await ds.get("google.client_id")
            or await ds.get("github.client_id")
        ),
        "trace_privacy": await ds.get_bool("security.trace_privacy"),
        "registered_agents_only": await ds.get_bool("registry.registered_agents_only"),
    }
    ping_id = uuid.uuid5(state.installation_id, _reporting_week(sent_at))
    payload = UsagePingPayload(
        schema_version=_SCHEMA_VERSION,
        ping_id=ping_id,
        installation_id=state.installation_id,
        sent_at=sent_at,
        identity=UsagePingIdentity(
            company_name=company_name or fallback_company_name(state.installation_id),
            hostname=_hostname(public_url),
        ),
        instance=UsagePingInstance(version=get_server_version(), deployment_type=_deployment_type()),
        counts=UsagePingCounts(**postgres_counts, **session_counts),
        activity=UsagePingActivity(**activity),
        features=features,
        harnesses=harnesses,
    )
    await db.commit()
    return payload


async def usage_ping_status(db: AsyncSession) -> UsagePingStatus:
    state = await _state(db)
    enabled = await ds.get_bool("usage_ping.enabled")
    company_name = (await ds.get("usage_ping.company_name")).strip()
    public_url = (await ds.get("deployment.public_url")).strip()
    frequency = await _usage_ping_frequency()
    configured = bool(company_name and public_url)
    await db.commit()
    return UsagePingStatus(
        enabled=enabled,
        configured=configured,
        frequency=frequency,
        collector_url=_collector_url(),
        installation_id=state.installation_id,
        last_attempt_at=state.last_attempt_at,
        last_success_at=state.last_success_at,
        last_error=state.last_error,
        next_scheduled_at=(
            _next_delivery_at(frequency, state.last_success_at) if enabled else next_scheduled_at(frequency=frequency)
        ),
    )


def _collector_url() -> str:
    if settings.USAGE_PING_URL == _PRODUCTION_COLLECTOR_URL:
        return settings.USAGE_PING_URL
    parsed = urlparse(settings.USAGE_PING_URL)
    if settings.USAGE_PING_DEPLOYMENT_TYPE == "development" and parsed.hostname in _LOCAL_COLLECTOR_HOSTS:
        return settings.USAGE_PING_URL
    raise RuntimeError("Usage-ping collector override is only allowed for local development")


async def _deliver_payload(payload: UsagePingPayload) -> httpx.Response:
    """Deliver with bounded retries for transient transport and upstream failures."""
    collector_url = _collector_url()
    parsed = urlparse(collector_url)
    local_development = (
        settings.USAGE_PING_DEPLOYMENT_TYPE == "development" and parsed.hostname in _LOCAL_COLLECTOR_HOSTS
    )
    if not local_development and await asyncio.to_thread(is_private_url, collector_url):
        raise RuntimeError("Usage-ping collector resolves to a private or internal address")

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=False) as client:
                response = await client.post(
                    collector_url,
                    json=payload.model_dump(mode="json"),
                    headers={"User-Agent": f"Observal/{payload.instance.version}"},
                )
                response.raise_for_status()
                return response
        except httpx.HTTPStatusError as exc:
            last_error = exc
            if exc.response.status_code < 500 and exc.response.status_code != 429:
                raise
        except httpx.TransportError as exc:
            last_error = exc
        if attempt < 2:
            await asyncio.sleep(0.25 * (2**attempt))
    if last_error:
        raise last_error
    raise RuntimeError("Usage report delivery failed")


async def send_scheduled_usage_ping(db: AsyncSession, *, now: datetime | None = None) -> str:
    """Send when the administrator-selected schedule is due."""
    if not await ds.get_bool("usage_ping.enabled"):
        return "disabled"
    current = _as_utc(now or datetime.now(UTC))
    frequency = await _usage_ping_frequency()
    state = await _state(db)
    due_at = _scheduled_at_or_before(frequency, current)
    if state.last_success_at is not None and _as_utc(state.last_success_at) >= due_at:
        await db.commit()
        return "not-due"
    return await send_usage_ping(db)


async def send_usage_ping(db: AsyncSession) -> str:
    """Send one usage ping. Installations without a company name report a stable placeholder."""
    if not await ds.get_bool("usage_ping.enabled"):
        return "disabled"

    payload = await build_usage_ping(db)
    state = await _state(db)
    state.last_attempt_at = datetime.now(UTC)
    state.last_payload = payload.model_dump(mode="json")
    try:
        response = await _deliver_payload(payload)
        state.last_success_at = datetime.now(UTC)
        state.last_error = None
        state.last_response = response.text[:500]
        result = "sent"
    except Exception as exc:
        state.last_error = str(exc)[:500]
        state.last_response = None
        result = "failed"
        optic.warning("usage ping delivery failed: {}", exc)
    await db.commit()
    return result
