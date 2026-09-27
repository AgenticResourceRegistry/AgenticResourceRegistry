# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Canonical harness metadata shared by the CLI and server."""

from __future__ import annotations

from .harness_models import supported_model_ids

# ── Runtime facts vocabulary ─────────────────────────────────
#
# Every harness carries five verified runtime facts that discovery and
# activation gate on. A value is only set to a non-default when the behaviour
# was checked against the harness's own documentation or adapter code; the
# per-harness comment records the evidence. Unknown means the default.
#
# mcp_install_mode        how Observal gets an MCP server into the harness
#   file            written to a config file the harness reads natively
#   setup_command   installed by running the harness's own CLI command
#   adapter         written to a file read by a third-party adapter
#   user_only       only a user-scope config file exists
# dynamic_tools           harness refreshes MCP tools mid-session
#                         (notifications/tools/list_changed). Default False
#                         until verified at runtime.
# prompt_context_injection  a hook or extension can add context before
#                         the model answers a prompt.
# guidance_file_write     Observal may write an instruction file the harness
#                         reads (AGENTS.md, rules, steering). Always False:
#                         the bundled skill is the instruction channel.
# headless_run            the harness CLI runs one prompt non-interactively
#                         and exits, so a delegated agent can run as a child
#                         process (observal_cli.delegation). The CLI adapter's
#                         headless_command builds the argv.

HARNESS_MCP_INSTALL_MODES: tuple[str, ...] = ("file", "setup_command", "adapter", "user_only")

HARNESS_RUNTIME_FACT_KEYS: tuple[str, ...] = (
    "mcp_install_mode",
    "dynamic_tools",
    "prompt_context_injection",
    "guidance_file_write",
    "headless_run",
)

