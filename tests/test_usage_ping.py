# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from models.usage_ping import UsagePingState
from schemas.usage_ping import (
    UsagePingActivity,
    UsagePingCounts,
    UsagePingIdentity,
    UsagePingInstance,
    UsagePingPayload,
)
from services import usage_ping


def test_usage_reporting_defaults_to_enabled_every_six_hours():
    from services.dynamic_settings import DEFAULTS

    assert DEFAULTS["usage_ping.enabled"] == "true"
    assert DEFAULTS["usage_ping.frequency"] == "every_6_hours"


@pytest.fixture(autouse=True)
def _allow_public_collector(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(usage_ping, "is_private_url", lambda _url: False)


class FakeDb:
    def __init__(self):
        self.state = UsagePingState(id=1, installation_id=uuid.uuid4())
        self.commits = 0

    async def get(self, model, key):
        return self.state

    def add(self, value):
        self.state = value

    async def flush(self):
        if self.state.installation_id is None:
            self.state.installation_id = uuid.uuid4()

    async def commit(self):
        self.commits += 1


def sample_activity() -> UsagePingActivity:
    return UsagePingActivity(
        active_users_7d=1,
        active_users_30d=1,
        active_agents_7d=1,
        active_agents_30d=1,
        events_7d=4,
        events_30d=4,
        prompts_7d=1,
        prompts_30d=1,
        tool_calls_7d=1,
        tool_calls_30d=1,
        tool_results_7d=1,
        tool_results_30d=1,
        input_tokens_7d=100,
        input_tokens_30d=100,
        output_tokens_7d=20,
        output_tokens_30d=20,
        cache_read_tokens_7d=50,
        cache_read_tokens_30d=50,
        cache_write_tokens_7d=10,
        cache_write_tokens_30d=10,
        credits_7d=1.25,
        credits_30d=1.25,
        average_session_duration_seconds_30d=60,
        average_prompts_per_session_30d=1,
        average_tool_calls_per_session_30d=1,
        sessions_with_tools_30d=1,
        sessions_with_tokens_30d=1,
        registered_agent_sessions_30d=1,
        unregistered_agent_sessions_30d=0,
        top_level_sessions_30d=1,
        subagent_sessions_30d=0,
        distinct_agent_versions_30d=1,
        distinct_models_30d=1,
        parse_errors_30d=0,
        truncated_events_30d=0,
    )


def sample_payload() -> UsagePingPayload:
    return UsagePingPayload(
        ping_id=uuid.uuid4(),
        installation_id=uuid.uuid4(),
        sent_at=datetime.now(UTC),
        identity=UsagePingIdentity(company_name="Acme", hostname="observal.acme.test"),
        instance=UsagePingInstance(version="1.12.1", deployment_type="self-managed"),
        counts=UsagePingCounts(
            users=1,
            teams=1,
            agents=1,
            mcp_servers=1,
            skills=1,
            hooks=1,
            prompts=1,
            sandboxes=1,
            agent_installs=1,
            sessions_total=1,
            sessions_7d=1,
            sessions_30d=1,
        ),
        activity=sample_activity(),
        features={},
        harnesses={},
    )


@pytest.mark.asyncio
async def test_build_payload_contains_only_aggregate_fields(monkeypatch: pytest.MonkeyPatch):
    db = FakeDb()

    async def fake_get(key, default=None):
        return {
            "usage_ping.company_name": "Acme",
            "deployment.public_url": "https://agents.acme.test/path",
            "oauth.client_id": "",
            "saml.idp_entity_id": "",
            "google.client_id": "",
            "github.client_id": "",
        }.get(key, default or "")

    async def fake_bool(key, default=False):
        return key == "insights.batch_enabled"

    monkeypatch.setattr(usage_ping.ds, "get", fake_get)
    monkeypatch.setattr(usage_ping.ds, "get_bool", fake_bool)
    monkeypatch.setattr(
        usage_ping,
        "_postgres_counts",
        AsyncMock(
            return_value={
                "users": 2,
                "teams": 1,
                "agents": 3,
                "mcp_servers": 4,
                "skills": 5,
                "hooks": 6,
                "prompts": 7,
                "sandboxes": 8,
                "agent_installs": 9,
            }
        ),
    )
    monkeypatch.setattr(
        usage_ping,
        "_session_metrics",
        AsyncMock(
            return_value=(
                {"sessions_total": 12, "sessions_7d": 2, "sessions_30d": 7},
                sample_activity().model_dump(),
                {"pi": 7},
            )
        ),
    )

    result = await usage_ping.build_usage_ping(db, now=datetime(2026, 3, 16, tzinfo=UTC))
    body = result.model_dump(mode="json")
    assert body["identity"] == {"company_name": "Acme", "hostname": "agents.acme.test"}
    assert body["schema_version"] == 2
    assert body["counts"]["sessions_30d"] == 7
    assert body["activity"]["active_users_30d"] == 1
    assert body["harnesses"] == {"pi": 7}
    serialized = str(body).lower()
    assert "email" not in serialized
    assert "prompt_content" not in serialized
    assert "raw_line" not in serialized


@pytest.mark.asyncio
async def test_session_metrics_include_aggregates_and_limit_harnesses(monkeypatch: pytest.MonkeyPatch):
    from services.clickhouse import client as clickhouse_client

    totals_response = MagicMock()
    totals_response.json.return_value = {
        "data": [
            {
                "sessions_total": 40,
                "sessions_7d": 20,
                "sessions_30d": 30,
                "active_users_30d": 12,
                "active_agents_30d": 8,
                "prompts_30d": 90,
                "tool_calls_30d": 150,
                "credits_30d": 7.5,
                "session_duration_seconds_30d": 3000,
            }
        ]
    }
    health_response = MagicMock()
    health_response.json.return_value = {"data": [{"parse_errors_30d": 2, "truncated_events_30d": 3}]}
    harness_response = MagicMock()
    harness_response.json.return_value = {
        "data": [{"harness": f"harness-{index}", "sessions": 40 - index} for index in range(40)]
    }
    query = AsyncMock(side_effect=[totals_response, health_response, harness_response])
    monkeypatch.setattr(clickhouse_client, "_query", query)

    totals, activity, harnesses = await usage_ping._session_metrics()

    assert totals["sessions_total"] == 40
    assert activity["active_users_30d"] == 12
    assert activity["active_agents_30d"] == 8
    assert activity["average_session_duration_seconds_30d"] == 100
    assert activity["average_prompts_per_session_30d"] == 3
    assert activity["average_tool_calls_per_session_30d"] == 5
    assert activity["credits_30d"] == 7.5
    assert activity["parse_errors_30d"] == 2
    assert activity["truncated_events_30d"] == 3
    assert len(harnesses) == 32
    assert "LIMIT 32" in query.await_args_list[2].args[0]


@pytest.mark.asyncio
async def test_send_respects_disabled_setting(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(usage_ping.ds, "get_bool", AsyncMock(return_value=False))
    assert await usage_ping.send_usage_ping(FakeDb()) == "disabled"


def test_fallback_company_name_is_stable_per_installation():
    installation_id = uuid.UUID("00000000-0000-0000-0000-0000000f4241")
    assert usage_ping.fallback_company_name(installation_id) == "unknown-000001"
    assert usage_ping.fallback_company_name(installation_id) == usage_ping.fallback_company_name(installation_id)


@pytest.mark.asyncio
async def test_unconfigured_installation_reports_fallback_identity(monkeypatch: pytest.MonkeyPatch):
    db = FakeDb()
    monkeypatch.setattr(usage_ping.ds, "get", AsyncMock(return_value=""))
    monkeypatch.setattr(usage_ping.ds, "get_bool", AsyncMock(return_value=False))
    monkeypatch.setattr(
        usage_ping,
        "_postgres_counts",
        AsyncMock(
            return_value=sample_payload().counts.model_dump(exclude={"sessions_total", "sessions_7d", "sessions_30d"})
        ),
    )
    monkeypatch.setattr(
        usage_ping,
        "_session_metrics",
        AsyncMock(
            return_value=(
                {"sessions_total": 0, "sessions_7d": 0, "sessions_30d": 0},
                sample_activity().model_dump(),
                {},
            )
        ),
    )

    result = await usage_ping.build_usage_ping(db)

    assert result.identity.company_name == usage_ping.fallback_company_name(db.state.installation_id)
    assert result.identity.hostname == "not-configured"


@pytest.mark.asyncio
async def test_send_does_not_require_company_or_public_url(monkeypatch: pytest.MonkeyPatch):
    db = FakeDb()
    monkeypatch.setattr(usage_ping.ds, "get_bool", AsyncMock(return_value=True))
    monkeypatch.setattr(usage_ping.ds, "get", AsyncMock(return_value=""))
    monkeypatch.setattr(usage_ping, "build_usage_ping", AsyncMock(return_value=sample_payload()))
    deliver = AsyncMock(return_value=MagicMock(text='{"status":"accepted"}'))
    monkeypatch.setattr(usage_ping, "_deliver_payload", deliver)

    assert await usage_ping.send_usage_ping(db) == "sent"
    deliver.assert_awaited_once()


@pytest.mark.asyncio
async def test_successful_send_updates_state(monkeypatch: pytest.MonkeyPatch):
    db = FakeDb()
    payload = sample_payload()
    monkeypatch.setattr(usage_ping.ds, "get_bool", AsyncMock(return_value=True))
    monkeypatch.setattr(usage_ping.ds, "get", AsyncMock(side_effect=["Acme", "https://agents.acme.test"]))
    monkeypatch.setattr(usage_ping, "build_usage_ping", AsyncMock(return_value=payload))

    response = AsyncMock()
    response.text = '{"status":"accepted"}'
    response.raise_for_status = lambda: None
    client = AsyncMock()
    client.post.return_value = response
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    monkeypatch.setattr(usage_ping.httpx, "AsyncClient", lambda **kwargs: client)

    assert await usage_ping.send_usage_ping(db) == "sent"
    assert db.state.last_success_at is not None
    assert db.state.last_error is None
    client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_delivery_failure_is_recorded(monkeypatch: pytest.MonkeyPatch):
    db = FakeDb()
    monkeypatch.setattr(usage_ping.ds, "get_bool", AsyncMock(return_value=True))
    monkeypatch.setattr(usage_ping.ds, "get", AsyncMock(side_effect=["Acme", "https://agents.acme.test"]))
    monkeypatch.setattr(usage_ping, "build_usage_ping", AsyncMock(return_value=sample_payload()))
    client = AsyncMock()
    client.post.side_effect = OSError("network unavailable")
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    monkeypatch.setattr(usage_ping.httpx, "AsyncClient", lambda **kwargs: client)

    assert await usage_ping.send_usage_ping(db) == "failed"
    assert db.state.last_success_at is None
    assert db.state.last_error == "network unavailable"


@pytest.mark.asyncio
async def test_delivery_blocks_private_production_collector(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(usage_ping.settings, "USAGE_PING_URL", "https://usage.observal.io/api/v1/usage-pings")
    monkeypatch.setattr(usage_ping.settings, "USAGE_PING_DEPLOYMENT_TYPE", "self-managed")
    monkeypatch.setattr(usage_ping, "is_private_url", lambda _url: True)

    with pytest.raises(RuntimeError, match="private or internal"):
        await usage_ping._deliver_payload(sample_payload())


@pytest.mark.asyncio
async def test_delivery_allows_explicit_local_development_collector(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(usage_ping.settings, "USAGE_PING_URL", "http://telemetry-api:8090/api/v1/usage-pings")
    monkeypatch.setattr(usage_ping.settings, "USAGE_PING_DEPLOYMENT_TYPE", "development")
    monkeypatch.setattr(usage_ping, "is_private_url", lambda _url: True)
    response = httpx.Response(202, request=httpx.Request("POST", "http://telemetry-api:8090"))
    client = AsyncMock()
    client.post.return_value = response
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    monkeypatch.setattr(usage_ping.httpx, "AsyncClient", lambda **kwargs: client)

    result = await usage_ping._deliver_payload(sample_payload())

    assert result.status_code == 202


@pytest.mark.asyncio
async def test_delivery_retries_transient_transport_errors(monkeypatch: pytest.MonkeyPatch):
    response = httpx.Response(202, request=httpx.Request("POST", "https://usage.observal.io"))
    client = AsyncMock()
    client.post.side_effect = [httpx.ConnectError("temporary"), response]
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    monkeypatch.setattr(usage_ping.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(usage_ping.asyncio, "sleep", AsyncMock())

    result = await usage_ping._deliver_payload(sample_payload())
    assert result.status_code == 202
    assert client.post.await_count == 2


@pytest.mark.asyncio
async def test_delivery_does_not_retry_client_errors(monkeypatch: pytest.MonkeyPatch):
    response = httpx.Response(422, request=httpx.Request("POST", "https://usage.observal.io"))
    client = AsyncMock()
    client.post.return_value = response
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    monkeypatch.setattr(usage_ping.httpx, "AsyncClient", lambda **kwargs: client)

    with pytest.raises(httpx.HTTPStatusError):
        await usage_ping._deliver_payload(sample_payload())
    assert client.post.await_count == 1


def test_collector_override_is_restricted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(usage_ping.settings, "USAGE_PING_URL", "http://169.254.169.254/latest/meta-data")
    monkeypatch.setattr(usage_ping.settings, "USAGE_PING_DEPLOYMENT_TYPE", "self-managed")
    with pytest.raises(RuntimeError, match="only allowed for local development"):
        usage_ping._collector_url()


@pytest.mark.parametrize(
    ("frequency", "now", "expected"),
    [
        ("weekly", datetime(2026, 3, 16, 7, tzinfo=UTC), "2026-03-23T06:30:00+00:00"),
        ("daily", datetime(2026, 3, 16, 7, tzinfo=UTC), "2026-03-17T06:30:00+00:00"),
        ("every_6_hours", datetime(2026, 3, 16, 7, tzinfo=UTC), "2026-03-16T12:30:00+00:00"),
        ("every_6_hours", datetime(2026, 3, 16, 12, 30, tzinfo=UTC), "2026-03-16T18:30:00+00:00"),
    ],
)
def test_next_schedule_matches_selected_frequency(frequency, now, expected):
    value = usage_ping.next_scheduled_at(now, frequency=frequency)
    assert value.isoformat() == expected


def test_failed_window_reports_next_worker_retry():
    value = usage_ping._next_delivery_at(
        "weekly",
        datetime(2026, 3, 9, 6, 30, tzinfo=UTC),
        now=datetime(2026, 3, 16, 7, tzinfo=UTC),
    )
    assert value.isoformat() == "2026-03-16T12:30:00+00:00"


@pytest.mark.asyncio
async def test_scheduled_send_runs_when_current_window_is_due(monkeypatch: pytest.MonkeyPatch):
    db = FakeDb()
    db.state.last_success_at = datetime(2026, 3, 15, 6, 30, tzinfo=UTC)
    monkeypatch.setattr(usage_ping.ds, "get_bool", AsyncMock(return_value=True))
    monkeypatch.setattr(usage_ping.ds, "get", AsyncMock(return_value="weekly"))
    send = AsyncMock(return_value="sent")
    monkeypatch.setattr(usage_ping, "send_usage_ping", send)

    result = await usage_ping.send_scheduled_usage_ping(db, now=datetime(2026, 3, 16, 7, tzinfo=UTC))

    assert result == "sent"
    send.assert_awaited_once_with(db)


@pytest.mark.asyncio
async def test_scheduled_send_skips_completed_window(monkeypatch: pytest.MonkeyPatch):
    db = FakeDb()
    db.state.last_success_at = datetime(2026, 3, 16, 6, 31, tzinfo=UTC)
    monkeypatch.setattr(usage_ping.ds, "get_bool", AsyncMock(return_value=True))
    monkeypatch.setattr(usage_ping.ds, "get", AsyncMock(return_value="weekly"))
    send = AsyncMock(return_value="sent")
    monkeypatch.setattr(usage_ping, "send_usage_ping", send)

    result = await usage_ping.send_scheduled_usage_ping(db, now=datetime(2026, 3, 16, 7, tzinfo=UTC))

    assert result == "not-due"
    send.assert_not_awaited()
    assert db.commits == 1


@pytest.mark.asyncio
async def test_unknown_frequency_falls_back_to_every_six_hours(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(usage_ping.ds, "get", AsyncMock(return_value="hourly"))
    assert await usage_ping._usage_ping_frequency() == "every_6_hours"
