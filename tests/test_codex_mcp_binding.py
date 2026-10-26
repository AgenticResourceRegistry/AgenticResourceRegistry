# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Codex-only pin, transport and native settings gates on the main foundation."""

import hashlib
import json
import tomllib

import pytest

from observal_cli import layer
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
