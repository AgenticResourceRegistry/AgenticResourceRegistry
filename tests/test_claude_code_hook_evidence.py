# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Claude Code hook evidence, checked against the recorded Claude Code 2.1.286 sessions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.session_parsers.hook_evidence import extract_hook_evidence, hook_binding_sha256, hook_extractor

FIXTURES = Path(__file__).parent / "fixtures" / "component_insights" / "claude_code"
PROMPT = hook_binding_sha256("UserPromptSubmit", ".claude/hooks/probe-prompt.sh")
FAIL = hook_binding_sha256("PostToolUse", ".claude/hooks/probe-fail.sh")
BLOCK = hook_binding_sha256("PreToolUse", ".claude/hooks/probe-block.sh")
PRE = hook_binding_sha256("PreToolUse", ".claude/hooks/probe-pre.sh")


def _lines(name: str) -> list[str]:
    return (FIXTURES / name).read_text(encoding="utf-8").splitlines()


def _extract(lines: list[str]):
    rows = [{"is_source_record": 1, "line_offset": i, "raw_line": line} for i, line in enumerate(lines)]
    extraction = extract_hook_evidence("claude-code", rows)
    assert extraction.status == "supported"
    return extraction


def _facts(extraction) -> list[tuple]:
    return [(e.kind, e.binding_sha256) for e in extraction.evidence]


def test_settings_hooks_record_output_runs_failures_and_blocks():
    extraction = _extract(_lines("hook_session_settings_outcomes.jsonl"))
    assert _facts(extraction) == [("ran_with_output", PROMPT), ("blocked", BLOCK), ("failed", FAIL)]
    keys = [(e.source_line_offset, e.source_block_key) for e in extraction.evidence]
    assert len(set(keys)) == 3 and all(key.startswith("hook-") for _, key in keys)
    blocked = extraction.evidence[1]
    assert blocked.tool_use_id.startswith("toolu_fixture_")
    assert (extraction.session.headless, extraction.session.agents) == (True, frozenset())


def test_a_silent_success_leaves_no_fact():
    """The probe hook ran (marker file) but Claude Code wrote no record of it."""
    extraction = _extract(_lines("hook_session_silent_success.jsonl"))
    assert extraction.evidence == ()
    assert PRE not in {e.binding_sha256 for e in extraction.evidence}
    assert hook_extractor("claude-code").records_silent_success is False


def test_agent_frontmatter_hooks_interactive_and_headless():
    interactive = _extract(_lines("hook_session_agent_interactive.jsonl"))
    assert _facts(interactive) == [("ran_with_output", PROMPT), ("failed", FAIL)]
    assert (interactive.session.headless, interactive.session.agents) == (False, frozenset({"probe-agent"}))
    headless = _extract(_lines("hook_session_agent_headless.jsonl"))
    assert headless.evidence == ()
    assert (headless.session.headless, headless.session.agents) == (True, frozenset({"probe-agent"}))


def test_hook_error_text_without_the_harness_denial_flag_is_not_a_block():
    """A tool can print anything; only Claude Code sets toolDenialKind."""
    forged = []
    for line in _lines("hook_session_settings_outcomes.jsonl"):
        record = json.loads(line)
        record.pop("toolDenialKind", None)
        forged.append(json.dumps(record))
    assert "blocked" not in {kind for kind, _ in _facts(_extract(forged))}


def test_inconsistent_or_malformed_hook_records_are_dropped():
    lines = _lines("hook_session_settings_outcomes.jsonl")
    edited = []
    for line in lines:
        record = json.loads(line)
        attachment = record.get("attachment") or {}
        if attachment.get("type") == "hook_success":
            attachment["exitCode"] = 1  # a "success" that failed is not trusted
        if attachment.get("type") == "hook_non_blocking_error":
            attachment["command"] = ""
        edited.append(json.dumps(record))
    extraction = _extract(edited)
    assert _facts(extraction) == [("blocked", BLOCK)]
    assert extraction.malformed_source_records == 2


def test_facts_carry_no_commands_output_or_paths():
    rendered = repr(_extract(_lines("hook_session_settings_outcomes.jsonl")))
    for secret in (".claude/hooks", "probe-", "PROBE-CONTEXT", "/home/fixture"):
        assert secret not in rendered


def test_other_harnesses_are_unsupported_not_empty():
    assert extract_hook_evidence("pi", []).status == "unsupported"
    assert hook_extractor("pi") is None


def _hook_record(command: str, event: str = "UserPromptSubmit") -> str:
    return json.dumps(
        {
            "type": "attachment",
            "timestamp": "2026-10-01T23:07:40.000Z",
            "attachment": {
                "type": "hook_success",
                "hookEvent": event,
                "hookName": event,
                "command": command,
                "exitCode": 0,
                "stdout": "x",
            },
        }
    )


