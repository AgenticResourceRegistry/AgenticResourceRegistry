# SPDX-FileCopyrightText: 2026 Madhumidha <madhumidha072005@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from observal_cli.harness_specs import claude_code_hooks_spec, cursor_hooks_spec, kiro_hooks_spec
from observal_cli.shared.utils import is_observal_hook_entry, is_observal_matcher_group


def test_hooks_spec_version_is_string():
    version = claude_code_hooks_spec.HOOKS_SPEC_VERSION
    assert isinstance(version, str)
    assert version.isdigit()


def test_claude_get_desired_hooks_has_expected_events():
    hooks = claude_code_hooks_spec.get_desired_hooks()
    assert "UserPromptSubmit" in hooks
    assert "Stop" in hooks
    assert len(hooks) == 2


def test_claude_get_desired_hooks_has_metadata():
    hooks = claude_code_hooks_spec.get_desired_hooks()
    for event, groups in hooks.items():
        for group in groups:
            assert "_observal" in group
            assert group["_observal"]["version"] == claude_code_hooks_spec.HOOKS_SPEC_VERSION


def test_claude_hook_command_uses_forward_slashes_on_windows(monkeypatch):
    # Claude Code runs hooks through Git Bash on Windows, which strips backslashes.
    from observal_cli.shared import launcher

    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(launcher.sys, "executable", r"C:\Users\dev user\uv\python.exe")
    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: True)

    command = claude_code_hooks_spec.get_desired_hooks()["Stop"][0]["hooks"][0]["command"]

    assert command.startswith('"C:/Users/dev user/uv/python.exe" -I -m observal_cli.hooks.session_push')
    assert "\\" not in command


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


def test_cursor_hooks_spec_version_is_string():
    version = cursor_hooks_spec.HOOKS_SPEC_VERSION
    assert isinstance(version, str)
    assert version.isdigit()


def test_cursor_build_hooks_has_expected_events():
    desired = cursor_hooks_spec.build_cursor_hooks()
    assert desired["version"] == 1
    assert set(desired["hooks"]) == {"beforeSubmitPrompt", "stop"}
    for event, entries in desired["hooks"].items():
        assert len(entries) == 1
        assert entries[0]["type"] == "command"
        assert "observal_cli.hooks.session_push" in entries[0]["command"]
        assert "--harness cursor" in entries[0]["command"]


def test_cursor_is_observal_hook_entry_matches_current_and_legacy():
    assert cursor_hooks_spec.is_observal_hook_entry(
        {"type": "command", "command": "python -m observal_cli.hooks.session_push --harness cursor"}
    )
    assert cursor_hooks_spec.is_observal_hook_entry(
        {"type": "command", "command": "python -m observal_cli.hooks.cursor_session_push"}
    )


def test_cursor_is_observal_hook_entry_rejects_foreign():
    assert not cursor_hooks_spec.is_observal_hook_entry({"type": "command", "command": "some-other-script.sh"})
    assert not cursor_hooks_spec.is_observal_hook_entry({"type": "command"})
    assert not cursor_hooks_spec.is_observal_hook_entry("not-a-dict")


def test_cursor_is_current_push_hook_entry_distinguishes_legacy():
    assert cursor_hooks_spec.is_current_push_hook_entry(
        {"type": "command", "command": "python -m observal_cli.hooks.session_push --harness cursor"}
    )
    assert not cursor_hooks_spec.is_current_push_hook_entry(
        {"type": "command", "command": "python -m observal_cli.hooks.cursor_session_push"}
    )
    assert not cursor_hooks_spec.is_current_push_hook_entry({"type": "command", "command": "foreign"})


def test_cursor_merge_hooks_preserves_foreign_and_replaces_observal():
    existing = {
        "version": 1,
        "hooks": {
            "beforeSubmitPrompt": [
                {"command": "foreign", "type": "command"},
                {"command": "python -m observal_cli.hooks.cursor_session_push", "type": "command"},
            ],
            "userHook": [{"command": "keep-me", "type": "command"}],
        },
    }
    merged = cursor_hooks_spec.merge_cursor_hooks(existing)

    assert merged["version"] == 1
    before = merged["hooks"]["beforeSubmitPrompt"]
    assert {"command": "foreign", "type": "command"} in before
    observal_entries = [e for e in before if cursor_hooks_spec.is_observal_hook_entry(e)]
    assert observal_entries == cursor_hooks_spec.build_cursor_hooks()["hooks"]["beforeSubmitPrompt"]
    assert merged["hooks"]["userHook"] == [{"command": "keep-me", "type": "command"}]
    # Idempotent: merging the result again changes nothing.
    assert cursor_hooks_spec.merge_cursor_hooks(merged) == merged


def test_cursor_merge_hooks_builds_file_from_empty():
    merged = cursor_hooks_spec.merge_cursor_hooks(None)
    assert merged == cursor_hooks_spec.build_cursor_hooks()
