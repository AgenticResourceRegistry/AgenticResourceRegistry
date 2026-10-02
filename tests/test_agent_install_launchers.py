# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""`observal agent install` prints Observal's launchers on this CLI's interpreter, as `agent pull` writes them.

Regression: the printed snippet kept the server's bare ``python3 -m observal_cli...``,
which rarely imports ``observal_cli`` when the CLI was installed with uv or pipx, so a
copied telemetry hook or sandbox/delegation MCP server failed to start.
"""

from __future__ import annotations

import copy
import json
import shlex
import sys
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

import observal_cli.cmd_agent as agent
from observal_cli.cmd_pull import localize_install_snippet, write_install_snippet
from observal_cli.harness import ensure_loaded, get_adapter

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX launcher forms")

RUNNER = CliRunner()
BARE = "python3 -m observal_cli."
PROFILE = """---
name: reviewer
mcpServers:
  - observal-sandbox
hooks:
  UserPromptSubmit:
    - hooks:
        - type: command
          command: "python3 -m observal_cli.hooks.session_push"
  PostToolUse:
    - hooks:
        - type: command
          command: ".claude/hooks/check.sh"
---

Mention python3 -m observal_cli.hooks.session_push in prose: never rewritten.
"""


def _snippet() -> dict:
    return {
        "agent_profile": {"path": ".claude/agents/reviewer.md", "content": PROFILE},
        "mcp_config": {
            "observal-sandbox": {"command": "python3", "args": ["-m", "observal_cli.sandbox_mcp", "--sandboxes", "[]"]},
            "observal-agents": {
                "command": "python3",
                "args": ["-m", "observal_cli.delegation.mcp_server", "--harness", "claude-code"],
                "env": {},
            },
        },
        "mcp_setup_commands": [
            ["claude", "mcp", "add", "observal-sandbox", "--", "python3", "-m", "observal_cli.sandbox_mcp"],
        ],
        "scope": "project",
    }


@pytest.fixture(params=[True, False], ids=["installed", "source-checkout"])
def installed(request, monkeypatch) -> bool:
    from observal_cli.shared import launcher

    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: request.param)
    return request.param


def _interpreter_prefix(installed: bool) -> str:
    from observal_cli.shared.launcher import package_root

    flag = "-I" if installed else "-P"
    executable = shlex.quote(sys.executable)
    return f"{executable} {flag}" if installed else f"PYTHONPATH={shlex.quote(package_root())} {executable} {flag}"


def _assert_localized(snippet: dict, installed: bool) -> None:
    profile = snippet["agent_profile"]["content"]
    head, body = profile.split("\n---\n", 1)
    assert BARE not in head, "no bare launcher left in the frontmatter"
    assert f"{_interpreter_prefix(installed)} -m observal_cli.hooks.session_push" in head
    assert ".claude/hooks/check.sh" in head, "user hook commands are untouched"
    assert BARE in body, "the agent's prose is never rewritten"
    for name in ("observal-sandbox", "observal-agents"):
        entry = snippet["mcp_config"][name]
        assert entry["command"] == sys.executable
        assert entry["args"][0] == ("-I" if installed else "-P")
        assert (entry.get("env") or {}).get("PYTHONPATH") == (None if installed else _package_root())
    (setup,) = snippet["mcp_setup_commands"]
    assert "python3" not in setup and sys.executable in setup


def _package_root() -> str:
    from observal_cli.shared.launcher import package_root

    return package_root()


def test_agent_install_prints_localized_launchers_in_every_output_mode(monkeypatch, installed):
    original = _snippet()
    post = Mock(return_value={"config_snippet": copy.deepcopy(original), "version": "1.0.0"})
    monkeypatch.setattr(agent.client, "resolve_registry_reference", Mock(return_value="agent-uuid"))
    monkeypatch.setattr(agent.client, "post", post)

    raw = RUNNER.invoke(agent.agent_app, ["install", "reviewer", "--harness", "claude-code", "--raw"])
    assert raw.exit_code == 0, raw.output
    _assert_localized(json.loads(raw.output), installed)

    full = RUNNER.invoke(agent.agent_app, ["install", "reviewer", "--harness", "claude-code", "--output", "json"])
    assert full.exit_code == 0, full.output
    result = json.loads(full.output)
    assert result["version"] == "1.0.0", "the rest of the server result is unchanged"
    _assert_localized(result["config_snippet"], installed)


def test_the_printed_snippet_is_exactly_what_pull_writes(tmp_path, monkeypatch, installed):
    ensure_loaded()
    adapter = get_adapter("claude-code")
    original = _snippet()
    localized = localize_install_snippet(original, adapter=adapter, agent_id="agent-uuid")
    assert original == _snippet(), "the server snippet is not modified"
    _assert_localized(localized, installed)

    from observal_cli.cmd_pull import rewrite_observal_interpreter

    pulled = rewrite_observal_interpreter(_snippet())  # what agent pull passes on
    write_install_snippet(
        pulled,
        harness="claude-code",
        adapter=adapter,
        target_dir=tmp_path,
        agent_id="agent-uuid",
        is_user_scope=False,
        quiet=True,
    )
    written = (tmp_path / ".claude" / "agents" / "reviewer.md").read_text()
    assert written == localized["agent_profile"]["content"]
    assert pulled["mcp_config"] == localized["mcp_config"]
    assert pulled["mcp_setup_commands"] == localized["mcp_setup_commands"]


def test_hook_config_launchers_are_localized_for_hooks_file_harnesses(installed):
    ensure_loaded()
    snippet = {
        "hooks_config": {
            "path": ".cursor/hooks.json",
            "content": {
                "version": 1,
                "hooks": {"stop": [{"command": "python3 -m observal_cli.hooks.session_push --harness cursor"}]},
            },
        }
    }
    localized = localize_install_snippet(snippet, adapter=get_adapter("cursor"), agent_id="agent-uuid")
    (hook,) = localized["hooks_config"]["content"]["hooks"]["stop"]
    assert hook["command"] == (f"{_interpreter_prefix(installed)} -m observal_cli.hooks.session_push --harness cursor")
    assert snippet["hooks_config"]["content"]["hooks"]["stop"][0]["command"].startswith(BARE)


def test_snippets_without_launchers_are_unchanged():
    ensure_loaded()
    adapter = get_adapter("claude-code")
    for snippet in ({"answer": 42}, {"skills": [{"path": "x/SKILL.md", "content": "python3 -m observal_cli.x"}]}, []):
        assert localize_install_snippet(copy.deepcopy(snippet), adapter=adapter, agent_id="a") == snippet
