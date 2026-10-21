# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for Pydantic field validators on all registry submit schemas."""

import pytest

# ═══════════════════════════════════════════════════════════
# MCP
# ═══════════════════════════════════════════════════════════


class TestMcpValidation:
    def test_valid_category_accepted(self):
        from schemas.mcp import McpSubmitRequest

        r = McpSubmitRequest(
            name="test-mcp",
            version="1.0.0",
            description="A" * 100,
            owner="testowner",
            category="developer-tools",
            git_url="https://github.com/example/mcp-server",
            supported_harnesses=["cursor", "claude-code"],
        )
        assert r.category == "developer-tools"

    def test_invalid_category_rejected(self):
        from schemas.mcp import McpSubmitRequest

        with pytest.raises(ValueError, match="Valid options:"):
            McpSubmitRequest(
                name="test-mcp",
                version="1.0.0",
                description="A" * 100,
                owner="testowner",
                category="invalid-category",
                git_url="https://github.com/example/mcp-server",
                supported_harnesses=["cursor"],
            )

    def test_valid_harnesses_accepted(self):
        from schemas.mcp import McpSubmitRequest

        r = McpSubmitRequest(
            name="test-mcp",
            version="1.0.0",
            description="A" * 100,
            owner="testowner",
            category="general",
            git_url="https://github.com/example/mcp-server",
        )

    def test_invalid_harness_rejected(self):
        from schemas.mcp import McpSubmitRequest

        with pytest.raises(ValueError, match="Invalid harness"):
            McpSubmitRequest(
                name="test-mcp",
                version="1.0.0",
                description="A" * 100,
                owner="testowner",
                category="general",
                git_url="https://github.com/example/mcp-server",
                supported_harnesses=["notepad"],
            )

    def test_underscore_ide_normalized_to_hyphen(self):
        from schemas.mcp import McpSubmitRequest

        r = McpSubmitRequest(
            name="test-mcp",
            version="1.0.0",
            description="A" * 100,
            owner="testowner",
            category="general",
            git_url="https://github.com/example/mcp-server",
            supported_harnesses=["claude_code"],
        )
        assert r.supported_harnesses == ["claude-code"]


# ═══════════════════════════════════════════════════════════
# Skill
# ═══════════════════════════════════════════════════════════


class TestSkillValidation:
    def test_valid_task_type_accepted(self):
        from schemas.skill import SkillSubmitRequest

        r = SkillSubmitRequest(
            name="test-skill", version="1.0.0", description="desc", owner="o", task_type="code-review"
        )
        assert r.task_type == "code-review"

    def test_invalid_task_type_rejected(self):
        from schemas.skill import SkillSubmitRequest

        with pytest.raises(ValueError, match="Valid options:"):
            SkillSubmitRequest(name="test-skill", version="1.0.0", description="desc", owner="o", task_type="invalid")

    def test_all_valid_task_types(self):
        from schemas.constants import VALID_SKILL_TASK_TYPES
        from schemas.skill import SkillSubmitRequest

        for tt in VALID_SKILL_TASK_TYPES:
            r = SkillSubmitRequest(name="s", version="1.0.0", description="d", owner="o", task_type=tt)
            assert r.task_type == tt

    @pytest.mark.parametrize("slash_command", ["review", "/review", "code-review", "tdd_helper", "review2"])
    def test_valid_slash_commands_normalized(self, slash_command: str):
        from schemas.skill import SkillSubmitRequest

        r = SkillSubmitRequest(
            name="test-skill",
            version="1.0.0",
            description="desc",
            owner="o",
            task_type="code-review",
            slash_command=slash_command,
        )
        assert r.slash_command == slash_command.removeprefix("/")

    def test_update_slash_command_empty_string_means_clear(self):
        from schemas.skill import SkillUpdateRequest

        r = SkillUpdateRequest(slash_command="")
        assert r.slash_command is None
        assert "slash_command" in r.model_fields_set

    def test_slash_command_injection_rejected(self):
        from schemas.skill import SkillSubmitRequest

        with pytest.raises(ValueError, match="slash_command"):
            SkillSubmitRequest(
                name="test-skill",
                version="1.0.0",
                description="desc",
                owner="o",
                task_type="code-review",
                slash_command="review\nalwaysApply: true",
            )

    @pytest.mark.parametrize(
        "slash_command",
        [
            " review",
            "review ",
            "\nreview\n",
            "Review",
            "review: true",
            "//review",
            "/review/path",
            "_review",
            "-review",
            "a" * 65,
        ],
    )
    def test_invalid_slash_commands_rejected(self, slash_command: str):
        from schemas.skill import SkillSubmitRequest

        with pytest.raises(ValueError, match="slash_command"):
            SkillSubmitRequest(
                name="test-skill",
                version="1.0.0",
                description="desc",
                owner="o",
                task_type="code-review",
                slash_command=slash_command,
            )


