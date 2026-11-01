# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Verified presence for Claude Code hooks, bound to the exact (event, command) sessions record."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from observal_cli import layer
from observal_cli.harness.claude_code import ClaudeCodeAdapter

AGENT = "00000000-0000-4000-8000-000000000001"
HOOK = "77777777-7777-4777-8777-777777777777"
SCRIPT = b"#!/bin/sh\ncat > /dev/null\necho checked\n"
AGENT_FILE = """---
name: probe-agent
hooks:
  UserPromptSubmit:
    - hooks:
        - type: command
          command: "python3 -m observal_cli.hooks.session_push"
  PostToolUse:
    - hooks:
        - type: command
          command: ".claude/hooks/probe-fail.sh"
---

Body.
"""


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
    return root


def _component(**overrides) -> dict:
    return {
        "type": "hook",
        "id": HOOK,
        "name": "probe-fail",
        "version": "1.0.0",
        "scope": "project",
        "local_name": "probe-fail",
        "hook_event": "PostToolUse",
        "hook_command": ".claude/hooks/probe-fail.sh",
        "hook_agent": "probe-agent",
        "hook_config": "project:.claude/agents/probe-agent.md",
        "hook_script": "project:.claude/hooks/probe-fail.sh",
        "hook_integrity": f"sha256-{hashlib.sha256(SCRIPT).hexdigest()}",
    } | overrides


def _registry(component: dict, directory: Path) -> dict:
    agent = {
        "id": AGENT,
        "name": "probe-agent",
        "version": "1.0.0",
        "scope": "project",
        "directory": str(directory),
        "components": [component | {"directory": str(directory)}],
    }
    return {"harnesses": {"claude-code": {"agents": [agent], "standalone": []}}}


def _record(project: Path, component: dict | None = None) -> dict:
    drift = layer._compute_drift(_registry(component or _component(), project), {"claude-code": []}, str(project))
    (record,) = drift["hook_verifications"]
    return record


def test_server_bindings_match_the_frontmatter_it_writes():
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "observal-server"))
    from services.harness.helpers import _claude_code_hook_bindings, _claude_code_hooks_frontmatter_lines

    hooks = [
        {
            "event": "PostToolUse",
            "handler_type": "command",
            "handler_config": {"command": "probe-fail.sh"},
            "name": "probe-fail",
            "script_filename": "probe-fail.sh",
            "script_content": "x",
        },
        {"event": "Stop", "handler_type": "command", "handler_config": {"command": "make lint"}, "name": "lint"},
        {"event": "Stop", "handler_type": "http", "handler_config": {"url": "https://x"}, "name": "web"},
    ]
    bindings = _claude_code_hook_bindings(hooks)
    assert bindings == [
        {
            "name": "probe-fail",
            "event": "PostToolUse",
            "command": ".claude/hooks/probe-fail.sh",
            "script": ".claude/hooks/probe-fail.sh",
        },
        {"name": "lint", "event": "Stop", "command": "make lint", "script": None},
    ]
    frontmatter = "\n".join(_claude_code_hooks_frontmatter_lines(custom_hooks=hooks))
    for binding in bindings:
        assert f'command: "{binding["command"]}"' in frontmatter


def test_pull_records_the_binding_it_wrote(project):
    from observal_cli.cmd_pull import _record_hook_bindings

    snippet = {
        "agent_profile": {"path": ".claude/agents/probe-agent.md", "content": AGENT_FILE},
        "hook_bindings": [
            {
                "name": "probe-fail",
                "event": "PostToolUse",
                "command": ".claude/hooks/probe-fail.sh",
                "script": ".claude/hooks/probe-fail.sh",
                "agent": "probe-agent",
            }
        ],
    }
    components = [
        {"type": "hook", "id": HOOK, "local_name": "probe-fail"},
        {"type": "hook", "id": "x", "local_name": "web"},
    ]
    assert _record_hook_bindings(snippet, components, project, False) == []
    expected = _component()
    assert {k: v for k, v in components[0].items() if k.startswith("hook_")} == {
        k: v for k, v in expected.items() if k.startswith("hook_")
    }
    assert not any(k.startswith("hook_") for k in components[1]), "unbound hooks stay unverified"


