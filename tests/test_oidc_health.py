# SPDX-FileCopyrightText: 2026 Tanvi Reddy <reddyplayer22@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deterministic coverage for OIDC health checks and their diagnostic records."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs

import httpx
import pytest
from redis.exceptions import RedisError

from schemas.sso_health import all_pass, make_check
from services import oidc_health as oidc
from services import sso_diagnostics as diagnostics

METADATA_URL = "https://idp.example.test/.well-known/openid-configuration"
AUTHORIZATION_ENDPOINT = "https://idp.example.test/authorize"
TOKEN_ENDPOINT = "https://idp.example.test/token"
JWKS_URI = "https://idp.example.test/jwks"
REDIRECT_URI = "https://app.example.test/api/v1/auth/oauth/callback"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


def _check_text(check: dict) -> str:
    return json.dumps(check, sort_keys=True)


@pytest.mark.asyncio
async def test_bounded_get_streams_body_and_rejects_oversized_response():
    async def ok_handler(request: httpx.Request) -> httpx.Response:
        assert request.url == METADATA_URL
        return httpx.Response(200, content=b"metadata", request=request)

    async with _client(ok_handler) as client:
        response, body = await oidc._bounded_get(client, METADATA_URL)

    assert response.status_code == 200
    assert body == b"metadata"

    async def large_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (oidc._MAX_BODY_BYTES + 1), request=request)

    async with _client(large_handler) as client:
        with pytest.raises(httpx.ReadError, match="Response exceeded"):
            await oidc._bounded_get(client, METADATA_URL)


@pytest.mark.asyncio
async def test_fetch_discovery_returns_object_and_server_clock():
    server_date = "Wed, 01 Jan 2025 12:00:00 GMT"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url == METADATA_URL
        return httpx.Response(
            200,
            json={"issuer": "https://idp.example.test"},
            headers={"date": server_date},
            request=request,
        )

    async with _client(handler) as client:
        metadata, date_header, check = await oidc.fetch_discovery(client, METADATA_URL)

    assert metadata == {"issuer": "https://idp.example.test"}
    assert date_header == server_date
    assert check == {
        "name": "discovery_doc",
        "label": "OIDC discovery document",
        "status": "pass",
    }


@pytest.mark.parametrize(
    ("failure", "message", "hint"),
    [
        ("timeout", "Timed out fetching OIDC metadata", "within 10 seconds"),
        ("status", "returned HTTP 503", "correct and accessible"),
        ("malformed", "Failed to fetch OIDC metadata", "reachable URL"),
    ],
)
@pytest.mark.asyncio
async def test_fetch_discovery_reports_sanitized_transport_and_json_failures(failure, message, hint):
    secret = "provider-secret-value"

    async def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout(secret, request=request)
        if failure == "status":
            return httpx.Response(503, text=secret, request=request)
        return httpx.Response(200, content=b"{not-json", request=request)

    async with _client(handler) as client:
        metadata, date_header, check = await oidc.fetch_discovery(client, METADATA_URL)

    assert metadata is None
    assert date_header is None
    assert message in check["message"]
    assert hint in check["hint"]
    assert secret not in _check_text(check)


@pytest.mark.asyncio
async def test_fetch_discovery_rejects_non_object_metadata_without_losing_date():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=["not", "an", "object"],
            headers={"date": "Wed, 01 Jan 2025 12:00:00 GMT"},
            request=request,
        )

    async with _client(handler) as client:
        metadata, date_header, check = await oidc.fetch_discovery(client, METADATA_URL)

    assert metadata is None
    assert date_header == "Wed, 01 Jan 2025 12:00:00 GMT"
    assert check["status"] == "fail"
    assert "JSON object" in check["message"]