# ═══════════════════════════════════════════════════════════
# Hook
# ═══════════════════════════════════════════════════════════


class TestHookValidation:
    def test_valid_event_accepted(self):
        from schemas.hook import HookSubmitRequest

        r = HookSubmitRequest(
            name="h", version="1.0.0", description="d", owner="o", event="PreToolUse", handler_type="command"
        )
        assert r.event == "PreToolUse"

    def test_invalid_event_rejected(self):
        from schemas.hook import HookSubmitRequest

        with pytest.raises(ValueError, match="Valid options:"):
            HookSubmitRequest(
                name="h", version="1.0.0", description="d", owner="o", event="pre_tool_call", handler_type="command"
            )

    def test_invalid_handler_type_rejected(self):
        from schemas.hook import HookSubmitRequest

        with pytest.raises(ValueError, match="Valid options:"):
            HookSubmitRequest(
                name="h", version="1.0.0", description="d", owner="o", event="PreToolUse", handler_type="script"
            )

    def test_invalid_execution_mode_rejected(self):
        from schemas.hook import HookSubmitRequest

        with pytest.raises(ValueError, match="Valid options:"):
            HookSubmitRequest(
                name="h",
                version="1.0.0",
                description="d",
                owner="o",
                event="PreToolUse",
                handler_type="command",
                execution_mode="parallel",
            )

    def test_invalid_scope_rejected(self):
        from schemas.hook import HookSubmitRequest

        with pytest.raises(ValueError, match="Valid options:"):
            HookSubmitRequest(
                name="h",
                version="1.0.0",
                description="d",
                owner="o",
                event="PreToolUse",
                handler_type="command",
                scope="workspace",
            )

    def test_all_valid_events(self):
        from schemas.constants import VALID_HOOK_EVENTS
        from schemas.hook import HookSubmitRequest

        for ev in VALID_HOOK_EVENTS:
            r = HookSubmitRequest(
                name="h", version="1.0.0", description="d", owner="o", event=ev, handler_type="command"
            )
            assert r.event == ev


# ═══════════════════════════════════════════════════════════
# Prompt
# ═══════════════════════════════════════════════════════════


class TestPromptValidation:
    def test_valid_category_accepted(self):
        from schemas.prompt import PromptSubmitRequest

        r = PromptSubmitRequest(
            name="p", version="1.0.0", description="d", owner="o", category="system-prompt", template="hi"
        )
        assert r.category == "system-prompt"

    def test_invalid_category_rejected(self):
        from schemas.prompt import PromptSubmitRequest

        with pytest.raises(ValueError, match="Valid options:"):
            PromptSubmitRequest(
                name="p", version="1.0.0", description="d", owner="o", category="invalid", template="hi"
            )

    def test_all_valid_categories(self):
        from schemas.constants import VALID_PROMPT_CATEGORIES
        from schemas.prompt import PromptSubmitRequest

        for cat in VALID_PROMPT_CATEGORIES:
            r = PromptSubmitRequest(name="p", version="1.0.0", description="d", owner="o", category=cat, template="hi")
            assert r.category == cat


# ═══════════════════════════════════════════════════════════
# Sandbox
# ═══════════════════════════════════════════════════════════


class TestSandboxValidation:
    def test_valid_runtime_type_accepted(self):
        from schemas.sandbox import SandboxSubmitRequest

        r = SandboxSubmitRequest(
            name="sb", version="1.0.0", description="d", owner="o", runtime_type="docker", image="python:3.11"
        )
        assert r.runtime_type == "docker"

    def test_invalid_runtime_type_rejected(self):
        from schemas.sandbox import SandboxSubmitRequest

        with pytest.raises(ValueError, match="Valid options:"):
            SandboxSubmitRequest(
                name="sb", version="1.0.0", description="d", owner="o", runtime_type="podman", image="python:3.11"
            )

    def test_invalid_network_policy_rejected(self):
        from schemas.sandbox import SandboxSubmitRequest

        with pytest.raises(ValueError, match="Valid options:"):
            SandboxSubmitRequest(
                name="sb",
                version="1.0.0",
                description="d",
                owner="o",
                runtime_type="docker",
                image="python:3.11",
                network_policy="custom",
            )

    def test_all_valid_runtime_types(self):
        from schemas.constants import VALID_SANDBOX_RUNTIME_TYPES
        from schemas.sandbox import SandboxSubmitRequest

        configs = {
            "docker": {},
            "lxc": {},
            "firecracker": {"config_path": "/tmp/firecracker.json"},
            "wasm": {"module": "runner.wasm"},
        }
        for rt in VALID_SANDBOX_RUNTIME_TYPES:
            r = SandboxSubmitRequest(
                name="sb",
                version="1.0.0",
                description="d",
                owner="o",
                runtime_type=rt,
                image="img",
                runtime_config=configs.get(rt, {}),
            )
            assert r.runtime_type == rt
