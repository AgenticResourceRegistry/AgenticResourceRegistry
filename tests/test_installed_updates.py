# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Shared comparison preserves install context and verifies exact release notes."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from observal_cli import installed_updates
from observal_cli.errors import CliError, ErrorCategory

_AGENT = "11111111-1111-4111-8111-111111111111"
_SKILL = "22222222-2222-4222-8222-222222222222"


def _entry(kind: str = "agent", **extra) -> dict:
    raw = {
        "entry_type": "agent" if kind == "agent" else "standalone",
        "type": kind,
        "id": _AGENT if kind == "agent" else _SKILL,
        "name": "reviewer",
        "namespace": "alice",
        "slug": "reviewer",
        "version": "1.0.0",
        "harness": "pi",
        "scope": "project",
        "directory": "/work/repo",
        "local_name": "reviewer",
        "components": [{"type": "skill", "id": _SKILL, "version": "1.0.0"}],
    }
    return installed_updates.prepare_entry(raw | extra, "lockfile.json")


def test_context_inventory_filters_other_projects_and_unknown_scopes(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from observal_cli import lockfile

    local = tmp_path / "local"
    other = tmp_path / "other"
    local.mkdir()
    other.mkdir()
    raw = [
        {"entry_type": "agent", "id": _AGENT, "version": "1.0.0", "harness": "pi", "scope": "user"},
        {
            "entry_type": "agent",
            "id": _AGENT,
            "version": "1.0.0",
            "harness": "pi",
            "scope": "project",
            "directory": str(local),
        },
        {
            "entry_type": "agent",
            "id": _AGENT,
            "version": "1.0.0",
            "harness": "pi",
            "scope": "project",
            "directory": str(other),
        },
        {"entry_type": "agent", "id": _AGENT, "version": "1.0.0", "harness": "pi"},
    ]
    monkeypatch.setattr(lockfile, "get_all_entries", lambda harness: raw)
    relevant = installed_updates.inventory_for_context("pi", str(local))
    assert [item["scope"] for item in relevant] == ["user", "project"]
    assert relevant[1]["directory"] == str(local)


def test_exact_approved_agent_release_and_private_install_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    get = MagicMock(
        side_effect=[
            {"latest_approved_version": "2.0.0", "version": "3.0.0", "namespace": "new", "slug": "reviewer"},
            {
                "version": "2.0.0",
                "status": "approved",
                "supported_harnesses": ["pi"],
                "description": "Better review guidance",
                "components": [{"component_type": "skill", "component_id": _SKILL, "resolved_version": "1.1.0"}],
            },
        ]
    )
    monkeypatch.setattr(installed_updates.client, "get", get)
    item = installed_updates.compare([_entry()], verify_releases=True)[0]
    assert item["status"] == "outdated"
    assert item["latest_version"] == "2.0.0"
    assert item["release"]["description"] == "Better review guidance"
    assert item["release"]["components"][0]["resolved_version"] == "1.1.0"
    assert (item["scope"], item["directory"], item["local_name"]) == ("project", "/work/repo", "reviewer")
    assert "scope" not in installed_updates.public_result(item)
    assert "release" not in installed_updates.public_result(item)
    assert get.call_args_list[1].args[0] == f"/api/v1/agents/{_AGENT}/versions/2.0.0"


def test_component_changelog_and_explicit_pin_are_notice_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        installed_updates.client,
        "get",
        MagicMock(
            side_effect=[
                {"version": "2.0.0"},
                {
                    "version": "2.0.0",
                    "status": "approved",
                    "supported_harnesses": ["pi"],
                    "description": "New checks",
                    "changelog": "Fixes bracket [x] formatting",
                },
            ]
        ),
    )
    result = installed_updates.compare([_entry("skill", requested_version="1.0.0")], verify_releases=True)[0]
    assert result["status"] == "skipped"
    assert "explicitly pinned" in result["reason"]
    assert result["release"]["changelog"] == "Fixes bracket [x] formatting"
    assert result["outdated"] is True  # availability is independent of install eligibility
    assert result["release_verified"] is True


@pytest.mark.parametrize(
    "detail",
    [
        {"version": "2.0.0", "status": "pending", "supported_harnesses": ["pi"]},
        {"version": "2.0.0", "status": "approved", "supported_harnesses": ["kiro"]},
        {"version": "3.0.0", "status": "approved", "supported_harnesses": ["pi"]},
        {"version": "2.0.0", "status": "approved"},
    ],
)
def test_unapproved_incompatible_and_malformed_target_are_not_verified(
    detail: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        installed_updates.client,
        "get",
        MagicMock(
            side_effect=[
                {"latest_approved_version": "2.0.0"},
                detail,
            ]
        ),
    )
    result = installed_updates.compare([_entry()], verify_releases=True)[0]
    assert result["status"] == "skipped"
    assert result["release"] is None
    assert result["outdated"] is True
    assert result["release_verified"] is False


def test_no_approved_agent_release_never_falls_back_to_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    get = MagicMock(return_value={"version": "3.0.0", "latest_approved_version": None})
    monkeypatch.setattr(installed_updates.client, "get", get)
    result = installed_updates.compare([_entry()], verify_releases=True)[0]
    assert result["status"] == "missing"
    assert result["latest_version"] is None
    get.assert_called_once()


def test_inaccessible_exact_release_is_notice_only(monkeypatch: pytest.MonkeyPatch) -> None:
    not_found = CliError(ErrorCategory.NOT_FOUND, "Not found", operation="Check installed versions")
    monkeypatch.setattr(
        installed_updates.client,
        "get",
        MagicMock(
            side_effect=[
                {"latest_approved_version": "2.0.0"},
                not_found,
            ]
        ),
    )
    result = installed_updates.compare([_entry()], verify_releases=True)[0]
    assert result["status"] == "skipped"
    assert "accessible" in result["reason"]
    assert result["outdated"] is True
    assert result["release_verified"] is False


@pytest.mark.parametrize(
    ("entry", "managed"),
    [
        ({"entry_type": "standalone", "type": "skill", "folder_receipt": {"version_id": "v", "target": "/x"}}, True),
        ({"entry_type": "agent", "components": [{"type": "skill", "folder_receipt": {"target": "/x"}}]}, True),
        ({"entry_type": "agent", "components": [{"type": "skill"}, "not-a-dict"]}, False),
        ({"entry_type": "standalone", "type": "skill", "folder_receipt": "forged-string"}, False),
        ({"entry_type": "standalone", "type": "skill"}, False),
    ],
)
def test_folder_receipts_mark_items_as_reviewed_upgrade_only(entry: dict, managed: bool) -> None:
    """Startup updates must recognise main's managed folder receipts without exposing them publicly."""
    item = installed_updates.prepare_entry({"id": "i", "harness": "pi", "version": "1.0.0", **entry}, "lock")
    assert item["folder_managed"] is managed
    assert "folder_managed" not in installed_updates._PUBLIC_FIELDS
