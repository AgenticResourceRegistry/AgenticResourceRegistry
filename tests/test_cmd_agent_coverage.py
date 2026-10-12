# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Focused coverage for the agent CLI command group."""

from __future__ import annotations

import json
import sys
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
import typer
import yaml
from click import Group
from typer.main import get_command
from typer.testing import CliRunner

import observal_cli.cmd_agent as agent
from observal_cli.main import app as cli_app

runner = CliRunner()


def _item(**overrides):
    item = {
        "id": "12345678-1234-1234-1234-123456789abc",
        "name": "reviewer",
        "slug": "reviewer",
        "namespace": "alice",
        "qualified_name": "alice/reviewer",
        "version": "1.2.3",
        "model_name": "claude-sonnet-4",
        "status": "approved",
        "description": "Reviews changes",
        "supported_harnesses": ["kiro"],
        "created_at": None,
    }
    item.update(overrides)
    return item


def _write_agent_yaml(tmp_path, **overrides):
    data = {
        "name": "reviewer",
        "version": "1.2.3",
        "description": "Reviews changes",
        "owner": "alice",
        "model_name": "claude-sonnet-4",
        "models_by_harness": {"kiro": "claude-haiku-4-5"},
        "prompt": "Review carefully.",
        "supported_harnesses": ["kiro"],
        "components": [{"component_type": "skill", "component_id": "skill-1"}],
        "success_criteria": {"intended_purpose": "Find bugs"},
    }
    data.update(overrides)
    path = tmp_path / agent.YAML_FILE
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return data


def _publish_target(payload, team, visibility):
    payload["visibility"] = visibility or "public"
    if team:
        payload["team_id"] = "team-1"


@pytest.fixture(autouse=True)
def _isolated_boundaries(monkeypatch):
    """Keep command tests away from credentials, caches, and terminal spinners."""
    load = Mock(return_value={"username": "fallback-user"})
    save_last_results = Mock()
    monkeypatch.setattr(agent, "spinner", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(agent.config, "load", load)
    monkeypatch.setattr(agent.config, "save_last_results", save_last_results)
    return SimpleNamespace(load=load, save_last_results=save_last_results)


def _invoke(*args, input=None):
    return runner.invoke(agent.agent_app, list(args), input=input)


def test_helpers_validate_fetch_and_round_trip_yaml(tmp_path, monkeypatch, capsys):
    assert agent._slugify("  Incident !!! Helper  ") == "incident-helper"
    assert agent._validate_name("valid-agent") is None
    assert agent._validate_name("") == "Agent name is required."
    assert "at most 64" in agent._validate_name("a" * 65)
    assert "lowercase" in agent._validate_name("Not.Valid")

    get = Mock(return_value=[{"id": "mcp-1"}])
    monkeypatch.setattr(agent.client, "get", get)
    assert agent._fetch_registry_items("mcp") == [{"id": "mcp-1"}]
    get.assert_called_once_with("/api/v1/mcps")

    get.side_effect = RuntimeError("offline")
    with pytest.raises(RuntimeError, match="offline"):
        agent._fetch_registry_items("skill")
    get.side_effect = typer.Exit(1)
    with pytest.raises(typer.Exit):
        agent._fetch_registry_items("hook")

    data = {"name": "local", "components": []}
    agent._save_agent_yaml(tmp_path / "nested", data)
    assert agent._load_agent_yaml(tmp_path / "nested") == data

    with pytest.raises(typer.Exit) as exc:
        agent._load_agent_yaml(tmp_path / "missing")
    assert exc.value.exit_code == 5
    assert "not found" in capsys.readouterr().out


def test_create_from_json_preserves_payload_and_publish_target(tmp_path, monkeypatch):
    definition = {"name": "from-file", "prompt": "Be useful", "components": []}
    source = tmp_path / "agent.json"
    source.write_text(json.dumps(definition), encoding="utf-8")
    target = Mock(side_effect=_publish_target)
    post = Mock(return_value={"id": "agent-1"})
    monkeypatch.setattr(agent.client, "add_publish_target", target)
    monkeypatch.setattr(agent.client, "post", post)

    result = _invoke(
        "create",
        "--from-file",
        str(source),
        "--team",
        "platform",
        "--visibility",
        "team",
    )

    assert result.exit_code == 0, result.output
    assert "submitted for review" in result.output
    assert "pending" in result.output
    expected_payload = {
        "name": "from-file",
        "prompt": "Be useful",
        "components": [],
        "visibility": "team",
        "team_id": "team-1",
    }
    target.assert_called_once_with(expected_payload, "platform", "team")
    post.assert_called_once_with("/api/v1/agents", expected_payload)


def test_create_from_json_without_scope_skips_publish_target_and_surfaces_http_failure(tmp_path, monkeypatch):
    source = tmp_path / "agent.json"
    source.write_text('{"name": "broken"}', encoding="utf-8")
    target = Mock()
    monkeypatch.setattr(agent.client, "add_publish_target", target)
    monkeypatch.setattr(agent.client, "post", Mock(side_effect=typer.Exit(1)))

    result = _invoke("create", "--from-file", str(source))

    assert result.exit_code == 1
    target.assert_not_called()
    assert "submitted" not in result.output


def test_create_flags_reads_prompt_file_slugifies_and_sends_exact_payload(tmp_path, monkeypatch):
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("Handle incidents.\n", encoding="utf-8")
    get = Mock(return_value={"username": "alice", "email": "ignored@example.test"})
    target = Mock(side_effect=_publish_target)
    post = Mock(return_value={"id": "agent-2", "status": "approved"})
    monkeypatch.setattr(agent.client, "get", get)
    monkeypatch.setattr(agent.client, "add_publish_target", target)
    monkeypatch.setattr(agent.client, "post", post)

    result = _invoke(
        "create",
        "--name",
        "Incident Helper!",
        "--version",
        "2.0.0",
        "--description",
        "Handles incidents",
        "--prompt-file",
        str(prompt_file),
        "--model",
        "gpt-4o",
        "--harness",
        "kiro",
        "--harness",
        "cursor",
    )

    assert result.exit_code == 0, result.output
    assert "Name slugified" in result.output
    get.assert_called_once_with("/api/v1/auth/whoami")
    payload = post.call_args.args[1]
    assert payload == {
        "name": "incident-helper",
        "version": "2.0.0",
        "description": "Handles incidents",
        "owner": "alice",
        "prompt": "Handle incidents.\n",
        "model_name": "gpt-4o",
        "supported_harnesses": ["kiro", "cursor"],
        "components": [],
        "visibility": "public",
    }
    target.assert_called_once_with(payload, None, None)


def test_create_flags_do_not_hide_owner_lookup_failure(monkeypatch):
    monkeypatch.setattr(agent.client, "get", Mock(side_effect=RuntimeError("offline")))
    monkeypatch.setattr(agent.client, "add_publish_target", Mock(side_effect=_publish_target))
    post = Mock(return_value={"id": "agent-3"})
    monkeypatch.setattr(agent.client, "post", post)

    result = _invoke("create", "--name", "helper", "--prompt", "Help")

    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)
    post.assert_not_called()


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("create", "--prompt", "Help"), "name is required"),
        (("create", "--name", "helper"), "prompt or"),
        (("create", "--name", "!!!", "--prompt", "Help"), "name is required"),
        (("create", "--name", "helper", "--prompt-file", "missing.md"), "Prompt file not found"),
    ],
)
def test_create_flag_validation(args, message):
    result = _invoke(*args)
    expected = 5 if "file not found" in message.lower() else 7
    assert result.exit_code == expected
    assert message.lower() in result.output.lower()


