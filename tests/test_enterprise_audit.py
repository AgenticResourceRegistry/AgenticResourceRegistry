# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Yash Gadgil <yashgadgil08@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the enterprise audit logging system."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestClassification:
    """Route sensitivity classification."""

    def test_health_is_skip(self):
        from services.audit.classification import classify_route

        assert classify_route("GET", "/health") == "skip"

    def test_livez_is_skip(self):
        from services.audit.classification import classify_route

        assert classify_route("GET", "/livez") == "skip"

    def test_metrics_is_skip(self):
        from services.audit.classification import classify_route

        assert classify_route("GET", "/metrics") == "skip"

    def test_openapi_is_skip(self):
        from services.audit.classification import classify_route

        assert classify_route("GET", "/openapi.json") == "skip"

    def test_sessions_is_phi_adjacent(self):
        from services.audit.classification import classify_route

        assert classify_route("GET", "/api/v1/sessions") == "phi_adjacent"
        assert classify_route("GET", "/api/v1/sessions/abc") == "phi_adjacent"

    def test_ingest_is_phi_adjacent(self):
        from services.audit.classification import classify_route

        assert classify_route("POST", "/api/v1/ingest/session") == "phi_adjacent"

    def test_telemetry_is_phi_adjacent(self):
        from services.audit.classification import classify_route

        assert classify_route("POST", "/api/v1/ingest/session") == "phi_adjacent"

    def test_admin_is_admin(self):
        from services.audit.classification import classify_route

        assert classify_route("GET", "/api/v1/admin/settings") == "admin"

    def test_agents_is_high(self):
        from services.audit.classification import classify_route

        assert classify_route("POST", "/api/v1/agents") == "high"

    def test_auth_is_high(self):
        from services.audit.classification import classify_route

        assert classify_route("POST", "/api/v1/auth/login") == "high"

    def test_feedback_is_standard(self):
        from services.audit.classification import classify_route

        assert classify_route("POST", "/api/v1/feedback") == "standard"

    def test_config_is_low(self):
        from services.audit.classification import classify_route

        assert classify_route("GET", "/api/v1/config/public") == "low"

    def test_unknown_api_defaults_to_standard(self):
        from services.audit.classification import classify_route

        assert classify_route("GET", "/api/v1/unknown") == "standard"

    def test_non_api_is_skip(self):
        from services.audit.classification import classify_route

        assert classify_route("GET", "/favicon.ico") == "skip"


class TestHelpers:
    """audit_detail() helper."""

    def test_sets_fields(self):
        from services.audit.helpers import audit_detail

        request = MagicMock()
        request.state = MagicMock()
        audit_detail(request, action="mcp.submit", resource_type="mcp", resource_id="abc")
        assert request.state.audit_action == "mcp.submit"
        assert request.state.audit_resource_type == "mcp"
        assert request.state.audit_resource_id == "abc"

    def test_skips_empty(self):
        from services.audit.helpers import audit_detail

        request = MagicMock()
        request.state = MagicMock(spec=[])
        audit_detail(request, action="test")
        assert request.state.audit_action == "test"


class TestSink:
    """Audit sink buffer and flush."""

    @pytest.mark.asyncio
    async def test_buffers_records(self):
        from services.audit.sink import _buffer, _buffer_lock, audit_sink

        async with _buffer_lock:
            _buffer.clear()

        record = {
            "record": {
                "time": {"timestamp": 1779600000.123},
                "extra": {
                    "audit": True,
                    "event_id": str(uuid.uuid4()),
                    "actor_id": "user-1",
                    "actor_email": "test@example.com",
                    "actor_role": "admin",
                    "action": "test.action",
                    "resource_type": "test",
                    "resource_id": "123",
                    "resource_name": "",
                    "http_method": "GET",
                    "http_path": "/api/v1/test",
                    "status_code": 200,
                    "ip_address": "127.0.0.1",
                    "user_agent": "test",
                    "detail": "",
                    "sensitivity": "standard",
                    "request_id": "req-1",
                    "outcome": "success",
                    "duration_ms": 5.0,
                    "_chain_hash": "abc123",
                    "source": "server",
                },
            }
        }

        await audit_sink(json.dumps(record))

        async with _buffer_lock:
            assert len(_buffer) == 1
            row = _buffer[0]
            assert row["actor_id"] == "user-1"
            assert row["action"] == "test.action"
            assert row["chain_hash"] == "abc123"
            assert "2026-" in row["timestamp"]
            _buffer.clear()

    @pytest.mark.asyncio
    async def test_ignores_non_audit(self):
        from services.audit.sink import _buffer, _buffer_lock, audit_sink

        async with _buffer_lock:
            _buffer.clear()

        record = {"record": {"time": {"timestamp": 0}, "extra": {"audit": False}}}
        await audit_sink(json.dumps(record))

        async with _buffer_lock:
            assert len(_buffer) == 0

    @pytest.mark.asyncio
    async def test_flush_calls_insert(self):
        from services.audit.sink import _buffer, _buffer_lock, _flush

        async with _buffer_lock:
            _buffer.clear()
            _buffer.append({"event_id": "test", "timestamp": "2026-01-01 00:00:00.000", "action": "t"})

        with patch("services.clickhouse.insert_audit_log", new_callable=AsyncMock) as mock_insert:
            async with _buffer_lock:
                await _flush()
            mock_insert.assert_called_once()
            assert len(mock_insert.call_args[0][0]) == 1

    @pytest.mark.asyncio
    async def test_flush_empty_noop(self):
        from services.audit.sink import _buffer, _buffer_lock, _flush

        async with _buffer_lock:
            _buffer.clear()

        with patch("services.clickhouse.insert_audit_log", new_callable=AsyncMock) as mock_insert:
            async with _buffer_lock:
                await _flush()
            mock_insert.assert_not_called()


