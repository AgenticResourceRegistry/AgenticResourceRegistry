# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Gated agent hooks: verified presence, placement in the drift record, and layer identity."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from observal_cli import agent_hooks, layer
from observal_cli.harness.claude_code import ClaudeCodeAdapter

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the gate is POSIX-only")

AGENT = "00000000-0000-4000-8000-000000000001"
HOOK = "77777777-7777-4777-8777-777777777777"
SCRIPT = b"#!/bin/sh\ncat > /dev/null\necho checked >&2\nexit 1\n"
ORIGINAL = ".claude/hooks/probe-fail.sh"
AGENT_FILE = """---
name: probe-agent
hooks:
  Stop:
    - hooks:
        - type: command
          command: "python3 -m observal_cli.hooks.session_push"
---

Body.
"""


@pytest.fixture(autouse=True)
def installed_cli(monkeypatch):
    from observal_cli.shared import launcher

    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: True)


@pytest.fixture
def project(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    root = tmp_path / "project"
    (root / ".claude" / "agents").mkdir(parents=True)
    (root / ".claude" / "hooks").mkdir()
    (root / ".claude" / "agents" / "probe-agent.md").write_text(AGENT_FILE)
    (root / ".claude" / "hooks" / "probe-fail.sh").write_bytes(SCRIPT)
    hook = _hook()
    settings = agent_hooks.plan_settings(
        root / ".claude" / "settings.json", "project:.claude/settings.json", None, "probe-agent", [hook]
    )
    agent_hooks.write_settings(root / ".claude" / "settings.json", settings.data)
    return root


def _hook() -> agent_hooks.AgentHook:
    (hook,) = agent_hooks.build_agent_hooks(
        "probe-agent",
        [{"name": "probe-fail", "event": "PostToolUse", "command": ORIGINAL}],
        [{"type": "hook", "id": HOOK, "local_name": "probe-fail"}],
        "skip",
    )
    return hook


def _component(**overrides) -> dict:
    return {
        "type": "hook",
        "id": HOOK,
        "name": "probe-fail",
        "version": "1.0.0",
        "scope": "project",
        "local_name": "probe-fail",
        "hook_event": "PostToolUse",
        "hook_command": _hook().gated,
        "hook_agent": "probe-agent",
        "hook_config": "project:.claude/settings.json",
        "hook_script": "project:.claude/hooks/probe-fail.sh",
        "hook_integrity": f"sha256-{hashlib.sha256(SCRIPT).hexdigest()}",
        "hook_placement": "gated_settings",
        "hook_original_command": ORIGINAL,
        "hook_agent_profile": "project:.claude/agents/probe-agent.md",
    } | overrides


def _status(project: Path, component: dict | None = None) -> str:
    return ClaudeCodeAdapter().verify_hook_binding(str(project), component or _component())


def _settings(project: Path) -> dict:
    return json.loads((project / ".claude" / "settings.json").read_text())


def _write_settings(project: Path, data: dict) -> None:
    (project / ".claude" / "settings.json").write_text(json.dumps(data))


def test_a_gated_hook_in_settings_is_verified(project):
    assert _status(project) == "verified"


def test_a_copy_left_in_the_agent_file_is_ambiguous(project):
    agent_file = project / ".claude" / "agents" / "probe-agent.md"
    agent_file.write_text(
        AGENT_FILE.replace(
            "---\n\nBody",
            f'  PostToolUse:\n    - hooks:\n        - type: command\n          command: "{ORIGINAL}"\n---\n\nBody',
        )
    )
    assert _status(project) == "unverified"
    agent_file.write_text("---\nhooks: [unclosed\n---\n")
    assert _status(project) == "unverified", "an unreadable agent file cannot rule out a copy"
    agent_file.unlink()
    assert _status(project) == "verified", "a removed agent file has no copy"


def test_any_edit_to_the_owned_group_is_drift(project):
    data = _settings(project)
    data["hooks"]["PostToolUse"][0]["matcher"] = "Bash"
    _write_settings(project, data)
    assert _status(project) == "drifted"


def test_a_group_handed_to_the_user_or_duplicated_is_unverified(project):
    data = _settings(project)
    group = data["hooks"]["PostToolUse"][0]
    del group["_observal"]
    _write_settings(project, data)
    assert _status(project) == "unverified"
    data["hooks"]["PostToolUse"] = [_hook().group("probe-agent")] * 2
    _write_settings(project, data)
    assert _status(project) == "unverified", "two identical entries: a recorded run names neither"


def test_a_removed_group_or_missing_placement_facts_are_not_verified(project):
    assert _status(project, _component(hook_original_command="")) == "unverified"
    assert _status(project, _component(hook_agent_profile="")) == "unverified"
    _write_settings(project, {"hooks": {}})
    assert _status(project) == "drifted"


def test_the_drift_record_names_the_placement_and_the_index_reads_it(project):
    from services.layer_components.normalizer import normalize_snapshot

    registry = {
        "harnesses": {
            "claude-code": {
                "agents": [
                    {
                        "id": AGENT,
                        "name": "probe-agent",
                        "version": "1.0.0",
                        "scope": "project",
                        "directory": str(project),
                        "components": [_component(directory=str(project))],
                    }
                ],
                "standalone": [],
            }
        }
    }
    (record,) = layer._compute_drift(registry, {"claude-code": []}, str(project))["hook_verifications"]
    assert (record["status"], record["hook_placement"]) == ("verified", "gated_settings")
    assert record["location_sha256"] == hashlib.sha256(f"PostToolUse\0{_hook().gated}".encode()).hexdigest()
    pin = {k: _component()[k] for k in ("type", "id", "name", "version", "scope", "local_name")}
    pins = {
        "schema_version": 2,
        "standalone": [],
        "agents": [
            {
                "id": AGENT,
                "name": "probe-agent",
                "version": "1.0.0",
                "harness": "claude-code",
                "scope": "project",
                "components": [pin],
            }
        ],
    }
    (occurrence,) = normalize_snapshot(pins, {"hook_verifications": [record]})
    assert (occurrence.binding_agent, occurrence.binding_placement) == ("probe-agent", "gated_settings")
    (legacy,) = normalize_snapshot(pins, {"hook_verifications": [{**record, "hook_placement": None}]})
    assert legacy.binding_placement == "frontmatter"
    no_agent = {**record, "hook_agent": ""}
    (standalone,) = normalize_snapshot(pins, {"hook_verifications": [no_agent]})
    assert standalone.binding_placement == "frontmatter", "placement is meaningless without an agent"


def test_only_a_gated_placement_changes_the_layer_identity_entry(project):
    def registry(component: dict) -> dict:
        agent = {
            "id": AGENT,
            "name": "probe-agent",
            "version": "1.0.0",
            "scope": "project",
            "directory": str(project),
            "components": [component | {"directory": str(project)}],
        }
        return {"harnesses": {"claude-code": {"agents": [agent], "standalone": []}}}

    def three_fields(component: dict, _scope: str) -> str:
        status = layer._hook_status("claude-code", str(project), component)
        return "|".join([layer.hook_binding_sha256(component), component["hook_agent"], status])

    frontmatter = _component(hook_placement="", hook_command=ORIGINAL)
    legacy = layer._pin_verification_entry(
        registry(frontmatter),
        str(project),
        "hook",
        "hook_integrity",
        "observal-claude-code-hook-verification-v1",
        layer.HOOK_VERIFICATION_PATH,
        require_integrity=True,
        extra=three_fields,
        harness="claude-code",
    )
    assert layer.hook_verification_entry("claude-code", registry(frontmatter), str(project)) == legacy
    gated = layer.hook_verification_entry("claude-code", registry(_component()), str(project))
    assert (
        gated["hash"]
        != layer.hook_verification_entry("claude-code", registry(_component(hook_placement="")), str(project))["hash"]
    )