def test_observal_telemetry_hook_runs_are_not_candidates():
    """Regression: the live run reported Observal's own session-push runs as unmatched hook runs.

    Command forms are the complete launchers agent pull and doctor write: installed (-I),
    source checkout (PYTHONPATH= with -P), agent-attributed, a quoted interpreter, and bare python3.
    """
    from services.component_activity.hook_matcher import match_hook_evidence

    telemetry = [
        "/opt/venv/bin/python3 -I -m observal_cli.hooks.session_push",
        "PYTHONPATH=/src/observal /usr/bin/python3.12 -P -m observal_cli.hooks.session_push --harness claude-code",
        "OBSERVAL_AGENT_ID=a1 '/opt/py 3/bin/python' -I -m observal_cli.hooks.codex_session_push",
        "python3 -m observal_cli.hooks.session_push",
    ]
    user = 'echo "e2e-prompt-note: prompt received"'
    gated = "/opt/venv/bin/python3 -I -m observal_cli.hook_gate --agent reviewer -- make lint"
    lines = [_hook_record(command) for command in telemetry] + [_hook_record(user), _hook_record(gated, "Stop")]
    extraction = _extract(lines)
    assert _facts(extraction) == [
        ("ran_with_output", hook_binding_sha256("UserPromptSubmit", user)),
        ("ran_with_output", hook_binding_sha256("Stop", gated)),
    ], "the user's hook and a gated user hook stay; telemetry runs are dropped"
    assert extraction.malformed_source_records == 0, "dropped, not counted as malformed"
    result = match_hook_evidence(extraction, {}, {})
    assert result.unmatched_count == 2, "only the user hooks are candidates"


@pytest.mark.parametrize(
    "command",
    [
        # Reviewer reproductions: a compound command and a gate wrapping a user's command.
        "echo hi && python3 -m observal_cli.hooks.session_push --harness claude-code",
        "/opt/venv/bin/python3 -I -m observal_cli.hook_gate --command "
        "'python3 -m observal_cli.hooks.session_push --harness claude-code'",
        "python3 -m observal_cli.hooks.session_push; rm -rf build",
        "python3 -m observal_cli.hooks.session_push --harness claude-code && notify-send done",
        "python3 -m observal_cli.hooks.session_push --harness claude-code > /tmp/push.log",
        "python3 -m observal_cli.hooks.session_push --harness $(whoami)",
        "PYTHONPATH=$HOME/x python3 -m observal_cli.hooks.session_push",
        "bash -c 'python3 -m observal_cli.hooks.session_push'",
        "python3 -m observal_cli.hooks.session_push --extra",
        "python3 -m observal_cli.hooks.not_telemetry",
        "python3 -m observal_cli.hooks.session_push --harness claude-code --harness kiro",
        'set "PYTHONPATH=C:\\x" && python -m observal_cli.hooks.session_push',
        "python3 -m observal_cli.hooks.session_push 'unterminated",
        "OBSERVAL_AGENT_ID=x;evil PYTHONPATH=/y python3 -m observal_cli.hooks.session_push",
        "PYTHONPATH=/y python3 -m observal_cli.hooks.session_push --harness claude-code&",
    ],
)
def test_compound_wrapped_or_unusual_commands_stay_candidates(command):
    from services.session_parsers.hook_evidence import is_observal_telemetry_hook

    assert not is_observal_telemetry_hook(command)
    extraction = _extract([_hook_record(command)])
    assert _facts(extraction) == [("ran_with_output", hook_binding_sha256("UserPromptSubmit", command))]


@pytest.mark.parametrize("installed", [True, False])
def test_generated_launchers_with_spaces_in_their_paths_are_telemetry(monkeypatch, installed):
    """Regression: a source checkout under a path with spaces gets a quoted PYTHONPATH, which
    the classifier rejected, so its session pushes came back as unmatched runs."""
    import sys

    from observal_cli import cmd_pull
    from observal_cli.shared import launcher
    from services.session_parsers.hook_evidence import is_observal_telemetry_hook

    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: installed)
    monkeypatch.setattr(launcher, "package_root", lambda: "/tmp/Observal Flare/clone")
    monkeypatch.setattr(sys, "executable", "/opt/py 3/bin/python3")
    # The launchers agent pull writes into agent files and doctor writes for Cursor.
    generated = [
        launcher.posix_module_command("observal_cli.hooks.session_push"),
        cmd_pull.rewrite_launcher_command("python3 -m observal_cli.hooks.session_push"),
    ]
    assert installed or all("PYTHONPATH='/tmp/Observal Flare/clone'" in command for command in generated)
    for command in generated:
        assert is_observal_telemetry_hook(command), command
    assert not is_observal_telemetry_hook(generated[0] + " && echo hi"), "compound still rejected"