@pytest.mark.parametrize(
    ("status", "location", "body", "expected_status", "message"),
    [
        (302, "https://idp.example.test/login", b"", "pass", None),
        (302, "https://app.example.test/callback?error=invalid_request", b"", "fail", "error redirect"),
        (200, "", b"login form", "pass", None),
        (400, "", b"redirect_uri is invalid", "fail", "rejected the redirect_uri"),
        (401, "", b"invalid_client", "fail", "recognize this client_id"),
        (403, "", b"unauthorized_client", "fail", "not authorized"),
        (418, "", b"teapot", "fail", "returned HTTP 418"),
    ],
)
@pytest.mark.asyncio
async def test_authorization_probe_sends_exact_client_and_redirect_parameters(
    status,
    location,
    body,
    expected_status,
    message,
):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert f"{request.url.scheme}://{request.url.host}{request.url.path}" == AUTHORIZATION_ENDPOINT
        assert parse_qs(request.url.query.decode()) == {
            "client_id": ["client-123"],
            "redirect_uri": [REDIRECT_URI],
            "response_type": ["code"],
            "scope": ["openid email profile groups"],
            "state": ["observal_validate_probe"],
            "nonce": ["observal_validate_nonce"],
        }
        return httpx.Response(status, content=body, headers={"location": location}, request=request)

    async with _client(handler) as client:
        check = await oidc.probe_authorization_endpoint(
            client,
            AUTHORIZATION_ENDPOINT,
            "client-123",
            REDIRECT_URI,
        )

    assert check["status"] == expected_status
    if message:
        assert message in check["message"]
    else:
        assert "message" not in check


@pytest.mark.parametrize("failure", ["timeout", "network"])
@pytest.mark.asyncio
async def test_authorization_probe_handles_timeout_and_network_failure_without_details(failure):
    secret = "socket-secret"

    async def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout(secret, request=request)
        raise httpx.ConnectError(secret, request=request)

    async with _client(handler) as client:
        check = await oidc.probe_authorization_endpoint(
            client,
            AUTHORIZATION_ENDPOINT,
            "client-123",
            REDIRECT_URI,
        )

    assert check["status"] == "fail"
    assert secret not in _check_text(check)
    expected = "Timed out" if failure == "timeout" else "Failed to reach"
    assert expected in check["message"]


@pytest.mark.asyncio
async def test_authorization_probe_stops_buffering_at_body_limit():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=b"x" * (oidc._MAX_BODY_BYTES + 1), request=request)

    async with _client(handler) as client:
        check = await oidc.probe_authorization_endpoint(
            client,
            AUTHORIZATION_ENDPOINT,
            "client-123",
            REDIRECT_URI,
        )

    assert check["status"] == "fail"
    assert check["message"] == "IdP authorization endpoint returned HTTP 400."


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (401, {"error": "invalid_grant"}, "fail"),
        (400, {"error": "INVALID_CLIENT"}, "fail"),
        (400, {"error": "unauthorized_client"}, "fail"),
        (400, {"error": "invalid_grant"}, "pass"),
    ],
)
@pytest.mark.asyncio
async def test_client_secret_probe_posts_configuration_without_leaking_secret(status, payload, expected):
    client_secret = "never-render-this-client-secret"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == TOKEN_ENDPOINT
        assert parse_qs(request.content.decode()) == {
            "grant_type": ["authorization_code"],
            "code": ["observal_validate_invalid_code"],
            "redirect_uri": [REDIRECT_URI],
            "client_id": ["client-123"],
            "client_secret": [client_secret],
        }
        return httpx.Response(status, json=payload, request=request)

    async with _client(handler) as client:
        check = await oidc.probe_client_secret(
            client,
            TOKEN_ENDPOINT,
            "client-123",
            client_secret,
            REDIRECT_URI,
        )

    assert check["status"] == expected
    assert client_secret not in _check_text(check)


@pytest.mark.asyncio
async def test_client_secret_probe_skips_absent_secret_and_unreachable_endpoint():
    unused_client = MagicMock()
    unused_client.post = AsyncMock()
    assert await oidc.probe_client_secret(unused_client, TOKEN_ENDPOINT, "client", "", REDIRECT_URI) is None
    unused_client.post.assert_not_awaited()

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("token endpoint unavailable", request=request)

    async with _client(handler) as client:
        assert await oidc.probe_client_secret(client, TOKEN_ENDPOINT, "client", "secret", REDIRECT_URI) is None


@pytest.mark.asyncio
async def test_client_secret_probe_treats_non_json_non_auth_rejection_as_reachable():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"not-json", request=request)

    async with _client(handler) as client:
        check = await oidc.probe_client_secret(client, TOKEN_ENDPOINT, "client", "secret", REDIRECT_URI)

    assert check["status"] == "pass"


