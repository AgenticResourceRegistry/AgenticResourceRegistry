# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Codex-only pin, transport and native settings gates on the main foundation."""

import hashlib
import json
import tomllib
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from observal_cli import cmd_scan, layer
from observal_cli.harness import ensure_loaded, get_adapter


@pytest.fixture(autouse=True)
def tested_runtime(monkeypatch):
    monkeypatch.setattr("observal_cli.harness.codex_mcp_binding.native_mcp_version", lambda _: "codex-cli 0.164.0")


def isolated(tmp_path, monkeypatch):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setattr(layer, "_FILE_HASH_CACHE_PATH", tmp_path / "hash-cache.json")
    ensure_loaded()
    return home, project, get_adapter("codex")


@pytest.mark.parametrize("version", ["codex-cli 0.163.0", "codex-cli 0.164.0"])
@pytest.mark.parametrize("scope", ["project", "user"])
def test_tracked_stdio_scopes_verify_then_drift_and_shadow(tmp_path, monkeypatch, version, scope):
    home, project, adapter = isolated(tmp_path, monkeypatch)
    monkeypatch.setattr("observal_cli.harness.codex_mcp_binding.native_mcp_version", lambda _: version)
    user_config = home / ".codex/config.toml"
    project_config = project / ".codex/config.toml"
    if scope == "project":
        user_config.parent.mkdir()
        user_config.write_text(f'[projects."{project.resolve()}"]\ntrust_level = "trusted"\n')
    entry = {"command": "inert", "args": ["fixture"], "env": {}}
    undo = adapter.configure_tracked_mcp(scope, str(project), "probe", {"mcp_servers": {"probe": entry}})
    config = project_config if scope == "project" else user_config
    assert tomllib.loads(config.read_text())["mcp_servers"]["probe"] == {"command": "inert", "args": ["fixture"]}
    assert config.stat().st_mode & 0o777 == 0o600
    status, pin = layer.verify_installed_mcp("codex", scope, str(project), "probe")
    assert status == "verified" and pin
    config.write_text(config.read_text().replace("inert", "changed"))
    assert layer.verify_installed_mcp("codex", scope, str(project), "probe", pin)[0] == "drifted"
    assert undo.rollback() is False  # Concurrent edits must never be overwritten.
    config.write_bytes(undo.after)
    other = user_config if scope == "project" else project_config
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text('[mcp_servers.probe]\ncommand = "other"\n')
    assert adapter.read_installed_mcp(scope, str(project), "probe")[0] == "unverified"
    other.unlink()
    assert undo.rollback() is True


@pytest.mark.parametrize("version", ["codex-cli 0.163.0", "codex-cli 0.164.0"])
@pytest.mark.parametrize("scope", ["project", "user"])
def test_tracked_http_headers_are_secret_free_in_the_layer(tmp_path, monkeypatch, version, scope):
    home, project, adapter = isolated(tmp_path, monkeypatch)
    monkeypatch.setattr("observal_cli.harness.codex_mcp_binding.native_mcp_version", lambda _: version)
    if scope == "project":
        trusted = home / ".codex/config.toml"
        trusted.parent.mkdir()
        trusted.write_text(f'[projects."{project.resolve()}"]\ntrust_level = "trusted"\n')
    secret = "MCP_PRIVATE_HEADER_NEVER_UPLOAD"
    entry = {"url": "https://example.test/mcp", "http_headers": {"X-Control": secret}}
    undo = adapter.configure_tracked_mcp(scope, str(project), "probe", {"mcp_servers": {"probe": entry}})
    status, pin = layer.verify_installed_mcp("codex", scope, str(project), "probe")
    assert status == "verified" and pin and secret not in pin
    manifest = layer.build_layer_manifest("codex", str(project), include_content=True, registry_data={"harnesses": {}})
    assert secret not in json.dumps(manifest)
    assert any(item["path"] == adapter.mcp_manifest_path(scope) and item["content"] == "" for item in manifest)
    config = project / ".codex/config.toml" if scope == "project" else home / ".codex/config.toml"
    assert tomllib.loads(config.read_text())["mcp_servers"]["probe"] == entry
    config.write_text(config.read_text().replace(secret, "changed"))
    assert layer.verify_installed_mcp("codex", scope, str(project), "probe", pin)[0] == "drifted"
    config.write_bytes(undo.after)
    assert undo.rollback() is True


