# SPDX-FileCopyrightText: 2026 杜昌亿 <218478651+duchangyi666@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0

"""OpenCode server adapter config generation.

Cover:
* one MCP server and one skill at project and user scope
* an agent with no components
* the registry default scope when none is requested

``format_config`` writes the agent profile and the OpenCode MCP document from
the registry. Skill configs are passed through as ``skill_components`` for the
CLI installer; this adapter does not rewrite them onto the registry SKILL.md
paths.
"""

from __future__ import annotations

import pytest

from observal_shared.harness_registry import HARNESS_REGISTRY
from services.harness import ConfigContext
from services.harness.opencode import OpenCodeAdapter

_OPENCODE_SCHEMA = "https://opencode.ai/config.json"
_SKILL = {"name": "my-skill", "skill_md_content": "# My skill\n"}


def _ctx(
    scope: str | None,
    *,
    mcp_configs: dict | None = None,
    skill_configs: list | None = None,
    rules_content: str = "# Quick fixer",
) -> ConfigContext:
    return ConfigContext(
        agent=None,
        safe_name="quick-fixer",
        harness="opencode",
        observal_url="http://localhost:8000",
        mcp_configs={"my-mcp": {"command": "node", "args": ["server.js"]}} if mcp_configs is None else mcp_configs,
        rules_content=rules_content,
        skill_configs=[dict(_SKILL)] if skill_configs is None else skill_configs,
        options={} if scope is None else {"scope": scope},
    )


class TestOpenCodeServerAdapter:
    """Tests for OpenCodeAdapter.format_config."""

    @pytest.mark.parametrize("scope", ["project", "user"])
    def test_mcp_and_skill_match_registry_paths_and_contents(self, scope: str) -> None:
        spec = HARNESS_REGISTRY["opencode"]
        skill = dict(_SKILL)
        ctx = _ctx(scope, skill_configs=[skill])

        result = OpenCodeAdapter().format_config(ctx)

        assert result == {
            "agent_profile": {
                "path": spec["agent_profile"][scope].format(name="quick-fixer"),
                "content": "# Quick fixer",
            },
            "mcp_config": {
                "path": spec["mcp_config"][scope],
                "content": {
                    "$schema": _OPENCODE_SCHEMA,
                    spec["mcp_servers_key"]: {
                        "my-mcp": {"type": "local", "command": ["node", "server.js"]},
                    },
                },
            },
            "scope": scope,
            "skill_components": [skill],
        }

    @pytest.mark.parametrize("scope", ["project", "user"])
    def test_agent_with_no_components_writes_empty_mcp_document(self, scope: str) -> None:
        spec = HARNESS_REGISTRY["opencode"]
        ctx = _ctx(scope, mcp_configs={}, skill_configs=[], rules_content="")

        result = OpenCodeAdapter().format_config(ctx)

        assert result == {
            "agent_profile": {
                "path": spec["agent_profile"][scope].format(name="quick-fixer"),
                "content": "",
            },
            "mcp_config": {
                "path": spec["mcp_config"][scope],
                "content": {"$schema": _OPENCODE_SCHEMA, spec["mcp_servers_key"]: {}},
            },
            "scope": scope,
        }
        assert "skill_components" not in result
        assert "hook_files" not in result

    def test_omitted_scope_uses_registry_default(self) -> None:
        spec = HARNESS_REGISTRY["opencode"]
        ctx = _ctx(None)

        result = OpenCodeAdapter().format_config(ctx)

        default_scope = spec["default_scope"]
        assert result["scope"] == default_scope
        assert result["agent_profile"]["path"] == spec["agent_profile"][default_scope].format(name="quick-fixer")
        assert result["mcp_config"]["path"] == spec["mcp_config"][default_scope]
        assert result["mcp_config"]["content"]["$schema"] == _OPENCODE_SCHEMA
        assert result["mcp_config"]["content"][spec["mcp_servers_key"]]["my-mcp"]["type"] == "local"
        assert result["skill_components"] == [dict(_SKILL)]