@pytest.mark.asyncio
async def test_jwks_probe_requires_uri_and_active_signing_keys():
    missing = await oidc.probe_jwks(MagicMock(), {})
    assert missing["status"] == "fail"
    assert "jwks_uri" in missing["message"]

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == JWKS_URI
        return httpx.Response(200, json={"keys": [{"kid": "key-1", "use": "sig"}]}, request=request)

    async with _client(handler) as client:
        passed = await oidc.probe_jwks(client, {"jwks_uri": JWKS_URI})

    assert passed["status"] == "pass"


@pytest.mark.parametrize("payload", [{}, {"keys": []}, {"keys": "not-a-list"}, ["not-an-object"]])
@pytest.mark.asyncio
async def test_jwks_probe_rejects_missing_or_malformed_key_sets(payload):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    async with _client(handler) as client:
        check = await oidc.probe_jwks(client, {"jwks_uri": JWKS_URI})

    assert check["status"] == "fail"
    assert "signing keys" in check["message"]


@pytest.mark.parametrize("failure", ["invalid-json", "timeout"])
@pytest.mark.asyncio
async def test_jwks_probe_sanitizes_unreachable_or_invalid_responses(failure):
    secret = "jwks-provider-secret"

    async def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout(secret, request=request)
        return httpx.Response(200, content=b"invalid-json", request=request)

    async with _client(handler) as client:
        check = await oidc.probe_jwks(client, {"jwks_uri": JWKS_URI})

    assert check["status"] == "fail"
    assert "unreachable or returned invalid JSON" in check["message"]
    assert secret not in _check_text(check)


@pytest.mark.parametrize(
    ("metadata", "url", "expected", "message"),
    [
        ({}, METADATA_URL, None, None),
        ({"issuer": "https://idp.example.test/"}, METADATA_URL, "pass", None),
        (
            {"issuer": "https://other.example.test/tenant"},
            METADATA_URL,
            "fail",
            "other.example.test",
        ),
        ({"issuer": "urn:issuer:value"}, METADATA_URL, "pass", None),
    ],
)
def test_issuer_consistency(metadata, url, expected, message):
    check = oidc.check_issuer_consistency(metadata, url)
    if expected is None:
        assert check is None
        return
    assert check["status"] == expected
    if message:
        assert message in check["message"]


@pytest.mark.parametrize(
    ("methods", "expected"),
    [
        (None, None),
        ([], None),
        (["CLIENT_SECRET_BASIC"], "pass"),
        (["client_secret_post", "private_key_jwt"], "fail"),
    ],
)
def test_token_endpoint_auth_method_support(methods, expected):
    check = oidc.check_token_endpoint_auth_methods({"token_endpoint_auth_methods_supported": methods})
    if expected is None:
        assert check is None
    else:
        assert check["status"] == expected


@pytest.mark.parametrize(
    ("algorithms", "expected"),
    [
        (None, None),
        ([None, 123], None),
        (["none"], "fail"),
        (["HS512"], "fail"),
        (["none", "RS256"], "pass"),
        (["EdDSA"], "pass"),
    ],
)
def test_id_token_signing_algorithm_support(algorithms, expected):
    check = oidc.check_id_token_signing_alg({"id_token_signing_alg_values_supported": algorithms})
    if expected is None:
        assert check is None
    else:
        assert check["status"] == expected
        if algorithms == ["none"]:
            assert "unsigned tokens" in check["message"]


@pytest.mark.parametrize(
    ("grants", "expected"),
    [
        (None, None),
        ([None, 1], None),
        (["authorization_code"], "skip"),
        (["AUTHORIZATION_CODE", "REFRESH_TOKEN"], "pass"),
    ],
)
def test_refresh_token_grant(grants, expected):
    check = oidc.check_refresh_token_grant({"grant_types_supported": grants})
    if expected is None:
        assert check is None
    else:
        assert check["status"] == expected


