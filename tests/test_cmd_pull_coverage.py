# SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Focused boundary and behavior coverage for the agent pull command."""

from __future__ import annotations

import json
import shlex
import stat
import subprocess
import sys
import tomllib
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest
import typer
import yaml
from typer.testing import CliRunner

import observal_cli.cmd_pull as cmd_pull
from observal_cli import project_lock
from observal_cli.errors import ErrorHandlingGroup
from observal_cli.lockfile import upsert_agent as real_upsert_agent

RUNNER = CliRunner()


def _agent_detail(**overrides) -> dict:
    detail = {
        "id": "agent-uuid",
        "name": "reviewer",
        "namespace": "acme",
        "slug": "reviewer",
        "version": "1.4.0",
        "mcp_links": [],
        "component_links": [],
    }
    detail.update(overrides)
    return detail


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


@pytest.fixture
def pull_app() -> typer.Typer:
    root = typer.Typer()
    agent = typer.Typer()
    cmd_pull.register_pull(agent)
    root.add_typer(agent, name="agent")
    return root


@pytest.fixture
def pull_app_boundary() -> typer.Typer:
    root = typer.Typer(cls=ErrorHandlingGroup)
    agent = typer.Typer()
    cmd_pull.register_pull(agent)
    root.add_typer(agent, name="agent")
    return root


