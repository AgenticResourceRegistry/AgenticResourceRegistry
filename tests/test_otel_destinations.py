# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""OTLP destination parsing, validation and the admin settings route that saves them."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import services.ssrf_guard as ssrf_guard
from api.routes.admin import enterprise_settings as es
from models.user import UserRole
from schemas.admin import EnterpriseConfigUpdate
from services.otel import destinations as dest_module
from services.otel.destinations import (
    Destination,
    DestinationError,
    load_destinations,
    parse_destinations,
    prepare_setting_value,
)

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
SECRET_HEADER = "Bearer super-secret-token"


def _item(**overrides) -> dict:
    item = {
        "id": "collector",
        "url": "https://otel.example.com/",
        "signals": ["logs"],
        "headers": {"Authorization": SECRET_HEADER},
    }
    item.update(overrides)
    return item


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr(ssrf_guard, "is_private_url", lambda url: "internal" in url)


def test_parses_defaults_and_strips_trailing_slash():
    (destination,) = parse_destinations(json.dumps([_item()]))

    assert destination.url == "https://otel.example.com"
    assert destination.endpoint("logs") == "https://otel.example.com/v1/logs"
    assert destination.protocol == "http/protobuf"
    assert destination.include_content is False
    assert destination.enabled is True
    assert destination.added_at is None


def test_empty_and_default_values_mean_no_destinations():
    assert parse_destinations("[]") == []
    assert parse_destinations("") == []
    assert parse_destinations(None) == []


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("not json", "JSON list"),
        (json.dumps({"id": "x"}), "JSON list"),
        (json.dumps([_item(url="http://otel.example.com")]), "collector: url: Value error, must be an https URL"),
        (json.dumps([_item(url="https://user:pw@otel.example.com")]), "must not carry credentials"),
        (json.dumps([_item(url="https://otel.example.com/?token=1")]), "query string"),
        (json.dumps([_item(signals=[])]), "at least one signal"),
        (json.dumps([_item(signals=["logs", "logs"])]), "must not repeat"),
        (json.dumps([_item(signals=["traces"])]), "signal 'traces' is not supported yet"),
        (json.dumps([_item(signals=["metrics"])]), "collector: signals.0"),
        (json.dumps([_item(protocol="grpc")]), "collector: protocol"),
        (json.dumps([_item(id="Has Spaces")]), "destination Has Spaces: id"),
        (json.dumps([_item(surprise=True)]), "collector: surprise: Extra inputs are not permitted"),
        (json.dumps([_item(), _item()]), "collector: id is used more than once"),
        (json.dumps([_item(headers={"Bad Name": "x"})]), "header name 'Bad Name' is not valid"),
        (json.dumps([_item(headers={"X-Key": "a\r\nInjected: 1"})]), "must not contain line breaks"),
        (json.dumps(["nope"]), "destination #1"),
    ],
)
def test_rejects_invalid_destinations_with_a_message_naming_them(raw, message):
    with pytest.raises(DestinationError) as excinfo:
        parse_destinations(raw)

    assert message in str(excinfo.value)


def test_error_messages_never_echo_header_values():
    bad = json.dumps([_item(url="ftp://otel.example.com", headers={"Authorization": SECRET_HEADER})])

    with pytest.raises(DestinationError) as excinfo:
        parse_destinations(bad)

    assert SECRET_HEADER not in str(excinfo.value)
    assert "super-secret" not in str(excinfo.value)


def test_naive_added_at_is_read_as_utc():
    (destination,) = parse_destinations(json.dumps([_item(added_at="2026-09-01T00:00:00")]))

    assert destination.added_at == datetime(2026, 9, 1, tzinfo=UTC)


def test_prepare_stamps_new_destinations_and_keeps_earlier_start_points():
    earlier = datetime(2026, 9, 1, tzinfo=UTC)
    previous = [Destination(**_item(added_at=earlier.isoformat()))]
    raw = json.dumps([_item(), _item(id="langfuse", url="https://cloud.langfuse.com/api/public/otel")])

    stored, destinations = prepare_setting_value(raw, previous=previous, now=NOW)

    assert [d.added_at for d in destinations] == [earlier, NOW]
    assert [d["added_at"] for d in json.loads(stored)] == ["2026-09-01T00:00:00Z", "2026-10-01T09:00:00Z"]


def test_prepare_rejects_private_hosts():
    with pytest.raises(DestinationError, match="collector: url resolves to a private or internal address"):
        prepare_setting_value(json.dumps([_item(url="https://otel.internal")]), previous=[], now=NOW)


async def test_invalid_stored_value_turns_forwarding_off(monkeypatch):
    import services.dynamic_settings as ds

    monkeypatch.setattr(ds, "get", AsyncMock(return_value="{broken"))

    assert await load_destinations() == []


# ── The admin settings route ─────────────────────────────────────────────


def _db():
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture
def route(monkeypatch):
    previous = AsyncMock(return_value=[])
    clear = AsyncMock()
    monkeypatch.setattr(es.ds, "invalidate", AsyncMock())
    monkeypatch.setattr(es.ds, "refresh_sync_cache", AsyncMock())
    monkeypatch.setattr(es.ds, "is_externally_managed", lambda _key: False)
    monkeypatch.setattr(es.ds, "encrypt_value", lambda value: f"enc:{value}")
    monkeypatch.setattr(es, "emit_security_event", AsyncMock())
    monkeypatch.setattr(dest_module, "load_destinations", previous)
    monkeypatch.setattr("services.otel.forwarder.clear_pauses", clear)
    return SimpleNamespace(previous=previous, clear=clear)


def _admin():
    return SimpleNamespace(id="admin-1", email="admin@example.test", role=UserRole.admin)


async def test_route_stores_the_stamped_list_encrypted_and_lifts_pauses(route):
    db = _db()

    response = await es.upsert_setting(
        es.OTLP_DESTINATIONS_KEY,
        EnterpriseConfigUpdate(value=json.dumps([_item()])),
        db=db,
        current_user=_admin(),
    )

    stored = db.add.call_args.args[0].value
    assert stored.startswith("enc:")
    (saved,) = json.loads(stored.removeprefix("enc:"))
    assert saved["id"] == "collector"
    assert saved["added_at"] is not None
    route.clear.assert_awaited_once_with(["collector"])
    assert SECRET_HEADER not in response.value  # sensitive values come back redacted


async def test_route_rejects_invalid_destinations_with_422_and_stores_nothing(route):
    db = _db()

    with pytest.raises(HTTPException) as excinfo:
        await es.upsert_setting(
            es.OTLP_DESTINATIONS_KEY,
            EnterpriseConfigUpdate(value=json.dumps([_item(url="http://otel.example.com")])),
            db=db,
            current_user=_admin(),
        )

    assert excinfo.value.status_code == 422
    assert "collector" in excinfo.value.detail
    assert SECRET_HEADER not in excinfo.value.detail
    db.add.assert_not_called()
    route.clear.assert_not_awaited()
