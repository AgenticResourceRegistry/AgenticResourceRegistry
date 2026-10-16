# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Only pre-owned Pi profile paths are eligible for the normal installer at startup."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from observal_cli import automatic_pull_plan as plan


def test_startup_plan_reuses_only_existing_profile_and_plain_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    root = home / ".pi/agent/agents/reviewer"
    profile = root / "AGENTS.md"
    skill = root / "skills/review/SKILL.md"
    skill.parent.mkdir(parents=True)
    profile.write_text("old profile")
    skill.write_text("old skill")
    item = {"directory": str(tmp_path), "local_name": "reviewer"}
    previous = {
        str(profile): hashlib.sha256(profile.read_bytes()).hexdigest(),
        str(skill): hashlib.sha256(skill.read_bytes()).hexdigest(),
    }
    valid = {
        "agent_profile": {"path": str(profile), "content": "new profile"},
        "skill_components": [{"path": str(skill), "name": "review", "skill_md_content": "new skill"}],
    }
    assert plan.plan_pi_files(valid, item, previous) == {
        profile: b"new profile",
        skill: b"new skill",
    }
    script = skill.parent / "scripts/run.sh"
    script.parent.mkdir()
    script.write_text("echo old\n")
    previous[str(script)] = hashlib.sha256(script.read_bytes()).hexdigest()
    with_script = {
        **valid,
        "skill_components": [
            {**valid["skill_components"][0], "script_content": "echo new\n", "script_filename": "run.sh"}
        ],
    }
    assert plan.plan_pi_files(with_script, item, previous)[script] == b"echo new\n"
    previous.pop(str(script))
    script.unlink()
    # A release may add a script: the writer creates it (recovery deletes it).
    assert plan.plan_pi_files(with_script, item, previous)[script] == b"echo new\n"
    # ...but never over an existing foreign file's directory entry or outside the profile.
    outside = {
        **valid,
        "skill_components": [{**valid["skill_components"][0], "path": str(root.parent / "x" / "SKILL.md")}],
    }
    with pytest.raises(plan.InstallSkipError):
        plan.plan_pi_files(outside, item, previous)
    # Removing the profile itself is never allowed; removing a skill file is.
    assert profile in plan.plan_pi_files({**valid, "skill_components": []}, item, previous)
    with pytest.raises(plan.InstallSkipError):
        plan.plan_pi_files(
            {"agent_profile": valid["agent_profile"], "skill_components": []},
            item,
            {**previous, str(root / "other.md"): "0" * 64},
        )
    cases = [
        {"mcp_config": {"path": str(root / "mcp.json"), "content": {}}},
        {"agent_profile": {"path": str(root / "elsewhere.md"), "content": "new"}},
        {"skill_components": [{"path": str(skill), "name": "review", "git_url": "https://example.test/repo"}]},
        {
            "skill_components": [
                {
                    "path": str(skill),
                    "name": "review",
                    "skill_md_content": "new",
                    "script_content": "echo hi",
                    "script_filename": "../unsafe.sh",
                }
            ]
        },
    ]
    for change in cases:
        with pytest.raises(plan.InstallSkipError):
            plan.plan_pi_files({**valid, **change}, item, previous)

    # The deployed server adds a delegation MCP to every Pi install.
    # An unchanged owned mcp.json is a verified no-op; changed existing
    # entries get the normal writer's exact merged bytes.
    item = {**item, "id": "agent-1"}
    mcp = root / "mcp.json"
    delegation = {"mcpServers": {"observal-agents": {"command": "/tmp/python", "args": []}}}
    mcp.write_text(json.dumps(delegation, indent=2) + "\n")
    previous[str(mcp)] = hashlib.sha256(mcp.read_bytes()).hexdigest()
    same = {**valid, "mcp_config": {"path": str(mcp), "content": delegation}}
    assert plan.plan_pi_files(same, item, previous)[mcp] == mcp.read_bytes()
    changed = {"mcpServers": {"observal-agents": {"command": "/tmp/different", "args": []}}}
    assert (
        plan.plan_pi_files({**valid, "mcp_config": {"path": str(mcp), "content": changed}}, item, previous)[mcp]
        == (json.dumps(changed, indent=2) + "\n").encode()
    )
    added = {"mcpServers": {"observal-agents": delegation["mcpServers"]["observal-agents"], "other": {}}}
    with pytest.raises(plan.InstallSkipError):
        plan.plan_pi_files({**valid, "mcp_config": {"path": str(mcp), "content": added}}, item, previous)
    # An approved version may change an existing bundled MCP reference without
    # adding a key to this fully owned per-agent config.
    owned = {"mcpServers": {**delegation["mcpServers"], "search": {"command": "/tmp/search-v1"}}}
    mcp.write_text(json.dumps(owned, indent=2) + "\n")
    previous[str(mcp)] = hashlib.sha256(mcp.read_bytes()).hexdigest()
    updated = {"mcpServers": {**delegation["mcpServers"], "search": {"command": "/tmp/search-v2"}}}
    assert (
        plan.plan_pi_files({**valid, "mcp_config": {"path": str(mcp), "content": updated}}, item, previous)[mcp]
        == (json.dumps(updated, indent=2) + "\n").encode()
    )
    # A release may add a plain local MCP, or drop one the agent owned.
    plain = {"command": "/tmp/new-mcp", "args": ["--serve"], "env": {"OBSERVAL_AGENT_ID": item["id"]}}
    grown = {"mcpServers": {**updated["mcpServers"], "extra": plain}}
    assert (
        plan.plan_pi_files({**valid, "mcp_config": {"path": str(mcp), "content": grown}}, item, previous)[mcp]
        == (json.dumps(grown, indent=2) + "\n").encode()
    )
    shrunk = {"mcpServers": dict(delegation["mcpServers"])}
    assert (
        plan.plan_pi_files({**valid, "mcp_config": {"path": str(mcp), "content": shrunk}}, item, previous)[mcp]
        == (json.dumps(shrunk, indent=2) + "\n").encode()
    )
    # New entries that need credentials, a URL or an env value stay manual.
    for risky in (
        {"command": "x", "env": {"TOKEN": "${TOKEN}"}},
        {"command": "x", "env": {"OBSERVAL_AGENT_ID": "someone-else"}},
        {"command": "x", "args": ["--key=$KEY"]},
        {"url": "https://example.test/mcp"},
        {"command": "x", "headers": {"a": "b"}},
    ):
        with pytest.raises(plan.InstallSkipError, match="needs credentials"):
            plan.plan_pi_files(
                {
                    **valid,
                    "mcp_config": {"path": str(mcp), "content": {"mcpServers": {**owned["mcpServers"], "bad": risky}}},
                },
                item,
                previous,
            )
    # Saved credentials are carried forward unchanged; anything else is manual.
    secret_old = {
        "mcpServers": {**delegation["mcpServers"], "api": {"command": "/tmp/api-v1", "env": {"KEY": "s3cret"}}}
    }
    mcp.write_text(json.dumps(secret_old, indent=2) + "\n")
    previous[str(mcp)] = hashlib.sha256(mcp.read_bytes()).hexdigest()

    def release(entry: dict) -> dict:
        content = {"mcpServers": {**delegation["mcpServers"], "api": entry}}
        return {**valid, "mcp_config": {"path": str(mcp), "content": content}}

    kept = {"command": "/tmp/api-v2", "env": {"KEY": "s3cret", "OBSERVAL_AGENT_ID": "agent-1"}}
    assert json.loads(plan.plan_pi_files(release(kept), item, previous)[mcp])["mcpServers"]["api"] == kept
    for bad in (
        {"command": "/tmp/api-v2", "env": {"KEY": "different"}},
        {"command": "/tmp/api-v2", "env": {"KEY": "s3cret", "NEW": "value"}},
        {"command": "/tmp/api-v2", "env": {"KEY": "s3cret"}, "headers": {"Authorization": "Bearer x"}},
        {"command": "/tmp/api-v2", "env": {"OBSERVAL_AGENT_ID": "someone-else"}},
    ):
        with pytest.raises(plan.InstallSkipError):
            plan.plan_pi_files(release(bad), item, previous)
    # An empty placeholder (an optional value the user skipped) is not a credential.
    assert plan.plan_pi_files(release({"command": "/tmp/api-v2", "env": {"KEY": "s3cret", "OPT": ""}}), item, previous)
    mcp.write_text(json.dumps(owned, indent=2) + "\n")
    previous[str(mcp)] = hashlib.sha256(mcp.read_bytes()).hexdigest()
    # Dropping every MCP deletes the file only while it is unedited.
    assert mcp not in plan.plan_pi_files(valid, item, previous)
    mcp.write_text(json.dumps({"mcpServers": {"observal-agents": {}, "other": {}}}))
    with pytest.raises(plan.InstallSkipError, match="edited"):
        plan.plan_pi_files(valid, item, previous)
    with pytest.raises(plan.InstallSkipError, match="edited"):
        plan.plan_pi_files(same, item, previous)
    profile.write_text("locally edited")
    # The plan alone is not an ownership check: verified_files in the normal
    # installer runs first and rejects this edit before any target is written.
    with pytest.raises(plan.InstallSkipError):
        plan.plan_pi_files(valid, item, previous)  # Owned mcp.json cannot silently disappear.
