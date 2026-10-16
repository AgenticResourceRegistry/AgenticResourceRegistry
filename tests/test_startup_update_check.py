# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Check-only Pi notices: scoped inventory, verified notes and durable replay."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from observal_cli import auto_update_policy, installed_updates
from observal_cli import startup_update_check as check

if TYPE_CHECKING:
    from pathlib import Path

REGISTRY = "https://registry.example"
KEY = "a" * 64


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(check, "NOTICE_DIR", tmp_path / "notices")
    monkeypatch.setattr(check, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(auto_update_policy, "active_registry", lambda: REGISTRY)
    monkeypatch.setattr(auto_update_policy, "active_account", lambda: "alice")
    monkeypatch.setattr(auto_update_policy, "policy_status", lambda _: {"effective": False})
    return tmp_path


def _entry() -> dict:
    return {
        "id": "item",
        "type": "agent",
        "qualified_name": "alice/code",
        "harness": "pi",
        "scope": "user",
        "directory": "/home/alice",
        "current_version": "1.0",
        "latest_version": "2.0",
        "outdated": True,
        "release_verified": True,
        "status": "outdated",
        "release": {"description": "Author's notes", "changelog": "What changed"},
    }


def test_worker_check_only_caches_releases_and_writes_private_notice(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = {**_entry(), "components": [{"env": {"PASSWORD": "must-not-be-cached"}}]}
    inventory = MagicMock(return_value=[item])
    compare = MagicMock(return_value=[item])
    monkeypatch.setattr(installed_updates, "inventory_for_context", inventory)
    monkeypatch.setattr(installed_updates, "compare", compare)

    check.check_pi(str(isolated), "session-1", KEY)
    notice = check.NOTICE_DIR / f"{KEY}.json"
    first = json.loads(notice.read_text())
    assert notice.stat().st_mode & 0o777 == 0o600
    assert first["registry"] == REGISTRY
    assert first["account_id"] == "alice"
    assert first["effective_in_current_session"] == "unknown"
    assert first["items"][0]["description"] == "Author's notes"
    assert first["items"][0]["manual_command"] == "observal agent pull alice/code --harness pi --upgrade --scope user"
    compare.assert_called_once_with([item], verify_releases=True)
    assert "must-not-be-cached" not in next(check.CACHE_DIR.glob("*.json")).read_text()
    inventory.assert_called_once_with("pi", str(isolated))
    check.check_pi(str(isolated), "session-2", "b" * 64)
    compare.assert_called_once()


def test_unverified_or_pinned_newer_release_still_noticed(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    item = {**_entry(), "release_verified": False, "release": None, "reason": "Release is not accessible"}
    monkeypatch.setattr(installed_updates, "inventory_for_context", lambda *_: [item])
    monkeypatch.setattr(installed_updates, "compare", lambda *_, **__: [item])
    check.check_pi(str(isolated), "session-1", KEY)
    result = json.loads((check.NOTICE_DIR / f"{KEY}.json").read_text())["items"][0]
    assert result["status"] == "unverified"
    assert result["description"] == ""
    assert result["manual_command"] is None
    assert "accessible" in result["reason"]


def test_broken_policy_does_not_suppress_notices(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    item = _entry()
    monkeypatch.setattr(installed_updates, "inventory_for_context", lambda *_: [item])
    monkeypatch.setattr(installed_updates, "compare", lambda *_, **__: [item])
    monkeypatch.setattr(
        auto_update_policy, "policy_status", lambda _: (_ for _ in ()).throw(auto_update_policy.PolicyError("secret"))
    )
    check.check_pi(str(isolated), "session-1", KEY)
    result = json.loads((check.NOTICE_DIR / f"{KEY}.json").read_text())
    assert result["items"][0]["status"] == "available"
    assert "unreadable" in result["warning"]
    assert "secret" not in json.dumps(result)


def test_offline_failure_is_secret_free_and_retryable(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installed_updates, "inventory_for_context", lambda *_: [_entry()])
    monkeypatch.setattr(installed_updates, "compare", MagicMock(side_effect=ValueError("Bearer private-token")))
    check.check_pi(str(isolated), "session-1", KEY)
    raw = (check.NOTICE_DIR / f"{KEY}.json").read_text()
    assert "private-token" not in raw
    assert "could not complete" in raw
    assert list(check.CACHE_DIR.glob("*.json")) == []


def test_rejects_unsafe_spool_key(isolated: Path) -> None:
    with pytest.raises(ValueError, match="notice key"):
        check.check_pi(".", "session-1", "../escape")
    assert not check.NOTICE_DIR.exists()
