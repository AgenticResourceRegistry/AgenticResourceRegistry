# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Pi server adapter: every generated file lands in the agent's profile directory."""

from __future__ import annotations

import pytest

from services.harness import ConfigContext
from services.harness.pi import PiAdapter


def _ctx(scope: str) -> ConfigContext:
    return ConfigContext(
        agent=None,
        safe_name="quick-fixer",
        harness="pi",
        observal_url="http://localhost:8000",
        mcp_configs={"my-mcp": {"command": "node", "args": ["server.js"]}},
        rules_content="# Quick fixer",
        skill_configs=[{"name": "my-skill"}],
        options={"scope": scope},
    )


@pytest.mark.parametrize(
    ("scope", "root"),
    [("project", ".pi/agents/quick-fixer"), ("user", "~/.pi/agent/agents/quick-fixer")],
)
def test_pi_config_paths_stay_in_agent_profile(scope: str, root: str):
    result = PiAdapter().format_config(_ctx(scope))

    assert result["agent_profile"] == {"path": f"{root}/AGENTS.md", "content": "# Quick fixer"}
    assert result["mcp_config"]["path"] == f"{root}/mcp.json"
    assert result["skill_components"][0]["path"] == f"{root}/skills/my-skill/SKILL.md"
