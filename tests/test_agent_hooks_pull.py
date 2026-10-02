# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""agent pull --hooks=settings|frontmatter: dry run, opt-in, restore and refusal paths."""

from __future__ import annotations

import copy
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import typer
from typer.testing import CliRunner

import observal_cli.cmd_pull as cmd_pull
from observal_cli import agent_hooks
from observal_cli.errors import ErrorHandlingGroup
from observal_cli.harness.claude_code import ClaudeCodeAdapter

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the gate is POSIX-only")

RUNNER = CliRunner()
HOOK = "11111111-1111-4111-8111-111111111111"
WEB = "22222222-2222-4222-8222-222222222222"
SCRIPT = "#!/bin/sh\ncat > /dev/null\nexit 1\n"
ORIGINAL = ".claude/hooks/probe-fail.sh"
SESSION_PUSH_FRONTMATTER = """    - hooks:
        - type: command
          command: "python3 -m observal_cli.hooks.session_push"
"""


def _profile(with_hook: bool) -> str:
    hook = (
        f'  PostToolUse:\n    - hooks:\n        - type: command\n          command: "{ORIGINAL}"\n' if with_hook else ""
    )
    return f"---\nname: reviewer\nhooks:\n  Stop:\n{SESSION_PUSH_FRONTMATTER}{hook}---\n\nBody.\n"


def _snippet(placement: str | None) -> dict:
    snippet = {
        "agent_profile": {"path": ".claude/agents/reviewer.md", "content": _profile(placement != "settings")},
        "mcp_config": {},
        "mcp_setup_commands": [],
        "scope": "project",
        "hook_files": [{"path": ORIGINAL, "content": SCRIPT, "executable": True}],
        "hook_bindings": [
            {"name": "probe-fail", "event": "PostToolUse", "command": ORIGINAL, "script": ORIGINAL, "agent": "reviewer"}
        ],
    }
    if placement:
        snippet["hook_placement"] = placement
    return snippet