def test_logout_pkce_and_email_metadata_checks():
    assert (
        oidc.check_end_session_endpoint({"end_session_endpoint": "https://idp.example.test/logout"})["status"] == "pass"
    )
    assert oidc.check_end_session_endpoint({})["status"] == "skip"

    assert oidc.check_pkce_methods({})["status"] == "pass"
    pkce = oidc.check_pkce_methods({"code_challenge_methods_supported": [None, "S256", 1]})
    assert pkce["status"] == "skip"
    assert "s256" in pkce["message"]

    assert oidc.check_email_scope({})["status"] == "pass"
    assert oidc.check_email_scope({"scopes_supported": ["OPENID"], "claims_supported": ["sub"]})["status"] == "fail"
    assert oidc.check_email_scope({"scopes_supported": ["OPENID", "EMAIL"]})["status"] == "pass"
    assert oidc.check_email_scope({"scopes_supported": ["openid"], "claims_supported": ["EMAIL"]})["status"] == "pass"


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        return value if tz is not None else value.replace(tzinfo=None)


def test_clock_skew_handles_absent_malformed_naive_and_boundary_values(monkeypatch):
    monkeypatch.setattr(oidc, "datetime", _FrozenDateTime)

    assert oidc.check_clock_skew(None) is None
    assert oidc.check_clock_skew("not-a-date") is None

    monkeypatch.setattr(oidc, "parsedate_to_datetime", lambda _value: datetime(2025, 1, 1, 11, 59, 59))
    assert oidc.check_clock_skew("naive-date")["status"] == "pass"

    monkeypatch.setattr(oidc, "parsedate_to_datetime", parsedate_to_datetime)
    boundary = format_datetime(datetime(2025, 1, 1, 11, 58, 0, tzinfo=UTC), usegmt=True)
    outside = format_datetime(datetime(2025, 1, 1, 11, 57, 59, tzinfo=UTC), usegmt=True)
    assert oidc.check_clock_skew(boundary)["status"] == "pass"
    failed = oidc.check_clock_skew(outside)
    assert failed["status"] == "fail"
    assert "121s" in failed["message"]


def _mock_async_client(monkeypatch):
    client = MagicMock(name="oidc-client")
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(oidc.httpx, "AsyncClient", factory)
    return client, factory


