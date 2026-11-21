# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Codex registry CLI receipt and negative transport controls on main."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from observal_cli import cmd_mcp as mcp
from observal_cli import layer, lockfile
from observal_cli.errors import CliError, ErrorCategory

LISTING_ID = "11111111-1111-4111-8111-111111111111"


def isolated_install(tmp_path, monkeypatch, *, transport="stdio", stdio_env: str | None = None):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr("observal_cli.harness.codex_mcp_binding.native_mcp_version", lambda _: "codex-cli 0.164.0")
    monkeypatch.setattr(lockfile, "LOCKFILE_PATH", home / "lockfile.json")
    monkeypatch.setattr(lockfile, "_LOCKFILE_LOCK", home / "lockfile.lock")
    monkeypatch.setattr(lockfile, "current_registry_url", lambda: "https://fixture.invalid")
    monkeypatch.setattr(lockfile, "_record_capability_use", lambda **_kwargs: None)
    monkeypatch.setattr(layer, "_LOCAL_SNAPSHOT_PATH", home / "layer_snapshot.json")
    monkeypatch.setattr(layer, "_LAST_UPLOADED_PATH", home / "layer_uploaded.json")
    monkeypatch.setattr(layer, "_FILE_HASH_CACHE_PATH", home / "hash-cache.json")
    monkeypatch.setattr(layer, "_detect_active_harnesses", lambda: ["codex"])
    listing = {
        "id": LISTING_ID,
        "name": "Probe",
        "namespace": "first",
        "slug": "probe",
        "version": "0.1.0",
        "environment_variables": ([{"name": "CONTROL_CHANNEL", "required": True}] if stdio_env else []),
        "headers": [],
    }
    if transport != "stdio":
        listing["url"] = "https://example.test/mcp"
        listing["transport"] = transport
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", lambda *_args: LISTING_ID)
    monkeypatch.setattr(mcp.client, "get", lambda *_args: listing)

    def generate(_path, body):
        entry = (
            {"url": "https://example.test/mcp", "http_headers": body["header_values"]}
            if transport != "stdio"
            else {"command": "inert", "args": [], "env": body["env_values"]}
        )
        return {
            "listing_id": LISTING_ID,
            "local_name": body["local_name"],
            "selected_version": "0.1.0",
            "config_snippet": {"mcp_servers": {body["local_name"]: entry}},
        }

    monkeypatch.setattr(mcp.client, "post_public", generate)
    outputs = []
    monkeypatch.setattr(mcp, "output_json", outputs.append)
    return home, project, outputs


@pytest.mark.parametrize("scope", ["project", "user"])
def test_real_codex_receipt_writes_verified_v2_mapping(tmp_path, monkeypatch, scope):
    home, project, outputs = isolated_install(tmp_path, monkeypatch)
    if scope == "project":
        trusted = home / ".codex/config.toml"
        trusted.parent.mkdir()
        trusted.write_text(f'[projects."{project.resolve()}"]\ntrust_level = "trusted"\n')
    mcp._install_impl(
        "first/probe", "codex", False, apply=True, directory=str(project), scope=scope, no_prompt=True, output="json"
    )
    receipt = outputs[-1]
    assert receipt["version"] == "0.1.0" and receipt["listing_id"] == LISTING_ID
    assert receipt["layer_hash"].startswith("v2_")
    saved = layer.get_local_snapshot()
    assert saved["hash"] == receipt["layer_hash"]
    matching = [x for x in saved["drift"]["mcp_verifications"] if x["component_id"] == LISTING_ID]
    assert len(matching) == 1 and matching[0]["status"] == "verified"
    _, registry = lockfile.read_registry_lockfile()
    pins = registry["harnesses"]["codex"]["standalone"]
    assert len(pins) == 1 and pins[0]["local_name"] == receipt["local_name"]
    assert pins[0]["mcp_integrity"].startswith("sha256-")
    config = project / ".codex/config.toml" if scope == "project" else home / ".codex/config.toml"
    assert config.stat().st_mode & 0o777 == 0o600
    config.write_text(config.read_text().replace("inert", "changed"))
    drift = layer.build_upload_payload(project_dir=str(project))["drift"]
    assert any(x["status"] == "drifted" for x in drift["mcp_verifications"])


@pytest.mark.parametrize("transport", ["sse", None, ""])
def test_http_unknown_transport_refuses_before_config_and_install_api(tmp_path, monkeypatch, transport):
    home, project, _ = isolated_install(tmp_path, monkeypatch, transport=transport)
    submitted = Mock(side_effect=AssertionError("must not issue tracked install request"))
    monkeypatch.setattr(mcp.client, "post_public", submitted)
    with pytest.raises(CliError) as exc:
        mcp._install_impl(
            "first/probe", "codex", False, apply=True, directory=str(project), scope="user", no_prompt=True
        )
    assert exc.value.category is ErrorCategory.VALIDATION
    submitted.assert_not_called()
    assert not (home / ".codex/config.toml").exists()


