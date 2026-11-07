# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Project-only cleanup must match patch destinations without touching other profiles."""

import json
from pathlib import Path

import pytest

from observal_cli import cmd_doctor as doctor
from observal_cli import lockfile

OWNED = {"command": "python -m observal_cli.hooks.session_push --harness kiro"}
FOREIGN = {"command": "echo user-hook", "timeout_ms": 1234}
UNIVERSAL_OWNED = {
    "name": "observal-session-push-stop",
    "trigger": "Stop",
    "action": {"type": "command", "command": OWNED["command"]},
}
UNIVERSAL_FOREIGN = {
    "name": "user-check",
    "trigger": "Stop",
    "action": {"type": "command", "command": "echo user-hook"},
    "custom": {"keep": True},
}


@pytest.fixture
def cleanup_state(tmp_path, monkeypatch):
    """Provide an isolated home and the current registry inventory for cleanup."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    registry = {"harnesses": {"kiro": {"agents": []}}}
    monkeypatch.setattr(lockfile, "read_registry_lockfile", lambda: ({}, registry))
    return home, registry


def write_profile(path, universal=False):
    """Return original content with owned hooks, user hooks and unrelated settings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    hooks = (
        [UNIVERSAL_OWNED, UNIVERSAL_FOREIGN, "future-entry"]
        if universal
        else {
            "stop": [OWNED, FOREIGN, "future-entry"],
            "custom": {"preserve": True},
        }
    )
    data = {"name": path.stem, "hooks": hooks, "tools": ["read"], "kiroUpgrade": {"keep": True}}
    path.write_text(json.dumps(data))
    return data


def expected_profile(data, universal):
    """Describe the expected profile after removing only owned hook entries."""
    return {
        **data,
        "hooks": [UNIVERSAL_FOREIGN, "future-entry"]
        if universal
        else {
            "stop": [FOREIGN, "future-entry"],
            "custom": {"preserve": True},
        },
    }


@pytest.mark.parametrize("universal", [False, True], ids=["legacy", "universal"])
@pytest.mark.parametrize("with_home_profiles", [False, True], ids=["project-only", "mixed"])
def test_cleanup_locked_project_profiles(cleanup_state, tmp_path, universal, with_home_profiles):
    """Clean the locked alias in either hook format without touching untracked profiles."""
    home, registry = cleanup_state
    project = tmp_path / "project with spaces"
    profile = project / ".kiro/agents/local-alias.json"
    original = write_profile(profile, universal)
    registry["harnesses"]["kiro"]["agents"] = [
        {
            "id": "agent-id",
            "scope": "project",
            "directory": str(project),
            "local_name": "local-alias",
            "slug": "wrong-slug",
        },
    ]
    unrelated = project / ".kiro/agents/not-locked.json"
    write_profile(unrelated, universal)
    untouched = unrelated.read_bytes()
    if with_home_profiles:
        user_profile = home / ".kiro/agents/user.json"
        user_original = write_profile(user_profile, universal)
    assert doctor._cleanup_kiro(dry_run=False)
    assert json.loads(profile.read_text()) == expected_profile(original, universal)
    assert unrelated.read_bytes() == untouched
    if with_home_profiles:
        assert json.loads(user_profile.read_text()) == expected_profile(user_original, universal)
    assert not doctor._cleanup_kiro(dry_run=False)


@pytest.mark.parametrize("universal", [False, True])
def test_project_only_dry_run_preserves_all_bytes(cleanup_state, tmp_path, universal):
    """Dry-run reports work without modifying inline or standalone hook bytes."""
    _, registry = cleanup_state
    project = tmp_path / "project"
    profile = project / ".kiro/agents/probe.json"
    write_profile(profile, universal)
    hooks = project / ".kiro/hooks/observal.json"
    hooks.parent.mkdir()
    hooks.write_text(json.dumps({"version": "v1", "hooks": [UNIVERSAL_OWNED, UNIVERSAL_FOREIGN]}))
    registry["harnesses"]["kiro"]["agents"] = [{"scope": "project", "directory": str(project), "name": "probe"}]
    before = {path: path.read_bytes() for path in [profile, hooks]}
    assert doctor._cleanup_kiro(dry_run=True)
    assert all(path.read_bytes() == content for path, content in before.items())
    assert doctor._cleanup_kiro(dry_run=False)
    assert json.loads(hooks.read_text())["hooks"] == [UNIVERSAL_FOREIGN]


@pytest.mark.parametrize("bad_content", ["{", "[]"])
def test_unreadable_profile_does_not_block_other_scopes(cleanup_state, tmp_path, bad_content):
    """Malformed user profiles remain unchanged while readable project cleanup proceeds."""
    home, registry = cleanup_state
    bad = home / ".kiro/agents/invalid.json"
    bad.parent.mkdir(parents=True)
    bad.write_text(bad_content)
    project = tmp_path / "project"
    good = project / ".kiro/agents/valid.json"
    original = write_profile(good)
    registry["harnesses"]["kiro"]["agents"] = [{"scope": "project", "directory": str(project), "slug": "valid"}]
    assert doctor._cleanup_kiro(dry_run=False)
    assert bad.read_text() == bad_content
    assert json.loads(good.read_text()) == expected_profile(original, False)


