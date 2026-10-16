# SPDX-FileCopyrightText: 2026 Madhumidha <madhumidha072005@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from observal_cli.harness_specs import claude_code_hooks_spec, kiro_hooks_spec
from observal_cli.shared.utils import is_observal_hook_entry, is_observal_matcher_group


def test_hooks_spec_version_is_string():
    version = claude_code_hooks_spec.HOOKS_SPEC_VERSION
    assert isinstance(version, str)
    assert version.isdigit()


def test_claude_get_desired_hooks_has_expected_events():
    hooks = claude_code_hooks_spec.get_desired_hooks()
    assert "UserPromptSubmit" in hooks
    assert "Stop" in hooks
    assert "SessionStart" in hooks
    assert "SessionEnd" in hooks
    assert len(hooks) == 4


def test_claude_get_desired_hooks_has_metadata():
    hooks = claude_code_hooks_spec.get_desired_hooks()
    for event, groups in hooks.items():
        for group in groups:
            assert "_observal" in group
            assert group["_observal"]["version"] == claude_code_hooks_spec.HOOKS_SPEC_VERSION


def test_claude_get_desired_env():
    env = claude_code_hooks_spec.get_desired_env()
    assert env == {}


def test_is_observal_hook_entry_matches_new_path():
    assert is_observal_hook_entry({"type": "command", "command": "python -m observal_cli.hooks.session_push"})


def test_is_observal_hook_entry_matches_legacy():
    assert is_observal_hook_entry({"type": "command", "command": "observal-hook"})
    assert is_observal_hook_entry({"type": "command", "command": "observal-stop-hook"})
    assert is_observal_hook_entry({"type": "http", "url": "/api/v1/otel/hooks"})


def test_is_observal_hook_entry_rejects_foreign():
    assert not is_observal_hook_entry({"type": "command", "command": "some-other-script.sh"})
    assert not is_observal_hook_entry({"type": "http", "url": "http://localhost:8080/api"})


def test_is_observal_matcher_group_matches_metadata():
    assert is_observal_matcher_group({"_observal": {"version": "1"}})


def test_is_observal_matcher_group_matches_legacy():
    group = {"hooks": [{"type": "command", "command": "observal-hook"}]}
    assert is_observal_matcher_group(group)


def test_is_observal_matcher_group_rejects_foreign():
    group = {"hooks": [{"type": "command", "command": "foreign-hook.sh"}]}
    assert not is_observal_matcher_group(group)


def test_kiro_build_hooks_returns_expected_events():
    hooks = kiro_hooks_spec.build_kiro_hooks()
    assert "userPromptSubmit" in hooks
    assert "stop" in hooks
    assert len(hooks) == 2


def test_kiro_build_hooks_with_agent_id():
    hooks = kiro_hooks_spec.build_kiro_hooks(agent_id="test-agent")
    cmd = hooks["userPromptSubmit"][0]["command"]
    assert "OBSERVAL_AGENT_ID=test-agent" in cmd or 'set "OBSERVAL_AGENT_ID=test-agent"' in cmd
