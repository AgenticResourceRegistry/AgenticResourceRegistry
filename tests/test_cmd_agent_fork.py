# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Agent fork CLI and editable YAML round trip."""

import json
from contextlib import nullcontext
from unittest.mock import Mock

import httpx
import pytest
import yaml
from click.utils import strip_ansi
from typer.testing import CliRunner

import observal_cli.cmd_agent as agent
from observal_cli.errors import CliError, ErrorCategory
from observal_cli.main import app as cli_app

runner = CliRunner()


def _forked():
    return {
        "id": "12345678-1234-1234-1234-123456789abc",
        "name": "my-agent",
        "slug": "my-agent",
        "namespace": "alice",
        "qualified_name": "alice/my-agent",
        "version": "2.0.0",
        "status": "draft",
        "owner": "alice",
        "description": "Review",
        "model_name": "gpt-4o",
        "model_config_json": {"temperature": 0.1},
        "models_by_harness": {"pi": "gpt-4o"},
        "prompt": "Review carefully",
        "supported_harnesses": ["pi"],
        "external_mcps": [{"name": "local", "command": "uvx"}],
        "component_links": [
            {
                "component_type": "skill",
                "component_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "version_ref": "1.2.0",
                "config_override": {"strict": True},
            }
        ],
        "forked_from": {"available": True, "qualified_name": "acme/original", "version": "2.0.0"},
        "warnings": [],
    }


@pytest.fixture(autouse=True)
def _no_spinner(monkeypatch):
    monkeypatch.setattr(agent, "spinner", lambda *_args, **_kwargs: nullcontext())


