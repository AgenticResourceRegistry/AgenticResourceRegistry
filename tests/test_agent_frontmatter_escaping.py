# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Free-text values in generated agent frontmatter must read back exactly.

Regression: descriptions were written as `description: "<text>"` and Copilot CLI
MCP entries as unquoted `command`/`args`/`url`, so a quote, colon, comma or bracket
made the agent file invalid YAML (and agent pull's launcher rewrite then refused it).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "observal-server"))

from services.harness import ConfigContext
from services.harness.claude_code import ClaudeCodeAdapter
from services.harness.copilot import CopilotAdapter
from services.harness.copilot_cli import CopilotCliAdapter

DESCRIPTIONS = [
    'Reviews "risky" code: style, tests & docs',
    "Back\\slash, #hash and a trailing colon:",
    "Emoji 🙂 and a line\u2028separator",
    "two\nlines",
]


def _frontmatter(content: str) -> dict:
    assert content.startswith("---\n")
    return yaml.safe_load(content[4 : content.index("\n---", 4)])


def _ctx(harness: str, description: str, mcp_configs: dict | None = None) -> ConfigContext:
    agent = SimpleNamespace(id="00000000-0000-4000-8000-000000000001", description=description, model_name="")
    return ConfigContext(
        agent=agent,
        safe_name="reviewer",
        harness=harness,
        observal_url="http://localhost:8000",
        mcp_configs=mcp_configs or {},
        options={"scope": "project"},
    )


@pytest.mark.parametrize("description", DESCRIPTIONS)
@pytest.mark.parametrize(
    ("harness", "adapter"),
    [("claude-code", ClaudeCodeAdapter), ("copilot", CopilotAdapter), ("copilot-cli", CopilotCliAdapter)],
)
def test_agent_description_reads_back_exactly(harness, adapter, description):
    content = adapter().format_config(_ctx(harness, description))["agent_profile"]["content"]
    assert _frontmatter(content)["description"] == description


def test_copilot_cli_mcp_command_args_and_url_read_back_exactly():
    args = ["-m", "observal_cli.sandbox_mcp", "--sandboxes", '[{"name": "a, b", "image": "x:1"}]', "#not-a-comment"]
    configs = {
        "sandbox": {"command": "/opt/py 3/bin/python3: odd", "args": args},
        "remote": {"url": "https://example.com/mcp?a=1&b=2#frag"},
    }
    content = CopilotCliAdapter().format_config(_ctx("copilot-cli", "x", configs))["agent_profile"]["content"]
    servers = _frontmatter(content)["mcp-servers"]
    assert servers["sandbox"]["command"] == "/opt/py 3/bin/python3: odd"
    assert servers["sandbox"]["args"] == args
    assert servers["remote"]["url"] == "https://example.com/mcp?a=1&b=2#frag"
