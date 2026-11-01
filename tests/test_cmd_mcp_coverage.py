# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import builtins
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import typer
from typer.testing import CliRunner

import observal_cli.cmd_mcp as mcp
from observal_cli import lockfile
from observal_cli.errors import CliError, ErrorCategory
from observal_cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_spinners(monkeypatch):
    monkeypatch.setattr(mcp, "spinner", lambda *_args, **_kwargs: nullcontext())


def _registry_item(**overrides):
    item = {
        "id": "mcp-1",
        "name": "Search MCP",
        "namespace": "alice",
        "slug": "search-mcp",
        "qualified_name": "alice/search-mcp",
        "version": "1.2.3",
        "latest_version": "1.2.3",
        "status": "approved",
        "category": "search",
        "description": "Searches documentation",
        "supported_harnesses": ["cursor", "kiro"],
        "git_url": "https://github.com/acme/search-mcp",
        "setup_instructions": "Run setup",
        "changelog": "Initial release",
        "created_at": "2026-01-01T00:00:00Z",
        "environment_variables": [],
        "headers": [],
    }
    item.update(overrides)
    return item


def test_parse_env_file_and_interactive_env_entry(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\n API_KEY = secret\nlower=value\nTOKEN\n\n", encoding="utf-8")

    assert mcp._parse_env_file(str(env_file)) == [
        {"name": "API_KEY", "description": "", "required": True},
        {"name": "TOKEN", "description": "", "required": True},
    ]
    with pytest.raises(CliError) as missing:
        mcp._parse_env_file(str(tmp_path / "missing.env"))
    assert missing.value.category is ErrorCategory.NOT_FOUND

    answers = iter(["api_key", "API key", "optional", "", ""])
    monkeypatch.setattr(mcp, "text_input", lambda *_args, **_kwargs: next(answers))
    confirms = iter([True, False])
    monkeypatch.setattr(mcp.typer, "confirm", lambda *_args, **_kwargs: next(confirms))

    assert mcp._enter_env_vars_manually() == [
        {"name": "API_KEY", "description": "API key", "required": True},
        {"name": "OPTIONAL", "description": "", "required": False},
    ]


def test_review_env_vars_supports_remove_optional_and_add(monkeypatch):
    answers = iter(["r", "o", "Optional token", "", "extra_key", "Extra key", ""])
    monkeypatch.setattr(mcp, "text_input", lambda *_args, **_kwargs: next(answers))
    monkeypatch.setattr(mcp.typer, "confirm", lambda *_args, **_kwargs: False)

    reviewed = mcp._review_env_vars(
        [
            {"name": "REMOVE", "description": "", "required": True},
            {"name": "OPTIONAL", "description": "", "required": True},
            {"name": "KEEP", "description": "Existing", "required": True},
        ]
    )

    assert reviewed == [
        {"name": "OPTIONAL", "description": "Optional token", "required": False},
        {"name": "KEEP", "description": "Existing", "required": True},
        {"name": "EXTRA_KEY", "description": "Extra key", "required": False},
    ]


def test_configure_env_vars_non_tty_paths(tmp_path, monkeypatch):
    detected = [{"name": "TOKEN", "description": "", "required": True}]
    monkeypatch.setattr(mcp.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(mcp, "text_input", Mock(return_value="1"))
    review = Mock(side_effect=lambda env_vars: env_vars)
    monkeypatch.setattr(mcp, "_review_env_vars", review)

    assert mcp._configure_env_vars_interactive(detected) == detected
    review.assert_called_once_with(detected)

    env_file = tmp_path / ".env.example"
    env_file.write_text("KEY=\n", encoding="utf-8")
    answers = iter(["1", str(env_file)])
    monkeypatch.setattr(mcp, "text_input", lambda *_args, **_kwargs: next(answers))
    assert mcp._configure_env_vars_interactive([]) == [{"name": "KEY", "description": "", "required": True}]


def test_configure_env_vars_tty_manual_skip_and_empty_file(monkeypatch):
    monkeypatch.setattr(mcp.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    choice = {"value": "Enter manually"}
    monkeypatch.setattr(mcp, "select_one", lambda *_args, **_kwargs: choice["value"])
    manual = Mock(return_value=[{"name": "MANUAL", "description": "", "required": True}])
    monkeypatch.setattr(mcp, "_enter_env_vars_manually", manual)

    assert mcp._configure_env_vars_interactive([]) == manual.return_value

    choice["value"] = "Skip (no env vars)"
    assert mcp._configure_env_vars_interactive([]) == []

    choice["value"] = "Load from .env file"
    monkeypatch.setattr(mcp, "text_input", Mock(return_value="missing"))
    monkeypatch.setattr(mcp, "_parse_env_file", Mock(return_value=[]))
    assert mcp._configure_env_vars_interactive([]) == []

    detected = [{"name": "TOKEN", "description": "", "required": True}]
    choice["value"] = "Review auto-detected vars"
    review = Mock(return_value=detected)
    monkeypatch.setattr(mcp, "_review_env_vars", review)
    assert mcp._configure_env_vars_interactive(detected) == detected
    review.assert_called_once_with(detected)


def test_submit_direct_remote_draft_with_git_source(monkeypatch):
    cfg = {
        "mcpServers": {
            "remote-search": {
                "type": "streamable-http",
                "url": "https://mcp.example.com",
                "headers": {"Authorization": "Bearer $TOKEN"},
                "env": {"REGION": "$REGION"},
                "autoApprove": ["search"],
            }
        }
    }
    analysis = {
        "tools": [{"name": "search"}],
        "issues": ["review scopes"],
        "framework": "remote",
        "entry_point": "server.py",
        "command": None,
        "args": None,
        "docker_image": "ghcr.io/acme/search:latest",
        "setup_instructions": "docker build -t search .",
    }
    monkeypatch.setattr(mcp, "analyze_local", Mock(return_value=analysis))
    monkeypatch.setattr(mcp.config, "load", Mock(return_value={"username": "alice"}))
    publish_target = Mock()
    monkeypatch.setattr(mcp.client, "add_publish_target", publish_target)
    calls = []

    def post(path, payload):
        calls.append((path, payload))
        return {
            "id": "draft-1",
            "status": "draft",
            "namespace": "alice",
            "slug": "remote-search",
        }

    monkeypatch.setattr(mcp.client, "post", post)

    result = runner.invoke(
        app,
        [
            "registry",
            "mcp",
            "submit",
            "--git",
            "https://github.com/acme/search",
            "--yes",
            "--draft",
            "--team",
            "platform",
            "--visibility",
            "team",
        ],
        input=json.dumps(cfg) + "\n\n",
    )

    assert result.exit_code == 0, result.output
    assert "Draft saved" in result.output
    assert calls[0][0] == "/api/v1/mcps/draft"
    payload = calls[0][1]
    assert payload == {
        "name": "remote-search",
        "version": "0.1.0",
        "category": "general",
        "description": "remote-search",
        "owner": "alice",
        "supported_harnesses": list(mcp.VALID_HARNESSES),
        "environment_variables": [
            {"name": "REGION", "description": "", "required": True},
            {"name": "TOKEN", "description": "", "required": True},
        ],
        "git_url": "https://github.com/acme/search",
        "setup_instructions": "docker build -t search .",
        "docker_image": "ghcr.io/acme/search:latest",
        "url": "https://mcp.example.com",
        "headers": [
            {
                "name": "Authorization",
                "value": "Bearer $TOKEN",
                "description": "",
                "required": True,
            }
        ],
        "auto_approve": ["search"],
        "transport": "streamable-http",
        "client_analysis": {
            "tools": [{"name": "search"}],
            "issues": ["review scopes"],
            "framework": "remote",
            "entry_point": "server.py",
            "command": None,
            "args": None,
            "docker_image": "ghcr.io/acme/search:latest",
        },
    }
    publish_target.assert_called_once_with(payload, "platform", "team")


def test_submit_direct_interactive_reviews_dependencies(monkeypatch):
    cfg = {
        "mcpServers": {
            "detected": {
                "command": "docker",
                "args": ["run", "$TOKEN", "image:latest"],
            }
        }
    }
    inputs = iter([json.dumps(cfg), ""])
    monkeypatch.setattr(builtins, "input", lambda: next(inputs))
    monkeypatch.setattr(mcp.typer, "confirm", Mock(return_value=True))
    descriptions = iter([" ", "Useful MCP"])

    def text_input(prompt, default=None):
        if prompt == "Server name":
            return "renamed"
        if prompt.startswith("Description"):
            return next(descriptions)
        raise AssertionError(prompt)

    monkeypatch.setattr(mcp, "text_input", text_input)
    monkeypatch.setattr(mcp, "select_one", Mock(return_value="developer-tools"))
    reviewed = [{"name": "TOKEN", "description": "Access token", "required": False}]
    review = Mock(return_value=reviewed)
    monkeypatch.setattr(mcp, "_review_env_vars", review)
    monkeypatch.setattr(mcp.config, "load", Mock(return_value={"username": "alice"}))
    monkeypatch.setattr(mcp.client, "add_publish_target", Mock())
    post = Mock(return_value={"id": "mcp-2", "status": "pending"})
    monkeypatch.setattr(mcp.client, "post", post)

    mcp._submit_impl(None, None, None, False, direct_config=True)

    payload = post.call_args.args[1]
    assert payload["name"] == "renamed"
    assert payload["description"] == "Useful MCP"
    assert payload["category"] == "developer-tools"
    assert payload["environment_variables"] == reviewed
    review.assert_called_once()


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        ([EOFError], "No MCP configuration was provided"),
        (["{not-json", ""], "not valid JSON"),
    ],
)
def test_submit_direct_rejects_missing_or_invalid_json(lines, message, monkeypatch, capsys):
    effects = iter(lines)

    def read_line():
        value = next(effects)
        if value is EOFError:
            raise EOFError
        return value

    monkeypatch.setattr(builtins, "input", read_line)

    with pytest.raises(typer.Exit) as exc:
        mcp._submit_impl(None, None, None, True, direct_config=True)

    assert exc.value.exit_code == 7
    assert message in capsys.readouterr().out


def test_submit_direct_recovers_terminal_split_json(monkeypatch):
    inputs = iter(['{"command": "np', 'x", "args": []}', ""])
    monkeypatch.setattr(builtins, "input", lambda: next(inputs))
    monkeypatch.setattr(mcp, "analyze_local", Mock(return_value={"error": "not a repository"}))
    monkeypatch.setattr(mcp.config, "load", Mock(return_value={"username": "alice"}))
    monkeypatch.setattr(mcp.client, "add_publish_target", Mock())
    post = Mock(return_value={"id": "mcp-3", "status": "pending"})
    monkeypatch.setattr(mcp.client, "post", post)

    mcp._submit_impl(
        "https://github.com/acme/split",
        "split",
        "general",
        True,
        direct_config=True,
    )

    assert post.call_args.args[1]["command"] == "npx"
    assert post.call_args.args[1]["git_url"] == "https://github.com/acme/split"


def test_submit_direct_decline_aborts_before_http(monkeypatch):
    inputs = iter([json.dumps({"command": "npx", "args": []}), ""])
    monkeypatch.setattr(builtins, "input", lambda: next(inputs))
    monkeypatch.setattr(mcp.typer, "confirm", Mock(return_value=False))
    post = Mock()
    monkeypatch.setattr(mcp.client, "post", post)

    with pytest.raises(typer.Abort):
        mcp._submit_impl(None, "declined", "general", False, direct_config=True)

    post.assert_not_called()


def test_git_analysis_yes_mode_submits_complete_payload(monkeypatch):
    tools = [{"name": f"tool-{index}", "docstring": "docs"} for index in range(11)]
    tools[0] = {"name": "undocumented"}
    prefill = {
        "name": "git-mcp",
        "description": "D" * 90,
        "version": "2.4.0",
        "framework": "go-mcp-sdk",
        "tools": tools,
        "environment_variables": [
            {"name": "EXISTING", "description": "", "required": True},
            "LEGACY",
        ],
        "issues": ["one warning"],
        "command": "custom-command",
        "args": ["serve", "$NEW_TOKEN"],
        "docker_image": "ghcr.io/acme/git-mcp:2.4.0",
        "docker_image_suggested": True,
        "setup_instructions": "make setup\nmake run",
        "entry_point": "main.go",
    }
    monkeypatch.setattr(mcp, "analyze_local", Mock(return_value=prefill))
    monkeypatch.setattr(mcp.config, "load", Mock(return_value={"username": "alice"}))
    monkeypatch.setattr(mcp.client, "add_publish_target", Mock())
    post = Mock(return_value={"id": "mcp-git", "status": "pending"})
    monkeypatch.setattr(mcp.client, "post", post)

    mcp._submit_impl("https://github.com/acme/git-mcp", None, None, True)

    endpoint, payload = post.call_args.args
    assert endpoint == "/api/v1/mcps/submit"
    assert payload["name"] == "git-mcp"
    assert payload["version"] == "2.4.0"
    assert payload["framework"] == "go"
    assert payload["docker_image"] == "ghcr.io/acme/git-mcp:2.4.0"
    assert payload["command"] == "custom-command"
    assert payload["args"] == ["serve", "$NEW_TOKEN"]
    assert payload["environment_variables"][-1]["name"] == "NEW_TOKEN"
    assert payload["client_analysis"]["tools"] == tools


def test_git_analysis_interactive_edits_startup_config(monkeypatch):
    prefill = {
        "name": "detected",
        "description": "Detected description",
        "version": "1.0.0",
        "framework": "docker",
        "tools": [],
        "environment_variables": [],
        "issues": [],
        "command": "docker",
        "args": ["run", "old:image"],
        "docker_image": "old:image",
        "docker_image_suggested": True,
        "setup_instructions": "old setup",
    }
    monkeypatch.setattr(mcp, "analyze_local", Mock(return_value=prefill))
    responses = {
        "Startup config looks correct? [Y/n/edit]": "edit",
        "Command": "npx",
        "Args (space-separated)": "-y package $TOKEN",
        "Setup instructions (optional, press Enter to skip)": "new setup",
        "Changelog": "Changed startup",
    }

    def text_input(prompt, default=None):
        return responses[prompt]

    monkeypatch.setattr(mcp, "text_input", text_input)
    monkeypatch.setattr(mcp, "select_one", Mock(return_value="developer-tools"))
    configured = Mock(return_value=[{"name": "TOKEN", "description": "Token", "required": True}])
    monkeypatch.setattr(mcp, "_configure_env_vars_interactive", configured)
    monkeypatch.setattr(mcp.config, "load", Mock(return_value={"username": "alice"}))
    monkeypatch.setattr(mcp.client, "add_publish_target", Mock())
    post = Mock(return_value={"id": "edited", "status": "pending"})
    monkeypatch.setattr(mcp.client, "post", post)

    mcp._submit_impl("https://github.com/acme/detected", "override", None, False)

    payload = post.call_args.args[1]
    assert payload["name"] == "override"
    assert payload["description"] == "Detected description"
    assert payload["command"] == "npx"
    assert payload["args"] == ["-y", "package", "$TOKEN"]
    assert payload["framework"] == "typescript"
    assert payload["setup_instructions"] == "new setup"
    assert payload["changelog"] == "Changed startup"
    assert payload["environment_variables"] == configured.return_value
    detected_for_review = configured.call_args.args[0]
    assert detected_for_review == [{"name": "TOKEN", "description": "", "required": True}]


def test_git_analysis_interactive_supplies_missing_fields(monkeypatch):
    monkeypatch.setattr(mcp, "analyze_local", Mock(return_value={}))
    responses = {
        "Command (e.g. docker, python, npx - Enter to skip)": "docker",
        "Args (space-separated)": "run -i image:latest",
        "Server name": "manual-mcp",
        "Description (what does this server do?)": "Manual description",
        "Setup instructions (optional, press Enter to skip)": "",
        "Changelog": "Initial release",
    }
    monkeypatch.setattr(mcp, "text_input", lambda prompt, default=None: responses[prompt])
    monkeypatch.setattr(mcp, "select_one", Mock(return_value="general"))
    monkeypatch.setattr(mcp, "_configure_env_vars_interactive", Mock(return_value=[]))
    monkeypatch.setattr(mcp.config, "load", Mock(return_value={"username": "alice"}))
    monkeypatch.setattr(mcp.client, "add_publish_target", Mock())
    post = Mock(return_value={"id": "manual", "status": "pending"})
    monkeypatch.setattr(mcp.client, "post", post)

    mcp._submit_impl("https://github.com/acme/manual", None, None, False)

    payload = post.call_args.args[1]
    assert payload["name"] == "manual-mcp"
    assert payload["description"] == "Manual description"
    assert payload["framework"] == "docker"
    assert payload["docker_image"] == "image:latest"


def test_git_analysis_falls_back_to_server(monkeypatch):
    monkeypatch.setattr(mcp, "analyze_local", Mock(return_value={"error": "git failed"}))
    monkeypatch.setattr(mcp.config, "load", Mock(return_value={"username": "alice"}))
    monkeypatch.setattr(mcp.client, "add_publish_target", Mock())
    calls = []

    def post(path, payload):
        calls.append((path, payload))
        if path.endswith("/analyze"):
            return {
                "name": "server-result",
                "description": "Analyzed remotely",
                "version": "1.0.0",
                "framework": "python-mcp",
                "command": "python",
                "args": ["-m", "server"],
            }
        return {"id": "fallback", "status": "pending"}

    monkeypatch.setattr(mcp.client, "post", post)

    mcp._submit_impl("https://github.com/acme/fallback", None, None, True)

    assert calls[0] == (
        "/api/v1/mcps/analyze",
        {"git_url": "https://github.com/acme/fallback"},
    )
    assert calls[1][1]["name"] == "server-result"
    assert "client_analysis" not in calls[1][1]


def test_git_analysis_handles_local_and_remote_failure(monkeypatch, capsys):
    analyze = Mock(side_effect=OSError("clone failed"))
    monkeypatch.setattr(mcp, "analyze_local", analyze)
    monkeypatch.setattr(mcp.config, "load", Mock(return_value={"username": "alice"}))
    monkeypatch.setattr(mcp.client, "add_publish_target", Mock())
    submitted = []

    def post(path, payload):
        if path.endswith("/analyze"):
            raise SystemExit(1)
        submitted.append(payload)
        return {"id": "manual", "status": "pending"}

    monkeypatch.setattr(mcp.client, "post", post)

    mcp._submit_impl("https://github.com/acme/unavailable", "manual", "general", True)

    assert len(submitted) == 1
    assert submitted[0]["name"] == "manual"
    assert submitted[0]["git_url"] == "https://github.com/acme/unavailable"
    assert submitted[0]["category"] == "general"
    assert "Could not analyze repo. Fill in details manually." in capsys.readouterr().out

    analyze.side_effect = None
    analyze.return_value = {"error": "parse failed"}
    mcp._submit_impl("https://github.com/acme/unavailable", "manual", "general", True)

    assert len(submitted) == 2
    assert submitted[1]["name"] == "manual"
    assert submitted[1]["git_url"] == "https://github.com/acme/unavailable"
    assert submitted[1]["category"] == "general"
    output = capsys.readouterr().out
    assert "Local analysis issue: parse failed" in output
    assert "Server analysis also failed. Fill in details manually." in output


@pytest.mark.parametrize(
    ("analysis", "framework"),
    [
        ({"command": "npx", "args": []}, "typescript"),
        ({"command": "custom", "args": [], "framework": "TypeScript SDK"}, "typescript"),
        ({"command": "custom", "args": [], "framework": "Docker"}, "docker"),
        ({"command": "custom", "args": [], "framework": "Ruby"}, "python"),
        ({"framework": "TypeScript SDK"}, "typescript"),
        ({"framework": "Go SDK"}, "go"),
        ({"framework": "Docker"}, "docker"),
        ({"framework": "Ruby"}, "python"),
        ({"entry_point": "server.py"}, "python"),
    ],
)
def test_git_analysis_framework_inference(analysis, framework, monkeypatch):
    prefill = {
        "name": "framework-mcp",
        "description": "Framework test",
        "version": "1.0.0",
        **analysis,
    }
    monkeypatch.setattr(mcp, "analyze_local", Mock(return_value=prefill))
    monkeypatch.setattr(mcp.config, "load", Mock(return_value={"username": "alice"}))
    monkeypatch.setattr(mcp.client, "add_publish_target", Mock())
    post = Mock(return_value={"id": "framework", "status": "pending"})
    monkeypatch.setattr(mcp.client, "post", post)

    mcp._submit_impl("https://github.com/acme/framework", None, None, True)

    assert post.call_args.args[1]["framework"] == framework


def test_git_analysis_startup_rejection_aborts(monkeypatch):
    monkeypatch.setattr(
        mcp,
        "analyze_local",
        Mock(
            return_value={
                "name": "rejected",
                "description": "Rejected startup",
                "command": "npx",
                "args": ["package"],
            }
        ),
    )
    monkeypatch.setattr(mcp, "text_input", Mock(return_value="n"))

    with pytest.raises(typer.Abort):
        mcp._submit_impl("https://github.com/acme/rejected", None, None, False)


def test_git_analysis_error_or_quality_rejection_aborts(monkeypatch):
    monkeypatch.setattr(mcp, "analyze_local", Mock(return_value={"error": "local"}))
    monkeypatch.setattr(
        mcp.client,
        "post",
        Mock(return_value={"error": "remote analysis failed"}),
    )
    monkeypatch.setattr(mcp.typer, "confirm", Mock(return_value=False))

    with pytest.raises(typer.Abort):
        mcp._submit_impl("https://github.com/acme/bad", None, None, False)

    monkeypatch.setattr(
        mcp,
        "analyze_local",
        Mock(return_value={"name": "warned", "issues": ["missing docs"]}),
    )
    with pytest.raises(typer.Abort):
        mcp._submit_impl("https://github.com/acme/warned", None, None, False)


def test_list_json_filters_sorts_limits_and_caches(monkeypatch):
    data = [
        _registry_item(id="2", name="Zulu", category="z"),
        _registry_item(id="1", name="Alpha", category="a"),
    ]
    get = Mock(return_value=data)
    monkeypatch.setattr(mcp.client, "get", get)
    monkeypatch.setattr(mcp.client, "resolve_team_id", Mock(return_value="team-1"))
    save = Mock()
    monkeypatch.setattr(mcp.config, "save_last_results", save)

    result = runner.invoke(
        app,
        [
            "registry",
            "mcp",
            "list",
            "--category",
            "search",
            "--search",
            "docs",
            "--namespace",
            "@Alice",
            "--team",
            "platform",
            "--sort",
            "category",
            "--limit",
            "1",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    rendered = json.loads(result.output)
    assert [item["name"] for item in rendered["items"]] == ["Alpha"]
    assert {key: rendered[key] for key in ("total", "page", "page_size")} == {
        "total": 1,
        "page": 1,
        "page_size": 1,
    }
    get.assert_called_once_with(
        "/api/v1/mcps",
        params={
            "category": "search",
            "search": "docs",
            "namespace": "alice",
            "team_id": "team-1",
        },
    )
    save.assert_called_once_with([data[1]], "mcp")


def test_list_table_empty_and_interactive(monkeypatch, capsys):
    save = Mock()
    monkeypatch.setattr(mcp.config, "save_last_results", save)
    item = _registry_item()

    monkeypatch.setattr(mcp.client, "get", Mock(return_value=[]))
    mcp._list_impl(None, None, 50, "name", "table")
    assert "No MCP servers found" in capsys.readouterr().out

    monkeypatch.setattr(mcp.client, "get", Mock(return_value=[item]))
    mcp._list_impl(None, None, 50, "version", "table")
    assert "mcp-1" in capsys.readouterr().out

    mcp._list_impl(None, None, 50, "unknown", "table")
    assert "MCP Servers" in capsys.readouterr().out

    show = Mock()
    monkeypatch.setattr(mcp, "_show_impl", show)

    def select(data, display, label):
        assert data == [item]
        assert "search-mcp" in display(item)
        assert label == "Select MCP server"
        return item

    monkeypatch.setattr(mcp, "fuzzy_select", select)
    mcp._list_impl(None, None, 50, "name", "table", interactive=True)
    show.assert_called_once_with("mcp-1", "table")

    show.reset_mock()
    monkeypatch.setattr(mcp, "fuzzy_select", Mock(return_value=None))
    mcp._list_impl(None, None, 50, "name", "table", interactive=True)
    show.assert_not_called()


def test_my_mcp_outputs_reject_plain_and_empty_state(monkeypatch):
    item = _registry_item()
    get = Mock(side_effect=[[], [item], [item]])
    monkeypatch.setattr(mcp.client, "get", get)
    save = Mock()
    monkeypatch.setattr(mcp.config, "save_last_results", save)

    empty = runner.invoke(app, ["registry", "mcp", "my"])
    plain = runner.invoke(app, ["registry", "mcp", "my", "--output", "plain"])
    as_json = runner.invoke(app, ["registry", "mcp", "my", "--output", "json"])
    table = runner.invoke(app, ["registry", "mcp", "my"])

    assert empty.exit_code == as_json.exit_code == table.exit_code == 0
    assert plain.exit_code == 2
    assert "Error" in plain.output
    assert "plain" in plain.output
    assert "You have no MCP servers" in empty.output
    assert json.loads(as_json.output)["items"][0]["id"] == "mcp-1"
    assert "My MCPs" in table.output
    assert save.call_count == 3


def test_show_renders_validation_and_json(monkeypatch):
    item = _registry_item(
        validation_results=[
            {"stage": "schema", "passed": True, "details": ""},
            {"stage": "security", "passed": False, "details": "blocked"},
        ]
    )
    resolve = Mock(return_value="resolved-id")
    get = Mock(return_value=item)
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(mcp.client, "get", get)

    table = runner.invoke(app, ["registry", "mcp", "show", "alice/search-mcp"])
    as_json = runner.invoke(
        app,
        ["registry", "mcp", "show", "alice/search-mcp", "--output", "json"],
    )

    assert table.exit_code == as_json.exit_code == 0
    assert "Validation" in table.output
    assert "schema: passed" in table.output
    assert "security: blocked" in table.output
    assert json.loads(as_json.output)["id"] == "mcp-1"
    assert get.call_args_list[0].args == ("/api/v1/mcps/resolved-id",)
    resolve.assert_any_call("mcp", "alice/search-mcp")


def test_show_surfaces_http_failure(monkeypatch):
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", Mock(return_value="missing"))
    monkeypatch.setattr(mcp.client, "get", Mock(side_effect=typer.Exit(1)))

    result = runner.invoke(app, ["registry", "mcp", "show", "missing"])

    assert result.exit_code == 1


def test_install_raw_passes_version_env_headers_and_placeholders(monkeypatch):
    listing = _registry_item(
        environment_variables=[
            {"name": "API_KEY", "description": "API key", "required": True},
            {"name": "OPTIONAL", "description": "Optional", "required": False},
        ],
        headers=[
            {"name": "Authorization", "description": "Bearer", "required": True},
            {"name": "X-Optional", "description": "Optional", "required": False},
        ],
    )
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(mcp.client, "get", Mock(return_value=listing))
    monkeypatch.setattr(lockfile, "local_registry_name", Mock(return_value="search-mcp"))
    post = Mock(return_value={"config_snippet": {"mcpServers": {"search-mcp": {}}}})
    monkeypatch.setattr(mcp.client, "post", post)

    result = runner.invoke(
        app,
        [
            "registry",
            "mcp",
            "install",
            "alice/search-mcp",
            "--harness",
            "cursor",
            "--raw",
            "--version",
            "1.1.0",
            "--env",
            "API_KEY='secret'",
            "--header",
            'Authorization="Bearer token"',
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"mcpServers": {"search-mcp": {}}}
    post.assert_called_once_with(
        "/api/v1/mcps/resolved/install",
        {
            "harness": "cursor",
            "local_name": "search-mcp",
            "env_values": {"API_KEY": "secret", "OPTIONAL": "<OPTIONAL>"},
            "header_values": {
                "Authorization": "Bearer token",
                "X-Optional": "<X-Optional>",
            },
            "version": "1.1.0",
        },
    )


def test_install_interactive_prompts_without_claiming_local_install(monkeypatch, capsys):
    listing = _registry_item(
        environment_variables=[
            {"name": "REQ_FLAG", "description": "Flag", "required": True},
            {"name": "REQ_PROMPT", "description": "Prompt", "required": True},
            {"name": "OPT_FLAG", "description": "Flag", "required": False},
            {"name": "OPT_SKIP", "description": "Skip", "required": False},
        ],
        headers=[
            {"name": "H_FLAG", "description": "Flag", "required": True},
            {"name": "H_PROMPT", "description": "Prompt", "required": True},
            {"name": "H_OPT_FLAG", "description": "Flag", "required": False},
            {"name": "H_OPT_PROMPT", "description": "Prompt", "required": False},
        ],
    )
    answers = {
        "REQ_PROMPT": "prompt-secret",
        "OPT_SKIP": "optional-prompt-secret",
        "H_PROMPT": "prompt-header",
        "H_OPT_PROMPT": "optional-header",
    }
    monkeypatch.setattr(
        mcp,
        "text_input",
        lambda prompt, default=None: next(value for key, value in answers.items() if key in prompt),
    )
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(mcp.client, "get", Mock(return_value=listing))
    monkeypatch.setattr(lockfile, "local_registry_name", Mock(return_value="search-mcp"))
    upsert = Mock()
    monkeypatch.setattr(lockfile, "upsert_standalone", upsert)
    post = Mock(
        return_value={
            "config_snippet": {"mcpServers": {"search-mcp": {"command": "npx"}}},
            "warnings": ["Run setup before use"],
        }
    )
    monkeypatch.setattr(mcp.client, "post", post)

    mcp._install_impl(
        "search",
        "cursor",
        False,
        env_overrides={"REQ_FLAG": "flag-secret", "OPT_FLAG": "optional-secret"},
        header_overrides={"H_FLAG": "flag-header", "H_OPT_FLAG": "optional-flag-header"},
    )

    body = post.call_args.args[1]
    assert body["env_values"] == {
        "REQ_FLAG": "flag-secret",
        "REQ_PROMPT": "prompt-secret",
        "OPT_FLAG": "optional-secret",
        "OPT_SKIP": "optional-prompt-secret",
    }
    assert body["header_values"] == {
        "H_FLAG": "flag-header",
        "H_PROMPT": "prompt-header",
        "H_OPT_FLAG": "optional-flag-header",
        "H_OPT_PROMPT": "optional-header",
    }
    upsert.assert_not_called()
    output = capsys.readouterr().out
    assert "Add to:" in output
    assert "Warning:" in output
    assert output.count("Run setup") == 1


def test_install_env_file_no_prompt_warns_without_lockfile_write(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text('FILE_TOKEN="from-file"\n# comment\n', encoding="utf-8")
    listing = _registry_item(
        setup_instructions="Run migrations",
        environment_variables=[
            {"name": "FILE_TOKEN", "description": "Token", "required": True},
            {"name": "MISSING", "description": "Missing", "required": False},
        ],
    )
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(mcp.client, "get", Mock(return_value=listing))
    monkeypatch.setattr(lockfile, "local_registry_name", Mock(return_value="search-mcp"))
    monkeypatch.setattr(lockfile, "upsert_standalone", Mock(side_effect=OSError("read only")))
    post = Mock(return_value={"config_snippet": {"mcp_servers": {}}, "warnings": []})
    monkeypatch.setattr(mcp.client, "post", post)

    mcp._install_impl(
        "search",
        "codex",
        False,
        env_file=str(env_file),
        no_prompt=True,
    )

    assert post.call_args.args[1]["env_values"] == {
        "FILE_TOKEN": "from-file",
        "MISSING": "<MISSING>",
    }
    output = capsys.readouterr().out
    assert "~/.codex/config.toml" in output
    assert "Run migrations" in output
    assert "MISSING" in output


def test_edit_all_flags(monkeypatch):
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(
        mcp.client,
        "get",
        Mock(return_value={"id": "resolved", "name": "old", "status": "draft"}),
    )

    def post(path, *args, **kwargs):
        if path.endswith("/start-edit"):
            return {}
        raise AssertionError(path)

    monkeypatch.setattr(mcp.client, "post", post)
    put = Mock(return_value={"name": "new", "status": "draft"})
    monkeypatch.setattr(mcp.client, "put", put)

    result = runner.invoke(
        app,
        [
            "registry",
            "mcp",
            "edit",
            "old",
            "--name",
            "new",
            "--description",
            "New description",
            "--category",
            "databases",
            "--version",
            "2.0.0",
            "--git-url",
            "https://github.com/acme/new",
            "--command",
            "npx",
            "--url",
            "https://mcp.example.com",
        ],
    )

    assert result.exit_code == 0, result.output
    put.assert_called_once_with(
        "/api/v1/mcps/resolved/draft",
        {
            "name": "new",
            "description": "New description",
            "category": "databases",
            "version": "2.0.0",
            "git_url": "https://github.com/acme/new",
            "command": "npx",
            "url": "https://mcp.example.com",
        },
    )


def test_edit_approved_uses_requested_bump_and_exact_version_payload(monkeypatch):
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(
        mcp.client,
        "get",
        Mock(
            return_value={
                "id": "resolved",
                "name": "old",
                "description": "Old description",
                "status": "approved",
                "version": "3.4.5",
            }
        ),
    )
    post = Mock(return_value={"name": "old"})
    monkeypatch.setattr(mcp.client, "post", post)

    result = runner.invoke(
        app,
        [
            "registry",
            "mcp",
            "edit",
            "old",
            "--name",
            "ignored-name",
            "--description",
            "Version description",
            "--command",
            "python",
            "--bump",
            "major",
            "--changelog",
            "Breaking protocol change",
        ],
    )

    assert result.exit_code == 0, result.output
    post.assert_called_once_with(
        "/api/v1/mcps/resolved/versions",
        {
            "version": "4.0.0",
            "description": "Version description",
            "extra": {"command": "python"},
            "changelog": "Breaking protocol change",
        },
    )


def test_edit_split_json_and_no_parsed_changes(monkeypatch):
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(
        mcp.client,
        "get",
        Mock(return_value={"id": "resolved", "name": "old", "status": "draft"}),
    )
    monkeypatch.setattr(mcp.client, "post", Mock(return_value={}))
    put = Mock(return_value={"name": "old", "status": "draft"})
    monkeypatch.setattr(mcp.client, "put", put)

    split = runner.invoke(
        app,
        ["registry", "mcp", "edit", "old"],
        input='{"command": "np\nx", "args": []}\n\ny\n',
    )
    assert split.exit_code == 0, split.output
    assert put.call_args.args[1]["command"] == "npx"

    empty = runner.invoke(
        app,
        ["registry", "mcp", "edit", "old"],
        input="{}\n\ny\n",
    )
    assert empty.exit_code == 7
    assert "No MCP changes could be parsed" in empty.output


def test_edit_save_failure_preserves_category(monkeypatch):
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(
        mcp.client,
        "get",
        Mock(return_value={"id": "resolved", "name": "old", "status": "draft"}),
    )
    post = Mock(return_value={})
    monkeypatch.setattr(mcp.client, "post", post)
    unavailable = CliError(
        ErrorCategory.UNAVAILABLE,
        "Registry unavailable.",
        operation="Edit MCP server",
        resource="MCP registry",
    )
    monkeypatch.setattr(mcp.client, "put", Mock(side_effect=unavailable))

    result = runner.invoke(
        app,
        ["registry", "mcp", "edit", "old", "--description", "new"],
    )

    assert result.exit_code == 9
    post.assert_called_once_with("/api/v1/mcps/resolved/start-edit")


def test_mcp_co_author_commands_include_delete_boundary(monkeypatch):
    get = Mock(return_value=[{"email": "dev@example.com", "username": "dev", "is_active": False}])
    post = Mock(return_value={"email": "dev@example.com", "username": "dev"})
    delete = Mock(return_value={})
    monkeypatch.setattr(mcp.client, "get", get)
    monkeypatch.setattr(mcp.client, "post", post)
    monkeypatch.setattr(mcp.client, "delete", delete)

    listed = runner.invoke(app, ["registry", "mcp", "co-authors", "list", "mcp-1"])
    added = runner.invoke(
        app,
        ["registry", "mcp", "co-authors", "add", "mcp-1", "DEV@EXAMPLE.COM"],
    )
    removed = runner.invoke(
        app,
        [
            "registry",
            "mcp",
            "co-authors",
            "remove",
            "mcp-1",
            "22222222-2222-2222-2222-222222222222",
        ],
    )

    assert listed.exit_code == added.exit_code == removed.exit_code == 0
    assert "dev@example.com" in listed.output
    get.assert_called_once_with("/api/v1/mcps/mcp-1/co-authors")
    post.assert_called_once_with(
        "/api/v1/mcps/mcp-1/co-authors",
        json_data={"email": "dev@example.com"},
    )
    delete.assert_called_once_with("/api/v1/mcps/mcp-1/co-authors/22222222-2222-2222-2222-222222222222")


def test_mcp_archive_is_the_destructive_listing_lifecycle(monkeypatch):
    resolve = Mock(return_value="resolved")
    patch = Mock(return_value={})
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(mcp.client, "patch", patch)

    archived = runner.invoke(app, ["registry", "mcp", "archive", "alice/search", "--yes"])
    restored = runner.invoke(app, ["registry", "mcp", "unarchive", "alice/search", "--yes"])

    assert archived.exit_code == restored.exit_code == 0
    assert patch.call_args_list[0].args == ("/api/v1/mcps/resolved/archive",)
    assert patch.call_args_list[1].args == ("/api/v1/mcps/resolved/unarchive",)


def test_submit_json_is_clean_and_returns_server_result(monkeypatch):
    monkeypatch.setattr(mcp.client, "post", Mock(return_value={"id": "mcp-1", "name": "search", "status": "pending"}))

    result = runner.invoke(
        app,
        [
            "registry",
            "mcp",
            "submit",
            "--yes",
            "--name",
            "search",
            "--category",
            "search",
            "--output",
            "json",
        ],
        input='{"command":"npx","args":["server"]}\n\n',
    )

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout) == {"id": "mcp-1", "name": "search", "status": "pending"}


@pytest.mark.parametrize(
    ("field", "kind", "requirements"),
    [
        ("environment_variables", "environment_variable", {}),
        ("environment_variables", "environment_variable", ""),
        ("environment_variables", "environment_variable", False),
        ("environment_variables", "environment_variable", 0),
        ("environment_variables", "environment_variable", ["not-an-object"]),
        ("environment_variables", "environment_variable", [{}]),
        ("environment_variables", "environment_variable", [{"name": ""}]),
        ("headers", "header", {}),
        ("headers", "header", ""),
        ("headers", "header", False),
        ("headers", "header", 0),
    ],
)
def test_install_json_rejects_malformed_server_requirements(monkeypatch, field, kind, requirements):
    listing = _registry_item(**{field: requirements})
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(mcp.client, "get", Mock(return_value=listing))
    post = Mock()
    monkeypatch.setattr(mcp.client, "post", post)

    result = runner.invoke(
        app,
        [
            "registry",
            "mcp",
            "install",
            "alice/search",
            "--harness",
            "cursor",
            "--no-prompt",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 9
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["result"]["invalid_input_kind"] == kind
    post.assert_not_called()


def test_install_json_missing_required_values_returns_needs_input_without_mutation(monkeypatch):
    listing = _registry_item(
        environment_variables=[{"name": "API_KEY", "required": True}],
        headers=[{"name": "Authorization", "required": True}],
    )
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(mcp.client, "get", Mock(return_value=listing))
    post = Mock()
    monkeypatch.setattr(mcp.client, "post", post)

    result = runner.invoke(
        app,
        [
            "registry",
            "mcp",
            "install",
            "alice/search",
            "--harness",
            "cursor",
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
            {"kind": "environment_variable", "name": "API_KEY"},
            {"kind": "header", "name": "Authorization"},
        ],
    }
    assert "fake-secret-value" not in result.stderr
    post.assert_not_called()


def test_install_json_reads_required_values_from_requested_version(monkeypatch):
    listing = _registry_item(environment_variables=[])
    pinned = {"version": "1.0.0", "environment_variables": [{"name": "OLD_KEY", "required": True}]}
    get = Mock(side_effect=lambda path, *args, **kwargs: pinned if path.endswith("/versions/1.0.0") else listing)
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(mcp.client, "get", get)
    post = Mock()
    monkeypatch.setattr(mcp.client, "post", post)

    result = runner.invoke(
        app,
        [
            "registry",
            "mcp",
            "install",
            "alice/search",
            "--harness",
            "cursor",
            "--version",
            "1.0.0",
            "--no-prompt",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 7
    assert json.loads(result.stderr)["error"]["result"]["inputs"] == [
        {"kind": "environment_variable", "name": "OLD_KEY"}
    ]
    post.assert_not_called()


def test_install_json_accepts_all_required_values_without_echoing_them(monkeypatch):
    listing = _registry_item(
        environment_variables=[{"name": "API_KEY", "required": True}],
        headers=[{"name": "Authorization", "required": True}],
    )
    response = {"config_snippet": {"mcpServers": {"search": {"command": "npx"}}}}
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(mcp.client, "get", Mock(return_value=listing))
    post = Mock(return_value=response)
    monkeypatch.setattr(mcp.client, "post", post)
    monkeypatch.setattr(lockfile, "local_registry_name", Mock(return_value="search"))

    result = runner.invoke(
        app,
        [
            "registry",
            "mcp",
            "install",
            "alice/search",
            "--harness",
            "cursor",
            "--env",
            "API_KEY=fake-secret-value",
            "--header",
            "Authorization=Bearer fake-secret-value",
            "--no-prompt",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == response
    assert "fake-secret-value" not in result.stdout
    assert post.call_args.args[1]["env_values"] == {"API_KEY": "fake-secret-value"}
    assert post.call_args.args[1]["header_values"] == {"Authorization": "Bearer fake-secret-value"}


def test_install_json_treats_null_requirement_fields_as_empty(monkeypatch):
    listing = _registry_item(environment_variables=None, headers=None)
    response = {"config_snippet": {"mcpServers": {"search": {}}}}
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(mcp.client, "get", Mock(return_value=listing))
    monkeypatch.setattr(mcp.client, "post", Mock(return_value=response))
    monkeypatch.setattr(lockfile, "local_registry_name", Mock(return_value="search"))

    result = runner.invoke(
        app,
        [
            "registry",
            "mcp",
            "install",
            "alice/search",
            "--harness",
            "cursor",
            "--no-prompt",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == response


def test_install_json_allows_missing_optional_values(monkeypatch):
    listing = _registry_item(
        environment_variables=[{"name": "OPTIONAL", "required": False}],
        headers=[{"name": "X-Optional", "required": False}],
    )
    response = {"config_snippet": {"mcpServers": {"search": {}}}}
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(mcp.client, "get", Mock(return_value=listing))
    monkeypatch.setattr(mcp.client, "post", Mock(return_value=response))
    monkeypatch.setattr(lockfile, "local_registry_name", Mock(return_value="search"))

    result = runner.invoke(
        app,
        [
            "registry",
            "mcp",
            "install",
            "alice/search",
            "--harness",
            "cursor",
            "--no-prompt",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == response


def test_install_json_returns_operation_result_without_lockfile_write(monkeypatch):
    listing = _registry_item()
    response = {"config_snippet": {"mcpServers": {"search": {"command": "npx"}}}, "warnings": []}
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(mcp.client, "get", Mock(return_value=listing))
    monkeypatch.setattr(mcp.client, "post", Mock(return_value=response))
    monkeypatch.setattr(lockfile, "local_registry_name", Mock(return_value="search"))
    upsert = Mock()
    monkeypatch.setattr(lockfile, "upsert_standalone", upsert)

    result = runner.invoke(
        app,
        [
            "registry",
            "mcp",
            "install",
            "alice/search",
            "--harness",
            "cursor",
            "--no-prompt",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout) == response
    upsert.assert_not_called()


def test_edit_reviewed_successor_amends_instead_of_creating_sibling(monkeypatch):
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(
        mcp.client,
        "get",
        Mock(return_value={"status": "pending", "version": "1.1.0", "description": "Before edit"}),
    )
    post = Mock(return_value={"status": "pending", "version": "1.1.0"})
    put = Mock(
        return_value={"id": "mcp-1", "name": "search", "version": "1.1.0", "status": "pending", "review_revision": 2}
    )
    monkeypatch.setattr(mcp.client, "post", post)
    monkeypatch.setattr(mcp.client, "put", put)

    result = runner.invoke(
        app,
        ["registry", "mcp", "edit", "alice/search", "--description", "Corrected", "--output", "json"],
    )

    assert result.exit_code == 0, result.stderr
    put.assert_called_once_with("/api/v1/mcps/resolved/draft", {"description": "Corrected"})
    assert [call.args[0] for call in post.call_args_list] == [
        "/api/v1/mcps/resolved/start-edit",
        "/api/v1/mcps/resolved/submit",
    ]
    assert json.loads(result.stdout)["version"] == "1.1.0"


def test_edit_json_is_noninteractive_and_returns_server_result(monkeypatch):
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(mcp.client, "get", Mock(return_value={"status": "draft"}))
    monkeypatch.setattr(mcp.client, "post", Mock(return_value={}))
    monkeypatch.setattr(mcp.client, "put", Mock(return_value={"id": "mcp-1", "name": "search", "status": "draft"}))

    result = runner.invoke(
        app,
        ["registry", "mcp", "edit", "alice/search", "--description", "Updated", "--output", "json"],
    )

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "draft"


@pytest.mark.parametrize(
    "arguments",
    [
        ["list", "--category", "ai"],
        ["list", "--sort", "unknown"],
        ["install", "alice/search", "--harness", "unknown"],
        ["install", "alice/search", "--harness", "cursor", "--env", "MISSING_SEPARATOR"],
        ["install", "alice/search", "--harness", "cursor", "--raw", "--output", "json"],
        ["install", "alice/search", "--harness", "cursor", "--output", "json"],
    ],
)
def test_mcp_validation_uses_stable_exit_code(arguments, monkeypatch):
    get = Mock()
    monkeypatch.setattr(mcp.client, "get", get)

    result = runner.invoke(app, ["registry", "mcp", *arguments])

    assert result.exit_code == 7
    get.assert_not_called()


def test_mcp_table_and_detail_escape_registry_markup(monkeypatch):
    hostile = "Clean [/tmp] array[0] [bold]literal[/bold]"
    item = _registry_item(name=hostile, slug=hostile, description=hostile)
    monkeypatch.setattr(mcp.client, "get", Mock(side_effect=[[item], item]))
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", Mock(return_value="resolved"))

    listed = runner.invoke(app, ["registry", "mcp", "list"])
    shown = runner.invoke(app, ["registry", "mcp", "show", "resolved"])

    assert listed.exit_code == shown.exit_code == 0
    assert hostile in listed.output
    assert hostile in shown.output


def test_approved_mcp_edit_json_requires_and_uses_explicit_bump(monkeypatch):
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(
        mcp.client,
        "get",
        Mock(return_value={"status": "approved", "version": "1.2.3", "description": "Old"}),
    )
    post = Mock(return_value={"id": "mcp-1", "name": "search", "version": "1.3.0"})
    monkeypatch.setattr(mcp.client, "post", post)

    missing = runner.invoke(
        app,
        ["registry", "mcp", "edit", "alice/search", "--description", "New", "--output", "json"],
    )
    published = runner.invoke(
        app,
        [
            "registry",
            "mcp",
            "edit",
            "alice/search",
            "--description",
            "New",
            "--bump",
            "minor",
            "--changelog",
            "Changed",
            "--output",
            "json",
        ],
    )

    assert missing.exit_code == 7
    assert published.exit_code == 0
    assert json.loads(published.stdout)["version"] == "1.3.0"
    assert post.call_args.args[1]["version"] == "1.3.0"
