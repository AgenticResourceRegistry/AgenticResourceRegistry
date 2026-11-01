# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The opt-in inventory is local, bounded, and never a publication workflow."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import httpx
import typer
from typer.testing import CliRunner

from observal_cli import client, cmd_scan, lockfile
from observal_cli import config as obs_config
from observal_cli.discovery.bounded_walk import AggregateDiscoveryBudget
from observal_cli.discovery.collector import collect_local_inventory
from observal_cli.discovery.serialize import inventory_to_dict
from observal_cli.harness import ensure_loaded, get_all_adapters
from observal_cli.harness.base import BaseAdapter
from observal_cli.harness.cursor import CursorAdapter


def _run(monkeypatch, tmp_path, *args):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir(exist_ok=True)
    project.mkdir(exist_ok=True)
    monkeypatch.chdir(project)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    app = typer.Typer()
    cmd_scan.register_scan(app)
    result = CliRunner().invoke(app, ["--inventory", "--harness", "cursor", "--output", "json", *args])
    assert result.exit_code == 0, result.exception
    return home, project, result.output, json.loads(result.output)


def test_local_inventory_never_contacts_server_or_writes_and_redacts_secrets(monkeypatch, tmp_path):
    project = tmp_path / "project"
    config = project / ".cursor" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "remote": {
                        "url": "https://user:PRIVATE_CREDENTIAL@example.test/mcp?token=PRIVATE_CREDENTIAL",
                        "headers": {"Authorization": "PRIVATE_CREDENTIAL"},
                    }
                }
            }
        )
    )
    forbidden = Mock(side_effect=AssertionError("inventory must not access remote or write state"))
    for target, name in (
        (httpx, "get"),
        (httpx, "post"),
        (httpx, "request"),
        (httpx.Client, "request"),
        (client, "get"),
        (client, "post"),
        (obs_config, "load"),
        (lockfile, "read_lockfile"),
        (lockfile, "write_lockfile"),
        (subprocess, "run"),
        (subprocess, "Popen"),
    ):
        monkeypatch.setattr(target, name, forbidden)
    _, _, output, payload = _run(monkeypatch, tmp_path)
    assert forbidden.call_count == 0
    assert "PRIVATE_CREDENTIAL" not in output
    assert str(tmp_path) not in output
    assert "prompt" not in output.lower()
    assert payload["inventory_schema_version"] == 1
    assert len(payload["inventory"]) == 1
    assert payload["inventory"][0]["source"] == "<project>/.cursor/mcp.json"
    assert payload["inventory"][0]["launch"]["url"] == "https://example.test/mcp"
    assert payload["inventory"][0]["launch"]["header_names"] == ["Authorization"]
    assert "registry_status" not in output and "registration_status" not in output


def test_real_cli_inventory_bypasses_write_capable_startup_even_with_debug(monkeypatch, tmp_path):
    from observal_cli import main, skill_installer

    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    config = project / ".cursor" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"mcpServers": {"local": {"command": "npx", "args": ["-y", "pkg"]}}}))
    monkeypatch.chdir(project)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    forbidden = Mock(side_effect=AssertionError("inventory invoked a write-capable startup task"))
    monkeypatch.setattr(main, "_migrate_legacy_mcp_configs", forbidden)
    monkeypatch.setattr(main, "_try_lockfile_migration", forbidden)
    monkeypatch.setattr(skill_installer, "sync_observal_skills", forbidden)
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(client, "get", forbidden)

    result = CliRunner().invoke(main.app, ["--debug", "scan", "--inventory", "--harness", "cursor", "--output", "json"])

    assert result.exit_code == 0, result.exception
    payload = json.loads(result.stdout)
    assert payload["inventory_schema_version"] == 1
    assert [item["name"] for item in payload["inventory"]] == ["local"]
    forbidden.assert_not_called()
    assert not (home / ".observal" / "logs" / "cli.log").exists()