HARNESS_REGISTRY: dict[str, dict] = {
    "cursor": {
        "display_name": "Cursor",
        "capabilities": {"hooks", "mcp_servers"},
        "session_parser": "cursor",
        "scopes": ["project", "user"],
        "default_scope": "project",
        "scope_labels": ("project (.cursor/agents/)", "user (~/.cursor/agents/)"),
        "agent_profile": {
            "project": ".cursor/agents/{name}.md",
            "user": "~/.cursor/agents/{name}.md",
        },
        "agent_profile_format": "markdown_frontmatter",
        "mcp_config": {
            "project": ".cursor/mcp.json",
            "user": "~/.cursor/mcp.json",
        },
        "mcp_servers_key": "mcpServers",
        "skills": {
            "project": ".cursor/skills/{name}/SKILL.md",
            "user": "~/.cursor/skills/{name}/SKILL.md",
        },
        "skill_format": "yaml_frontmatter",
        "hook_type": "command",
        "hooks": {
            "project": ".cursor/hooks.json",
            "user": "~/.cursor/hooks.json",
        },
        "hook_scripts_dir": ".cursor/hooks",
        "hook_events_map": {
            "PreToolUse": "preToolUse",
            "PostToolUse": "postToolUse",
            "Stop": "sessionEnd",
            "SessionStart": "sessionStart",
            "UserPromptSubmit": "beforeSubmitPrompt",
            "SubagentStop": "subagentStop",
        },
        "config_dir": ".cursor",
        # .cursor/mcp.json is read natively. No Observal hook spec exists for
        # Cursor, so no verified context-injection path.
        "mcp_install_mode": "file",
        "dynamic_tools": False,
        "prompt_context_injection": False,
        "guidance_file_write": False,
        # `cursor-agent -p` prints and exits (cursor-agent --help: --print, --mode plan, --trust).
        "headless_run": True,
    },
    "kiro": {
        "display_name": "Kiro",
        "capabilities": {"hooks", "mcp_servers", "skills"},
        "session_parser": "kiro",
        "scopes": ["project", "user"],
        "default_scope": "user",
        "scope_labels": ("project (.kiro/agents/)", "user (~/.kiro/agents/)"),
        "agent_profile": {
            "project": ".kiro/agents/{name}.json",
            "user": "~/.kiro/agents/{name}.json",
        },
        "agent_profile_format": "json",
        "mcp_config": {
            "project": ".kiro/settings/mcp.json",
            "user": "~/.kiro/settings/mcp.json",
        },
        "mcp_servers_key": "mcpServers",
        "skills": {
            "project": ".kiro/skills/{name}/SKILL.md",
            "user": "~/.kiro/skills/{name}/SKILL.md",
        },
        "skill_format": "yaml_frontmatter",
        "hook_type": "command",
        "hooks": {
            "project": ".kiro/hooks/{name}.json",
            "user": "~/.kiro/hooks/{name}.json",
        },
        "hook_scripts_dir": ".kiro/hooks",
        "hook_events_map": {
            "PreToolUse": "preToolUse",
            "PostToolUse": "postToolUse",
            "Stop": "stop",
            "SessionStart": "agentSpawn",
            "UserPromptSubmit": "userPromptSubmit",
        },
        "config_dir": ".kiro",
        # .kiro/settings/mcp.json is read natively. Kiro hooks docs: Prompt
        # Submit hooks can "inject context - feed the agent additional
        # instructions" (kiro.dev/docs/cli/hooks).
        "mcp_install_mode": "file",
        "dynamic_tools": False,
        "prompt_context_injection": True,
        "guidance_file_write": False,
        # `kiro-cli chat --no-interactive --agent <name>` (kiro-cli chat --help).
        "headless_run": True,
    },
    "claude-code": {
        "display_name": "Claude Code",
        "capabilities": {"skills", "hooks", "mcp_servers"},
        "session_parser": "claude-code",
        "invocation_extractor": "claude-code",
        "scopes": ["project", "user"],
        "default_scope": "project",
        "scope_labels": ("project (.claude/agents/)", "user (~/.claude/agents/)"),
        "agent_profile": {
            "project": ".claude/agents/{name}.md",
            "user": "~/.claude/agents/{name}.md",
        },
        "agent_profile_format": "yaml_frontmatter",
        "mcp_config": {
            "project": None,
            "user": None,
        },
        "mcp_servers_key": "mcpServers",
        "skills": {
            "project": ".claude/skills/{name}/SKILL.md",
            "user": "~/.claude/skills/{name}/SKILL.md",
        },
        "skill_format": "yaml_frontmatter",
        "hook_type": "command",
        "hooks": {
            "project": ".claude/settings.json",
            "user": "~/.claude/settings.json",
        },
        "hook_scripts_dir": ".claude/hooks",
        "hook_events_map": {
            "PreToolUse": "PreToolUse",
            "PostToolUse": "PostToolUse",
            "Stop": "Stop",
            "SessionStart": "SessionStart",
            "UserPromptSubmit": "UserPromptSubmit",
            "Notification": "Notification",
            "SubagentStop": "SubagentStop",
        },
        "config_dir": ".claude",
        # mcp_config is None above: servers are installed with `claude mcp add`
        # (services/harness/claude_code.py). UserPromptSubmit hooks return
        # stdout / hookSpecificOutput.additionalContext into the prompt.
        "mcp_install_mode": "setup_command",
        "dynamic_tools": False,
        "prompt_context_injection": True,
        "guidance_file_write": False,
        # `claude -p --agent <name>` (claude --help: --print, --agent, --permission-mode, --mcp-config).
        "headless_run": True,
    },
    "codex": {
        "display_name": "Codex",
        "capabilities": {"mcp_servers", "hooks", "skills"},
        "session_parser": "codex",
        "scopes": ["project", "user"],
        "default_scope": "project",
        "scope_labels": ("project (.codex/agents/)", "user (~/.codex/agents/)"),
        "agent_profile": {
            "project": ".codex/agents/{name}.toml",
            "user": "~/.codex/agents/{name}.toml",
        },
        "agent_profile_format": "toml",
        "mcp_config": {
            "project": ".codex/config.toml",
            "user": "~/.codex/config.toml",
        },
        "mcp_servers_key": "mcp_servers",
        "skills": {
            "project": ".agents/skills/{name}/SKILL.md",
            "user": "~/.agents/skills/{name}/SKILL.md",
        },
        "skill_format": "yaml_frontmatter",
        "hook_type": "command",
        "hooks": {
            "project": ".codex/hooks.json",
            "user": "~/.codex/hooks.json",
        },
        "hook_scripts_dir": ".codex/hooks",
        "hook_events_map": {
            "UserPromptSubmit": "UserPromptSubmit",
            "PreToolUse": "PreToolUse",
            "PostToolUse": "PostToolUse",
            "Stop": "Stop",
        },
        "config_dir": ".codex",
        # .codex/config.toml [mcp_servers] is read natively.
        "mcp_install_mode": "file",
        "dynamic_tools": False,
        "prompt_context_injection": False,
        "guidance_file_write": False,
        # `codex exec` runs one prompt (Codex CLI reference: exec, --sandbox, --cd, --output-last-message).
        "headless_run": True,
    },
    "copilot": {
        "display_name": "Copilot",
        "capabilities": {"mcp_servers", "hooks", "skills", "prompts"},
        "session_parser": "copilot-cli",
        "scopes": ["project"],
        "default_scope": "project",
        "scope_labels": None,
        "agent_profile": {
            "project": ".github/agents/{name}.agent.md",
        },
        "agent_profile_format": "markdown",
        "mcp_config": {
            "project": ".vscode/mcp.json",
        },
        "mcp_servers_key": "servers",
        "skills": {
            "project": ".github/skills/{name}/SKILL.md",
            "user": "~/.copilot/skills/{name}/SKILL.md",
        },
        "skill_format": "yaml_frontmatter",
        "hook_type": "command",
        "hooks": {
            "project": ".github/hooks/{name}.json",
            "user": "~/.copilot/hooks/{name}.json",
        },
        "hook_scripts_dir": ".github/hooks/scripts",
        "hook_events_map": {
            "SessionStart": "SessionStart",
            "UserPromptSubmit": "UserPromptSubmit",
            "PreToolUse": "PreToolUse",
            "PostToolUse": "PostToolUse",
            "Stop": "Stop",
        },
        "config_dir": ".vscode",
        # .vscode/mcp.json is read natively.
        "mcp_install_mode": "file",
        "dynamic_tools": False,
        "prompt_context_injection": False,
        "guidance_file_write": False,
        # VS Code Copilot has no CLI entry point; Copilot CLI covers headless runs.
        "headless_run": False,
    },
    "copilot-cli": {
        "display_name": "Copilot CLI",
        "capabilities": {"mcp_servers", "hooks", "skills", "prompts"},
        "session_parser": "copilot-cli",
        "scopes": ["project"],
        "default_scope": "project",
        "scope_labels": None,
        "agent_profile": {
            "project": ".github/agents/{name}.agent.md",
        },
        "agent_profile_format": "markdown",
        "mcp_config": {
            "project": ".mcp.json",
        },
        "mcp_servers_key": "mcpServers",
        "skills": {
            "project": ".agents/skills/{name}/SKILL.md",
            "user": "~/.copilot/skills/{name}/SKILL.md",
        },
        "skill_format": "yaml_frontmatter",
        "hook_type": "command",
        "hooks": {
            "project": ".github/hooks/{name}.json",
            "user": "~/.copilot/hooks/{name}.json",
        },
        "hook_scripts_dir": ".github/hooks/scripts",
        "hook_events_map": {
            "SessionStart": "sessionStart",
            "UserPromptSubmit": "userPromptSubmitted",
            "PreToolUse": "preToolUse",
            "PostToolUse": "postToolUse",
            "Stop": "sessionEnd",
        },
        "config_dir": ".copilot",
        # Project .mcp.json is read natively; ~/.copilot/mcp-config.json is user-only.
        "mcp_install_mode": "file",
        "dynamic_tools": False,
        "prompt_context_injection": False,
        "guidance_file_write": False,
        # `copilot -p <prompt> --agent <name>` (GitHub Copilot CLI docs).
        "headless_run": True,
    },
    "opencode": {
        "display_name": "OpenCode",
        "session_parser": "opencode",
        "capabilities": {"skills", "hooks", "mcp_servers"},
        "scopes": ["project", "user"],
        "default_scope": "user",
        "scope_labels": ("project (.opencode/agents/<name>.md)", "user (~/.config/opencode/agents/<name>.md)"),
        "agent_profile": {
            "project": ".opencode/agents/{name}.md",
            "user": "~/.config/opencode/agents/{name}.md",
        },
        "agent_profile_format": "markdown",
        "mcp_config": {
            "project": "opencode.json",
            "user": "~/.config/opencode/opencode.json",
        },
        "mcp_servers_key": "mcp",
        "skills": {
            "project": ".opencode/skills/{name}/SKILL.md",
            "user": "~/.config/opencode/skills/{name}/SKILL.md",
        },
        "skill_format": "yaml_frontmatter",
        "hook_type": "plugin",
        "hooks": {
            "project": ".opencode/plugins/{name}.ts",
            "user": "~/.config/opencode/plugins/{name}.ts",
        },
        "hook_scripts_dir": ".opencode/hooks",
        "hook_events_map": {
            "PreToolUse": "tool.execute.before",
            "PostToolUse": "tool.execute.after",
            "Stop": "session.idle",
            "SessionStart": "session.created",
            "UserPromptSubmit": "message.updated",
        },
        "config_dir": ".config/opencode",
        # opencode.json "mcp" block is read natively.
        "mcp_install_mode": "file",
        "dynamic_tools": False,
        "prompt_context_injection": False,
        "guidance_file_write": False,
        # `opencode run --agent <name> <message>` (opencode.ai/docs/cli).
        "headless_run": True,
    },
    "antigravity": {
        "display_name": "Antigravity",
        "capabilities": {"hooks", "mcp_servers", "skills"},
        "session_parser": "antigravity",
        "scopes": ["project", "user"],
        "default_scope": "user",
        "scope_labels": ("project (.agents/)", "user (~/.gemini/antigravity-cli/)"),
        "agent_profile": {
            "project": ".agents/agents/{name}/agent.json",
            "user": "~/.gemini/antigravity-cli/agents/{name}/agent.json",
        },
        "agent_profile_format": "json",
        "mcp_config": {
            "project": ".agents/mcp_config.json",
            "user": "~/.gemini/antigravity-cli/mcp_config.json",
        },
        "mcp_servers_key": "mcpServers",
        "skills": {
            "project": ".agents/skills/{name}/SKILL.md",
            "user": "~/.gemini/antigravity-cli/skills/{name}/SKILL.md",
        },
        "skill_format": "yaml_frontmatter",
        "hook_type": "command",
        "hooks": {
            "project": ".agents/hooks.json",
            "user": "~/.gemini/config/hooks.json",
        },
        "hook_scripts_dir": ".agents/hooks",
        "hook_events_map": {
            "PreToolUse": "PreToolUse",
            "PostToolUse": "PostToolUse",
            "Stop": "Stop",
            "SessionStart": "SessionStart",
            "UserPromptSubmit": "PreInvocation",
        },
        "config_dir": ".agents",
        # .agents/mcp_config.json is read natively.
        "mcp_install_mode": "file",
        "dynamic_tools": False,
        "prompt_context_injection": False,
        "guidance_file_write": False,
        # `agy --print <prompt>` (agy --help: --print, --print-timeout, --sandbox).
        "headless_run": True,
    },
    "goose": {
        "display_name": "Goose",
        "capabilities": {"skills", "hooks", "mcp_servers"},
        "session_parser": "goose",
        "scopes": ["project", "user"],
        "default_scope": "user",
        "scope_labels": ("project (.agents/agents/)", "user (~/.agents/agents/)"),
        "agent_profile": {
            "project": ".agents/agents/{name}.md",
            "user": "~/.agents/agents/{name}.md",
        },
        "agent_profile_format": "yaml_frontmatter",
        # Goose only reads extensions from its single user-level config file.
        "mcp_config": {
            "project": None,
            "user": "~/.config/goose/config.yaml",
        },
        "mcp_servers_key": "extensions",
        "home_mcp_config": "~/.config/goose/config.yaml",
        "skills": {
            "project": ".agents/skills/{name}/SKILL.md",
            "user": "~/.agents/skills/{name}/SKILL.md",
        },
        "skill_format": "yaml_frontmatter",
        "hook_type": "plugin",
        "hooks": {
            "project": ".agents/plugins/observal/hooks/hooks.json",
            "user": "~/.agents/plugins/observal/hooks/hooks.json",
        },
        "hook_scripts_dir": ".agents/plugins/observal/scripts",
        "hook_events_map": {
            "PreToolUse": "PreToolUse",
            "PostToolUse": "PostToolUse",
            "Stop": "Stop",
            "SessionStart": "SessionStart",
            "SessionEnd": "SessionEnd",
            "UserPromptSubmit": "UserPromptSubmit",
        },
        "config_dir": ".config/goose",
        # Goose reads extensions only from the single user-level config.yaml
        # (mcp_config.project is None above).
        "mcp_install_mode": "user_only",
        "dynamic_tools": False,
        "prompt_context_injection": False,
        "guidance_file_write": False,
        # Not verified: goose runs recipes headless, but agent profiles are not recipes.
        "headless_run": False,
    },
    "pi": {
        "display_name": "Pi",
        "capabilities": {"skills", "hooks", "mcp_servers"},
        "session_parser": "pi",
        "scopes": ["project", "user"],
        "default_scope": "user",
        "scope_labels": ("project (.pi/)", "user (~/.pi/agent/)"),
        "agent_profile": {
            "project": "AGENTS.md",
            "user": "~/.pi/agent/AGENTS.md",
        },
        "agent_profile_format": "markdown",
        "mcp_config": {
            "project": ".pi/mcp.json",
            "user": "~/.pi/agent/mcp.json",
        },
        "mcp_servers_key": "mcpServers",
        "skills": {
            "project": ".pi/skills/{name}/SKILL.md",
            "user": "~/.pi/agent/skills/{name}/SKILL.md",
        },
        "skill_format": "yaml_frontmatter",
        "hook_type": "extension",
        "hooks": {
            "user": "~/.pi/agent/settings.json",
        },
        "hook_scripts_dir": None,
        "hook_events_map": {},
        "config_dir": ".pi",
        # Pi has no native MCP; ~/.pi/agent/mcp.json is read by the third-party
        # pi-mcp-adapter. A Pi extension's before_agent_start handler can
        # modify the system prompt for the turn (pi docs/extensions.md).
        "mcp_install_mode": "adapter",
        "dynamic_tools": False,
        "prompt_context_injection": True,
        "guidance_file_write": False,
        # Verified against pi 0.87: `pi -p --session-id <id> -- <prompt>` answers
        # on stdout and exits.
        "headless_run": True,
    },
}


