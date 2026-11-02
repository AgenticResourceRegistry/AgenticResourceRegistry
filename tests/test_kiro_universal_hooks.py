# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regressions for #2015/#2024 using a profile upgraded by Kiro itself."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from observal_cli import cmd_doctor, cmd_pull, lockfile
from observal_cli.discovery.models import DiagnosticCode
from observal_cli.errors import CliError
from observal_cli.harness import DiscoveredHook
from observal_cli.harness.kiro import KiroAdapter
from observal_cli.harness_specs.kiro_hooks_spec import build_kiro_hooks_file, iter_kiro_hooks

FIXTURES = Path(__file__).parent / "fixtures" / "kiro"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture(params=["legacy-agent.json", "universal-agent.json"])
def profile(request: pytest.FixtureRequest) -> dict:
    return json.loads((FIXTURES / request.param).read_text())


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OBSERVAL_KIRO_CLI_VERSION", "2.35.0")
    monkeypatch.setenv("OBSERVAL_KIRO_IDE", "0")
    monkeypatch.setattr(lockfile, "LOCKFILE_PATH", tmp_path / ".observal/lockfile.json")
    monkeypatch.setattr(lockfile, "_LOCKFILE_LOCK", tmp_path / ".observal/lockfile.lock")


def test_reader_preserves_commands_triggers_timeouts_and_input(profile: dict) -> None:
    before = copy.deepcopy(profile)
    hooks = list(iter_kiro_hooks(profile["hooks"]))

    assert [hook.trigger for hook in hooks] == ["stop", "stop", "userPromptSubmit"]
    assert hooks[1].command == "echo user-stop"
    timeout_key = "timeout" if isinstance(profile["hooks"], list) else "timeout_ms"
    assert hooks[1].handler[timeout_key] == (20 if timeout_key == "timeout" else 20000)
    assert profile == before


def test_reader_accepts_pascal_case_standalone_triggers() -> None:
    assert [hook.trigger for hook in iter_kiro_hooks(build_kiro_hooks_file()["hooks"])] == ["UserPromptSubmit", "Stop"]


@pytest.mark.parametrize("hooks", [None, 3, "stop", [None, 1, {"action": "echo"}], {"stop": [None, 1]}])
def test_reader_ignores_malformed_values(hooks: object) -> None:
    assert list(iter_kiro_hooks(hooks)) == []


def test_reader_does_not_coerce_a_non_string_command() -> None:
    assert next(iter_kiro_hooks([{"trigger": "Stop", "action": {"command": ["observal_cli"]}}])).command == ""


def test_scan_and_detection_accept_both_shapes(tmp_path: Path, profile: dict) -> None:
    write_json(tmp_path / ".kiro/agents/fixture.json", profile)
    adapter = KiroAdapter()

    result = adapter.scan_home(tmp_path)

    assert [agent.name for agent in result.agents] == ["fixture"]
    assert len(result.hooks) == 3
    assert result.hooks[1].handler_config["command"] == "echo user-stop"
    assert adapter.detect_hooks(tmp_path / ".kiro") == "installed"


@pytest.mark.parametrize("scope", ["user", "project"])
def test_inventory_accepts_both_shapes_and_standalone_files(tmp_path: Path, profile: dict, scope: str) -> None:
    write_json(tmp_path / ".kiro/agents/fixture.json", profile)
    write_json(tmp_path / ".kiro/hooks/observal.json", build_kiro_hooks_file())
    adapter = KiroAdapter()

    result = adapter.discover_home(tmp_path) if scope == "user" else adapter.discover_project(tmp_path)
    hooks = [item.component for item in result.evidence if isinstance(item.component, DiscoveredHook)]

    assert len(hooks) == 5
    assert {hook.event for hook in hooks} == {"stop", "userPromptSubmit", "UserPromptSubmit", "Stop"}
    assert not any(d.code is DiagnosticCode.METADATA_MALFORMED for d in result.diagnostics)
    user_hook = next(hook for hook in hooks if hook.handler_config.get("command") == "echo user-stop")
    key = "timeout" if isinstance(profile["hooks"], list) else "timeout_ms"
    assert user_hook.handler_config[key] == (20 if key == "timeout" else 20000)


