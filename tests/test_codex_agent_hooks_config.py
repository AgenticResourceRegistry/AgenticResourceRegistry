# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""A Codex agent pull must merge into ~/.codex/hooks.json and name a real hook module."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "observal-server"))


def _config() -> dict:
    from services.harness import ConfigContext
    from services.harness.codex import CodexAdapter

    ctx = ConfigContext(
        agent=SimpleNamespace(description="Reviews code", prompt="Review"),
        safe_name="reviewer",
        harness="codex",
        observal_url="http://localhost:8000",
        mcp_configs={},
        rules_content="Review",
        options={"scope": "user"},
    )
    return CodexAdapter().format_config(ctx)


def test_hooks_config_merges_instead_of_replacing_the_users_file(tmp_path: Path) -> None:
    from observal_cli.cmd_pull import _render_file

    hooks = _config()["hooks_config"]
    assert hooks["path"] == "~/.codex/hooks.json" and hooks["merge"] is True
    existing = tmp_path / "hooks.json"
    mine = [{"matcher": "startup", "hooks": [{"type": "command", "command": "/usr/bin/mine"}]}]
    existing.write_text(json.dumps({"hooks": {"SessionStart": mine}}))
    merged = json.loads(_render_file(existing, hooks["content"], merge_mcp=hooks["merge"])[0])
    assert merged["hooks"]["SessionStart"] == mine
    assert set(merged["hooks"]) == {"SessionStart", "UserPromptSubmit", "Stop"}


def test_every_hook_command_names_an_existing_module() -> None:
    content = _config()["hooks_config"]["content"]
    commands = [h["command"] for groups in content["hooks"].values() for g in groups for h in g["hooks"]]
    assert commands
    for command in commands:
        module = re.search(r"-m (\S+)", command).group(1)
        assert importlib.util.find_spec(module) is not None, command
        assert "--harness codex" in command