_GUIDANCE_FILES = {
    "cursor": [".cursor/rules/*.mdc", "AGENTS.md"],
    "kiro": [".kiro/steering/*.md", "~/.kiro/steering/*.md", "AGENTS.md"],
    "claude-code": ["CLAUDE.md", ".claude/CLAUDE.md", "~/.claude/CLAUDE.md", "CLAUDE.local.md"],
    "codex": ["AGENTS.md", "AGENTS.override.md", "~/.codex/AGENTS.md"],
    "copilot": [
        ".github/copilot-instructions.md",
        ".github/instructions/*.instructions.md",
        "AGENTS.md",
        "CLAUDE.md",
        "GEMINI.md",
    ],
    "copilot-cli": [
        ".github/copilot-instructions.md",
        ".github/instructions/**/*.instructions.md",
        "AGENTS.md",
        "~/.copilot/copilot-instructions.md",
    ],
    "opencode": ["opencode.json", "~/.config/opencode/opencode.json"],
    "antigravity": ["AGENTS.md", "GEMINI.md"],
    "goose": [".goosehints", "~/.config/goose/.goosehints", "AGENTS.md"],
    "pi": ["AGENTS.md", "~/.pi/agent/AGENTS.md", ".pi/SYSTEM.md", ".pi/APPEND_SYSTEM.md"],
}