def test_help_table_and_json_yaml_round_trip(tmp_path, monkeypatch):
    help_result = runner.invoke(agent.agent_app, ["fork", "--help"])
    assert help_result.exit_code == 0 and "observal agent fork" in help_result.output
    help_text = strip_ansi(help_result.output)
    assert "--new-version" in help_text and "--dir" in help_text
    resolve = Mock(return_value="source-uuid")
    post = Mock(return_value=_forked())
    monkeypatch.setattr(agent.client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(agent.client, "post", post)
    json_result = runner.invoke(agent.agent_app, ["fork", "acme/original", "--dir", str(tmp_path), "--output", "json"])
    assert json_result.exit_code == 0, json_result.output
    data = json.loads(json_result.stdout)
    assert data["id"] == _forked()["id"]
    assert data["yaml_path"] == str(tmp_path / agent.YAML_FILE)
    definition = yaml.safe_load((tmp_path / agent.YAML_FILE).read_text())
    assert definition["agent_id"] == data["id"]
    assert definition["version"] == "2.0.0"
    assert definition["model_config_json"] == {"temperature": 0.1}
    assert definition["models_by_harness"] == {"pi": "gpt-4o"}
    assert definition["external_mcps"] == _forked()["external_mcps"]
    assert definition["components"] == [
        {
            "component_type": "skill",
            "component_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "version": "1.2.0",
            "config_override": {"strict": True},
        }
    ]
    assert "yaml_snapshot" not in definition
    resolve.assert_called_once_with("agent", "acme/original")
    post.assert_called_once_with("/api/v1/agents/source-uuid/fork", json_data={})

    put = Mock(return_value={"id": data["id"], "version": "2.0.0"})
    monkeypatch.setattr(agent.client, "put", put)
    get = Mock(side_effect=AssertionError("Draft lookup must use agent_id, not public search"))
    monkeypatch.setattr(agent.client, "get", get)
    updated = runner.invoke(agent.agent_app, ["publish", "--dir", str(tmp_path), "--update", "--output", "json"])
    assert updated.exit_code == 0, updated.output
    assert json.loads(updated.stdout)["id"] == data["id"]
    assert put.call_args.args[0] == f"/api/v1/agents/{data['id']}"
    payload = put.call_args.args[1]
    assert payload["components"] == definition["components"]
    assert payload["model_config_json"] == definition["model_config_json"]
    assert payload["external_mcps"] == definition["external_mcps"]
    get.assert_not_called()

    table_result = runner.invoke(agent.agent_app, ["fork", "acme/original"])
    assert table_result.exit_code == 0, table_result.output
    assert "Forked agent" in table_result.stdout
    assert "acme/original@2.0.0" in table_result.stdout
    assert "observal agent publish --submit" in table_result.stdout


@pytest.mark.parametrize("status,code", [(404, 5), (409, 6), (429, 8)])
def test_agent_fork_http_errors_have_safe_machine_output(monkeypatch, status, code):
    monkeypatch.setattr("observal_cli.main._try_lockfile_migration", lambda: None)
    monkeypatch.setattr("observal_cli.main._migrate_legacy_mcp_configs", lambda: None)
    monkeypatch.setattr(agent.client, "resolve_registry_reference", Mock(return_value="source-uuid"))
    monkeypatch.setattr(agent.client, "_client", lambda: ("https://registry.example", {}))
    request = httpx.Request("POST", "https://registry.example/api/v1/agents/source-uuid/fork")
    response = httpx.Response(
        status, request=request, headers={"X-Request-ID": "request-42"}, json={"detail": "Not approved"}
    )
    monkeypatch.setattr(
        agent.client,
        "_request_with_retry",
        Mock(side_effect=httpx.HTTPStatusError("request rejected", request=request, response=response)),
    )
    result = runner.invoke(cli_app, ["agent", "fork", "acme/original", "--output", "json"])
    assert result.exit_code == code
    assert result.stdout == ""
    error = json.loads(result.stderr)["error"]
    assert error["operation"] == "Fork agent" and error["resource"] == "agent registry"
    assert error["http_status"] == status and error["request_id"] == "request-42"


def test_agent_fork_team_and_version_flags_are_sent_exactly(monkeypatch):
    target = Mock(
        side_effect=lambda payload, team, visibility: payload.update(
            {
                "team_id": "team-uuid",
                "visibility": visibility,
            }
        )
    )
    monkeypatch.setattr(agent.client, "add_publish_target", target)
    monkeypatch.setattr(agent.client, "resolve_registry_reference", Mock(return_value="source-uuid"))
    post = Mock(return_value=_forked())
    monkeypatch.setattr(agent.client, "post", post)
    result = runner.invoke(
        agent.agent_app,
        [
            "fork",
            "acme/original",
            "--name",
            "my-copy",
            "--version",
            "1.2.0",
            "--new-version",
            "0.1.0",
            "--team",
            "platform",
            "--visibility",
            "team",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "draft"
    target.assert_called_once()
    assert post.call_args.kwargs["json_data"] == {
        "name": "my-copy",
        "version": "1.2.0",
        "new_version": "0.1.0",
        "team_id": "team-uuid",
        "visibility": "team",
    }


def test_existing_yaml_is_never_overwritten_or_forked(tmp_path, monkeypatch):
    path = tmp_path / agent.YAML_FILE
    path.write_text("existing: true\n")
    post = Mock()
    monkeypatch.setattr(agent.client, "post", post)
    result = runner.invoke(agent.agent_app, ["fork", "acme/original", "--dir", str(tmp_path)])
    assert result.exit_code == 6
    assert path.read_text() == "existing: true\n"
    post.assert_not_called()


def test_concurrent_file_creation_is_never_overwritten_after_remote_fork(tmp_path, monkeypatch):
    monkeypatch.setattr("observal_cli.main._try_lockfile_migration", lambda: None)
    monkeypatch.setattr("observal_cli.main._migrate_legacy_mcp_configs", lambda: None)
    monkeypatch.setattr(agent.client, "resolve_registry_reference", Mock(return_value="source-uuid"))
    path = tmp_path / agent.YAML_FILE

    def create_file_during_fork(*_args, **_kwargs):
        path.write_text("concurrent: true\n", encoding="utf-8")
        return _forked()

    monkeypatch.setattr(agent.client, "post", Mock(side_effect=create_file_during_fork))
    result = runner.invoke(cli_app, ["agent", "fork", "acme/original", "--dir", str(tmp_path), "--output", "json"])
    assert result.exit_code == 9
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["result"]["id"] == _forked()["id"]
    assert path.read_text(encoding="utf-8") == "concurrent: true\n"
    assert not list(tmp_path.glob(f".{agent.YAML_FILE}.*"))


def test_yaml_write_failure_preserves_created_fork_id_without_claiming_success(tmp_path, monkeypatch):
    monkeypatch.setattr("observal_cli.main._try_lockfile_migration", lambda: None)
    monkeypatch.setattr("observal_cli.main._migrate_legacy_mcp_configs", lambda: None)
    monkeypatch.setattr(agent.client, "resolve_registry_reference", Mock(return_value="source-uuid"))
    post = Mock(return_value=_forked())
    monkeypatch.setattr(agent.client, "post", post)
    monkeypatch.setattr(
        agent,
        "_save_agent_yaml",
        Mock(side_effect=CliError(ErrorCategory.UNAVAILABLE, "Disk write failed", operation="Scaffold forked agent")),
    )
    result = runner.invoke(cli_app, ["agent", "fork", "acme/original", "--dir", str(tmp_path), "--output", "json"])
    assert result.exit_code == 9
    assert result.stdout == ""
    error = json.loads(result.stderr)["error"]
    assert error["result"]["id"] == _forked()["id"]
    assert error["result"]["partial"] is True
    assert "do not fork again" in error["remediation"].lower()
    post.assert_called_once()


def test_invalid_fork_scoping_and_invalid_yaml_id_stop_before_update(tmp_path, monkeypatch):
    post = Mock()
    monkeypatch.setattr(agent.client, "post", post)
    bad = runner.invoke(agent.agent_app, ["fork", "acme/original", "--visibility", "team"])
    assert bad.exit_code == 2
    post.assert_not_called()
    definition = agent._fork_agent_yaml(_forked())
    definition["agent_id"] = "not-a-uuid"
    agent._save_agent_yaml(tmp_path, definition)
    put = Mock()
    monkeypatch.setattr(agent.client, "put", put)
    invalid = runner.invoke(agent.agent_app, ["publish", "--dir", str(tmp_path), "--update"])
    assert invalid.exit_code == 7
    put.assert_not_called()
