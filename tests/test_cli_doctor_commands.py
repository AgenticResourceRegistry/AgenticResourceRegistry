# SPDX-FileCopyrightText: 2026 0xSHSH <156781261+0xSHSH@users.noreply.github.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 EuanTop <euan@mail.bnu.edu.cn>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Behavioral coverage for the doctor CLI commands and untested state branches."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import httpx
import pytest
from typer.testing import CliRunner

import observal_cli.cmd_doctor as doctor_module
from observal_cli import pi_extension
from observal_cli.harness.protocol import NotSupportedError

# Fixed regardless of what observal-cli version this test environment actually
# resolves (which varies across invocation contexts) - keeps Pi stale/current/newer
# comparisons deterministic.
CLI_VERSION = "2.0.0"

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_DIAGNOSTIC_CHECKS = (
    "_check_observal_config",
    "_check_claude_code",
    "_check_kiro",
    "_check_pi",
    "_check_cursor",
    "_check_codex",
    "_check_copilot",
    "_check_copilot_cli",
    "_check_opencode",
    "_check_antigravity",
    "_check_goose",
)


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Keep every doctor test away from the real home, network, and processes."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for name in ("APPDATA", "APPDATA_WIN", "GOOSE_PATH_ROOT", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)

    network = MagicMock(side_effect=AssertionError("unexpected network request"))
    process = MagicMock(side_effect=AssertionError("unexpected subprocess"))
    prompt = MagicMock(side_effect=AssertionError("unexpected prompt"))
    monkeypatch.setattr(httpx, "get", network)
    monkeypatch.setattr(subprocess, "run", process)
    monkeypatch.setattr(doctor_module.typer, "confirm", prompt)
    monkeypatch.setattr(pi_extension, "get_current_version", lambda: CLI_VERSION)
    return SimpleNamespace(network=network, process=process, prompt=prompt)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def quiet_diagnosis(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    from observal_cli import lockfile_reconcile

    checks: dict[str, MagicMock] = {}
    for name in _DIAGNOSTIC_CHECKS:
        check = MagicMock(name=name)
        monkeypatch.setattr(doctor_module, name, check)
        checks[name] = check

    skill_check = MagicMock(return_value=[])
    monkeypatch.setattr(doctor_module, "_check_observal_skill_missing", skill_check)
    plan = SimpleNamespace(changes=[], warnings=[], apply=MagicMock())
    planner = MagicMock(return_value=plan)
    monkeypatch.setattr(lockfile_reconcile, "plan_lockfile_reconciliation", planner)
    return SimpleNamespace(checks=checks, skill_check=skill_check, plan=plan, planner=planner)


class TestDoctorDiagnosis:
    def test_all_clear_runs_every_check_and_reports_success(self, runner: CliRunner, quiet_diagnosis: SimpleNamespace):
        result = runner.invoke(doctor_module.doctor_app, [])

        assert result.exit_code == 0
        assert "Observal Doctor" in _plain(result.output)
        assert "Checking Goose" in _plain(result.output)
        assert "All clear! No issues found." in _plain(result.output)
        for check in quiet_diagnosis.checks.values():
            check.assert_called_once()
        quiet_diagnosis.skill_check.assert_called_once_with()

    def test_issues_and_warnings_have_deterministic_counts_and_guidance(
        self,
        runner: CliRunner,
        quiet_diagnosis: SimpleNamespace,
        isolated_runtime: SimpleNamespace,
    ):
        def config_issue(issues: list[str], _warnings: list[str]) -> None:
            issues.append("configuration is broken")

        def cursor_warning(_issues: list[str], warnings: list[str]) -> None:
            warnings.append("cursor hooks are stale")

        quiet_diagnosis.checks["_check_observal_config"].side_effect = config_issue
        quiet_diagnosis.checks["_check_cursor"].side_effect = cursor_warning
        quiet_diagnosis.skill_check.return_value = ["Pi"]

        result = runner.invoke(doctor_module.doctor_app, [])
        output = _plain(result.output)

        assert result.exit_code == 1
        assert "1 issue(s):" in output
        assert "configuration is broken" in output
        assert "2 warning(s):" in output
        assert "cursor hooks are stale" in output
        assert "Observal AI skill not installed for: Pi" in output
        assert "observal doctor patch --all-harnesses" in output
        isolated_runtime.process.assert_not_called()
        isolated_runtime.prompt.assert_not_called()

    def test_interactive_decline_uses_prompt_and_does_not_patch(
        self,
        quiet_diagnosis: SimpleNamespace,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        def warning(_issues: list[str], warnings: list[str]) -> None:
            warnings.append("hooks missing")

        quiet_diagnosis.checks["_check_cursor"].side_effect = warning
        confirm = MagicMock(return_value=False)
        monkeypatch.setattr(doctor_module.typer, "confirm", confirm)
        monkeypatch.setattr(doctor_module.sys, "stdin", SimpleNamespace(isatty=lambda: True))

        doctor_module.doctor(SimpleNamespace(invoked_subcommand=None), yes=False, output="table")

        confirm.assert_called_once_with(
            "Fix all warnings? (configures telemetry and installs AI skills for detected harnesses)",
            default=True,
        )
        isolated_runtime.process.assert_not_called()
        assert "observal doctor patch --all-harnesses" in _plain(capsys.readouterr().out)

    def test_yes_reconciles_lockfile_runs_patch_and_installs_skill(
        self,
        runner: CliRunner,
        quiet_diagnosis: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from observal_cli import skill_installer

        changes = [
            SimpleNamespace(label=f"component-{index}", field="version", old="1", new="2") for index in range(12)
        ]
        plan = SimpleNamespace(changes=changes, warnings=[], apply=MagicMock())
        quiet_diagnosis.planner.return_value = plan
        patch_targets = MagicMock(return_value={"action": "patch", "changed": True})
        install_skill = MagicMock()
        monkeypatch.setattr(doctor_module, "_patch_targets", patch_targets)
        monkeypatch.setattr(skill_installer, "install_observal_skill", install_skill)

        result = runner.invoke(doctor_module.doctor_app, ["--yes"])
        output = _plain(result.output)

        assert result.exit_code == 0
        assert "Registry metadata drift found in 12 lockfile field(s)." in output
        assert "component-0: version '1' → '2'" in output
        assert "...and 2 more change(s)" in output
        assert "Reconciled 12 lockfile field(s)" in output
        plan.apply.assert_called_once_with()
        patch_targets.assert_called_once_with(list(doctor_module._VALID_HARNESSES), dry_run=False, output="table")
        install_skill.assert_called_once_with()

    def test_stale_reconciliation_does_not_claim_success(
        self, runner: CliRunner, quiet_diagnosis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ):
        from observal_cli import skill_installer

        plan = SimpleNamespace(
            changes=[SimpleNamespace(label="agent", field="name", old="old", new="new")],
            warnings=[],
            apply=MagicMock(return_value=False),
        )
        plan.apply.side_effect = lambda: (plan.warnings.append("Installed state changed; rerun doctor."), False)[1]
        quiet_diagnosis.planner.return_value = plan
        monkeypatch.setattr(doctor_module, "_patch_targets", MagicMock(return_value={}))
        monkeypatch.setattr(skill_installer, "install_observal_skill", MagicMock())

        result = runner.invoke(doctor_module.doctor_app, ["--yes"])
        assert result.exit_code == 0
        assert "reconciliation was skipped" in _plain(result.output)
        assert "Reconciled 1 lockfile field(s)" not in _plain(result.output)

    def test_lockfile_planning_failure_is_reported_as_an_issue(
        self, runner: CliRunner, quiet_diagnosis: SimpleNamespace
    ):
        quiet_diagnosis.planner.side_effect = RuntimeError("lock unavailable")

        result = runner.invoke(doctor_module.doctor_app, [])

        assert result.exit_code == 1
        assert "Lockfile reconciliation failed: lock unavailable" in _plain(result.output)


class TestConfigAndSkillDiagnosis:
    def test_skill_check_returns_empty_when_bundled_source_is_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(doctor_module, "__file__", str(tmp_path / "pkg" / "cmd_doctor.py"))

        assert doctor_module._check_observal_skill_missing() == []

    def test_skill_check_uses_detected_harnesses_and_skips_unsupported_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import observal_shared.harness_registry as harness_registry

        module_file = tmp_path / "pkg" / "cmd_doctor.py"
        skill_source = module_file.parent / "skills" / "observal" / "SKILL.md"
        skill_source.parent.mkdir(parents=True)
        skill_source.write_text("skill", encoding="utf-8")
        (tmp_path / ".kiro").mkdir()
        registry = {
            "unsupported": {"display_name": "Unsupported", "config_dir": ".unsupported", "skills": {}},
            "kiro": {
                "display_name": "Kiro",
                "config_dir": ".kiro",
                "skills": {"user": "~/.kiro/skills/{name}/SKILL.md"},
            },
            "absent": {
                "display_name": "Absent",
                "config_dir": ".absent",
                "skills": {"user": "~/.absent/skills/{name}/SKILL.md"},
            },
        }
        monkeypatch.setattr(doctor_module, "__file__", str(module_file))
        monkeypatch.setattr(harness_registry, "HARNESS_REGISTRY", registry)

        assert doctor_module._check_observal_skill_missing() == ["Kiro"]

    def test_invalid_config_is_reported_without_network(self, tmp_path: Path, isolated_runtime: SimpleNamespace):
        config_path = tmp_path / ".observal" / "config.json"
        config_path.parent.mkdir()
        config_path.write_text("{bad json", encoding="utf-8")
        issues: list[str] = []

        doctor_module._check_observal_config(issues, [])

        assert issues == ["~/.observal/config.json is not valid JSON."]
        isolated_runtime.network.assert_not_called()

    def test_missing_config_fields_are_both_reported(self, tmp_path: Path, isolated_runtime: SimpleNamespace):
        _write_json(tmp_path / ".observal" / "config.json", {})
        issues: list[str] = []

        doctor_module._check_observal_config(issues, [])

        assert issues == [
            "No access token in ~/.observal/config.json. Run `observal auth login`.",
            "No server_url in ~/.observal/config.json. Run `observal auth login`.",
        ]
        isolated_runtime.network.assert_not_called()

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (200, []),
            (503, ["Observal server at https://server.test returned status 503."]),
        ],
    )
    def test_server_health_status_controls_config_issue(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        status: int,
        expected: list[str],
    ):
        _write_json(
            tmp_path / ".observal" / "config.json",
            {"access_token": "placeholder", "server_url": "https://server.test"},
        )
        get = MagicMock(return_value=SimpleNamespace(status_code=status))
        monkeypatch.setattr(httpx, "get", get)
        issues: list[str] = []

        doctor_module._check_observal_config(issues, [])

        assert issues == expected
        get.assert_called_once_with("https://server.test/health", timeout=5)


class TestHarnessDiagnosisState:
    def test_claude_accepts_current_hooks_and_reports_only_legacy_hooks(self, tmp_path: Path):
        _write_json(
            tmp_path / ".claude" / "settings.json",
            {
                "hooks": {
                    "UserPromptSubmit": [{"hooks": [{"command": "python -m observal_cli.hooks.session_push"}]}],
                    "Legacy": [{"hooks": [{"command": "/tmp/observal-hook"}]}],
                    "Ignored": "not-a-list",
                }
            },
        )
        issues: list[str] = []
        warnings: list[str] = []

        doctor_module._check_claude_code(issues, warnings)

        assert issues == []
        assert warnings == [
            "Legacy Observal hooks detected (old hook scripts). "
            "Run `observal doctor cleanup --harness claude-code` to remove them."
        ]

    def test_claude_invalid_settings_are_an_issue(self, tmp_path: Path):
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir()
        settings.write_text("invalid", encoding="utf-8")
        issues: list[str] = []

        doctor_module._check_claude_code(issues, [])

        assert issues == [f"{settings}: not valid JSON."]

    def test_kiro_empty_directory_and_current_profile_have_no_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        agents = tmp_path / ".kiro" / "agents"
        agents.mkdir(parents=True)

        doctor_module._check_kiro([], [])
        assert "No Kiro agent configs found" in _plain(capsys.readouterr().out)

        (agents / "broken.json").write_text("invalid", encoding="utf-8")
        _write_json(
            agents / "current.json",
            {
                "hooks": {
                    "ignored": "not-a-list",
                    "userPromptSubmit": [{"command": "python -m observal_cli.hooks.session_push --harness kiro"}],
                }
            },
        )
        warnings: list[str] = []

        doctor_module._check_kiro([], warnings)

        assert warnings == []

    def test_pi_recognizes_dict_form_npm_entry_and_ignores_malformed_entries(self, tmp_path: Path):
        _write_json(
            tmp_path / ".pi" / "agent" / "settings.json",
            {"packages": [{"source": f"npm:observal-pi@{CLI_VERSION}"}, 7]},
        )
        issues: list[str] = []
        warnings: list[str] = []

        doctor_module._check_pi(issues, warnings)

        assert issues == []
        assert warnings == []  # npm configured and current: no local install, nothing to report
        assert not (tmp_path / ".pi" / "agent" / "extensions" / "observal.ts").exists()

    def test_pi_source_and_settings_failures_are_issues(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        (tmp_path / ".pi" / "agent").mkdir(parents=True)
        monkeypatch.setattr(pi_extension, "extension_source", MagicMock(side_effect=OSError("source missing")))
        issues: list[str] = []

        doctor_module._check_pi(issues, [])

        assert issues == ["Pi telemetry extension: source missing"]

        monkeypatch.setattr(pi_extension, "extension_source", lambda: "source")
        settings = tmp_path / ".pi" / "agent" / "settings.json"
        settings.write_text("invalid", encoding="utf-8")
        issues = []

        doctor_module._check_pi(issues, [])

        assert issues == [f"{settings}: not valid JSON."]

    def test_pi_extension_source_fails_when_no_bundled_or_source_file_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(pi_extension, "__file__", str(tmp_path / "pkg" / "observal_cli" / "pi_extension.py"))

        with pytest.raises(FileNotFoundError, match="Bundled Pi telemetry extension is missing"):
            pi_extension.extension_source()

    @pytest.mark.parametrize(
        ("harness", "relative_path", "checker", "expected"),
        [
            ("cursor", ".cursor/hooks.json", doctor_module._check_cursor, "not valid JSON"),
            ("copilot-cli", ".copilot/hooks/observal.json", doctor_module._check_copilot_cli, "not valid JSON"),
        ],
    )
    def test_invalid_hook_files_are_diagnosis_issues(
        self,
        tmp_path: Path,
        harness: str,
        relative_path: str,
        checker,
        expected: str,
    ):
        del harness
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True)
        path.write_text("invalid", encoding="utf-8")
        issues: list[str] = []

        checker(issues, [])

        assert issues == [f"{path}: {expected}."]

    @pytest.mark.parametrize(
        ("relative_path", "checker", "hooks"),
        [
            (
                ".cursor/hooks.json",
                doctor_module._check_cursor,
                {"beforeSubmitPrompt": [{"command": "python -m hooks.session_push --harness cursor"}]},
            ),
            (
                ".copilot/hooks/observal.json",
                doctor_module._check_copilot_cli,
                {"sessionStart": [{"bash": "python -m hooks.session_push --harness copilot-cli"}]},
            ),
        ],
    )
    def test_current_cursor_and_copilot_cli_hooks_have_no_findings(
        self, tmp_path: Path, relative_path: str, checker, hooks: dict
    ):
        _write_json(tmp_path / relative_path, {"hooks": hooks})
        issues: list[str] = []
        warnings: list[str] = []

        checker(issues, warnings)

        assert issues == []
        assert warnings == []

    def test_current_codex_hooks_survive_unreadable_optional_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        hooks_path = tmp_path / ".codex" / "hooks.json"
        config_path = tmp_path / ".codex" / "config.toml"
        _write_json(
            hooks_path,
            {"hooks": {"UserPromptSubmit": [{"hooks": [{"command": "python -m hooks.session_push --harness codex"}]}]}},
        )
        config_path.write_text("codex_hooks = true\n", encoding="utf-8")
        original_read_text = Path.read_text

        def read_text(path: Path, *args, **kwargs) -> str:
            if path == config_path:
                raise OSError("denied")
            return original_read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read_text)
        issues: list[str] = []
        warnings: list[str] = []

        doctor_module._check_codex(issues, warnings)

        assert issues == [f"{config_path}: cannot read Codex configuration: denied"]
        assert warnings == []

    def test_copilot_detects_current_project_hook(self, tmp_path: Path):
        (tmp_path / ".vscode").mkdir()
        _write_json(
            tmp_path / ".github" / "hooks" / "observal.json",
            {"hooks": {"Stop": [{"command": "run_hook.ps1"}]}},
        )
        warnings: list[str] = []

        doctor_module._check_copilot([], warnings)

        assert warnings == []

    def test_opencode_read_failure_is_an_issue(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        plugin = tmp_path / ".config" / "opencode" / "plugins" / "observal-plugin.ts"
        plugin.parent.mkdir(parents=True)
        plugin.write_text("plugin", encoding="utf-8")
        original_read_text = Path.read_text

        def read_text(path: Path, *args, **kwargs) -> str:
            if path == plugin:
                raise OSError("denied")
            return original_read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read_text)
        issues: list[str] = []

        doctor_module._check_opencode(issues, [])

        assert issues == [f"{plugin}: failed to read OpenCode plugin: denied"]

    @pytest.mark.parametrize(
        ("data", "issue", "warning"),
        [
            (None, "not valid JSON", None),
            ({"observal-telemetry": []}, None, "not installed"),
            ({"observal-telemetry": {"PreInvocation": "invalid"}}, None, "not installed"),
            (
                {"observal-telemetry": {"Stop": [{"command": "antigravity_session_push"}]}},
                None,
                None,
            ),
        ],
    )
    def test_antigravity_hook_states(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        data: dict | None,
        issue: str | None,
        warning: str | None,
    ):
        from observal_cli.shared import utils

        config_dir = tmp_path / ".gemini" / "config"
        config_dir.mkdir(parents=True)
        hooks_path = config_dir / "hooks.json"
        if data is None:
            hooks_path.write_text("invalid", encoding="utf-8")
        else:
            _write_json(hooks_path, data)
        monkeypatch.setattr(utils, "resolve_antigravity_config_dir", lambda: config_dir)
        issues: list[str] = []
        warnings: list[str] = []

        doctor_module._check_antigravity(issues, warnings)

        assert (issue is None) == (issues == [])
        assert (warning is None) == (warnings == [])
        if issue:
            assert issue in issues[0]
        if warning:
            assert warning in warnings[0]