@pytest.mark.asyncio
async def test_run_oidc_checks_uses_one_bounded_non_redirecting_client_and_aggregates(monkeypatch):
    client, factory = _mock_async_client(monkeypatch)
    metadata = {
        "issuer": "https://idp.example.test",
        "authorization_endpoint": AUTHORIZATION_ENDPOINT,
        "token_endpoint": TOKEN_ENDPOINT,
        "jwks_uri": JWKS_URI,
        "scopes_supported": ["openid", "email"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "end_session_endpoint": "https://idp.example.test/logout",
    }
    discovery = make_check("discovery_doc", "Discovery", "pass")
    fetch = AsyncMock(return_value=(metadata, None, discovery))
    authz = AsyncMock(return_value=make_check("authorization_endpoint", "Authorization", "pass"))
    secret = AsyncMock(return_value=make_check("client_secret", "Secret", "pass"))
    jwks = AsyncMock(return_value=make_check("jwks_reachable", "JWKS", "pass"))
    monkeypatch.setattr(oidc, "fetch_discovery", fetch)
    monkeypatch.setattr(oidc, "probe_authorization_endpoint", authz)
    monkeypatch.setattr(oidc, "probe_client_secret", secret)
    monkeypatch.setattr(oidc, "probe_jwks", jwks)

    checks, returned_metadata = await oidc.run_oidc_checks(
        METADATA_URL,
        "client-123",
        "client-secret",
        REDIRECT_URI,
    )

    assert returned_metadata is metadata
    assert all_pass(checks) is True
    assert [check["name"] for check in checks] == [
        "discovery_doc",
        "authorization_endpoint",
        "client_secret",
        "jwks_reachable",
        "email_scope",
        "issuer_host_matches",
        "token_auth_method_supported",
        "id_token_alg",
        "refresh_token_grant",
        "end_session_endpoint",
        "pkce_supported",
    ]
    factory.assert_called_once_with(timeout=10.0, follow_redirects=False)
    fetch.assert_awaited_once_with(client, METADATA_URL)
    authz.assert_awaited_once_with(client, AUTHORIZATION_ENDPOINT, "client-123", REDIRECT_URI)
    secret.assert_awaited_once_with(client, TOKEN_ENDPOINT, "client-123", "client-secret", REDIRECT_URI)
    jwks.assert_awaited_once_with(client, metadata)


@pytest.mark.asyncio
async def test_run_oidc_checks_stops_after_failed_discovery(monkeypatch):
    _client_mock, factory = _mock_async_client(monkeypatch)
    failure = make_check("discovery_doc", "Discovery", "fail", "unreachable")
    monkeypatch.setattr(oidc, "fetch_discovery", AsyncMock(return_value=(None, None, failure)))
    authorization = AsyncMock()
    monkeypatch.setattr(oidc, "probe_authorization_endpoint", authorization)

    checks, metadata = await oidc.run_oidc_checks(METADATA_URL, "client", "secret", REDIRECT_URI)

    assert checks == [failure]
    assert metadata is None
    factory.assert_called_once_with(timeout=10.0, follow_redirects=False)
    authorization.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_oidc_checks_reports_missing_endpoints_and_omits_skipped_secret(monkeypatch):
    _mock_async_client(monkeypatch)
    metadata = {"jwks_uri": JWKS_URI}
    monkeypatch.setattr(
        oidc,
        "fetch_discovery",
        AsyncMock(return_value=(metadata, None, make_check("discovery_doc", "Discovery", "pass"))),
    )
    secret_probe = AsyncMock(return_value=None)
    jwks_probe = AsyncMock(return_value=make_check("jwks_reachable", "JWKS", "pass"))
    monkeypatch.setattr(oidc, "probe_client_secret", secret_probe)
    monkeypatch.setattr(oidc, "probe_jwks", jwks_probe)

    checks, _ = await oidc.run_oidc_checks(METADATA_URL, "client", "", REDIRECT_URI)

    by_name = {check["name"]: check for check in checks}
    assert by_name["authorization_endpoint"]["status"] == "fail"
    assert by_name["client_secret"]["status"] == "fail"
    assert by_name["jwks_reachable"]["status"] == "pass"
    secret_probe.assert_not_awaited()

    metadata["authorization_endpoint"] = AUTHORIZATION_ENDPOINT
    metadata["token_endpoint"] = TOKEN_ENDPOINT
    authorization_probe = AsyncMock(return_value=make_check("authorization_endpoint", "Authorization", "pass"))
    monkeypatch.setattr(oidc, "probe_authorization_endpoint", authorization_probe)

    checks, _ = await oidc.run_oidc_checks(METADATA_URL, "client", "", REDIRECT_URI)

    assert not any(check["name"] == "client_secret" for check in checks)
    secret_probe.assert_awaited_once()


class _ExpiringRedis:
    def __init__(self, clock):
        self.clock = clock
        self.entries: dict[str, tuple[float, str]] = {}
        self.setex_calls: list[tuple[str, int, str]] = []

    async def setex(self, key: str, ttl: int, value: str):
        self.entries[key] = (self.clock() + ttl, value)
        self.setex_calls.append((key, ttl, value))

    async def get(self, key: str):
        entry = self.entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self.clock() >= expires_at:
            self.entries.pop(key, None)
            return None
        return value


def _diagnostic_store(monkeypatch):
    now = {"value": 1_000.0}
    redis = _ExpiringRedis(lambda: now["value"])
    monkeypatch.setattr(diagnostics, "get_redis", lambda: redis)
    monkeypatch.setattr(diagnostics, "time", SimpleNamespace(time=lambda: now["value"]))
    monkeypatch.setattr(diagnostics, "new_session_id", lambda: "diagnostic-session-1")
    return now, redis


@pytest.mark.asyncio
async def test_diagnostic_session_persists_updates_and_expires(monkeypatch):
    now, redis = _diagnostic_store(monkeypatch)

    session_id, created = await diagnostics.create_session("oidc", "e2e")
    assert session_id == "diagnostic-session-1"
    assert created["started_at"] == 1_000.0
    assert redis.setex_calls[-1][0:2] == ("sso_diag:diagnostic-session-1", 600)

    check = make_check("token_exchange", "Token exchange", "fail", "invalid grant")
    await diagnostics.append_check(session_id, check)
    await diagnostics.record_actor(session_id, "operator@example.test")
    now["value"] = 1_025.0
    await diagnostics.finalize(session_id, summary="login failed")

    stored = await diagnostics.get_session(session_id)
    assert stored["checks"] == [check]
    assert stored["actor_email"] == "operator@example.test"
    assert stored["summary"] == "login failed"
    assert stored["ok"] is False
    assert stored["finished_at"] == 1_025.0

    stored["nonce"] = "private-nonce"
    assert "nonce" not in diagnostics.public_view(stored)

    now["value"] = 1_626.0
    assert await diagnostics.get_session(session_id) is None


@pytest.mark.asyncio
async def test_diagnostic_session_handles_missing_corrupt_and_failed_redis(monkeypatch):
    now, redis = _diagnostic_store(monkeypatch)
    await diagnostics.append_check("missing", make_check("x", "X", "pass"))
    await diagnostics.record_actor("missing", "nobody@example.test")
    await diagnostics.record_actor("missing", None)

    redis.entries["sso_diag:corrupt"] = (now["value"] + 600, "not-json")
    assert await diagnostics.get_session("corrupt") is None

    broken = MagicMock()
    broken.get = AsyncMock(side_effect=RedisError("redis contains a secret"))
    broken.setex = AsyncMock(side_effect=RedisError("redis contains a secret"))
    monkeypatch.setattr(diagnostics, "get_redis", lambda: broken)
    assert await diagnostics.get_session("safe-id") is None
    await diagnostics.save_session({"session_id": "safe-id"})
    with pytest.raises(RedisError):
        await diagnostics.create_session("oidc", "real")


@pytest.mark.asyncio
async def test_finalize_reconstructs_real_oidc_session_and_replaces_checks(monkeypatch):
    now, _redis = _diagnostic_store(monkeypatch)
    now["value"] = 2_000.0
    checks = [make_check("callback", "Callback", "pass")]

    await diagnostics.finalize(
        "new-real-session",
        checks=checks,
        summary="complete",
        actor_email="person@example.test",
    )

    stored = await diagnostics.get_session("new-real-session")
    assert stored["provider"] == "oidc"
    assert stored["mode"] == "real"
    assert stored["checks"] == checks
    assert stored["ok"] is True
    assert stored["actor_email"] == "person@example.test"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        ("", False),
        ("safe_ID-123", True),
        ("bad/id", False),
        ("a" * 65, False),
    ],
)
def test_diagnostic_session_id_validation(value, expected):
    assert diagnostics.is_safe_session_id(value) is expected