def _response(placement: str | None) -> dict:
    return {
        "version": "1.4.0",
        "lock": {
            "status": "locked",
            "digest": "sha256:" + "a" * 64,
            "components": [
                {"id": HOOK, "type": "hook", "version": "1.0.0", "qualified_name": "acme/probe-fail"},
                {"id": WEB, "type": "hook", "version": "1.0.0", "qualified_name": "acme/web"},
            ],
        },
        "component_pins": [
            {
                "id": HOOK,
                "type": "hook",
                "name": "probe-fail",
                "version": "1.0.0",
                "local_name": "probe-fail",
                "qualified_name": "acme/probe-fail",
            },
            {
                "id": WEB,
                "type": "hook",
                "name": "web",
                "version": "1.0.0",
                "local_name": "web",
                "qualified_name": "acme/web",
            },
        ],
        "config_snippet": _snippet(placement),
    }


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    import observal_cli.audit as audit
    import observal_cli.layer as layer
    import observal_cli.lockfile as lockfile
    from observal_cli.shared import launcher

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: True)
    adapter = MagicMock(name="adapter")
    adapter.saved_model.return_value = None
    adapter.rewrite_agent_profile.side_effect = lambda content, agent_id: content
    adapter.allow_home_agent_profile.return_value = False
    post = MagicMock(return_value=_response("settings"))
    upsert = MagicMock()
    installed = MagicMock(return_value=None)
    version = MagicMock(return_value="2.1.286")
    startup = MagicMock(return_value=40.0)
    monkeypatch.setattr(cmd_pull, "spinner", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(cmd_pull, "ensure_loaded", MagicMock())
    monkeypatch.setattr(cmd_pull, "get_adapter", MagicMock(return_value=adapter))
    monkeypatch.setattr(cmd_pull, "_installed_agent", installed)
    monkeypatch.setattr(cmd_pull.client, "resolve_registry_reference", MagicMock(return_value="agent-uuid"))
    detail = {"id": "agent-uuid", "name": "reviewer", "namespace": "acme", "slug": "reviewer", "version": "1.4.0"}
    monkeypatch.setattr(cmd_pull.client, "get", MagicMock(return_value=detail | {"component_links": []}))
    monkeypatch.setattr(cmd_pull.client, "post", post)
    monkeypatch.setattr(lockfile, "local_registry_name", MagicMock(return_value="reviewer"))
    monkeypatch.setattr(lockfile, "read_registry_lockfile", MagicMock(return_value=({}, {"harnesses": {}})))
    monkeypatch.setattr(lockfile, "upsert_agent", upsert)
    monkeypatch.setattr(layer, "ensure_local_snapshot", MagicMock())
    monkeypatch.setattr(audit, "emit_cli_audit", MagicMock())
    monkeypatch.setattr(agent_hooks, "claude_code_version", version)
    monkeypatch.setattr(agent_hooks, "measure_gate_startup", startup)
    target = tmp_path / "project"
    target.mkdir()
    root = typer.Typer(cls=ErrorHandlingGroup)
    agent = typer.Typer()
    cmd_pull.register_pull(agent)
    root.add_typer(agent, name="agent")
    return SimpleNamespace(
        app=root, target=target, post=post, upsert=upsert, installed=installed, version=version, startup=startup
    )


def _pull(env: SimpleNamespace, *options: str, harness: str = "claude-code", json_output: bool = False):
    args = ["agent", "pull", "acme/reviewer", "--harness", harness, "--dir", str(env.target), "--no-prompt"]
    if json_output:
        args += ["--output", "json"]
    return RUNNER.invoke(env.app, [*args, *options])


def _shown(result) -> str:
    return " ".join(result.output.split())  # rich wraps long lines


def _error(result) -> dict:
    return json.loads(result.stderr)["error"]


def _settings_path(env) -> Path:
    return env.target / ".claude" / "settings.json"


def _settings(env) -> dict:
    return json.loads(_settings_path(env).read_text())


def _agent_file(env) -> Path:
    return env.target / ".claude" / "agents" / "reviewer.md"


def test_opt_in_places_gated_hooks_records_them_and_verifies(env):
    result = _pull(env, "--hooks=settings")
    assert result.exit_code == 0, result.output
    assert env.post.call_args.args[1]["options"]["hook_placement"] == "settings"
    (event,) = _settings(env)["hooks"]
    group = _settings(env)["hooks"]["PostToolUse"][0]
    assert event == "PostToolUse" and group["_observal"]["agent"] == "reviewer"
    gated = group["hooks"][0]["command"]
    assert f"--agent reviewer --command {ORIGINAL}" in gated
    assert ORIGINAL not in _agent_file(env).read_text(), "the agent file no longer carries the hook"
    kwargs = env.upsert.call_args.kwargs
    assert (kwargs["hook_placement"], kwargs["hook_on_unknown"], kwargs["hook_gate_agent"]) == (
        "settings",
        "skip",
        "reviewer",
    )
    hook = next(component for component in kwargs["components"] if component["id"] == HOOK)
    assert hook["hook_placement"] == "gated_settings"
    assert hook["hook_command"] == gated and hook["hook_original_command"] == ORIGINAL
    assert hook["hook_config"] == "project:.claude/settings.json"
    assert hook["hook_agent_profile"] == "project:.claude/agents/reviewer.md"
    assert hook["hook_gate_python"] == sys.executable
    web = next(component for component in kwargs["components"] if component["id"] == WEB)
    assert not any(key.startswith("hook_") for key in web), "an unbound hook stays unverified"
    assert ClaudeCodeAdapter().verify_hook_binding(str(env.target), hook) == "verified"
    assert "Agent hooks in settings.json" in _shown(result)


def test_dry_run_shows_the_plan_and_writes_nothing(env):
    result = _pull(env, "--hooks=settings", "--dry-run")
    assert result.exit_code == 0, result.output
    assert not (env.target / ".claude").exists()
    env.upsert.assert_not_called()
    shown = _shown(result)
    for visible in ("would add", "PostToolUse: probe-fail", "40 ms", "Timeouts are unchanged", "no longer lists"):
        assert visible in shown, visible
    data = json.loads(_pull(env, "--hooks=settings", "--dry-run", json_output=True).stdout)
    hooks = data["agent_hooks"]
    assert hooks["placement"] == "settings" and hooks["gate_startup_ms"] == 40.0
    assert [item["name"] for item in hooks["added"]] == ["probe-fail"] and hooks["changed"] is True
    assert {"path": str(_settings_path(env)), "status": "would write"} in data["files"]
    assert not (env.target / ".claude").exists()


def test_a_default_pull_writes_no_settings_and_reports_nothing(env):
    env.post.return_value = _response(None)
    result = _pull(env, json_output=True)
    assert result.exit_code == 0, result.stderr
    assert "agent_hooks" not in json.loads(result.stdout)
    assert not _settings_path(env).exists()
    assert "hook_placement" not in env.upsert.call_args.kwargs
    assert "hook_placement" not in env.post.call_args.args[1]["options"]


def test_the_choice_is_remembered_and_restore_removes_only_owned_groups(env):
    assert _pull(env, "--hooks=settings").exit_code == 0
    user_group = {"hooks": [{"type": "command", "command": "make lint"}]}
    data = _settings(env)
    data["hooks"]["PostToolUse"].append(user_group)
    _settings_path(env).write_text(json.dumps(data))
    env.installed.return_value = {"hook_placement": "settings", "hook_on_unknown": "run", "hook_gate_agent": "reviewer"}
    assert _pull(env).exit_code == 0, "a plain re-pull keeps the opt-in"
    assert env.upsert.call_args.kwargs["hook_on_unknown"] == "run", "and the remembered policy"
    owned = [group for group in _settings(env)["hooks"]["PostToolUse"] if "_observal" in group]
    assert len(owned) == 1 and owned[0]["hooks"][0]["command"].endswith("--on-unknown run")
    assert user_group in _settings(env)["hooks"]["PostToolUse"]

    env.post.return_value = _response(None)
    result = _pull(env, "--hooks=frontmatter")
    assert result.exit_code == 0, result.output
    assert _settings(env)["hooks"] == {"PostToolUse": [user_group]}
    assert ORIGINAL in _agent_file(env).read_text()
    assert "hook_placement" not in env.upsert.call_args.kwargs
    assert "hook_placement" not in env.post.call_args.args[1]["options"]
    assert "restored to the agent file" in _shown(result)


def test_a_locally_edited_owned_group_refuses_the_pull_until_forced(env):
    assert _pull(env, "--hooks=settings").exit_code == 0
    edited = _settings(env)
    edited["hooks"]["PostToolUse"][0]["hooks"][0]["timeout"] = 5
    _settings_path(env).write_text(json.dumps(edited))
    _agent_file(env).unlink()
    result = _pull(env, "--hooks=settings", json_output=True)
    assert result.exit_code != 0
    assert "edited locally" in _error(result)["message"]
    assert _settings(env) == edited, "the edited group is kept"
    assert not _agent_file(env).exists(), "nothing was written"
    preview = _pull(env, "--hooks=settings", "--dry-run")
    assert preview.exit_code == 0 and "edited locally" in _shown(preview), "a dry run reports the conflict"
    forced = _pull(env, "--hooks=settings", "--force-hooks")
    assert forced.exit_code == 0, forced.output
    assert "timeout" not in _settings(env)["hooks"]["PostToolUse"][0]["hooks"][0]


def test_an_invalid_settings_file_refuses_the_opt_in_but_not_a_default_pull(env):
    _settings_path(env).parent.mkdir(parents=True)
    _settings_path(env).write_text("{not json")
    result = _pull(env, "--hooks=settings", json_output=True)
    assert result.exit_code != 0 and "not valid JSON" in _error(result)["message"]
    assert not _agent_file(env).exists()
    env.post.return_value = _response(None)
    assert _pull(env).exit_code == 0
    assert _settings_path(env).read_text() == "{not json"


def test_a_server_that_ignores_the_request_is_refused_before_writing(env):
    env.post.return_value = _response(None)
    result = _pull(env, "--hooks=settings", json_output=True)
    assert result.exit_code != 0 and "does not support placing agent hooks" in _error(result)["message"]
    assert not (env.target / ".claude").exists()


def test_untested_claude_code_refuses_a_new_opt_in_and_warns_on_a_remembered_one(env):
    env.version.return_value = "2.1.300"
    result = _pull(env, "--hooks=settings", json_output=True)
    assert result.exit_code != 0 and "tested Claude Code versions (2.1.286)" in _error(result)["message"]
    env.post.assert_not_called()
    env.installed.return_value = {"hook_placement": "settings", "hook_gate_agent": "reviewer"}
    remembered = _pull(env, json_output=True)
    assert remembered.exit_code == 0, remembered.stderr
    assert any("tested only with Claude Code 2.1.286" in w for w in json.loads(remembered.stdout)["warnings"])


def test_flags_that_do_not_apply_are_rejected(env, monkeypatch):
    for options, harness, message in (
        (("--hooks=settings",), "cursor", "does not support agent hook placement"),
        (("--hooks=elsewhere",), "claude-code", "Unknown hook placement"),
        (("--hooks=settings", "--on-unknown=maybe"), "claude-code", "Unknown --on-unknown"),
        (("--hooks=frontmatter", "--on-unknown=run"), "claude-code", "applies only"),
        (("--on-unknown=run",), "claude-code", "applies only"),
    ):
        result = _pull(env, *options, harness=harness, json_output=True)
        assert result.exit_code != 0 and message in _error(result)["message"], options
    monkeypatch.setattr(cmd_pull.sys, "platform", "win32")
    result = _pull(env, "--hooks=settings", json_output=True)
    assert result.exit_code != 0 and "not supported on Windows" in _error(result)["message"]
    env.post.assert_not_called()


def test_a_shared_settings_file_keeps_each_agents_groups_apart(env):
    other = agent_hooks.build_agent_hooks(
        "other",
        [{"name": "lint", "event": "PostToolUse", "command": "make lint"}],
        [{"type": "hook", "id": WEB, "local_name": "lint"}],
        "skip",
    )
    other_group = other[0].group("other")
    _settings_path(env).parent.mkdir(parents=True)
    _settings_path(env).write_text(json.dumps({"hooks": {"PostToolUse": [copy.deepcopy(other_group)]}}))
    assert _pull(env, "--hooks=settings").exit_code == 0
    env.post.return_value = _response(None)
    assert _pull(env, "--hooks=frontmatter").exit_code == 0
    assert _settings(env)["hooks"]["PostToolUse"] == [other_group]


def test_an_opt_in_found_in_settings_is_kept_without_a_lockfile_record(env):
    """A teammate's committed settings, or a lockfile write that failed after settings.json was written."""
    assert _pull(env, "--hooks=settings", "--on-unknown=run").exit_code == 0
    env.installed.return_value = None  # no local record of the choice
    result = _pull(env, json_output=True)
    assert result.exit_code == 0, result.stderr
    assert env.post.call_args.args[1]["options"]["hook_placement"] == "settings"
    assert env.upsert.call_args.kwargs["hook_on_unknown"] == "run", "the policy is read back from the gated command"
    assert any("Found this agent's gated hooks" in w for w in json.loads(result.stdout)["warnings"])
    owned = [group for group in _settings(env)["hooks"]["PostToolUse"] if "_observal" in group]
    assert len(owned) == 1 and owned[0]["hooks"][0]["command"].endswith("--on-unknown run")
    other_agent = {"hooks": {"Stop": [{**owned[0], "_observal": {**owned[0]["_observal"], "agent": "someone"}}]}}
    _settings_path(env).write_text(json.dumps(other_agent))
    env.post.return_value = _response(None)
    assert _pull(env).exit_code == 0
    assert "hook_placement" not in env.post.call_args.args[1]["options"], "another agent's groups are not a choice"


def test_the_lockfile_remembers_only_a_settings_placement(monkeypatch, tmp_path):
    import observal_cli.lockfile as lockfile

    data = {"registries": {}}
    registry = {"harnesses": {}}
    monkeypatch.setattr(lockfile, "_LOCKFILE_LOCK", tmp_path / "lockfile.lock")
    monkeypatch.setattr(lockfile, "read_registry_lockfile", lambda **_kwargs: (data, registry))
    monkeypatch.setattr(lockfile, "write_lockfile", lambda _data: None)
    monkeypatch.setattr(lockfile, "_record_capability_use", lambda **_kwargs: None)

    def entry(**placement) -> dict:
        lockfile.upsert_agent(
            "claude-code", name="reviewer", agent_id="agent-uuid", version="1.0.0", scope="user", **placement
        )
        (agent,) = registry["harnesses"]["claude-code"]["agents"]
        return agent

    gated = entry(hook_placement="settings", hook_on_unknown="run", hook_gate_agent="reviewer")
    assert (gated["hook_placement"], gated["hook_on_unknown"], gated["hook_gate_agent"]) == (
        "settings",
        "run",
        "reviewer",
    )
    restored = entry()
    assert not any(key.startswith("hook_") for key in restored), "a restore forgets the choice"
    ignored = entry(hook_placement="frontmatter", hook_on_unknown="run", hook_gate_agent="reviewer")
    assert not any(key.startswith("hook_") for key in ignored), "frontmatter is the default, never recorded"


def test_doctor_warns_after_an_untested_upgrade_and_when_the_gate_interpreter_moved(monkeypatch, tmp_path):
    import observal_cli.lockfile as lockfile
    from observal_cli.cmd_doctor import _check_claude_code_agent_hooks

    component = {
        "type": "hook",
        "id": HOOK,
        "hook_placement": "gated_settings",
        "hook_gate_python": sys.executable,
    }
    registry = {
        "harnesses": {
            "claude-code": {
                "agents": [{"name": "reviewer", "hook_placement": "settings", "components": [component]}],
                "standalone": [],
            }
        }
    }
    monkeypatch.setattr(lockfile, "read_registry_lockfile", lambda **_kwargs: ({}, registry))
    monkeypatch.setattr(agent_hooks, "claude_code_version", lambda: "2.1.286")

    def warnings() -> list[str]:
        issues: list[str] = []
        found: list[str] = []
        _check_claude_code_agent_hooks(issues, found)
        assert issues == [], "only warnings: the hooks still work as installed"
        return found

    assert warnings() == []
    monkeypatch.setattr(agent_hooks, "claude_code_version", lambda: "2.2.0")
    (untested,) = warnings()
    assert "tested only with Claude Code 2.1.286; found Claude Code 2.2.0" in untested
    monkeypatch.setattr(agent_hooks, "claude_code_version", lambda: "2.1.286")
    component["hook_gate_python"] = str(tmp_path / "gone" / "python3")
    (moved,) = warnings()
    assert "no longer exists (agents: reviewer)" in moved
    registry["harnesses"]["claude-code"]["agents"][0]["hook_placement"] = None
    assert warnings() == [], "agents without gated hooks are not checked"
