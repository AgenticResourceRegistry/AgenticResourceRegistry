# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import shlex
from unittest.mock import patch

import pytest
from typer.main import get_command
from typer.testing import CliRunner

from observal_cli.main import app

runner = CliRunner()


def _command_tree(command, path="observal"):
    yield path, command
    if isinstance(getattr(command, "commands", None), dict):
        for name, child in command.commands.items():
            yield from _command_tree(child, f"{path} {name}")


def _help_examples(help_text: str) -> list[str]:
    marker = "Examples:" if "Examples:" in help_text else "Example:"
    if marker not in help_text:
        return []

    lines = help_text.split(marker, 1)[1].splitlines()
    examples: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if line != "observal" and not line.startswith("observal "):
            continue
        parts = [line]
        while parts[-1].endswith("\\") and index < len(lines):
            parts[-1] = parts[-1][:-1].rstrip()
            parts.append(lines[index].strip())
            index += 1
        examples.append(" ".join(parts))
    return examples


def _parse_without_invoking(command, args: list[str], parent=None) -> None:
    """Parse a command chain without invoking command callbacks."""
    ctx = command.make_context(command.name or "observal", args, parent=parent)
    try:
        if isinstance(getattr(command, "commands", None), dict):
            protected = list(getattr(ctx, "_protected_args", ()))
            remaining = [*protected, *ctx.args]
            if remaining:
                _, child, child_args = command.resolve_command(ctx, remaining)
                _parse_without_invoking(child, child_args, parent=ctx)
    finally:
        ctx.close()


def _assert_example_parses(command, path: str, example: str) -> None:
    tokens = shlex.split(example, comments=True)
    prefix = shlex.split(path)
    args = tokens[len(prefix) :]
    for operator in ("|", ">", ">>"):
        if operator in args:
            args = args[: args.index(operator)]
    _parse_without_invoking(command, args)


def test_every_cli_help_screen_has_copyable_examples():
    for path, command in _command_tree(get_command(app)):
        examples = _help_examples(command.help or "")
        assert 1 <= len(examples) <= 3, path
        assert all(example == path or example.startswith(f"{path} ") for example in examples), path
        for example in examples:
            _assert_example_parses(command, path, example)


@pytest.mark.parametrize("component", ["mcp", "skill", "hook", "sandbox"])
def test_submit_rejects_removed_example_flag(component):
    with patch("observal_cli.client.post") as post:
        result = runner.invoke(app, ["registry", component, "submit", "--example"])

    assert result.exit_code == 2
    assert "No such option" in result.output
    post.assert_not_called()


def test_skill_submit_flags_post_payload(tmp_path):
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("---\nname: frontend-helper\ndescription: Helps frontends\n---\n\nUse design systems.\n")

    with (
        patch("observal_cli.config.load", return_value={"username": "me"}),
        patch("observal_cli.client.post", return_value={"id": "skill-1", "validated": True}) as post,
    ):
        result = runner.invoke(
            app,
            [
                "registry",
                "skill",
                "submit",
                "--skill-md",
                str(skill_md),
                "--delivery-mode",
                "registry_direct",
                "--name",
                "frontend-helper",
                "--description",
                "Helps frontends",
                "--task-type",
                "general",
                "--target-agent",
                "designer",
                "--harness",
                "claude-code",
            ],
        )

    assert result.exit_code == 0, result.output
    assert post.call_args[0][0] == "/api/v1/skills/submit"
    payload = post.call_args[0][1]
    assert payload["name"] == "frontend-helper"
    assert payload["target_agents"] == ["designer"]
    assert payload["supported_harnesses"] == ["claude-code"]
    assert payload["delivery_mode"] == "registry_direct"


def test_hook_submit_flags_post_payload():
    with (
        patch("observal_cli.config.load", return_value={"username": "me"}),
        patch("observal_cli.client.post", return_value={"id": "hook-1"}) as post,
    ):
        result = runner.invoke(
            app,
            [
                "registry",
                "hook",
                "submit",
                "--name",
                "guard",
                "--description",
                "Guard files",
                "--event",
                "UserPromptSubmit",
                "--handler-command",
                "./guard.sh",
                "--timeout",
                "5",
                "--execution-mode",
                "sync",
                "--scope",
                "agent",
                "--harness",
                "kiro",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = post.call_args[0][1]
    assert payload["handler_type"] == "command"
    assert payload["handler_config"] == {"command": "./guard.sh", "timeout": 5}
    assert payload["execution_mode"] == "sync"
    assert payload["supported_harnesses"] == ["kiro"]


def test_prompt_submit_flags_post_payload():
    with (
        patch("observal_cli.config.load", return_value={"username": "me"}),
        patch("observal_cli.client.post", return_value={"id": "prompt-1"}) as post,
    ):
        result = runner.invoke(
            app,
            [
                "registry",
                "prompt",
                "submit",
                "--name",
                "frontend-brief",
                "--description",
                "Frontend design brief",
                "--category",
                "general",
                "--template",
                "Design {{component}} accessibly",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = post.call_args[0][1]
    assert payload["name"] == "frontend-brief"
    assert payload["template"] == "Design {{component}} accessibly"


def test_sandbox_submit_flags_post_payload():
    with (
        patch("observal_cli.config.load", return_value={"username": "me"}),
        patch("observal_cli.client.post", return_value={"id": "sandbox-1"}) as post,
    ):
        result = runner.invoke(
            app,
            [
                "registry",
                "sandbox",
                "submit",
                "--name",
                "node-runner",
                "--description",
                "Node sandbox",
                "--runtime-type",
                "docker",
                "--image",
                "node:22-alpine",
                "--resource-limits",
                '{"memory_mb": 512}',
                "--network-policy",
                "none",
                "--entrypoint",
                "node",
                "--harness",
                "claude-code",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = post.call_args[0][1]
    assert payload["image"] == "node:22-alpine"
    assert payload["resource_limits"] == {"memory_mb": 512}
    assert payload["entrypoint"] == "node"
    assert payload["supported_harnesses"] == ["claude-code"]