for _harness, _spec in HARNESS_REGISTRY.items():
    _spec["guidance_files"] = _GUIDANCE_FILES.get(_harness, [])
    _spec["model_catalog_file"] = f"harness_models/{_harness}.json"
    _spec["supported_models"] = supported_model_ids(_harness)


# ── Derived helpers ──────────────────────────────────────────


def get_valid_harnesses() -> list[str]:
    """Return the canonical list of valid harness names."""
    return list(HARNESS_REGISTRY.keys())


def get_harness_capability_matrix() -> dict[str, set[str]]:
    """Return {harness: capability_set} mapping for all registered harnesses."""
    return {harness: spec["capabilities"] for harness, spec in HARNESS_REGISTRY.items()}


def get_harness_display_names() -> dict[str, str]:
    """Return {harness: display_name} mapping for all registered harnesses."""
    return {harness: spec["display_name"] for harness, spec in HARNESS_REGISTRY.items()}


def get_scope_aware_harnesses() -> dict[str, tuple[str, str]]:
    """Return harnesses that support project/user scope selection, with labels."""
    return {harness: spec["scope_labels"] for harness, spec in HARNESS_REGISTRY.items() if spec.get("scope_labels")}


def get_default_scope(harness: str) -> str:
    """Return the default install scope for an harness."""
    return HARNESS_REGISTRY.get(harness, {}).get("default_scope", "project")


