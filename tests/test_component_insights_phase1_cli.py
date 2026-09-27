# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Focused first-harness v2 fingerprint and tracked MCP installation contracts."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from typer.testing import CliRunner

from observal_cli import cmd_mcp as mcp
from observal_cli import layer, lockfile
from observal_cli.errors import CliError, ErrorCategory
from observal_cli.main import app

FIRST = "11111111-1111-4111-8111-111111111111"
SECOND = "22222222-2222-4222-8222-222222222222"


def test_v2_hash_follows_byte_contract_and_is_order_independent():
    pins = {
        "schema_version": 2,
        "agents": [],
        "standalone": [
            {
                "type": "mcp",
                "id": FIRST,
                "name": "Probe",
                "version": "1.0.0",
                "harness": "claude-code",
                "scope": "user",
                "local_name": "probe",
            }
        ],
    }
    a = "sha256-" + "a" * 64
    b = "sha256-" + "b" * 64
    manifest = {"claude-code": [{"path": "project:.mcp.json", "hash": a}, {"path": "user:.claude.json", "hash": b}]}
    file_pairs = [["claude-code/project:.mcp.json", a], ["claude-code/user:.claude.json", b]]
    pin_tuple = ["standalone", "mcp", FIRST, "1.0.0", "claude-code", "user", "probe", "", "", "", "Probe"]
    expected = (
        "v2_"
        + hashlib.sha256(
            json.dumps(
                ["observal-layer-v2", file_pairs, [pin_tuple]], ensure_ascii=False, separators=(",", ":")
            ).encode()
        ).hexdigest()[:60]
    )
    assert layer.layer_hash_v2(manifest, pins) == expected
    assert layer.layer_hash_v2({"claude-code": list(reversed(manifest["claude-code"]))}, pins) == expected
    different = {**pins, "standalone": [{**pins["standalone"][0], "local_name": "other"}]}
    assert layer.layer_hash_v2(manifest, different) != expected
    with pytest.raises(ValueError, match="duplicate"):
        layer.layer_hash_v2(
            {"claude-code": [{"path": "user:.claude.json", "hash": a}, {"path": "user:.claude.json", "hash": b}]}, pins
        )


