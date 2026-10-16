# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Automatic updates are refused without exact pins, unchanged files and consent."""

from __future__ import annotations

from pathlib import Path

import pytest

from observal_cli import auto_update_policy as policy
from observal_cli import config, install_baseline, update_preflight

REGISTRY = "https://example.test"
AGENT_ID = "11111111-1111-4111-8111-111111111111"
COMPONENT_ID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture()
def candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr(policy, "POLICY_PATH", tmp_path / "policy.json")
    monkeypatch.setattr(policy, "GATE_DIR", tmp_path / "gates")
    credentials = {"server_url": REGISTRY, "user_id": "alice", "access_token": "alice-token"}
    monkeypatch.setattr(config, "load", lambda: dict(credentials))
    monkeypatch.setattr(config, "load_persisted", lambda: dict(credentials))
    monkeypatch.setattr(install_baseline, "BASELINE_DIR", tmp_path / "baselines")
    profile = tmp_path / "profile"
    profile.mkdir()
    file = profile / "AGENTS.md"
    file.write_text("managed")
    item = {
        "type": "agent",
        "harness": "pi",
        "scope": "user",
        "directory": str(tmp_path),
        "id": AGENT_ID,
        "current_version": "1.0.0",
        "latest_version": "1.1.0",
        "outdated": True,
        "release_verified": True,
        "status": "outdated",
        "lock_status": "locked",
        "lock_digest": "sha256:example",
        "pin_known": True,
        "requested_version": None,
        "components": [{"type": "skill", "id": COMPONENT_ID, "version": "1.0.0"}],
        "release": {
            "description": "Fix",
            "components": [{"component_type": "skill", "component_id": COMPONENT_ID, "resolved_version": "1.1.0"}],
        },
    }
    policy.set_policy(REGISTRY, enabled=True)
    install_baseline.capture(
        registry=REGISTRY,
        harness="pi",
        agent_id=AGENT_ID,
        scope="user",
        root=str(tmp_path),
        version="1.0.0",
        lock_digest="sha256:example",
        written_paths=[str(file)],
    )
    return item


def test_generated_lock_must_match_exact_approved_pins_before_write(candidate: dict) -> None:
    release = {**candidate["release"], "version": "1.1.0", "status": "approved", "supported_harnesses": ["pi"]}
    locked = {
        "status": "locked",
        "digest": "new-digest",
        "components": [{"type": "skill", "id": COMPONENT_ID, "version": "1.1.0"}],
    }
    update_preflight.require_generated_release_lock(release, locked, version="1.1.0", harness="pi")
    for changed in (
        {**locked, "components": [{"type": "skill", "id": COMPONENT_ID, "version": "1.2.0"}]},
        {**locked, "components": [{"type": "mcp", "id": COMPONENT_ID, "version": "1.1.0"}]},
        {**locked, "components": []},
        {**locked, "components": locked["components"] * 2},
        {**locked, "components": None},
        {**locked, "status": "partial"},
    ):
        with pytest.raises(update_preflight.PreflightSkipError):
            update_preflight.require_generated_release_lock(release, changed, version="1.1.0", harness="pi")
    with pytest.raises(update_preflight.PreflightSkipError, match="approved"):
        update_preflight.require_generated_release_lock(
            {**release, "status": "pending"}, locked, version="1.1.0", harness="pi"
        )


def test_candidate_requires_verified_existing_files_and_same_components(candidate: dict, tmp_path: Path) -> None:
    result = update_preflight.pi_user_agent_candidate(candidate, registry=REGISTRY)
    assert result["target_version"] == "1.1.0"
    assert result["verified_files"] == "1"
    (tmp_path / "profile" / "AGENTS.md").write_text("custom edit")
    with pytest.raises(update_preflight.PreflightSkipError, match="changed"):
        update_preflight.pi_user_agent_candidate(candidate, registry=REGISTRY)


def test_component_add_remove_unknown_and_duplicate_are_notice_only(candidate: dict) -> None:
    extra = {
        "component_type": "prompt",
        "component_id": "33333333-3333-4333-8333-333333333333",
        "resolved_version": "1.0.0",
    }
    candidate["release"]["components"].append(extra)
    with pytest.raises(update_preflight.PreflightSkipError, match="adds or removes"):
        update_preflight.pi_user_agent_candidate(candidate, registry=REGISTRY)
    candidate["release"]["components"] = []
    # Dropping a skill is allowed at preflight (its files are checked by the plan);
    # dropping a prompt is not. Pi may also add or drop MCPs; Claude may not.
    assert update_preflight.pi_user_agent_candidate(candidate, registry=REGISTRY)["target_version"]
    candidate["components"].append({"type": "mcp", "id": "44444444-4444-4444-8444-444444444444", "version": "1.0.0"})
    assert update_preflight.pi_user_agent_candidate(candidate, registry=REGISTRY)["target_version"]
    candidate["components"].pop()
    candidate["components"].append({"type": "prompt", "id": "44444444-4444-4444-8444-444444444444", "version": "1.0.0"})
    with pytest.raises(update_preflight.PreflightSkipError, match="adds or removes"):
        update_preflight.pi_user_agent_candidate(candidate, registry=REGISTRY)
    candidate["components"].pop()
    candidate["components"] = [{"type": "skill", "name": "guessed"}]
    with pytest.raises(update_preflight.PreflightSkipError, match="exact type and registry ID"):
        update_preflight.pi_user_agent_candidate(candidate, registry=REGISTRY)
    candidate["components"] = [{"type": "skill", "id": COMPONENT_ID, "version": "1.0.0"}] * 2
    with pytest.raises(update_preflight.PreflightSkipError, match="duplicate"):
        update_preflight.pi_user_agent_candidate(candidate, registry=REGISTRY)


def test_unreadable_installed_file_remains_notice_only(
    candidate: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.read_bytes

    def unreadable(self: Path) -> bytes:
        if self == tmp_path / "profile" / "AGENTS.md":
            raise PermissionError("denied")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    with pytest.raises(update_preflight.PreflightSkipError, match="could not be read"):
        update_preflight.pi_user_agent_candidate(candidate, registry=REGISTRY)


def test_unknown_or_explicit_agent_pin_is_notice_only(candidate: dict) -> None:
    candidate["pin_known"] = False
    with pytest.raises(update_preflight.PreflightSkipError, match="pin intent is unknown"):
        update_preflight.pi_user_agent_candidate(candidate, registry=REGISTRY)
    candidate["pin_known"] = True
    candidate["requested_version"] = "1.0.0"
    with pytest.raises(update_preflight.PreflightSkipError, match="explicitly pinned"):
        update_preflight.pi_user_agent_candidate(candidate, registry=REGISTRY)


def test_policy_and_lock_are_independent_guards(candidate: dict) -> None:
    policy.set_policy(REGISTRY, enabled=False)
    with pytest.raises(update_preflight.PreflightSkipError, match="frozen"):
        update_preflight.pi_user_agent_candidate(candidate, registry=REGISTRY)
    policy.set_policy(REGISTRY, enabled=True)
    candidate["lock_status"] = "partial"
    with pytest.raises(update_preflight.PreflightSkipError, match="complete component lock"):
        update_preflight.pi_user_agent_candidate(candidate, registry=REGISTRY)
