# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Pi registry hooks in the CLI: bound at pull time, verified against the active file.

A pulled hook is bound from the ``observal-hooks.json`` the pull wrote, and is
verified only while the Observal extension would load that exact entry from
``~/.pi/agent/observal-hooks.json`` (docs/integrations/pi.md, "Registry hooks").
The extension mirrors this byte for byte (``packages/pi-extension/tests/hook-runner.test.ts``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from observal_cli import layer, pi_hooks

AGENT = "00000000-0000-4000-8000-000000000001"
GUARD = "66666666-6666-4666-8666-666666666666"
PI_HOOKS_SCHEMA = pi_hooks.PI_HOOKS_SCHEMA


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


GUARD_ENTRY = {
    "name": "guard",
    "event": "tool_call",
    "type": "command",
    "command": "./guard.sh --strict",
    "timeout": 60,
}


def _write_hooks(path: Path, hooks: list[dict], agent: str = "reviewer") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": PI_HOOKS_SCHEMA, "agent": agent, "hooks": hooks}), encoding="utf-8")
    return path


def _lock_component() -> dict:
    return {"type": "hook", "name": "guard", "id": GUARD, "version": "1.0.0", "scope": "user", "local_name": "guard"}


def _bound(home: Path, hooks: list[dict] | None = None) -> dict:
    profile = _write_hooks(home / ".pi/agent/agents/reviewer/observal-hooks.json", hooks or [GUARD_ENTRY])
    component = _lock_component() | {"hook_event": "stale", "hook_placement": "gated_settings"}
    assert pi_hooks.bind_pulled_hooks(profile, [component]) == []
    return component


def _status(home: Path, component: dict) -> str:
    registry = {
        "harnesses": {
            "pi": {
                "agents": [
                    {"id": AGENT, "name": "reviewer", "version": "1.0.0", "scope": "user", "components": [component]}
                ],
                "standalone": [],
            }
        }
    }
    drift = layer._compute_drift(registry, {"pi": []}, str(home))
    (record,) = drift["hook_verifications"]
    assert record["hook_agent"] == ""
    assert record["hook_placement"] == "settings"
    return record["status"]


def test_pull_binds_the_hook_from_the_file_it_wrote(home):
    component = _bound(home)
    assert component == _lock_component() | {
        "hook_event": "tool_call",
        "hook_command": "./guard.sh --strict",
        "hook_agent": "",
        "hook_config": "user:observal-hooks.json",
        "hook_profile": "reviewer",
        "hook_integrity": pi_hooks.entry_integrity("reviewer", GUARD_ENTRY),
    }, "a stale placement from an earlier pull must not survive"


def test_a_hook_not_in_the_written_file_stays_unbound(home):
    other = dict(GUARD_ENTRY, name="other")
    component = _bound(home, [other])
    assert not any(key.startswith("hook_") for key in component)


def test_verified_only_while_the_profile_is_active(home):
    component = _bound(home)
    assert _status(home, component) == "unverified", "pulled but not activated with /agent"
    _write_hooks(home / ".pi/agent/observal-hooks.json", [GUARD_ENTRY])
    assert _status(home, component) == "verified"


@pytest.mark.parametrize(
    ("active", "agent", "expected"),
    [
        ([dict(GUARD_ENTRY, timeout=5)], "reviewer", "drifted"),
        ([dict(GUARD_ENTRY, command="./guard.sh")], "reviewer", "unverified"),
        ([GUARD_ENTRY, dict(GUARD_ENTRY, name="copy")], "reviewer", "unverified"),
        ([GUARD_ENTRY], "another-agent", "unverified"),
        ([GUARD_ENTRY, dict(GUARD_ENTRY, name="web", type="http")], "reviewer", "unverified"),
    ],
    ids=["edited-timeout", "edited-command", "duplicate", "other-profile", "invalid-entry"],
)
def test_edited_duplicate_shadowed_or_invalid_hooks_are_not_verified(home, active, agent, expected):
    component = _bound(home)
    _write_hooks(home / ".pi/agent/observal-hooks.json", active, agent)
    assert _status(home, component) == expected


def test_unreadable_active_file_is_unverified(home):
    component = _bound(home)
    (home / ".pi/agent/observal-hooks.json").write_text("{not json", encoding="utf-8")
    assert _status(home, component) == "unverified"


def test_windows_is_unverified(home, monkeypatch):
    component = _bound(home)
    _write_hooks(home / ".pi/agent/observal-hooks.json", [GUARD_ENTRY])
    monkeypatch.setattr(pi_hooks.sys, "platform", "win32")
    assert _status(home, component) == "unverified"


def test_hook_files_are_hash_only_layer_inputs():
    from observal_cli.harness.pi import PiAdapter as CliPiAdapter

    adapter = CliPiAdapter()
    assert adapter.redact_layer_content("user:observal-hooks.json")
    assert adapter.redact_layer_content("user:agents/reviewer/observal-hooks.json")
    assert "pi" in layer.HOOK_VERIFICATION_HARNESSES