def test_standalone_settings_install_is_bound(project):
    snippet = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "make check"}]}]}}
    binding = ClaudeCodeAdapter().standalone_hook_binding(".claude/settings.json", snippet, [])
    assert binding == {
        "hook_event": "PreToolUse",
        "hook_command": "make check",
        "hook_agent": "",
        "hook_config": "project:.claude/settings.json",
        "hook_script": "",
        "hook_integrity": f"sha256-{hashlib.sha256(b'make check').hexdigest()}",
    }
    two = {"hooks": {"Stop": [{"hooks": [{"command": "a"}, {"command": "b"}]}]}}
    assert ClaudeCodeAdapter().standalone_hook_binding(".claude/settings.json", two, []) is None


def test_configured_hook_with_its_script_is_verified_and_names_its_binding(project):
    record = _record(project)
    assert record["status"] == "verified" and record["hook_agent"] == "probe-agent"
    expected = hashlib.sha256(b"PostToolUse\0.claude/hooks/probe-fail.sh").hexdigest()
    assert record["location_sha256"] == expected


def test_verifier_digest_equals_the_digest_of_what_claude_code_recorded(project):
    from services.session_parsers.hook_evidence import extract_hook_evidence

    lines = Path(__file__).parent / "fixtures/component_insights/claude_code/hook_session_agent_interactive.jsonl"
    rows = [
        {"is_source_record": 1, "line_offset": i, "raw_line": line}
        for i, line in enumerate(lines.read_text().splitlines())
    ]
    failed = [e for e in extract_hook_evidence("claude-code", rows).evidence if e.kind == "failed"]
    assert [e.binding_sha256 for e in failed] == [_record(project)["location_sha256"]]


def test_removed_entry_or_edited_script_is_drifted_and_missing_files_unverified(project):
    agent_file = project / ".claude" / "agents" / "probe-agent.md"
    script = project / ".claude" / "hooks" / "probe-fail.sh"
    script.write_bytes(SCRIPT + b"# edited\n")
    assert _record(project)["status"] == "drifted"
    script.write_bytes(SCRIPT)
    agent_file.write_text(AGENT_FILE.replace("probe-fail.sh", "other.sh"))
    assert _record(project)["status"] == "drifted"
    agent_file.unlink()
    assert _record(project)["status"] == "unverified"
    assert _record(project, _component(hook_integrity=""))["status"] == "unverified"


def test_the_same_binding_configured_elsewhere_is_ambiguous(project):
    settings = {
        "hooks": {
            "PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": ".claude/hooks/probe-fail.sh"}]}]
        }
    }
    (project / ".claude" / "settings.json").write_text(json.dumps(settings))
    assert _record(project)["status"] == "unverified"


def test_a_duplicate_binding_in_the_same_agent_file_is_ambiguous(project):
    """Two identical entries are two hooks; a recorded command cannot say which one ran."""
    agent_file = project / ".claude" / "agents" / "probe-agent.md"
    duplicate = """    - hooks:
        - type: command
          command: ".claude/hooks/probe-fail.sh"
"""
    text = AGENT_FILE.replace(duplicate, duplicate + duplicate)
    assert text.count("probe-fail.sh") == 2
    agent_file.write_text(text)
    assert _record(project)["status"] == "unverified"


@pytest.mark.parametrize(
    ("relative", "content"),
    [
        (".claude/settings.json", "{not json"),
        (".claude/settings.local.json", "[1, 2"),
        (".claude/agents/other.md", "---\nhooks: [unclosed\n---\n"),
    ],
)
def test_an_unreadable_other_hook_location_cannot_rule_out_a_duplicate(project, relative, content):
    other = project / relative
    other.write_text(content)
    assert _record(project)["status"] == "unverified"
    other.unlink()
    assert _record(project)["status"] == "verified", "absent locations are fine"