def test_inventory_redacts_universal_hook_secrets(tmp_path: Path) -> None:
    write_json(
        tmp_path / ".kiro/agents/fixture.json",
        {
            "hooks": [
                {"name": "test", "trigger": "Stop", "action": {"command": "echo hi", "env": {"API_KEY": "SECRET"}}}
            ]
        },
    )
    result = KiroAdapter().discover_home(tmp_path)
    hook = next(item.component for item in result.evidence if isinstance(item.component, DiscoveredHook))
    assert "SECRET" not in json.dumps(hook.handler_config)


@pytest.mark.parametrize("bad", [[], None, 3, "profile"])
def test_scan_and_detect_skip_non_object_profiles(tmp_path: Path, profile: dict, bad: object) -> None:
    write_json(tmp_path / ".kiro/agents/a-bad.json", bad)
    write_json(tmp_path / ".kiro/agents/fixture.json", profile)
    adapter = KiroAdapter()

    assert [agent.name for agent in adapter.scan_home(tmp_path).agents] == ["fixture"]
    assert adapter.detect_hooks(tmp_path / ".kiro") == "partial"


def test_doctor_reads_both_shapes(tmp_path: Path, profile: dict) -> None:
    write_json(tmp_path / ".kiro/agents/fixture.json", profile)
    issues: list[str] = []
    warnings: list[str] = []

    cmd_doctor._check_kiro(issues, warnings)

    assert issues == warnings == []


@pytest.mark.parametrize("hooks", [None, "hooks", 3, [None, {"action": "echo"}], {"stop": [None, 3]}])
def test_doctor_warns_instead_of_crashing_on_malformed_hooks(tmp_path: Path, hooks: object) -> None:
    write_json(tmp_path / ".kiro/agents/fixture.json", {"hooks": hooks})
    warnings: list[str] = []
    cmd_doctor._check_kiro([], warnings)
    assert any("session hooks not installed" in warning for warning in warnings)


