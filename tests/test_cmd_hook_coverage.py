# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from contextlib import nullcontext
from unittest.mock import Mock, call

import pytest
import typer
from typer.testing import CliRunner

import observal_cli.cmd_hook as hook
from observal_cli import lockfile
from observal_cli.errors import CliError, ErrorCategory
from observal_cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_hook_cli(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hook, "spinner", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(hook.config, "load", Mock(return_value={"username": "alice"}))
    monkeypatch.setattr(hook.config, "save_last_results", Mock())
    monkeypatch.setattr(lockfile, "local_registry_name", Mock(return_value="guard"))
    monkeypatch.setattr(lockfile, "upsert_standalone", Mock())


def _hook_item(**overrides):
    item = {
        "id": "hook-123456789",
        "name": "Guard Hook",
        "namespace": "alice",
        "slug": "guard",
        "qualified_name": "alice/guard",
        "version": "1.2.3",
        "status": "approved",
        "event": "PreToolUse",
        "handler_type": "command",
        "handler_config": {"command": "python guard.py", "timeout": 10},
        "execution_mode": "blocking",
        "scope": "agent",
        "description": "Guards tool calls",
        "created_at": "not-a-date",
    }
    item.update(overrides)
    return item


def test_register_hook_commands():
    parent = Mock()
    hook.register_hook(parent)
    parent.add_typer.assert_called_once_with(hook.hook_app, name="hook")


def test_submit_existing_draft_and_rejects_conflicting_modes(monkeypatch):
    resolve = Mock(return_value="resolved-draft")
    post = Mock(return_value={"id": "hook-1"})
    monkeypatch.setattr(hook.client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(hook.client, "post", post)

    submitted = runner.invoke(app, ["registry", "hook", "submit", "--submit", "alice/draft"])
    conflicting = runner.invoke(
        app,
        ["registry", "hook", "submit", "--draft", "--submit", "alice/draft"],
    )

    assert submitted.exit_code == 0, submitted.output
    assert "Draft submitted for review" in submitted.output
    assert conflicting.exit_code == 7
    assert "Draft creation" in conflicting.output
    resolve.assert_called_once_with("hook", "alice/draft")
    post.assert_called_once_with("/api/v1/hooks/resolved-draft/submit")


def test_submit_from_file_sets_owner_and_preserves_publish_target(tmp_path, monkeypatch):
    source = tmp_path / "hook.json"
    source_payload = {
        "name": "file-hook",
        "version": "2.0.0",
        "description": "Loaded from a file",
        "event": "Stop",
        "handler_type": "command",
        "handler_config": {"command": "python stop.py", "timeout": 10},
    }
    source.write_text(json.dumps(source_payload), encoding="utf-8")
    publish_target = Mock()
    post = Mock(return_value={"id": "draft-1", "namespace": "alice", "slug": "file-hook"})
    monkeypatch.setattr(hook.client, "add_publish_target", publish_target)
    monkeypatch.setattr(hook.client, "post", post)

    result = runner.invoke(
        app,
        [
            "registry",
            "hook",
            "submit",
            "--from-file",
            str(source),
            "--draft",
            "--team",
            "platform",
            "--visibility",
            "team",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Draft saved" in result.output
    expected = {**source_payload, "owner": "alice"}
    publish_target.assert_called_once_with(expected, "platform", "team")
    post.assert_called_once_with("/api/v1/hooks/draft", expected)


@pytest.mark.parametrize(
    ("filename", "contents", "exit_code", "message"),
    [
        ("missing.json", None, 5, "not found"),
        ("broken.json", "{not-json", 7, "not valid JSON"),
    ],
)
def test_submit_from_file_reports_parse_failures(tmp_path, filename, contents, exit_code, message, monkeypatch):
    source = tmp_path / filename
    if contents is not None:
        source.write_text(contents, encoding="utf-8")
    post = Mock()
    monkeypatch.setattr(hook.client, "post", post)

    result = runner.invoke(app, ["registry", "hook", "submit", "--from-file", str(source)])

    assert result.exit_code == exit_code
    assert message in result.output
    post.assert_not_called()


def test_submit_script_draft_builds_complete_payload(tmp_path, monkeypatch):
    script = tmp_path / "guard.py"
    script.write_text("print('guard')\n", encoding="utf-8")
    publish_target = Mock()
    post = Mock(
        return_value={
            "id": "draft-script",
            "namespace": "platform",
            "slug": "guard",
            "qualified_name": "platform/guard",
        }
    )
    monkeypatch.setattr(hook.client, "add_publish_target", publish_target)
    monkeypatch.setattr(hook.client, "post", post)

    result = runner.invoke(
        app,
        [
            "registry",
            "hook",
            "submit",
            "--script",
            str(script),
            "--name",
            "guard",
            "--version",
            "3.0.0",
            "--description",
            "Guard files",
            "--event",
            "PreToolUse",
            "--timeout",
            "30",
            "--execution-mode",
            "blocking",
            "--scope",
            "global",
            "--harness",
            "claude-code",
            "--harness",
            "kiro",
            "--source-url",
            "https://github.com/acme/hooks",
            "--source-ref",
            "v3",
            "--source-path",
            "hooks/guard",
            "--requires",
            "python>=3.11",
            "--requires",
            "pip install policy",
            "--draft",
            "--team",
            "platform",
            "--visibility",
            "team",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "registry hook install platform/guard" in result.output
    expected = {
        "name": "guard",
        "version": "3.0.0",
        "description": "Guard files",
        "owner": "alice",
        "event": "PreToolUse",
        "handler_type": "command",
        "handler_config": {"command": "guard.py", "timeout": 30},
        "execution_mode": "blocking",
        "scope": "global",
        "supported_harnesses": ["claude-code", "kiro"],
        "script_content": "print('guard')\n",
        "script_filename": "guard.py",
        "source_url": "https://github.com/acme/hooks",
        "source_ref": "v3",
        "source_path": "hooks/guard",
        "requirements": ["python>=3.11", "pip install policy"],
    }
    publish_target.assert_called_once_with(expected, "platform", "team")
    post.assert_called_once_with("/api/v1/hooks/draft", expected)


def test_submit_http_flags_use_defaults(monkeypatch):
    publish_target = Mock()
    post = Mock(return_value={"id": "http-1", "name": "audit"})
    monkeypatch.setattr(hook.client, "add_publish_target", publish_target)
    monkeypatch.setattr(hook.client, "post", post)

    result = runner.invoke(
        app,
        [
            "registry",
            "hook",
            "submit",
            "--name",
            "audit",
            "--description",
            "Audit requests",
            "--event",
            "PostToolUse",
            "--handler-url",
            "https://hooks.example.test/audit",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = post.call_args.args[1]
    assert payload == {
        "name": "audit",
        "version": "1.0.0",
        "description": "Audit requests",
        "owner": "alice",
        "event": "PostToolUse",
        "handler_type": "http",
        "handler_config": {"url": "https://hooks.example.test/audit", "timeout": 10},
        "execution_mode": "async",
        "scope": "agent",
        "supported_harnesses": [],
    }
    publish_target.assert_called_once_with(payload, None, None)


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            [
                "--name",
                "guard",
                "--description",
                "Guard",
                "--event",
                "UnknownEvent",
                "--handler-command",
                "guard.py",
            ],
            "Unknown hook event",
        ),
        (
            [
                "--name",
                "guard",
                "--description",
                "Guard",
                "--event",
                "Stop",
                "--handler-type",
                "python",
                "--handler-command",
                "guard.py",
            ],
            "Unknown hook handler type",
        ),
        (
            [
                "--name",
                "guard",
                "--description",
                "Guard",
                "--event",
                "Stop",
                "--handler-command",
                "guard.py",
                "--execution-mode",
                "parallel",
            ],
            "Unknown hook execution mode",
        ),
        (
            [
                "--name",
                "guard",
                "--description",
                "Guard",
                "--event",
                "Stop",
                "--handler-command",
                "guard.py",
                "--scope",
                "workspace",
            ],
            "Unknown hook scope",
        ),
        (
            [
                "--name",
                "guard",
                "--description",
                "Guard",
                "--event",
                "Stop",
                "--handler-command",
                "guard.py",
                "--harness",
                "unknown-harness",
            ],
            "Unknown harness",
        ),
        (["--name", "guard"], "Hook name, description"),
        (
            [
                "--name",
                "audit",
                "--description",
                "Audit",
                "--event",
                "Stop",
                "--handler-type",
                "http",
            ],
            "HTTP handler URL is required",
        ),
        (
            ["--name", "guard", "--description", "Guard", "--event", "Stop"],
            "handler command or script is required",
        ),
    ],
)
def test_submit_flag_validation_stops_before_http(arguments, message, monkeypatch):
    post = Mock()
    monkeypatch.setattr(hook.client, "post", post)

    result = runner.invoke(app, ["registry", "hook", "submit", *arguments])

    assert result.exit_code == 7
    assert message in result.output
    post.assert_not_called()


def test_submit_reports_missing_script(monkeypatch):
    post = Mock()
    monkeypatch.setattr(hook.client, "post", post)

    result = runner.invoke(app, ["registry", "hook", "submit", "--script", "missing.py"])

    assert result.exit_code == 5
    assert "script file was not found" in result.output
    post.assert_not_called()


def test_timeout_validation_accepts_missing_and_rejects_over_cap(capsys):
    hook._validate_timeout("blocking", {})
    hook._validate_timeout("unknown", {"timeout": 999})

    with pytest.raises(typer.Exit) as exc:
        hook._validate_timeout("sync", {"timeout": 11})

    assert exc.value.exit_code == 7
    output = capsys.readouterr().out
    assert "exceeds the 10s maximum" in output


@pytest.mark.parametrize(
    ("handler_type", "script_name", "expected_config"),
    [
        ("command", "interactive.py", {"command": "interactive.py", "timeout": 10}),
        ("command", None, {"command": "python guard.py", "timeout": 10}),
        ("http", None, {"url": "https://hooks.example.test/interactive", "timeout": 10}),
    ],
)
def test_submit_interactive_handler_paths(tmp_path, handler_type, script_name, expected_config, monkeypatch):
    arguments = ["registry", "hook", "submit"]
    if script_name:
        script = tmp_path / script_name
        script.write_text("print('interactive')\n", encoding="utf-8")
        arguments.extend(["--script", str(script)])

    answers = {
        "Hook name": "interactive-hook",
        "Version": "1.5.0",
        "Description": "Interactive description",
        "Command": "python guard.py",
        "Hook URL": "https://hooks.example.test/interactive",
        "Timeout (seconds)": "10",
    }
    selections = {
        "Event": "UserPromptSubmit",
        "Handler type": handler_type,
        "Execution mode": "sync",
    }
    monkeypatch.setattr(hook, "text_input", lambda prompt, default=None: answers[prompt])
    monkeypatch.setattr(hook, "select_one", lambda prompt, _choices: selections[prompt])
    monkeypatch.setattr(hook.client, "add_publish_target", Mock())
    post = Mock(return_value={"id": "interactive", "name": "interactive-hook"})
    monkeypatch.setattr(hook.client, "post", post)

    result = runner.invoke(app, arguments)

    assert result.exit_code == 0, result.output
    payload = post.call_args.args[1]
    assert payload["name"] == "interactive-hook"
    assert payload["version"] == "1.5.0"
    assert payload["event"] == "UserPromptSubmit"
    assert payload["handler_type"] == handler_type
    assert payload["handler_config"] == expected_config
    assert payload["execution_mode"] == "sync"
    if script_name:
        assert payload["script_filename"] == script_name
        assert payload["script_content"] == "print('interactive')\n"
        assert "Command auto-set" in result.output


def test_list_filters_json_empty_rejects_plain_and_renders_table(monkeypatch):
    item = _hook_item()
    get = Mock(side_effect=[[item], [], [item]])
    save = Mock()
    resolve_team = Mock(return_value="team-1")
    monkeypatch.setattr(hook.client, "get", get)
    monkeypatch.setattr(hook.client, "resolve_team_id", resolve_team)
    monkeypatch.setattr(hook.config, "save_last_results", save)

    as_json = runner.invoke(
        app,
        [
            "registry",
            "hook",
            "list",
            "--event",
            "PreToolUse",
            "--search",
            "guard",
            "--namespace",
            "@Alice",
            "--team",
            "platform",
            "--output",
            "json",
        ],
    )
    empty = runner.invoke(app, ["registry", "hook", "list"])
    plain = runner.invoke(app, ["registry", "hook", "list", "--output", "plain"])
    table = runner.invoke(app, ["registry", "hook", "list"])

    assert all(result.exit_code == 0 for result in (as_json, empty, table))
    assert plain.exit_code == 2
    assert "Error" in plain.output
    assert "plain" in plain.output
    assert json.loads(as_json.output) == {"items": [item], "total": 1, "page": 1, "page_size": 1}
    assert "No hooks found" in empty.output
    assert "Hooks (1)" in table.output
    assert "@alice" in table.output
    assert get.call_args_list[0] == call(
        "/api/v1/hooks",
        params={
            "event": "PreToolUse",
            "search": "guard",
            "namespace": "alice",
            "team_id": "team-1",
        },
    )
    resolve_team.assert_called_once_with("platform")
    assert save.call_count == 3


def test_list_surfaces_http_failure(monkeypatch):
    monkeypatch.setattr(hook.client, "get", Mock(side_effect=RuntimeError("registry offline")))

    result = runner.invoke(app, ["registry", "hook", "list"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Error (unexpected)" in result.output
    assert "Run observal registry hook list" in result.output


def test_show_renders_optional_metadata_and_json(monkeypatch):
    item = _hook_item(
        script_filename="guard.py",
        source_url="https://github.com/acme/hooks",
        source_ref="v2",
        requirements=["python>=3.11", "pip install policy"],
    )
    resolve = Mock(return_value="resolved")
    get = Mock(return_value=item)
    monkeypatch.setattr(hook.client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(hook.client, "get", get)

    rendered = runner.invoke(app, ["registry", "hook", "show", "alice/guard"])
    as_json = runner.invoke(
        app,
        ["registry", "hook", "show", "alice/guard", "--output", "json"],
    )

    assert rendered.exit_code == as_json.exit_code == 0
    assert "guard v1.2.3" in rendered.output
    assert "guard.py" in rendered.output
    assert "https://github.com/acme/hooks@v2" in rendered.output
    assert "python>=3.11, pip install policy" in rendered.output
    assert "not-a-date" in rendered.output
    assert json.loads(as_json.output) == item
    assert resolve.call_args_list == [call("hook", "alice/guard"), call("hook", "alice/guard")]
    assert get.call_args_list == [call("/api/v1/hooks/resolved"), call("/api/v1/hooks/resolved")]


def test_show_surfaces_http_failure(monkeypatch):
    monkeypatch.setattr(hook.client, "resolve_registry_reference", Mock(return_value="missing"))
    monkeypatch.setattr(hook.client, "get", Mock(side_effect=RuntimeError("lookup failed")))

    result = runner.invoke(app, ["registry", "hook", "show", "missing"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Error (unexpected)" in result.output
    assert "Run observal registry hook show" in result.output


def test_install_raw_preserves_server_result_and_skips_writes(tmp_path, monkeypatch):
    project = tmp_path / "project"
    listing = _hook_item()
    response = {
        "config_snippet": {"hooks": {"PreToolUse": [{"command": "python guard.py"}]}},
        "files": [{"path": "hooks/guard.py", "content": "print('guard')"}],
        "requirements": ["python>=3.11"],
        "warnings": ["Review the command"],
        "notes": ["Restart the harness"],
        "config_path": ".claude/settings.json",
    }
    resolve = Mock(return_value="resolved")
    local_name = Mock(return_value="alice-guard")
    post = Mock(return_value=response)
    upsert = Mock()
    monkeypatch.setattr(hook.client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(hook.client, "get", Mock(return_value=listing))
    monkeypatch.setattr(hook.client, "post", post)
    monkeypatch.setattr(lockfile, "local_registry_name", local_name)
    monkeypatch.setattr(lockfile, "upsert_standalone", upsert)

    result = runner.invoke(
        app,
        [
            "registry",
            "hook",
            "install",
            "alice/guard",
            "--harness",
            "claude-code",
            "--platform",
            "linux",
            "--dir",
            str(project),
            "--raw",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == response
    resolve.assert_called_once_with("hook", "alice/guard")
    local_name.assert_called_once_with(
        "claude-code",
        "hook",
        "alice",
        "guard",
        scope="project",
        directory=str(project.resolve()),
    )
    post.assert_called_once_with(
        "/api/v1/hooks/resolved/install",
        {"harness": "claude-code", "platform": "linux", "local_name": "alice-guard"},
    )
    assert not project.exists()
    upsert.assert_not_called()


def test_install_writes_safe_files_config_and_lock_entry(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    response = {
        "files": [
            {"path": "hooks/guard.py", "content": "print('guard')\n", "executable": True},
        ],
        "config_path": ".claude/settings.json",
        "config_snippet": {
            "version": 1,
            "hooks": {"PreToolUse": [{"command": "python hooks/guard.py"}]},
        },
        "requirements": ["python>=3.11", "pip install policy"],
        "warnings": ["Review before enabling"],
        "notes": ["Restart Claude Code"],
    }
    upsert = Mock()
    monkeypatch.setattr(hook.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(hook.client, "get", Mock(return_value=_hook_item()))
    monkeypatch.setattr(hook.client, "post", Mock(return_value=response))
    monkeypatch.setattr(lockfile, "upsert_standalone", upsert)

    result = runner.invoke(
        app,
        ["registry", "hook", "install", "guard", "--harness", "claude-code", "--dir", str(project)],
    )

    assert result.exit_code == 0, result.output
    script = project / "hooks/guard.py"
    assert script.read_text(encoding="utf-8") == "print('guard')\n"
    assert script.stat().st_mode & 0o111
    config_file = project / ".claude/settings.json"
    assert json.loads(config_file.read_text(encoding="utf-8")) == response["config_snippet"]
    assert "Updated" in result.output
    assert "Review before enabling" in result.output
    assert "Prerequisites required" in result.output
    assert "python>=3.11" in result.output
    assert "Restart Claude Code" in result.output
    assert "Hook installed for claude-code" in result.output
    upsert.assert_called_once_with(
        "claude-code",
        component_type="hook",
        name="Guard Hook",
        component_id="hook-123456789",
        version="1.2.3",
        scope="project",
        version_id=None,
        digest=None,
        requested_version=None,
        directory=str(project.resolve()),
        namespace="alice",
        slug="guard",
        local_name="guard",
    )


def test_install_merges_existing_config_and_rejects_invalid_json(tmp_path, monkeypatch):
    project = tmp_path / "project"
    valid_path = project / "valid.json"
    invalid_path = project / "invalid.json"
    valid_path.parent.mkdir(parents=True)
    valid_path.write_text(
        json.dumps(
            {
                "other": True,
                "hooks": {
                    "PreToolUse": [{"command": "existing"}],
                    "Stop": [{"command": "stop"}],
                },
            }
        ),
        encoding="utf-8",
    )
    invalid_path.write_text("{broken", encoding="utf-8")
    responses = [
        {
            "config_path": "valid.json",
            "config_snippet": {
                "version": 2,
                "hooks": {
                    "PreToolUse": [{"command": "new"}],
                    "PostToolUse": [{"command": "post"}],
                },
            },
        },
        {
            "config_path": "invalid.json",
            "config_snippet": {"hooks": {"Stop": [{"command": "replacement"}]}},
        },
    ]
    monkeypatch.setattr(hook.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(hook.client, "get", Mock(return_value=_hook_item()))
    monkeypatch.setattr(hook.client, "post", Mock(side_effect=responses))
    upsert = Mock()
    monkeypatch.setattr(lockfile, "upsert_standalone", upsert)

    valid = runner.invoke(
        app,
        ["registry", "hook", "install", "guard", "--harness", "cursor", "--dir", str(project)],
    )
    invalid = runner.invoke(
        app,
        ["registry", "hook", "install", "guard", "--harness", "cursor", "--dir", str(project)],
    )

    assert valid.exit_code == 0, valid.output
    assert invalid.exit_code == 7
    assert "Updated" in valid.output
    assert "not valid JSON" in invalid.output
    merged = json.loads(valid_path.read_text(encoding="utf-8"))
    assert merged == {
        "other": True,
        "version": 2,
        "hooks": {
            "PreToolUse": [{"command": "existing"}, {"command": "new"}],
            "Stop": [{"command": "stop"}],
            "PostToolUse": [{"command": "post"}],
        },
    }
    assert invalid_path.read_text(encoding="utf-8") == "{broken"
    upsert.assert_called_once()


def test_install_surfaces_generation_failure_without_writing(tmp_path, monkeypatch):
    project = tmp_path / "project"
    monkeypatch.setattr(hook.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(hook.client, "get", Mock(return_value=_hook_item()))
    monkeypatch.setattr(hook.client, "post", Mock(side_effect=RuntimeError("generation failed")))

    result = runner.invoke(
        app,
        ["registry", "hook", "install", "guard", "--harness", "kiro", "--dir", str(project)],
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Error (unexpected)" in result.output
    assert "Run observal registry hook install" in result.output
    assert not project.exists()


def test_edit_flags_acquire_lock_and_send_updates(monkeypatch):
    resolve = Mock(return_value="resolved")
    post = Mock(return_value={})
    put = Mock(return_value={"name": "new-guard", "status": "draft"})
    monkeypatch.setattr(hook.client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(hook.client, "post", post)
    monkeypatch.setattr(hook.client, "put", put)

    result = runner.invoke(
        app,
        [
            "registry",
            "hook",
            "edit",
            "alice/guard",
            "--name",
            "new-guard",
            "--description",
            "Updated guard",
            "--version",
            "2.0.0",
            "--event",
            "Stop",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Updated new-guard" in result.output
    resolve.assert_called_once_with("hook", "alice/guard")
    post.assert_called_once_with("/api/v1/hooks/resolved/start-edit")
    put.assert_called_once_with(
        "/api/v1/hooks/resolved/draft",
        {
            "name": "new-guard",
            "description": "Updated guard",
            "version": "2.0.0",
            "event": "Stop",
        },
    )


def test_edit_from_file_and_parse_failures(tmp_path, monkeypatch):
    valid_source = tmp_path / "updates.json"
    valid_updates = {"description": "From file", "handler_config": {"command": "python new.py"}}
    valid_source.write_text(json.dumps(valid_updates), encoding="utf-8")
    broken_source = tmp_path / "broken.json"
    broken_source.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(hook.client, "resolve_registry_reference", Mock(return_value="resolved"))
    post = Mock(return_value={})
    put = Mock(return_value={"name": "guard", "status": "pending"})
    monkeypatch.setattr(hook.client, "post", post)
    monkeypatch.setattr(hook.client, "put", put)

    valid = runner.invoke(
        app,
        ["registry", "hook", "edit", "guard", "--from-file", str(valid_source)],
    )
    missing = runner.invoke(
        app,
        ["registry", "hook", "edit", "guard", "--from-file", str(tmp_path / "missing.json")],
    )
    broken = runner.invoke(
        app,
        ["registry", "hook", "edit", "guard", "--from-file", str(broken_source)],
    )

    assert valid.exit_code == 0, valid.output
    assert missing.exit_code == 5
    assert broken.exit_code == 7
    assert "not found" in missing.output
    assert "not valid JSON" in broken.output
    put.assert_called_once_with("/api/v1/hooks/resolved/draft", valid_updates)
    post.assert_called_once_with("/api/v1/hooks/resolved/start-edit")


def test_edit_no_changes_conflict_and_failed_save_cancellation(monkeypatch):
    monkeypatch.setattr(hook.client, "resolve_registry_reference", Mock(return_value="resolved"))
    post = Mock()
    put = Mock()
    monkeypatch.setattr(hook.client, "post", post)
    monkeypatch.setattr(hook.client, "put", put)

    no_changes = runner.invoke(app, ["registry", "hook", "edit", "guard"])
    assert no_changes.exit_code == 7
    assert "No hook changes" in no_changes.output
    post.assert_not_called()

    post.side_effect = CliError(
        ErrorCategory.CONFLICT,
        "The hook is currently being edited.",
        operation="Edit hook",
        resource="hook registry",
    )
    conflict = runner.invoke(
        app,
        ["registry", "hook", "edit", "guard", "--description", "new"],
    )
    assert conflict.exit_code == 6
    assert "currently being edited" in conflict.output
    put.assert_not_called()

    post.reset_mock(side_effect=True)
    post.return_value = {}
    monkeypatch.setattr(hook.client, "post", post)
    monkeypatch.setattr(
        hook.client,
        "put",
        Mock(
            side_effect=CliError(
                ErrorCategory.UNAVAILABLE,
                "Registry unavailable.",
                operation="Edit hook",
                resource="hook registry",
            )
        ),
    )
    failed = runner.invoke(
        app,
        ["registry", "hook", "edit", "guard", "--description", "new"],
    )

    assert failed.exit_code == 9
    assert "Registry unavailable" in failed.output
    post.assert_called_once_with("/api/v1/hooks/resolved/start-edit")


def test_hook_archive_unarchive_and_confirmation_cancellation(monkeypatch):
    resolve = Mock(return_value="resolved")
    get = Mock(return_value={"name": "Guard Hook"})
    patch = Mock(return_value={})
    monkeypatch.setattr(hook.client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(hook.client, "get", get)
    monkeypatch.setattr(hook.client, "patch", patch)

    archived = runner.invoke(app, ["registry", "hook", "archive", "alice/guard", "--yes"])
    restored = runner.invoke(app, ["registry", "hook", "unarchive", "alice/guard"], input="y\n")
    cancelled = runner.invoke(app, ["registry", "hook", "archive", "alice/guard"], input="n\n")

    assert archived.exit_code == restored.exit_code == 0
    assert cancelled.exit_code == 1
    assert "Hook archived" in archived.output
    assert "Hook restored" in restored.output
    assert "Aborted" in cancelled.output
    assert resolve.call_args_list == [
        call("hooks", "alice/guard"),
        call("hooks", "alice/guard"),
        call("hooks", "alice/guard"),
    ]
    assert get.call_count == 2
    assert patch.call_args_list == [
        call("/api/v1/hooks/resolved/archive"),
        call("/api/v1/hooks/resolved/unarchive"),
    ]


def test_hook_co_author_commands_render_and_preserve_http_boundaries(monkeypatch):
    get = Mock(
        side_effect=[
            [{"email": "dev@example.com", "username": "dev", "is_active": False}],
            [],
        ]
    )
    post = Mock(return_value={"email": "dev@example.com", "username": "dev"})
    delete = Mock(return_value={})
    monkeypatch.setattr(hook.client, "get", get)
    monkeypatch.setattr(hook.client, "post", post)
    monkeypatch.setattr(hook.client, "delete", delete)

    listed = runner.invoke(app, ["registry", "hook", "co-authors", "list", "hook-1"])
    empty = runner.invoke(app, ["registry", "hook", "co-authors", "list", "hook-1"])
    added = runner.invoke(
        app,
        ["registry", "hook", "co-authors", "add", "hook-1", "DEV@EXAMPLE.COM"],
    )
    removed = runner.invoke(
        app,
        [
            "registry",
            "hook",
            "co-authors",
            "remove",
            "hook-1",
            "22222222-2222-2222-2222-222222222222",
        ],
    )

    assert all(result.exit_code == 0 for result in (listed, empty, added, removed))
    assert "dev@example.com" in listed.output
    assert any(cell.strip() == "no" for line in listed.output.splitlines() for cell in line.split("│"))
    assert "No co-authors" in empty.output
    assert "Added co-author" in added.output
    assert "Co-author removed" in removed.output
    assert get.call_args_list == [
        call("/api/v1/hooks/hook-1/co-authors"),
        call("/api/v1/hooks/hook-1/co-authors"),
    ]
    post.assert_called_once_with(
        "/api/v1/hooks/hook-1/co-authors",
        json_data={"email": "dev@example.com"},
    )
    delete.assert_called_once_with("/api/v1/hooks/hook-1/co-authors/22222222-2222-2222-2222-222222222222")


def test_hook_submit_json_is_noninteractive_and_clean(monkeypatch):
    monkeypatch.setattr(
        hook.client,
        "post",
        Mock(return_value={"id": "hook-1", "name": "guard", "status": "pending"}),
    )

    result = runner.invoke(
        app,
        [
            "registry",
            "hook",
            "submit",
            "--name",
            "guard",
            "--description",
            "Guard prompts",
            "--event",
            "Stop",
            "--handler-command",
            "guard.py",
            "--execution-mode",
            "sync",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["id"] == "hook-1"


def test_hook_install_json_is_idempotent(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    response = {
        "files": [{"path": "hooks/guard.py", "content": "print('guard')\n", "executable": True}],
        "config_path": ".claude/settings.json",
        "config_snippet": {"hooks": {"Stop": [{"command": "python hooks/guard.py"}]}},
    }
    monkeypatch.setattr(hook.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(hook.client, "get", Mock(return_value=_hook_item()))
    monkeypatch.setattr(hook.client, "post", Mock(return_value=response))
    upsert = Mock()
    monkeypatch.setattr(lockfile, "upsert_standalone", upsert)

    first = runner.invoke(
        app,
        ["registry", "hook", "install", "guard", "--harness", "claude-code", "--dir", str(project), "--output", "json"],
    )
    second = runner.invoke(
        app,
        ["registry", "hook", "install", "guard", "--harness", "claude-code", "--dir", str(project), "--output", "json"],
    )

    assert first.exit_code == second.exit_code == 0
    assert json.loads(first.stdout)["config_path"].endswith(".claude/settings.json")
    saved = json.loads((project / ".claude/settings.json").read_text())
    assert saved["hooks"]["Stop"] == [{"command": "python hooks/guard.py"}]
    assert upsert.call_count == 2


def test_hook_install_rejects_path_traversal_before_writes(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    response = {"files": [{"path": "../escape.py", "content": "bad"}]}
    monkeypatch.setattr(hook.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(hook.client, "get", Mock(return_value=_hook_item()))
    monkeypatch.setattr(hook.client, "post", Mock(return_value=response))
    upsert = Mock()
    monkeypatch.setattr(lockfile, "upsert_standalone", upsert)

    result = runner.invoke(
        app,
        ["registry", "hook", "install", "guard", "--harness", "claude-code", "--dir", str(project)],
    )

    assert result.exit_code == 7
    assert not (tmp_path / "escape.py").exists()
    upsert.assert_not_called()


def test_hook_edit_json_returns_server_result(monkeypatch):
    monkeypatch.setattr(hook.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(hook.client, "post", Mock(return_value={}))
    monkeypatch.setattr(
        hook.client,
        "put",
        Mock(return_value={"id": "hook-1", "name": "guard", "status": "pending"}),
    )

    result = runner.invoke(
        app,
        ["registry", "hook", "edit", "guard", "--description", "Updated", "--output", "json"],
    )

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "pending"


@pytest.mark.parametrize(
    "arguments",
    [
        ["list", "--event", "Unknown"],
        ["install", "guard", "--harness", "unknown"],
        ["install", "guard", "--harness", "claude-code", "--platform", "plan9"],
        ["install", "guard", "--harness", "claude-code", "--raw", "--output", "json"],
    ],
)
def test_hook_validation_uses_stable_exit_code(arguments, monkeypatch):
    get = Mock()
    monkeypatch.setattr(hook.client, "get", get)

    result = runner.invoke(app, ["registry", "hook", *arguments])

    assert result.exit_code == 7
    get.assert_not_called()


def test_install_sends_the_requested_version_and_records_what_was_installed(tmp_path, monkeypatch):
    import observal_cli.lockfile as lockfile

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(hook.client, "resolve_registry_reference", Mock(return_value="hook-1"))
    monkeypatch.setattr(
        hook.client, "get", Mock(return_value={"id": "hook-1", "name": "Guard", "namespace": "alice", "slug": "guard"})
    )
    post = Mock(
        return_value={"config_snippet": {}, "files": [], "version": "1.0.0", "version_id": "v-1", "digest": "sha256:x"}
    )
    monkeypatch.setattr(hook.client, "post", post)
    monkeypatch.setattr(lockfile, "local_registry_name", Mock(return_value="guard"))
    upsert = Mock()
    monkeypatch.setattr(lockfile, "upsert_standalone", upsert)

    result = runner.invoke(
        app,
        [
            "registry",
            "hook",
            "install",
            "guard",
            "--harness",
            "claude-code",
            "--dir",
            str(project),
            "--version",
            "1.0.0",
        ],
    )
    invalid = runner.invoke(
        app, ["registry", "hook", "install", "guard", "--harness", "claude-code", "--version", "not-a-version"]
    )

    assert result.exit_code == 0, result.output
    assert post.call_args.args[1]["version"] == "1.0.0"
    recorded = upsert.call_args.kwargs
    assert (recorded["version"], recorded["version_id"], recorded["digest"], recorded["requested_version"]) == (
        "1.0.0",
        "v-1",
        "sha256:x",
        "1.0.0",
    )
    assert invalid.exit_code == 7
