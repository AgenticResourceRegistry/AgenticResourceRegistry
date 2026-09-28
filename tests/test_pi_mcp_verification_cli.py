# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""CLI side of Pi MCP verification: pull-time fingerprints and identity inputs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from observal_cli import layer
from observal_cli.harness import ensure_loaded, get_adapter

AGENT = "00000000-0000-4000-8000-000000000001"
MCP = "11111111-1111-4111-8111-111111111111"


def _pi():
    ensure_loaded()
    return get_adapter("pi")


def test_pull_fingerprints_the_written_pi_profile_not_the_active_config(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    entry = {"command": "python3", "args": ["/srv/probe.py"], "env": {"API_KEY": "private"}}
    profile = tmp_path / ".pi/agent/agents/probe-agent/mcp.json"
    profile.parent.mkdir(parents=True)
    profile.write_text(json.dumps({"mcpServers": {"probe": entry}}))
    # The active ~/.pi/agent/mcp.json does not exist until `/agent` swaps the profile in.
    assert layer.verify_installed_mcp("pi", "user", str(tmp_path), "probe") == ("unverified", None)
    status, fingerprint = layer.verify_installed_mcp("pi", "user", str(tmp_path), "probe", written_config=profile)
    assert (status, fingerprint) == ("verified", layer.mcp_entry_fingerprint(entry))
    assert layer.verify_installed_mcp("pi", "user", str(tmp_path), "other", written_config=profile) == (
        "missing",
        None,
    )
    profile.write_text("{not json")
    assert layer.verify_installed_mcp("pi", "user", str(tmp_path), "probe", written_config=profile)[0] == "unverified"


def test_other_harnesses_ignore_the_written_config(monkeypatch, tmp_path):
    written = tmp_path / "mcp.json"
    written.write_text(json.dumps({"mcpServers": {"probe": {"command": "x"}}}))
    assert layer.verify_installed_mcp("cursor", "user", str(tmp_path), "probe", written_config=written) == (
        "unverified",
        None,
    )


def test_pi_mcp_config_sources_are_hash_only():
    adapter = _pi()
    for path in (
        "user:mcp.json",
        "user:mcp-adapter.json",
        "user:.config/mcp/mcp.json",
        "user:.agents/mcp.json",
        "user:.agents/mcp/mcp.json",
        "user:agents/probe/mcp.json",
        "project:.mcp.json",
        "project:.pi/mcp.json",
        "project:.pi/mcp-adapter.json",
        "user:settings.json",
    ):
        assert adapter.redact_layer_content(path), path
    assert not adapter.redact_layer_content("user:AGENTS.md")


def test_verification_entry_binds_fingerprints_and_scope_to_identity(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    component = {"type": "mcp", "id": MCP, "local_name": "probe", "scope": "user", "mcp_integrity": "sha256-a"}
    registry = {
        "harnesses": {
            "pi": {
                "agents": [{"id": AGENT, "scope": "user", "components": [component, {"type": "skill", "id": MCP}]}],
                "standalone": [
                    {"type": "mcp", "id": MCP, "local_name": "elsewhere", "directory": str(tmp_path / "other")}
                ],
            }
        }
    }
    entry = layer.pi_mcp_verification_entry(registry, str(project))
    data = json.dumps(
        ["observal-pi-mcp-verification-v1", [[AGENT, MCP, "probe", "user", "sha256-a"]]],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    assert entry == {
        "path": "observal:mcp-verification",
        "hash": f"sha256-{hashlib.sha256(data).hexdigest()}",
        "size": len(data),
        "source": "observal",
    }
    component["mcp_integrity"] = "sha256-b"
    assert layer.pi_mcp_verification_entry(registry, str(project))["hash"] != entry["hash"]
    # No pinned Pi MCP: no synthetic entry, so MCP-free layers keep their identity.
    registry["harnesses"]["pi"]["agents"][0]["components"] = []
    assert layer.pi_mcp_verification_entry(registry, str(project)) is None
    assert layer.pi_mcp_verification_entry({"harnesses": {}}, str(project)) is None