@pytest.mark.parametrize("inline", [True, False])
def test_rewrite_preserves_profile_shape_user_hooks_and_upgrade_state(
    profile: dict, inline: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("observal_cli.harness.kiro.use_inline_hooks", lambda: inline)
    before = copy.deepcopy(profile)

    rewritten = cmd_pull._rewrite_kiro_agent_profile(profile, agent_id="agent-123")

    assert type(rewritten["hooks"]) is type(before["hooks"])
    if isinstance(before["hooks"], list):
        assert rewritten["hooks"][0] == before["hooks"][1]
        assert rewritten["kiroUpgrade"] == before["kiroUpgrade"]
    else:
        assert rewritten["hooks"]["stop"][0] == before["hooks"]["stop"][1]
    managed = [hook for hook in iter_kiro_hooks(rewritten["hooks"]) if "observal_cli" in hook.command]
    assert len(managed) == (2 if inline else 0)
    assert all("agent-123" in hook.command for hook in managed)
    assert cmd_pull._rewrite_kiro_agent_profile(copy.deepcopy(rewritten), agent_id="agent-123") == rewritten


@pytest.mark.parametrize("inline", [True, False])
def test_rewrite_keeps_unknown_array_entries(inline: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("observal_cli.harness.kiro.use_inline_hooks", lambda: inline)
    unknown = [None, "unknown", {"name": [], "action": "unknown"}]
    profile = {"hooks": copy.deepcopy(unknown)}
    result = cmd_pull._rewrite_kiro_agent_profile(profile, agent_id="agent-123")
    assert result["hooks"][:3] == unknown


@pytest.mark.parametrize("hooks", [None, 3, "hooks"])
def test_rewrite_rejects_malformed_collection_without_mutating_it(hooks: object) -> None:
    profile = {"hooks": hooks, "allowedTools": []}
    before = copy.deepcopy(profile)
    with pytest.raises(ValueError, match="object or array"):
        cmd_pull._rewrite_kiro_agent_profile(profile)
    assert profile == before


@pytest.mark.parametrize("dry_run", [True, False])
def test_cleanup_preserves_user_hooks_and_skips_bad_profiles(tmp_path: Path, profile: dict, dry_run: bool) -> None:
    path = tmp_path / ".kiro/agents/fixture.json"
    bad = path.parent / "a-bad.json"
    write_json(path, profile)
    write_json(bad, [])
    before = path.read_bytes()

    assert cmd_doctor._cleanup_kiro(dry_run=dry_run) is True

    assert bad.read_text() == "[]"
    if dry_run:
        assert path.read_bytes() == before
    else:
        result = json.loads(path.read_text())
        assert type(result["hooks"]) is type(profile["hooks"])
        hooks = list(iter_kiro_hooks(result["hooks"]))
        assert [hook.command for hook in hooks] == ["echo user-stop"]
        assert {key: value for key, value in result.items() if key != "hooks"} == {
            key: value for key, value in profile.items() if key != "hooks"
        }
        assert cmd_doctor._cleanup_kiro(dry_run=False) is False


@pytest.mark.parametrize("dry_run", [True, False])
def test_patch_preserves_shape_and_continues_after_bad_profile(tmp_path: Path, profile: dict, dry_run: bool) -> None:
    path = tmp_path / ".kiro/agents/fixture.json"
    bad = path.parent / "a-bad.json"
    write_json(path, profile)
    write_json(bad, [])
    before = path.read_bytes()
    for name in ("a-bad", "fixture"):
        lockfile.upsert_agent("kiro", name=name, agent_id=f"id-{name}", version="1.0.0", scope="user", local_name=name)

    assert cmd_doctor._patch_kiro(dry_run=dry_run) is True

    assert bad.read_text() == "[]"
    if dry_run:
        assert path.read_bytes() == before
        assert not (tmp_path / ".kiro/hooks/observal.json").exists()
    else:
        result = json.loads(path.read_text())
        assert type(result["hooks"]) is type(profile["hooks"])
        assert next(iter_kiro_hooks(result["hooks"])).command == "echo user-stop"
        assert (tmp_path / ".kiro/hooks/observal.json").exists()
        assert cmd_doctor._patch_kiro(dry_run=False) is False


def test_multi_harness_patch_processes_remaining_targets_then_reports_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A genuinely invalid other harness must not prevent Kiro from being patched.
    write_json(tmp_path / ".claude/settings.json", {"hooks": []})
    (tmp_path / ".kiro").mkdir()
    monkeypatch.setattr("observal_cli.audit.emit_cli_audit", lambda *args, **kwargs: None)

    with pytest.raises(CliError) as captured:
        cmd_doctor._patch_targets(["claude-code", "kiro"], dry_run=False, output="json")

    assert (tmp_path / ".kiro/hooks/observal.json").exists()
    result = captured.value.result
    assert result["changed"] is True
    assert "error" in result["targets"][0]
    assert result["targets"][1] == {"harness": "kiro", "changed": True}


@pytest.mark.parametrize("existing", ["{broken", "[]", '{"hooks": null}', '{"hooks": "invalid"}'])
def test_merge_refuses_an_invalid_existing_profile_without_overwriting_it(tmp_path: Path, existing: str) -> None:
    path = tmp_path / "profile.json"
    path.write_text(existing)
    incoming = {"name": "updated", "prompt": "new prompt"}

    with pytest.raises(CliError) as error:
        KiroAdapter().merge_agent_profile(incoming, path)

    assert error.value.category.value == "validation"
    assert path.read_text() == existing
    assert incoming == {"name": "updated", "prompt": "new prompt"}


def test_default_adapter_merge_does_not_change_other_harness_profiles(tmp_path: Path) -> None:
    from observal_cli.harness.base import BaseAdapter

    path = tmp_path / "profile.json"
    path.write_text("{broken")
    incoming = {"name": "updated"}

    assert BaseAdapter().merge_agent_profile(incoming, path) is incoming
    assert path.read_text() == "{broken"
