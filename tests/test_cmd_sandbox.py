# SPDX-FileCopyrightText: 2026 Abdul Moiz Hussain <abdulmoizx97@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Behavioral coverage for the sandbox registry CLI."""

from __future__ import annotations

import json
from contextlib import nullcontext
from unittest.mock import MagicMock, call, mock_open

import pytest
from typer.testing import CliRunner

from observal_cli import client, cmd_sandbox, config
from observal_cli.constants import VALID_SANDBOX_RUNTIME_TYPES
from observal_cli.main import app

runner = CliRunner()
COMMAND = ("registry", "sandbox")
VALID_SUBMIT = (
    "--name",
    "runner",
    "--description",
    "Run tests",
    "--runtime-type",
    "docker",
    "--image",
    "python:3.12",
)

EXAMPLES = {
    "python-pytest": {
        "name": "python-pytest",
        "version": "1.0.0",
        "description": "Run Python tests in a reviewed Docker image",
        "owner": "your-team",
        "runtime_type": "docker",
        "image": "python:3.12-slim",
        "resource_limits": {"timeout": 60, "memory_mb": 512, "cpu_count": 1},
        "network_policy": "none",
        "entrypoint": "pytest",
        "runtime_config": {},
        "source_url": "https://github.com/docker-library/python",
        "source_ref": "master",
        "sandbox_path": "3.12/slim-bookworm",
    },
    "node-tests": {
        "name": "node-tests",
        "version": "1.0.0",
        "description": "Run Node test and build commands",
        "owner": "your-team",
        "runtime_type": "docker",
        "image": "node:22-alpine",
        "resource_limits": {"timeout": 120, "memory_mb": 1024, "cpu_count": 2},
        "network_policy": "none",
        "entrypoint": "npm test",
        "runtime_config": {},
        "source_url": "https://github.com/nodejs/docker-node",
        "source_ref": "main",
        "sandbox_path": "22/alpine3.22",
    },
    "go-tests": {
        "name": "go-tests",
        "version": "1.0.0",
        "description": "Run Go tests in an Alpine Go image",
        "owner": "your-team",
        "runtime_type": "docker",
        "image": "golang:1.24-alpine",
        "resource_limits": {"timeout": 180, "memory_mb": 1024, "cpu_count": 2},
        "network_policy": "none",
        "entrypoint": "go test ./...",
        "runtime_config": {},
        "source_url": "https://github.com/docker-library/golang",
        "source_ref": "master",
        "sandbox_path": "1.24/alpine3.21",
    },
}


def invoke(*args: str):
    return runner.invoke(app, [*COMMAND, *args])


