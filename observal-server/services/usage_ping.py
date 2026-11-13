# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Privacy-bounded collection and delivery of aggregate installation usage."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, cast
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
from models.user import User, UserRole
from schemas.usage_ping import (
    UsagePingActivity,
    UsagePingContact,
    UsagePingContactState,
    UsagePingContactUpdate,
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
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

_SCHEMA_VERSION = 3
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


_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)[a-z0-9_](?:[a-z0-9_-]{0,62}[a-z0-9_])?(?:\.[a-z0-9_](?:[a-z0-9_-]{0,62}[a-z0-9_])?)*$"
)
_GITHUB_ORG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38})$")
_ACCESS_HOSTS_KEY = "usage_ping:access_hosts"
_ACCESS_HOSTS_TTL_SECONDS = 30 * 24 * 3600
_ACCESS_HOSTS_TRACKED = 50
_ACCESS_HOSTS_REPORTED = 5


def _report_host(value: str | None) -> str | None:
    """Normalize a request or configuration host. IP literals are reported only by kind."""
    if not value:
        return None
    raw = value.split(",")[0].strip().lower()
    try:
        host = urlparse(raw if "://" in raw else f"//{raw}").hostname or ""
    except ValueError:
        # A malformed header (for example "http://[") must not fail the token refresh it rides on.
        return None
    host = host.rstrip(".")
    if not host:
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if host == "localhost" or host.endswith(".localhost"):
            return "localhost"
        return host if _DOMAIN_RE.match(host) else None
    if address.is_loopback:
        return "localhost"
    return "private-ip" if address.is_private else "public-ip"


def request_access_host(request: Request) -> str | None:
    """The host a user reached this server on. Origin survives proxies that rewrite Host."""
    for value in (
        request.headers.get("origin"),
        request.headers.get("x-forwarded-host"),
        request.headers.get("host"),
    ):
        host = _report_host(value)
        if host:
            return host
    return None


async def record_access_host(request: Request) -> None:
    """Count the host behind an authenticated request. Never fails the request."""
    host = request_access_host(request)
    if not host:
        return
    try:
        from services.redis import get_redis

        async with get_redis().pipeline(transaction=False) as pipe:
            pipe.zincrby(_ACCESS_HOSTS_KEY, 1, host)
            pipe.zremrangebyrank(_ACCESS_HOSTS_KEY, 0, -(_ACCESS_HOSTS_TRACKED + 1))
            pipe.expire(_ACCESS_HOSTS_KEY, _ACCESS_HOSTS_TTL_SECONDS)
            await pipe.execute()
    except Exception as exc:
        optic.debug("usage ping access host not recorded: {}", exc)


async def _access_hosts() -> list[str]:
    """Most used hosts users reach this server on, from the Redis tally."""
    try:
        from services.redis import get_redis

        hosts = await get_redis().zrevrange(_ACCESS_HOSTS_KEY, 0, _ACCESS_HOSTS_REPORTED - 1)
    except Exception as exc:
        optic.debug("usage ping access hosts unavailable: {}", exc)
        return []
    return [host for host in (_report_host(str(value)) for value in hosts) if host]


async def _email_domains(db: AsyncSession) -> dict[str, int]:
    """User counts per email domain. Individual addresses never leave the server."""
    domain = func.lower(func.split_part(User.email, "@", 2))
    statement = (
        select(domain, func.count())
        .where(User.is_demo.is_(False), User.email.contains("@"))
        .group_by(domain)
        .order_by(func.count().desc(), domain)
        .limit(10)
    )
    try:
        async with db.begin_nested():
            rows = (await db.execute(statement)).all()
    except Exception as exc:
        optic.warning("usage ping email domains unavailable: {}", exc)
        return {}
    return {str(name): int(count) for name, count in rows if _DOMAIN_RE.match(str(name or ""))}


_ADMIN_EMAILS_REPORTED = 5


async def admin_emails(db: AsyncSession, *, respect_setting: bool = True) -> list[str]:
    """Active super administrators' addresses, oldest accounts first.

    Reports include them unless a super admin turns off usage_ping.include_admin_emails;
    the settings page lists them either way so admins see exactly who would be shared.
    """
    if respect_setting and not await ds.get_bool("usage_ping.include_admin_emails"):
        return []
    statement = (
        select(User.email)
        .where(
            User.role == UserRole.super_admin,
            User.is_demo.is_(False),
            User.deleted_at.is_(None),
            User.auth_provider != "deactivated",
            User.email.contains("@"),
        )
        .order_by(User.created_at, User.email)
        .limit(_ADMIN_EMAILS_REPORTED)
    )
    try:
        async with db.begin_nested():
            rows = (await db.scalars(statement)).all()
    except Exception as exc:
        optic.warning("usage ping admin emails unavailable: {}", exc)
        return []
    emails: list[str] = []
    for value in rows:
        email = str(value or "").strip().lower()
        local, _, domain = email.partition("@")
        if local and _DOMAIN_RE.match(domain) and len(email) <= 254 and email not in emails:
            emails.append(email)
    return emails