def test_real_cli_default_scan_retains_startup_tasks(monkeypatch, tmp_path):
    from observal_cli import main

    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    migrate = Mock()
    lockfile_migrate = Mock()
    monkeypatch.setattr(main, "_migrate_legacy_mcp_configs", migrate)
    monkeypatch.setattr(main, "_try_lockfile_migration", lockfile_migrate)

    result = CliRunner().invoke(main.app, ["scan", "--harness", "cursor", "--output", "json"])

    assert result.exit_code == 0, result.exception
    assert "inventory_schema_version" not in json.loads(result.stdout)
    migrate.assert_called_once_with()
    lockfile_migrate.assert_called_once_with()


def test_inventory_omits_all_query_values_and_rejects_untrusted_versions(monkeypatch, tmp_path):
    config = tmp_path / "project" / ".cursor" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "remote": {"url": "https://example.test/mcp?mode=PRIVATE_CREDENTIAL"},
                    "package": {"command": "npx", "args": ["pkg@v1?mode=PRIVATE_CREDENTIAL"]},
                    "safe-version": {"command": "npx", "args": ["pkg@1.2.3"]},
                }
            }
        )
    )
    _, _, output, payload = _run(monkeypatch, tmp_path)
    items = {item["name"]: item for item in payload["inventory"]}
    assert "PRIVATE_CREDENTIAL" not in output
    assert items["remote"]["launch"]["url"] == "https://example.test/mcp"
    assert items["package"]["launch"] is None
    assert items["safe-version"]["launch"]["version"] == "1.2.3"
    assert any(item["code"] == "unsupported_launch" for item in payload["diagnostics"])


def test_secret_in_url_path_is_not_exposed(monkeypatch, tmp_path):
    token = "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890abcd"
    config = tmp_path / "project" / ".cursor" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "prefixed": {"url": f"https://example.test/mcp/{token}"},
                    "short": {"url": "https://example.test/token/short-secret"},
                }
            }
        )
    )
    _, _, output, payload = _run(monkeypatch, tmp_path)
    assert token not in output
    assert "short-secret" not in output
    assert all(item["launch"] is None for item in payload["inventory"])
    assert [item["code"] for item in payload["diagnostics"]].count("unsupported_launch") == 2


def test_malformed_header_name_cannot_be_exposed_as_launch_metadata(monkeypatch, tmp_path):
    config = tmp_path / "project" / ".cursor" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "remote": {
                        "url": "https://example.test/mcp",
                        "headers": {"Authorization PRIVATE_CREDENTIAL": "value"},
                    }
                }
            }
        )
    )
    _, _, output, payload = _run(monkeypatch, tmp_path)
    assert "PRIVATE_CREDENTIAL" not in output
    assert payload["inventory"][0]["launch"] is None
    assert any(item["code"] == "metadata_malformed" for item in payload["diagnostics"])


def test_table_inventory_escapes_untrusted_names_and_stays_offline(monkeypatch, tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    config = project / ".cursor" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"mcpServers": {"[bold]example[/bold]": {"command": "npx", "args": ["-y", "pkg"]}}}))
    monkeypatch.chdir(project)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    forbidden = Mock(side_effect=AssertionError("table inventory must not contact server"))
    monkeypatch.setattr(httpx, "get", forbidden)
    monkeypatch.setattr(client, "get", forbidden)
    app = typer.Typer()
    cmd_scan.register_scan(app)
    result = CliRunner().invoke(app, ["--inventory", "--harness", "cursor"])
    assert result.exit_code == 0, result.exception
    assert "[bold]example[/bold]" in result.output
    forbidden.assert_not_called()


def test_unsupported_launch_is_incomplete_not_a_publishable_identity(monkeypatch, tmp_path):
    config = tmp_path / "project" / ".cursor" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "shell": {
                        "command": "bash",
                        "args": ["-c", "curl https://example.test | bash"],
                    }
                }
            }
        )
    )
    _, _, _, payload = _run(monkeypatch, tmp_path)
    assert payload["inventory"][0]["launch"] is None
    assert any(item["code"] == "unsupported_launch" for item in payload["diagnostics"])