def test_result_pages_escape_untrusted_diagnostic_values():
    hostile = '<script>alert("secret")</script>'
    checks = [
        {"name": "fallback", "status": "pass"},
        {"name": "failed", "label": hostile, "status": "fail", "message": hostile},
        {"name": "unknown", "label": "Unknown", "status": "unexpected"},
    ]
    page = diagnostics.render_result_page(
        session_id=hostile,
        provider="oidc",
        ok=False,
        checks=checks,
        actor_email=hostile,
        summary=hostile,
        admin_url='https://app.example.test/admin/sso?next=" onclick="alert(1)',
    )

    assert hostile not in page
    assert "&lt;script&gt;" in page
    assert "<code>invalid</code>" in page
    assert "OIDC / OAuth 2.0" in page
    assert "Issues detected" in page
    css_prefix = "var(" + chr(45) * 2
    assert css_prefix + "pass)" in page
    assert css_prefix + "fail)" in page
    assert css_prefix + "skip)" in page
    assert 'onclick="alert(1)' not in page

    passed = diagnostics.render_result_page(
        session_id="safe-id",
        provider="saml",
        ok=True,
        checks=[],
        actor_email=None,
        summary=None,
        admin_url="https://app.example.test/admin/sso",
    )
    assert "End-to-end test passed" in passed
    assert "SAML 2.0" in passed
    assert "No checks recorded" in passed
    assert "(not returned)" in passed

    error = diagnostics.render_error_page(hostile, hostile, 'https://app.example.test/"bad')
    assert hostile not in error
    assert "Could not run" in error
    assert "&lt;script&gt;" in error


def test_check_rendering_and_status_aggregation_fail_safely():
    assert make_check("ok", "Healthy", "pass") == {"name": "ok", "label": "Healthy", "status": "pass"}
    detailed = make_check("bad", "Unhealthy", "fail", "message", "hint")
    assert detailed["message"] == "message"
    assert detailed["hint"] == "hint"

    invalid = make_check("typo", "Typo", "unknown")
    assert invalid["status"] == "fail"
    assert all_pass([make_check("skip", "Skipped", "skip"), {"name": "odd", "status": "unknown"}]) is True
    assert all_pass([make_check("pass", "Passed", "pass"), detailed]) is False