async def _sso_identity() -> tuple[list[str], list[str], list[str]]:
    """Identity provider hosts, Google allowed domains and GitHub allowed orgs."""
    hosts: list[str] = []
    for key in ("oauth.server_metadata_url", "saml.idp_entity_id", "saml.idp_sso_url", "saml.idp_metadata_url"):
        host = _report_host(await ds.get(key, ""))
        if host and host not in hosts:
            hosts.append(host)
    allowed = [
        value.strip().lower().lstrip("@")
        for value in (await ds.get("google.allowed_domains", "")).split(",")
        if _DOMAIN_RE.match(value.strip().lower().lstrip("@"))
    ]
    orgs = [
        value.strip().lower()
        for value in (await ds.get("github.allowed_orgs", "")).split(",")
        if _GITHUB_ORG_RE.match(value.strip().lower())
    ]
    return hosts[:5], allowed[:10], orgs[:10]


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
    email_domains = await _email_domains(db)
    sso_hosts, sso_allowed_domains, github_orgs = await _sso_identity()
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
            access_hosts=await _access_hosts(),
            email_domains=email_domains,
            admin_emails=await admin_emails(db),
            sso_hosts=sso_hosts,
            sso_allowed_domains=sso_allowed_domains,
            github_orgs=github_orgs,
        ),
        instance=UsagePingInstance(version=get_server_version(), deployment_type=_deployment_type()),
        counts=UsagePingCounts(**postgres_counts, **session_counts),
        activity=UsagePingActivity(**activity),
        features=features,
        harnesses=harnesses,
        contact=_stored_contact(state),
    )
    await db.commit()
    return payload


_CONTACT_REPROMPT_AFTER = timedelta(days=30)
_CONTACT_BANNER_USERS = 5
_CONTACT_BANNER_AGE = timedelta(days=14)


def _stored_contact(state: UsagePingState) -> UsagePingContact | None:
    """The stored contact, or None when absent or no longer valid."""
    if not state.contact:
        return None
    try:
        return UsagePingContact.model_validate(state.contact)
    except ValueError:
        optic.warning("usage ping stored contact is invalid and was skipped")
        return None


async def _installation_age(db: AsyncSession, now: datetime) -> timedelta:
    """Time since the first non-demo user was created."""
    try:
        async with db.begin_nested():
            first_user = await db.scalar(select(func.min(User.created_at)).where(User.is_demo.is_(False)))
    except Exception as exc:
        optic.warning("usage ping installation age unavailable: {}", exc)
        return timedelta(0)
    return now - _as_utc(first_user) if first_user else timedelta(0)


async def contact_state(db: AsyncSession, *, now: datetime | None = None) -> UsagePingContactState:
    """Decide whether to ask a super administrator to stay in touch.

    A fresh installation gets a one-time dialog. "Not now" returns as a banner once the
    installation has grown (5+ users or 14+ days old), at most once every 30 days.
    "Don't ask again" and a submitted contact end the prompts.
    """
    current = _as_utc(now or datetime.now(UTC))
    state = await _state(db)
    prompt: Literal["dialog", "banner"] | None = None
    asked = state.contact_prompt or ""
    if await ds.get_bool("usage_ping.enabled"):
        if asked == "":
            prompt = "dialog"
        elif asked == "later":
            asked_at = _as_utc(state.contact_prompted_at) if state.contact_prompted_at else None
            if asked_at is None or current - asked_at >= _CONTACT_REPROMPT_AFTER:
                users = await _safe_count(db, User, User.is_demo.is_(False))
                if users >= _CONTACT_BANNER_USERS or await _installation_age(db, current) >= _CONTACT_BANNER_AGE:
                    prompt = "banner"
    result = UsagePingContactState(prompt=prompt, contact=_stored_contact(state))
    await db.commit()
    return result


async def save_contact(db: AsyncSession, update: UsagePingContactUpdate) -> UsagePingContactState:
    """Store a consented contact and end the prompts."""
    state = await _state(db)
    contact = UsagePingContact(
        name=update.name,
        email=update.email,
        role=update.role,
        team_size=update.team_size,
        consented_at=datetime.now(UTC),
    )
    state.contact = contact.model_dump(mode="json")
    state.contact_prompt = "submitted"
    state.contact_prompted_at = datetime.now(UTC)
    await db.commit()
    return UsagePingContactState(prompt=None, contact=contact)


async def withdraw_contact(db: AsyncSession) -> UsagePingContactState:
    """Stop sharing the contact. Later reports carry no contact block."""
    state = await _state(db)
    state.contact = None
    state.contact_prompt = "never"
    state.contact_prompted_at = datetime.now(UTC)
    await db.commit()
    return UsagePingContactState(prompt=None, contact=None)


async def defer_contact(db: AsyncSession, *, never: bool) -> UsagePingContactState:
    """Record "Not now" (ask again later) or "Don't ask again"."""
    state = await _state(db)
    if state.contact_prompt != "submitted":
        state.contact_prompt = "never" if never else "later"
    state.contact_prompted_at = datetime.now(UTC)
    await db.commit()
    return UsagePingContactState(prompt=None, contact=_stored_contact(state))


async def usage_ping_status(db: AsyncSession) -> UsagePingStatus:
    state = await _state(db)
    enabled = await ds.get_bool("usage_ping.enabled")
    company_name = (await ds.get("usage_ping.company_name")).strip()
    public_url = (await ds.get("deployment.public_url")).strip()
    frequency = await _usage_ping_frequency()
    configured = bool(company_name and public_url)
    shared_admin_emails = await admin_emails(db, respect_setting=False)
    await db.commit()
    return UsagePingStatus(
        admin_emails=shared_admin_emails,
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