def test_create_interactive_builds_components_sections_and_model_config(monkeypatch):
    answers = iter(
        [
            "Interactive Agent",
            "Interactive description",
            "2.1.0",
            "Ship safely",
            "quality",
            "No regressions",
            "done",
            "System prompt",
            "8192",
            "0.4",
        ]
    )
    monkeypatch.setattr(agent, "text_input", lambda *_args, **_kwargs: next(answers))
    monkeypatch.setattr(agent, "select_one", Mock(return_value="gpt-4o"))

    registry = {
        component_type: [{"id": f"{component_type}-1", "name": f"{component_type}-name"}]
        for component_type in agent.VALID_COMPONENT_TYPES
    }
    monkeypatch.setattr(agent, "_fetch_registry_items", lambda component_type: registry[component_type])

    def select_many(label, choices, defaults):
        if "harnesses" in label:
            return ["kiro"]
        return [choices[0], "unmatched  [dim](missing)[/dim]"]

    monkeypatch.setattr(agent, "select_many", select_many)
    monkeypatch.setattr(agent.typer, "confirm", Mock(return_value=True))
    monkeypatch.setattr(agent.client, "get", Mock(return_value={"email": "author@example.test"}))
    target = Mock(side_effect=_publish_target)
    post = Mock(return_value={"id": "interactive-1", "status": "pending"})
    monkeypatch.setattr(agent.client, "add_publish_target", target)
    monkeypatch.setattr(agent.client, "post", post)

    result = _invoke("create", "--team", "platform", "--visibility", "team")

    assert result.exit_code == 0, result.output
    assert "Slugified to" in result.output
    assert "Review" in result.output
    payload = post.call_args.args[1]
    assert payload == {
        "name": "interactive-agent",
        "version": "2.1.0",
        "description": "Interactive description",
        "owner": "author@example.test",
        "prompt": "System prompt",
        "model_name": "gpt-4o",
        "model_config_json": {"max_tokens": 8192, "temperature": 0.4},
        "supported_harnesses": ["kiro"],
        "components": [
            {"component_type": component_type, "component_id": f"{component_type}-1"}
            for component_type in ("mcp", "skill", "hook", "prompt", "sandbox")
        ],
        "visibility": "team",
        "team_id": "team-1",
    }
    target.assert_called_once_with(payload, "platform", "team")


def test_create_interactive_rejects_invalid_name_before_network(monkeypatch):
    monkeypatch.setattr(agent, "text_input", Mock(return_value="!!!"))
    post = Mock()
    monkeypatch.setattr(agent.client, "post", post)

    result = _invoke("create")

    assert result.exit_code == 7
    assert "Agent name is required" in result.output
    post.assert_not_called()


def test_create_interactive_uses_defaults_and_can_be_cancelled(monkeypatch, _isolated_boundaries):
    answers = iter(["helper", "Description", "1.0.0", "Goal", "done", "", "4096", "0.2"])
    monkeypatch.setattr(agent, "text_input", lambda *_args, **_kwargs: next(answers))
    monkeypatch.setattr(agent, "select_one", Mock(return_value="claude-sonnet-4"))
    monkeypatch.setattr(agent, "select_many", Mock(return_value=["cursor"]))
    monkeypatch.setattr(agent, "_fetch_registry_items", Mock(return_value=[]))
    monkeypatch.setattr(agent.typer, "confirm", Mock(return_value=False))
    monkeypatch.setattr(agent.client, "get", Mock(side_effect=RuntimeError("offline")))
    post = Mock()
    monkeypatch.setattr(agent.client, "post", post)
    _isolated_boundaries.load.return_value = {"username": "fallback-user"}

    result = _invoke("create")

    assert result.exit_code == 0
    assert "Using default section" in result.output
    assert "Aborted" in result.output
    post.assert_not_called()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("not json", "Could not read JSON"),
        (json.dumps({"wrong": []}), "bare array"),
        (json.dumps([]), "contains no agents"),
    ],
)
def test_bulk_create_rejects_invalid_input(tmp_path, content, message):
    source = tmp_path / "agents.json"
    source.write_text(content, encoding="utf-8")
    result = _invoke("bulk-create", "--from-file", str(source))
    assert result.exit_code == 7
    assert message in result.output


def test_bulk_create_rejects_missing_file(tmp_path):
    result = _invoke("bulk-create", "--from-file", str(tmp_path / "missing.json"))
    assert result.exit_code == 5
    assert "JSON file not found" in result.output


def test_bulk_create_rejects_server_limit_and_canonical_duplicates_before_http(tmp_path, monkeypatch):
    post = Mock()
    monkeypatch.setattr(agent.client, "post", post)

    too_many = tmp_path / "too-many.json"
    too_many.write_text(json.dumps([{"name": f"agent-{index}"} for index in range(51)]), encoding="utf-8")
    oversized = _invoke("bulk-create", "--from-file", str(too_many), "--dry-run")

    duplicates = tmp_path / "duplicates.json"
    duplicates.write_text(json.dumps([{"name": "Review Agent"}, {"name": "review-agent"}]), encoding="utf-8")
    repeated = _invoke("bulk-create", "--from-file", str(duplicates), "--dry-run")

    assert oversized.exit_code == 7
    assert "at most 50" in oversized.output
    assert repeated.exit_code == 7
    assert "resolve to the same name" in repeated.output
    post.assert_not_called()


@pytest.mark.parametrize(
    "agent_payload",
    [
        {"name": "___"},
        {"name": "install"},
        {"name": "fork"},
        {"name": "forks"},
        {"name": "valid", "components": "not-a-list"},
        {"name": "valid", "components": ["not-an-object"]},
        {"name": "valid", "components": [{"component_type": "skill"}]},
        {"name": "valid", "version": 1},
        {"name": "valid", "model_config_json": []},
        {"name": "valid", "supported_harnesses": {}},
        {"name": "valid", "supported_harnesses": [1]},
        {"name": "valid", "external_mcps": {}},
        {"name": "valid", "external_mcps": [1]},
    ],
)
def test_bulk_create_rejects_invalid_agent_shapes_before_http(tmp_path, monkeypatch, agent_payload):
    source = tmp_path / "invalid-agent.json"
    source.write_text(json.dumps([agent_payload]), encoding="utf-8")
    post = Mock()
    monkeypatch.setattr(agent.client, "post", post)

    result = _invoke("bulk-create", "--from-file", str(source), "--dry-run")

    assert result.exit_code == 7
    post.assert_not_called()


def test_bulk_create_does_not_collapse_distinct_valid_hyphenated_names(tmp_path, monkeypatch):
    agents = [{"name": "agent--one"}, {"name": "agent-one"}]
    source = tmp_path / "distinct.json"
    source.write_text(json.dumps(agents), encoding="utf-8")
    post = Mock(return_value={"created": 2, "skipped": 0, "errors": 0, "results": []})
    monkeypatch.setattr(agent.client, "post", post)

    result = _invoke("bulk-create", "--from-file", str(source), "--dry-run", "--output", "json")

    assert result.exit_code == 0
    post.assert_called_once()


def test_bulk_create_accepts_exactly_fifty_unique_agents(tmp_path, monkeypatch):
    agents = [{"name": f"agent-{index}"} for index in range(50)]
    source = tmp_path / "agents.json"
    source.write_text(json.dumps(agents), encoding="utf-8")
    post = Mock(return_value={"created": 50, "skipped": 0, "errors": 0, "results": []})
    monkeypatch.setattr(agent.client, "post", post)

    result = _invoke("bulk-create", "--from-file", str(source), "--dry-run", "--output", "json")

    assert result.exit_code == 0
    assert json.loads(result.stdout)["partial"] is False
    post.assert_called_once_with("/api/v1/bulk/agents", {"agents": agents, "dry_run": True})