class TestSetup:
    """Hash chain and setup."""

    def test_chain_hash_adds_hash(self):
        from services.audit.setup import _chain_hash_patcher

        record = {"extra": {"audit": True, "action": "test"}}
        _chain_hash_patcher(record)
        assert "_chain_hash" in record["extra"]
        assert len(record["extra"]["_chain_hash"]) == 64

    def test_chain_hash_skips_non_audit(self):
        from services.audit.setup import _chain_hash_patcher

        record = {"extra": {"audit": False}}
        _chain_hash_patcher(record)
        assert "_chain_hash" not in record["extra"]

    def test_chain_is_sequential(self):
        from services.audit import setup as setup_mod

        setup_mod._prev_hash = "0" * 64
        r1 = {"extra": {"audit": True, "action": "first"}}
        setup_mod._chain_hash_patcher(r1)
        r2 = {"extra": {"audit": True, "action": "second"}}
        setup_mod._chain_hash_patcher(r2)
        assert r1["extra"]["_chain_hash"] != r2["extra"]["_chain_hash"]


def _audited_app(handler, *, path="/api/v1/agents", methods=("GET",)):
    from starlette.applications import Starlette
    from starlette.routing import Route

    from api.middleware.audit import AuditMiddleware

    app = Starlette(routes=[Route(path, handler, methods=list(methods))])
    app.add_middleware(AuditMiddleware)
    return app


async def _call(app, method, path, *, client=("127.0.0.1", 5000), headers=None):
    import httpx

    transport = httpx.ASGITransport(app=app, client=client)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        return await http.request(method, path, headers=headers)


class TestMiddleware:
    """AuditMiddleware."""

    @pytest.mark.asyncio
    async def test_skips_health(self):
        from starlette.responses import PlainTextResponse

        app = _audited_app(lambda request: PlainTextResponse("ok"), path="/health")

        with patch("api.middleware.audit.optic") as mock_optic:
            response = await _call(app, "GET", "/health")
            mock_optic.bind.assert_not_called()
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_emits_for_api(self):
        from starlette.responses import PlainTextResponse

        app = _audited_app(lambda request: PlainTextResponse("ok"))

        with patch("api.middleware.audit.optic") as mock_optic:
            mock_bound = MagicMock()
            mock_optic.bind.return_value = mock_bound
            await _call(app, "GET", "/api/v1/agents", headers={"x-forwarded-for": "10.0.0.1", "user-agent": "test/1.0"})
            mock_optic.bind.assert_called_once()
            kwargs = mock_optic.bind.call_args[1]
            assert kwargs["audit"] is True
            assert kwargs["outcome"] == "success"
            assert kwargs["sensitivity"] == "high"
            assert kwargs["ip_address"] == "127.0.0.1"
            assert kwargs["user_agent"] == "test/1.0"
            assert kwargs["action"] == "get./api/v1/agents"
            mock_bound.info.assert_called_once_with("audit")

    @pytest.mark.asyncio
    async def test_denied_on_403(self):
        from starlette.responses import PlainTextResponse

        app = _audited_app(
            lambda request: PlainTextResponse("no", status_code=403), path="/api/v1/admin/settings", methods=("POST",)
        )

        with patch("api.middleware.audit.optic") as mock_optic:
            mock_optic.bind.return_value = MagicMock()
            await _call(app, "POST", "/api/v1/admin/settings", client=("192.168.1.1", 5000))
            kwargs = mock_optic.bind.call_args[1]
            assert kwargs["outcome"] == "denied"
            assert kwargs["actor_id"] == "anonymous"
            assert kwargs["ip_address"] == "192.168.1.1"

    @pytest.mark.asyncio
    async def test_records_actor_and_resource_set_by_the_route(self):
        from types import SimpleNamespace

        from starlette.responses import PlainTextResponse

        user = SimpleNamespace(id=uuid.uuid4(), email="a@example.com", role=SimpleNamespace(value="admin"))

        async def handler(request):
            request.state.audit_user = user
            request.state.audit_action = "agent.delete"
            request.state.audit_resource_type = "agent"
            request.state.audit_resource_id = "agent-1"
            return PlainTextResponse("ok")

        app = _audited_app(handler, path="/api/v1/agents/agent-1", methods=("DELETE",))

        with patch("api.middleware.audit.optic") as mock_optic:
            mock_optic.bind.return_value = MagicMock()
            await _call(app, "DELETE", "/api/v1/agents/agent-1")
            kwargs = mock_optic.bind.call_args[1]
            assert kwargs["actor_id"] == str(user.id)
            assert kwargs["actor_email"] == "a@example.com"
            assert kwargs["actor_role"] == "admin"
            assert kwargs["action"] == "agent.delete"
            assert kwargs["resource_type"] == "agent"
            assert kwargs["resource_id"] == "agent-1"

    @pytest.mark.asyncio
    async def test_streaming_response_is_audited_when_it_starts(self):
        from starlette.responses import StreamingResponse

        emitted_before_body: list[bool] = []
        mock_optic = MagicMock()

        async def body():
            emitted_before_body.append(mock_optic.bind.called)
            yield b"chunk"

        app = _audited_app(lambda request: StreamingResponse(body()), path="/api/v1/sessions/stream")

        with patch("api.middleware.audit.optic", mock_optic):
            response = await _call(app, "GET", "/api/v1/sessions/stream")
        assert response.content == b"chunk"
        assert emitted_before_body == [True]
        mock_optic.bind.assert_called_once()

    @pytest.mark.asyncio
    async def test_share_tokens_are_redacted_from_the_path(self):
        from starlette.responses import PlainTextResponse

        app = _audited_app(lambda request: PlainTextResponse("ok"), path="/api/v1/agent-shares/{token}")

        with patch("api.middleware.audit.optic") as mock_optic:
            mock_optic.bind.return_value = MagicMock()
            await _call(app, "GET", "/api/v1/agent-shares/secret-share-token")
            kwargs = mock_optic.bind.call_args[1]
            assert kwargs["http_path"] == "/api/v1/agent-shares/{token}"
            assert "secret-share-token" not in repr(mock_optic.bind.call_args)