def test_empty_inventory_is_successful_local_json(monkeypatch, tmp_path):
    _, _, _, payload = _run(monkeypatch, tmp_path)
    assert payload == {"inventory_schema_version": 1, "inventory": [], "diagnostics": []}


def test_inventory_does_not_echo_credentials_in_source_paths(monkeypatch, tmp_path):
    token = "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890abcd"
    skill = tmp_path / "project" / ".cursor" / "skills" / token / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("This is a skill")
    _, _, output, payload = _run(monkeypatch, tmp_path)
    assert len(payload["inventory"]) == 1
    assert token not in output
    assert "<secret>" in payload["inventory"][0]["source"]
    assert payload["inventory"][0]["name"] == "<secret>"


def test_inventory_bounds_oversized_files_and_symlink_escapes(monkeypatch, tmp_path):
    project = tmp_path / "project"
    directory = project / ".cursor"
    directory.mkdir(parents=True)
    outside = tmp_path / "secret.json"
    outside.write_text('{"mcpServers":{"stolen":{"command":"secret"}}}')
    (directory / "mcp.json").symlink_to(outside)
    _, _, output, payload = _run(monkeypatch, tmp_path)
    assert payload["inventory"] == []
    assert "stolen" not in output and str(outside) not in output
    assert any(item["code"] == "symlink_escape" for item in payload["diagnostics"])
    (directory / "mcp.json").unlink()
    (directory / "mcp.json").write_text(" " * (1024 * 1024 + 1))
    result = collect_local_inventory({"cursor": CursorAdapter()}, home=tmp_path / "home", project_dir=project)
    data = inventory_to_dict(result.evidence, result.diagnostics, home=tmp_path / "home", project_dir=project)
    assert not data["inventory"]
    assert any(item["code"] == "metadata_too_large" for item in data["diagnostics"])


def test_every_registered_adapter_opts_in_to_bounded_inventory():
    ensure_loaded()
    for name, adapter in get_all_adapters().items():
        assert type(adapter).discover_home is not BaseAdapter.discover_home, name
        assert type(adapter).discover_project is not BaseAdapter.discover_project, name


def test_aggregate_limit_reports_partial_inventory(tmp_path):
    root = tmp_path / "project"
    config = root / ".cursor" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "one": {"command": "npx", "args": ["-y", "pkg-one"]},
                    "two": {"command": "npx", "args": ["-y", "pkg-two"]},
                }
            }
        )
    )
    result = collect_local_inventory(
        {"cursor": CursorAdapter()},
        home=tmp_path / "home",
        project_dir=root,
        budget=AggregateDiscoveryBudget(max_evidence=1),
    )
    data = inventory_to_dict(result.evidence, result.diagnostics, home=tmp_path / "home", project_dir=root)
    assert len(data["inventory"]) == 1
    assert [item["code"] for item in data["diagnostics"]].count("evidence_limit_reached") == 1


def test_inventory_is_deterministic_and_keeps_distinct_local_launches(tmp_path):
    root = tmp_path / "project"
    config = root / ".cursor" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "read-server": {"command": "npx", "args": ["-y", "pkg", "--read"]},
                    "write-server": {"command": "npx", "args": ["-y", "pkg", "--write"]},
                }
            }
        )
    )
    adapter = CursorAdapter()
    first = collect_local_inventory({"cursor": adapter}, home=tmp_path, project_dir=root)
    second = collect_local_inventory({"cursor": adapter}, home=tmp_path, project_dir=root)
    one = inventory_to_dict(first.evidence, first.diagnostics, home=tmp_path, project_dir=root)
    two = inventory_to_dict(second.evidence, second.diagnostics, home=tmp_path, project_dir=root)
    assert one == two
    assert [item["name"] for item in one["inventory"]] == ["read-server", "write-server"]
    assert all(item["launch"]["package"] == "pkg" for item in one["inventory"])