def get_model_choice_harnesses() -> list[str]:
    """Return harnesses with registry-backed model choices."""
    return [harness for harness, spec in HARNESS_REGISTRY.items() if spec.get("supported_models")]


def has_model_selection(harness: str) -> bool:
    """Return True if this harness has registry-backed model choices."""
    return bool(HARNESS_REGISTRY.get(harness, {}).get("supported_models"))


def get_session_parser_id(harness: str) -> str:
    """Return the registered session parser ID for a harness."""
    return HARNESS_REGISTRY[harness]["session_parser"]


def get_harness_runtime_facts(harness: str) -> dict[str, str | bool]:
    """Return the verified runtime facts for a harness (see HARNESS_RUNTIME_FACT_KEYS)."""
    spec = HARNESS_REGISTRY[harness]
    return {key: spec[key] for key in HARNESS_RUNTIME_FACT_KEYS}


def get_harnesses_with_fact(fact: str, value: str | bool = True) -> list[str]:
    """Return harnesses whose runtime fact ``fact`` equals ``value``."""
    if fact not in HARNESS_RUNTIME_FACT_KEYS:
        raise KeyError(f"Unknown harness runtime fact: {fact}")
    return [harness for harness, spec in HARNESS_REGISTRY.items() if spec[fact] == value]