def test_genuine_project_mcp_installs_preserve_distinct_aliases_and_detect_drift(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "target"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(lockfile, "LOCKFILE_PATH", home / "lockfile.json")
    monkeypatch.setattr(lockfile, "_LOCKFILE_LOCK", home / "lockfile.lock")
    monkeypatch.setattr(lockfile, "current_registry_url", lambda: "https://fixture.invalid")
    monkeypatch.setattr(lockfile, "_record_capability_use", lambda **_kwargs: None)
    monkeypatch.setattr(layer, "_LOCAL_SNAPSHOT_PATH", home / "layer_snapshot.json")
    monkeypatch.setattr(layer, "_LAST_UPLOADED_PATH", home / "layer_uploaded.json")
    monkeypatch.setattr(layer, "_detect_active_harnesses", lambda: ["claude-code"])
    current = {"id": FIRST, "namespace": "first"}

    def listing():
        return {
            "id": current["id"],
            "name": "Search",
            "namespace": current["namespace"],
            "slug": "search",
            "environment_variables": [],
            "headers": [],
        }

    monkeypatch.setattr(mcp.client, "resolve_registry_reference", lambda _kind, _ref: current["id"])
    monkeypatch.setattr(mcp.client, "get", lambda _path: listing())

    def generate(_path, body):
        alias = body["local_name"]
        return {
            "listing_id": current["id"],
            "local_name": alias,
            "selected_version": "1.2.3",
            "config_snippet": {"command": ["claude", "mcp", "add", alias, "--", "inert", "fixture"]},
        }

    monkeypatch.setattr(mcp.client, "post_public", generate)
    calls = []

    def configure(command, *, cwd, capture_output, text):
        calls.append((command, cwd))
        path = cwd / ".mcp.json"
        existing = json.loads(path.read_text()) if path.exists() else {"mcpServers": {}}
        existing["mcpServers"][command[5]] = {"command": "inert", "args": ["fixture"]}
        path.write_text(json.dumps(existing))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(mcp.subprocess, "run", configure)
    outputs = []
    monkeypatch.setattr(mcp, "output_json", outputs.append)

    mcp._install_impl(
        "first/search",
        "claude-code",
        False,
        no_prompt=True,
        apply=True,
        directory=str(project),
        scope="project",
        output="json",
    )
    assert calls[0] == (["claude", "mcp", "add", "--scope", "project", "search", "--", "inert", "fixture"], project)
    first_hash = outputs[-1]["layer_hash"]
    assert re.fullmatch(r"v2_[0-9a-f]{60}", first_hash)
    assert layer.get_last_uploaded_hash() == ""  # A local build is not a server acknowledgement.
    layer.set_last_uploaded_hash(first_hash, "https://fixture.invalid", "fixture-user")
    assert layer.needs_upload(first_hash) is False
    current.update(id=SECOND, namespace="second")
    mcp._install_impl(
        "second/search",
        "claude-code",
        False,
        no_prompt=True,
        apply=True,
        directory=str(project),
        scope="project",
        output="json",
    )
    assert calls[1][0][5] == "second-search"
    assert outputs[-1]["layer_hash"] != first_hash
    assert layer.needs_upload(outputs[-1]["layer_hash"]) is True
    _, registry = lockfile.read_registry_lockfile()
    installs = registry["harnesses"]["claude-code"]["standalone"]
    assert [(entry["id"], entry["version"], entry["local_name"]) for entry in installs] == [
        (FIRST, "1.2.3", "search"),
        (SECOND, "1.2.3", "second-search"),
    ]
    assert all(entry["mcp_integrity"].startswith("sha256-") for entry in installs)
    snapshot = layer.get_local_snapshot()
    assert snapshot["drift"]["is_canonical"] is True
    assert {record["status"] for record in snapshot["drift"]["mcp_verifications"]} == {"verified"}
    assert snapshot["pinned_versions"]["schema_version"] == 2
    assert {item["local_name"] for item in snapshot["pinned_versions"]["standalone"]} == {"search", "second-search"}
    cfg = project / ".mcp.json"
    content = json.loads(cfg.read_text())
    content["mcpServers"]["search"]["command"] = "modified"
    cfg.write_text(json.dumps(content))
    changed = layer.build_upload_payload(project_dir=str(project))["drift"]
    assert changed["is_canonical"] is False
    assert any(record["status"] == "drifted" for record in changed["mcp_verifications"])
    content["mcpServers"].pop("search")
    cfg.write_text(json.dumps(content))
    assert any(
        item["status"] == "missing"
        for item in layer.build_upload_payload(project_dir=str(project))["drift"]["mcp_verifications"]
    )


def test_tracked_install_does_not_claim_unverified_snapshot(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"probe": {"command": "inert", "args": []}}}))
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", lambda *_args: FIRST)
    monkeypatch.setattr(mcp.client, "get", lambda *_args: {"name": "Probe", "namespace": "first", "slug": "probe"})
    monkeypatch.setattr(lockfile, "local_registry_name", lambda *_args, **_kwargs: "probe")
    monkeypatch.setattr(
        mcp.client,
        "post_public",
        lambda *_args: {
            "listing_id": FIRST,
            "selected_version": "1.0.0",
            "local_name": "probe",
            "config_snippet": {"command": ["claude", "mcp", "add", "probe", "--", "inert"]},
        },
    )
    monkeypatch.setattr(mcp.subprocess, "run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0))
    installed = Mock()
    monkeypatch.setattr(lockfile, "upsert_standalone", installed)
    from observal_cli import layer as snapshot_layer

    snapshot_hash = "v2_" + "a" * 60
    monkeypatch.setattr(snapshot_layer, "ensure_local_snapshot", lambda **_kwargs: snapshot_hash)
    monkeypatch.setattr(
        snapshot_layer,
        "get_local_snapshot",
        lambda: {
            "hash": snapshot_hash,
            "drift": {
                "mcp_verifications": [
                    {
                        "harness": "claude-code",
                        "component_id": FIRST,
                        "alias": "probe",
                        "scope": "user",
                        "parent_agent_id": "",
                        "status": "unverified",
                    }
                ]
            },
        },
    )
    with pytest.raises(CliError) as unsafe:
        mcp._install_impl("first/probe", "claude-code", False, apply=True, directory=str(tmp_path), scope="user")
    assert unsafe.value.category is ErrorCategory.UNAVAILABLE
    installed.assert_called_once()  # Config and lockfile were written; CLI must report partial failure.


def test_tracked_install_cli_flags_validate_before_network(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", Mock(side_effect=AssertionError("unexpected API")))
    outcome = CliRunner().invoke(
        app,
        [
            "registry",
            "mcp",
            "install",
            "fixture",
            "--harness",
            "cursor",
            "--apply",
            "--dir",
            str(tmp_path),
            "--scope",
            "project",
            "--no-prompt",
        ],
    )
    assert outcome.exit_code != 0
    assert "Tracked install requires Claude Code" in outcome.output


def test_tracked_install_refuses_unsupported_harness_and_old_server(monkeypatch, tmp_path):
    with pytest.raises(CliError) as unsupported:
        mcp._install_impl("fixture", "cursor", False, apply=True, scope="project", directory=str(tmp_path))
    assert unsupported.value.category is ErrorCategory.VALIDATION
    monkeypatch.setattr(mcp.client, "resolve_registry_reference", lambda *_args: FIRST)
    monkeypatch.setattr(mcp.client, "get", lambda *_args: {"name": "Search", "namespace": "first", "slug": "search"})
    monkeypatch.setattr(lockfile, "local_registry_name", lambda *_args, **_kwargs: "search")
    monkeypatch.setattr(
        mcp.client, "post_public", lambda *_args: {"config_snippet": {"command": ["claude", "mcp", "add", "search"]}}
    )
    process = Mock(side_effect=AssertionError("an unversioned command must not run"))
    monkeypatch.setattr(mcp.subprocess, "run", process)
    with pytest.raises(CliError) as old_server:
        mcp._install_impl("fixture", "claude-code", False, apply=True, scope="project", directory=str(tmp_path))
    assert old_server.value.category is ErrorCategory.UNAVAILABLE
    process.assert_not_called()
    monkeypatch.setattr(
        lockfile,
        "local_registry_name",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("malformed local lockfile")),
    )
    with pytest.raises(CliError) as corrupt:
        mcp._install_impl("fixture", "claude-code", False, apply=True, scope="project", directory=str(tmp_path))
    assert corrupt.value.category is ErrorCategory.UNAVAILABLE
    process.assert_not_called()


@pytest.mark.asyncio
async def test_agent_install_returns_pinned_mcp_version_and_generator_alias(monkeypatch):
    from api.routes.agent import install as agent_install
    from models.agent import AgentStatus
    from models.mcp import ListingStatus
    from schemas.agent import AgentInstallRequest

    agent_id, mcp_id, user_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    component = SimpleNamespace(
        component_type="mcp", component_id=mcp_id, resolved_version="1.0.0", component_name="Probe"
    )
    agent = SimpleNamespace(
        id=agent_id,
        status=AgentStatus.approved,
        is_private=False,
        team_id=None,
        name="Agent",
        model_name="",
        latest_version=SimpleNamespace(components=[component], version="2.0.0"),
    )
    listing = SimpleNamespace(
        id=mcp_id,
        version="2.0.0",
        status=ListingStatus.approved,
        name="Probe",
        namespace="first",
        slug="probe",
        setup_instructions=None,
        qualified_name="first/probe",
    )
    historical = SimpleNamespace(version="1.0.0", status=ListingStatus.approved, setup_instructions=None)
    listing_result = Mock()
    listing_result.scalars.return_value.all.return_value = [listing]
    historical_result = Mock()
    historical_result.scalar_one_or_none.return_value = historical
    db = SimpleNamespace(execute=AsyncMock(side_effect=[listing_result, historical_result]), commit=AsyncMock())
    # Current main resolves pinned releases through the agent lock service.
    pinned_listing = SimpleNamespace(
        id=mcp_id,
        version=historical.version,
        status=listing.status,
        name=listing.name,
        namespace=listing.namespace,
        slug=listing.slug,
        setup_instructions=None,
        qualified_name=listing.qualified_name,
    )
    monkeypatch.setattr(
        "services.agent_lock.load_pinned_listings",
        AsyncMock(
            return_value=SimpleNamespace(
                listings={"mcp": {mcp_id: pinned_listing}, "skill": {}, "hook": {}, "prompt": {}, "sandbox": {}},
                entries=[{"id": str(mcp_id), "type": "mcp", "version": historical.version}],
                warnings=[],
                problems=[],
                status="locked",
            )
        ),
    )
    monkeypatch.setattr(agent_install, "_load_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(agent_install, "get_effective_agent_permission", lambda *_args: "owner")
    monkeypatch.setattr(agent_install, "get_effective_component_permission", lambda *_args: "owner")
    monkeypatch.setattr(agent_install, "apply_publish_scope", lambda query, *_args: query)
    monkeypatch.setattr(agent_install, "apply_visibility_filter", lambda query, *_args: query)
    monkeypatch.setattr(agent_install, "_resolve_component_names", AsyncMock(return_value={}))
    monkeypatch.setattr("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "https://api.test"}))
    monkeypatch.setattr("services.model_resolver.resolve_model_for_harness", AsyncMock(return_value=(None, [])))
    monkeypatch.setattr("services.download_tracker.record_agent_download", AsyncMock())
    monkeypatch.setattr(agent_install, "emit_registry_event", Mock())
    seen = []

    def generate(*_args, **kwargs):
        seen.append(kwargs["mcp_listings"][mcp_id].version)
        kwargs["component_aliases"][str(mcp_id)] = "first-probe"
        return {"agent_profile": {"path": "agent.md", "content": "agent"}}

    monkeypatch.setattr(agent_install, "generate_agent_config", generate)
    response = await agent_install.install_agent(
        str(agent_id),
        AgentInstallRequest(harness="claude-code"),
        Mock(),
        db,
        SimpleNamespace(id=user_id, email="a@fixture.invalid", role=SimpleNamespace(value="user")),
    )
    assert seen == ["1.0.0"]
    assert response.selected_version == "2.0.0"
    assert response.component_pins[0]["version"] == "1.0.0"
    assert response.component_pins[0]["local_name"] == "first-probe"


@pytest.mark.asyncio
async def test_agent_install_rejects_historical_non_mcp_version_before_generation(monkeypatch):
    from api.routes.agent import install as agent_install
    from models.agent import AgentStatus
    from schemas.agent import AgentInstallRequest

    agent_id, skill_id, user_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    component = SimpleNamespace(component_type="skill", component_id=skill_id, resolved_version="1.0.0")
    agent = SimpleNamespace(
        id=agent_id,
        status=AgentStatus.approved,
        is_private=False,
        team_id=None,
        latest_version=SimpleNamespace(components=[component], version="2.0.0"),
    )
    monkeypatch.setattr(agent_install, "_load_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(agent_install, "get_effective_agent_permission", lambda *_args: "owner")
    monkeypatch.setattr(agent_install, "apply_publish_scope", lambda query, *_args: query)
    monkeypatch.setattr(agent_install, "apply_visibility_filter", lambda query, *_args: query)
    generate = Mock(side_effect=AssertionError("must reject before config generation"))
    monkeypatch.setattr(agent_install, "generate_agent_config", generate)
    result = Mock()
    result.scalars.return_value.all.return_value = [SimpleNamespace(id=skill_id, version="2.0.0")]
    db = SimpleNamespace(execute=AsyncMock(return_value=result), commit=AsyncMock())
    with pytest.raises(HTTPException) as rejected:
        await agent_install.install_agent(
            str(agent_id),
            AgentInstallRequest(harness="claude-code"),
            Mock(),
            db,
            SimpleNamespace(id=user_id),
        )
    assert rejected.value.status_code == 409
    db.commit.assert_not_awaited()
    generate.assert_not_called()


def test_user_scope_mcp_drift_and_settings_secrets_stay_out_of_snapshot(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(
        lockfile,
        "read_registry_lockfile",
        lambda: (
            {},
            {
                "harnesses": {
                    "claude-code": {
                        "agents": [],
                        "standalone": [
                            {
                                "id": FIRST,
                                "type": "mcp",
                                "name": "Probe",
                                "version": "1.0.0",
                                "scope": "user",
                                "local_name": "probe",
                                "mcp_integrity": layer.mcp_entry_fingerprint({"command": "inert", "args": []}),
                            }
                        ],
                    }
                }
            },
        ),
    )
    monkeypatch.setattr(layer, "_detect_active_harnesses", lambda: ["claude-code"])
    settings = home / ".claude.json"
    settings.write_text(
        json.dumps({"mcpServers": {"probe": {"command": "inert", "args": [], "env": {"TOKEN": "constructed-secret"}}}})
    )
    snapshot = layer.build_upload_payload(project_dir=str(tmp_path))
    assert snapshot["drift"]["is_canonical"] is True
    config_files = snapshot["harnesses"]["claude-code"]
    assert next(f["content"] for f in config_files if f["path"] == "user:.claude.json") == ""
    assert "constructed-secret" not in json.dumps(snapshot)
    settings.write_text(json.dumps({"mcpServers": {"other": {"command": "inert"}}}))
    assert layer.build_upload_payload(project_dir=str(tmp_path))["drift"]["is_canonical"] is False
    settings.write_text("malformed")
    assert layer.build_upload_payload(project_dir=str(tmp_path))["drift"]["is_canonical"] is None
    settings.write_text(
        json.dumps({"mcpServers": {"probe": {"command": "inert", "args": []}}, "other": "x" * layer.MAX_FILE_SIZE})
    )
    too_large = layer.build_upload_payload(project_dir=str(tmp_path))
    assert too_large["drift"]["is_canonical"] is None
    assert too_large["drift"]["mcp_verifications"][0]["status"] == "unverified"
    assert not any(f["path"] == "user:.claude.json" for f in too_large["harnesses"]["claude-code"])


def test_layer_build_reads_one_registry_and_pairs_hash_with_payload(monkeypatch, tmp_path):
    pin = {
        "harnesses": {
            "claude-code": {
                "agents": [],
                "standalone": [
                    {
                        "type": "mcp",
                        "id": FIRST,
                        "name": "Probe",
                        "version": "1.0.0",
                        "scope": "project",
                        "local_name": "probe",
                        "directory": str(tmp_path),
                    }
                ],
            }
        }
    }
    calls = []
    monkeypatch.setattr(lockfile, "read_registry_lockfile", lambda: (calls.append("read") or {}, pin))
    monkeypatch.setattr(layer, "_detect_active_harnesses", lambda: ["claude-code"])
    monkeypatch.setattr(layer, "build_layer_manifest", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(layer, "_compute_drift", lambda *_args, **_kwargs: {"is_canonical": None, "drifted_files": []})
    payload = layer.build_upload_payload(project_dir=str(tmp_path))
    assert calls == ["read"]
    assert payload["hash"] == layer.layer_hash_v2(payload["harnesses"], payload["pinned_versions"])
    assert payload["pinned_versions"]["standalone"][0]["id"] == FIRST
    assert layer.compute_layer_hash(project_dir=str(tmp_path)) == payload["hash"]


def test_legacy_malformed_pins_are_unknown_and_v1_upload_marker_is_stale(monkeypatch, tmp_path):
    monkeypatch.setattr(layer, "_LAST_UPLOADED_PATH", tmp_path / "uploaded.json")
    layer._LAST_UPLOADED_PATH.write_text(json.dumps({"hash": "0" * 16}))
    assert layer.get_last_uploaded_hash() == ""
    assert layer.needs_upload("v2_" + "a" * 60) is True
    assert layer._extract_pinned_versions({"harnesses": {"claude-code": {"agents": None, "standalone": False}}}) == {
        "schema_version": 2,
        "agents": [],
        "standalone": [],
    }
    legacy = {
        "harnesses": {
            "claude-code": {
                "agents": [
                    {
                        "id": FIRST,
                        "scope": "user",
                        "components": [
                            {"type": "mcp", "name": "legacy"},
                            {"type": "prompt", "id": SECOND, "name": "inline", "version": "1.0.0"},
                            {"type": "sandbox", "id": FIRST, "name": "shared-proxy", "version": "1.0.0"},
                        ],
                    }
                ]
            }
        }
    }
    pins = layer._extract_pinned_versions(legacy)
    assert pins["agents"][0]["components"][0]["id"] == ""
    assert [component["local_name"] for component in pins["agents"][0]["components"]] == ["", "", ""]
    # Inline prompts and sandbox proxy records have no genuine per-component runtime alias.
    assert layer.layer_hash_v2({}, pins).startswith("v2_")


def test_mcp_fingerprint_keeps_endpoint_port_but_never_credentials():
    base = {"type": "http", "url": "http://localhost:8080/mcp"}
    fingerprint = layer.mcp_entry_fingerprint(base)
    assert layer.mcp_entry_fingerprint({**base, "url": "http://localhost:9090/mcp"}) != fingerprint
    # Userinfo, query and fragment can carry secrets and never enter the fingerprint.
    assert (
        layer.mcp_entry_fingerprint({**base, "url": "http://user:secret@localhost:8080/mcp?token=x#f"}) == fingerprint
    )
    ipv6 = layer.mcp_entry_fingerprint({**base, "url": "http://[::1]:8080/mcp"})
    assert ipv6 != layer.mcp_entry_fingerprint({**base, "url": "http://[::1]:8081/mcp"})
    with pytest.raises(ValueError):
        layer.mcp_entry_fingerprint({**base, "url": "http://localhost:99999/mcp"})


def test_concurrent_standalone_upserts_keep_every_entry(monkeypatch, tmp_path):
    import threading

    monkeypatch.setattr(lockfile, "LOCKFILE_PATH", tmp_path / "lockfile.json")
    monkeypatch.setattr(lockfile, "_LOCKFILE_LOCK", tmp_path / "lockfile.lock")
    monkeypatch.setattr(lockfile, "current_registry_url", lambda: "https://fixture.invalid")
    monkeypatch.setattr(lockfile, "_record_capability_use", lambda **_kwargs: None)
    barrier = threading.Barrier(2, timeout=1)
    original_read = lockfile.read_registry_lockfile

    def slow_read(**kwargs):
        result = original_read(**kwargs)
        try:
            barrier.wait()  # Both writers would read the same stale document without the lock.
        except threading.BrokenBarrierError:
            pass
        return result

    # Threads stand in for separate processes: each takes flock through its own descriptor.
    monkeypatch.setattr(lockfile, "read_registry_lockfile", slow_read)

    def install(component_id: str) -> None:
        lockfile.upsert_standalone(
            "claude-code", component_type="mcp", name=component_id, component_id=component_id, version="1"
        )

    threads = [threading.Thread(target=install, args=(value,)) for value in (FIRST, SECOND)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    entries = lockfile.read_lockfile()["registries"]["https://fixture.invalid"]["harnesses"]["claude-code"][
        "standalone"
    ]
    assert {entry["id"] for entry in entries} == {FIRST, SECOND}