@pytest.fixture
def patch_dispatch(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    import observal_cli.audit as audit_module

    config_load = MagicMock(return_value={"server_url": "https://server.test"})
    ensure_loaded = MagicMock()
    get_adapter = MagicMock()
    audit = MagicMock()
    monkeypatch.setattr(doctor_module.config, "load", config_load)
    monkeypatch.setattr(doctor_module, "ensure_loaded", ensure_loaded)
    monkeypatch.setattr(doctor_module, "get_adapter", get_adapter)
    monkeypatch.setattr(audit_module, "emit_cli_audit", audit)
    monkeypatch.setattr(doctor_module, "_VALID_HARNESSES", ["claude-code", "pi"])
    return SimpleNamespace(config_load=config_load, ensure_loaded=ensure_loaded, get_adapter=get_adapter, audit=audit)


class TestDoctorPatchCommand:
    def test_requires_a_target_before_loading_config(self, runner: CliRunner, patch_dispatch: SimpleNamespace):
        result = runner.invoke(doctor_module.doctor_app, ["patch"])

        assert result.exit_code == 7
        assert "requires a target harness" in _plain(result.output)
        patch_dispatch.config_load.assert_not_called()
        patch_dispatch.ensure_loaded.assert_not_called()

    def test_requires_login_before_loading_adapters(self, runner: CliRunner, patch_dispatch: SimpleNamespace):
        patch_dispatch.config_load.return_value = {}

        result = runner.invoke(doctor_module.doctor_app, ["patch", "--harness", "pi"])

        assert result.exit_code == 3
        assert "authentication is not configured" in _plain(result.output)
        patch_dispatch.ensure_loaded.assert_not_called()
        patch_dispatch.get_adapter.assert_not_called()

    def test_rejects_unknown_harness_with_valid_choices(self, runner: CliRunner, patch_dispatch: SimpleNamespace):
        result = runner.invoke(doctor_module.doctor_app, ["patch", "--harness", "unknown"])

        assert result.exit_code == 7
        assert "Unknown harness: unknown" in _plain(result.output)
        patch_dispatch.ensure_loaded.assert_not_called()

    def test_selected_harnesses_dispatch_in_order_and_emit_audit(
        self, runner: CliRunner, patch_dispatch: SimpleNamespace
    ):
        adapters = {name: MagicMock() for name in ("claude-code", "pi")}
        adapters["claude-code"].patch_hooks.return_value = False
        adapters["pi"].patch_hooks.return_value = True
        patch_dispatch.get_adapter.side_effect = adapters.__getitem__

        result = runner.invoke(
            doctor_module.doctor_app,
            ["patch", "--harness", "claude-code", "--harness", "pi"],
        )

        assert result.exit_code == 0
        assert "Patch complete. Restart your harness sessions" in _plain(result.output)
        patch_dispatch.ensure_loaded.assert_called_once_with()
        assert patch_dispatch.get_adapter.call_args_list == [call("claude-code"), call("pi")]
        adapters["claude-code"].patch_hooks.assert_called_once_with(False)
        adapters["pi"].patch_hooks.assert_called_once_with(False)
        patch_dispatch.audit.assert_called_once_with(
            "doctor.patch",
            resource_type="harness",
            detail="harnesses=claude-code,pi, hooks=true",
            sensitivity="high",
        )

    def test_all_harnesses_dry_run_dispatches_every_target_without_audit(
        self, runner: CliRunner, patch_dispatch: SimpleNamespace
    ):
        adapters = {name: MagicMock() for name in ("claude-code", "pi")}
        for adapter in adapters.values():
            adapter.patch_hooks.return_value = True
        patch_dispatch.get_adapter.side_effect = adapters.__getitem__

        result = runner.invoke(doctor_module.doctor_app, ["patch", "--all-harnesses", "--dry-run"])

        assert result.exit_code == 0
        assert "Dry run, no changes made." in _plain(result.output)
        assert patch_dispatch.get_adapter.call_args_list == [call("claude-code"), call("pi")]
        for adapter in adapters.values():
            adapter.patch_hooks.assert_called_once_with(True)
        patch_dispatch.audit.assert_not_called()

    def test_no_adapter_changes_reports_up_to_date_without_audit(
        self, runner: CliRunner, patch_dispatch: SimpleNamespace
    ):
        adapter = MagicMock()
        adapter.patch_hooks.return_value = False
        patch_dispatch.get_adapter.return_value = adapter

        result = runner.invoke(doctor_module.doctor_app, ["patch", "--harness", "pi"])

        assert result.exit_code == 0
        assert "Everything already up to date." in _plain(result.output)
        patch_dispatch.audit.assert_not_called()

    def test_unsupported_adapter_failure_is_not_hidden(self, runner: CliRunner, patch_dispatch: SimpleNamespace):
        adapter = MagicMock()
        adapter.patch_hooks.side_effect = NotSupportedError("pi", "patch_hooks")
        patch_dispatch.get_adapter.return_value = adapter

        result = runner.invoke(doctor_module.doctor_app, ["patch", "--harness", "pi"])

        assert result.exit_code == 7
        assert "pi does not support patch_hooks" in _plain(result.output)
        assert "Patch complete" not in _plain(result.output)
        patch_dispatch.audit.assert_not_called()


@pytest.fixture
def cleanup_dispatch(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    import observal_cli.audit as audit_module

    ensure_loaded = MagicMock()
    get_adapter = MagicMock()
    valid_harnesses = MagicMock(return_value=["claude-code", "pi"])
    audit = MagicMock()
    monkeypatch.setattr(doctor_module, "ensure_loaded", ensure_loaded)
    monkeypatch.setattr(doctor_module, "get_adapter", get_adapter)
    monkeypatch.setattr(doctor_module, "get_valid_harnesses", valid_harnesses)
    monkeypatch.setattr(audit_module, "emit_cli_audit", audit)
    return SimpleNamespace(
        ensure_loaded=ensure_loaded,
        get_adapter=get_adapter,
        valid_harnesses=valid_harnesses,
        audit=audit,
    )


class TestDoctorCleanupCommand:
    def test_selected_harness_dispatches_and_reports_completion(
        self, runner: CliRunner, cleanup_dispatch: SimpleNamespace
    ):
        adapter = MagicMock()
        adapter.cleanup_hooks.return_value = True
        cleanup_dispatch.get_adapter.return_value = adapter

        result = runner.invoke(doctor_module.doctor_app, ["cleanup", "--harness", "claude-code", "--yes"])

        assert result.exit_code == 0
        assert "Cleanup complete. Restart your harness sessions" in _plain(result.output)
        cleanup_dispatch.valid_harnesses.assert_called_once_with()
        cleanup_dispatch.ensure_loaded.assert_called_once_with()
        cleanup_dispatch.get_adapter.assert_called_once_with("claude-code")
        adapter.cleanup_hooks.assert_called_once_with(False)

    def test_default_selection_honors_exclusions_and_dry_run(
        self, runner: CliRunner, cleanup_dispatch: SimpleNamespace
    ):
        adapter = MagicMock()
        adapter.cleanup_hooks.return_value = True
        cleanup_dispatch.get_adapter.return_value = adapter

        result = runner.invoke(
            doctor_module.doctor_app,
            ["cleanup", "--exclude", "pi", "--dry-run"],
        )

        assert result.exit_code == 0
        cleanup_dispatch.valid_harnesses.assert_called_once_with()
        cleanup_dispatch.get_adapter.assert_called_once_with("claude-code")
        adapter.cleanup_hooks.assert_called_once_with(True)
        output = _plain(result.output)
        assert "Cleanup complete" not in output
        assert "Nothing to clean up" not in output

    def test_unknown_adapter_is_reported_and_cleanup_continues(
        self, runner: CliRunner, cleanup_dispatch: SimpleNamespace
    ):
        cleanup_dispatch.get_adapter.side_effect = KeyError("missing")

        result = runner.invoke(doctor_module.doctor_app, ["cleanup", "--harness", "unknown"])
        output = _plain(result.output)

        assert result.exit_code == 7
        assert "Unknown harness: unknown" in output

    def test_no_changes_reports_nothing_to_clean(self, runner: CliRunner, cleanup_dispatch: SimpleNamespace):
        adapter = MagicMock()
        adapter.cleanup_hooks.return_value = False
        cleanup_dispatch.get_adapter.return_value = adapter

        result = runner.invoke(doctor_module.doctor_app, ["cleanup", "--harness", "pi", "--yes"])

        assert result.exit_code == 0
        assert "Nothing to clean up, no Observal artifacts found." in _plain(result.output)

    def test_unsupported_cleanup_failure_is_not_hidden(self, runner: CliRunner, cleanup_dispatch: SimpleNamespace):
        adapter = MagicMock()
        adapter.cleanup_hooks.side_effect = NotSupportedError("pi", "cleanup_hooks")
        cleanup_dispatch.get_adapter.return_value = adapter

        result = runner.invoke(doctor_module.doctor_app, ["cleanup", "--harness", "pi", "--yes"])

        assert result.exit_code == 7
        assert "pi does not support cleanup_hooks" in _plain(result.output)
        assert "Cleanup complete" not in _plain(result.output)


class TestCleanupStateEdges:
    def test_claude_dry_run_preserves_file_then_cleanup_removes_empty_sections(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        settings = tmp_path / ".claude" / "settings.json"
        _write_json(
            settings,
            {
                "env": {"OBSERVAL_HOOKS_URL": "https://server.test"},
                "hooks": {
                    "Stop": [
                        {
                            "_observal": {"version": "1"},
                            "hooks": [{"command": "python -m observal_cli.hooks.session_push"}],
                        }
                    ]
                },
            },
        )
        original = settings.read_text(encoding="utf-8")

        assert doctor_module._cleanup_claude_code(dry_run=True) is True
        assert settings.read_text(encoding="utf-8") == original
        assert "Would remove env vars" in _plain(capsys.readouterr().out)

        assert doctor_module._cleanup_claude_code(dry_run=False) is True
        assert json.loads(settings.read_text(encoding="utf-8")) == {}

    @pytest.mark.parametrize(
        ("relative_path", "cleanup"),
        [
            (".claude/settings.json", doctor_module._cleanup_claude_code),
            (".pi/agent/settings.json", doctor_module._cleanup_pi),
            (".cursor/hooks.json", doctor_module._cleanup_cursor),
            (".codex/hooks.json", doctor_module._cleanup_codex),
        ],
    )
    def test_invalid_cleanup_files_fail_without_mutation(self, tmp_path: Path, relative_path: str, cleanup):
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True)
        path.write_text("invalid", encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            cleanup(dry_run=False)
        assert path.read_text(encoding="utf-8") == "invalid"

    @pytest.mark.parametrize(
        "cleanup",
        [
            doctor_module._cleanup_kiro,
            doctor_module._cleanup_cursor,
            doctor_module._cleanup_codex,
            doctor_module._cleanup_copilot,
            doctor_module._cleanup_copilot_cli,
            doctor_module._cleanup_opencode,
        ],
    )
    def test_missing_cleanup_artifacts_report_no_change(self, cleanup):
        assert cleanup(dry_run=False) is False


class TestPatchStateEdges:
    def test_kiro_skips_unusable_lock_entries_and_reports_profiles(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        from observal_cli import lockfile

        invalid = tmp_path / ".kiro" / "agents" / "invalid.json"
        invalid.parent.mkdir(parents=True)
        invalid.write_text("invalid", encoding="utf-8")
        current = invalid.parent / "current.json"
        _write_json(current, {"hooks": {}})
        registry = {
            "harnesses": {
                "kiro": {
                    "agents": [
                        {"id": "no-name", "scope": "user"},
                        {"id": "no-directory", "local_name": "project", "scope": "project"},
                        {
                            "id": "missing-profile",
                            "local_name": "missing",
                            "scope": "project",
                            "directory": str(tmp_path / "repo"),
                        },
                        {"id": "invalid-profile", "local_name": "invalid", "scope": "user"},
                        {"id": "current-profile", "local_name": "current", "scope": "user"},
                    ]
                }
            }
        }
        adapter = MagicMock()
        adapter.rewrite_agent_profile.side_effect = lambda profile, *, agent_id: profile
        monkeypatch.setattr(lockfile, "read_registry_lockfile", MagicMock(return_value=(None, registry)))
        monkeypatch.setattr(doctor_module, "ensure_loaded", MagicMock())
        monkeypatch.setattr(doctor_module, "get_adapter", MagicMock(return_value=adapter))

        with pytest.raises(json.JSONDecodeError):
            doctor_module._patch_kiro(dry_run=False)
        assert "Missing locked profile" in _plain(capsys.readouterr().out)
        adapter.rewrite_agent_profile.assert_not_called()

    def test_cursor_missing_directory_and_invalid_file_dry_run_do_not_write(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        assert doctor_module._patch_cursor(dry_run=False) is False
        assert "skipping" in _plain(capsys.readouterr().out)

        hooks = tmp_path / ".cursor" / "hooks.json"
        hooks.parent.mkdir()
        hooks.write_text("invalid", encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            doctor_module._patch_cursor(dry_run=True)
        assert hooks.read_text(encoding="utf-8") == "invalid"

    def test_antigravity_missing_detection_and_invalid_file_dry_run_do_not_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        from observal_cli.shared import utils

        resolver = MagicMock(return_value=None)
        monkeypatch.setattr(utils, "resolve_antigravity_config_dir", resolver)
        assert doctor_module._patch_antigravity(dry_run=False) is False
        assert "skipping" in _plain(capsys.readouterr().out)

        config_dir = tmp_path / ".gemini" / "config"
        config_dir.mkdir(parents=True)
        hooks = config_dir / "hooks.json"
        hooks.write_text("invalid", encoding="utf-8")
        resolver.return_value = config_dir

        with pytest.raises(json.JSONDecodeError):
            doctor_module._patch_antigravity(dry_run=True)
        assert hooks.read_text(encoding="utf-8") == "invalid"

    def test_pi_missing_detection_and_invalid_settings_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        assert doctor_module._patch_pi(dry_run=False) is False
        assert "skipping" in _plain(capsys.readouterr().out)

        pi_dir = tmp_path / ".pi" / "agent"
        pi_dir.mkdir(parents=True)
        settings = pi_dir / "settings.json"
        settings.write_text("invalid", encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            doctor_module._patch_pi(dry_run=False)
        assert not (pi_dir / "extensions" / "observal.ts").exists()

    def test_pi_dry_run_leaves_npm_configured_settings_untouched(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        settings = tmp_path / ".pi" / "agent" / "settings.json"
        _write_json(settings, {"packages": ["npm:observal-pi"]})
        before = settings.read_text(encoding="utf-8")

        assert doctor_module._patch_pi(dry_run=True) is False
        assert settings.read_text(encoding="utf-8") == before
        assert not (tmp_path / ".pi" / "agent" / "extensions" / "observal.ts").exists()
        output = _plain(capsys.readouterr().out)
        assert "npm:observal-pi is configured" in output

    def test_pi_dry_run_previews_local_install_without_writing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        (tmp_path / ".pi" / "agent").mkdir(parents=True)

        assert doctor_module._patch_pi(dry_run=True) is True
        assert not (tmp_path / ".pi" / "agent" / "extensions" / "observal.ts").exists()
        assert not (tmp_path / ".pi" / "agent" / "extensions" / ".observal-extension.json").exists()
        output = _plain(capsys.readouterr().out)
        assert "Would install" in output

    def test_codex_dry_run_on_missing_state_does_not_create_directory(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        assert doctor_module._patch_codex(dry_run=True) is True
        assert not (tmp_path / ".codex").exists()
        output = _plain(capsys.readouterr().out)
        assert "Would install hooks" in output
        assert "Would enable codex_hooks flag" in output

    @pytest.mark.parametrize(
        ("patcher", "relative_path", "message"),
        [
            (
                doctor_module._patch_copilot_cli,
                ".copilot/hooks/observal.json",
                "Would install hooks",
            ),
            (
                doctor_module._patch_opencode,
                ".config/opencode/plugins/observal-plugin.ts",
                "Would update plugin",
            ),
        ],
    )
    def test_plugin_patch_dry_runs_leave_files_untouched(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        patcher,
        relative_path: str,
        message: str,
    ):
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True)
        content = "{}" if patcher is doctor_module._patch_copilot_cli else "stale"
        path.write_text(content, encoding="utf-8")

        assert patcher(dry_run=True) is True
        assert path.read_text(encoding="utf-8") == content
        assert message in _plain(capsys.readouterr().out)


def test_doctor_json_returns_findings_with_success_exit(runner, quiet_diagnosis):
    def issue(issues: list[str], warnings: list[str]) -> None:
        issues.append("configuration is broken")
        warnings.append("hooks are stale")

    quiet_diagnosis.checks["_check_observal_config"].side_effect = issue

    result = runner.invoke(doctor_module.doctor_app, ["--output", "json"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["healthy"] is False
    assert data["issues"] == ["configuration is broken"]
    assert data["warnings"] == ["hooks are stale"]
    assert data["fix_attempted"] is False
    assert "Observal Doctor" not in result.stdout


def test_doctor_patch_and_cleanup_json_results(runner, patch_dispatch, cleanup_dispatch, monkeypatch):
    patch_adapters = {name: MagicMock() for name in ("claude-code", "pi")}
    patch_adapters["claude-code"].patch_hooks.return_value = False
    patch_adapters["pi"].patch_hooks.return_value = True
    patch_dispatch.get_adapter.side_effect = patch_adapters.__getitem__
    monkeypatch.setattr(doctor_module, "get_adapter", patch_dispatch.get_adapter)

    patch_result = runner.invoke(
        doctor_module.doctor_app,
        ["patch", "--all-harnesses", "--dry-run", "--output", "json"],
    )

    assert patch_result.exit_code == 0
    assert json.loads(patch_result.stdout) == {
        "action": "patch",
        "dry_run": True,
        "changed": True,
        "targets": [
            {"harness": "claude-code", "changed": False},
            {"harness": "pi", "changed": True},
        ],
    }

    cleanup_adapter = MagicMock()
    cleanup_adapter.cleanup_hooks.return_value = True
    cleanup_dispatch.get_adapter.return_value = cleanup_adapter
    monkeypatch.setattr(doctor_module, "get_adapter", cleanup_dispatch.get_adapter)
    cleanup_result = runner.invoke(
        doctor_module.doctor_app,
        ["cleanup", "--harness", "pi", "--yes", "--output", "json"],
    )

    assert cleanup_result.exit_code == 0
    assert json.loads(cleanup_result.stdout) == {
        "action": "cleanup",
        "dry_run": False,
        "changed": True,
        "targets": [{"harness": "pi", "changed": True}],
    }
    assert "Cleanup complete" not in cleanup_result.stdout


def test_doctor_command_inventory_and_json_cleanup_confirmation():
    from click import Group
    from typer.main import get_command

    from observal_cli.main import app

    doctor = get_command(app).commands["doctor"]

    def leaves(command, path=()):
        if not isinstance(command, Group) or command.invoke_without_command:
            yield " ".join(path) or "doctor", command
        if isinstance(command, Group):
            for name, child in command.commands.items():
                yield from leaves(child, (*path, name))

    rows = list(leaves(doctor))
    assert len(rows) == 5
    assert all(any(parameter.name == "output" for parameter in command.params) for _name, command in rows)

    result = CliRunner().invoke(app, ["doctor", "cleanup", "--harness", "pi", "--output", "json"])
    assert result.exit_code == 7
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "validation"