@pytest.fixture
def boundaries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    import observal_cli.audit as audit
    import observal_cli.cmd_skill as cmd_skill
    import observal_cli.layer as layer
    import observal_cli.lockfile as lockfile
    import observal_cli.model_catalog as model_catalog
    from observal_cli.shared import launcher

    # Launcher resolution spawns an isolated interpreter; pin it to an installed CLI
    # so it never runs inside a test's mocked subprocess boundary.
    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: True)
    adapter = MagicMock(name="adapter")
    adapter.saved_model.return_value = None
    adapter.rewrite_hooks.side_effect = lambda content, agent_id: content
    adapter.rewrite_agent_profile.side_effect = lambda content, agent_id: content
    adapter.allow_home_agent_profile.return_value = False
    adapter.prepare_mcp_setup_command.side_effect = lambda command, scope: (
        [*command[:3], "--scope", scope, *command[3:]] if command[:3] == ["claude", "mcp", "add"] else command
    )
    adapter.reports_sessions_outside_pull.return_value = False

    def apply_install_options(options: dict, tools: str | None) -> None:
        if tools:
            options["tools"] = tools

    adapter.apply_install_options.side_effect = apply_install_options

    resolve = MagicMock(return_value="agent-uuid")
    get = MagicMock(return_value=_agent_detail())
    post = MagicMock(return_value={"config_snippet": {"agent_profile": {"path": "agent.md", "content": "agent\n"}}})
    ensure_loaded = MagicMock()
    get_adapter = MagicMock(return_value=adapter)
    local_name = MagicMock(return_value="local-reviewer")
    read_registry = MagicMock(return_value=({}, {"harnesses": {}}))
    upsert = MagicMock()
    snapshot = MagicMock()
    emit = MagicMock()
    invalidate = MagicMock()
    git_install = MagicMock()
    direct_install = MagicMock()

    def successful_skill_install(**kwargs):
        destination = kwargs.get("dest") or tmp_path / "fallback-skill"
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "SKILL.md").write_text(kwargs.get("skill_md_content") or "cloned\n")
        return destination

    git_install.side_effect = successful_skill_install
    direct_install.side_effect = successful_skill_install

    monkeypatch.setattr(cmd_pull, "spinner", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(cmd_pull, "ensure_loaded", ensure_loaded)
    monkeypatch.setattr(cmd_pull, "get_adapter", get_adapter)
    monkeypatch.setattr(cmd_pull.client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(cmd_pull.client, "get", get)
    monkeypatch.setattr(cmd_pull.client, "post", post)
    monkeypatch.setattr(lockfile, "local_registry_name", local_name)
    monkeypatch.setattr(lockfile, "read_registry_lockfile", read_registry)
    monkeypatch.setattr(lockfile, "upsert_agent", upsert)
    monkeypatch.setattr(layer, "ensure_local_snapshot", snapshot)
    monkeypatch.setattr(audit, "emit_cli_audit", emit)
    monkeypatch.setattr(model_catalog, "invalidate_cache", invalidate)
    monkeypatch.setattr(cmd_skill, "install_skill_from_git", git_install)
    monkeypatch.setattr(cmd_skill, "install_skill_registry_direct", direct_install)

    return SimpleNamespace(
        adapter=adapter,
        resolve=resolve,
        get=get,
        post=post,
        ensure_loaded=ensure_loaded,
        get_adapter=get_adapter,
        local_name=local_name,
        read_registry=read_registry,
        upsert=upsert,
        snapshot=snapshot,
        emit=emit,
        invalidate=invalidate,
        git_install=git_install,
        direct_install=direct_install,
    )


def _invoke(
    app: typer.Typer,
    target: Path,
    *options: str,
    reference: str = "acme/reviewer",
    harness: str = "claude-code",
    no_prompt: bool = True,
):
    args = ["agent", "pull", reference, "--harness", harness, "--dir", str(target)]
    if no_prompt:
        args.append("--no-prompt")
    args.extend(options)
    return RUNNER.invoke(app, args)


def test_component_conflicts_report_only_other_agent_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    import observal_cli.lockfile as lockfile

    registry = {
        "harnesses": {
            "cursor": {
                "agents": [
                    {
                        "name": "incoming",
                        "components": [{"name": "shared", "version": "0.1.0"}],
                    },
                    {
                        "name": "older-agent",
                        "components": [
                            {"name": "shared", "version": "1.0.0"},
                            {"name": "unversioned", "version": None},
                        ],
                    },
                ]
            }
        }
    }
    read_registry = MagicMock(return_value=({}, registry))
    monkeypatch.setattr(lockfile, "read_registry_lockfile", read_registry)

    conflicts = cmd_pull._component_conflicts(
        "cursor",
        "incoming",
        [
            {"type": "mcp", "name": "shared", "version": "2.0.0"},
            {"name": "unversioned", "version": None},
        ],
    )

    assert conflicts == ["mcp shared: v2.0.0 (this agent) vs v1.0.0 (from older-agent)"]

    read_registry.side_effect = OSError("broken lockfile")
    with pytest.raises(typer.Exit) as error:
        cmd_pull._component_conflicts("cursor", "incoming", [])
    assert error.value.exit_code == 9


def test_pin_hook_interpreter_is_idempotent_and_json_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmd_pull.sys, "executable", r"C:\Users\ada\observal\python.exe")
    source = json.dumps({"command": "python3 -m observal_cli.hooks.session_push --harness cursor"})

    once = cmd_pull._pin_hook_interpreter(source)

    # Backslashes in the interpreter must neither break re.sub nor the JSON string.
    assert (
        json.loads(once)["command"]
        == "C:/Users/ada/observal/python.exe -m observal_cli.hooks.session_push --harness cursor"
    )
    assert cmd_pull._pin_hook_interpreter(once) == once
    assert (
        cmd_pull._pin_hook_interpreter("/opt/venv/bin/python3 -m observal_cli.x")
        == "/opt/venv/bin/python3 -m observal_cli.x"
    )
    # The same holds for a Windows path, in raw text and inside a JSON string.
    windows = r"C:\Python312\python3 -m observal_cli.x"
    assert cmd_pull._pin_hook_interpreter(windows) == windows
    assert cmd_pull._pin_hook_interpreter(json.dumps(windows)) == json.dumps(windows)


def test_pin_hook_interpreter_quotes_paths_with_spaces(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmd_pull.sys, "executable", "/tmp/Observal Tools/bin/python3")
    source = json.dumps({"command": "python3 -m observal_cli.hooks.session_push --harness cursor"})
    result = cmd_pull._pin_hook_interpreter(source)
    command = json.loads(result)["command"]
    assert command == "'/tmp/Observal Tools/bin/python3' -m observal_cli.hooks.session_push --harness cursor"
    assert cmd_pull._pin_hook_interpreter(result) == result


def test_pin_hook_interpreter_windows_path_with_spaces(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmd_pull.sys, "platform", "win32")
    monkeypatch.setattr(cmd_pull.sys, "executable", r"C:\Program Files\Observal\python.exe")
    source = json.dumps({"command": "python3 -m observal_cli.hooks.session_push"})
    command = json.loads(cmd_pull._pin_hook_interpreter(source))["command"]
    assert command == '"C:/Program Files/Observal/python.exe" -m observal_cli.hooks.session_push'
    profile = 'command: "python3 -m observal_cli.hooks.session_push"'
    rewritten = cmd_pull._pin_hook_interpreter(profile)
    assert rewritten == 'command: "\\"C:/Program Files/Observal/python.exe\\" -m observal_cli.hooks.session_push"'
    assert yaml.safe_load(rewritten)["command"] == command


def test_pin_hook_interpreter_keeps_shell_and_frontmatter_valid_with_apostrophe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = "/tmp/Your App's/bin/python3"
    monkeypatch.setattr(cmd_pull.sys, "executable", path)
    profile = 'hooks:\n  Stop:\n    - hooks:\n        - command: "python3 -m observal_cli.hooks.session_push"\n'
    rendered = cmd_pull._pin_hook_interpreter(profile)
    command = yaml.safe_load(rendered)["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert shlex.split(command) == [path, "-m", "observal_cli.hooks.session_push"]
    assert cmd_pull._pin_hook_interpreter(rendered) == rendered


@pytest.mark.parametrize("path", ["/tmp/Observal Tools/bin/python3", "/tmp/Your App's/bin/python3"])
@pytest.mark.parametrize("style", ["single", "bare"])
def test_pin_hook_interpreter_preserves_yaml_commands(monkeypatch: pytest.MonkeyPatch, path: str, style: str) -> None:
    monkeypatch.setattr(cmd_pull.sys, "executable", path)
    original = "python3 -m observal_cli.hooks.session_push"
    if style == "single":
        original = f"'{original}'"
    profile = (
        "---\nname: test\nhooks:\n  Stop:\n    - hooks:\n"
        f"        - command: {original}\n"
        "---\nRun python3 -m observal_cli.hooks.session_push to test.\n"
    )

    rewritten = cmd_pull._pin_agent_profile_hooks(profile)
    frontmatter, body = rewritten.split("\n---\n", 1)
    command = yaml.safe_load(frontmatter[4:])["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert shlex.split(command) == [path, "-m", "observal_cli.hooks.session_push"]
    assert body == "Run python3 -m observal_cli.hooks.session_push to test.\n"
    assert cmd_pull._pin_agent_profile_hooks(rewritten) == rewritten


@pytest.mark.parametrize("indicator", ["|", "|-", ">-"])
def test_pin_hook_interpreter_rewrites_yaml_block_command(monkeypatch: pytest.MonkeyPatch, indicator: str) -> None:
    path = "/tmp/Your App's/bin/python3"
    monkeypatch.setattr(cmd_pull.sys, "executable", path)
    profile = (
        "---\nname: test\nhooks:\n  Stop:\n    - hooks:\n"
        f"        - command: {indicator}\n"
        "            python3 -m observal_cli.hooks.session_push --harness claude-code\n"
        "          timeoutSec: 5\n"
        "description: Run python3 -m observal_cli.hooks.session_push manually\n"
        "---\nRun python3 -m observal_cli.hooks.session_push to test.\n"
    )

    rewritten = cmd_pull._pin_agent_profile_hooks(profile)
    frontmatter, body = rewritten.split("\n---\n", 1)
    data = yaml.safe_load(frontmatter[4:])
    command = data["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert shlex.split(command) == [path, "-m", "observal_cli.hooks.session_push", "--harness", "claude-code"]
    assert data["description"] == "Run python3 -m observal_cli.hooks.session_push manually"
    assert body == "Run python3 -m observal_cli.hooks.session_push to test.\n"
    assert cmd_pull._pin_agent_profile_hooks(rewritten) == rewritten


def test_pin_hook_interpreter_preserves_block_script_comments_and_siblings(monkeypatch: pytest.MonkeyPatch) -> None:
    path = "/tmp/Your App's/bin/python3"
    monkeypatch.setattr(cmd_pull.sys, "executable", path)
    profile = (
        "---\nhooks:\n  Stop:\n    - hooks:\n"
        "        - command: |2- # shell script\n"
        "            # To debug: python3 -m observal_cli.hooks.session_push\n"
        "            OBSERVAL_AGENT_ID=abc exec python3 -m observal_cli.hooks.session_push\n"
        "            echo python3 -m observal_cli.hooks.session_push\n"
        "          timeoutSec: 5\n"
        "        - command: 'python3 -m observal_cli.hooks.kiro_hook'\n"
        "---\nPlain prose python3 -m observal_cli.hooks.session_push\n"
    )

    rewritten = cmd_pull._pin_agent_profile_hooks(profile)
    frontmatter, body = rewritten.split("\n---\n", 1)
    hooks = yaml.safe_load(frontmatter[4:])["hooks"]["Stop"][0]["hooks"]
    script = hooks[0]["command"].splitlines()
    assert script[0] == "# To debug: python3 -m observal_cli.hooks.session_push"
    assert shlex.split(script[1]) == ["OBSERVAL_AGENT_ID=abc", "exec", path, "-m", "observal_cli.hooks.session_push"]
    assert script[2] == "echo python3 -m observal_cli.hooks.session_push"
    assert hooks[0]["timeoutSec"] == 5
    assert shlex.split(hooks[1]["command"]) == [path, "-m", "observal_cli.hooks.kiro_hook"]
    assert body == "Plain prose python3 -m observal_cli.hooks.session_push\n"
    assert cmd_pull._pin_agent_profile_hooks(rewritten) == rewritten


def test_write_profile_pins_block_hook_and_preserves_body(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = "/tmp/Your App's/bin/python3"
    monkeypatch.setattr(cmd_pull.sys, "executable", path)
    profile = (
        "---\nhooks:\n  Stop:\n    - command: |-\n"
        "        python3 -m observal_cli.hooks.session_push\n"
        "---\nTo debug, run python3 -m observal_cli.hooks.session_push\n"
    )
    adapter = MagicMock()
    adapter.allow_home_agent_profile.return_value = False
    adapter.reports_sessions_outside_pull.return_value = False

    written, failed = cmd_pull.write_install_snippet(
        {"agent_profile": {"path": "agent.md", "content": profile}},
        harness="claude-code",
        adapter=adapter,
        target_dir=tmp_path,
        agent_id="agent-uuid",
        is_user_scope=False,
    )

    assert failed == []
    assert written == [(str(tmp_path / "agent.md"), "created")]
    saved = (tmp_path / "agent.md").read_text()
    frontmatter, body = saved[4:].split("\n---\n", 1)
    command = yaml.safe_load(frontmatter)["hooks"]["Stop"][0]["command"]
    assert shlex.split(command) == [path, "-m", "observal_cli.hooks.session_push"]
    assert body == "To debug, run python3 -m observal_cli.hooks.session_push\n"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell invocation")
def test_single_quoted_yaml_hook_launches_interpreter_with_special_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    interpreter = tmp_path / "Your App's $Tools" / "python3"
    interpreter.parent.mkdir()
    interpreter.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    interpreter.chmod(0o755)
    monkeypatch.setattr(cmd_pull.sys, "executable", str(interpreter))
    profile = "---\nhooks:\n  Stop:\n    - command: 'python3 -m observal_cli.hooks.session_push'\n---\n"

    rendered = cmd_pull._pin_agent_profile_hooks(profile)
    command = yaml.safe_load(rendered[4:].split("\n---", 1)[0])["hooks"]["Stop"][0]["command"]
    result = subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == ["-m", "observal_cli.hooks.session_push"]


def test_profile_rewrite_only_touches_frontmatter_hook_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmd_pull.sys, "executable", "/tmp/Your App's/bin/python3")
    mention = "For troubleshooting, run python3 -m observal_cli.hooks.session_push"
    toml_profile = "developer_instructions = " + json.dumps(mention) + "\n"
    assert cmd_pull._pin_agent_profile_hooks(toml_profile) == toml_profile
    assert tomllib.loads(cmd_pull._pin_agent_profile_hooks(toml_profile))["developer_instructions"] == mention

    markdown = (
        "---\n"
        "name: reviewer\n"
        "hooks:\n"
        "  Stop:\n"
        "    - hooks:\n"
        '        - command: "python3 -m observal_cli.hooks.session_push"\n'
        "---\n"
        "To debug, run python3 -m observal_cli.hooks.session_push\n"
    )
    rewritten = cmd_pull._pin_agent_profile_hooks(markdown)
    frontmatter, body = rewritten.split("\n---\n", 1)
    command = yaml.safe_load(frontmatter[4:])["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert shlex.split(command) == ["/tmp/Your App's/bin/python3", "-m", "observal_cli.hooks.session_push"]
    assert body == "To debug, run python3 -m observal_cli.hooks.session_push\n"
    assert cmd_pull._pin_agent_profile_hooks(rewritten) == rewritten


def test_write_codex_profile_preserves_quoted_instructions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cmd_pull.sys, "executable", "/tmp/Your App's/bin/python3")
    instruction = "For troubleshooting, run python3 -m observal_cli.hooks.session_push"
    content = "developer_instructions = " + json.dumps(instruction) + "\n"
    adapter = MagicMock()
    adapter.allow_home_agent_profile.return_value = False
    adapter.reports_sessions_outside_pull.return_value = False
    cmd_pull.write_install_snippet(
        {"agent_profile": {"path": "agent.toml", "content": content}},
        harness="codex",
        adapter=adapter,
        target_dir=tmp_path,
        agent_id="agent-uuid",
        is_user_scope=False,
    )
    assert tomllib.loads((tmp_path / "agent.toml").read_text())["developer_instructions"] == instruction


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        (
            {
                "agent_profile": {
                    "content": '---\nhooks:\n  Stop:\n    - hooks:\n        - command: "python3 -m observal_cli.hooks.session_push"\n---\n'
                }
            },
            True,
        ),
        ({"hooks_config": {"content": {"hooks": {"stop": [{"command": "x -m observal_cli.hooks.kiro_hook"}]}}}}, True),
        (
            {
                "agent_profile": {
                    "content": {"hooks": {"stop": [{"command": "python3 -m observal_cli.hooks.session_push"}]}}
                }
            },
            True,
        ),
        ({"agent_profile": {"content": "---\nname: plain\n---\n"}, "mcp_config": {"a": {"command": "npx"}}}, False),
        # These mention the module but install no executable session hook.
        ({"agent_profile": {"content": "Explains how python3 -m observal_cli.hooks.session_push runs."}}, False),
        (
            {"agent_profile": {"content": "---\nname: plain\n---\nRun python3 -m observal_cli.hooks.session_push"}},
            False,
        ),
        (
            {"agent_profile": {"content": "---\ndescription: 'Run python3 -m observal_cli.hooks.session_push'\n---\n"}},
            False,
        ),
        (
            {
                "hooks_config": {
                    "content": {
                        "hooks": {
                            "stop": [
                                {"description": "python3 -m observal_cli.hooks.session_push", "command": "echo ok"}
                            ]
                        }
                    }
                }
            },
            False,
        ),
        ({"agent_profile": {"content": "---\nhooks: &hooks\n  Stop: [*hooks]\n---\n"}}, False),
    ],
)
def test_reports_sessions_detects_telemetry_hooks_anywhere(snippet: dict, expected: bool) -> None:
    assert cmd_pull._reports_sessions(snippet) is expected


@pytest.mark.parametrize("dry_run", [False, True])
def test_reports_sessions_uses_effective_merged_hooks(tmp_path: Path, dry_run: bool) -> None:
    path = tmp_path / "hooks.json"
    path.write_text(json.dumps({"hooks": {"retained": [{"command": "python3 -m observal_cli.hooks.session_push"}]}}))
    snippet = {
        "hooks_config": {
            "path": "hooks.json",
            "content": {"hooks": {"new": [{"command": "echo ok"}]}},
            "merge": True,
        }
    }
    if not dry_run:
        cmd_pull._write_file(path, snippet["hooks_config"]["content"], merge_mcp=True)
    assert cmd_pull._reports_sessions(snippet, target_dir=tmp_path, dry_run=dry_run)

    # Replacing the same event with a non-telemetry command removes that hook.
    snippet["hooks_config"]["content"]["hooks"] = {"retained": [{"command": "echo ok"}]}
    if not dry_run:
        cmd_pull._write_file(path, snippet["hooks_config"]["content"], merge_mcp=True)
    assert not cmd_pull._reports_sessions(snippet, target_dir=tmp_path, dry_run=dry_run)


@pytest.mark.parametrize("dry_run", [False, True])
def test_pull_json_reports_retained_telemetry_hook(
    pull_app: typer.Typer, boundaries: SimpleNamespace, tmp_path: Path, dry_run: bool
) -> None:
    target = tmp_path / "project"
    target.mkdir()
    path = target / "hooks.json"
    original = {"hooks": {"retained": [{"command": "python3 -m observal_cli.hooks.session_push"}]}}
    path.write_text(json.dumps(original))
    boundaries.post.return_value = {
        "config_snippet": {
            "agent_profile": {"path": "agent.md", "content": "No hooks in this profile"},
            "hooks_config": {
                "path": "hooks.json",
                "content": {"hooks": {"new": [{"command": "echo hello"}]}},
                "merge": True,
            },
        }
    }
    result = _invoke(pull_app, target, "--output", "json", *(["--dry-run"] if dry_run else []))
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["reports_sessions"] is True
    if dry_run:
        assert json.loads(path.read_text()) == original
    else:
        assert "new" in json.loads(path.read_text())["hooks"]


def test_reports_written_sessions_recognizes_yaml_hooks_but_not_prose(tmp_path: Path) -> None:
    hooks = tmp_path / "hooks.yaml"
    hooks.write_text("hooks:\n  stop:\n    - command: python3 -m observal_cli.hooks.session_push\n")
    prose = tmp_path / "notes.md"
    prose.write_text("Use python3 -m observal_cli.hooks.session_push to install hooks.\n")
    assert cmd_pull._reports_written_sessions([str(prose), str(hooks)])
    assert not cmd_pull._reports_written_sessions([str(prose)])


def test_dry_run_reports_string_hook_replacement_not_a_merge(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    path.write_text(json.dumps({"hooks": {"stop": [{"command": "python3 -m observal_cli.hooks.session_push"}]}}))
    replacement = json.dumps({"hooks": {"onStart": [{"command": "echo ok"}]}})
    snippet = {"hooks_config": {"path": "hooks.json", "content": replacement, "merge": True}}

    assert not cmd_pull._reports_sessions(snippet, target_dir=tmp_path, dry_run=True)
    cmd_pull._write_file(path, replacement, merge_mcp=True)
    assert not cmd_pull._reports_sessions(snippet, target_dir=tmp_path)


def test_pull_dry_run_string_hook_replacement_matches_real_pull(
    pull_app: typer.Typer, boundaries: SimpleNamespace, tmp_path: Path
) -> None:
    target = tmp_path / "project"
    target.mkdir()
    path = target / "hooks.json"
    original = {"hooks": {"retained": [{"command": "python3 -m observal_cli.hooks.session_push"}]}}
    path.write_text(json.dumps(original))
    boundaries.post.return_value = {
        "config_snippet": {
            "hooks_config": {
                "path": "hooks.json",
                "content": json.dumps({"hooks": {"new": [{"command": "echo hello"}]}}),
                "merge": True,
            }
        }
    }

    preview = _invoke(pull_app, target, "--output", "json", "--dry-run")
    assert preview.exit_code == 0, preview.output
    assert json.loads(preview.stdout)["reports_sessions"] is False
    assert json.loads(path.read_text()) == original

    installed = _invoke(pull_app, target, "--output", "json")
    assert installed.exit_code == 0, installed.output
    assert json.loads(installed.stdout)["reports_sessions"] is False
    assert json.loads(path.read_text())["hooks"] == {"new": [{"command": "echo hello"}]}


def test_resolve_hook_paths_uses_path_fallback_only_in_quoted_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    monkeypatch.setattr(Path, "is_file", lambda _path: False)
    which = MagicMock(side_effect=["/opt/observal/observal-hook.sh", None])
    monkeypatch.setattr(shutil, "which", which)
    source = (
        'command = "observal-hook.sh --agent-name reviewer"\n'
        "observal-hook.sh appears in prose\n"
        'stop = "observal-stop-hook.sh"\n'
    )

    rendered = cmd_pull._resolve_hook_paths(source)

    assert rendered == (
        'command = "/opt/observal/observal-hook.sh"\n'
        "observal-hook.sh appears in prose\n"
        'stop = "observal-stop-hook.sh"\n'
    )
    assert which.call_args_list == [call("observal-hook.sh"), call("observal-stop-hook.sh")]


def test_collect_mcp_env_vars_prompts_and_deduplicates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    detail = {
        "mcp_links": [{"mcp_listing_id": "mcp-1", "mcp_name": "Primary"}],
        "component_links": [
            {"component_type": "mcp", "component_id": "mcp-1", "component_name": "duplicate"},
            {"component_type": "mcp", "component_id": "mcp-2", "component_name": ""},
            {"component_type": "skill", "component_id": "skill-1"},
        ],
    }

    def get(path: str):
        if path.endswith("mcp-1"):
            return {
                "environment_variables": [
                    {"name": "TOKEN", "description": "required token", "required": True},
                    {"name": "EMPTY", "description": "optional", "required": False},
                    {"name": "FILLED", "required": False},
                ]
            }
        if path.endswith("mcp-2"):
            return {
                "name": "Secondary",
                "environment_variables": [
                    {"name": "ASK", "required": True},
                    {"name": "REGION", "required": False},
                ],
            }
        return {"environment_variables": []}

    prompt = MagicMock(side_effect=["", "optional-value", "typed-secret"])
    monkeypatch.setattr(cmd_pull.client, "get", get)
    monkeypatch.setattr(cmd_pull, "password_input", prompt)

    values = cmd_pull._collect_mcp_env_vars(
        detail,
        env_overrides={"TOKEN": "flag-secret", "REGION": "eu-west-1"},
    )

    assert values == {
        "mcp-1": {"TOKEN": "flag-secret", "FILLED": "optional-value"},
        "mcp-2": {"ASK": "typed-secret", "REGION": "eu-west-1"},
    }
    assert prompt.call_args_list == [
        call("  EMPTY [dim](optional)[/dim] (press Enter to skip)"),
        call("  FILLED (press Enter to skip)"),
        call("  ASK"),
    ]
    output = capsys.readouterr().out
    assert "Primary requires 1 environment variable(s)" in output
    assert "TOKEN (from --env)" in output
    assert "Secondary: 1 optional env var(s)" in output


def test_collect_mcp_env_vars_no_prompt_uses_only_known_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    detail = {"mcp_links": [{"mcp_listing_id": "mcp-1"}], "component_links": []}
    monkeypatch.setattr(
        cmd_pull.client,
        "get",
        lambda _path: {
            "environment_variables": [
                {"name": "REQUIRED"},
                {"name": "OPTIONAL", "required": False},
            ]
        },
    )
    prompt = MagicMock(side_effect=AssertionError("prompted in no-prompt mode"))
    monkeypatch.setattr(cmd_pull, "password_input", prompt)

    assert cmd_pull._collect_mcp_env_vars(
        detail,
        no_prompt=True,
        env_overrides={"OPTIONAL": "set", "UNKNOWN": "ignored"},
    ) == {"mcp-1": {"OPTIONAL": "set"}}
    prompt.assert_not_called()
    assert cmd_pull._collect_mcp_env_vars({}, no_prompt=True) == {}


def test_collect_mcp_headers_prompts_and_deduplicates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    detail = {
        "mcp_links": [{"mcp_listing_id": "mcp-1", "mcp_name": "Remote"}],
        "component_links": [
            {"component_type": "mcp", "component_id": "mcp-1"},
            {"component_type": "mcp", "component_id": "mcp-2", "component_name": ""},
            {"component_type": "hook", "component_id": "hook-1"},
        ],
    }

    def get(path: str):
        if path.endswith("mcp-1"):
            return {
                "headers": [
                    {"name": "Authorization", "description": "access token"},
                    {"name": "X-Skip", "description": "optional", "required": False},
                    {"name": "X-Filled", "required": False},
                ]
            }
        if path.endswith("mcp-2"):
            return {
                "name": "Fallback name",
                "headers": [
                    {"name": "X-Required"},
                    {"name": "X-Region", "required": False},
                ],
            }
        return {"headers": []}

    prompt = MagicMock(side_effect=["", "optional-value", "required-value"])
    monkeypatch.setattr(cmd_pull.client, "get", get)
    monkeypatch.setattr(cmd_pull, "password_input", prompt)

    values = cmd_pull._collect_mcp_headers(
        detail,
        header_overrides={"Authorization": "Bearer flag", "X-Region": "eu"},
    )

    assert values == {
        "mcp-1": {"Authorization": "Bearer flag", "X-Filled": "optional-value"},
        "mcp-2": {"X-Required": "required-value", "X-Region": "eu"},
    }
    assert prompt.call_args_list == [
        call("  X-Skip [dim](optional)[/dim] (press Enter to skip)"),
        call("  X-Filled (press Enter to skip)"),
        call("  X-Required"),
    ]
    output = capsys.readouterr().out
    assert "Remote requires 1 header(s)" in output
    assert "Authorization (from --header)" in output
    assert "Fallback name: 1 optional header(s)" in output


def test_collect_mcp_headers_no_prompt_uses_only_known_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    detail = {"mcp_links": [{"mcp_listing_id": "mcp-1"}]}
    monkeypatch.setattr(
        cmd_pull.client,
        "get",
        lambda _path: {"headers": [{"name": "Required"}, {"name": "Optional", "required": False}]},
    )
    prompt = MagicMock(side_effect=AssertionError("prompted in no-prompt mode"))
    monkeypatch.setattr(cmd_pull, "password_input", prompt)

    assert cmd_pull._collect_mcp_headers(
        detail,
        no_prompt=True,
        header_overrides={"Optional": "yes", "Unknown": "ignored"},
    ) == {"mcp-1": {"Optional": "yes"}}
    prompt.assert_not_called()
    assert cmd_pull._collect_mcp_headers({}, no_prompt=True) == {}


def test_dict_to_toml_serializes_every_supported_value_shape() -> None:
    rendered = cmd_pull._dict_to_toml(
        {
            "mcp_servers": {
                "server": {
                    "args": ["one", "two"],
                    "env": {"TOKEN": "secret"},
                    "enabled": True,
                    "label": 'quoted "value"',
                    "retries": 3,
                }
            }
        }
    )

    assert rendered == (
        "[mcp_servers.server]\n"
        'args = ["one", "two"]\n'
        'env.TOKEN = "secret"\n'
        "enabled = true\n"
        'label = "quoted \\"value\\""\n'
        "retries = 3\n"
    )


def test_toml_merge_replaces_a_server_with_its_nested_tables(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "# mine\n"
        'model = "gpt-5"\n\n'
        '[mcp_servers.srv]\ncommand = "old"\n\n'
        '[mcp_servers.srv.env]\nMY_VAR = "1"\n\n'
        '[mcp_servers.other]\ncommand = "keep"\n'
    )

    content = {"mcp_servers": {"srv": {"command": "new", "env": {"TOKEN": "t"}}}}
    assert cmd_pull._write_file(path, content, merge_mcp=True) == "merged"

    text = path.read_text()
    assert text.startswith("# mine\n")
    assert tomllib.loads(text) == {
        "model": "gpt-5",
        "mcp_servers": {"srv": {"command": "new", "env": {"TOKEN": "t"}}, "other": {"command": "keep"}},
    }


def test_toml_merge_replaces_a_server_defined_only_by_a_sub_table(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[mcp_servers.srv.env]\nMY_VAR = "1"\n')

    cmd_pull._write_file(path, {"mcp_servers": {"srv": {"command": "new", "env": {"A": "b"}}}}, merge_mcp=True)

    assert tomllib.loads(path.read_text()) == {"mcp_servers": {"srv": {"command": "new", "env": {"A": "b"}}}}


def test_toml_merge_refuses_to_write_invalid_toml_and_leaves_the_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    original = '[mcp_servers]\nsrv = { command = "old" }\n'
    path.write_text(original)

    with pytest.raises(tomllib.TOMLDecodeError):
        cmd_pull._write_file(path, {"mcp_servers": {"srv": {"command": "new"}}}, merge_mcp=True)

    assert path.read_text() == original


def test_write_file_handles_toml_json_strings_and_empty_content(tmp_path: Path) -> None:
    toml_path = tmp_path / "config.toml"
    assert cmd_pull._write_file(toml_path, {"mcp_servers": {"old": {"command": "old"}}}) == "created"
    assert (
        cmd_pull._write_file(
            toml_path,
            {"mcp_servers": {"new": {"command": "new"}}},
            merge_mcp=True,
        )
        == "merged"
    )
    assert toml_path.read_text() == ('[mcp_servers.old]\ncommand = "old"\n\n[mcp_servers.new]\ncommand = "new"\n')

    json_path = tmp_path / "broken.json"
    json_path.write_text("not-json")
    with pytest.raises(ValueError, match="unreadable JSON"):
        cmd_pull._write_file(json_path, {"servers": {"new": {"command": "npx"}}}, merge_mcp=True)
    assert json_path.read_text() == "not-json"

    empty_path = tmp_path / "empty.json"
    assert cmd_pull._write_file(empty_path, {}) == "created"
    assert empty_path.read_text() == "{}\n"

    text_path = tmp_path / "rules.md"
    assert cmd_pull._write_file(text_path, "first") == "created"
    assert cmd_pull._write_file(text_path, "second") == "updated"
    assert text_path.read_text() == "second"


def test_write_file_merges_hooks_config_that_starts_with_a_scalar(tmp_path: Path) -> None:
    # Cursor's hooks.json leads with "version": 1; pulling again must merge "hooks".
    hooks_path = tmp_path / "hooks.json"
    first = {"version": 1, "hooks": {"stop": [{"command": "observal"}]}}
    hooks_path.write_text(json.dumps({"version": 1, "hooks": {"user": [{"command": "mine"}]}}))

    assert cmd_pull._write_file(hooks_path, first, merge_mcp=True) == "merged"
    assert cmd_pull._write_file(hooks_path, first, merge_mcp=True) == "merged"

    assert json.loads(hooks_path.read_text()) == {
        "version": 1,
        "hooks": {"user": [{"command": "mine"}], "stop": [{"command": "observal"}]},
    }


def test_write_file_yaml_merges_or_preserves_existing_content(tmp_path: Path) -> None:
    path = tmp_path / "goose.yaml"
    path.write_text(yaml.safe_dump({"provider": "anthropic", "extensions": {"old": {"type": "stdio"}}}))
    assert (
        cmd_pull._write_file(
            path,
            {"extensions": {"github": {"type": "stdio"}}},
            merge_mcp=True,
        )
        == "merged"
    )
    assert yaml.safe_load(path.read_text()) == {
        "provider": "anthropic",
        "extensions": {"old": {"type": "stdio"}, "github": {"type": "stdio"}},
    }

    replace_path = tmp_path / "replace.yml"
    replace_path.write_text(yaml.safe_dump({"provider": "old", "extensions": {"old": {}}}))
    assert (
        cmd_pull._write_file(
            replace_path,
            {"provider": "new", "extensions": {"new": {}}},
        )
        == "merged"
    )
    assert yaml.safe_load(replace_path.read_text()) == {"provider": "new", "extensions": {"new": {}}}

    malformed = tmp_path / "malformed.yaml"
    malformed.write_text("extensions: [unterminated\n")
    with pytest.raises(ValueError, match="unreadable YAML"):
        cmd_pull._write_file(malformed, {"extensions": {"new": {}}}, merge_mcp=True)
    assert malformed.read_text() == "extensions: [unterminated\n"

    unexpected = tmp_path / "unexpected.yaml"
    unexpected.write_text("- one\n- two\n")
    with pytest.raises(ValueError, match="top level"):
        cmd_pull._write_file(unexpected, {"extensions": {}}, merge_mcp=True)
    assert unexpected.read_text() == "- one\n- two\n"

    created = tmp_path / "new.yaml"
    assert cmd_pull._write_file(created, {"extensions": {"new": {}}}, merge_mcp=True) == "created"
    assert yaml.safe_load(created.read_text()) == {"extensions": {"new": {}}}


def _kiro_profile_with_hooks() -> dict:
    return {
        "hooks": {
            "stop": [
                {"command": "python -m observal_cli.old"},
                {"command": "echo user"},
            ],
            "custom": [{"command": "custom"}],
        }
    }


def test_rewrite_kiro_agent_profile_strips_inline_hooks_for_ide(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiro IDE 1.0 hides agents carrying a `hooks` field, so Observal's go away."""
    import observal_cli.harness.kiro as kiro_adapter

    monkeypatch.setattr(kiro_adapter, "use_inline_hooks", lambda *_a, **_k: False)

    assert cmd_pull._rewrite_kiro_agent_profile(_kiro_profile_with_hooks(), agent_id="agent-1") == {
        "hooks": {
            "stop": [{"command": "echo user"}],
            "custom": [{"command": "custom"}],
        }
    }


def test_rewrite_kiro_agent_profile_drops_hooks_key_when_only_observal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An agent with nothing but Observal hooks must end up with no `hooks` key at all."""
    import observal_cli.harness.kiro as kiro_adapter

    monkeypatch.setattr(kiro_adapter, "use_inline_hooks", lambda *_a, **_k: False)
    content = {"name": "a", "hooks": {"stop": [{"command": "python -m observal_cli.hooks.session_push"}]}}

    assert cmd_pull._rewrite_kiro_agent_profile(content, agent_id="agent-1") == {"name": "a"}


def test_rewrite_kiro_agent_profile_keeps_inline_hooks_on_legacy_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kiro CLI 2.x only understands inline hooks, so they are re-added there."""
    import observal_cli.harness.kiro as kiro_adapter
    import observal_cli.harness_specs.kiro_hooks_spec as spec

    monkeypatch.setattr(kiro_adapter, "use_inline_hooks", lambda *_a, **_k: True)
    build = MagicMock(
        return_value={
            "stop": [{"command": "new stop"}],
            "userPromptSubmit": [{"command": "new prompt"}],
        }
    )
    monkeypatch.setattr(spec, "build_kiro_hooks", build)

    assert cmd_pull._rewrite_kiro_agent_profile(_kiro_profile_with_hooks(), agent_id="agent-1") == {
        "hooks": {
            "stop": [{"command": "echo user"}, {"command": "new stop"}],
            "custom": [{"command": "custom"}],
            "userPromptSubmit": [{"command": "new prompt"}],
        }
    }
    build.assert_called_once_with(agent_id="agent-1")


def test_rewrite_copilot_hooks_removes_both_legacy_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    import observal_cli.harness_specs.copilot_cli_hooks_spec as spec

    build = MagicMock(
        return_value={"hooks": {"sessionStart": [{"bash": "new attributed"}], "stop": [{"bash": "new stop"}]}}
    )
    monkeypatch.setattr(spec, "build_copilot_cli_hooks", build)
    content = {
        "hooks": {
            "sessionStart": [
                {"bash": "python -m observal_cli.hooks.copilot_cli_session_push"},
                {"bash": "python -m observal_cli.hooks.session_push --harness copilot-cli"},
                {"bash": "echo user"},
            ]
        }
    }

    assert cmd_pull._rewrite_copilot_cli_hooks(content, agent_id="agent-2") == {
        "hooks": {
            "sessionStart": [{"bash": "echo user"}, {"bash": "new attributed"}],
            "stop": [{"bash": "new stop"}],
        }
    }
    build.assert_called_once_with(agent_id="agent-2")
    empty = {}
    assert cmd_pull._rewrite_copilot_cli_hooks(empty, agent_id="ignored") is empty


def test_resolve_path_maps_project_home_and_rejects_traversal(
    tmp_path: Path, isolated_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = (tmp_path / "project").resolve()
    target.mkdir()

    assert cmd_pull._resolve_path("rules/AGENTS.md", target) == target / "rules" / "AGENTS.md"
    assert cmd_pull._resolve_path("~/agent/config.json", target) == target / "agent" / "config.json"
    assert cmd_pull._resolve_path("~\\agent.json", target) == target / "agent.json"
    assert cmd_pull._resolve_path("~/agent/config.json", target, allow_home=True) == (
        isolated_home / "agent" / "config.json"
    )

    outside = tmp_path / "outside"
    outside.mkdir()
    (target / "link").symlink_to(outside, target_is_directory=True)
    for unsafe in ("../outside.txt", str(outside / "absolute.txt"), "link/escaped.txt"):
        with pytest.raises(typer.Exit) as caught:
            cmd_pull._resolve_path(unsafe, target)
        assert caught.value.exit_code == 7
    output = capsys.readouterr().out
    assert "escapes the target directory" in output


def test_parse_model_overrides_and_saved_model_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    assert cmd_pull._parse_model_overrides([" default-one ", "codex = gpt-5", " default-two "]) == (
        "default-two",
        {"codex": "gpt-5"},
    )
    for invalid in ("=missing", "kiro=", ""):
        with pytest.raises(typer.Exit) as error:
            cmd_pull._parse_model_overrides([invalid])
        assert error.value.exit_code == 7

    adapter = MagicMock()
    adapter.saved_model.return_value = "saved-model"
    ensure = MagicMock()
    get_adapter = MagicMock(return_value=adapter)
    monkeypatch.setattr(cmd_pull, "ensure_loaded", ensure)
    monkeypatch.setattr(cmd_pull, "get_adapter", get_adapter)
    detail = {"models_by_harness": {"kiro": "saved-model"}}

    assert cmd_pull._agent_saved_model(detail, "kiro") == "saved-model"
    ensure.assert_called_once_with()
    get_adapter.assert_called_once_with("kiro")
    adapter.saved_model.assert_called_once_with(detail)


def test_collect_install_options_interactively_selects_scope_model_and_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    import observal_cli.model_catalog as catalog
    import observal_shared.harness_registry as registry

    adapter = MagicMock()
    monkeypatch.setattr(cmd_pull, "_SCOPE_AWARE_HARNESSES", {"demo": ("project files", "user files")})
    monkeypatch.setattr(registry, "get_default_scope", lambda _harness: "user")
    monkeypatch.setattr(registry, "has_model_selection", lambda _harness: True)
    monkeypatch.setattr(cmd_pull, "_agent_saved_model", lambda _detail, _harness: None)
    monkeypatch.setattr(cmd_pull, "ensure_loaded", MagicMock())
    monkeypatch.setattr(cmd_pull, "get_adapter", lambda _harness: adapter)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    picker = MagicMock(side_effect=["project files", "Pretty model"])
    monkeypatch.setattr(cmd_pull, "select_one", picker)
    fetch = MagicMock(return_value={"models": [{"id": "model-1"}]})
    choices = MagicMock(return_value=[("Pretty model", "provider/model-1")])
    monkeypatch.setattr(catalog, "fetch_catalog", fetch)
    monkeypatch.setattr(catalog, "model_choices_for_picker", choices)

    options = cmd_pull._collect_install_options(
        "demo",
        scope=None,
        model_default=None,
        model_overrides={},
        tools="Read,Write",
        no_prompt=False,
        refresh_models=True,
        agent_detail={},
    )

    assert options == {"scope": "project", "model": "provider/model-1"}
    assert picker.call_args_list == [
        call("  Scope", ["user files", "project files"], default="user files"),
        call(
            "  Model",
            ["auto (let the harness decide)", "Pretty model"],
            default="auto (let the harness decide)",
        ),
    ]
    fetch.assert_called_once_with(refresh=True)
    choices.assert_called_once_with({"models": [{"id": "model-1"}]}, "demo")
    adapter.apply_install_options.assert_called_once_with(options, "Read,Write")


def test_collect_install_options_handles_catalog_and_model_format_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import observal_cli.model_catalog as catalog
    import observal_cli.render as render
    import observal_shared.harness_registry as registry

    adapter = MagicMock()
    monkeypatch.setattr(cmd_pull, "_SCOPE_AWARE_HARNESSES", {})
    monkeypatch.setattr(registry, "has_model_selection", lambda _harness: True)
    monkeypatch.setattr(cmd_pull, "ensure_loaded", MagicMock())
    monkeypatch.setattr(cmd_pull, "get_adapter", lambda _harness: adapter)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    monkeypatch.setattr(cmd_pull, "_agent_saved_model", lambda _detail, _harness: "saved/model")
    format_model = MagicMock(return_value=("Pretty saved", "", {}))
    monkeypatch.setattr(render, "format_model", format_model)
    assert cmd_pull._collect_install_options(
        "demo",
        scope=None,
        model_default=None,
        model_overrides={},
        tools=None,
        no_prompt=False,
    ) == {"model": "saved/model"}
    assert "Pretty saved (from agent)" in capsys.readouterr().out

    format_model.side_effect = ValueError("bad model")
    assert cmd_pull._collect_install_options(
        "demo",
        scope=None,
        model_default=None,
        model_overrides={},
        tools=None,
        no_prompt=False,
    ) == {"model": "saved/model"}
    assert "saved/model (from agent)" in capsys.readouterr().out

    monkeypatch.setattr(cmd_pull, "_agent_saved_model", lambda _detail, _harness: None)
    monkeypatch.setattr(catalog, "fetch_catalog", MagicMock(side_effect=RuntimeError("offline")))
    choices = MagicMock(return_value=[])
    monkeypatch.setattr(catalog, "model_choices_for_picker", choices)
    monkeypatch.setattr(cmd_pull, "select_one", lambda *_args, **_kwargs: "auto (let the harness decide)")
    with pytest.raises(RuntimeError, match="offline"):
        cmd_pull._collect_install_options(
            "demo",
            scope=None,
            model_default=None,
            model_overrides={},
            tools=None,
            no_prompt=False,
        )
    choices.assert_not_called()


def test_collect_install_options_no_prompt_uses_registry_default_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    import observal_shared.harness_registry as registry

    adapter = MagicMock()
    monkeypatch.setattr(cmd_pull, "_SCOPE_AWARE_HARNESSES", {"demo": ("project", "user")})
    monkeypatch.setattr(registry, "get_default_scope", lambda _harness: "user")
    monkeypatch.setattr(registry, "has_model_selection", lambda _harness: False)
    monkeypatch.setattr(cmd_pull, "ensure_loaded", MagicMock())
    monkeypatch.setattr(cmd_pull, "get_adapter", lambda _harness: adapter)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    picker = MagicMock(side_effect=AssertionError("no prompt expected"))
    monkeypatch.setattr(cmd_pull, "select_one", picker)

    assert cmd_pull._collect_install_options(
        "demo",
        scope=None,
        model_default=None,
        model_overrides={},
        tools=None,
        no_prompt=True,
    ) == {"scope": "user"}
    picker.assert_not_called()


def test_pull_dir_registers_mcp_in_target_and_records_server_selected_alias(
    pull_app: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "elsewhere"
    project.mkdir()
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    component_id = "11111111-1111-4111-8111-111111111111"
    detail = _agent_detail(
        component_links=[
            {"component_type": "mcp", "component_id": component_id, "component_name": "display", "version_ref": "old"}
        ]
    )
    boundaries.get.side_effect = lambda path: (
        detail if "/agents/" in path else {"environment_variables": [], "headers": []}
    )
    boundaries.post.return_value = {
        "version": "1.4.0",
        "lock": {
            "status": "locked",
            "digest": "sha256:" + "a" * 64,
            "components": [
                {
                    "id": component_id,
                    "type": "mcp",
                    "version": "2.1.0",
                    "version_id": "22222222-2222-4222-8222-222222222222",
                    "digest": "sha256:" + "b" * 64,
                    "source": "lock",
                }
            ],
        },
        "selected_version": "1.4.0",
        "component_pins": [
            {
                "id": component_id,
                "name": "display",
                "type": "mcp",
                "version": "2.1.0",
                "local_name": "installed-mcp",
                "qualified_name": "first/display",
            }
        ],
        "config_snippet": {
            "agent_profile": {"path": ".claude/agents/reviewer.md", "content": "agent\n"},
            "mcp_setup_commands": [["claude", "mcp", "add", "installed-mcp", "--", "inert"]],
        },
    }
    calls = []

    def add_mcp(command, *, cwd, capture_output, text, timeout):
        calls.append((command, cwd))
        (cwd / ".mcp.json").write_text(json.dumps({"mcpServers": {"installed-mcp": {"command": "inert"}}}))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(cmd_pull.subprocess, "run", add_mcp)
    result = _invoke(pull_app, project, "--scope", "project")
    assert result.exit_code == 0, result.output
    assert calls == [(["claude", "mcp", "add", "--scope", "project", "installed-mcp", "--", "inert"], project)]
    assert not (caller / ".mcp.json").exists()
    components = boundaries.upsert.call_args.kwargs["components"]
    assert components[0]["local_name"] == "installed-mcp"
    assert components[0]["version"] == "2.1.0"
    assert components[0]["scope"] == "project"
    assert components[0]["mcp_integrity"].startswith("sha256-")
    assert components[0]["version_id"] == "22222222-2222-4222-8222-222222222222"
    assert components[0]["digest"] == "sha256:" + "b" * 64
    assert components[0]["source"] == "lock"


def test_pull_full_project_flow_writes_every_shape_and_exact_side_effects(
    pull_app: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "project"
    target.mkdir()
    detail = _agent_detail(
        mcp_links=[{"mcp_listing_id": "mcp-1", "mcp_name": "GitHub"}],
        component_links=[
            {
                "component_type": "mcp",
                "component_id": "mcp-1",
                "component_name": "github",
                "version_ref": "2.1.0",
            },
            {
                "component_type": "skill",
                "component_id": "skill-1",
                "component_name": "review-skill",
                "version_ref": "3.0.0",
            },
        ],
    )
    listing = {
        "environment_variables": [{"name": "API_KEY"}, {"name": "UNSET", "required": False}],
        "headers": [{"name": "Authorization"}, {"name": "X-Unset", "required": False}],
    }

    version_detail = {
        "version": "1.4.0",
        "components": [
            {"component_type": "mcp", "component_id": "mcp-1", "name": "github", "resolved_version": "2.0.0"},
            {"component_type": "skill", "component_id": "skill-1", "name": "review-skill", "resolved_version": "3.0.0"},
        ],
    }

    def get(path: str):
        if path == "/api/v1/agents/agent-uuid":
            return detail
        if path == "/api/v1/agents/agent-uuid/versions/1.4.0":
            return version_detail
        if path == "/api/v1/mcps/mcp-1/versions/2.0.0":
            return listing
        raise AssertionError(path)

    boundaries.get.side_effect = get

    snippet = {
        "mcp_config": {
            "path": ".config/mcp.json",
            "content": {"mcpServers": {"new": {"command": "npx", "args": ["new"]}}},
        },
        "hooks_config": {
            "path": ".config/hooks.json",
            "content": {
                "hooks": {
                    "new": [{"command": "python3 -m observal_cli.hooks.session_push"}],
                }
            },
            "merge": True,
        },
        "agent_profile": {
            "path": ".agents/reviewer.json",
            "content": {"name": "reviewer", "tools": ["read"]},
        },
        "steering_file": {"path": ".agents/steering.md", "content": "steer\n"},
        "hook_files": [
            {"path": ".agents/hooks/run.sh", "content": "#!/bin/sh\nexit 0\n", "executable": True},
            {"path": ".agents/hooks/data.txt", "content": "data\n"},
        ],
        "prompt_files": [{"path": ".github/prompts/review.prompt.md", "content": "review prompt\n"}],
        "skills": [{"path": ".agents/skills/native/SKILL.md", "content": "native skill\n"}],
        "skill_components": [
            {
                "name": "git-skill",
                "path": ".agents/skills/git-skill/SKILL.md",
                "git_url": "https://example.test/skill.git",
                "skill_path": "skills/review",
                "git_ref": "v2",
                "skill_md_content": "cached git skill\n",
            },
            {
                "name": "direct-skill",
                "path": ".agents/skills/direct-skill/SKILL.md",
                "skill_md_content": "direct skill\n",
                "script_content": "print('ok')\n",
                "script_filename": "run.py",
            },
        ],
        "mcp_setup_commands": [
            ["good", "mcp", "add", "new"],
            ["missing", "mcp", "add", "manual"],
            ["bad", "mcp", "add", "broken"],
        ],
        "_warnings": ["snippet warning"],
    }
    lock = {
        "lock_version": 1,
        "status": "locked",
        "digest": "sha256:" + "a" * 64,
        "problems": [],
        "components": [
            {
                "type": "mcp",
                "id": "mcp-1",
                "qualified_name": "acme/github",
                "version": "2.0.0",
                "version_id": "mcp-version-2",
                "digest": "sha256:" + "b" * 64,
                "source": "lock",
            },
            {
                "type": "skill",
                "id": "skill-1",
                "qualified_name": "acme/review-skill",
                "version": "3.0.0",
                "version_id": "skill-version-3",
                "digest": "sha256:" + "c" * 64,
                "source": "lock",
            },
        ],
    }
    boundaries.post.return_value = {
        "config_snippet": snippet,
        "warnings": ["server warning"],
        "version": "1.4.0",
        "lock": lock,
    }

    mcp_path = target / ".config" / "mcp.json"
    mcp_path.parent.mkdir(parents=True)
    mcp_path.write_text(json.dumps({"mcpServers": {"old": {"command": "old"}}, "keep": True}))
    hooks_path = target / ".config" / "hooks.json"
    hooks_path.write_text(json.dumps({"hooks": {"old": [{"command": "echo user"}]}, "keep": True}))
    executable = target / ".agents" / "hooks" / "run.sh"
    executable.parent.mkdir(parents=True)
    executable.write_text("old\n")
    prompt_path = target / ".github" / "prompts" / "review.prompt.md"
    prompt_path.parent.mkdir(parents=True)
    prompt_path.write_text("old prompt\n")

    def rewrite_hooks(content: dict, agent_id: str) -> dict:
        content["hooks"]["adapter"] = [{"agent_id": agent_id}]
        return content

    def rewrite_profile(content: dict, agent_id: str) -> dict:
        return {**content, "agent_id": agent_id}

    boundaries.adapter.rewrite_hooks.side_effect = rewrite_hooks
    boundaries.adapter.rewrite_agent_profile.side_effect = rewrite_profile

    run = MagicMock()

    def run_command(command: list[str], **_kwargs):
        return subprocess.CompletedProcess(command, 0, "", "")

    run.side_effect = run_command
    monkeypatch.setattr(cmd_pull.subprocess, "run", run)

    result = _invoke(
        pull_app,
        target,
        "--scope",
        "project",
        "--model",
        "fallback-model",
        "--model",
        "claude-code=selected-model",
        "--tools",
        "Read,Write",
        "--env",
        "API_KEY='secret'",
        "--header",
        'Authorization="Bearer token"',
        "--version",
        "1.4.0",
    )

    assert result.exit_code == 0, result.output
    boundaries.resolve.assert_called_once_with("agent", "acme/reviewer")
    # The requested version's pins drive MCP prompts, and each MCP is read once.
    assert boundaries.get.call_args_list == [
        call("/api/v1/agents/agent-uuid"),
        call("/api/v1/agents/agent-uuid/versions/1.4.0"),
        call("/api/v1/mcps/mcp-1/versions/2.0.0"),
    ]
    boundaries.local_name.assert_called_once_with(
        "claude-code",
        "agent",
        "acme",
        "reviewer",
        scope="project",
        directory=str(target.resolve()),
    )
    boundaries.post.assert_called_once_with(
        "/api/v1/agents/agent-uuid/install",
        {
            "harness": "claude-code",
            "env_values": {"mcp-1": {"API_KEY": "secret"}},
            "header_values": {"mcp-1": {"Authorization": "Bearer token"}},
            "options": {
                "scope": "project",
                "model": "selected-model",
                "tools": "Read,Write",
                "local_name": "local-reviewer",
            },
            "platform": sys.platform,
            "supported_features": ["skill_extra_files_v1"],
            "version": "1.4.0",
        },
    )
    boundaries.invalidate.assert_not_called()

    assert json.loads(mcp_path.read_text()) == {
        "mcpServers": {
            "old": {"command": "old"},
            "new": {"command": "npx", "args": ["new"]},
        },
        "keep": True,
    }
    assert json.loads(hooks_path.read_text()) == {
        "hooks": {
            "old": [{"command": "echo user"}],
            # Rewritten to this CLI's interpreter, with -P so the project cannot shadow observal_cli.
            "new": [{"command": f"{shlex.quote(sys.executable)} -I -m observal_cli.hooks.session_push"}],
            "adapter": [{"agent_id": "agent-uuid"}],
        },
        "keep": True,
    }
    assert json.loads((target / ".agents" / "reviewer.json").read_text()) == {
        "name": "reviewer",
        "tools": ["read"],
        "agent_id": "agent-uuid",
    }
    assert (target / ".agents" / "steering.md").read_text() == "steer\n"
    assert executable.read_text() == "#!/bin/sh\nexit 0\n"
    assert executable.stat().st_mode & stat.S_IXUSR
    assert (target / ".agents" / "hooks" / "data.txt").read_text() == "data\n"
    assert prompt_path.read_text() == "review prompt\n"
    assert (target / ".agents" / "skills" / "native" / "SKILL.md").read_text() == "native skill\n"
    assert (target / ".agents" / "skills" / "git-skill" / "SKILL.md").read_text() == "cached git skill\n"
    assert (target / ".agents" / "skills" / "direct-skill" / "SKILL.md").read_text() == "direct skill\n"

    boundaries.git_install.assert_called_once_with(
        name="git-skill",
        git_url="https://example.test/skill.git",
        skill_path="skills/review",
        git_ref="v2",
        harness="claude-code",
        scope="project",
        skill_md_content="cached git skill\n",
        cwd=target.resolve(),
        dest=target.resolve() / ".agents" / "skills" / "git-skill",
    )
    boundaries.direct_install.assert_called_once_with(
        name="direct-skill",
        skill_md_content="direct skill\n",
        script_content="print('ok')\n",
        script_filename="run.py",
        harness="claude-code",
        scope="project",
        cwd=target.resolve(),
        dest=target.resolve() / ".agents" / "skills" / "direct-skill",
    )
    boundaries.upsert.assert_called_once_with(
        "claude-code",
        name="reviewer",
        agent_id="agent-uuid",
        version="1.4.0",
        scope="project",
        directory=str(target.resolve()),
        components=[
            {
                "type": "mcp",
                "name": "github",
                "id": "mcp-1",
                "version": "2.0.0",
                "version_id": "mcp-version-2",
                "digest": "sha256:" + "b" * 64,
                "qualified_name": "acme/github",
                "source": "lock",
            },
            {
                "type": "skill",
                "name": "review-skill",
                "id": "skill-1",
                "version": "3.0.0",
                "version_id": "skill-version-3",
                "digest": "sha256:" + "c" * 64,
                "qualified_name": "acme/review-skill",
                "source": "lock",
            },
        ],
        namespace="acme",
        slug="reviewer",
        local_name="local-reviewer",
        lock_digest="sha256:" + "a" * 64,
        lock_status="locked",
    )
    boundaries.snapshot.assert_called_once_with(project_dir=str(target.resolve()))
    boundaries.adapter.persist_active_agent.assert_called_once_with(
        "agent-uuid", "reviewer", "1.4.0", is_user_scope=False
    )
    boundaries.emit.assert_called_once_with(
        "agent.pull",
        resource_type="agent",
        resource_id="agent-uuid",
        resource_name="reviewer",
        detail="harness=claude-code",
        sensitivity="high",
    )
    assert run.call_args_list == [
        call(["good", "mcp", "add", "new"], capture_output=True, text=True, timeout=60, cwd=target.resolve()),
        call(["missing", "mcp", "add", "manual"], capture_output=True, text=True, timeout=60, cwd=target.resolve()),
        call(["bad", "mcp", "add", "broken"], capture_output=True, text=True, timeout=60, cwd=target.resolve()),
    ]
    for visible in (
        "Pulled claude-code config (10 files)",
        "server warning",
        "snippet warning",
        "Registered MCP servers",
    ):
        assert visible in result.output


def test_pull_dry_run_previews_all_shapes_without_mutating_boundaries(
    pull_app: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "project"
    target.mkdir()
    boundaries.get.return_value = _agent_detail()
    boundaries.post.return_value = {
        "config_snippet": {
            "mcp_config": {"path": "mcp.json", "content": {"mcpServers": {}}},
            "hooks_config": {"path": "hooks.json", "content": {"hooks": {}}},
            "agent_profile": {"path": "agent.json", "content": {"name": "reviewer"}},
            "steering_file": {"path": "steering.md", "content": "steer"},
            "hook_files": [{"path": "hook.sh", "content": "hook", "executable": True}],
            "prompt_files": [{"path": "prompt.md", "content": "prompt"}],
            "skills": [{"path": "native/SKILL.md", "content": "skill"}],
            "skill_components": [
                {"name": "git-skill", "git_url": "https://example.test/git"},
                {"name": "direct-skill", "path": "direct/SKILL.md", "skill_md_content": "direct"},
            ],
            "mcp_setup_commands": [["claude", "mcp", "add", "server"]],
        }
    }
    run = MagicMock(side_effect=AssertionError("setup command ran during dry run"))
    monkeypatch.setattr(cmd_pull.subprocess, "run", run)

    result = _invoke(pull_app, target, "--dry-run")

    assert result.exit_code == 0, result.output
    assert list(target.iterdir()) == []
    assert result.output.count("would write") == 8
    assert "would clone  <skill:git-skill>" in result.output
    assert "Would run these setup commands" in result.output
    assert "$ claude mcp add server" in result.output
    boundaries.git_install.assert_not_called()
    boundaries.direct_install.assert_not_called()
    boundaries.upsert.assert_not_called()
    boundaries.snapshot.assert_not_called()
    boundaries.adapter.persist_active_agent.assert_not_called()
    boundaries.emit.assert_not_called()
    run.assert_not_called()


def test_pull_user_scope_expands_home_for_string_hook_and_agent_files(
    pull_app: typer.Typer,
    boundaries: SimpleNamespace,
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil

    target = tmp_path / "project"
    target.mkdir()
    boundaries.adapter.allow_home_agent_profile.return_value = True
    boundaries.post.return_value = {
        "config_snippet": {
            "mcp_config": {
                "path": "~/.kiro/mcp.json",
                "content": {"mcpServers": {"server": {"command": "npx"}}},
            },
            "hooks_config": {
                "path": "~/.kiro/hooks.txt",
                "content": 'command = "observal-hook.sh --agent reviewer"\n',
            },
            "agent_profile": {
                "path": "~/.kiro/agents/reviewer.md",
                "content": 'stop = "observal-stop-hook.sh"\n',
            },
        }
    }
    monkeypatch.setattr(shutil, "which", lambda name: f"/opt/observal/{name}")

    result = _invoke(pull_app, target, "--scope", "user", harness="kiro")

    assert result.exit_code == 0, result.output
    assert "Files will be written to your home directory" in result.output
    assert json.loads((isolated_home / ".kiro" / "mcp.json").read_text()) == {
        "mcpServers": {"server": {"command": "npx"}}
    }
    assert (isolated_home / ".kiro" / "hooks.txt").read_text() == ('command = "/opt/observal/observal-hook.sh"\n')
    assert (isolated_home / ".kiro" / "agents" / "reviewer.md").read_text() == (
        'stop = "/opt/observal/observal-stop-hook.sh"\n'
    )
    assert list(target.iterdir()) == []
    boundaries.local_name.assert_called_once_with(
        "kiro",
        "agent",
        "acme",
        "reviewer",
        scope="user",
        directory=str(target.resolve()),
    )
    boundaries.upsert.assert_called_once()


def test_pull_json_rejects_malformed_server_input_requirements(
    pull_app_boundary: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
) -> None:
    detail = _agent_detail(mcp_links=[{"mcp_listing_id": "mcp-1", "mcp_name": "GitHub"}])

    def get(path: str):
        if path == "/api/v1/agents/agent-uuid":
            return detail
        if path == "/api/v1/mcps/mcp-1":
            return {"environment_variables": [{"name": ""}], "headers": []}
        raise AssertionError(path)

    boundaries.get.side_effect = get
    result = RUNNER.invoke(
        pull_app_boundary,
        [
            "agent",
            "pull",
            "acme/reviewer",
            "--harness",
            "claude-code",
            "--dir",
            str(tmp_path / "project"),
            "--no-prompt",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 9
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["result"] == {
        "invalid_input_kind": "environment_variable",
        "component": "GitHub",
    }
    boundaries.post.assert_not_called()


def test_pull_json_missing_required_inputs_returns_needs_input_before_install(
    pull_app_boundary: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
) -> None:
    detail = _agent_detail(mcp_links=[{"mcp_listing_id": "mcp-1", "mcp_name": "GitHub"}])

    def get(path: str):
        if path == "/api/v1/agents/agent-uuid":
            return detail
        if path == "/api/v1/mcps/mcp-1":
            return {
                "environment_variables": [
                    {"name": "API_KEY", "required": True},
                    {"name": "REGION", "required": False},
                ],
                "headers": [{"name": "Authorization", "required": True}],
            }
        raise AssertionError(path)

    boundaries.get.side_effect = get
    target = tmp_path / "project"
    result = RUNNER.invoke(
        pull_app_boundary,
        [
            "agent",
            "pull",
            "acme/reviewer",
            "--harness",
            "claude-code",
            "--dir",
            str(target),
            "--no-prompt",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 7
    assert result.stdout == ""
    error = json.loads(result.stderr)["error"]
    assert error["result"] == {
        "needs_input": True,
        "inputs": [
            {"kind": "environment_variable", "name": "API_KEY", "component": "GitHub"},
            {"kind": "header", "name": "Authorization", "component": "GitHub"},
        ],
    }
    boundaries.post.assert_not_called()
    boundaries.local_name.assert_not_called()
    assert not target.exists()


def test_pull_partial_skill_failure_stops_metadata_updates_without_rolling_back_files(
    pull_app: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
) -> None:
    target = tmp_path / "project"
    boundaries.git_install.side_effect = None
    boundaries.git_install.return_value = None
    boundaries.direct_install.side_effect = None
    boundaries.direct_install.return_value = None
    boundaries.post.return_value = {
        "config_snippet": {
            "agent_profile": {"path": "agent.md", "content": "written before skills\n"},
            "skill_components": [
                {"name": "git-failure", "git_url": "https://example.test/fail"},
                {"name": "direct-failure", "skill_md_content": None},
            ],
        }
    }

    result = _invoke(pull_app, target)

    assert result.exit_code == 9
    assert "Failed to install 2 agent skill(s)" in result.output
    assert (target / "agent.md").read_text() == "written before skills\n"
    boundaries.upsert.assert_not_called()
    boundaries.snapshot.assert_not_called()
    boundaries.adapter.persist_active_agent.assert_not_called()
    boundaries.emit.assert_not_called()


def test_pull_json_dry_run_invalid_setup_has_no_partial_side_effects(
    pull_app_boundary: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
) -> None:
    target = tmp_path / "project"
    boundaries.post.return_value = {
        "config_snippet": {
            "agent_profile": {"path": "agent.md", "content": "agent\n"},
            "mcp_setup_commands": [{}],
        }
    }

    result = RUNNER.invoke(
        pull_app_boundary,
        [
            "agent",
            "pull",
            "acme/reviewer",
            "--harness",
            "claude-code",
            "--dir",
            str(target),
            "--dry-run",
            "--no-prompt",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 9
    assert result.stdout == ""
    state = json.loads(result.stderr)["error"]["result"]
    assert state["partial"] is False
    assert state["dry_run"] is True
    assert state["files"] == [{"path": str(target / "agent.md"), "status": "would write"}]
    assert not target.exists()
    boundaries.upsert.assert_not_called()


def test_pull_json_skill_exception_reports_partial_state(
    pull_app_boundary: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
) -> None:
    target = tmp_path / "project"
    boundaries.direct_install.side_effect = OSError("private filesystem detail")
    boundaries.post.return_value = {
        "config_snippet": {
            "agent_profile": {"path": "agent.md", "content": "agent\n"},
            "skill_components": [{"name": "broken-skill", "skill_md_content": "content"}],
        }
    }

    result = RUNNER.invoke(
        pull_app_boundary,
        [
            "agent",
            "pull",
            "acme/reviewer",
            "--harness",
            "claude-code",
            "--dir",
            str(target),
            "--no-prompt",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 9
    assert result.stdout == ""
    error = json.loads(result.stderr)["error"]
    assert error["result"]["stage"] == "install_skills"
    assert error["result"]["failed_skills"] == ["broken-skill"]
    assert error["result"]["partial"] is True
    assert "private filesystem detail" not in result.stderr
    assert (target / "agent.md").is_file()
    boundaries.upsert.assert_not_called()


def test_pull_json_setup_failure_reports_secret_free_partial_state(
    pull_app_boundary: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "project"
    boundaries.post.return_value = {
        "config_snippet": {
            "agent_profile": {"path": "agent.md", "content": "agent\n"},
            "mcp_setup_commands": [["broken", "--token", "secret-value"]],
        }
    }
    monkeypatch.setattr(
        cmd_pull.subprocess,
        "run",
        MagicMock(return_value=subprocess.CompletedProcess(["broken"], 2, "", "private stderr")),
    )

    result = RUNNER.invoke(
        pull_app_boundary,
        [
            "agent",
            "pull",
            "acme/reviewer",
            "--harness",
            "claude-code",
            "--dir",
            str(target),
            "--no-prompt",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 9
    assert result.stdout == ""
    error = json.loads(result.stderr)["error"]
    partial = error["result"]
    assert partial["partial"] is True
    assert partial["stage"] == "run_setup_commands"
    assert partial["files"] == [{"path": str(target / "agent.md"), "status": "created"}]
    assert partial["setup_commands"] == [{"executable": "broken", "status": "failed", "return_code": 2}]
    assert partial["reports_sessions"] is False
    assert "secret-value" not in result.stderr
    assert "private stderr" not in result.stderr
    boundaries.upsert.assert_not_called()


@pytest.mark.parametrize("failure_stage", ["run_setup_commands", "install_skills"])
@pytest.mark.parametrize("hook_source", ["hooks_config", "agent_profile"])
def test_partial_pull_discloses_session_hooks_on_failure(
    pull_app_boundary: typer.Typer,
    pull_app: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
    hook_source: str,
) -> None:
    hook_command = "python3 -m observal_cli.hooks.session_push"
    if hook_source == "hooks_config":
        hook_path = "hooks.json"
        snippet = {"hooks_config": {"path": hook_path, "content": {"hooks": {"stop": [{"command": hook_command}]}}}}
    else:
        hook_path = "agent.md"
        snippet = {
            "agent_profile": {
                "path": hook_path,
                "content": f"---\nhooks:\n  stop:\n    - command: {hook_command}\n---\nAgent profile\n",
            }
        }
    if failure_stage == "run_setup_commands":
        snippet["mcp_setup_commands"] = [["broken", "--token", "secret-value"]]
        monkeypatch.setattr(
            cmd_pull.subprocess,
            "run",
            MagicMock(return_value=subprocess.CompletedProcess(["broken"], 2, "", "private stderr")),
        )
    else:
        snippet["skill_components"] = [{"name": "broken-skill", "skill_md_content": "content"}]
        boundaries.direct_install.side_effect = OSError("private filesystem detail")
    boundaries.post.return_value = {"config_snippet": snippet}
    target = tmp_path / "json-project"

    json_result = _invoke(pull_app_boundary, target, "--output", "json")
    assert json_result.exit_code == 9
    error = json.loads(json_result.stderr)["error"]
    assert error["result"]["stage"] == failure_stage
    assert error["result"]["reports_sessions"] is True
    assert (target / hook_path).is_file()
    assert "secret-value" not in json_result.stderr
    assert "private stderr" not in json_result.stderr

    human_result = _invoke(pull_app, tmp_path / "human-project")
    assert human_result.exit_code == 9
    assert "Telemetry:" in human_result.output
    assert "session hooks are present" in human_result.output
    assert "may send prompts" in human_result.output
    assert (tmp_path / "human-project" / hook_path).is_file()


def test_partial_pull_does_not_claim_unwritten_profile_hooks(
    pull_app_boundary: typer.Typer, boundaries: SimpleNamespace, tmp_path: Path
) -> None:
    target = tmp_path / "project"
    target.mkdir()
    (target / "hooks.json").write_text("not valid JSON")
    boundaries.post.return_value = {
        "config_snippet": {
            "mcp_config": {"path": "mcp.json", "content": {"mcpServers": {"example": {"command": "echo"}}}},
            "hooks_config": {"path": "hooks.json", "content": {"hooks": {}}, "merge": True},
            "agent_profile": {
                "path": "agent.md",
                "content": "---\nhooks:\n  stop:\n    - command: python3 -m observal_cli.hooks.session_push\n---\n",
            },
        }
    }

    result = _invoke(pull_app_boundary, target, "--output", "json")
    assert result.exit_code == 6
    partial = json.loads(result.stderr)["error"]["result"]
    assert partial["stage"] == "write_files"
    assert partial["reports_sessions"] is False
    assert (target / "mcp.json").is_file()
    assert not (target / "agent.md").exists()


def test_earlier_mcp_write_failure_still_reports_existing_session_hooks(
    pull_app_boundary: typer.Typer, pull_app: typer.Typer, boundaries: SimpleNamespace, tmp_path: Path
) -> None:
    command = "python3 -m observal_cli.hooks.session_push"
    snippet = {
        "mcp_config": {"path": "mcp.json", "content": {"mcpServers": {"example": {"command": "echo"}}}, "merge": True},
        "hooks_config": {"path": "hooks.json", "content": {"hooks": {"start": [{"command": "echo ok"}]}}},
    }
    boundaries.post.return_value = {"config_snippet": snippet}

    def project(name: str) -> Path:
        target = tmp_path / name
        target.mkdir()
        (target / "mcp.json").write_text("not valid JSON")
        (target / "hooks.json").write_text(json.dumps({"hooks": {"stop": [{"command": command}]}}))
        return target

    result = _invoke(pull_app_boundary, project("json-project"), "--output", "json")
    assert result.exit_code != 0
    partial = json.loads(result.stderr)["error"]["result"]
    assert partial["reports_sessions"] is True
    assert not any(item["path"].endswith("hooks.json") for item in partial["files"])

    human = _invoke(pull_app, project("human-project"))
    assert human.exit_code != 0
    assert "Telemetry:" in human.output


def test_failed_pull_reports_sessions_from_adapter_telemetry_outside_pull(
    pull_app_boundary: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An extension that reports sessions stays active even when the pull fails before writing."""
    target = tmp_path / "project"
    target.mkdir()
    boundaries.adapter.reports_sessions_outside_pull.return_value = True
    boundaries.post.return_value = {
        "config_snippet": {"mcp_config": {"path": "mcp.json", "content": {"mcpServers": {}}}}
    }

    def fail_write(path: Path, content: str) -> None:
        raise OSError("synthetic write failure")

    monkeypatch.setattr(cmd_pull, "_atomic_write_text", fail_write)

    result = _invoke(pull_app_boundary, target, "--output", "json")
    assert result.exit_code == 9
    assert json.loads(result.stderr)["error"]["result"]["reports_sessions"] is True


@pytest.mark.parametrize("existing_hook", [True, False])
@pytest.mark.parametrize("preceding_file", [True, False])
def test_failed_hook_write_reports_only_existing_session_hooks(
    pull_app_boundary: typer.Typer,
    pull_app: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing_hook: bool,
    preceding_file: bool,
) -> None:
    target = tmp_path / "project"
    target.mkdir()
    command = "python3 -m observal_cli.hooks.session_push"
    original = {"hooks": {"stop": [{"command": command if existing_hook else "echo ok"}]}}
    hook_path = target / "hooks.json"
    hook_path.write_text(json.dumps(original))
    snippet = {
        "hooks_config": {
            "path": "hooks.json",
            "content": {"hooks": {"start": [{"command": "echo ok" if existing_hook else command}]}},
            "merge": True,
        }
    }
    if preceding_file:
        snippet["mcp_config"] = {"path": "mcp.json", "content": {"mcpServers": {"example": {"command": "echo"}}}}
    boundaries.post.return_value = {"config_snippet": snippet}
    atomic_write = cmd_pull._atomic_write_text

    def fail_hook_write(path: Path, content: str) -> None:
        if path.name == "hooks.json":
            raise OSError("synthetic write failure")
        atomic_write(path, content)

    monkeypatch.setattr(cmd_pull, "_atomic_write_text", fail_hook_write)

    result = _invoke(pull_app_boundary, target, "--output", "json")
    assert result.exit_code == 9
    partial = json.loads(result.stderr)["error"]["result"]
    assert partial["stage"] == "write_files"
    assert partial["partial"] is preceding_file
    assert partial["failed_path"] == str(hook_path)
    expected_files = [{"path": str(target / "mcp.json"), "status": "created"}] if preceding_file else []
    assert partial["files"] == expected_files
    assert partial["reports_sessions"] is existing_hook
    assert json.loads(hook_path.read_text()) == original
    assert "synthetic write failure" not in result.stderr

    human_target = tmp_path / "human-project"
    human_target.mkdir()
    (human_target / "hooks.json").write_text(json.dumps(original))
    human = _invoke(pull_app, human_target)
    assert human.exit_code == 9
    assert ("Telemetry:" in human.output) is existing_hook


def test_pull_lockfile_failure_is_not_reported_as_success(
    pull_app: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
) -> None:
    target = tmp_path / "project"
    boundaries.get.return_value = {"mcp_links": [], "component_links": []}
    boundaries.upsert.side_effect = OSError("lock unavailable")
    boundaries.snapshot.side_effect = RuntimeError("snapshot unavailable")
    boundaries.post.return_value = {"config_snippet": {"agent_profile": {"path": "agent.md", "content": "agent\n"}}}

    result = _invoke(pull_app, target, reference="name-only")

    assert result.exit_code == 9
    assert "installation tracking failed" in result.output
    assert (target / "agent.md").read_text() == "agent\n"
    boundaries.local_name.assert_called_once_with(
        "claude-code",
        "agent",
        "",
        "agent",
        scope="project",
        directory=str(target.resolve()),
    )
    boundaries.snapshot.assert_not_called()
    boundaries.adapter.persist_active_agent.assert_not_called()
    boundaries.emit.assert_not_called()


def test_pull_json_lockfile_failure_reports_tracking_state(
    pull_app_boundary: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
) -> None:
    target = tmp_path / "project"
    boundaries.upsert.side_effect = OSError("private lock detail")

    result = RUNNER.invoke(
        pull_app_boundary,
        [
            "agent",
            "pull",
            "acme/reviewer",
            "--harness",
            "claude-code",
            "--dir",
            str(target),
            "--no-prompt",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 9
    assert result.stdout == ""
    partial = json.loads(result.stderr)["error"]["result"]
    assert partial["stage"] == "update_lockfile"
    assert partial["installation_tracked"] is False
    assert partial["active_agent_persisted"] is False
    assert partial["partial"] is True
    assert "private lock detail" not in result.stderr
    boundaries.adapter.persist_active_agent.assert_not_called()


def test_pull_json_project_lock_failure_reports_tracking_state(
    pull_app_boundary: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "project"
    monkeypatch.setattr(project_lock, "record_agent", MagicMock(side_effect=OSError("private project lock detail")))

    result = RUNNER.invoke(
        pull_app_boundary,
        [
            "agent",
            "pull",
            "acme/reviewer",
            "--harness",
            "claude-code",
            "--dir",
            str(target),
            "--no-prompt",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 9
    assert result.stdout == ""
    partial = json.loads(result.stderr)["error"]["result"]
    assert partial["stage"] == "update_project_lock"
    assert partial["installation_tracked"] is True
    assert partial["active_agent_persisted"] is False
    assert partial["partial"] is True
    assert "private project lock detail" not in result.stderr
    boundaries.adapter.persist_active_agent.assert_not_called()


@pytest.mark.parametrize(
    ("snippet", "message", "metadata_written"),
    [
        ({}, "empty agent configuration", False),
        ({"scope": "project", "mcp_config": {"content": {}}}, "no writable files", False),
    ],
)
def test_pull_rejects_empty_or_unsupported_snippets(
    pull_app: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
    snippet: dict,
    message: str,
    metadata_written: bool,
) -> None:
    boundaries.post.return_value = {"config_snippet": snippet}

    result = _invoke(pull_app, tmp_path / "project")

    assert result.exit_code == 9
    assert message.lower() in result.output.lower()
    assert boundaries.upsert.called is metadata_written
    assert boundaries.emit.called is metadata_written


def test_pull_required_and_unknown_harness_validation_stops_before_http(
    pull_app: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
) -> None:
    missing = RUNNER.invoke(pull_app, ["agent", "pull", "agent-id"])
    assert missing.exit_code == 2
    assert "Missing option" in missing.output
    boundaries.resolve.assert_not_called()

    boundaries.get_adapter.side_effect = KeyError("unknown harness")
    unknown = _invoke(pull_app, tmp_path / "project", harness="unknown")
    assert unknown.exit_code == 7
    assert "Unknown harness" in unknown.output
    boundaries.get.assert_not_called()
    boundaries.post.assert_not_called()


def test_pull_server_scope_validation_failure_leaves_filesystem_clean(
    pull_app: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
) -> None:
    target = tmp_path / "project"
    target.mkdir()
    seen = {}

    def reject_scope(_path: str, body: dict):
        seen.update(body)
        cmd_pull.rprint("[red]Invalid scope: workspace[/red]")
        raise typer.Exit(1)

    boundaries.post.side_effect = reject_scope

    result = _invoke(pull_app, target, "--scope", "workspace")

    assert result.exit_code == 7
    assert "Unknown install scope" in result.output
    assert seen == {}
    assert list(target.iterdir()) == []
    boundaries.upsert.assert_not_called()


def test_pull_path_traversal_stops_before_lockfile_update(
    pull_app: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
) -> None:
    target = tmp_path / "project"
    boundaries.post.return_value = {
        "config_snippet": {"hook_files": [{"path": "../../escape.sh", "content": "unsafe"}]}
    }

    result = _invoke(pull_app, target)

    assert result.exit_code == 7
    assert "escapes the target directory" in result.output
    assert not (tmp_path.parent / "escape.sh").exists()
    boundaries.upsert.assert_not_called()
    boundaries.emit.assert_not_called()


def test_parse_assignments_rejects_missing_names_and_values():
    assert cmd_pull._parse_assignments(["TOKEN='secret'"], "environment variable") == {"TOKEN": "secret"}
    for invalid in ("TOKEN", "=secret", "TOKEN="):
        with pytest.raises(typer.Exit) as error:
            cmd_pull._parse_assignments([invalid], "environment variable")
        assert error.value.exit_code == 7


def test_toml_merge_is_idempotent_for_existing_server(tmp_path: Path):
    path = tmp_path / "config.toml"
    first = {"mcp_servers": {"github": {"command": "old"}}}
    second = {"mcp_servers": {"github": {"command": "new"}}}

    cmd_pull._write_file(path, first)
    cmd_pull._write_file(path, second, merge_mcp=True)
    cmd_pull._write_file(path, second, merge_mcp=True)

    assert tomllib.loads(path.read_text()) == second
    assert path.read_text().count("[mcp_servers.github]") == 1


def test_pull_json_returns_stable_file_and_setup_result_without_secrets(
    pull_app: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
):
    target = tmp_path / "project"
    boundaries.post.return_value = {
        "config_snippet": {
            "agent_profile": {"path": "agent.md", "content": "agent\n"},
            "mcp_setup_commands": [[sys.executable, "-c", "pass"]],
        },
        "warnings": ["server warning"],
    }

    result = _invoke(
        pull_app,
        target,
        "--env",
        "TOKEN=secret-value",
        "--header",
        "Authorization=Bearer secret-value",
        "--output",
        "json",
    )

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    payload = json.loads(result.output)
    assert payload["agent"]["qualified_name"] == "acme/reviewer"
    assert payload["harness"] == "claude-code"
    assert payload["dry_run"] is False
    assert payload["files"] == [{"path": str(target.resolve() / "agent.md"), "status": "created"}]
    assert payload["warnings"] == ["server warning"]
    assert payload["setup_commands"][0]["status"] == "completed"
    assert "secret-value" not in result.output
    boundaries.upsert.assert_called_once()


def test_pull_json_dry_run_has_no_write_or_metadata_side_effects(
    pull_app: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
):
    target = tmp_path / "project"
    boundaries.post.return_value = {
        "config_snippet": {
            "agent_profile": {"path": "agent.md", "content": "agent\n"},
            "mcp_setup_commands": [["claude", "mcp", "add", "server"]],
        }
    }

    result = _invoke(pull_app, target, "--dry-run", "--output", "json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["files"][0]["status"] == "would write"
    assert payload["setup_commands"][0]["status"] == "would_run"
    assert not target.exists()
    boundaries.upsert.assert_not_called()
    boundaries.emit.assert_not_called()


def test_pull_setup_failure_does_not_record_installation(
    pull_app: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / "project"
    boundaries.post.return_value = {
        "config_snippet": {
            "agent_profile": {"path": "agent.md", "content": "agent\n"},
            "mcp_setup_commands": [["broken", "mcp", "add"]],
        }
    }
    monkeypatch.setattr(
        cmd_pull.subprocess,
        "run",
        MagicMock(return_value=subprocess.CompletedProcess(["broken"], 2, "", "failed")),
    )

    result = _invoke(pull_app, target)

    assert result.exit_code == 9
    assert (target / "agent.md").is_file()
    boundaries.upsert.assert_not_called()
    boundaries.emit.assert_not_called()


def test_pull_resets_then_registers_mcp_servers_in_target_without_echoing_values(
    pull_app: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / "project"
    remote = ["claude", "mcp", "add", "--transport", "http", "linear", "https://x.test/mcp", "-H", "Auth: tok-1"]
    stdio = ["claude", "mcp", "add", "srv", "-e", "API_KEY=key-1", "--", "npx", "srv"]
    boundaries.post.return_value = {
        "config_snippet": {
            "agent_profile": {"path": "agent.md", "content": "agent\n"},
            "mcp_reset_commands": [["claude", "mcp", "remove", "linear"], ["missing-binary"]],
            "mcp_setup_commands": [remote, stdio],
        }
    }
    calls: list[tuple[list[str], object]] = []

    def run(command, **kwargs):
        calls.append((command, kwargs.get("cwd")))
        if command[0] == "missing-binary":
            raise FileNotFoundError(command[0])
        # Nothing registered yet, so the reset fails; that must not fail the pull.
        return subprocess.CompletedProcess(command, 1 if "remove" in command else 0, "", "")

    monkeypatch.setattr(cmd_pull.subprocess, "run", run)

    result = _invoke(pull_app, target, "--output", "json")

    assert result.exit_code == 0, result.output
    assert calls == [
        (["claude", "mcp", "remove", "linear"], target.resolve()),
        (["missing-binary"], target.resolve()),
        ([*remote[:3], "--scope", "project", *remote[3:]], target.resolve()),
        ([*stdio[:3], "--scope", "project", *stdio[3:]], target.resolve()),
    ]
    shown = [setup["command"] for setup in json.loads(result.output)["setup_commands"]]
    assert shown == [
        [*remote[:-1], "Auth: <secret>"],
        ["claude", "mcp", "add", "srv", "-e", "API_KEY=<secret>", "--", "npx", "srv"],
    ]
    assert "tok-1" not in result.output and "key-1" not in result.output
    boundaries.upsert.assert_called_once()


def test_pull_snapshot_failure_is_visible_warning(
    pull_app: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
):
    target = tmp_path / "project"
    boundaries.snapshot.side_effect = RuntimeError("snapshot failed")
    boundaries.post.return_value = {"config_snippet": {"agent_profile": {"path": "agent.md", "content": "agent\n"}}}

    result = _invoke(pull_app, target, "--output", "json")

    assert result.exit_code == 0, result.output
    assert any(
        "Local layer snapshot could not be refreshed" in warning for warning in json.loads(result.output)["warnings"]
    )
    boundaries.upsert.assert_called_once()


def test_pull_rejects_malformed_existing_config_without_overwrite(
    pull_app: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
):
    target = tmp_path / "project"
    path = target / "mcp.json"
    path.parent.mkdir(parents=True)
    path.write_text("not-json")
    boundaries.post.return_value = {
        "config_snippet": {"mcp_config": {"path": "mcp.json", "content": {"mcpServers": {"new": {}}}}}
    }

    result = _invoke(pull_app, target)

    assert result.exit_code == 6
    assert path.read_text() == "not-json"
    boundaries.upsert.assert_not_called()


def test_pull_json_write_failure_reports_failed_path(
    pull_app_boundary: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
) -> None:
    target = tmp_path / "project"
    path = target / "mcp.json"
    path.parent.mkdir(parents=True)
    path.write_text("not-json")
    boundaries.post.return_value = {
        "config_snippet": {"mcp_config": {"path": "mcp.json", "content": {"mcpServers": {"new": {}}}}}
    }

    result = RUNNER.invoke(
        pull_app_boundary,
        [
            "agent",
            "pull",
            "acme/reviewer",
            "--harness",
            "claude-code",
            "--dir",
            str(target),
            "--no-prompt",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 6
    assert result.stdout == ""
    state = json.loads(result.stderr)["error"]["result"]
    assert state["stage"] == "write_files"
    assert state["failed_path"] == str(path)
    assert state["partial"] is False
    assert path.read_text() == "not-json"
    boundaries.upsert.assert_not_called()


def test_pull_rejects_irrelevant_model_refresh_before_http(
    pull_app: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
):
    result = _invoke(pull_app, tmp_path / "project", "--refresh-models")

    assert result.exit_code == 7
    assert "requires the interactive model picker" in result.output
    boundaries.resolve.assert_not_called()
    boundaries.get.assert_not_called()


@pytest.mark.parametrize(
    ("options", "environment", "strict"),
    [
        ((), {}, False),
        (("--strict",), {}, True),
        ((), {"OBSERVAL_STRICT": "1"}, True),
        (("--no-strict",), {"OBSERVAL_STRICT": "true"}, False),
    ],
)
def test_pull_strict_flag_wins_over_the_environment(
    pull_app: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    options: tuple[str, ...],
    environment: dict[str, str],
    strict: bool,
) -> None:
    monkeypatch.delenv("OBSERVAL_STRICT", raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    boundaries.post.return_value = {
        "config_snippet": {"agent_profile": {"path": "agent.md", "content": "agent\n"}},
        "lock": {"status": "locked", "digest": None, "components": [], "problems": []},
    }

    result = _invoke(pull_app, tmp_path / "project", *options)

    assert result.exit_code == 0, result.output
    body = boundaries.post.call_args.args[1]
    assert body.get("strict", False) is strict


def test_strict_refuses_a_server_that_reports_no_lock(
    pull_app: typer.Typer, boundaries: SimpleNamespace, tmp_path: Path
):
    """A server from before component locks ignores `strict`; nothing was checked."""
    target = tmp_path / "project"

    result = _invoke(pull_app, target, "--strict")

    assert result.exit_code == 10
    assert "does not report component locks" in result.output
    assert not (target / "agent.md").exists()
    assert not (target / "observal.lock").exists()


def test_pull_json_reports_the_installed_version_and_lock(
    pull_app: typer.Typer,
    boundaries: SimpleNamespace,
    tmp_path: Path,
) -> None:
    lock = {
        "status": "partial",
        "digest": "sha256:" + "d" * 64,
        "problems": ["mcp 'legacy' is not locked"],
        "components": [
            {
                "type": "mcp",
                "id": "mcp-1",
                "qualified_name": "acme/legacy",
                "version": "2.0.0",
                "source": "fallback-latest",
            }
        ],
    }
    boundaries.post.return_value = {
        "config_snippet": {"agent_profile": {"path": "agent.md", "content": "agent\n"}},
        "version": "1.0.0",
        "lock": lock,
    }

    result = _invoke(pull_app, tmp_path / "project", "--output", "json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["agent"]["version"] == "1.0.0"
    assert payload["lock"]["status"] == "partial"
    assert payload["lock"]["problems"] == ["mcp 'legacy' is not locked"]
    assert payload["lock"]["components"][0]["source"] == "fallback-latest"
    assert boundaries.upsert.call_args.kwargs["version"] == "1.0.0"
    assert boundaries.upsert.call_args.kwargs["lock_status"] == "partial"
    boundaries.adapter.persist_active_agent.assert_called_once_with(
        "agent-uuid", "reviewer", "1.0.0", is_user_scope=False
    )


def test_component_conflicts_match_components_by_registry_id(monkeypatch: pytest.MonkeyPatch) -> None:
    import observal_cli.lockfile as lockfile

    registry = {
        "harnesses": {
            "cursor": {
                "agents": [
                    {
                        "name": "older-agent",
                        "components": [
                            {"id": "same-id", "name": "Renamed MCP", "version": "1.0.0"},
                            {"id": "other-id", "name": "shared", "version": "1.0.0"},
                        ],
                    }
                ]
            }
        }
    }
    monkeypatch.setattr(lockfile, "read_registry_lockfile", MagicMock(return_value=({}, registry)))

    conflicts = cmd_pull._component_conflicts(
        "cursor",
        "incoming",
        [{"type": "mcp", "id": "same-id", "name": "github", "version": "2.0.0"}],
    )

    assert conflicts == ["mcp github: v2.0.0 (this agent) vs v1.0.0 (from older-agent)"]


def test_mcp_spec_reads_the_pinned_version_and_falls_back_to_the_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    from observal_cli.errors import CliError, ErrorCategory

    def get(path: str):
        if path == "/api/v1/mcps/mcp-1/versions/1.0.0":
            return {"environment_variables": [{"name": "PINNED"}]}
        if path == "/api/v1/mcps/mcp-2/versions/1.0.0":
            raise CliError(ErrorCategory.NOT_FOUND, "Version not found", operation="read", resource=path)
        return {"environment_variables": [{"name": "LATEST"}]}

    fetch = MagicMock(side_effect=get)
    monkeypatch.setattr(cmd_pull.client, "get", fetch)
    cache: dict = {}

    pinned = cmd_pull._mcp_spec("mcp-1", "1.0.0", cache)
    fallback = cmd_pull._mcp_spec("mcp-2", "1.0.0", cache)
    cached = cmd_pull._mcp_spec("mcp-1", "1.0.0", cache)

    assert pinned == cached == {"environment_variables": [{"name": "PINNED"}]}
    assert fallback == {"environment_variables": [{"name": "LATEST"}]}
    assert [c.args[0] for c in fetch.call_args_list] == [
        "/api/v1/mcps/mcp-1/versions/1.0.0",
        "/api/v1/mcps/mcp-2/versions/1.0.0",
        "/api/v1/mcps/mcp-2",
    ]


# ── Pinned pulls: observal.lock, the local lockfile, --upgrade, --version ─────
# A plain pull keeps the locked agent version. The project lock (committed)
# wins over this machine's lockfile, which wins over the latest approved
# version. --version and --upgrade are the only ways to move.

LOCK_DIGEST = "sha256:" + "a" * 64


def _install_result(version: str, *, digest: str = LOCK_DIGEST) -> dict:
    return {
        "config_snippet": {"agent_profile": {"path": "agent.md", "content": "agent\n"}},
        "version": version,
        "lock": {
            "status": "locked",
            "digest": digest,
            "problems": [],
            "components": [
                {
                    "type": "mcp",
                    "id": "mcp-1",
                    "qualified_name": "acme/github",
                    "version": "1.0.0" if version == "1.0.0" else "2.0.0",
                    "digest": "sha256:" + "b" * 64,
                    "source": "lock",
                }
            ],
        },
    }


@pytest.fixture
def registry(boundaries: SimpleNamespace) -> SimpleNamespace:
    """The latest approved agent version is 1.1.0; 1.0.0 is also approved."""

    def get(path: str):
        if path == "/api/v1/agents/agent-uuid":
            return _agent_detail(version="1.1.0", qualified_name="acme/reviewer")
        if path.startswith("/api/v1/agents/agent-uuid/versions/"):
            return {"version": path.rsplit("/", 1)[1], "components": []}
        raise AssertionError(path)

    boundaries.get.side_effect = get

    def post(_path: str, body: dict):
        return _install_result(body.get("version") or "1.1.0")

    boundaries.post.side_effect = post
    return boundaries


def _lock(directory: Path, version: str, digest: str = LOCK_DIGEST) -> None:
    project_lock.record_agent(
        directory,
        "acme/reviewer",
        project_lock.agent_entry(agent_id="agent-uuid", version=version, lock_digest=digest, components=[]),
    )


def _sent_version(boundaries: SimpleNamespace) -> str | None:
    return boundaries.post.call_args.args[1].get("version")


def test_first_pull_installs_latest_and_writes_the_project_lock(pull_app, registry, tmp_path):
    target = tmp_path / "project"

    result = _invoke(pull_app, target)

    assert result.exit_code == 0, result.output
    assert _sent_version(registry) is None
    locked = json.loads((target / "observal.lock").read_text())
    assert locked["lock_version"] == 1
    assert locked["agents"]["acme/reviewer"]["version"] == "1.1.0"
    assert locked["agents"]["acme/reviewer"]["lock_digest"] == LOCK_DIGEST
    assert locked["agents"]["acme/reviewer"]["components"] == [
        {"type": "mcp", "qualified_name": "acme/github", "version": "2.0.0", "digest": "sha256:" + "b" * 64}
    ]
    assert "latest approved" in result.output


def test_plain_pull_keeps_the_version_in_the_project_lock(pull_app, registry, tmp_path):
    target = tmp_path / "project"
    _lock(target, "1.0.0")

    result = _invoke(pull_app, target, "--output", "json")

    assert result.exit_code == 0, result.output
    assert _sent_version(registry) == "1.0.0"
    payload = json.loads(result.output)
    assert payload["agent"]["version"] == "1.0.0"
    assert payload["agent"]["latest_version"] == "1.1.0"
    assert payload["agent"]["resolved_from"] == "project-lock"
    assert json.loads((target / "observal.lock").read_text())["agents"]["acme/reviewer"]["version"] == "1.0.0"


def test_upgrade_moves_to_the_latest_version_and_rewrites_the_lock(pull_app, registry, tmp_path):
    target = tmp_path / "project"
    _lock(target, "1.0.0")

    result = _invoke(pull_app, target, "--upgrade")

    assert result.exit_code == 0, result.output
    assert _sent_version(registry) is None
    assert json.loads((target / "observal.lock").read_text())["agents"]["acme/reviewer"]["version"] == "1.1.0"


def test_version_moves_the_lock_to_exactly_that_version(pull_app, registry, tmp_path):
    target = tmp_path / "project"
    _lock(target, "1.1.0")

    result = _invoke(pull_app, target, "--version", "1.0.0")

    assert result.exit_code == 0, result.output
    assert _sent_version(registry) == "1.0.0"
    assert json.loads((target / "observal.lock").read_text())["agents"]["acme/reviewer"]["version"] == "1.0.0"


def test_without_a_project_lock_the_installed_version_is_kept(pull_app, registry, tmp_path, monkeypatch):
    import observal_cli.lockfile as lockfile

    target = tmp_path / "project"
    installed = MagicMock(return_value={"id": "agent-uuid", "version": "1.0.0"})
    monkeypatch.setattr(lockfile, "installed_agent", installed)

    result = _invoke(pull_app, target, "--output", "json")

    assert result.exit_code == 0, result.output
    assert _sent_version(registry) == "1.0.0"
    assert json.loads(result.output)["agent"]["resolved_from"] == "installed"
    # Recheck current receipts immediately before the first nonfolder write:
    # a later legacy pull must not erase ownership of a newly installed folder.
    assert installed.call_count == 2
    assert (
        installed.call_args_list
        == [
            call("claude-code", "agent-uuid", scope="project", directory=str(target.resolve())),
        ]
        * 2
    )


def test_nonfolder_agent_pull_refuses_managed_receipt_before_config_writes(
    pull_app_boundary, registry, tmp_path, monkeypatch
):
    import observal_cli.lockfile as lockfile

    target = tmp_path / "project"
    monkeypatch.setattr(
        lockfile,
        "installed_agent",
        MagicMock(
            return_value={
                "id": "agent-uuid",
                "version": "1.0.0",
                "components": [
                    {"type": "skill", "id": "skill-1", "folder_receipt": {"target": str(target / ".pi/skills/review")}},
                ],
            }
        ),
    )
    result = _invoke(pull_app_boundary, target, "--output", "json")
    assert result.exit_code == 6, result.output
    assert "owns verified skill folders" in json.loads(result.stderr)["error"]["message"]
    assert not (target / "observal.lock").exists()
    assert not (target / ".claude").exists()


def test_upgrade_and_version_cannot_be_combined(pull_app, registry, tmp_path):
    result = _invoke(pull_app, tmp_path / "project", "--upgrade", "--version", "1.0.0")

    assert result.exit_code == 7
    registry.post.assert_not_called()


def test_user_scope_and_dry_run_leave_the_project_lock_alone(pull_app, registry, tmp_path):
    locked = tmp_path / "locked"
    _lock(locked, "1.0.0")
    fresh = tmp_path / "fresh"

    user_scope = _invoke(pull_app, locked, "--scope", "user")
    dry_run = _invoke(pull_app, fresh, "--dry-run")

    assert user_scope.exit_code == 0, user_scope.output
    assert dry_run.exit_code == 0, dry_run.output
    # User-scope installs are not tied to a project: latest, and the lock is untouched.
    assert registry.post.call_args_list[0].args[1].get("version") is None
    assert json.loads((locked / "observal.lock").read_text())["agents"]["acme/reviewer"]["version"] == "1.0.0"
    assert not (fresh / "observal.lock").exists()


def test_a_changed_lock_digest_warns_and_strict_refuses_before_writing(pull_app, registry, tmp_path):
    target = tmp_path / "project"
    _lock(target, "1.0.0", digest="sha256:" + "f" * 64)

    warned = _invoke(pull_app, target)
    # A warned install records the digest it installed; the change shows in the lock's diff.
    recorded = json.loads((target / "observal.lock").read_text())["agents"]["acme/reviewer"]["lock_digest"]
    (target / "agent.md").unlink()
    _lock(target, "1.0.0", digest="sha256:" + "f" * 64)
    refused = _invoke(pull_app, target, "--strict")

    assert warned.exit_code == 0, warned.output
    assert "no longer matches the lock digest" in warned.output
    assert recorded == LOCK_DIGEST
    assert refused.exit_code == 6
    assert not (target / "agent.md").exists()


def test_a_stale_locked_version_says_where_it_came_from_and_how_to_move(pull_app, registry, tmp_path):
    from observal_cli.errors import CliError, ErrorCategory

    target = tmp_path / "project"
    _lock(target, "9.9.9")
    detail = registry.get.side_effect

    def get(path: str):
        if path.endswith("/versions/9.9.9"):
            raise CliError(category=ErrorCategory.NOT_FOUND, message="Version not found", operation="Pull agent")
        return detail(path)

    registry.get.side_effect = get

    result = _invoke(pull_app, target)
    with pytest.raises(CliError) as error:
        cmd_pull._locked_version_detail(
            "agent-uuid", "9.9.9", "project-lock", qualified_name="acme/reviewer", directory=target
        )

    assert result.exit_code == 5
    assert "pins agent acme/reviewer to version 9.9.9" in " ".join(result.output.split())
    assert "--upgrade" in error.value.remediation
    registry.post.assert_not_called()


def test_a_renamed_agent_keeps_the_version_its_old_name_locked(pull_app, registry, tmp_path):
    target = tmp_path / "project"
    project_lock.record_agent(
        target,
        "old-team/reviewer",
        project_lock.agent_entry(agent_id="agent-uuid", version="1.0.0", lock_digest=LOCK_DIGEST, components=[]),
    )

    result = _invoke(pull_app, target)

    assert result.exit_code == 0, result.output
    assert _sent_version(registry) == "1.0.0"
    agents = json.loads((target / "observal.lock").read_text())["agents"]
    assert list(agents) == ["acme/reviewer"]
    assert agents["acme/reviewer"]["version"] == "1.0.0"


def test_a_malformed_project_lock_fails_clearly(pull_app, registry, tmp_path):
    target = tmp_path / "project"
    target.mkdir()
    (target / "observal.lock").write_text("{not json")

    result = _invoke(pull_app, target)

    assert result.exit_code == 7
    assert "observal.lock" in result.output
    registry.post.assert_not_called()


def test_project_lock_is_sorted_and_versioned(tmp_path):
    project_lock.record_agent(tmp_path, "zeta/agent", {"id": "z", "version": "1.0.0"})
    project_lock.record_agent(tmp_path, "alpha/agent", {"id": "a", "version": "2.0.0"})

    data = json.loads((tmp_path / "observal.lock").read_text())

    assert list(data["agents"]) == ["alpha/agent", "zeta/agent"]
    assert project_lock.locked_agent(tmp_path, "alpha/agent")["version"] == "2.0.0"
    assert project_lock.locked_agent(tmp_path, "missing/agent") is None
    (tmp_path / "observal.lock").write_text(json.dumps({"lock_version": 99, "agents": {}}))
    with pytest.raises(project_lock.ProjectLockError, match="unsupported lock_version"):
        project_lock.read(tmp_path)


def test_inline_hook_rewrite_survives_a_malformed_entry():
    """A truthy non-dict entry would raise on .get and abort the whole pull."""
    from observal_cli.cmd_pull import _rewrite_kiro_agent_profile

    cleaned = _rewrite_kiro_agent_profile(
        {
            "hooks": {
                "userPromptSubmit": [
                    "a bare string someone hand-edited in",
                    {"command": "python -m observal_cli.hooks.session_push --harness kiro"},
                    {"command": "echo mine"},
                ]
            }
        }
    )

    assert cleaned["hooks"]["userPromptSubmit"][:2] == [
        "a bare string someone hand-edited in",
        {"command": "echo mine"},
    ]


def _folder_install_result() -> dict:
    import base64
    import hashlib

    def entry(path: str, data: bytes) -> dict:
        return {
            "path": path,
            "content": base64.b64encode(data).decode(),
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "mode": "0644",
            "version_id": "ver-1",
        }

    result = _install_result("1.1.0")
    result["config_snippet"]["skill_components"] = [
        {"name": "demo", "path": ".claude/skills/demo/SKILL.md", "bundle_version_id": "ver-1"}
    ]
    result["skill_bundles"] = [
        {
            "listing_id": "skill-1",
            "version_id": "ver-1",
            "digest": "sha256:" + "d" * 64,
            "skill_file_path": ".claude/skills/demo/SKILL.md",
            "files": [entry("SKILL.md", b"---\nname: demo\n---\n")],
        }
    ]
    result["lock"]["components"] = [
        {
            "type": "skill",
            "id": "skill-1",
            "qualified_name": "acme/demo",
            "version": "1.0.0",
            "version_id": "ver-1",
            "digest": "sha256:" + "d" * 64,
            "source": "lock",
        }
    ]
    return result


def test_committed_folder_failure_reports_a_partial_pull_and_rolls_nothing_back(
    pull_app_boundary, registry, tmp_path, monkeypatch
):
    from observal_cli import managed_skill

    target = tmp_path / "project"
    registry.post.side_effect = lambda _path, _body: _folder_install_result()
    plan = [(object(), target / "f", {"target": str(target / "f"), "listing_id": "skill-1"}, {})]
    monkeypatch.setattr(cmd_pull, "_managed_agent_folders", lambda *_a, **_k: plan)

    def committed(*_args, **_kwargs):
        raise managed_skill.ManagedSkillCommittedError(
            "New skill is complete and recorded, but its destination path was substituted afterwards",
            outcome={"action": "install", "target": str(target / "f")},
            remediation="Verify the destination path.",
        )

    def forbidden_rollback(*_args, **_kwargs):
        raise AssertionError("a committed folder must never be rolled back")

    monkeypatch.setattr(managed_skill, "transact", committed)
    monkeypatch.setattr(cmd_pull, "_rollback_managed_agent_state", forbidden_rollback)

    result = _invoke(pull_app_boundary, target, "--output", "json")

    assert result.exit_code == 6, result.output
    error = json.loads(result.stderr)["error"]
    assert "partially applied" in error["message"]
    assert error["result"]["installation_tracked"] is True
    assert error["result"]["committed_folders"] == [str(target / "f")]


def test_display_setup_command_redacts_url_credentials():
    from observal_cli.cmd_pull import _display_setup_command

    shown = _display_setup_command(["claude", "mcp", "add", "x", "https://u:p@h.example/mcp?token=abc"])
    assert shown[-1] == "https://h.example/mcp?<redacted>"


def test_repull_restores_deleted_project_skill_folder(pull_app_boundary, registry, tmp_path, monkeypatch):
    import shutil

    from observal_cli import lockfile

    config = tmp_path / "config"
    monkeypatch.setattr(lockfile, "CONFIG_DIR", config)
    monkeypatch.setattr(lockfile, "LOCKFILE_PATH", config / "lockfile.json")
    monkeypatch.setattr(lockfile, "_LOCKFILE_LOCK", config / "lockfile.lock")
    registry.post.side_effect = lambda _path, _body: _folder_install_result()
    target = tmp_path / "project"
    first = _invoke(pull_app_boundary, target, "--output", "json")
    assert first.exit_code == 0, first.output
    skill_dir = target / ".claude/skills/demo"
    assert (skill_dir / "SKILL.md").exists()
    original_lock = json.loads((config / "lockfile.json").read_text())["registries"]
    original_receipt = original_lock["https://public.observal.io"]["harnesses"]["claude-code"]["agents"][0][
        "components"
    ][0]["folder_receipt"]
    shutil.rmtree(target / ".claude")
    assert not skill_dir.exists()

    second = _invoke(pull_app_boundary, target, "--output", "json")
    assert second.exit_code == 0, second.output
    assert (skill_dir / "SKILL.md").read_text() == "---\nname: demo\n---\n"
    updated = json.loads((config / "lockfile.json").read_text())["registries"]
    assert (
        updated["https://public.observal.io"]["harnesses"]["claude-code"]["agents"][0]["components"][0][
            "folder_receipt"
        ]
        == original_receipt
    )
    assert json.loads((target / "observal.lock").read_text())["agents"]["acme/reviewer"]["version"] == "1.1.0"


def test_kiro_agent_pull_installs_skill_folder(pull_app_boundary, registry, tmp_path, monkeypatch):
    from observal_cli import lockfile

    config = tmp_path / "config"
    monkeypatch.setattr(lockfile, "CONFIG_DIR", config)
    monkeypatch.setattr(lockfile, "LOCKFILE_PATH", config / "lockfile.json")
    monkeypatch.setattr(lockfile, "_LOCKFILE_LOCK", config / "lockfile.lock")

    def install(_path, _body):
        result = _folder_install_result()
        result["config_snippet"]["skill_components"][0]["path"] = ".kiro/skills/demo/SKILL.md"
        result["skill_bundles"][0]["skill_file_path"] = ".kiro/skills/demo/SKILL.md"
        return result

    registry.post.side_effect = install
    target = tmp_path / "project"
    pulled = _invoke(pull_app_boundary, target, "--output", "json", harness="kiro")
    assert pulled.exit_code == 0, pulled.output
    assert (target / ".kiro/skills/demo/SKILL.md").read_text() == "---\nname: demo\n---\n"


def test_pull_refuses_an_unsupported_harness_before_prompting_or_installing(
    pull_app_boundary: typer.Typer, boundaries: SimpleNamespace, tmp_path: Path
) -> None:
    # OBS-1421: the pull used to install for any harness and leave a partial install.
    boundaries.get.return_value = _agent_detail(supported_harnesses=["claude-code", "kiro"])
    result = _invoke(pull_app_boundary, tmp_path, "--output", "json", harness="pi")
    assert result.exit_code != 0 and result.stdout == ""
    error = json.loads(result.stderr)["error"]
    assert error["message"] == "Agent acme/reviewer 1.4.0 does not support pi; it supports claude-code, kiro."
    assert "--allow-unsupported-harness" in error["remediation"]
    boundaries.post.assert_not_called()


def test_pull_allow_unsupported_harness_asks_the_server_to_install_anyway(
    pull_app: typer.Typer, boundaries: SimpleNamespace, tmp_path: Path
) -> None:
    boundaries.get.return_value = _agent_detail(supported_harnesses=["claude-code"])
    result = _invoke(pull_app, tmp_path, "--output", "json", "--allow-unsupported-harness", harness="pi")
    assert result.exit_code == 0, result.output
    assert boundaries.post.call_args.args[1]["allow_unsupported_harness"] is True


@pytest.mark.parametrize("supported", [["claude-code"], []])
def test_pull_for_a_supported_or_unrestricted_harness_sends_no_override(
    pull_app: typer.Typer, boundaries: SimpleNamespace, tmp_path: Path, supported: list[str]
) -> None:
    boundaries.get.return_value = _agent_detail(supported_harnesses=supported)
    result = _invoke(pull_app, tmp_path, "--output", "json")
    assert result.exit_code == 0, result.output
    assert "allow_unsupported_harness" not in boundaries.post.call_args.args[1]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (
            ["-e", "TOKEN=DUMMY_SECRET_FOR_TEST", "--", "srv", "--token=DUMMY_SECRET_FOR_TEST"],
            ["-e", "TOKEN=<secret>", "--", "srv", "<secret>"],
        ),
        (["--", "srv", "DUMMY_SECRET_FOR_TEST/DUMMY_SECRET_FOR_TEST"], ["--", "srv", "<secret>"]),
        (["https://host.test/DUMMY_SECRET_FOR_TEST?key=x"], ["<secret>"]),
        (["-H", "Auth: DUMMY_SECRET_FOR_TEST", "Bearer DUMMY_SECRET_FOR_TEST"], ["-H", "Auth: <secret>", "<secret>"]),
        (["https://user:password@host.test/mcp?token=x"], ["https://host.test/mcp?<redacted>"]),
        (
            ["--", "x", "--token=DUMMY_LITERAL_FOR_TEST", "--password", "DUMMY_LITERAL_FOR_TEST"],
            ["--", "x", "--token=<secret>", "--password", "<secret>"],
        ),
    ],
)
def test_display_setup_command_dummy_matrix(argv, expected):
    original = list(argv)
    assert cmd_pull._display_setup_command(argv, secret_values={"DUMMY_SECRET_FOR_TEST"}) == expected
    assert argv == original


def test_reporting_preserves_rich_tags_and_masks_escaped_dummy_labels():
    secret = "DUMMY_SECRET_FOR_TEST"
    token = cmd_pull._pull_secrets.set({secret, "e"})
    try:
        shown = cmd_pull._safe_markup(f"[green]\\[{secret}][/green]")
        assert secret not in shown
        assert "<secret>" in shown
        # A short secret in an authored tag requires unstyled escaped output.
        assert "\\[" in shown
        assert cmd_pull._safe_text(cmd_pull._safe_text(f"x-{secret}")) == "x-<secret>"
    finally:
        cmd_pull._pull_secrets.reset(token)


def test_rich_bracketed_warning_does_not_bypass_masking():
    secret = "DUMMY_SECRET_FOR_TEST"
    token = cmd_pull._pull_secrets.set({secret})
    try:
        assert secret not in cmd_pull._safe_markup(f"[yellow]warning [{secret}][/yellow]")
        assert "[yellow]" in cmd_pull._safe_markup(f"[yellow]warning [{secret}][/yellow]")
    finally:
        cmd_pull._pull_secrets.reset(token)


@pytest.mark.parametrize("secret", ["a", "lock", "name"])
def test_short_secret_preserves_json_field_names_and_masks_dynamic_keys(secret):
    token = cmd_pull._pull_secrets.set({secret})
    try:
        result = cmd_pull._safe_data(
            {
                "lock": {"status": secret, "components": [{"name": secret}]},
                "agent": {"qualified_name": secret},
                "warnings": [{f"dynamic-{secret}": secret}],
            },
            "output",
        )
        assert set(result) == {"lock", "agent", "warnings"}
        assert set(result["lock"]) == {"status", "components"}
        assert result["lock"]["components"][0] == {"name": "<secret>"}
        assert result["agent"] == {"qualified_name": "<secret>"}
        assert result["warnings"] == [{cmd_pull._safe_text(f"dynamic-{secret}"): "<secret>"}]
        error_result = cmd_pull._safe_data(
            {"inputs": [{"kind": secret, "name": secret, "component": secret}], f"dynamic-{secret}": secret},
            "result",
        )
        assert error_result["inputs"] == [{"kind": "<secret>", "name": "<secret>", "component": "<secret>"}]
        assert error_result[cmd_pull._safe_text(f"dynamic-{secret}")] == "<secret>"
    finally:
        cmd_pull._pull_secrets.reset(token)


def test_display_setup_command_masks_flag_key_containing_supplied_value():
    command = ["claude", "-e", "a=a", "-H", "Header-a: a", "--", "server"]
    assert cmd_pull._display_setup_command(command, secret_values={"a"}) == [
        "<secret>",
        "-e",
        "<secret>=<secret>",
        "-H",
        "He<secret>der-<secret>: <secret>",
        "--",
        "server",
    ]
    assert command[2] == "a=a"


def test_display_setup_command_short_and_overlapping_values():
    argv = ["executable", "--", "prefix-DUMMY", "DUMMY-DUMMY", "one-a-char"]
    assert cmd_pull._display_setup_command(argv, secret_values={"DUMMY", "a"}) == [
        "<secret>",
        "--",
        "<secret>",
        "<secret>",
        "<secret>",
    ]


@pytest.mark.parametrize("mode", ["table", "json", "dry-table", "dry-json"])
def test_pull_dummy_credential_keeps_real_setup_argv_and_post(pull_app, boundaries, tmp_path, monkeypatch, mode):
    secret = "DUMMY_SECRET_FOR_TEST"
    target = tmp_path / "project"
    command = ["claude", "mcp", "add", "srv", "-e", f"TOKEN={secret}", "--", "srv", f"--token={secret}"]
    boundaries.get.return_value = _agent_detail(mcp_links=[{"mcp_listing_id": "mcp-id", "mcp_name": "srv"}])
    boundaries.get.side_effect = lambda path: (
        {
            "name": "srv",
            "environment_variables": [{"name": "TOKEN", "required": True}],
            "headers": [{"name": "Authorization", "required": True}],
        }
        if path.endswith("/mcps/mcp-id")
        else boundaries.get.return_value
    )
    boundaries.post.return_value = {
        "config_snippet": {
            "agent_profile": {"path": "agent.md", "content": "agent\n"},
            "mcp_setup_commands": [command],
        },
        "warnings": [f"warning {secret}"],
        "lock": {"problems": [f"problem {secret}"], "status": "partial"},
    }
    run = MagicMock(return_value=subprocess.CompletedProcess(command, 0, "", ""))
    monkeypatch.setattr(cmd_pull.subprocess, "run", run)
    options = ["--env", f"TOKEN={secret}", "--header", f"Authorization={secret}"]
    if "json" in mode:
        options += ["--output", "json"]
    if "dry" in mode:
        options += ["--dry-run"]
    result = _invoke(pull_app, target, *options)
    assert result.exit_code == 0, result.output
    assert secret not in result.output
    assert "<secret>" in result.output
    assert boundaries.post.call_args.args[1]["env_values"] == {"mcp-id": {"TOKEN": secret}}
    assert boundaries.post.call_args.args[1]["header_values"] == {"mcp-id": {"Authorization": secret}}
    if "dry" in mode:
        run.assert_not_called()
        assert not target.exists()
    else:
        assert run.call_args.args[0] == ["claude", "mcp", "add", "--scope", "project", *command[3:]]
        assert (target / "agent.md").read_text() == "agent\n"


@pytest.mark.parametrize("failure", ["post", "path", "setup", "timeout", "missing"])
def test_pull_dummy_errors_are_sanitized_at_boundary(pull_app_boundary, boundaries, tmp_path, monkeypatch, failure):
    from observal_cli.errors import CliError, ErrorCategory

    secret = "DUMMY_SECRET_FOR_TEST"
    target = tmp_path / "project"
    command = [f"binary-{secret}", "--", f"--token={secret}"]
    if failure == "post":
        monkeypatch.setattr(
            cmd_pull.client,
            "post_public",
            MagicMock(
                side_effect=CliError(
                    ErrorCategory.VALIDATION,
                    f"invalid {secret}",
                    f"operation {secret}",
                    resource=f"resource {secret}",
                    remediation=f"retry {secret}",
                    request_id=f"id-{secret}",
                    http_status=422,
                    detail=f"detail {secret}",
                    result={"nested": [secret]},
                )
            ),
        )
    else:
        boundaries.post.return_value = {
            "config_snippet": {
                "agent_profile": {
                    "path": f"../{secret}/agent.md" if failure == "path" else "agent.md",
                    "content": "agent\n",
                },
                "mcp_setup_commands": [command],
            }
        }
        if failure == "setup":
            outcome = subprocess.CompletedProcess(command, 2, secret, secret)
        elif failure == "timeout":
            outcome = subprocess.TimeoutExpired(command, 60, stderr=secret)
        else:
            outcome = FileNotFoundError(secret)
        monkeypatch.setattr(
            cmd_pull.subprocess,
            "run",
            MagicMock(
                return_value=outcome if failure == "setup" else None,
                side_effect=outcome if failure in ("timeout", "missing") else None,
            ),
        )
    result = _invoke(pull_app_boundary, target, "--env", f"TOKEN={secret}", "--output", "json")
    assert result.exit_code != 0
    assert secret not in result.output
    error = json.loads(result.stderr)["error"]
    if failure == "post":
        assert error["http_status"] == 422
        assert error["request_id"] == "id-<secret>"
        assert error["result"] == {"nested": ["<secret>"]}
    if failure in ("setup", "timeout", "missing"):
        assert error["result"]["setup_commands"][0]["executable"] == "<secret>"


@pytest.mark.parametrize("kind", ["request", "status", "unexpected"])
def test_pull_transport_and_unexpected_errors_mask_debug_after_scope(pull_app_boundary, boundaries, tmp_path, kind):
    import httpx

    secret = "DUMMY_SECRET_FOR_TEST"
    request = httpx.Request("POST", f"https://example.test/{secret}")
    if kind == "request":
        failure = httpx.ConnectError(f"reflected {secret}", request=request)
        category = "unavailable"
    elif kind == "status":
        response = httpx.Response(422, request=request, headers={"x-request-id": secret})
        failure = httpx.HTTPStatusError(f"reflected {secret}", request=request, response=response)
        category = "validation"
    else:
        failure = RuntimeError(f"reflected {secret}")
        category = "unexpected"
    boundaries.post.side_effect = failure
    # The minimal test app has no root --debug option; enable the same renderer branch.
    from observal_cli import errors

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(errors, "debug_requested", lambda *_args: True)
        result = _invoke(pull_app_boundary, tmp_path / "project", "--env", f"TOKEN={secret}", "--output", "json")
    assert secret not in result.output
    error = json.loads(result.stderr)["error"]
    assert error["category"] == category
    assert error["detail"] == type(failure).__name__
    if kind == "status":
        assert error["http_status"] == 422
        assert error["request_id"] == "<secret>"
    assert cmd_pull._pull_secrets.get() is None


@pytest.mark.parametrize("kind", ["request", "status", "unexpected"])
@pytest.mark.parametrize(
    ("secret", "reflected"),
    [
        ("DUMMY/path/to", r"DUMMY\/path\/to"),
        ("DUMMY/ñ\\path/to", r"DUMMY\/\u00f1\\path\/to"),
    ],
)
def test_pull_repr_doubled_json_escapes_mask_debug_json(
    pull_app_boundary, boundaries, tmp_path, kind, secret, reflected
):
    import httpx

    from observal_cli import errors

    request = httpx.Request("POST", "https://example.test/agent")
    if kind == "request":
        failure = httpx.ConnectError(f"reflected {reflected}", request=request)
        category = "unavailable"
    elif kind == "status":
        response = httpx.Response(422, request=request)
        failure = httpx.HTTPStatusError(f"reflected {reflected}", request=request, response=response)
        category = "validation"
    else:
        failure = ValueError(f"reflected {reflected}")
        category = "unexpected"
    boundaries.post.side_effect = failure
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(errors, "debug_requested", lambda *_args: True)
        result = _invoke(pull_app_boundary, tmp_path / "project", "--env", f"TOKEN={secret}", "--output", "json")
    assert result.exit_code != 0
    detail = json.loads(result.stderr)["error"]
    assert detail["category"] == category
    assert detail["detail"] == type(failure).__name__
    assert cmd_pull._pull_secrets.get() is None


@pytest.mark.parametrize("kind", ["request", "status", "unexpected", "usage"])
@pytest.mark.parametrize("output", ["json", "table"])
def test_pull_actual_debug_cli_fails_closed_on_repr_credentials(boundaries, tmp_path, kind, output):
    import click
    import httpx

    secret = "DUMMY'\"token"
    root = typer.Typer(cls=ErrorHandlingGroup)
    agent = typer.Typer()
    cmd_pull.register_pull(agent)
    root.add_typer(agent, name="agent")

    @root.callback()
    def debug_root(debug: bool = typer.Option(False, "--debug")):
        assert debug

    request = httpx.Request("POST", "https://example.test/agent")
    if kind == "request":
        failure = httpx.ConnectError(f"reflected {secret}", request=request)
        category = "unavailable"
    elif kind == "status":
        response = httpx.Response(422, request=request, headers={"x-request-id": f"id-{secret}"})
        failure = httpx.HTTPStatusError(f"reflected {secret}", request=request, response=response)
        category = "validation"
    elif kind == "usage":
        failure = click.UsageError(f"invalid {secret!r}")
        category = "usage"
    else:
        failure = ValueError(f"reflected {secret}")
        category = "unexpected"
    boundaries.post.side_effect = failure
    result = RUNNER.invoke(
        root,
        [
            "--debug",
            "agent",
            "pull",
            "acme/reviewer",
            "--harness",
            "claude-code",
            "--dir",
            str(tmp_path / "project"),
            "--no-prompt",
            "--env",
            f"TOKEN={secret}",
            "--output",
            output,
        ],
    )
    assert result.exit_code != 0
    assert secret not in result.output
    assert repr(secret)[1:-1] not in result.output
    assert "DUMMY" not in result.output
    if output == "json":
        error = json.loads(result.stderr)["error"]
        assert error["category"] == category
        assert error["detail"] == type(failure).__name__
        if kind == "status":
            assert error["http_status"] == 422
            assert error["request_id"] == "id-<secret>"
        if kind == "usage":
            assert error["message"] == "Invalid pull arguments."
    else:
        assert type(failure).__name__ in result.stderr


def test_pull_dummy_delegated_skill_stdout_is_sanitized(pull_app, boundaries, tmp_path):
    secret = "DUMMY_SECRET_FOR_TEST"
    target = tmp_path / "project"
    boundaries.post.return_value = {
        "config_snippet": {
            "agent_profile": {"path": "agent.md", "content": "agent\n"},
            "skill_components": [
                {"name": "safe-skill", "path": f"skills/{secret}/SKILL.md", "skill_md_content": "skill\n"}
            ],
        }
    }
    real_install = boundaries.direct_install.side_effect

    def reflected_install(**kwargs):
        installed = real_install(**kwargs)
        print(f"Wrote skill file: {installed / 'SKILL.md'}")
        return installed

    boundaries.direct_install.side_effect = reflected_install
    result = _invoke(pull_app, target, "--env", f"TOKEN={secret}")
    assert result.exit_code == 0, result.output
    assert secret not in result.output
    assert "Wrote skill file:" in result.output
    assert (target / "skills" / secret / "SKILL.md").exists()
    boundaries.direct_install.assert_called_once()


def test_pull_dummy_skill_failure_prints_safe_name_and_url(pull_app_boundary, boundaries, tmp_path):
    secret = "DUMMY_SECRET_FOR_TEST"
    boundaries.post.return_value = {
        "config_snippet": {
            "agent_profile": {"path": "agent.md", "content": "agent\n"},
            "skill_components": [{"name": f"skill-{secret}", "git_url": f"https://example.test/{secret}"}],
        }
    }
    boundaries.git_install.return_value = None
    boundaries.git_install.side_effect = None
    result = _invoke(pull_app_boundary, tmp_path / "project", "--env", f"TOKEN={secret}")
    assert result.exit_code == 9
    assert secret not in result.output
    assert "<secret>" in result.output


def test_pull_dummy_prompt_then_reflected_metadata_and_header_label(pull_app, boundaries, tmp_path, monkeypatch):
    secret = "DUMMY_SECRET_FOR_TEST"
    target = tmp_path / "project"
    detail = _agent_detail(
        mcp_links=[
            {"mcp_listing_id": "first", "mcp_name": "first"},
            {"mcp_listing_id": "second", "mcp_name": f"next-{secret}"},
        ]
    )
    boundaries.get.return_value = detail
    boundaries.get.side_effect = lambda path: (
        {"environment_variables": [{"name": "TOKEN", "required": True}]}
        if path.endswith("/mcps/first")
        else {"headers": [{"name": "Authorization", "description": f"use {secret}", "required": True}]}
        if path.endswith("/mcps/second")
        else detail
    )
    boundaries.post.return_value = {"config_snippet": {"agent_profile": {"path": "agent.md", "content": "agent\n"}}}
    labels = []

    def prompt(label):
        labels.append(label)
        return secret if len(labels) == 1 else "DUMMY_SECOND_VALUE"

    monkeypatch.setattr(cmd_pull, "password_input", prompt)
    result = _invoke(pull_app, target, no_prompt=False)
    assert result.exit_code == 0, result.output
    assert secret not in result.output
    assert secret not in labels[1]
    assert boundaries.post.call_args.args[1]["env_values"]["first"] == {"TOKEN": secret}
    assert boundaries.post.call_args.args[1]["header_values"]["second"] == {"Authorization": "DUMMY_SECOND_VALUE"}


def test_pull_dummy_override_redacts_early_saved_model_and_first_prompt(pull_app, boundaries, tmp_path, monkeypatch):
    secret = "DUMMY_SECRET_FOR_TEST"
    detail = _agent_detail(mcp_links=[{"mcp_listing_id": "first", "mcp_name": f"name-{secret}"}])
    boundaries.get.return_value = detail
    boundaries.get.side_effect = lambda path: (
        {"environment_variables": [{"name": f"ASK-{secret}", "required": True}]}
        if path.endswith("/mcps/first")
        else detail
    )
    boundaries.adapter.saved_model.return_value = f"model-{secret}"
    boundaries.post.return_value = {"config_snippet": {"agent_profile": {"path": "agent.md", "content": "agent\n"}}}
    labels = []
    monkeypatch.setattr(cmd_pull, "password_input", lambda label: labels.append(label) or "DUMMY_OTHER_VALUE")
    result = _invoke(pull_app, tmp_path / "project", "--env", f"UNUSED={secret}", no_prompt=False)
    assert result.exit_code == 0, result.output
    assert secret not in result.output
    assert labels and secret not in labels[0]
    assert boundaries.post.call_args.args[1]["options"]["model"] == f"model-{secret}"


@pytest.mark.parametrize("secret", ["a", "lock", "name"])
def test_pull_short_secret_json_keeps_schema(pull_app, boundaries, tmp_path, secret):
    boundaries.post.return_value = {
        "config_snippet": {"agent_profile": {"path": "agent.md", "content": "agent\n"}},
        "warnings": [secret],
    }
    result = _invoke(pull_app, tmp_path / "project", "--env", f"TOKEN={secret}", "--output", "json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert "status" in data["files"][0]
    assert "agent" in data and "qualified_name" in data["agent"]
    assert "lock" in data and "status" in data["lock"]
    assert "warnings" in data
    assert data["warnings"] == ["<secret>"]


def test_pull_json_redacts_reflected_server_fields_and_keeps_report_shape(pull_app, boundaries, tmp_path):
    secret = "DUMMY_SECRET_FOR_TEST"
    boundaries.get.return_value = _agent_detail(qualified_name=f"acme/{secret}")
    boundaries.post.return_value = {
        "config_snippet": {"agent_profile": {"path": "agent.md", "content": "agent\n"}},
        "warnings": [f"server echoed {secret}"],
    }
    result = _invoke(pull_app, tmp_path / "project", "--env", f"TOKEN={secret}", "--output", "json")
    assert result.exit_code == 0, result.output
    assert secret not in result.output
    data = json.loads(result.stdout)
    assert data["agent"]["qualified_name"] == "acme/<secret>"
    assert data["warnings"] == ["server echoed <secret>"]
    assert "status" in data["lock"]
    assert data["files"][0]["status"]


def test_pull_json_masks_numeric_credential_before_serialization(capsys):
    token = cmd_pull._pull_secrets.set({"424242"})
    try:
        cmd_pull.output_json({"number": 424242, "ordinary": 7})
        assert json.loads(capsys.readouterr().out) == {"number": "<secret>", "ordinary": 7}
    finally:
        cmd_pull._pull_secrets.reset(token)


def test_pull_json_masks_unknown_object_before_serialization(capsys):
    class ReflectedValue:
        def __str__(self):
            return "DUMMY_SECRET_FOR_TEST"

    token = cmd_pull._pull_secrets.set({"DUMMY_SECRET_FOR_TEST"})
    try:
        cmd_pull.output_json({"warnings": [ReflectedValue()]})
        assert json.loads(capsys.readouterr().out)["warnings"] == ["<secret>"]
    finally:
        cmd_pull._pull_secrets.reset(token)


def test_pull_setup_flag_keys_are_masked_without_changing_execution(pull_app, boundaries, tmp_path, monkeypatch):
    command = ["claude", "-e", "a=a", "-H", "Header-a: a", "--", "server"]
    boundaries.post.return_value = {
        "config_snippet": {
            "agent_profile": {"path": "agent.md", "content": "agent\n"},
            "mcp_setup_commands": [command],
        }
    }
    run = MagicMock(return_value=subprocess.CompletedProcess(command, 0, "", ""))
    monkeypatch.setattr(cmd_pull.subprocess, "run", run)
    result = _invoke(pull_app, tmp_path / "project", "--env", "TOKEN=a", "--output", "json")
    assert result.exit_code == 0, result.output
    assert run.call_args.args[0] == command
    shown = json.loads(result.stdout)["setup_commands"][0]["command"]
    assert shown[2] == "<secret>=<secret>"
    assert shown[4] == "He<secret>der-<secret>: <secret>"
    assert command[2] == "a=a"


def test_pull_spinner_masks_reflected_version(pull_app, boundaries, tmp_path, monkeypatch):
    secret = "DUMMY_SECRET_FOR_TEST"
    messages = []
    monkeypatch.setattr(cmd_pull, "spinner", lambda message: messages.append(message) or nullcontext())
    monkeypatch.setattr(cmd_pull, "_target_version", lambda **_kwargs: (f"1.0-{secret}", "requested"))
    monkeypatch.setattr(cmd_pull, "_locked_version_detail", lambda *_args, **_kwargs: {"components": []})
    result = _invoke(pull_app, tmp_path / "project", "--env", f"TOKEN={secret}")
    assert result.exit_code == 0, result.output
    assert any("Fetching agent version" in message for message in messages)
    assert all(secret not in message for message in messages)


def test_httpx_logger_does_not_log_reflected_url_during_pull(monkeypatch):
    import logging

    import httpx

    from observal_cli import client

    secret = "DUMMY_SECRET_FOR_TEST"
    url = f"https://registry.example.test/api/agents/{secret}/versions/1.0"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    http = httpx.Client(transport=transport)
    monkeypatch.setattr(client.httpx, "get", http.get)
    monkeypatch.setattr(client.config, "get_timeout", lambda: 30)
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = Capture()
    httpx_logger = logging.getLogger("httpx")
    old_level = httpx_logger.level
    httpx_logger.setLevel(logging.INFO)
    httpx_logger.addHandler(handler)
    token = cmd_pull._pull_secrets.set({secret})
    try:
        client._request_with_retry("get", url, {})
        assert secret not in "\n".join(records)
        assert not records
    finally:
        cmd_pull._pull_secrets.reset(token)
        client._request_with_retry("get", url, {})
        httpx_logger.removeHandler(handler)
        httpx_logger.setLevel(old_level)
        http.close()
    assert secret in "\n".join(records)


def test_client_debug_log_hides_reflected_dummy_path_only_during_pull(monkeypatch):
    from io import StringIO

    from loguru import logger

    from observal_cli import client

    secret = "DUMMY_SECRET_FOR_TEST"
    url = f"https://registry.example.test/api/agents/{secret}/versions/1.0"
    response = MagicMock(status_code=200, headers={})
    monkeypatch.setattr(client.config, "get_timeout", lambda: 30)
    monkeypatch.setattr(client.httpx, "get", lambda *_args, **_kwargs: response)
    stream = StringIO()
    sink = logger.add(stream, level="DEBUG")
    token = cmd_pull._pull_secrets.set({secret})
    try:
        client._request_with_retry("get", url, {})
    finally:
        cmd_pull._pull_secrets.reset(token)
        logger.remove(sink)
    assert secret not in stream.getvalue()
    assert stream.getvalue()  # The request was logged without its secret-bearing path.


@pytest.fixture
def reflected_http_server():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from urllib.parse import unquote

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            secret = unquote(self.path.split("/", 2)[-1])
            body = json.dumps({"detail": f"Invalid path {self.path}"}).encode()
            self.send_response(422 if "error" in self.path else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Reflected-Name", secret)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_pull_audit_thread_keeps_http_filters_after_parent_scope_exits(monkeypatch, caplog):
    import logging
    import threading

    import httpx

    from observal_cli import audit

    secret = "DUMMY_SECRET_FOR_TEST"
    canary = "harmless-httpcore-canary"
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(audit, "optic", MagicMock())
    monkeypatch.setattr(
        "observal_cli.config.load", lambda: {"access_token": "test", "server_url": "https://example.test"}
    )

    def send(*_args, **_kwargs):
        started.set()
        assert release.wait(5)
        logging.getLogger("httpcore.http11").debug("diagnostic: %s", canary)
        finished.set()
        raise httpx.ConnectError(f"reflected {secret}")

    monkeypatch.setattr(httpx, "post", send)
    caplog.set_level(logging.DEBUG, logger="httpcore.http11")
    token = cmd_pull._pull_secrets.set({secret})
    try:
        audit.emit_cli_audit("agent.pull", resource_name=secret)
        assert started.wait(5)
    finally:
        cmd_pull._pull_secrets.reset(token)
        release.set()
    assert finished.wait(5)
    # Wait for the worker's exception handler as well as the transport call.
    from time import sleep

    for _ in range(100):
        if audit.optic.debug.called:
            break
        sleep(0.01)
    assert secret not in caplog.text
    assert canary not in caplog.text
    audit.optic.debug.assert_called_once_with("cli audit POST error: {}", "ConnectError")
    logging.getLogger("httpcore.http11").debug("diagnostic: %s", canary)
    assert canary in caplog.text
    assert secret not in caplog.text


def test_pull_real_httpcore_logs_hide_reflected_response_header(reflected_http_server, monkeypatch, caplog):
    import logging

    from observal_cli import client

    secret = "DUMMY_SECRET_FOR_TEST"
    url = f"{reflected_http_server}/agent/{secret}"
    monkeypatch.setattr(client.config, "get_timeout", lambda: 5)
    caplog.set_level(logging.DEBUG, logger="httpcore.http11")
    caplog.set_level(logging.INFO, logger="httpx")
    token = cmd_pull._pull_secrets.set({secret})
    try:
        client._request_with_retry("get", url, {})
        assert secret not in caplog.text
        assert not any(record.name == "httpcore.http11" for record in caplog.records)
    finally:
        cmd_pull._pull_secrets.reset(token)
    caplog.clear()
    client._request_with_retry("get", url, {})
    assert secret in caplog.text


def test_pull_encoded_http_error_detail_is_masked(reflected_http_server, monkeypatch):
    from urllib.parse import quote

    import httpx

    from observal_cli import client
    from observal_cli.errors import CliError

    secret = "DUMMY_SECRET_FOR_TEST"
    encoded = "".join(f"%{ord(char):02x}" for char in secret)
    monkeypatch.setattr(client.config, "get_timeout", lambda: 5)
    token = cmd_pull._pull_secrets.set({secret})
    try:
        with pytest.raises(httpx.HTTPStatusError) as raised:
            client._request_with_retry("get", f"{reflected_http_server}/error/{encoded}", {})
        with pytest.raises(CliError) as failure:
            client._handle_error(raised.value, f"/error/{encoded}")
        safe = cmd_pull._safe_error(failure.value)
        assert encoded not in safe.message
        assert encoded not in safe.detail
        assert encoded not in safe.resource
        assert "<secret>" in safe.message
        assert cmd_pull._safe_text(f"mixed {quote('DUMMY', safe='')}%5fSECRET_FOR_TEST") == "mixed <secret>"
    finally:
        cmd_pull._pull_secrets.reset(token)


def test_pull_live_encoded_http_error_json_is_sanitized(
    pull_app_boundary, boundaries, reflected_http_server, tmp_path, monkeypatch
):
    import httpx

    from observal_cli import client

    secret = "DUMMY_SECRET_FOR_TEST"
    encoded = "".join(f"%{ord(char):02X}" for char in secret)
    monkeypatch.setattr(client.config, "get_timeout", lambda: 5)

    def failed_install(*_args, **_kwargs):
        with pytest.raises(httpx.HTTPStatusError) as raised:
            client._request_with_retry("get", f"{reflected_http_server}/error/{encoded}", {})
        client._handle_error(raised.value, f"/error/{encoded}")

    boundaries.post.side_effect = failed_install
    result = _invoke(pull_app_boundary, tmp_path / "project", "--env", f"TOKEN={secret}", "--output", "json")
    assert result.exit_code != 0
    assert secret not in result.output
    assert encoded not in result.output
    error = json.loads(result.stderr)["error"]
    assert "<secret>" in error["message"]
    assert "<secret>" in error["resource"]


def test_pull_bracket_spanning_secret_is_masked_before_rich_parsing(pull_app, boundaries, tmp_path):
    secret = "DUMMY[SECRET]FOR_TEST"
    boundaries.post.return_value = {
        "config_snippet": {"agent_profile": {"path": "agent.md", "content": "agent\n"}},
        "warnings": [f"reflected {secret}"],
    }
    result = _invoke(pull_app, tmp_path / "project", "--env", f"TOKEN={secret}")
    assert result.exit_code == 0, result.output
    assert secret not in result.output
    assert "<secret>" in result.output
    assert "SECRET" not in result.output


def test_pull_rich_escaped_bracket_secret_and_literal_tag(pull_app, boundaries, tmp_path):
    from observal_cli.render import esc

    secret = "ab[cd]ef"  # Dummy credential; lowercase brackets are Rich markup candidates.
    boundaries.post.return_value = {
        "config_snippet": {"agent_profile": {"path": "agent.md", "content": "agent\n"}},
        "warnings": [f"reflected {esc(secret)} and {esc('[foo]')}"],
    }
    result = _invoke(pull_app, tmp_path / "project", "--env", f"TOKEN={secret}")
    assert result.exit_code == 0, result.output
    assert secret.encode() not in result.output.encode()
    assert b"ab\\[cd]ef" not in result.output.encode()
    assert b"<secret>" in result.output.encode()
    assert b"[foo]" in result.output.encode()  # No extra slash from escaping twice.
    assert b"\\[foo]" not in result.output.encode()


def test_pull_escaped_warning_keeps_rich_style_without_literal_slashes():
    from rich.text import Text

    token = cmd_pull._pull_secrets.set({"ab[cd]ef"})
    try:
        markup = cmd_pull._safe_markup(f"[yellow]{cmd_pull.esc('ab[cd]ef')} and {cmd_pull.esc('[foo]')}[/yellow]")
        shown = Text.from_markup(markup)
        assert shown.plain == "<secret> and [foo]"
        assert any(span.style == "yellow" for span in shown.spans)
    finally:
        cmd_pull._pull_secrets.reset(token)


def test_pull_preescaped_literal_stays_literal_when_secret_overlaps_style(capsys):
    token = cmd_pull._pull_secrets.set({"e"})
    try:
        literal = r"\[foo]"
        cmd_pull.rprint(f"[green]{cmd_pull.esc(literal)}[/green]")
        output = capsys.readouterr().out
        assert "[foo]" in output
        assert "\\" not in output
    finally:
        cmd_pull._pull_secrets.reset(token)


def test_pull_json_serialized_and_double_encoded_reflections_are_masked(capsys):
    from urllib.parse import quote

    from observal_cli.errors import CliError, ErrorCategory

    sample_value = 'DUMMY"\\TOKEN'
    serialized = json.dumps(sample_value)[1:-1]
    twice_encoded = quote(quote(sample_value, safe=""), safe="")
    token = cmd_pull._pull_secrets.set({sample_value})
    try:
        cmd_pull.output_json({"warnings": [f"reflected {serialized} {twice_encoded}"]})
        warning = json.loads(capsys.readouterr().out)["warnings"][0]
        assert warning == "reflected <secret> <secret>"
        error = CliError(
            ErrorCategory.VALIDATION, f"rejected {serialized}", operation="Pull agent", detail=twice_encoded
        )
        safe = cmd_pull._safe_error(error)
        assert safe.message == "rejected <secret>"
        assert safe.detail == "<secret>"
    finally:
        cmd_pull._pull_secrets.reset(token)


def test_pull_json_escaped_slash_reflections_mask_warnings_and_errors(capsys):
    from observal_cli.errors import CliError, ErrorCategory

    probe = "DUMMY/path/to"
    forms = [probe, r"DUMMY\/path\/to", r"DUMMY\/path/to", r"DUMMY%252fpath\/to"]
    token = cmd_pull._pull_secrets.set({probe})
    try:
        cmd_pull.output_json({"warnings": forms})
        assert json.loads(capsys.readouterr().out)["warnings"] == ["<secret>"] * len(forms)
        for form in forms:
            failure = CliError(ErrorCategory.VALIDATION, form, operation="Pull agent", detail=form)
            assert cmd_pull._safe_error(failure).message == "<secret>"
            assert cmd_pull._safe_error(failure).detail == "<secret>"
    finally:
        cmd_pull._pull_secrets.reset(token)


def test_pull_form_encoded_space_reflections_in_json_markup_and_errors(capsys):
    from urllib.parse import quote_plus

    from rich.text import Text

    from observal_cli.errors import CliError, ErrorCategory

    sample_value = "DUMMY alpha+beta gamma"
    form = quote_plus(sample_value, safe="")
    double = quote_plus(form, safe="")
    mixed = "DUMMY+alpha%252Bbeta%20gamma"
    serialized = json.dumps(sample_value)[1:-1]
    assert (
        cmd_pull._mask_values(f"{form} {double} {mixed} {serialized}", {sample_value}) == "<secret> " * 3 + "<secret>"
    )
    # Literal pluses at space positions are ambiguous with quote_plus: fail closed.
    assert cmd_pull._mask_values("DUMMY+alpha+beta+gamma", {sample_value}) == "<secret>"
    # A plus in untrusted text is never globally decoded or rewritten.
    assert cmd_pull._mask_values("unrelated+value DUMMY%2Balpha+beta", {sample_value}) == (
        "unrelated+value DUMMY%2Balpha+beta"
    )
    token = cmd_pull._pull_secrets.set({sample_value})
    try:
        cmd_pull.output_json({"warnings": [f"{form} {double} {mixed} unrelated+value"]})
        assert json.loads(capsys.readouterr().out)["warnings"] == ["<secret> <secret> <secret> unrelated+value"]
        markup = cmd_pull._safe_markup(f"[yellow]{form} {double} {mixed} unrelated+value[/yellow]")
        assert Text.from_markup(markup).plain == "<secret> <secret> <secret> unrelated+value"
        error = CliError(ErrorCategory.VALIDATION, f"rejected {mixed}", operation="Pull agent", detail=double)
        safe = cmd_pull._safe_error(error)
        assert safe.message == "rejected <secret>"
        assert safe.detail == "<secret>"
    finally:
        cmd_pull._pull_secrets.reset(token)


def test_pull_unicode_json_and_mixed_percent_reflections_are_masked():
    from urllib.parse import quote

    secret = "DUMMY_ñ[ok]"
    serialized = json.dumps(secret, ensure_ascii=True)[1:-1]
    twice_encoded = quote(quote(secret, safe=""), safe="")
    mixed = "DUMMY%255f" + quote("ñ", safe="") + r"\[ok]"
    assert cmd_pull._mask_values(f"{serialized} {twice_encoded} {mixed}", {secret}) == "<secret> <secret> <secret>"


def test_pull_repr_escaped_json_choices_mix_with_raw_and_url_encodings():
    secret = "DUMMY/ñ\\path/to"
    # repr doubles each JSON backslash, but other pieces can remain raw or URL encoded.
    forms = (r"DUMMY\\/\\u00f1\\\\path\\/to", r"DUMMY\\/ñ\\\\path%2fto", r"DUMMY\/\\u00f1\\\\path/to")
    assert cmd_pull._mask_values(" | ".join(forms), {secret}) == " | ".join(["<secret>"] * len(forms))
    assert cmd_pull._mask_values(r"DUMMY\\/\\u00f1\\\\path\\/tx", {secret}) != "<secret>"


def test_pull_repr_quote_choices_in_mixed_warnings():
    secret = "DUMMY'\"token"
    forms = (r"DUMMY\'\"token", r"DUMMY\'\\\"token", r"DUMMY%27\"token", r"DUMMY\'%22token")
    assert cmd_pull._mask_values(" | ".join(forms), {secret}) == " | ".join(["<secret>"] * len(forms))
    assert cmd_pull._mask_values(r"DUMMY\'\"tokex", {secret}) == r"DUMMY\'\"tokex"


def test_pull_reverse_bitset_accepts_mixed_url_json_and_raw_choices():
    # A bit can advance across different-length alternatives at successive
    # offsets; the encoded prefix index must never reject those transitions.
    secret = "ñ[é]ñ"
    forms = (
        r"\u00F1%5B%C3%A9\u005Dñ",
        r"%25c3%25B1[\u00e9%5d%c3%b1",
        r"%c3%b1\[é%255d\u00f1",
    )
    assert cmd_pull._mask_values("!" + " | ".join(forms), {secret}) == "!" + " | ".join(["<secret>"] * 3)
    near_miss = forms[0][:-1] + "Q"
    assert cmd_pull._mask_values(near_miss, {secret}) == near_miss


def test_pull_literal_marker_is_not_trusted_but_generated_marker_is_preserved():
    marker = "<secret>"
    secret = "DUMMY" + marker
    assert cmd_pull._mask_values(f"server reflected {secret}", {secret}) == "server reflected <secret>"
    assert cmd_pull._mask_values("server reflected <secret>", {marker}) == "server reflected <secret>"
    # A short second credential inside the replacement must not corrupt it,
    # while the same text supplied by the server must still be scanned.
    first = cmd_pull._mask_values(f"{secret} untrusted {marker} end", {secret})
    assert cmd_pull._mask_values(first, {"e"}) == "<secret> untrust<secret>d <s<secret>cr<secret>t> <secret>nd"


def test_pull_unicode_json_hex_case_and_mixed_reflections():
    secret = "DUMMYñTOKEN"
    text = r"DUMMY\u00F1TOKEN DUMMY\u00f1TOKEN DUMMY%C3%B1TOKEN DUMMY%25c3%25B1TOKEN"
    assert cmd_pull._mask_values(text, {secret}) == "<secret> <secret> <secret> <secret>"
    # Raw matching is still case sensitive; only hex digits in encodings fold.
    assert cmd_pull._mask_values("dummyñTOKEN", {secret}) == "dummyñTOKEN"


def test_pull_short_overlapping_values_and_mixed_encodings():
    assert cmd_pull._mask_values("DUMMYX DUMMY%58 DUMMY", {"DUMMY", "DUMMYX"}) == ("<secret> <secret> <secret>")
    assert cmd_pull._mask_values(r"a\\b a%5Cb a%255cb", {r"a\b"}) == "<secret> <secret> <secret>"


def test_pull_first_character_prefilter_keeps_encoded_starts():
    # None of these warnings contains the secret's raw first character.
    assert cmd_pull._mask_values(r"\[ok]", {"[ok]"}) == "<secret>"
    assert cmd_pull._mask_values(r"\"TOKEN", {'"TOKEN'}) == "<secret>"
    assert cmd_pull._mask_values(r"\u00F1TOKEN", {"ñTOKEN"}) == "<secret>"
    assert cmd_pull._mask_values("%C3%B1TOKEN %25c3%25B1TOKEN", {"ñTOKEN"}) == "<secret> <secret>"
    assert cmd_pull._mask_values("+TOKEN %2bTOKEN %252BTOKEN %20TOKEN", {" TOKEN"}) == (
        "<secret> <secret> <secret> <secret>"
    )
    assert cmd_pull._mask_values("qtOKEN", {"QtOKEN"}) == "qtOKEN"  # raw remains case-sensitive


def test_pull_many_impossible_secrets_skip_reverse_scans():
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "from time import monotonic\n"
        "from observal_cli.cmd_pull import _mask_values\n"
        "text = 'Q' * 100_000\n"
        "for count in (100, 300):\n"
        "    secrets = {f'DUMMY_{i:03d}_secret' for i in range(count)}\n"
        "    start = monotonic()\n"
        "    assert _mask_values(text, secrets) == text\n"
        "    assert monotonic() - start < 3.0, count\n"
    )
    subprocess.run([sys.executable, "-c", script], check=True, timeout=10)


def test_pull_common_prefix_impossible_suffix_skips_reverse_scans():
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "from time import monotonic\n"
        "from observal_cli.cmd_pull import _mask_values\n"
        "text = 'a' * 100_000\n"
        "secrets = {f'a{i:04d}' for i in range(100)}\n"
        "start = monotonic()\n"
        "assert _mask_values(text, secrets) == text\n"
        "assert monotonic() - start < 3.0\n"
        "assert _mask_values('a' * 100_000 + 'a00%39%39', secrets) == 'a' * 100_000 + '<secret>'\n"
        "assert _mask_values('a' * 100_000 + 'a0099', secrets) == 'a' * 100_000 + '<secret>'\n"
    )
    subprocess.run([sys.executable, "-c", script], check=True, timeout=10)


def test_pull_ambiguous_backslashes_finish_with_bounded_resources():
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "from observal_cli.cmd_pull import _mask_values\n"
        "if sys.platform == 'linux':\n"
        "    import resource\n"
        "    baseline_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
        "secret = '\\\\' * 32 + 'Z'\n"
        "text = '\\\\' * 100_000 + 'Q'\n"
        "assert _mask_values(text, {secret}) == text\n"
        "# An attainable lowercase-hex ending must not be rejected by the fast path.\n"
        "assert _mask_values('\\\\' * 100_000 + '%5a', {secret}) == '\\\\' * 99_872 + '<secret>'\n"
        "assert _mask_values('\\\\' * 80 + 'Q', {'\\\\' * 32 + 'Q'}) == '<secret>'\n"
        "if sys.platform == 'linux':\n"
        "    assert resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - baseline_rss < 96 * 1024\n"
    )
    subprocess.run([sys.executable, "-c", script], check=True, timeout=10)


def test_pull_long_near_matches_have_bounded_runtime():
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "from time import monotonic\n"
        "from observal_cli.cmd_pull import _mask_values\n"
        "secret = 'a' * 1600 + 'Z'\n"
        "text = 'a' * 1600 + 'Q%5a'\n"
        "start = monotonic()\n"
        "assert _mask_values(text, {secret}) == text\n"
        "assert monotonic() - start < 1.0\n"
        "assert _mask_values('a' * 1600 + '%5a', {secret}) == '<secret>'\n"
    )
    subprocess.run([sys.executable, "-c", script], check=True, timeout=10)


def test_pull_distinct_unicode_masks_skip_unrelated_warning_positions():
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "from time import monotonic\n"
        "from observal_cli.cmd_pull import _mask_values\n"
        "secret = ''.join(chr(0x400 + i) for i in range(6400))\n"
        "for text in ('Q' * 6400, (secret[:80] + 'Q') * 80, '%GG' * 6400):\n"
        "    start = monotonic()\n"
        "    assert _mask_values(text, {secret}) == text\n"
        "    assert monotonic() - start < 5.0\n"
        "assert _mask_values('Q' + secret, {secret}) == 'Q<secret>'\n"
    )
    subprocess.run([sys.executable, "-c", script], check=True, timeout=15)


def test_model_override_trace_does_not_log_raw_values():
    from io import StringIO

    from loguru import logger

    stream = StringIO()
    sink = logger.add(stream, level="TRACE")
    try:
        assert cmd_pull._parse_model_overrides(["DUMMY_MODEL_OVERRIDE"]) == ("DUMMY_MODEL_OVERRIDE", {})
    finally:
        logger.remove(sink)
    assert "DUMMY_MODEL_OVERRIDE" not in stream.getvalue()


def test_pull_dummy_server_metadata_does_not_enter_debug_log(boundaries, tmp_path):
    from io import StringIO

    from loguru import logger

    root = typer.Typer(cls=ErrorHandlingGroup)
    agent = typer.Typer()
    cmd_pull.register_pull(agent)
    root.add_typer(agent, name="agent")

    @root.callback()
    def debug_root(debug: bool = typer.Option(False, "--debug")):
        assert debug

    secret = "DUMMY_SECRET_FOR_TEST"
    boundaries.get.return_value = _agent_detail(name=f"name-{secret}", version=f"v-{secret}")
    boundaries.post.return_value = {
        "version": f"v-{secret}",
        "config_snippet": {"agent_profile": {"path": "agent.md", "content": "agent\n"}},
    }
    boundaries.upsert.side_effect = real_upsert_agent
    stream = StringIO()
    log_path = tmp_path / "cli.log"
    sink = logger.add(stream, level="DEBUG")
    file_sink = logger.add(log_path, level="DEBUG")
    try:
        result = RUNNER.invoke(
            root,
            [
                "--debug",
                "agent",
                "pull",
                "acme/reviewer",
                "--harness",
                "claude-code",
                "--dir",
                str(tmp_path / "project"),
                "--no-prompt",
                "--env",
                f"TOKEN={secret}",
            ],
        )
    finally:
        logger.remove(sink)
        logger.remove(file_sink)
    assert result.exit_code == 0, result.output
    assert secret not in result.output
    assert secret not in stream.getvalue()
    assert secret not in log_path.read_text()
    assert boundaries.upsert.call_args.kwargs["name"] == f"name-{secret}"
    from observal_cli.lockfile import LOCKFILE_PATH

    assert secret in LOCKFILE_PATH.read_text()