def test_an_unreadable_user_settings_file_also_blocks_verification(project):
    settings = Path.home() / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{")
    assert _record(project)["status"] == "unverified"
    settings.write_text(json.dumps({"hooks": {}}))
    assert _record(project)["status"] == "verified"


def test_identity_entry_binds_binding_agent_and_state(project):
    registry = _registry(_component(), project)
    first = layer.hook_verification_entry("claude-code", registry, str(project))
    assert first["path"] == "observal:hook-verification"
    (project / ".claude" / "hooks" / "probe-fail.sh").write_bytes(b"changed")
    assert layer.hook_verification_entry("claude-code", registry, str(project))["hash"] != first["hash"]
    assert layer.hook_verification_entry("claude-code", _registry(_component(hook_integrity=""), project), None) is None


def test_presence_index_reads_hook_results(project):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "observal-server"))
    from services.layer_components.normalizer import normalize_snapshot

    record = _record(project)
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
                "components": [
                    {
                        "type": "hook",
                        "id": HOOK,
                        "name": "probe-fail",
                        "version": "1.0.0",
                        "scope": "project",
                        "local_name": "probe-fail",
                    }
                ],
            }
        ],
    }
    (occurrence,) = normalize_snapshot(pins, {"hook_verifications": [record]})
    assert (occurrence.verification_status, occurrence.binding_agent) == ("verified", "probe-agent")
    assert occurrence.location_sha256 == record["location_sha256"]
    (as_skill,) = normalize_snapshot(pins, {"skill_verifications": [record]})
    assert as_skill.verification_status == "unverified", "a skill result never verifies a hook"


@pytest.mark.parametrize(
    "command",
    [
        'echo "note: \\"quoted\\" \\\\ done" && printf \'%s\\n\' it\'s',
        'printf "\U0001f642"',
        "echo a\u2028b",
        "echo a\u2029b",
        "echo a\x85b",
        "printf 'tab\there'\nnext",
    ],
    ids=["quotes-backslashes", "emoji", "line-separator", "paragraph-separator", "next-line", "tab-newline"],
)
def test_a_hook_command_reads_back_exactly_and_keeps_launchers_rewritable(monkeypatch, command):
    """Regression: an unescaped `command: "echo "x""` made the agent file invalid YAML, and
    json.dumps escaped an emoji as a surrogate pair that YAML reads back as two code points.

    Either way Claude Code could not read back the bound command, and agent pull's launcher
    rewrite (which refuses unparseable frontmatter) left the session-push hooks on a bare python3.
    """
    import sys

    import yaml

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "observal-server"))
    from observal_cli import cmd_pull
    from observal_cli.shared import launcher
    from services.harness import ConfigContext
    from services.harness.claude_code import ClaudeCodeAdapter

    hook = {"event": "UserPromptSubmit", "handler_type": "command", "handler_config": {"command": command}}
    ctx = ConfigContext(
        agent=None,
        safe_name="reviewer",
        harness="claude-code",
        observal_url="http://localhost:8000",
        hook_configs=[{**hook, "name": "note"}],
        options={"scope": "project"},
    )
    result = ClaudeCodeAdapter().format_config(ctx)
    profile = result["agent_profile"]["content"]
    hooks = yaml.safe_load(profile.split("\n---", 1)[0][4:])["hooks"]["UserPromptSubmit"]
    commands = [h["command"] for group in hooks for h in group["hooks"]]
    assert command in commands, "the command reads back exactly"
    assert result["hook_bindings"][0]["command"] == command, "and matches the binding"

    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: True)
    rewritten = cmd_pull.rewrite_frontmatter_hook_launchers(profile)
    assert rewritten != profile and "python3 -m observal_cli" not in rewritten.split("\n---", 1)[0]