def test_http_version_detail_is_fetched_when_listing_omits_transport(tmp_path, monkeypatch):
    home, project, _ = isolated_install(tmp_path, monkeypatch, transport="sse")
    listing = {
        "id": LISTING_ID,
        "name": "Probe",
        "namespace": "first",
        "slug": "probe",
        "version": "0.1.0",
        "url": "https://example.test/mcp",
    }
    detail = listing | {"transport": "sse"}
    fetch = Mock(side_effect=[listing, detail])
    monkeypatch.setattr(mcp.client, "get", fetch)
    monkeypatch.setattr(mcp.client, "post_public", Mock(side_effect=AssertionError("unsupported transport")))
    with pytest.raises(CliError):
        mcp._install_impl(
            "first/probe", "codex", False, apply=True, directory=str(project), scope="user", no_prompt=True
        )
    assert fetch.call_args_list[1].args[0] == f"/api/v1/mcps/{LISTING_ID}/versions/0.1.0"
    assert not (home / ".codex/config.toml").exists()


@pytest.mark.parametrize("mutation,expected_status", [("env", "drifted"), ("legacy", "unverified")])
def test_registry_stdio_receipt_loses_presence_after_env_or_active_table_drift(
    tmp_path, monkeypatch, mutation, expected_status
):
    import tomllib

    home, project, outputs = isolated_install(tmp_path, monkeypatch, stdio_env="inert-initial")
    mcp._install_impl(
        "first/probe",
        "codex",
        False,
        apply=True,
        directory=str(project),
        scope="user",
        env_overrides={"CONTROL_CHANNEL": "inert-initial"},
        no_prompt=True,
        output="json",
    )
    receipt = outputs[-1]
    _, registry = lockfile.read_registry_lockfile()
    pin = registry["harnesses"]["codex"]["standalone"][0]["mcp_integrity"]
    assert receipt["layer_hash"] and pin
    config = home / ".codex/config.toml"
    before = layer.build_upload_payload(project_dir=str(project))
    assert before["drift"]["mcp_verifications"][0]["status"] == "verified"
    if mutation == "env":
        config.write_text(config.read_text().replace("inert-initial", "inert-changed"))
    else:
        config.write_text(config.read_text().replace("[mcp_servers.", "[mcp.servers."))
        original = tomllib.loads(config.read_text())["mcp"]["servers"]["probe"]
        assert layer.mcp_entry_fingerprint(original, harness="codex") == pin
    assert layer.verify_installed_mcp("codex", "user", str(project), "probe", pin)[0] == expected_status
    after = layer.build_upload_payload(project_dir=str(project))
    assert after["hash"] != before["hash"]
    assert after["drift"]["mcp_verifications"][0]["status"] == expected_status
    assert after["drift"]["is_canonical"] is (False if mutation == "env" else None)
    assert "inert-initial" not in str(before) + str(after) + str(outputs)


def test_registry_http_install_pins_headers_without_exposing_values(tmp_path, monkeypatch):
    import tomllib

    home, project, outputs = isolated_install(tmp_path, monkeypatch, transport="streamable-http")
    listing = {
        "id": LISTING_ID,
        "name": "Probe",
        "namespace": "first",
        "slug": "probe",
        "version": "0.1.0",
        "url": "https://example.test/mcp",
        "transport": "streamable-http",
        "headers": [{"name": "X-Control", "required": True}],
    }
    monkeypatch.setattr(mcp.client, "get", lambda *_args: listing)
    mcp._install_impl(
        "first/probe",
        "codex",
        False,
        apply=True,
        directory=str(project),
        scope="user",
        header_overrides={"X-Control": "inert-fixture"},
        no_prompt=True,
        output="json",
    )
    saved = layer.get_local_snapshot()
    assert saved["hash"] == outputs[-1]["layer_hash"]
    assert saved["drift"]["mcp_verifications"][0]["status"] == "verified"
    assert "inert-fixture" not in str(saved) + str(outputs)
    config = home / ".codex/config.toml"
    assert tomllib.loads(config.read_text())["mcp_servers"]["probe"]["http_headers"] == {"X-Control": "inert-fixture"}
    config.write_text(config.read_text().replace("inert-fixture", "tampered"))
    assert layer.build_upload_payload(project_dir=str(project))["drift"]["mcp_verifications"][0]["status"] == "drifted"


def test_http_header_prompt_is_hidden_and_secrets_are_not_printed(tmp_path, monkeypatch):
    home, project, _ = isolated_install(tmp_path, monkeypatch, transport="streamable-http")
    listing = {
        "id": LISTING_ID,
        "name": "Probe",
        "namespace": "first",
        "slug": "probe",
        "version": "0.1.0",
        "url": "https://example.test/mcp",
        "transport": "streamable-http",
        "headers": [{"name": "Authorization", "required": True}],
    }
    monkeypatch.setattr(mcp.client, "get", lambda *_args: listing)
    prompt = Mock(return_value="inert-test-value")
    monkeypatch.setattr(mcp, "password_input", prompt)
    monkeypatch.setattr(mcp, "text_input", Mock(side_effect=AssertionError("header must not echo")))
    posted = Mock(side_effect=RuntimeError("stop before writing private settings"))
    monkeypatch.setattr(mcp.client, "post_public", posted)
    with pytest.raises(RuntimeError, match="stop before writing"):
        mcp._install_impl("first/probe", "codex", False, apply=True, directory=str(project), scope="user")
    prompt.assert_called_once()
    assert posted.call_args.args[1]["header_values"] == {"Authorization": "inert-test-value"}
    assert not (home / ".codex/config.toml").exists()
