# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Agent hooks placed in settings.json behind the agent gate: ownership-scoped reconciliation."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from observal_cli import agent_hooks
from observal_cli.shared.utils import is_observal_matcher_group

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the gate is POSIX-only")

HOOK_A = "11111111-1111-4111-8111-111111111111"
HOOK_B = "22222222-2222-4222-8222-222222222222"
SESSION_PUSH = {
    "_observal": {"version": "13"},
    "hooks": [{"type": "command", "command": "python3 -I -m observal_cli.hooks.session_push --harness claude-code"}],
}
USER_GROUP = {"matcher": "Bash", "hooks": [{"type": "command", "command": "make lint", "timeout": 30}]}


@pytest.fixture(autouse=True)
def installed_cli(monkeypatch):
    from observal_cli.shared import launcher

    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: True)


def _components() -> list[dict]:
    return [
        {"type": "hook", "id": HOOK_A, "local_name": "probe-fail"},
        {"type": "hook", "id": HOOK_B, "local_name": "lint"},
        {"type": "mcp", "id": "33333333-3333-4333-8333-333333333333", "local_name": "probe"},
    ]


def _bindings(agent: str = "reviewer") -> list[dict]:
    return [
        {"name": "probe-fail", "event": "PostToolUse", "command": ".claude/hooks/probe-fail.sh", "agent": agent},
        {"name": "lint", "event": "Stop", "command": "make lint && echo 'done'", "agent": agent},
    ]


def _hooks(agent: str = "reviewer", bindings: list[dict] | None = None) -> list[agent_hooks.AgentHook]:
    return agent_hooks.build_agent_hooks(agent, bindings or _bindings(agent), _components(), "skip")


def _plan(settings: dict | None, agent: str = "reviewer", hooks=None, **options) -> agent_hooks.HookPlan:
    return agent_hooks.plan_settings(
        Path("/x/settings.json"),
        "project:.claude/settings.json",
        settings,
        agent,
        _hooks(agent) if hooks is None else hooks,
        **options,
    )


def test_each_hook_becomes_one_owned_gated_group_and_user_groups_stay():
    settings = {"model": "opus", "hooks": {"Stop": [USER_GROUP, SESSION_PUSH]}}
    plan = _plan(settings)
    assert plan.changed and not plan.conflicts and not plan.removed
    assert sorted((item["event"], item["name"]) for item in plan.added) == [
        ("PostToolUse", "probe-fail"),
        ("Stop", "lint"),
    ]
    stop = plan.data["hooks"]["Stop"]
    assert stop[:2] == [USER_GROUP, SESSION_PUSH], "foreign and telemetry groups are untouched, in order"
    group = stop[2]
    meta = group["_observal"]
    assert meta["kind"] == "agent-hook" and meta["agent"] == "reviewer" and meta["component_id"] == HOOK_B
    assert meta["digest"] == agent_hooks.group_digest("Stop", group)
    (hook,) = group["hooks"]
    assert hook["type"] == "command" and "-m observal_cli.hook_gate --agent reviewer --command" in hook["command"]
    assert "matcher" not in group and "timeout" not in hook, "the agent file's hooks carry neither; none is added"
    assert settings == {"model": "opus", "hooks": {"Stop": [USER_GROUP, SESSION_PUSH]}}, "the input is not mutated"


def test_reconcile_is_idempotent():
    first = _plan({"hooks": {"Stop": [USER_GROUP]}})
    second = _plan(first.data)
    assert not second.changed and not second.added and not second.removed
    assert sorted(item["name"] for item in second.kept) == ["lint", "probe-fail"]


def test_no_hooks_and_no_file_writes_nothing():
    plan = _plan(None, hooks=[])
    assert plan.data is None and not plan.changed