class TestCliAudit:
    """CLI audit emission."""

    def test_no_config_is_noop(self):
        with patch("observal_cli.config.load", return_value={}):
            from observal_cli.audit import emit_cli_audit

            emit_cli_audit("test.action")  # should not raise

    def test_starts_thread(self):
        cfg = {"api_key": "test-key", "server_url": "http://localhost:8000"}
        with (
            patch("observal_cli.config.load", return_value=cfg),
            patch("threading.Thread") as mock_thread,
        ):
            from observal_cli.audit import emit_cli_audit

            emit_cli_audit("agent.pull", resource_type="agent", resource_id="abc")
            mock_thread.assert_called_once()
            assert mock_thread.call_args[1]["daemon"] is True


class TestSchemaExpansion:
    """ClickHouse schema includes new audit columns."""

    def test_new_columns_in_baseline_migration(self):
        from services.clickhouse.migrations import MIGRATIONS_DIR

        sql_blob = (MIGRATIONS_DIR / "001_baseline.sql").read_text()
        assert "ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS sensitivity" in sql_blob
        assert "ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS outcome" in sql_blob
        assert "ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS duration_ms" in sql_blob
        assert "ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS chain_hash" in sql_blob
        assert "ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS source" in sql_blob

    def test_new_indexes_in_baseline_migration(self):
        from services.clickhouse.migrations import MIGRATIONS_DIR

        sql_blob = (MIGRATIONS_DIR / "001_baseline.sql").read_text()
        assert "idx_outcome" in sql_blob
        assert "idx_sensitivity" in sql_blob
        assert "idx_source" in sql_blob


class TestInsertAuditLog:
    """insert_audit_log writes new fields."""

    @pytest.mark.asyncio
    async def test_includes_all_fields(self):
        with patch("services.clickhouse.client._query", new_callable=AsyncMock) as mock_query:
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_query.return_value = mock_response

            with patch("services.clickhouse.client._invalidate_cache", new_callable=AsyncMock):
                from services.clickhouse.insert import insert_audit_log

                rows = [
                    {
                        "event_id": str(uuid.uuid4()),
                        "timestamp": "2026-01-01 00:00:00.000",
                        "actor_id": "user-1",
                        "actor_email": "t@t.com",
                        "actor_role": "admin",
                        "action": "test",
                        "resource_type": "",
                        "resource_id": "",
                        "resource_name": "",
                        "http_method": "POST",
                        "http_path": "/test",
                        "status_code": 200,
                        "ip_address": "10.0.0.1",
                        "user_agent": "cli",
                        "detail": "",
                        "sensitivity": "high",
                        "request_id": "req-1",
                        "outcome": "success",
                        "duration_ms": 42.5,
                        "chain_hash": "a" * 64,
                        "source": "cli",
                    }
                ]

                await insert_audit_log(rows)
                mock_query.assert_called_once()
                data = mock_query.call_args.kwargs.get("data", "")
                parsed = json.loads(data)
                assert parsed["sensitivity"] == "high"
                assert parsed["outcome"] == "success"
                assert parsed["source"] == "cli"
                assert parsed["chain_hash"] == "a" * 64