def test_bulk_create_dry_run_renders_each_status_and_exact_payload(tmp_path, monkeypatch):
    agents = [
        {"name": "one", "version": "1.0.0", "model_name": "gpt-4o", "components": []},
        {"name": "two"},
        {"name": "three", "components": [{"component_type": "skill", "component_id": "bad-id"}]},
    ]
    source = tmp_path / "agents.json"
    source.write_text(json.dumps({"agents": agents}), encoding="utf-8")
    response = {
        "results": [
            {"name": "one", "status": "created"},
            {"name": "two", "status": "skipped", "error": "exists"},
            {"name": "three", "status": "invalid", "error": "bad prompt"},
        ],
        "created": 1,
        "skipped": 1,
        "errors": 1,
    }
    post = Mock(return_value=response)
    monkeypatch.setattr(agent.client, "post", post)

    result = _invoke("bulk-create", "--from-file", str(source), "--dry-run")

    assert result.exit_code == 11, result.output
    assert "Dry-run results" in result.output
    assert "created" in result.output
    assert "skipped" in result.output
    assert "invalid" in result.output
    assert "1 would be created, 1 skipped, 1 errors" in result.output
    post.assert_called_once_with("/api/v1/bulk/agents", {"agents": agents, "dry_run": True})


def test_bulk_create_cancellation_and_creation_results(tmp_path, monkeypatch):
    agents = [{"name": "one"}, {"name": "two", "components": []}]
    source = tmp_path / "agents.json"
    source.write_text(json.dumps(agents), encoding="utf-8")
    post = Mock()
    monkeypatch.setattr(agent.client, "post", post)
    monkeypatch.setattr(agent.typer, "confirm", Mock(return_value=False))

    cancelled = _invoke("bulk-create", "--from-file", str(source))

    assert cancelled.exit_code == 0
    assert "Aborted" in cancelled.output
    post.assert_not_called()

    post.return_value = {
        "results": [
            {"name": "one", "status": "created", "agent_id": "1234567890"},
            {"name": "two", "status": "skipped", "error": "exists"},
            {"name": "three", "status": "invalid", "error": "invalid"},
        ],
        "created": 1,
        "skipped": 1,
        "errors": 1,
    }
    created = _invoke("bulk-create", "--from-file", str(source), "--yes")

    assert created.exit_code == 11, created.output
    assert "Bulk create complete" in created.output
    assert "12345678" in created.output
    post.assert_called_once_with("/api/v1/bulk/agents", {"agents": agents, "dry_run": False})


def test_bulk_create_partial_json_result_survives_nonzero_root_exit(tmp_path, monkeypatch):
    import observal_cli.main as main

    source = tmp_path / "agents.json"
    source.write_text(json.dumps([{"name": "one"}, {"name": "two"}]), encoding="utf-8")
    monkeypatch.setattr(main, "_migrate_legacy_mcp_configs", lambda: None)
    monkeypatch.setattr(main, "_try_lockfile_migration", lambda: None)
    monkeypatch.setattr(
        agent.client,
        "post",
        Mock(
            return_value={
                "created": 1,
                "skipped": 0,
                "errors": 1,
                "results": [
                    {"name": "one", "status": "created", "agent_id": "agent-1"},
                    {"name": "two", "status": "error", "error": "invalid"},
                ],
            }
        ),
    )

    result = runner.invoke(
        cli_app,
        ["agent", "bulk-create", "--from-file", str(source), "--yes", "--output", "json"],
    )

    assert result.exit_code == 11
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["partial"] is True
    assert payload["errors"] == 1
    assert len(payload["results"]) == 2


def test_agent_list_table_filters_ids_and_pagination(monkeypatch, _isolated_boundaries):
    data = [_item(), _item(id="abcdefab-1234", name="second", slug="second")]
    get_with_headers = Mock(return_value=(data, {"x-total-count": "3"}))
    resolve_team = Mock(return_value="team-1")
    monkeypatch.setattr(agent.client, "get_with_headers", get_with_headers)
    monkeypatch.setattr(agent.client, "resolve_team_id", resolve_team)

    first = _invoke(
        "list",
        "--search",
        "review",
        "--namespace",
        "@Alice",
        "--team",
        "@platform",
        "--limit",
        "2",
        "--page",
        "1",
        "--full-id",
    )

    assert first.exit_code == 0, first.output
    assert "page 1 of 2" in first.output
    assert data[0]["id"] in first.output
    assert "agent list --page 2 --limit 2" in first.output
    get_with_headers.assert_called_once_with(
        "/api/v1/agents",
        params={"limit": 2, "offset": 0, "search": "review", "namespace": "alice", "team_id": "team-1"},
    )
    resolve_team.assert_called_once_with("@platform")
    _isolated_boundaries.save_last_results.assert_called_once_with(data, "agent")

    get_with_headers.reset_mock()
    get_with_headers.return_value = (data[:1], {"x-total-count": "3"})
    last = _invoke("list", "--limit", "2", "--page", "2", "--id")
    assert last.exit_code == 0, last.output
    assert "12345678" in last.output
    assert "End of results" in last.output

    get_with_headers.return_value = (data[:1], {})
    no_id = _invoke("list")
    assert no_id.exit_code == 0, no_id.output
    assert data[0]["id"] not in no_id.output
    assert "Next:" not in no_id.output
    assert "End of results" not in no_id.output


def test_agent_list_json_rejects_plain_and_handles_empty_pages(monkeypatch):
    data = [_item()]
    get_with_headers = Mock(return_value=(data, {}))
    monkeypatch.setattr(agent.client, "get_with_headers", get_with_headers)

    as_json = _invoke("list", "--output", "json")
    assert as_json.exit_code == 0, as_json.output
    assert json.loads(as_json.output)["items"][0]["qualified_name"] == "alice/reviewer"

    plain = _invoke("list", "--output", "plain")
    assert plain.exit_code == 2
    assert "Error" in plain.output
    assert "plain" in plain.output

    get_with_headers.return_value = ([], {"x-total-count": "0"})
    empty = _invoke("list")
    assert empty.exit_code == 0
    assert "No agents found" in empty.output

    get_with_headers.return_value = ([], {"x-total-count": "5"})
    empty_page = _invoke("list", "--page", "3", "--limit", "2")
    assert empty_page.exit_code == 0
    assert "Page 3 is empty" in empty_page.output
    assert "last page: 3" in empty_page.output


def test_agent_list_interactive_selection_and_cancellation(monkeypatch):
    data = [_item(created_by_email="author@example.test")]
    monkeypatch.setattr(agent.client, "get_with_headers", Mock(return_value=(data, {})))
    show = Mock()
    monkeypatch.setattr(agent, "agent_show", show)

    def select(items, display, label):
        assert items == data
        assert label == "Select agent"
        assert "author@example.test" in display(items[0])
        return items[0]

    monkeypatch.setattr(agent, "fuzzy_select", select)
    selected = _invoke("list", "--interactive")
    assert selected.exit_code == 0, selected.output
    show.assert_called_once_with(data[0]["id"])

    monkeypatch.setattr(agent, "fuzzy_select", Mock(return_value=None))
    show.reset_mock()
    cancelled = _invoke("list", "--interactive")
    assert cancelled.exit_code == 0
    show.assert_not_called()


