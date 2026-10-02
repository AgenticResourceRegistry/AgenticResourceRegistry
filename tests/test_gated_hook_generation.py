# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Server config generation for agent hooks the CLI places in settings.json behind the agent gate."""

from __future__ import annotations


def _format(options: dict) -> dict:
    from services.harness import ConfigContext
    from services.harness.claude_code import ClaudeCodeAdapter as ServerAdapter

    hooks = [
        {
            "event": "PostToolUse",
            "handler_type": "command",
            "handler_config": {"command": "probe-fail.sh"},
            "name": "probe-fail",
            "script_filename": "probe-fail.sh",
        },
        {"event": "Stop", "handler_type": "http", "handler_config": {"url": "https://hooks.test/x"}, "name": "web"},
    ]
    ctx = ConfigContext(
        agent=None,
        safe_name="probe-agent",
        harness="claude-code",
        observal_url="http://localhost:8000",
        hook_configs=hooks,
        options={"scope": "project", **options},
    )
    return ServerAdapter().format_config(ctx)


def test_settings_placement_leaves_bound_hooks_out_of_the_agent_file():
    import yaml

    def frontmatter(result: dict) -> dict:
        return yaml.safe_load(result["agent_profile"]["content"].split("\n---", 1)[0][4:])

    default = _format({})
    assert "hook_placement" not in default
    assert "PostToolUse" in frontmatter(default)["hooks"]
    gated = _format({"hook_placement": "settings"})
    assert gated["hook_placement"] == "settings"
    hooks = frontmatter(gated)["hooks"]
    assert "PostToolUse" not in hooks, "the CLI places it in settings.json instead"
    stop = [hook for group in hooks["Stop"] for hook in group["hooks"]]
    assert any(hook.get("url") == "https://hooks.test/x" for hook in stop), "an HTTP hook has no command to gate"
    assert any("observal_cli.hooks.session_push" in hook.get("command", "") for hook in stop), "telemetry stays"
    assert (
        gated["hook_bindings"]
        == default["hook_bindings"]
        == [
            {
                "name": "probe-fail",
                "event": "PostToolUse",
                "command": ".claude/hooks/probe-fail.sh",
                "script": ".claude/hooks/probe-fail.sh",
                "agent": "probe-agent",
            }
        ]
    )