def test_restore_removes_only_this_agents_clean_groups():
    placed = _plan({"hooks": {"Stop": [USER_GROUP]}}).data
    other = _plan(copy.deepcopy(placed), agent="other", hooks=_hooks("other")).data
    restored = _plan(copy.deepcopy(other), hooks=[])
    assert sorted(item["component_id"] for item in restored.removed) == [HOOK_A, HOOK_B]
    remaining = [
        group.get("_observal", {}).get("agent")
        for groups in restored.data["hooks"].values()
        for group in groups
        if group.get("_observal")
    ]
    assert remaining == ["other", "other"], "another agent's groups are never touched"
    assert USER_GROUP in restored.data["hooks"]["Stop"]
    emptied = _plan(placed, hooks=[])
    assert emptied.data["hooks"] == {"Stop": [USER_GROUP]}, "emptied events are dropped"


def test_an_edited_group_is_kept_and_reported_unless_forced():
    placed = _plan({}).data
    edited = copy.deepcopy(placed)
    edited["hooks"]["Stop"][0]["hooks"][0]["timeout"] = 5  # any change to the group, not only the command
    plan = _plan(edited)
    assert plan.conflicts == [{"event": "Stop", "component_id": HOOK_B, "agent": "reviewer"}]
    assert plan.data["hooks"]["Stop"] == edited["hooks"]["Stop"], "kept as edited, and no duplicate is added"
    restore = _plan(edited, hooks=[])
    assert restore.conflicts and edited["hooks"]["Stop"][0] in restore.data["hooks"]["Stop"]
    forced = _plan(edited, force=True)
    assert not forced.conflicts and forced.removed == [
        {"event": "Stop", "component_id": HOOK_B, "agent": "reviewer", "edited": True}
    ]
    assert forced.data == placed


def test_removing_the_observal_key_hands_the_group_to_the_user():
    placed = _plan({}).data
    handed = copy.deepcopy(placed)
    del handed["hooks"]["Stop"][0]["_observal"]
    restored = _plan(handed, hooks=[])
    assert handed["hooks"]["Stop"][0] in restored.data["hooks"]["Stop"]
    assert not restored.conflicts


def test_a_renamed_agent_takes_over_its_earlier_groups():
    placed = _plan({}).data
    renamed = _plan(placed, agent="reviewer-2", hooks=_hooks("reviewer-2"), owners=("reviewer",))
    agents = {group["_observal"]["agent"] for groups in renamed.data["hooks"].values() for group in groups}
    assert agents == {"reviewer-2"} and len(renamed.removed) == 2


def test_hooks_must_map_to_exactly_one_pinned_component():
    with pytest.raises(ValueError, match="exactly one"):
        agent_hooks.build_agent_hooks("reviewer", _bindings(), _components()[:1], "skip")
    twice = [*_components(), {"type": "hook", "id": "x", "local_name": "lint"}]
    with pytest.raises(ValueError, match="exactly one"):
        agent_hooks.build_agent_hooks("reviewer", _bindings(), twice, "skip")
    with pytest.raises(ValueError, match="malformed"):
        agent_hooks.build_agent_hooks("reviewer", [{"name": "lint", "event": "Bad Event", "command": "x"}], [], "skip")
    with pytest.raises(ValueError, match="agent name"):
        agent_hooks.build_agent_hooks("bad name", _bindings(), _components(), "skip")
    with pytest.raises(ValueError, match="on-unknown"):
        agent_hooks.build_agent_hooks("reviewer", _bindings(), _components(), "maybe")


def test_on_unknown_run_is_carried_into_the_gated_command():
    (hook,) = agent_hooks.build_agent_hooks("reviewer", _bindings()[:1], _components(), "run")
    assert hook.gated.endswith("--on-unknown run")
    (default,) = agent_hooks.build_agent_hooks("reviewer", _bindings()[:1], _components(), "skip")
    assert "--on-unknown" not in default.gated