@pytest.mark.parametrize(
    "bad",
    [
        {"url": "https://example.test/mcp", "headers": {"X": "secret"}},
        {"url": "https://example.test/mcp", "env": {"TOKEN": "secret"}},
        {"url": "https://name:secret@example.test/mcp"},
        {"url": "https://example.test/mcp?token=secret"},
        {"url": "https://example.test/mcp", "bearer_token_env_var": "TOKEN"},
        {"url": "https://example.test/mcp", "http_headers": {"X": ""}},
        {"url": "file:///tmp/mcp"},
    ],
)
def test_unverified_http_auth_or_url_is_never_installed(tmp_path, monkeypatch, bad):
    home, project, adapter = isolated(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        adapter.configure_tracked_mcp("user", str(project), "probe", {"mcp_servers": {"probe": bad}})
    assert not (home / ".codex/config.toml").exists()


def test_project_trust_and_unrecorded_runtime_are_fail_closed(tmp_path, monkeypatch):
    home, project, adapter = isolated(tmp_path, monkeypatch)
    snippet = {"mcp_servers": {"probe": {"command": "inert", "args": [], "env": {}}}}
    with pytest.raises(ValueError, match="untrusted"):
        adapter.configure_tracked_mcp("project", str(project), "probe", snippet)
    assert not (project / ".codex/config.toml").exists()
    monkeypatch.setattr("observal_cli.harness.codex_mcp_binding.native_mcp_version", lambda _: "codex-cli 0.165.0")
    with pytest.raises(ValueError, match="tested"):
        adapter.configure_tracked_mcp("user", str(project), "probe", snippet)
    assert not (home / ".codex/config.toml").exists()


@pytest.mark.parametrize("scope", ["project", "user"])
def test_stdio_env_is_bound_to_new_receipts_and_old_unbound_receipts_are_not_trusted(tmp_path, monkeypatch, scope):
    home, project, adapter = isolated(tmp_path, monkeypatch)
    if scope == "project":
        trusted = home / ".codex/config.toml"
        trusted.parent.mkdir()
        trusted.write_text(f'[projects."{project.resolve()}"]\ntrust_level = "trusted"\n')
    secret = "TEST_ONLY_ENV_VALUE_NEVER_PUBLISH"
    entry = {"command": "inert", "args": ["fixture"], "env": {"TOKEN": secret}}
    adapter.configure_tracked_mcp(scope, str(project), "probe", {"mcp_servers": {"probe": entry}})
    config = project / ".codex/config.toml" if scope == "project" else home / ".codex/config.toml"
    status, pin = layer.verify_installed_mcp("codex", scope, str(project), "probe")
    assert status == "verified" and pin and secret not in pin
    old_unbound_pin = layer.mcp_entry_fingerprint(entry)  # e531ba674 receipt; env was omitted.
    assert old_unbound_pin != pin
    assert layer.verify_installed_mcp("codex", scope, str(project), "probe", old_unbound_pin)[0] == "drifted"
    manifest = layer.build_layer_manifest("codex", str(project), include_content=True, registry_data={"harnesses": {}})
    assert secret not in json.dumps(manifest)
    assert next(item for item in manifest if item["path"] == adapter.mcp_manifest_path(scope))["content"] == ""
    config.write_text(config.read_text().replace(secret, "TEST_ONLY_ENV_VALUE_CHANGED"))
    assert layer.verify_installed_mcp("codex", scope, str(project), "probe", pin)[0] == "drifted"
    assert secret not in str(layer.verify_installed_mcp("codex", scope, str(project), "probe", pin))


def test_codex_empty_env_and_other_harness_receipts_remain_compatible():
    entry = {"command": "inert", "args": [], "env": {}}
    assert layer.mcp_entry_fingerprint(entry, harness="codex") == layer.mcp_entry_fingerprint(entry)
    entry["env"] = {"TOKEN": "inert"}
    assert layer.mcp_entry_fingerprint(entry, harness="claude-code") == layer.mcp_entry_fingerprint(entry)
    assert layer.mcp_entry_fingerprint(entry, harness="codex") != layer.mcp_entry_fingerprint(entry)


def test_legacy_only_table_cannot_verify_the_exact_same_old_pin(tmp_path, monkeypatch):
    home, project, adapter = isolated(tmp_path, monkeypatch)
    entry = {"command": "inert", "args": [], "env": {}}
    adapter.configure_tracked_mcp("user", str(project), "probe", {"mcp_servers": {"probe": entry}})
    config = home / ".codex/config.toml"
    status, pin = layer.verify_installed_mcp("codex", "user", str(project), "probe")
    assert status == "verified" and pin
    config.write_text(config.read_text().replace("[mcp_servers.", "[mcp.servers."))
    legacy = tomllib.loads(config.read_text())["mcp"]["servers"]["probe"]
    assert layer.mcp_entry_fingerprint(legacy, harness="codex") == pin
    assert adapter.read_installed_mcp("user", str(project), "probe")[0] == "unverified"
    assert layer.verify_installed_mcp("codex", "user", str(project), "probe", pin) == ("unverified", None)
    with pytest.raises(ValueError, match="alias"):
        adapter.configure_tracked_mcp("user", str(project), "probe", {"mcp_servers": {"probe": entry}})
    config.write_text(config.read_text() + '\n[mcp_servers.probe]\ncommand = "inert"\n')
    assert layer.verify_installed_mcp("codex", "user", str(project), "probe", pin)[0] == "unverified"


@pytest.mark.parametrize("scope", ["project", "user"])
def test_scan_command_discovers_new_canonical_codex_installs(tmp_path, monkeypatch, scope):
    home, project, adapter = isolated(tmp_path, monkeypatch)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(project)
    if scope == "project":
        trusted = home / ".codex/config.toml"
        trusted.parent.mkdir()
        trusted.write_text(f'[projects."{project.resolve()}"]\ntrust_level = "trusted"\n')
    adapter.configure_tracked_mcp(
        scope, str(project), "canonical_probe", {"mcp_servers": {"canonical_probe": {"command": "inert"}}}
    )
    scan_result = adapter.scan_project(project) if scope == "project" else adapter.scan_home(home)
    assert [m.name for m in scan_result.mcps] == ["canonical_probe"]
    app = typer.Typer()
    cmd_scan.register_scan(app)
    result = CliRunner().invoke(app, ["--harness", "codex", "--output", "json"])
    assert result.exit_code == 0, type(result.exception).__name__ if result.exception else result.output
    matches = [m for m in json.loads(result.stdout)["mcps"] if m["name"] == "canonical_probe"]
    assert len(matches) == 1 and matches[0]["source"] == f"codex:{scope if scope == 'project' else 'global'}"


def test_existing_url_only_receipts_keep_their_historical_fingerprint():
    entry = {"url": "https://example.test/mcp"}
    historical = ["", [], "https://example.test/mcp", ""]
    expected = (
        "sha256-"
        + hashlib.sha256(
            json.dumps(historical, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert layer.mcp_entry_fingerprint(entry) == expected
    assert layer.mcp_entry_fingerprint(entry | {"http_headers": {"X-Control": "inert"}}) != expected


def test_codex_refuses_symlink_redirects(tmp_path, monkeypatch):
    home, project, adapter = isolated(tmp_path, monkeypatch)
    config = home / ".codex/config.toml"
    config.parent.mkdir()
    outside = tmp_path / "outside.toml"
    outside.write_text("\n")
    config.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        adapter.configure_tracked_mcp(
            "user", str(project), "probe", {"mcp_servers": {"probe": {"command": "inert", "args": [], "env": {}}}}
        )
    assert outside.read_text() == "\n"