def test_agent_my_empty_json_rejects_plain_and_renders_table(monkeypatch, _isolated_boundaries):
    get = Mock(return_value=[])
    monkeypatch.setattr(agent.client, "get", get)

    empty = _invoke("my")
    assert empty.exit_code == 0
    assert "no agents" in empty.output.lower()

    data = [_item(status="pending")]
    get.return_value = data
    as_json = _invoke("my", "--output", "json")
    assert as_json.exit_code == 0
    assert json.loads(as_json.output)["items"][0]["status"] == "pending"

    plain = _invoke("my", "--output", "plain")
    assert plain.exit_code == 2
    assert "Error" in plain.output
    assert "plain" in plain.output

    table = _invoke("my")
    assert table.exit_code == 0, table.output
    assert "My Agents (1)" in table.output
    assert "pending" in table.output
    assert _isolated_boundaries.save_last_results.call_count == 3


def test_agent_show_json_and_full_rendering(monkeypatch):
    item = _item(
        created_by_username="author",
        mcp_links=[{"mcp_name": "search", "mcp_listing_id": "mcp-1"}],
        success_criteria={
            "intended_purpose": "Find regressions",
            "success_metrics": [{"name": "precision", "target": "95%", "measurement": "eval"}],
            "evaluation_notes": "Run weekly",
        },
    )
    resolve = Mock(return_value="resolved-agent")
    get = Mock(return_value=item)
    monkeypatch.setattr(agent.client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(agent.client, "get", get)

    as_json = _invoke("show", "alice/reviewer", "--output", "json")
    assert as_json.exit_code == 0, as_json.output
    assert json.loads(as_json.output)["id"] == item["id"]

    rendered = _invoke("show", "alice/reviewer")
    assert rendered.exit_code == 0, rendered.output
    assert "Linked MCP Servers" in rendered.output
    assert "search" in rendered.output
    assert "Success Criteria" in rendered.output
    assert "precision" in rendered.output
    assert "Run weekly" in rendered.output
    resolve.assert_has_calls([call("agent", "alice/reviewer"), call("agent", "alice/reviewer")])
    get.return_value = _item(mcp_links=[], success_criteria=None)
    minimal = _invoke("show", "alice/reviewer")
    assert minimal.exit_code == 0, minimal.output
    assert "Linked MCP Servers" not in minimal.output
    assert "Success Criteria" not in minimal.output

    get.return_value = _item(success_criteria={"intended_purpose": "Find bugs"})
    purpose_only = _invoke("show", "alice/reviewer")
    assert purpose_only.exit_code == 0, purpose_only.output
    assert "Find bugs" in purpose_only.output
    assert "Metrics:" not in purpose_only.output
    assert "Notes:" not in purpose_only.output

    assert get.call_args_list == [call("/api/v1/agents/resolved-agent")] * 4


def test_agent_install_raw_and_profile_rendering(monkeypatch):
    resolve = Mock(return_value="resolved-agent")
    post = Mock(return_value={"config_snippet": {"answer": 42}})
    monkeypatch.setattr(agent.client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(agent.client, "post", post)

    raw = _invoke("install", "reviewer", "--harness", "kiro", "--raw")
    assert raw.exit_code == 0, raw.output
    assert json.loads(raw.output) == {"answer": 42}
    post.assert_called_once_with("/api/v1/agents/resolved-agent/install", {"harness": "kiro"})

    post.return_value = {
        "config_snippet": {"agent_profile": {"path": ".kiro/agents/reviewer.json", "content": {"name": "reviewer"}}}
    }
    profile = _invoke("install", "reviewer", "--harness", "kiro")
    assert profile.exit_code == 0, profile.output
    assert "Save to" in profile.output
    assert ".kiro/agents/reviewer.json" in profile.output
    assert "Or pipe" in profile.output


def test_agent_install_renders_skills_mcp_and_fallback(monkeypatch):
    snippets = iter(
        [
            {
                "config_snippet": {
                    "skills": [{"path": ".agent/skills/test/SKILL.md"}],
                    "mcp_config": {"path": ".agent/mcp.json", "content": {"servers": {"one": {}}}},
                }
            },
            {"config_snippet": {"mcp_config": {"servers": {"two": {}}}}},
            {"config_snippet": {"custom": {"enabled": True}}},
        ]
    )
    monkeypatch.setattr(agent.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(agent.client, "post", Mock(side_effect=lambda *_args: next(snippets)))

    skills = _invoke("install", "reviewer", "--harness", "cursor")
    assert skills.exit_code == 0, skills.output
    assert "Skill files (1)" in skills.output
    assert ".agent/skills/test/SKILL.md" in skills.output
    assert "MCP config" in skills.output
    assert ".agent/mcp.json" in skills.output

    bare_mcp = _invoke("install", "reviewer", "--harness", "cursor")
    assert bare_mcp.exit_code == 0, bare_mcp.output
    assert "MCP config" in bare_mcp.output
    assert '"two"' in bare_mcp.output

    fallback = _invoke("install", "reviewer", "--harness", "cursor")
    assert fallback.exit_code == 0, fallback.output
    assert '"custom"' in fallback.output


def test_archive_delete_and_unarchive_request_boundaries(monkeypatch):
    resolve = Mock(return_value="resolved")
    patch_request = Mock(return_value={})
    monkeypatch.setattr(agent.client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(agent.client, "patch", patch_request)

    archived = _invoke("archive", "reviewer", "--yes")
    deleted = _invoke("delete", "reviewer", "--yes")
    restored = _invoke("unarchive", "reviewer", "--yes")

    assert archived.exit_code == deleted.exit_code == restored.exit_code == 0
    assert "archived" in archived.output
    assert "archived" in deleted.output
    assert "restored" in restored.output
    assert patch_request.call_args_list == [
        call("/api/v1/agents/resolved/archive"),
        call("/api/v1/agents/resolved/archive"),
        call("/api/v1/agents/resolved/unarchive"),
    ]


@pytest.mark.parametrize(
    ("command", "prompt_word"),
    [("archive", "Archive"), ("unarchive", "Unarchive")],
)
def test_archive_lifecycle_confirmation_and_cancellation(command, prompt_word, monkeypatch):
    monkeypatch.setattr(agent.client, "resolve_registry_reference", Mock(return_value="resolved"))
    get = Mock(return_value={"name": "reviewer"})
    patch_request = Mock(return_value={})
    monkeypatch.setattr(agent.client, "get", get)
    monkeypatch.setattr(agent.client, "patch", patch_request)
    confirm = Mock(return_value=True)
    monkeypatch.setattr(agent.typer, "confirm", confirm)

    accepted = _invoke(command, "reviewer")
    assert accepted.exit_code == 0, accepted.output
    assert prompt_word in confirm.call_args.args[0]
    get.assert_called_once_with("/api/v1/agents/resolved")
    patch_request.assert_called_once()

    confirm.return_value = False
    patch_request.reset_mock()
    cancelled = _invoke(command, "reviewer")
    assert cancelled.exit_code != 0
    patch_request.assert_not_called()


def test_agent_init_flag_mode_prompt_file_beta_and_harnesses(tmp_path, monkeypatch, _isolated_boundaries):
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("Triage incidents.", encoding="utf-8")
    _isolated_boundaries.load.return_value = {"username": "alice"}

    result = _invoke(
        "init",
        "--dir",
        str(tmp_path / "agent"),
        "--beta",
        "--name",
        "Incident Helper",
        "--description",
        "Triage incidents",
        "--prompt-file",
        str(prompt_file),
        "--harness",
        "kiro",
    )

    assert result.exit_code == 0, result.output
    data = yaml.safe_load((tmp_path / "agent" / agent.YAML_FILE).read_text(encoding="utf-8"))
    assert data == {
        "name": "incident-helper",
        "version": "0.1.0",
        "description": "Triage incidents",
        "owner": "alice",
        "model_name": "claude-sonnet-4",
        "model_config_json": {},
        "models_by_harness": {},
        "prompt": "Triage incidents.",
        "supported_harnesses": ["kiro"],
        "components": [],
        "external_mcps": [],
        "success_criteria": None,
    }


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (("--name", "helper", "--description", "desc"), "required"),
        (
            ("--name", "helper", "--description", "desc", "--prompt", "Help", "--harness", "invalid"),
            "Unknown harness",
        ),
        (("--name", "!!!", "--description", "desc", "--prompt", "Help"), "name is required"),
    ],
)
def test_agent_init_flag_validation(tmp_path, extra, message):
    result = _invoke("init", "--dir", str(tmp_path), *extra)
    assert result.exit_code == 7
    assert message.lower() in result.output.lower()


def test_agent_init_missing_prompt_file_reports_actionable_error(tmp_path):
    result = _invoke(
        "init",
        "--dir",
        str(tmp_path / "agent"),
        "--name",
        "helper",
        "--description",
        "desc",
        "--prompt-file",
        str(tmp_path / "missing.md"),
    )
    assert result.exit_code == 5
    assert "Prompt file not found" in result.output
    assert "missing.md" in result.output.replace("\n", "")


def test_agent_add_scans_existing_non_duplicate_components(tmp_path):
    _write_agent_yaml(
        tmp_path,
        components=[{"component_type": "mcp", "component_id": "mcp-1"}],
    )

    result = _invoke("add", "skill", "22222222-2222-2222-2222-222222222222", "--dir", str(tmp_path))

    assert result.exit_code == 0, result.output
    data = yaml.safe_load((tmp_path / agent.YAML_FILE).read_text(encoding="utf-8"))
    assert data["components"] == [
        {"component_type": "mcp", "component_id": "mcp-1"},
        {"component_type": "skill", "component_id": "22222222-2222-2222-2222-222222222222"},
    ]


def test_agent_build_without_components_skips_http(tmp_path, monkeypatch):
    _write_agent_yaml(tmp_path, components=[])
    get = Mock()
    post = Mock()
    monkeypatch.setattr(agent.client, "get", get)
    monkeypatch.setattr(agent.client, "post", post)

    result = _invoke("build", "--dir", str(tmp_path))

    assert result.exit_code == 0, result.output
    assert "No components to validate" in result.output
    get.assert_not_called()
    post.assert_not_called()


def test_agent_build_validates_every_component_resource(tmp_path, monkeypatch):
    components = [
        {"component_type": component_type, "component_id": f"{component_type}-1"}
        for component_type in ("mcp", "skill", "hook", "prompt", "sandbox")
    ]
    _write_agent_yaml(tmp_path, components=components)
    get = Mock(return_value={"id": "found"})
    post = Mock(return_value={"issues": []})
    target = Mock(side_effect=_publish_target)
    monkeypatch.setattr(agent.client, "get", get)
    monkeypatch.setattr(agent.client, "post", post)
    monkeypatch.setattr(agent.client, "add_publish_target", target)

    result = _invoke("build", "--dir", str(tmp_path), "--team", "platform", "--visibility", "team")

    assert result.exit_code == 0, result.output
    assert get.call_args_list == [
        call("/api/v1/mcps/mcp-1"),
        call("/api/v1/skills/skill-1"),
        call("/api/v1/hooks/hook-1"),
        call("/api/v1/prompts/prompt-1"),
        call("/api/v1/sandboxes/sandbox-1"),
    ]
    scope_payload = {"components": components, "visibility": "team", "team_id": "team-1"}
    target.assert_called_once_with(scope_payload, "platform", "team")
    post.assert_called_once_with("/api/v1/agents/validate", scope_payload)


def test_agent_publish_rejects_incompatible_draft_modes(monkeypatch):
    post = Mock()
    monkeypatch.setattr(agent.client, "post", post)
    result = _invoke("publish", "--draft", "--submit", "draft-1")
    assert result.exit_code == 7
    assert "cannot be used together" in result.output
    post.assert_not_called()


def test_agent_publish_submits_existing_draft_without_local_yaml(monkeypatch):
    resolve = Mock(return_value="resolved")
    post = Mock(return_value={"id": "draft-1"})
    monkeypatch.setattr(agent.client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(agent.client, "post", post)

    result = _invoke("publish", "--submit", "alice/reviewer")

    assert result.exit_code == 0, result.output
    assert "Draft submitted" in result.output
    resolve.assert_called_once_with("agent", "alice/reviewer")
    post.assert_called_once_with("/api/v1/agents/resolved/submit")


def test_agent_publish_saves_complete_draft_payload(tmp_path, monkeypatch):
    definition = _write_agent_yaml(tmp_path)
    target = Mock(side_effect=_publish_target)
    post = Mock(return_value={"id": "draft-1"})
    monkeypatch.setattr(agent.client, "add_publish_target", target)
    monkeypatch.setattr(agent.client, "post", post)

    result = _invoke(
        "publish",
        "--dir",
        str(tmp_path),
        "--draft",
        "--team",
        "platform",
        "--visibility",
        "team",
    )

    assert result.exit_code == 0, result.output
    assert "Draft saved" in result.output
    payload = post.call_args.args[1]
    assert payload == {
        "name": definition["name"],
        "version": definition["version"],
        "description": definition["description"],
        "owner": definition["owner"],
        "model_name": definition["model_name"],
        "models_by_harness": definition["models_by_harness"],
        "prompt": definition["prompt"],
        "supported_harnesses": definition["supported_harnesses"],
        "components": definition["components"],
        "success_criteria": definition["success_criteria"],
        "visibility": "team",
        "team_id": "team-1",
    }
    target.assert_called_once_with(payload, "platform", "team")
    post.assert_called_once_with("/api/v1/agents/draft", payload)


@pytest.mark.parametrize(
    ("option", "message"),
    [(("--visibility", "team"), "visibility cannot be combined"), (("--team", "platform"), "team cannot be used")],
)
def test_agent_publish_update_rejects_scope_changes(tmp_path, monkeypatch, option, message):
    _write_agent_yaml(tmp_path)
    put = Mock()
    monkeypatch.setattr(agent.client, "put", put)
    result = _invoke("publish", "--dir", str(tmp_path), "--update", *option)
    assert result.exit_code == 7
    assert message in result.output.lower()
    put.assert_not_called()


def test_agent_publish_update_requires_exact_name_match(tmp_path, monkeypatch):
    _write_agent_yaml(tmp_path)
    monkeypatch.setattr(agent.client, "get", Mock(return_value=[{"id": "other", "name": "other"}]))
    put = Mock()
    monkeypatch.setattr(agent.client, "put", put)

    result = _invoke("publish", "--dir", str(tmp_path), "--update")

    assert result.exit_code == 5
    assert "No existing agent" in result.output
    put.assert_not_called()


def test_agent_publish_update_with_explicit_bump_sends_no_version(tmp_path, monkeypatch):
    _write_agent_yaml(tmp_path)
    get = Mock(return_value=[{"id": "agent-1", "name": "reviewer", "version": "1.2.3"}])
    put = Mock(return_value={"id": "agent-1", "version": "1.3.0"})
    monkeypatch.setattr(agent.client, "get", get)
    monkeypatch.setattr(agent.client, "put", put)

    result = _invoke("publish", "--dir", str(tmp_path), "--update", "--bump", "minor")

    assert result.exit_code == 0, result.output
    payload = put.call_args.args[1]
    assert "version" not in payload
    assert payload["version_bump_type"] == "minor"
    put.assert_called_once_with("/api/v1/agents/agent-1", payload)


def test_agent_publish_tty_selects_suggested_bump(tmp_path, monkeypatch):
    _write_agent_yaml(tmp_path)
    get = Mock(
        side_effect=[
            [{"id": "agent-1", "name": "reviewer", "version": "1.2.3"}],
            {"suggestions": {"patch": "1.2.4", "minor": "1.3.0", "major": "2.0.0"}},
        ]
    )
    put = Mock(return_value={"id": "agent-1", "version": "1.3.0"})
    monkeypatch.setattr(agent.client, "get", get)
    monkeypatch.setattr(agent.client, "put", put)
    monkeypatch.setattr(agent, "select_one", Mock(side_effect=lambda _label, choices, **_kwargs: choices[1]))
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))

    agent.agent_publish(
        directory=str(tmp_path),
        update=True,
        draft=False,
        submit=None,
        bump=None,
        team=None,
        visibility=None,
    )

    payload = put.call_args.args[1]
    assert payload["version_bump_type"] == "minor"
    assert "version" not in payload

    get.side_effect = [
        [{"id": "agent-1", "name": "reviewer", "version": "1.2.3"}],
        {"suggestions": {"patch": "1.2.4", "minor": "1.3.0", "major": "2.0.0"}},
    ]
    put.reset_mock()
    monkeypatch.setattr(agent, "select_one", Mock(side_effect=lambda _label, choices, **_kwargs: choices[-1]))

    agent.agent_publish(
        directory=str(tmp_path),
        update=True,
        draft=False,
        submit=None,
        bump=None,
        team=None,
        visibility=None,
    )

    keep_payload = put.call_args.args[1]
    assert keep_payload["version"] == "1.2.3"
    assert "version_bump_type" not in keep_payload


def test_agent_publish_tty_surfaces_suggestion_failure(tmp_path, monkeypatch):
    _write_agent_yaml(tmp_path)
    get = Mock(
        side_effect=[
            [{"id": "agent-1", "name": "reviewer", "version": "1.2.3"}],
            RuntimeError("offline"),
        ]
    )
    put = Mock(return_value={"id": "agent-1", "version": "1.2.3"})
    monkeypatch.setattr(agent.client, "get", get)
    monkeypatch.setattr(agent.client, "put", put)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))

    with pytest.raises(RuntimeError, match="offline"):
        agent.agent_publish(
            directory=str(tmp_path),
            update=True,
            draft=False,
            submit=None,
            bump=None,
            team=None,
            visibility=None,
        )

    put.assert_not_called()


