# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Claude Code rewrites ~/.claude.json on every run; the layer identity must not follow that churn."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from observal_cli import layer

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(layer, "_load_hash_cache", lambda: {})
    monkeypatch.setattr(layer, "_save_hash_cache", lambda _cache: None)
    return home


def _write(home: Path, project: Path, *, local_servers: dict, noise: int, user_servers: dict | None = None) -> None:
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "numStartups": noise,
                "cachedGrowthBookFeatures": {"at": noise},
                "mcpServers": user_servers or {},
                "projects": {
                    str(project.resolve()): {"mcpServers": local_servers, "lastCost": noise},
                    "/somewhere/else": {"mcpServers": {"other": {"command": "x"}}, "lastCost": noise},
                },
            }
        )
    )


def _claude_json_entry(project: Path, include_content: bool = False) -> dict:
    manifest = layer.build_layer_manifest("claude-code", str(project), include_content=include_content)
    return next(entry for entry in manifest if entry["path"] == "user:.claude.json")


def test_unrelated_rewrites_keep_the_hash(home, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    servers = {"time": {"command": "uvx", "args": ["mcp-server-time"]}}
    _write(home, project, local_servers=servers, noise=1)
    first = _claude_json_entry(project)
    _write(home, project, local_servers=servers, noise=2)
    assert _claude_json_entry(project)["hash"] == first["hash"]


def test_this_projects_local_servers_change_the_hash(home, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _write(home, project, local_servers={"time": {"command": "uvx"}}, noise=1)
    first = _claude_json_entry(project)["hash"]
    _write(home, project, local_servers={"time": {"command": "uvx"}, "fs": {"command": "npx"}}, noise=1)
    assert _claude_json_entry(project)["hash"] != first


def test_user_level_servers_change_the_hash(home, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _write(home, project, local_servers={}, noise=1)
    first = _claude_json_entry(project)["hash"]
    _write(home, project, local_servers={}, noise=1, user_servers={"fetch": {"command": "uvx"}})
    assert _claude_json_entry(project)["hash"] != first


def test_other_projects_do_not_change_the_hash(home, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _write(home, project, local_servers={}, noise=1)
    first = _claude_json_entry(project)["hash"]
    data = json.loads((home / ".claude.json").read_text())
    data["projects"]["/somewhere/else"]["mcpServers"]["added"] = {"command": "y"}
    (home / ".claude.json").write_text(json.dumps(data))
    assert _claude_json_entry(project)["hash"] == first


def test_hash_only_and_uploaded_manifests_agree_and_carry_no_file_bytes(home, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _write(home, project, local_servers={"time": {"command": "uvx"}}, noise=1)
    scanned = _claude_json_entry(project)
    uploaded = _claude_json_entry(project, include_content=True)
    assert uploaded["hash"] == scanned["hash"]
    assert uploaded["content"] == ""
