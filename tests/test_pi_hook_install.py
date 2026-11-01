# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Pi registry hooks: what the server's Pi adapter installs, and what it reports it cannot.

Regression: the Pi adapter ignored ``ctx.hook_configs``, so a pulled agent's hooks
were listed in the lockfile but never written, with no warning. Every hook is now
either written to the profile's ``observal-hooks.json`` or reported as not
installed (docs/integrations/pi.md, "Registry hooks").
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "observal-server"))

from services.harness import ConfigContext
from services.harness.pi import PI_HOOKS_SCHEMA, PiAdapter

AGENT = "00000000-0000-4000-8000-000000000001"


def _hook(name: str, event: str = "PreToolUse", **extra) -> dict:
    return {
        "event": event,
        "handler_type": "command",
        "handler_config": {"command": f"./{name}.sh --strict"},
        "name": name,
        "script_filename": None,
        "script_content": None,
        "tool_filter": None,
    } | extra


def _format(hooks: list[dict], scope: str = "user", compatibility_warnings: list[str] | None = None) -> dict:
    agent = SimpleNamespace(id=AGENT, description="", model_name="")
    ctx = ConfigContext(
        agent=agent,
        safe_name="reviewer",
        harness="pi",
        observal_url="http://localhost:8000",
        rules_content="Review.",
        hook_configs=hooks,
        options={"scope": scope},
        compatibility_warnings=compatibility_warnings or [],
    )
    return PiAdapter().format_config(ctx)


def test_supported_hooks_are_written_to_the_profile_hooks_file():
    result = _format(
        [
            _hook("guard"),
            _hook("audit", "PostToolUse", handler_config={"command": "audit", "timeout": 5}),
            _hook("slow", handler_config={"command": "slow", "timeout": 10_000}),
        ]
    )
    assert result["hooks_config"] == {
        "path": "~/.pi/agent/agents/reviewer/observal-hooks.json",
        "content": {
            "schema": PI_HOOKS_SCHEMA,
            "agent": "reviewer",
            "hooks": [
                {
                    "name": "guard",
                    "event": "tool_call",
                    "type": "command",
                    "command": "./guard.sh --strict",
                    "timeout": 60,
                },
                {"name": "audit", "event": "tool_result", "type": "command", "command": "audit", "timeout": 5},
                # An out-of-range timeout falls back to the default rather than being trusted.
                {"name": "slow", "event": "tool_call", "type": "command", "command": "slow", "timeout": 60},
            ],
        },
    }
    assert "_warnings" not in result


@pytest.mark.parametrize(
    ("hook", "reason"),
    [
        (_hook("stop", "Stop"), "does not support the Stop hook event"),
        (_hook("prompt", "UserPromptSubmit"), "does not support the UserPromptSubmit hook event"),
        (_hook("web", handler_type="http", handler_config={"url": "https://x"}), "runs only command hooks"),
        (_hook("script", script_filename="s.sh", script_content="echo"), "does not install hook scripts yet"),
        (_hook("filtered", tool_filter={"tools": ["Bash"]}), "cannot apply a hook tool filter"),
        (_hook("empty", handler_config={"command": "  "}), "has no command"),
    ],
)
def test_unsupported_hooks_are_reported_never_dropped_silently(hook, reason):
    result = _format([hook])
    assert "hooks_config" not in result
    (warning,) = result["_warnings"]
    assert f"Hook '{hook['name']}' was not installed for Pi" in warning
    assert reason in warning


def test_project_scope_hooks_are_reported_not_installed():
    result = _format([_hook("guard")], scope="project")
    assert "hooks_config" not in result
    assert result["_warnings"] == [
        "Hook 'guard' was not installed for Pi: Pi runs registry hooks only from user-scope profiles."
    ]


def test_mixed_hooks_write_the_supported_ones_and_warn_for_the_rest():
    result = _format([_hook("guard"), _hook("stop", "Stop")])
    assert [entry["name"] for entry in result["hooks_config"]["content"]["hooks"]] == ["guard"]
    assert len(result["_warnings"]) == 1


def test_compatibility_warnings_reach_the_pull():
    """Regression: the Pi adapter dropped the server's compatibility warnings, unlike every other adapter."""
    required = "This agent requires 'Prompts' but pi does not support it. Some functionality may not work."
    assert _format([], compatibility_warnings=[required])["_warnings"] == [required]
    result = _format([_hook("stop", "Stop")], compatibility_warnings=[required])
    assert result["_warnings"][0] == required
    assert "Hook 'stop' was not installed for Pi" in result["_warnings"][1]