def test_agent_release_validates_bump_and_suggestion(tmp_path, monkeypatch):
    invalid = _invoke("release", "reviewer", "--bump", "epoch", "--dir", str(tmp_path))
    assert invalid.exit_code == 7
    assert "Unknown version bump" in invalid.output

    _write_agent_yaml(tmp_path)
    monkeypatch.setattr(agent.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(
        agent.client,
        "get",
        Mock(side_effect=[{"id": "agent-1"}, {"current": "1.2.3", "suggestions": {}}]),
    )
    post = Mock()
    monkeypatch.setattr(agent.client, "post", post)

    missing = _invoke("release", "reviewer", "--bump", "patch", "--dir", str(tmp_path))
    assert missing.exit_code == 7
    assert "did not provide" in missing.output
    post.assert_not_called()


def test_agent_release_updates_yaml_and_posts_complete_snapshot(tmp_path, monkeypatch):
    definition = _write_agent_yaml(
        tmp_path,
        model_config_json={"temperature": 0.2},
        external_mcps=[{"name": "remote"}],
    )
    resolve = Mock(return_value="resolved")
    get = Mock(
        side_effect=[
            {"id": "agent-1", "name": "reviewer"},
            {"current": "1.2.3", "suggestions": {"patch": "1.2.4"}},
        ]
    )
    post = Mock(return_value={"warnings": ["Another version is pending"]})
    monkeypatch.setattr(agent.client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(agent.client, "get", get)
    monkeypatch.setattr(agent.client, "post", post)

    result = _invoke("release", "alice/reviewer", "--bump", "patch", "--dir", str(tmp_path))

    assert result.exit_code == 0, result.output
    assert "1.2.3" in result.output
    assert "1.2.4" in result.output
    assert "Another version is pending" in result.output
    saved = yaml.safe_load((tmp_path / agent.YAML_FILE).read_text(encoding="utf-8"))
    assert saved["version"] == "1.2.4"
    payload = post.call_args.args[1]
    assert payload["version"] == "1.2.4"
    assert payload["description"] == definition["description"]
    assert payload["model_config_json"] == {"temperature": 0.2}
    assert payload["models_by_harness"] == definition["models_by_harness"]
    assert payload["external_mcps"] == [{"name": "remote"}]
    assert payload["components"] == definition["components"]
    assert payload["success_criteria"] == definition["success_criteria"]
    assert yaml.safe_load(payload["yaml_snapshot"])["version"] == "1.2.4"
    post.assert_called_once_with("/api/v1/agents/agent-1/versions", payload)


def test_agent_versions_empty_json_and_table(monkeypatch):
    resolve = Mock(return_value="resolved")
    get = Mock(return_value={"items": []})
    monkeypatch.setattr(agent.client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(agent.client, "get", get)

    empty = _invoke("versions", "reviewer")
    assert empty.exit_code == 0
    assert "No versions found" in empty.output

    response = {
        "items": [
            {
                "version": "1.2.4",
                "status": "pending",
                "created_at": None,
                "created_by_email": "author@example.test",
                "component_count": 3,
            },
            {
                "version": "1.2.3",
                "status": "approved",
                "created_at": None,
                "created_by_username": "alice",
                "component_count": 2,
            },
        ]
    }
    get.return_value = response
    as_json = _invoke("versions", "reviewer", "--output", "json")
    assert as_json.exit_code == 0, as_json.output
    assert json.loads(as_json.output) == {**response, "total": 2, "page": 1, "page_size": 2}

    table = _invoke("versions", "reviewer")
    assert table.exit_code == 0, table.output
    assert "1.2.4" in table.output
    assert "author@example.test" in table.output
    assert "alice" in table.output
    assert get.call_args_list == [
        call("/api/v1/agents/resolved/versions", params={"page": 1, "page_size": 50}),
        call("/api/v1/agents/resolved/versions", params={"page": 1, "page_size": 50}),
        call("/api/v1/agents/resolved/versions", params={"page": 1, "page_size": 50}),
    ]


def test_agent_transfer_owner_validation_cancellation_and_success(monkeypatch):
    resolve = Mock(return_value="resolved")
    post = Mock(return_value={"owner": "bob"})
    monkeypatch.setattr(agent.client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(agent.client, "post", post)

    invalid = _invoke("transfer-owner", "reviewer", "@", "--yes")
    assert invalid.exit_code == 7
    assert "username is required" in invalid.output

    monkeypatch.setattr(typer, "confirm", Mock(return_value=False))
    cancelled = _invoke("transfer-owner", "reviewer", "bob")
    assert cancelled.exit_code == 1
    post.assert_not_called()

    transferred = _invoke("transfer-owner", "alice/reviewer", "@bob", "--yes")
    assert transferred.exit_code == 0, transferred.output
    assert "@bob" in transferred.output
    resolve.assert_called_once_with("agents", "alice/reviewer")
    post.assert_called_once_with(
        "/api/v1/agents/resolved/transfer-ownership",
        json_data={"username": "bob"},
    )


def test_agent_co_author_commands_render_and_preserve_http_boundaries(monkeypatch):
    get = Mock(return_value=[{"email": "dev@example.test", "username": "dev", "is_active": False}])
    post = Mock(return_value={"email": "dev@example.test", "username": "dev"})
    delete = Mock(return_value={})
    monkeypatch.setattr(agent.client, "get", get)
    monkeypatch.setattr(agent.client, "post", post)
    monkeypatch.setattr(agent.client, "delete", delete)

    listed = _invoke("co-authors", "list", "agent-1")
    added = _invoke("co-authors", "add", "agent-1", "DEV@EXAMPLE.TEST")
    removed = _invoke("co-authors", "remove", "agent-1", "22222222-2222-2222-2222-222222222222")

    assert listed.exit_code == added.exit_code == removed.exit_code == 0
    assert "dev@example.test" in listed.output
    assert any(cell.strip() == "no" for line in listed.output.splitlines() for cell in line.split("│"))
    assert "Added co-author" in added.output
    assert "Co-author removed" in removed.output
    get.assert_called_once_with("/api/v1/agents/agent-1/co-authors")
    post.assert_called_once_with(
        "/api/v1/agents/agent-1/co-authors",
        json_data={"email": "dev@example.test"},
    )
    delete.assert_called_once_with("/api/v1/agents/agent-1/co-authors/22222222-2222-2222-2222-222222222222")

    get.return_value = []
    empty = _invoke("co-authors", "list", "agent-1")
    assert empty.exit_code == 0
    assert "No co-authors" in empty.output


def test_agent_commands_are_registered_on_public_cli():
    result = runner.invoke(cli_app, ["agent", "--help"])
    assert result.exit_code == 0, result.output
    for command in (
        "create",
        "bulk-create",
        "list",
        "my",
        "show",
        "install",
        "archive",
        "unarchive",
        "delete",
        "init",
        "add",
        "build",
        "publish",
        "release",
        "versions",
        "transfer-owner",
        "co-authors",
    ):
        assert command in result.output


def test_every_agent_leaf_has_shared_output_option():
    command = get_command(cli_app).commands["agent"]

    def leaves(group):
        for child in group.commands.values():
            if isinstance(child, Group):
                yield from leaves(child)
            else:
                yield child

    assert all(any(parameter.name == "output" for parameter in leaf.params) for leaf in leaves(command))


def test_create_flags_json_returns_only_server_result(monkeypatch):
    response = {"id": "agent-1", "status": "pending", "qualified_name": "alice/reviewer"}
    monkeypatch.setattr(agent.client, "get", Mock(return_value={"username": "alice"}))
    monkeypatch.setattr(agent.client, "add_publish_target", Mock(side_effect=_publish_target))
    monkeypatch.setattr(agent.client, "post", Mock(return_value=response))

    result = runner.invoke(
        cli_app,
        ["agent", "create", "--name", "reviewer", "--prompt", "Review", "--output", "json"],
    )

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout) == response
    assert result.stderr == ""


def test_bulk_create_json_requires_noninteractive_confirmation(tmp_path):
    source = tmp_path / "agents.json"
    source.write_text(json.dumps([{"name": "reviewer"}]), encoding="utf-8")

    result = runner.invoke(
        cli_app,
        ["agent", "bulk-create", "--from-file", str(source), "--output", "json"],
    )

    assert result.exit_code == 7
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "validation"


def test_install_and_archive_lifecycle_json_return_server_results(monkeypatch):
    resolve = Mock(return_value="agent-1")
    post = Mock(return_value={"config_snippet": {"agent_profile": {"path": "agent.md", "content": "rules"}}})
    patch_request = Mock(
        side_effect=[
            {"id": "agent-1", "archived": True},
            {"id": "agent-1", "archived": True},
            {"id": "agent-1", "archived": False},
        ]
    )
    monkeypatch.setattr(agent.client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(agent.client, "post", post)
    monkeypatch.setattr(agent.client, "patch", patch_request)

    installed = _invoke("install", "reviewer", "--harness", "kiro", "--output", "json")
    archived = _invoke("archive", "reviewer", "--yes", "--output", "json")
    deleted = _invoke("delete", "reviewer", "--yes", "--output", "json")
    restored = _invoke("unarchive", "reviewer", "--yes", "--output", "json")

    assert json.loads(installed.output)["config_snippet"]["agent_profile"]["path"] == "agent.md"
    assert json.loads(archived.output)["archived"] is True
    assert json.loads(deleted.output)["archived"] is True
    assert json.loads(restored.output)["archived"] is False


def test_local_init_add_and_build_json_contracts(tmp_path, monkeypatch):
    target = tmp_path / "agent"
    monkeypatch.setattr(agent.config, "load", Mock(return_value={"username": "alice"}))

    initialized = _invoke(
        "init",
        "--dir",
        str(target),
        "--name",
        "reviewer",
        "--description",
        "Reviews changes",
        "--prompt",
        "Review carefully",
        "--harness",
        "kiro",
        "--output",
        "json",
    )
    added = _invoke(
        "add",
        "skill",
        "22222222-2222-2222-2222-222222222222",
        "--dir",
        str(target),
        "--output",
        "json",
    )
    monkeypatch.setattr(agent.client, "get", Mock(return_value={"id": "skill"}))
    monkeypatch.setattr(agent.client, "post", Mock(return_value={"issues": []}))
    monkeypatch.setattr(agent.client, "add_publish_target", Mock(side_effect=_publish_target))
    built = _invoke("build", "--dir", str(target), "--output", "json")

    assert json.loads(initialized.output)["agent"]["name"] == "reviewer"
    assert json.loads(added.output)["component"]["component_id"] == "22222222-2222-2222-2222-222222222222"
    assert json.loads(built.output)["valid"] is True


def test_build_json_error_contains_structured_repair_result(tmp_path, monkeypatch):
    import observal_cli.main as main
    from observal_cli.errors import CliError, ErrorCategory

    _write_agent_yaml(
        tmp_path,
        components=[{"component_type": "skill", "component_id": "missing-skill"}],
    )
    monkeypatch.setattr(main, "_migrate_legacy_mcp_configs", lambda: None)
    monkeypatch.setattr(main, "_try_lockfile_migration", lambda: None)
    monkeypatch.setattr(
        agent.client,
        "get",
        Mock(side_effect=CliError(ErrorCategory.NOT_FOUND, "Missing.", operation="Validate agent")),
    )
    monkeypatch.setattr(agent.client, "post", Mock(return_value={"valid": True, "issues": []}))
    monkeypatch.setattr(agent.client, "add_publish_target", Mock(side_effect=_publish_target))

    result = runner.invoke(cli_app, ["agent", "build", "--dir", str(tmp_path), "--output", "json"])

    assert result.exit_code == 7
    assert result.stdout == ""
    error = json.loads(result.stderr)["error"]
    assert error["result"]["valid"] is False
    assert error["result"]["components"] == [
        {"type": "skill", "id": "missing-skill", "valid": False, "error": "not found"}
    ]


def test_publish_and_release_json_return_server_results(tmp_path, monkeypatch):
    _write_agent_yaml(tmp_path)
    monkeypatch.setattr(agent.client, "add_publish_target", Mock(side_effect=_publish_target))
    post = Mock(
        side_effect=[
            {"id": "agent-1", "status": "pending"},
            {"id": "version-1", "status": "pending", "warnings": []},
        ]
    )
    monkeypatch.setattr(agent.client, "post", post)

    published = _invoke("publish", "--dir", str(tmp_path), "--output", "json")

    monkeypatch.setattr(agent.client, "resolve_registry_reference", Mock(return_value="agent-1"))
    monkeypatch.setattr(
        agent.client,
        "get",
        Mock(
            side_effect=[
                {"id": "agent-1"},
                {"current": "1.2.3", "suggestions": {"patch": "1.2.4"}},
            ]
        ),
    )
    released = _invoke(
        "release",
        "reviewer",
        "--bump",
        "patch",
        "--dir",
        str(tmp_path),
        "--output",
        "json",
    )

    assert json.loads(published.output) == {"id": "agent-1", "status": "pending"}
    assert json.loads(released.output)["version"] == "1.2.4"


def test_release_failure_does_not_change_local_version(tmp_path, monkeypatch):
    _write_agent_yaml(tmp_path)
    monkeypatch.setattr(agent.client, "resolve_registry_reference", Mock(return_value="agent-1"))
    monkeypatch.setattr(
        agent.client,
        "get",
        Mock(
            side_effect=[
                {"id": "agent-1"},
                {"current": "1.2.3", "suggestions": {"patch": "1.2.4"}},
            ]
        ),
    )
    monkeypatch.setattr(agent.client, "post", Mock(side_effect=RuntimeError("server failed")))

    result = _invoke("release", "reviewer", "--bump", "patch", "--dir", str(tmp_path))

    assert result.exit_code == 1
    assert yaml.safe_load((tmp_path / agent.YAML_FILE).read_text())["version"] == "1.2.3"


def test_co_author_validation_stops_before_requests(monkeypatch):
    post = Mock()
    delete = Mock()
    monkeypatch.setattr(agent.client, "post", post)
    monkeypatch.setattr(agent.client, "delete", delete)

    empty_user = _invoke("co-authors", "add", "alice/reviewer", "@", "--output", "json")
    bad_id = _invoke("co-authors", "remove", "alice/reviewer", "not-a-uuid", "--output", "json")

    assert empty_user.exit_code == 7
    assert bad_id.exit_code == 7
    post.assert_not_called()
    delete.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [
        ["agent", "create", "--output", "json"],
        ["agent", "archive", "alice/reviewer", "--output", "json"],
        ["agent", "delete", "alice/reviewer", "--output", "json"],
        ["agent", "unarchive", "alice/reviewer", "--output", "json"],
        ["agent", "pull", "alice/reviewer", "--harness", "kiro", "--output", "json"],
        ["agent", "pull", "alice/reviewer", "--harness", "unknown", "--no-prompt", "--output", "json"],
    ],
)
def test_agent_json_validation_uses_shared_error_boundary(arguments):
    result = runner.invoke(cli_app, arguments)

    assert result.exit_code == 7
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "validation"


def test_agent_release_refresh_components_asks_the_server_to_move_pins(tmp_path, monkeypatch):
    _write_agent_yaml(tmp_path)
    monkeypatch.setattr(agent.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(
        agent.client,
        "get",
        Mock(side_effect=[{"id": "agent-1"}, {"current": "1.2.3", "suggestions": {"patch": "1.2.4"}}]),
    )
    post = Mock(return_value={})
    monkeypatch.setattr(agent.client, "post", post)

    kept = _invoke("release", "alice/reviewer", "--bump", "patch", "--dir", str(tmp_path), "--output", "json")
    assert kept.exit_code == 0, kept.output
    assert post.call_args.args[1]["refresh_components"] is False

    monkeypatch.setattr(
        agent.client,
        "get",
        Mock(side_effect=[{"id": "agent-1"}, {"current": "1.2.4", "suggestions": {"patch": "1.2.5"}}]),
    )
    refreshed = _invoke(
        "release", "alice/reviewer", "--bump", "patch", "--dir", str(tmp_path), "--refresh-components", "-o", "json"
    )
    assert refreshed.exit_code == 0, refreshed.output
    assert post.call_args.args[1]["refresh_components"] is True


def test_agent_add_records_an_exact_component_version(tmp_path):
    _write_agent_yaml(tmp_path, components=[])
    component = "33333333-3333-3333-3333-333333333333"

    added = _invoke("add", "mcp", component, "--version", "1.2.0-beta.1", "--dir", str(tmp_path), "--output", "json")
    invalid = _invoke("add", "skill", component, "--version", "latest", "--dir", str(tmp_path))

    assert added.exit_code == 0, added.output
    saved = yaml.safe_load((tmp_path / agent.YAML_FILE).read_text(encoding="utf-8"))
    assert saved["components"] == [{"component_type": "mcp", "component_id": component, "version": "1.2.0-beta.1"}]
    assert invalid.exit_code == 7


def test_agent_build_checks_that_a_pinned_component_version_exists(tmp_path, monkeypatch):
    _write_agent_yaml(tmp_path, components=[{"component_type": "skill", "component_id": "skill-1", "version": "2.0.0"}])
    get = Mock(return_value={})
    monkeypatch.setattr(agent.client, "get", get)
    monkeypatch.setattr(agent.client, "add_publish_target", Mock())
    monkeypatch.setattr(agent.client, "post", Mock(return_value={"issues": []}))

    result = _invoke("build", "--dir", str(tmp_path), "--output", "json")

    assert result.exit_code == 0, result.output
    get.assert_called_once_with("/api/v1/skills/skill-1/versions/2.0.0")


def test_agent_outdated_reports_pins_and_suggests_a_refreshed_release(monkeypatch):
    monkeypatch.setattr(agent.client, "resolve_registry_reference", Mock(return_value="resolved"))
    report = {
        "qualified_name": "alice/reviewer",
        "version": "1.2.3",
        "components": [
            {
                "type": "mcp",
                "qualified_name": "acme/github",
                "pinned_version": "1.4.2",
                "latest_version": "2.0.0",
                "outdated": True,
                "archived": False,
                "locked": True,
            },
            {
                "type": "skill",
                "qualified_name": "acme/review",
                "pinned_version": "1.0.0",
                "latest_version": "1.0.0",
                "outdated": False,
                "archived": True,
                "locked": True,
            },
        ],
        "summary": {"total": 2, "outdated": 1, "unlocked": 0, "archived": 1},
    }
    get = Mock(side_effect=[{"version": "1.2.3"}, report])
    monkeypatch.setattr(agent.client, "get", get)

    table = _invoke("outdated", "alice/reviewer")

    assert table.exit_code == 0, table.output
    assert get.call_args_list == [
        call("/api/v1/agents/resolved"),
        call("/api/v1/agents/resolved/versions/1.2.3/outdated"),
    ]
    assert "acme/github" in table.output
    assert "outdated" in table.output
    assert "--refresh-components" in table.output

    get.reset_mock(side_effect=True)
    get.return_value = report
    pinned = _invoke("outdated", "alice/reviewer", "--version", "1.2.3", "--output", "json")
    assert pinned.exit_code == 0, pinned.output
    assert json.loads(pinned.output) == report
    get.assert_called_once_with("/api/v1/agents/resolved/versions/1.2.3/outdated")


@pytest.mark.parametrize("version", ["1.2.0-beta.1", "1.2.0-rc.1"])
def test_agent_outdated_looks_up_the_version_exactly_as_written(monkeypatch, version):
    """PEP 440 would rewrite 1.2.0-beta.1 as 1.2.0b1, which names no agent version."""
    monkeypatch.setattr(agent.client, "resolve_registry_reference", Mock(return_value="resolved"))
    get = Mock(return_value={"components": []})
    monkeypatch.setattr(agent.client, "get", get)

    result = _invoke("outdated", "alice/reviewer", "--version", version, "--output", "json")

    assert result.exit_code == 0, result.output
    get.assert_called_once_with(f"/api/v1/agents/resolved/versions/{version}/outdated")


def test_agent_outdated_rejects_a_version_that_is_not_exact(monkeypatch):
    get = Mock()
    monkeypatch.setattr(agent.client, "get", get)

    result = _invoke("outdated", "alice/reviewer", "--version", "latest")

    assert result.exit_code == 7
    assert "Invalid agent version" in result.output
    get.assert_not_called()