@pytest.mark.parametrize(
    "text",
    ["{not json", "[1, 2]", json.dumps({"hooks": []}), json.dumps({"hooks": {"Stop": {"x": 1}}})],
)
def test_settings_that_cannot_be_edited_safely_are_refused(tmp_path, text):
    path = tmp_path / "settings.json"
    path.write_text(text)
    with pytest.raises(agent_hooks.SettingsError):
        agent_hooks.read_settings(path)
    assert agent_hooks.read_settings(tmp_path / "absent.json") is None


def test_write_is_atomic_and_keeps_the_file_mode(tmp_path):
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir()
    path.write_text("{}")
    path.chmod(0o600)
    agent_hooks.write_settings(path, {"hooks": {}})
    assert json.loads(path.read_text()) == {"hooks": {}}
    assert path.stat().st_mode & 0o777 == 0o600
    assert [p.name for p in path.parent.iterdir()] == ["settings.json"], "no temporary file left behind"


def test_version_range():
    assert agent_hooks.parse_version("2.1.286 (Claude Code)") == (2, 1, 286)
    assert agent_hooks.is_tested_version("2.1.286")
    assert not agent_hooks.is_tested_version("2.1.287")
    assert not agent_hooks.is_tested_version(None)
    assert agent_hooks.tested_range() == "2.1.286"


def test_settings_location_follows_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    assert agent_hooks.settings_location("project", tmp_path) == (
        tmp_path / ".claude" / "settings.json",
        "project:.claude/settings.json",
    )
    assert agent_hooks.settings_location("user", tmp_path) == (
        tmp_path / "home" / ".claude" / "settings.json",
        "user:settings.json",
    )


def test_telemetry_patching_and_cleanup_leave_agent_hook_groups_alone():
    from observal_cli.settings_reconciler import reconcile_hooks

    group = _hooks()[1].group("reviewer")
    assert not is_observal_matcher_group(group), "an agent's gated hook is not telemetry"
    assert is_observal_matcher_group(SESSION_PUSH)
    merged, _ = reconcile_hooks({"Stop": [group]}, {"Stop": [SESSION_PUSH]})
    assert merged["Stop"] == [group, SESSION_PUSH]


def test_the_gated_command_is_not_mistaken_for_observals_own_telemetry(monkeypatch):
    """Server-side, a recorded run of a gated hook stays a candidate for attribution."""
    from observal_cli.shared import launcher
    from services.session_parsers.hook_evidence import is_observal_telemetry_hook

    for installed in (True, False):  # -I, and the PYTHONPATH=<root> ... -P source-checkout form
        monkeypatch.setattr(launcher, "importable_in_isolation", lambda installed=installed: installed)
        for binding in _bindings():
            (hook,) = agent_hooks.build_agent_hooks("reviewer", [binding], _components(), "skip")
            assert not is_observal_telemetry_hook(hook.gated), hook.gated


def test_expected_agent_name_follows_the_server_rule_and_existing_placement_reads_policy(tmp_path):
    assert agent_hooks.expected_agent_name("reviewer_2-x") == "reviewer_2-x"
    assert agent_hooks.expected_agent_name("acme.reviewer v2") == "acme-reviewer-v2"
    path = tmp_path / "settings.json"
    assert agent_hooks.existing_placement(path, "reviewer") is None
    path.write_text("{bad")
    assert agent_hooks.existing_placement(path, "reviewer") is None
    skip = _hooks()[0].group("reviewer")
    (run_hook,) = agent_hooks.build_agent_hooks("reviewer", _bindings()[:1], _components(), "run")
    path.write_text(json.dumps({"hooks": {"Stop": [USER_GROUP, skip]}}))
    assert agent_hooks.existing_placement(path, "reviewer") == "skip"
    assert agent_hooks.existing_placement(path, "other") is None
    path.write_text(json.dumps({"hooks": {"Stop": [run_hook.group("reviewer")]}}))
    assert agent_hooks.existing_placement(path, "reviewer") == "run"
    path.write_text(json.dumps({"hooks": {"Stop": [run_hook.group("reviewer"), skip]}}))
    assert agent_hooks.existing_placement(path, "reviewer") == "skip", "mixed policies fall back to the default"
