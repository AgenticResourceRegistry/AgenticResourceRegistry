# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Kiro hook-format selection.

Kiro IDE 1.0 / CLI 3.0 read standalone v1 hook files (``.kiro/hooks/*.json``).
Kiro CLI 2.x reads inline ``hooks`` inside the agent profile — the IDE loads an
agent carrying that field but never fires the hooks, leaving it silent. These
tests pin which format is produced for which machine, and which fields keep a
profile loadable by the IDE at all.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from observal_cli.harness.kiro import (
    KiroAdapter,
    kiro_cli_major,
    kiro_ide_installed,
    strip_ide_hostile_fields,
    use_inline_hooks,
)
from observal_cli.harness_specs.kiro_hooks_spec import (
    KIRO_V1_HOOK_FILENAME,
    build_kiro_hooks_file,
    is_observal_v1_hook,
    merge_kiro_hooks_file,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestV1HooksFile:
    def test_uses_pascal_case_triggers_and_v1_schema(self) -> None:
        data = build_kiro_hooks_file()

        assert data["version"] == "v1"
        assert [h["trigger"] for h in data["hooks"]] == ["UserPromptSubmit", "Stop"]
        for hook in data["hooks"]:
            assert hook["action"]["type"] == "command"

    def test_never_carries_per_agent_attribution(self) -> None:
        """One file per scope serves every agent, so an agent id cannot belong here.

        Baking one in made each locked agent rewrite the previous agent's copy,
        so ``observal doctor patch`` reported a write on every single run.
        """
        for hook in build_kiro_hooks_file()["hooks"]:
            assert "OBSERVAL_AGENT_ID" not in hook["action"]["command"]

    def test_merge_preserves_user_hooks_and_refreshes_observal(self) -> None:
        existing = {
            "version": "v1",
            "hooks": [
                {"name": "lint", "trigger": "PostFileSave", "action": {"type": "command", "command": "eslint"}},
                {
                    "name": "observal-session-push-stop",
                    "trigger": "Stop",
                    "action": {"type": "command", "command": "stale"},
                },
            ],
        }

        merged = merge_kiro_hooks_file(existing)

        names = [h["name"] for h in merged["hooks"]]
        assert names.count("observal-session-push-stop") == 1
        assert "lint" in names
        assert all("stale" not in h["action"]["command"] for h in merged["hooks"])

    def test_merge_from_scratch(self) -> None:
        assert merge_kiro_hooks_file(None) == build_kiro_hooks_file()

    def test_merge_converges_across_agents(self) -> None:
        """Refreshing for one agent then another must reach a fixed point.

        Regression: two user-scope locked agents each rewrote the shared file
        with their own id, so the content flip-flopped forever.
        """
        first = merge_kiro_hooks_file(None)
        second = merge_kiro_hooks_file(first)
        third = merge_kiro_hooks_file(second)

        assert first == second == third

    @pytest.mark.parametrize(
        "entry,expected",
        [
            ({"name": "observal-session-push-stop"}, True),
            ({"name": "lint", "action": {"command": "python -m observal_cli.hooks.session_push"}}, True),
            ({"name": "lint", "action": {"command": "eslint"}}, False),
            ("not-a-dict", False),
        ],
    )
    def test_observal_ownership_detection(self, entry, expected) -> None:
        assert is_observal_v1_hook(entry) is expected


class TestSurfaceDetection:
    def test_legacy_cli_keeps_inline_hooks_even_with_the_ide_installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """IDE and CLI are separate surfaces that coexist; both need their format.

        Regression: withholding inline hooks whenever the IDE was present left
        Kiro CLI 2.x with no readable hook format at all, and CLI sessions
        silently stopped reporting. The IDE loads an inline-hooked agent fine.
        """
        monkeypatch.setenv("OBSERVAL_KIRO_IDE", "1")
        monkeypatch.setenv("OBSERVAL_KIRO_CLI_VERSION", "2.24.0")

        assert use_inline_hooks() is True

    def test_cli_3x_with_ide_uses_standalone_file_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OBSERVAL_KIRO_IDE", "1")
        monkeypatch.setenv("OBSERVAL_KIRO_CLI_VERSION", "3.1.0")

        assert use_inline_hooks() is False

    def test_legacy_cli_without_ide_keeps_inline_hooks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OBSERVAL_KIRO_IDE", "0")
        monkeypatch.setenv("OBSERVAL_KIRO_CLI_VERSION", "2.24.0")

        assert use_inline_hooks() is True

    def test_cli_3x_uses_standalone_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OBSERVAL_KIRO_IDE", "0")
        monkeypatch.setenv("OBSERVAL_KIRO_CLI_VERSION", "3.1.0")

        assert use_inline_hooks() is False

    def test_no_kiro_cli_detected_uses_standalone_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OBSERVAL_KIRO_IDE", "0")
        monkeypatch.setattr("observal_cli.harness.kiro.shutil.which", lambda _name: None)

        assert use_inline_hooks() is False

    def test_version_parsed_from_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OBSERVAL_KIRO_CLI_VERSION", "kiro-cli 2.24.0")

        assert kiro_cli_major() == 2

    def test_ide_detected_from_install_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("OBSERVAL_KIRO_IDE", raising=False)
        (tmp_path / "Applications" / "Kiro.app").mkdir(parents=True)

        assert kiro_ide_installed(home=tmp_path) is True

    def test_ide_absent(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("OBSERVAL_KIRO_IDE", raising=False)
        monkeypatch.setattr("observal_cli.harness.kiro.Path.exists", lambda _self: False)

        assert kiro_ide_installed(home=tmp_path) is False


class TestDetectHooks:
    def test_standalone_file_counts_as_installed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # A CLI 2.x-only machine cannot read this file; explicitly model IDE 1.x.
        monkeypatch.setenv("OBSERVAL_KIRO_IDE", "1")
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / KIRO_V1_HOOK_FILENAME).write_text(json.dumps(build_kiro_hooks_file()))

        assert KiroAdapter().detect_hooks(tmp_path) == "installed"

    def test_inline_hooks_still_recognised(self, tmp_path: Path) -> None:
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "a.json").write_text(
            json.dumps({"hooks": {"stop": [{"command": "python -m observal_cli.hooks.session_push"}]}})
        )

        assert KiroAdapter().detect_hooks(tmp_path) == "installed"

    def test_missing_when_neither_present(self, tmp_path: Path) -> None:
        (tmp_path / "agents").mkdir()
        (tmp_path / "agents" / "a.json").write_text(json.dumps({"name": "a"}))

        assert KiroAdapter().detect_hooks(tmp_path) == "missing"


class TestStripIdeHostileFields:
    """Kiro IDE 1.x ProfileLoader rejects "CLI-only" profiles.

    A profile carrying ``allowedTools`` or ``toolsSettings`` without a
    ``permissions`` block is dropped outright (reasonCode ``cli_only_agent``)
    and never reaches the IDE agent picker. This - not the ``hooks`` field - is
    what made every Observal-pulled agent invisible in the IDE.
    """

    def test_removes_empty_cli_only_fields(self) -> None:
        content = {"name": "a", "allowedTools": [], "toolsSettings": {}}

        removed = strip_ide_hostile_fields(content)

        assert removed == ["allowedTools", "toolsSettings"]
        assert content == {"name": "a"}

    def test_keeps_non_empty_user_configuration(self) -> None:
        """A populated value is real user config, not an Observal placeholder."""
        content = {"name": "a", "allowedTools": ["fsRead"], "toolsSettings": {}}

        removed = strip_ide_hostile_fields(content)

        assert removed == ["toolsSettings"]
        assert content["allowedTools"] == ["fsRead"]

    def test_leaves_profile_alone_when_permissions_present(self) -> None:
        """With "permissions" the profile is a valid V3 agent; nothing to repair."""
        content = {"name": "a", "allowedTools": [], "permissions": {"fsWrite": "ask"}}

        assert strip_ide_hostile_fields(content) == []
        assert content["allowedTools"] == []

    def test_no_op_on_a_clean_profile(self) -> None:
        content = {"name": "a", "tools": ["*"]}

        assert strip_ide_hostile_fields(content) == []
        assert content == {"name": "a", "tools": ["*"]}


# ── Review follow-ups ─────────────────────────────────────────────


def test_cleanup_removes_project_scope_hook_files(tmp_path, monkeypatch):
    """Patch writes one file per scope, so cleanup has to cover them all.

    Cleaning only the user scope left project hooks live, and Kiro kept running
    Observal commands after an uninstall.
    """
    import json as _json

    from observal_cli import cmd_doctor

    project = tmp_path / "proj"
    for base in (tmp_path / ".kiro", project / ".kiro"):
        (base / "hooks").mkdir(parents=True)
        (base / "hooks" / "observal.json").write_text(
            _json.dumps(
                {
                    "version": "v1",
                    "hooks": [
                        {"name": "observal-session-push-stop", "action": {"command": "observal_cli.hooks"}},
                        {"name": "mine", "action": {"command": "echo hi"}},
                    ],
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.setattr(cmd_doctor.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(
        "observal_cli.lockfile.read_registry_lockfile",
        lambda: (None, {"harnesses": {"kiro": {"agents": [{"directory": str(project)}]}}}),
    )

    assert cmd_doctor._cleanup_kiro_hook_files(dry_run=False) is True

    for base in (tmp_path / ".kiro", project / ".kiro"):
        remaining = _json.loads((base / "hooks" / "observal.json").read_text())["hooks"]
        assert [h["name"] for h in remaining] == ["mine"]


def test_detect_hooks_does_not_report_installed_for_an_unreadable_file(tmp_path, monkeypatch):
    """CLI 2.x with no IDE cannot read the standalone file.

    Reporting "installed" on its presence alone showed a healthy status for a
    surface that was in fact silent.
    """
    import json as _json

    from observal_cli.harness import ensure_loaded, get_adapter

    config_dir = tmp_path / ".kiro"
    (config_dir / "hooks").mkdir(parents=True)
    (config_dir / "hooks" / "observal.json").write_text(
        _json.dumps(
            {
                "version": "v1",
                "hooks": [
                    {
                        "name": "observal-session-push-stop",
                        "trigger": "Stop",
                        "action": {"type": "command", "command": "python -m observal_cli.hooks.session_push"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (config_dir / "agents").mkdir()
    ensure_loaded()
    monkeypatch.setattr("observal_cli.harness.kiro.use_inline_hooks", lambda home=None: True)
    monkeypatch.setattr("observal_cli.harness.kiro.kiro_ide_installed", lambda home=None: False)

    assert get_adapter("kiro").detect_hooks(config_dir) == "missing"

    monkeypatch.setattr("observal_cli.harness.kiro.kiro_ide_installed", lambda home=None: True)

    assert get_adapter("kiro").detect_hooks(config_dir) == "installed"


def test_merge_ignores_a_non_list_hooks_value():
    """Server content on pull, arbitrary JSON on disk in doctor."""
    from observal_cli.harness_specs.kiro_hooks_spec import merge_kiro_hooks_file

    merged = merge_kiro_hooks_file({"version": "v1", "hooks": {"userPromptSubmit": []}})

    assert all(isinstance(h, dict) for h in merged["hooks"])
    assert merged["hooks"] == merge_kiro_hooks_file({})["hooks"]
