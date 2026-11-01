# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Validate HARNESS_REGISTRY structural invariants.

Catches misconfigurations early: missing keys, invalid scopes, features
that don't exist in the canonical HARNESS_CAPABILITY_NAMES list, etc.
"""

from __future__ import annotations

import pytest

from observal_shared.harness_registry import (
    HARNESS_MCP_INSTALL_MODES,
    HARNESS_REGISTRY,
    HARNESS_RUNTIME_FACT_KEYS,
    get_harness_runtime_facts,
    get_harnesses_with_fact,
)
from schemas.constants import HARNESS_CAPABILITY_NAMES

REQUIRED_KEYS = {
    "display_name",
    *HARNESS_RUNTIME_FACT_KEYS,
    "capabilities",
    "scopes",
    "default_scope",
    "scope_labels",
    "agent_profile",
    "agent_profile_format",
    "mcp_config",
    "mcp_servers_key",
    "skills",
    "skill_format",
    "hook_type",
    "config_dir",
}


@pytest.mark.parametrize("harness", list(HARNESS_REGISTRY.keys()))
def test_registry_has_required_keys(harness):
    spec = HARNESS_REGISTRY[harness]
    missing = REQUIRED_KEYS - set(spec.keys())
    assert not missing, f"harness {harness!r} missing keys: {missing}"


@pytest.mark.parametrize("harness", list(HARNESS_REGISTRY.keys()))
def test_default_scope_is_valid(harness):
    spec = HARNESS_REGISTRY[harness]
    assert spec["default_scope"] in spec["scopes"], (
        f"harness {harness!r}: default_scope {spec['default_scope']!r} not in scopes {spec['scopes']!r}"
    )


@pytest.mark.parametrize("harness", list(HARNESS_REGISTRY.keys()))
def test_features_are_valid(harness):
    spec = HARNESS_REGISTRY[harness]
    invalid = spec["capabilities"] - set(HARNESS_CAPABILITY_NAMES)
    assert not invalid, f"harness {harness!r} has invalid capabilities: {invalid}"


@pytest.mark.parametrize("harness", list(HARNESS_REGISTRY.keys()))
def test_agent_profile_has_scope_entries(harness):
    spec = HARNESS_REGISTRY[harness]
    for scope in spec["scopes"]:
        assert scope in spec["agent_profile"], (
            f"harness {harness!r}: scope {scope!r} not in agent_profile keys {list(spec['agent_profile'].keys())!r}"
        )


@pytest.mark.parametrize("harness", list(HARNESS_REGISTRY.keys()))
def test_scope_labels_consistency(harness):
    spec = HARNESS_REGISTRY[harness]
    if len(spec["scopes"]) > 1 and spec["scope_labels"] is not None:
        assert isinstance(spec["scope_labels"], tuple), (
            f"harness {harness!r}: scope_labels should be a tuple, got {type(spec['scope_labels'])}"
        )
        assert len(spec["scope_labels"]) == 2, (
            f"harness {harness!r}: scope_labels should have 2 entries (project, user)"
        )


@pytest.mark.parametrize("harness", list(HARNESS_REGISTRY.keys()))
def test_display_name_is_nonempty(harness):
    assert HARNESS_REGISTRY[harness]["display_name"], f"harness {harness!r} has empty display_name"


def test_no_duplicate_display_names():
    names = [spec["display_name"] for spec in HARNESS_REGISTRY.values()]
    assert len(names) == len(set(names)), f"Duplicate display names: {names}"


def test_all_harnesses_have_features():
    for harness, spec in HARNESS_REGISTRY.items():
        assert len(spec["capabilities"]) > 0, f"harness {harness!r} has no capabilities"


# ── Runtime facts ──────────────────────────────────────────────────
#
# These values were verified against harness docs or adapter code when they
# were recorded (see the per-harness comments in harness_registry.py). A
# change here must come with new evidence, so the expected table is explicit
# rather than derived.

VERIFIED_RUNTIME_FACTS: dict[str, dict[str, str | bool]] = {
    "cursor": {"mcp_install_mode": "file", "prompt_context_injection": False, "headless_run": True},
    "kiro": {"mcp_install_mode": "file", "prompt_context_injection": True, "headless_run": True},
    "claude-code": {"mcp_install_mode": "setup_command", "prompt_context_injection": True, "headless_run": True},
    "codex": {"mcp_install_mode": "file", "prompt_context_injection": False, "headless_run": True},
    "copilot": {"mcp_install_mode": "file", "prompt_context_injection": False, "headless_run": False},
    "copilot-cli": {"mcp_install_mode": "file", "prompt_context_injection": False, "headless_run": True},
    "opencode": {"mcp_install_mode": "file", "prompt_context_injection": False, "headless_run": True},
    "antigravity": {"mcp_install_mode": "file", "prompt_context_injection": False, "headless_run": True},
    "goose": {"mcp_install_mode": "user_only", "prompt_context_injection": False, "headless_run": False},
    "pi": {"mcp_install_mode": "adapter", "prompt_context_injection": True, "headless_run": True},
}


def test_verified_facts_table_covers_every_harness():
    assert set(VERIFIED_RUNTIME_FACTS) == set(HARNESS_REGISTRY)


@pytest.mark.parametrize("harness", list(HARNESS_REGISTRY.keys()))
def test_mcp_install_mode_is_in_vocabulary(harness):
    mode = HARNESS_REGISTRY[harness]["mcp_install_mode"]
    assert mode in HARNESS_MCP_INSTALL_MODES, f"harness {harness!r}: unknown mcp_install_mode {mode!r}"


@pytest.mark.parametrize("harness", list(HARNESS_REGISTRY.keys()))
def test_boolean_facts_are_booleans(harness):
    spec = HARNESS_REGISTRY[harness]
    for key in ("dynamic_tools", "prompt_context_injection", "guidance_file_write", "headless_run"):
        assert isinstance(spec[key], bool), f"harness {harness!r}: {key} must be a bool"


@pytest.mark.parametrize("harness", list(HARNESS_REGISTRY.keys()))
def test_runtime_facts_match_verified_table(harness):
    facts = get_harness_runtime_facts(harness)
    for key, expected in VERIFIED_RUNTIME_FACTS[harness].items():
        assert facts[key] == expected, f"harness {harness!r}: {key}={facts[key]!r}, verified value is {expected!r}"


@pytest.mark.parametrize("harness", list(HARNESS_REGISTRY.keys()))
def test_mcp_install_mode_agrees_with_mcp_config_paths(harness):
    """The install mode must be consistent with which mcp_config paths exist."""
    spec = HARNESS_REGISTRY[harness]
    mode = spec["mcp_install_mode"]
    paths = spec["mcp_config"]
    has_project = bool(paths.get("project"))
    has_user = bool(paths.get("user"))
    if mode == "setup_command":
        assert not has_project and not has_user, (
            f"harness {harness!r}: setup_command mode must not declare config paths"
        )
    elif mode == "user_only":
        assert has_user and not has_project, f"harness {harness!r}: user_only mode needs only a user path"
    else:
        assert has_project or has_user, f"harness {harness!r}: {mode} mode needs at least one config path"


def test_guidance_file_write_is_off_everywhere():
    """The bundled skill is the instruction channel; no harness gets guidance-file writes."""
    assert get_harnesses_with_fact("guidance_file_write", True) == []


def test_dynamic_tools_is_unverified_everywhere():
    """No harness has a recorded runtime verification of tools/list_changed yet."""
    assert get_harnesses_with_fact("dynamic_tools", True) == []


def test_get_harnesses_with_fact_rejects_unknown_fact():
    with pytest.raises(KeyError):
        get_harnesses_with_fact("not_a_fact")


def test_prompt_context_injection_harnesses():
    assert get_harnesses_with_fact("prompt_context_injection") == ["kiro", "claude-code", "pi"]