@pytest.fixture(autouse=True)
def _boundaries(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Keep each command at its network, config, prompt, and render boundaries."""

    def unexpected(*_args, **_kwargs):
        raise AssertionError("unexpected external boundary call")

    config_load = MagicMock(return_value={"username": "alice"})
    monkeypatch.setenv("OBSERVAL_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr("observal_cli.main._migrate_legacy_mcp_configs", lambda: None)
    monkeypatch.setattr("observal_cli.main._try_lockfile_migration", lambda: None)
    monkeypatch.setattr("observal_cli.optic.setup_optic", lambda **_kwargs: None)
    monkeypatch.setattr(cmd_sandbox, "spinner", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(cmd_sandbox.config, "load", config_load)
    monkeypatch.setattr(cmd_sandbox.config, "save_last_results", unexpected)
    monkeypatch.setattr(cmd_sandbox, "text_input", unexpected)
    monkeypatch.setattr(cmd_sandbox, "select_one", unexpected)
    monkeypatch.setattr(cmd_sandbox, "output_json", unexpected)
    monkeypatch.setattr(cmd_sandbox.console, "print", unexpected)
    for method in ("get", "post", "put", "patch", "delete"):
        monkeypatch.setattr(client, method, unexpected)
    monkeypatch.setattr(client, "resolve_registry_reference", unexpected)
    monkeypatch.setattr(client, "resolve_team_id", unexpected)
    return {"config_load": config_load}


def test_register_sandbox_mounts_the_command_group() -> None:
    parent = MagicMock()

    cmd_sandbox.register_sandbox(parent)

    parent.add_typer.assert_called_once_with(cmd_sandbox.sandbox_app, name="sandbox")


def test_submit_example_renders_exact_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    output_json = MagicMock()
    monkeypatch.setattr(cmd_sandbox, "output_json", output_json)

    result = invoke("submit", "--example")

    assert result.exit_code == 0, result.output
    assert result.output == ""
    output_json.assert_called_once_with(EXAMPLES)


def test_submit_rejects_conflicting_draft_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    resolve = MagicMock()
    post = MagicMock()
    monkeypatch.setattr(client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(client, "post", post)

    result = invoke("submit", "--draft", "--submit", "@draft")

    assert result.exit_code == 1
    assert "Cannot use --draft and --submit together" in result.output
    resolve.assert_not_called()
    post.assert_not_called()


def test_submit_existing_draft_resolves_registry_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    resolve = MagicMock(return_value="sandbox-id")
    post = MagicMock(return_value={"id": "sandbox-id"})
    monkeypatch.setattr(client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(client, "post", post)

    result = invoke("submit", "--submit", "platform/python-runner")

    assert result.exit_code == 0, result.output
    assert "Draft submitted for review!" in result.output
    assert "sandbox-id" in result.output
    resolve.assert_called_once_with("sandbox", "platform/python-runner")
    post.assert_called_once_with("/api/v1/sandboxes/sandbox-id/submit")


@pytest.mark.parametrize(("owner", "expected_owner"), [(None, "alice"), ("publisher", "publisher")])
def test_submit_from_file_posts_exact_payload(
    monkeypatch: pytest.MonkeyPatch,
    _boundaries: dict[str, MagicMock],
    owner: str | None,
    expected_owner: str,
) -> None:
    payload = {
        "name": "python-runner",
        "version": "2.0.0",
        "description": "Run Python",
        "runtime_type": "docker",
        "image": "python:3.12",
        "resource_limits": {"memory_mb": 512},
        "runtime_config": {"workdir": "/workspace"},
        "network_policy": "restricted",
    }
    if owner is not None:
        payload["owner"] = owner
    loaded = dict(payload)
    opener = mock_open()
    load_json = MagicMock(return_value=loaded)
    post = MagicMock(return_value={"id": "sandbox-id", "qualified_name": "alice/python-runner"})
    monkeypatch.setattr("builtins.open", opener)
    monkeypatch.setattr(cmd_sandbox._json, "load", load_json)
    monkeypatch.setattr(client, "post", post)

    result = invoke("submit", "--from-file", "sandbox.json")

    assert result.exit_code == 0, result.output
    assert "Sandbox submitted!" in result.output
    assert "observal registry sandbox install alice/python-runner" in result.output
    opener.assert_called_once_with("sandbox.json")
    load_json.assert_called_once_with(opener.return_value.__enter__.return_value)
    post.assert_called_once_with(
        "/api/v1/sandboxes/submit",
        {**payload, "owner": expected_owner, "visibility": "public"},
    )
    assert _boundaries["config_load"].call_count == (1 if owner is None else 0)


@pytest.mark.parametrize("failure", ["missing", "invalid-json"])
def test_submit_from_file_reports_filesystem_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    opener = mock_open()
    if failure == "missing":
        opener.side_effect = FileNotFoundError
    else:
        monkeypatch.setattr(
            cmd_sandbox._json,
            "load",
            MagicMock(side_effect=json.JSONDecodeError("bad document", "{", 1)),
        )
    post = MagicMock()
    monkeypatch.setattr("builtins.open", opener)
    monkeypatch.setattr(client, "post", post)

    result = invoke("submit", "--from-file", "sandbox.json")

    assert result.exit_code == 1
    expected = "File not found" if failure == "missing" else "Invalid JSON in sandbox.json"
    assert expected in result.output
    post.assert_not_called()


def test_submit_flags_post_complete_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    post = MagicMock(return_value={"id": "sandbox-id", "qualified_name": "alice/node-runner"})
    monkeypatch.setattr(client, "post", post)

    result = invoke(
        "submit",
        "--name",
        "node-runner",
        "--version",
        "3.1.0",
        "--description",
        "Run Node tests",
        "--runtime-type",
        "docker",
        "--image",
        "node:22-alpine",
        "--resource-limits",
        '{"timeout":90,"memory_mb":1024}',
        "--runtime-config",
        '{"workdir":"/repo"}',
        "--network-policy",
        "restricted",
        "--entrypoint",
        "npm test",
        "--harness",
        "claude-code",
        "--harness",
        "pi",
        "--source-url",
        "https://github.com/acme/sandboxes",
        "--source-ref",
        "v3.1.0",
        "--sandbox-path",
        "node",
    )

    assert result.exit_code == 0, result.output
    assert "Sandbox submitted!" in result.output
    assert "sandbox-id" in result.output
    post.assert_called_once_with(
        "/api/v1/sandboxes/submit",
        {
            "name": "node-runner",
            "version": "3.1.0",
            "description": "Run Node tests",
            "owner": "alice",
            "runtime_type": "docker",
            "image": "node:22-alpine",
            "resource_limits": {"timeout": 90, "memory_mb": 1024},
            "runtime_config": {"workdir": "/repo"},
            "network_policy": "restricted",
            "supported_harnesses": ["claude-code", "pi"],
            "entrypoint": "npm test",
            "source_url": "https://github.com/acme/sandboxes",
            "source_ref": "v3.1.0",
            "sandbox_path": "node",
            "visibility": "public",
        },
    )


def test_submit_draft_targets_a_teamspace(monkeypatch: pytest.MonkeyPatch) -> None:
    resolve_team = MagicMock(return_value="team-id")
    post = MagicMock(return_value={"id": "draft-id", "qualified_name": "platform/runner"})
    monkeypatch.setattr(client, "resolve_team_id", resolve_team)
    monkeypatch.setattr(client, "post", post)

    result = invoke("submit", *VALID_SUBMIT, "--draft", "--team", "@Platform", "--visibility", "team")

    assert result.exit_code == 0, result.output
    assert "Draft saved!" in result.output
    resolve_team.assert_called_once_with("@Platform")
    post.assert_called_once_with(
        "/api/v1/sandboxes/draft",
        {
            "name": "runner",
            "version": "1.0.0",
            "description": "Run tests",
            "owner": "alice",
            "runtime_type": "docker",
            "image": "python:3.12",
            "resource_limits": {},
            "runtime_config": {},
            "network_policy": "none",
            "supported_harnesses": [],
            "visibility": "team",
            "team_id": "team-id",
        },
    )


def test_submit_interactive_uses_prompt_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    text_input = MagicMock(
        side_effect=[
            "interactive-runner",
            "1.4.0",
            "Interactive sandbox",
            "python:3.12",
            '{"timeout":45}',
            '{"workdir":"/src"}',
        ]
    )
    select_one = MagicMock(return_value="docker")
    post = MagicMock(return_value={"id": "sandbox-id", "qualified_name": "alice/interactive-runner"})
    monkeypatch.setattr(cmd_sandbox, "text_input", text_input)
    monkeypatch.setattr(cmd_sandbox, "select_one", select_one)
    monkeypatch.setattr(client, "post", post)

    result = invoke("submit")

    assert result.exit_code == 0, result.output
    text_input.assert_has_calls(
        [
            call("Sandbox name"),
            call("Version", default="1.0.0"),
            call("Description"),
            call("Image"),
            call("Resource limits (JSON)"),
            call("Runtime config (JSON)", default="{}"),
        ]
    )
    select_one.assert_called_once_with("Runtime type", VALID_SANDBOX_RUNTIME_TYPES)
    post.assert_called_once_with(
        "/api/v1/sandboxes/submit",
        {
            "name": "interactive-runner",
            "version": "1.4.0",
            "description": "Interactive sandbox",
            "owner": "alice",
            "runtime_type": "docker",
            "image": "python:3.12",
            "resource_limits": {"timeout": 45},
            "runtime_config": {"workdir": "/src"},
            "visibility": "public",
        },
    )


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--name", "runner"), "--name, --description, --runtime-type, and --image are required"),
        ((*VALID_SUBMIT, "--runtime-type", "process"), "Invalid runtime type: process"),
        ((*VALID_SUBMIT, "--network-policy", "internet"), "Invalid network policy: internet"),
        ((*VALID_SUBMIT, "--harness", "unknown"), "Invalid harness: unknown"),
    ],
)
def test_submit_validates_flag_fields(
    monkeypatch: pytest.MonkeyPatch,
    args: tuple[str, ...],
    message: str,
) -> None:
    post = MagicMock()
    monkeypatch.setattr(client, "post", post)

    result = invoke("submit", *args)

    assert result.exit_code == 1
    assert message in result.output
    post.assert_not_called()


@pytest.mark.parametrize("option", ["--resource-limits", "--runtime-config"])
def test_submit_rejects_malformed_json_options(monkeypatch: pytest.MonkeyPatch, option: str) -> None:
    post = MagicMock()
    monkeypatch.setattr(client, "post", post)

    result = invoke("submit", *VALID_SUBMIT, option, "{")

    assert result.exit_code == 1
    assert "Invalid JSON option" in result.output
    post.assert_not_called()


@pytest.mark.parametrize(
    "target_args",
    [("--visibility", "internal"), ("--visibility", "team")],
)
def test_submit_validates_publish_target(
    monkeypatch: pytest.MonkeyPatch,
    target_args: tuple[str, ...],
) -> None:
    post = MagicMock()
    monkeypatch.setattr(client, "post", post)

    result = invoke("submit", *VALID_SUBMIT, *target_args)

    assert result.exit_code != 0
    expected = "visibility must be" if target_args[-1] == "internal" else "requires --team"
    assert expected in result.output
    post.assert_not_called()


def test_list_empty_result_is_visible_and_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    get = MagicMock(return_value=[])
    save = MagicMock()
    monkeypatch.setattr(client, "get", get)
    monkeypatch.setattr(config, "save_last_results", save)

    result = invoke("list")

    assert result.exit_code == 0, result.output
    assert "No sandboxes found." in result.output
    get.assert_called_once_with("/api/v1/sandboxes", params={})
    save.assert_not_called()


def test_list_filters_and_json_output_use_exact_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    data = [{"id": "sandbox-id", "name": "Runner", "version": "1.0.0"}]
    resolve_team = MagicMock(return_value="team-id")
    get = MagicMock(return_value=data)
    save = MagicMock()
    output_json = MagicMock()
    monkeypatch.setattr(client, "resolve_team_id", resolve_team)
    monkeypatch.setattr(client, "get", get)
    monkeypatch.setattr(config, "save_last_results", save)
    monkeypatch.setattr(cmd_sandbox, "output_json", output_json)

    result = invoke(
        "list",
        "--runtime",
        "docker",
        "--search",
        "python",
        "--namespace",
        "@Alice",
        "--team",
        "platform",
        "--output",
        "json",
    )

    assert result.exit_code == 0, result.output
    resolve_team.assert_called_once_with("platform")
    get.assert_called_once_with(
        "/api/v1/sandboxes",
        params={"runtime": "docker", "search": "python", "namespace": "alice", "team_id": "team-id"},
    )
    save.assert_called_once_with(data)
    output_json.assert_called_once_with(data)


def test_list_plain_output_renders_each_result(monkeypatch: pytest.MonkeyPatch) -> None:
    data = [
        {"id": "one", "name": "Runner", "version": "2.0.0"},
        {"id": "two", "name": "Minimal"},
    ]
    get = MagicMock(return_value=data)
    save = MagicMock()
    name_inline = MagicMock(side_effect=["runner [dim]@alice[/dim]", "minimal"])
    rprint = MagicMock()
    monkeypatch.setattr(client, "get", get)
    monkeypatch.setattr(config, "save_last_results", save)
    monkeypatch.setattr(cmd_sandbox, "name_inline", name_inline)
    monkeypatch.setattr(cmd_sandbox, "rprint", rprint)

    result = invoke("list", "--output", "plain")

    assert result.exit_code == 0, result.output
    get.assert_called_once_with("/api/v1/sandboxes", params={})
    save.assert_called_once_with(data)
    assert rprint.call_args_list == [
        call("one  runner [dim]@alice[/dim]  v2.0.0"),
        call("two  minimal  v?"),
    ]


def test_list_table_builds_exact_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    data = [
        {
            "id": "123456789abcdef",
            "slug": "runner",
            "namespace": "alice",
            "version": "2.0.0",
            "status": "approved",
        }
    ]
    table = MagicMock()
    table_type = MagicMock(return_value=table)
    console_print = MagicMock()
    get = MagicMock(return_value=data)
    save = MagicMock()
    display_name = MagicMock(return_value="runner")
    handle = MagicMock(return_value="@alice")
    status_badge = MagicMock(return_value="APPROVED")
    monkeypatch.setattr(client, "get", get)
    monkeypatch.setattr(config, "save_last_results", save)
    monkeypatch.setattr(cmd_sandbox, "Table", table_type)
    monkeypatch.setattr(cmd_sandbox.console, "print", console_print)
    monkeypatch.setattr(cmd_sandbox, "display_name", display_name)
    monkeypatch.setattr(cmd_sandbox, "handle", handle)
    monkeypatch.setattr(cmd_sandbox, "status_badge", status_badge)

    result = invoke("list")

    assert result.exit_code == 0, result.output
    table_type.assert_called_once_with(title="Sandboxes (1)", show_lines=False, padding=(0, 1))
    assert table.add_column.call_args_list == [
        call("#", style="dim", width=3),
        call("Name", style="bold cyan", no_wrap=True),
        call("Version", style="green"),
        call("Namespace", style="dim"),
        call("Status"),
        call("ID", style="dim", max_width=12),
    ]
    table.add_row.assert_called_once_with("1", "runner", "2.0.0", "@alice", "APPROVED", "12345678…")
    console_print.assert_called_once_with(table)
    save.assert_called_once_with(data)


def test_show_json_resolves_alias_and_uses_json_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    item = {"id": "sandbox-id", "name": "Runner"}
    resolve = MagicMock(return_value="sandbox-id")
    get = MagicMock(return_value=item)
    output_json = MagicMock()
    monkeypatch.setattr(client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(client, "get", get)
    monkeypatch.setattr(cmd_sandbox, "output_json", output_json)

    result = invoke("show", "@runner", "--output", "json")

    assert result.exit_code == 0, result.output
    resolve.assert_called_once_with("sandbox", "@runner")
    get.assert_called_once_with("/api/v1/sandboxes/sandbox-id")
    output_json.assert_called_once_with(item)


def test_show_table_builds_exact_detail_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    item = {
        "id": "sandbox-id",
        "slug": "runner",
        "namespace": "platform",
        "version": "2.0.0",
        "status": "pending",
        "runtime_type": "docker",
        "image": "python:3.12",
        "description": "Run tests",
        "created_at": "2026-08-01T00:00:00Z",
    }
    resolve = MagicMock(return_value="sandbox-id")
    get = MagicMock(return_value=item)
    display_name = MagicMock(return_value="runner")
    status_badge = MagicMock(return_value="PENDING")
    handle = MagicMock(return_value="@platform")
    relative_time = MagicMock(return_value="8d ago")
    panel = object()
    kv_panel = MagicMock(return_value=panel)
    console_print = MagicMock()
    monkeypatch.setattr(client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(client, "get", get)
    monkeypatch.setattr(cmd_sandbox, "display_name", display_name)
    monkeypatch.setattr(cmd_sandbox, "status_badge", status_badge)
    monkeypatch.setattr(cmd_sandbox, "handle", handle)
    monkeypatch.setattr(cmd_sandbox, "relative_time", relative_time)
    monkeypatch.setattr(cmd_sandbox, "kv_panel", kv_panel)
    monkeypatch.setattr(cmd_sandbox.console, "print", console_print)

    result = invoke("show", "platform/runner")

    assert result.exit_code == 0, result.output
    resolve.assert_called_once_with("sandbox", "platform/runner")
    get.assert_called_once_with("/api/v1/sandboxes/sandbox-id")
    kv_panel.assert_called_once_with(
        "runner v2.0.0",
        [
            ("Status", "PENDING"),
            ("Runtime", "docker"),
            ("Image", "python:3.12"),
            ("Namespace", "@platform"),
            ("Description", "Run tests"),
            ("Created", "8d ago"),
            ("ID", "[dim]sandbox-id[/dim]"),
        ],
        border_style="red",
    )
    console_print.assert_called_once_with(panel)


@pytest.mark.parametrize(
    "args",
    [("my",), ("install", "runner", "--harness", "pi")],
    ids=["my", "install"],
)
def test_removed_sandbox_commands_are_not_registered(args: tuple[str, ...]) -> None:
    result = invoke(*args)

    assert result.exit_code == 2
    assert f"No such command '{args[0]}'" in result.output


def test_edit_flags_acquire_lock_and_put_exact_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    resolve = MagicMock(return_value="sandbox-id")
    post = MagicMock(return_value={})
    put = MagicMock(return_value={"name": "renamed", "status": "draft"})
    monkeypatch.setattr(client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(client, "post", post)
    monkeypatch.setattr(client, "put", put)

    result = invoke(
        "edit",
        "@runner",
        "--name",
        "renamed",
        "--description",
        "Updated",
        "--version",
        "2.1.0",
        "--runtime-type",
        "lxc",
        "--image",
        "images:ubuntu/24.04",
        "--resource-limits",
        '{"cpu_count":2}',
        "--runtime-config",
        '{"profile":"safe"}',
        "--network-policy",
        "restricted",
        "--entrypoint",
        "pytest",
    )

    assert result.exit_code == 0, result.output
    assert "Updated renamed (status: draft)" in result.output
    resolve.assert_called_once_with("sandbox", "@runner")
    post.assert_called_once_with("/api/v1/sandboxes/sandbox-id/start-edit")
    put.assert_called_once_with(
        "/api/v1/sandboxes/sandbox-id/draft",
        {
            "name": "renamed",
            "description": "Updated",
            "version": "2.1.0",
            "runtime_type": "lxc",
            "image": "images:ubuntu/24.04",
            "resource_limits": {"cpu_count": 2},
            "runtime_config": {"profile": "safe"},
            "network_policy": "restricted",
            "entrypoint": "pytest",
        },
    )


def test_edit_from_file_puts_exact_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    updates = {"image": "python:3.13", "runtime_config": {"workdir": "/work"}}
    opener = mock_open()
    load_json = MagicMock(return_value=updates)
    resolve = MagicMock(return_value="sandbox-id")
    post = MagicMock(return_value={})
    put = MagicMock(return_value={"name": "runner", "status": "pending"})
    monkeypatch.setattr("builtins.open", opener)
    monkeypatch.setattr(cmd_sandbox._json, "load", load_json)
    monkeypatch.setattr(client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(client, "post", post)
    monkeypatch.setattr(client, "put", put)

    result = invoke("edit", "1", "--from-file", "updates.json")

    assert result.exit_code == 0, result.output
    resolve.assert_called_once_with("sandbox", "1")
    opener.assert_called_once_with("updates.json")
    post.assert_called_once_with("/api/v1/sandboxes/sandbox-id/start-edit")
    put.assert_called_once_with("/api/v1/sandboxes/sandbox-id/draft", updates)


@pytest.mark.parametrize("failure", ["missing", "invalid-json"])
def test_edit_from_file_reports_filesystem_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    opener = mock_open()
    if failure == "missing":
        opener.side_effect = FileNotFoundError
    else:
        monkeypatch.setattr(
            cmd_sandbox._json,
            "load",
            MagicMock(side_effect=json.JSONDecodeError("bad document", "{", 1)),
        )
    resolve = MagicMock(return_value="sandbox-id")
    post = MagicMock()
    monkeypatch.setattr("builtins.open", opener)
    monkeypatch.setattr(client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(client, "post", post)

    result = invoke("edit", "runner", "--from-file", "updates.json")

    assert result.exit_code == 1
    expected = "File not found" if failure == "missing" else "Invalid JSON in updates.json"
    assert expected in result.output
    resolve.assert_called_once_with("sandbox", "runner")
    post.assert_not_called()


def test_edit_without_updates_stops_before_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    resolve = MagicMock(return_value="sandbox-id")
    post = MagicMock()
    monkeypatch.setattr(client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(client, "post", post)

    result = invoke("edit", "runner")

    assert result.exit_code == 1
    assert "No changes specified" in result.output
    resolve.assert_called_once_with("sandbox", "runner")
    post.assert_not_called()


@pytest.mark.parametrize("message", ["409 Conflict", "sandbox is currently being edited by bob"])
def test_edit_lock_conflict_is_visible(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> None:
    resolve = MagicMock(return_value="sandbox-id")
    post = MagicMock(side_effect=RuntimeError(message))
    put = MagicMock()
    monkeypatch.setattr(client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(client, "post", post)
    monkeypatch.setattr(client, "put", put)

    result = invoke("edit", "runner", "--description", "Changed")

    assert result.exit_code == 1
    assert "Cannot edit" in result.output
    assert message in result.output
    post.assert_called_once_with("/api/v1/sandboxes/sandbox-id/start-edit")
    put.assert_not_called()


@pytest.mark.parametrize("cancel_fails", [False, True])
def test_edit_save_failure_attempts_lock_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    cancel_fails: bool,
) -> None:
    resolve = MagicMock(return_value="sandbox-id")
    post = MagicMock(side_effect=[{}, RuntimeError("cancel unavailable")] if cancel_fails else None)
    put = MagicMock(side_effect=RuntimeError("HTTP 503 while saving"))
    monkeypatch.setattr(client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(client, "post", post)
    monkeypatch.setattr(client, "put", put)

    result = invoke("edit", "runner", "--image", "python:3.13")

    assert result.exit_code == 1
    assert "Failed to update" in result.output
    assert "HTTP 503 while saving" in result.output
    assert post.call_args_list == [
        call("/api/v1/sandboxes/sandbox-id/start-edit"),
        call("/api/v1/sandboxes/sandbox-id/cancel-edit"),
    ]
    put.assert_called_once_with("/api/v1/sandboxes/sandbox-id/draft", {"image": "python:3.13"})


def test_archive_yes_resolves_reference_without_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    resolve = MagicMock(return_value="sandbox-id")
    get = MagicMock()
    patch_request = MagicMock(return_value={})
    confirm = MagicMock()
    monkeypatch.setattr(client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(client, "get", get)
    monkeypatch.setattr(client, "patch", patch_request)
    monkeypatch.setattr("observal_cli.cmd_archive.typer.confirm", confirm)

    result = invoke("archive", "platform/runner", "--yes")

    assert result.exit_code == 0, result.output
    assert "Sandbox archived" in result.output
    resolve.assert_called_once_with("sandboxes", "platform/runner")
    get.assert_not_called()
    confirm.assert_not_called()
    patch_request.assert_called_once_with("/api/v1/sandboxes/sandbox-id/archive")


def test_unarchive_confirms_and_restores(monkeypatch: pytest.MonkeyPatch) -> None:
    resolve = MagicMock(return_value="sandbox-id")
    get = MagicMock(return_value={"name": "Runner"})
    patch_request = MagicMock(return_value={})
    confirm = MagicMock(return_value=True)
    monkeypatch.setattr(client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(client, "get", get)
    monkeypatch.setattr(client, "patch", patch_request)
    monkeypatch.setattr("observal_cli.cmd_archive.typer.confirm", confirm)

    result = invoke("unarchive", "@runner")

    assert result.exit_code == 0, result.output
    assert "Sandbox restored" in result.output
    resolve.assert_called_once_with("sandboxes", "@runner")
    get.assert_called_once_with("/api/v1/sandboxes/sandbox-id")
    confirm.assert_called_once_with("Restore sandbox [bold]Runner[/bold] (sandbox-id)?")
    patch_request.assert_called_once_with("/api/v1/sandboxes/sandbox-id/unarchive")


def test_archive_cancellation_does_not_mutate(monkeypatch: pytest.MonkeyPatch) -> None:
    resolve = MagicMock(return_value="sandbox-id")
    get = MagicMock(return_value={"name": "Runner"})
    patch_request = MagicMock()
    confirm = MagicMock(return_value=False)
    monkeypatch.setattr(client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(client, "get", get)
    monkeypatch.setattr(client, "patch", patch_request)
    monkeypatch.setattr("observal_cli.cmd_archive.typer.confirm", confirm)

    result = invoke("archive", "runner")

    assert result.exit_code == 1
    confirm.assert_called_once_with("Archive sandbox [bold]Runner[/bold] (sandbox-id)?")
    patch_request.assert_not_called()


def test_co_author_list_renders_exact_table(monkeypatch: pytest.MonkeyPatch) -> None:
    authors = [{"email": "bob@example.com", "username": "bob", "is_active": False}]
    get = MagicMock(return_value=authors)
    table = MagicMock()
    table_type = MagicMock(return_value=table)
    rprint = MagicMock()
    monkeypatch.setattr(client, "get", get)
    monkeypatch.setattr("observal_cli.cmd_co_authors.Table", table_type)
    monkeypatch.setattr("observal_cli.cmd_co_authors.rprint", rprint)

    result = invoke("co-authors", "list", "sandbox-id")

    assert result.exit_code == 0, result.output
    get.assert_called_once_with("/sandboxes/sandbox-id/co-authors")
    table_type.assert_called_once_with(title="Co-Authors")
    assert table.add_column.call_args_list == [
        call("Email", style="cyan"),
        call("Username", style="green"),
        call("Active", style="dim"),
    ]
    table.add_row.assert_called_once_with("bob@example.com", "bob", "no")
    rprint.assert_called_once_with(table)


@pytest.mark.parametrize(
    ("user", "body"),
    [("Bob@Example.COM", {"email": "bob@example.com"}), ("@Bob", {"username": "Bob"})],
)
def test_co_author_add_normalizes_identity(
    monkeypatch: pytest.MonkeyPatch,
    user: str,
    body: dict[str, str],
) -> None:
    post = MagicMock(return_value={"email": "bob@example.com", "username": "bob"})
    monkeypatch.setattr(client, "post", post)

    result = invoke("co-authors", "add", "sandbox-id", user)

    assert result.exit_code == 0, result.output
    assert "Added co-author" in result.output
    post.assert_called_once_with("/sandboxes/sandbox-id/co-authors", json_data=body)


def test_co_author_remove_calls_exact_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    delete = MagicMock(return_value={})
    monkeypatch.setattr(client, "delete", delete)

    result = invoke("co-authors", "remove", "sandbox-id", "user-id")

    assert result.exit_code == 0, result.output
    assert "Co-author removed" in result.output
    delete.assert_called_once_with("/sandboxes/sandbox-id/co-authors/user-id")
