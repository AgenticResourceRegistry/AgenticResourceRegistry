# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Startup refresh of Observal's own Claude Code hook groups."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from observal_cli import settings_reconciler as rec
from observal_cli.harness_specs import claude_code_hooks_spec as spec


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    monkeypatch.setattr(rec, "CLAUDE_SETTINGS_PATH", settings)
    monkeypatch.setattr(rec, "RECORD_PATH", tmp_path / ".observal" / "managed-claude-hooks.json")
    monkeypatch.setattr(rec.config, "CONFIG_DIR", tmp_path / ".observal")
    monkeypatch.setattr(rec.config, "CONFIG_FILE", tmp_path / ".observal" / "config.json", raising=False)
    monkeypatch.setattr(rec.config, "save", lambda *_a, **_k: None)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return settings


def _older(hooks: dict) -> dict:
    """The spec as an earlier release would have shipped it."""
    old = copy.deepcopy(hooks)
    for groups in old.values():
        for group in groups:
            group[next(k for k in group if k != "hooks")]["version"] = "12"
    return old


def _install_old(settings: Path, foreign: dict | None = None) -> dict:
    old = _older(spec.get_desired_hooks())
    body = {"theme": "dark", "hooks": {**old, **(foreign or {})}}
    settings.write_text(json.dumps(body, indent=2) + "\n")
    rec._write_record(rec._observal_hashes(old))
    return old


def test_unedited_old_groups_are_replaced_and_everything_else_is_kept(env: Path) -> None:
    foreign = {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "my-linter"}]}]}
    _install_old(env, foreign)
    env.chmod(0o640)
    status, reason = rec.refresh_unedited()
    assert status == "updated", reason
    data = json.loads(env.read_text())
    assert data["theme"] == "dark" and data["hooks"]["PreToolUse"] == foreign["PreToolUse"]
    assert data["hooks"]["Stop"] == spec.get_desired_hooks()["Stop"]
    assert env.stat().st_mode & 0o777 == 0o640
    assert rec.refresh_unedited() == ("current", "")  # Idempotent, and now recorded as current.
    assert not list(env.parent.glob(".settings.*"))


def test_edited_or_unrecorded_groups_are_never_replaced(env: Path) -> None:
    old = _install_old(env)
    before = env.read_bytes()
    data = json.loads(before)
    data["hooks"]["Stop"][0]["hooks"][0]["command"] = "python my_own_push.py"
    env.write_text(json.dumps(data, indent=2) + "\n")
    edited = env.read_bytes()
    status, reason = rec.refresh_unedited()
    assert status == "manual" and "edited" in reason and "doctor patch" in reason
    assert env.read_bytes() == edited
    # No record at all: Observal never infers ownership of what it finds.
    env.write_bytes(before)
    rec.RECORD_PATH.unlink()
    status, reason = rec.refresh_unedited()
    assert status == "manual" and "No ownership record" in reason
    assert env.read_bytes() == before
    assert old  # silence unused


def test_symlink_unreadable_and_foreign_config_dir_are_manual(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_old(env)
    real = tmp_path / "real.json"
    real.write_bytes(env.read_bytes())
    env.unlink()
    env.symlink_to(real)
    status, reason = rec.refresh_unedited()
    assert status == "manual" and "link" in reason
    env.unlink()
    env.write_text("{not json")
    assert rec.refresh_unedited()[0] == "manual"
    env.write_text(json.dumps({"hooks": []}))
    assert rec.refresh_unedited()[0] == "manual"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "elsewhere"))
    assert rec.refresh_unedited()[0] == "manual"