def test_standalone_cleanup_without_any_profiles(cleanup_state, tmp_path):
    """A missing profile must not prevent removing the project standalone hooks."""
    _, registry = cleanup_state
    project = tmp_path / "project"
    hooks = project / ".kiro/hooks/observal.json"
    hooks.parent.mkdir(parents=True)
    hooks.write_text(json.dumps({"version": "v1", "hooks": [UNIVERSAL_OWNED]}))
    registry["harnesses"]["kiro"]["agents"] = [{"scope": "project", "directory": str(project), "name": "missing"}]
    assert doctor._cleanup_kiro(dry_run=False)
    assert not hooks.exists()


def test_lock_alias_cannot_escape_agents_directory(cleanup_state, tmp_path):
    """Invalid lock aliases never expand cleanup beyond the project agents directory."""
    home, registry = cleanup_state
    (home / ".kiro/agents").mkdir(parents=True)
    project = tmp_path / "project"
    outside = project / ".kiro/outside.json"
    write_profile(outside)
    before = outside.read_bytes()
    registry["harnesses"]["kiro"]["agents"] = [
        {"scope": "project", "directory": str(project), "local_name": "../outside"},
        {"scope": "project", "directory": str(project), "local_name": str(outside.with_suffix(""))},
        {"scope": "project", "local_name": "missing-directory"},
        {"scope": "project", "directory": str(project)},
    ]
    assert not doctor._cleanup_kiro(dry_run=False)
    assert outside.read_bytes() == before


@pytest.mark.parametrize("error", [RuntimeError("invalid lockfile"), OSError("unreadable lockfile")])
def test_bad_inventory_still_cleans_user_profiles(cleanup_state, monkeypatch, error):
    """Unreadable inventory must not disable cleanup of known user profiles."""
    home, _ = cleanup_state
    profile = home / ".kiro/agents/user.json"
    original = write_profile(profile)

    def unreadable_inventory():
        """Reproduce the inventory read failure without touching host configuration."""
        raise error

    monkeypatch.setattr(lockfile, "read_registry_lockfile", unreadable_inventory)
    assert doctor._cleanup_kiro(dry_run=False)
    assert json.loads(profile.read_text()) == expected_profile(original, False)


@pytest.mark.parametrize(
    "entry",
    [{"command": "python /home/user/bin/observal_cli_wrapper"}, {"url": "https://example.org/observal_cli_notes"}],
)
def test_cleanup_preserves_generic_package_mentions(cleanup_state, tmp_path, entry):
    """A package-name substring alone does not establish ownership for deletion."""
    _, registry = cleanup_state
    project = tmp_path / "project"
    profile = project / ".kiro/agents/probe.json"
    data = write_profile(profile)
    data["hooks"]["stop"] = [OWNED, entry]
    profile.write_text(json.dumps(data))
    registry["harnesses"]["kiro"]["agents"] = [{"scope": "project", "directory": str(project), "name": "probe"}]
    assert doctor._cleanup_kiro(dry_run=False)
    assert json.loads(profile.read_text())["hooks"]["stop"] == [entry]


@pytest.mark.parametrize("other_scope", [False, True], ids=["failed-only", "continue-other-scopes"])
def test_profile_write_failure_does_not_claim_success_or_block_scopes(
    cleanup_state, tmp_path, monkeypatch, capsys, other_scope
):
    """Only successful writes count as cleaned; a failed profile does not halt cleanup."""
    _, registry = cleanup_state
    project = tmp_path / "a-project"
    bad = project / ".kiro/agents/probe.json"
    write_profile(bad)
    before = bad.read_bytes()
    registry["harnesses"]["kiro"]["agents"] = [{"scope": "project", "directory": str(project), "name": "probe"}]
    if other_scope:
        second = tmp_path / "z-project"
        good = second / ".kiro/agents/good.json"
        original = write_profile(good)
        registry["harnesses"]["kiro"]["agents"].append({"scope": "project", "directory": str(second), "name": "good"})
        hooks = project / ".kiro/hooks/observal.json"
        hooks.parent.mkdir()
        hooks.write_text(json.dumps({"version": "v1", "hooks": [UNIVERSAL_OWNED, UNIVERSAL_FOREIGN]}))
    original_write = doctor.atomic_write

    def write_with_denied_profile(path, content):
        """Emulate Windows read-only replacement or a non-writable POSIX directory."""
        if path == bad:
            raise PermissionError("profile directory is not writable")
        return original_write(path, content)

    monkeypatch.setattr(doctor, "atomic_write", write_with_denied_profile)
    assert doctor._cleanup_kiro(dry_run=False) is other_scope
    assert bad.read_bytes() == before
    output = capsys.readouterr().out
    assert "Cannot write profile" in output
    assert f"Cleaned {bad}" not in output
    if other_scope:
        assert json.loads(good.read_text()) == expected_profile(original, False)
        assert json.loads(hooks.read_text())["hooks"] == [UNIVERSAL_FOREIGN]
    assert doctor._cleanup_kiro(dry_run=True)
    assert bad.read_bytes() == before


def test_explicit_hook_markers_remain_owned_without_generic_match():
    """Narrow Kiro cleanup leaves every explicit current and legacy marker supported."""
    from observal_cli.shared.utils import _OBSERVAL_HOOK_MARKERS, is_observal_hook_entry

    for marker in _OBSERVAL_HOOK_MARKERS:
        if marker != "observal_cli":
            assert is_observal_hook_entry({"command": marker}, include_generic_marker=False)
    assert is_observal_hook_entry({"command": "observal_cli_wrapper"})
    assert not is_observal_hook_entry({"command": "observal_cli_wrapper"}, include_generic_marker=False)
