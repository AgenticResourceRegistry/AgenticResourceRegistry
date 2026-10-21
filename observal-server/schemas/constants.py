# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Canonical valid-option lists for all registry submit fields.

This module is the single source of truth for constrained field values.
The CLI mirrors these in ``observal_cli/constants.py`` -- a sync test
(``tests/test_constants_sync.py``) ensures they stay in lockstep.

harness-specific data (features, paths, scopes) is defined in
``observal_shared/harness_registry.py``; the lists below are derived from it.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BeforeValidator, StringConstraints

from observal_shared.harness_registry import get_harness_capability_matrix, get_valid_harnesses

# ── Name validation ───────────────────────────────────────────

Visibility = Literal["public", "team"]

# Exact semver with an optional prerelease tag. Release ordering and the
# suggested next versions (1.0.1, 1.1.0, 2.0.0) both parse versions this way.
COMPONENT_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?$")


def _check_component_version(v: str) -> str:
    if not COMPONENT_VERSION_RE.match(v):
        raise ValueError(f"Invalid version '{v}'. Use semver such as 1.2.0 or 1.2.0-beta.1")
    return v


# Bounds match the VARCHAR columns in models/. Without them an over-long value
# reaches PostgreSQL and fails as StringDataRightTruncationError, a 500 rather
# than a 422 that names the field.
ListingName = Annotated[str, StringConstraints(max_length=255)]
ListingOwner = Annotated[str, StringConstraints(max_length=255)]
ListingVersion = Annotated[str, StringConstraints(max_length=50), AfterValidator(_check_component_version)]
ModelName = Annotated[str, StringConstraints(max_length=100)]

AGENT_NAME_REGEX = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# ── harness / client names (hyphen-canonical) ───────────────────
# Derived from HARNESS_REGISTRY key order.
VALID_HARNESSES: list[str] = get_valid_harnesses()

# ── harness feature capabilities ──────────────────────────────────
# HARNESS_CAPABILITY_NAMES defines the vocabulary of possible features (used by
# Pydantic validators).  HARNESS_CAPABILITIES is derived from the
# registry. The same vocabulary is mirrored in observal_cli/constants.py.

HARNESS_CAPABILITY_NAMES: list[str] = [
    "skills",
    "hooks",
    "mcp_servers",
    "prompts",
]

HARNESS_CAPABILITIES: dict[str, set[str]] = get_harness_capability_matrix()

# ── MCP servers ─────────────────────────────────────────────
VALID_MCP_CATEGORIES: list[str] = [
    "browser-automation",
    "cloud-platforms",
    "code-execution",
    "communication",
    "databases",
    "developer-tools",
    "devops",
    "file-systems",
    "finance",
    "knowledge-memory",
    "monitoring",
    "multimedia",
    "productivity",
    "search",
    "security",
    "version-control",
    "ai-ml",
    "data-analytics",
    "general",
]

VALID_MCP_TRANSPORTS: list[str] = [
    "stdio",
    "sse",
    "streamable-http",
]

VALID_MCP_FRAMEWORKS: list[str] = [
    "python",
    "docker",
    "typescript",
    "go",
]

# ── Skills ──────────────────────────────────────────────────
VALID_SKILL_TASK_TYPES: list[str] = [
    "code-review",
    "code-generation",
    "testing",
    "documentation",
    "debugging",
    "refactoring",
    "deployment",
    "security-audit",
    "performance",
    "general",
]

# ── Hooks ───────────────────────────────────────────────────
VALID_HOOK_EVENTS: list[str] = [
    "PreToolUse",
    "PostToolUse",
    "Notification",
    "Stop",
    "SubagentStop",
    "SessionStart",
    "UserPromptSubmit",
]

VALID_HOOK_HANDLER_TYPES: list[str] = [
    "command",
    "http",
]

VALID_HOOK_EXECUTION_MODES: list[str] = [
    "async",
    "sync",
    "blocking",
]

VALID_HOOK_SCOPES: list[str] = [
    "agent",
    "session",
    "global",
]

HOOK_TIMEOUT_CAPS: dict[str, int] = {
    "blocking": 30,
    "sync": 10,
    "async": 60,
}

# ── Prompts ─────────────────────────────────────────────────
VALID_PROMPT_CATEGORIES: list[str] = [
    "system-prompt",
    "code-review",
    "code-generation",
    "testing",
    "documentation",
    "debugging",
    "general",
]

# ── Sandboxes ───────────────────────────────────────────────
VALID_SANDBOX_RUNTIME_TYPES: list[str] = [
    "docker",
    "lxc",
    "firecracker",
    "wasm",
]

VALID_SANDBOX_NETWORK_POLICIES: list[str] = [
    "none",
    "host",
    "bridge",
    "restricted",
]


# ── Pydantic validator helpers ──────────────────────────────


def _normalize_harness(value: str) -> str:
    """Normalize underscore harness names to hyphens (e.g. claude_code -> claude-code)."""
    return value.replace("_", "-")


def make_option_validator(field_name: str, valid_options: list[str]):
    """Return a classmethod suitable for ``@field_validator``."""

    def _check(cls, v: str) -> str:
        if v not in valid_options:
            raise ValueError(f"Invalid {field_name} '{v}'. Valid options: {', '.join(valid_options)}")
        return v

    return classmethod(_check)


def make_name_validator(field_name: str = "name", max_length: int = 64):
    """Return a classmethod that validates slug-style names (no spaces)."""

    def _check(cls, v: str) -> str:
        if not v:
            raise ValueError(f"{field_name} is required")
        if len(v) > max_length:
            raise ValueError(f"{field_name} must be at most {max_length} characters")
        if not AGENT_NAME_REGEX.match(v):
            raise ValueError(
                f"Invalid {field_name} '{v}'. "
                "Must start with a letter or digit and contain only lowercase letters, digits, hyphens, and underscores."
            )
        return v

    return classmethod(_check)


def make_harness_list_validator():
    """Return a classmethod that validates and normalizes each harness in a list."""

    def _check(cls, v: list[str] | None) -> list[str] | None:
        if v is None:  # an omitted field on a partial update
            return v
        normalized = [_normalize_harness(harness) for harness in v]
        invalid = [harness for harness in normalized if harness not in VALID_HARNESSES]
        if invalid:
            raise ValueError(f"Invalid harness(s): {', '.join(invalid)}. Valid options: {', '.join(VALID_HARNESSES)}")
        return normalized

    return classmethod(_check)


# Admin editorial flag. Rows that have not been flushed yet still hold None
# until the column default applies on insert, so coerce instead of rejecting.
RecommendedFlag = Annotated[bool, BeforeValidator(bool)]