def test_a_concurrent_write_by_claude_abandons_the_update(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_old(env)
    original_read = Path.read_bytes
    state = {"reads": 0}

    def racing(self: Path) -> bytes:
        data = original_read(self)
        if self == env:
            state["reads"] += 1
            if state["reads"] == 2:  # The compare-and-swap read, after Claude has written.
                body = json.loads(data)
                body["permissions"] = {"allow": ["Bash(ls)"]}
                env.write_text(json.dumps(body, indent=2) + "\n")
                return original_read(self)
        return data

    monkeypatch.setattr(Path, "read_bytes", racing)
    status, reason = rec.refresh_unedited()
    assert status == "manual" and "changed while updating" in reason
    assert "permissions" in json.loads(env.read_text()), "Claude's own write must survive"
    assert not list(env.parent.glob(".settings.*"))


def test_reconcile_records_ownership_only_for_exactly_generated_groups(env: Path) -> None:
    rec.reconcile(spec.get_desired_hooks(), {})
    recorded = rec._read_record()
    assert recorded and set(recorded) == set(spec.get_desired_hooks())
    assert rec.RECORD_PATH.stat().st_mode & 0o777 == 0o600
    # A hand-edited Observal group is not adopted by a later reconcile.
    data = json.loads(env.read_text())
    data["hooks"]["Stop"][0]["hooks"][0]["command"] = "python mine.py"
    env.write_text(json.dumps(data))
    rec.RECORD_PATH.unlink()
    rec._record_if_generated(json.loads(env.read_text()), spec.get_desired_hooks())
    assert not rec.RECORD_PATH.exists()


def test_no_installed_hooks_means_nothing_is_added(env: Path) -> None:
    env.write_text(json.dumps({"theme": "dark"}, indent=2) + "\n")
    before = env.read_bytes()
    assert rec.refresh_unedited() == ("current", "")
    assert env.read_bytes() == before
    env.write_text(json.dumps({"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "mine"}]}]}}))
    before = env.read_bytes()
    assert rec.refresh_unedited() == ("current", "")
    assert env.read_bytes() == before


@pytest.fixture()
def worker(env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import contextlib

    from observal_cli import auto_update_policy, startup_update_apply
    from observal_cli.hooks import claude_updates

    notice = tmp_path / ".observal" / "claude-hooks-notice.json"
    monkeypatch.setattr(claude_updates, "HOOKS_NOTICE", notice)
    state = {"effective": True}
    monkeypatch.setattr(auto_update_policy, "registry_gate", lambda *_a, **_k: contextlib.nullcontext(), raising=True)
    monkeypatch.setattr(auto_update_policy, "policy_status", lambda _r: {"effective": state["effective"]})
    return startup_update_apply, claude_updates, notice, state


def test_worker_refreshes_only_with_consent_and_reports_once(env: Path, worker) -> None:
    apply, bridge, notice, state = worker
    _install_old(env)
    state["effective"] = False
    apply._refresh_claude_hooks("https://registry.test")
    assert not notice.exists() and "12" in env.read_text(), "frozen means no writes"
    state["effective"] = True
    apply._refresh_claude_hooks("https://registry.test")
    assert notice.stat().st_mode & 0o777 == 0o600
    messages = bridge._hooks_notice(None)
    assert len(messages) == 1 and "updated its Claude Code hooks" in messages[0]
    assert bridge._hooks_notice(None) == [] and not notice.exists(), "delivered once"


def test_refused_refresh_says_why_once_per_spec_version(env: Path, worker) -> None:
    apply, bridge, notice, _state = worker
    _install_old(env)
    data = json.loads(env.read_text())
    data["hooks"]["Stop"][0]["hooks"][0]["command"] = "python mine.py"
    env.write_text(json.dumps(data))
    edited = env.read_bytes()
    apply._refresh_claude_hooks("https://registry.test")
    (message,) = bridge._hooks_notice(None)
    assert "could not update" in message and "edited" in message and "doctor patch" in message
    apply._refresh_claude_hooks("https://registry.test")
    assert not notice.exists(), "the same refusal is not repeated every session"
    assert env.read_bytes() == edited


@pytest.mark.parametrize("removal", ["event deleted", "event emptied"])
def test_a_deliberately_removed_managed_group_is_not_reinstalled(env: Path, removal: str) -> None:
    """Remove just Stop while the other Observal groups stay installed."""
    _install_old(env)
    data = json.loads(env.read_text())
    if removal == "event deleted":
        del data["hooks"]["Stop"]
    else:
        data["hooks"]["Stop"] = []
    env.write_text(json.dumps(data, indent=2) + "\n")
    before = env.read_bytes()
    status, reason = rec.refresh_unedited()
    assert status == "manual" and "removed" in reason and "doctor patch" in reason
    assert env.read_bytes() == before
    assert "Stop" not in json.loads(env.read_text())["hooks"] or json.loads(env.read_text())["hooks"]["Stop"] == []


def test_an_unexpected_refresh_error_is_reported_not_swallowed(
    env: Path, worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    apply, bridge, _notice, _state = worker

    def boom() -> tuple[str, str]:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(rec, "refresh_unedited", boom)
    apply._refresh_claude_hooks("https://registry.test")
    (message,) = bridge._hooks_notice(None)
    assert "could not update" in message and "doctor patch" in message
