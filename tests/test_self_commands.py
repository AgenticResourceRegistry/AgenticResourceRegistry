# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for `observal self upgrade/downgrade/rollback/status` commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture(autouse=True)
def disable_version_check(monkeypatch):
    """Disable version checks in all self-command tests."""
    monkeypatch.setenv("OBSERVAL_NO_UPDATE_CHECK", "1")


@pytest.fixture
def mock_version(monkeypatch):
    """Mock current CLI version."""
    monkeypatch.setattr("observal_cli.version_check.get_current_version", lambda: "1.2.0")


@pytest.fixture
def mock_install_uv(monkeypatch):
    """Mock install detection as uv tool."""
    from observal_cli.install_detector import InstallInfo, InstallMethod

    info = InstallInfo(
        method=InstallMethod.UV_TOOL,
        path=Path("/home/user/.local/bin/observal"),
        writable=True,
        managed_by="uv",
    )
    monkeypatch.setattr("observal_cli.install_detector.detect", lambda: info)
    monkeypatch.setattr("observal_cli.install_detector._cached_info", info)
    return info


@pytest.fixture
def mock_install_system(monkeypatch):
    """Mock install detection as a system package."""
    from observal_cli.install_detector import InstallInfo, InstallMethod

    info = InstallInfo(
        method=InstallMethod.SYSTEM_PACKAGE,
        path=Path("/usr/bin/observal"),
        writable=False,
        managed_by="apt",
    )
    monkeypatch.setattr("observal_cli.install_detector.detect", lambda: info)
    monkeypatch.setattr("observal_cli.install_detector._cached_info", info)
    return info


@pytest.fixture
def mock_lock(monkeypatch, tmp_path):
    """Mock upgrade lock to use tmp directory."""
    monkeypatch.setattr("observal_cli.upgrade_lock.CONFIG_DIR", tmp_path)


@pytest.fixture
def mock_auto_update_config(monkeypatch):
    """Keep downgrade pinning isolated from the user's real config."""
    state = {}
    monkeypatch.setattr("observal_cli.cmd_ops.config.load", lambda: state.copy())
    monkeypatch.setattr("observal_cli.cmd_ops.config.save", lambda updates: state.update(updates))
    return state


def _get_app():
    """Import the app fresh (avoids circular import issues in tests)."""
    from observal_cli.main import app

    return app


class TestSelfUpgrade:
    def test_upgrade_already_latest(self, mock_version, mock_install_uv, mock_lock, monkeypatch):
        monkeypatch.setattr(
            "observal_cli.version_check._fetch_from_github",
            lambda include_pre=False: {"latest_version": "1.2.0", "source": "github"},
        )
        app = _get_app()
        result = runner.invoke(app, ["self", "upgrade", "--force"])
        assert "Already on v1.2.0" in result.output

    def test_upgrade_managed_install_blocked(self, mock_version, mock_install_system, monkeypatch):
        app = _get_app()
        result = runner.invoke(app, ["self", "upgrade", "--force"])
        assert "managed by apt" in result.output.lower()

    def test_upgrade_specific_version(self, mock_version, mock_install_uv, mock_lock, monkeypatch):
        """--version 1.3.0 should attempt install of that version."""
        install_called = {"version": None}

        def mock_do_install(info, target, direction, output):
            install_called["version"] = target

        monkeypatch.setattr("observal_cli.cmd_ops._do_install", mock_do_install)
        app = _get_app()
        result = runner.invoke(app, ["self", "upgrade", "--version", "1.3.0", "--force"])
        assert install_called["version"] == "1.3.0"

    def test_upgrade_older_version_rejected(self, mock_version, mock_install_uv, mock_lock, monkeypatch):
        """Attempting to 'upgrade' to an older version should fail."""
        app = _get_app()
        result = runner.invoke(app, ["self", "upgrade", "--version", "1.0.0", "--force"])
        assert "older" in result.output.lower() or "downgrade" in result.output.lower()


class TestSelfDowngrade:
    def test_downgrade_requires_version(self, mock_version, monkeypatch):
        app = _get_app()
        result = runner.invoke(app, ["self", "downgrade"])
        assert "--version is required" in result.output or "version" in result.output.lower()

    def test_downgrade_list(self, mock_version, monkeypatch):
        monkeypatch.setattr(
            "observal_cli.version_check.fetch_all_releases",
            lambda include_pre=False: [
                {"version": "1.1.0", "published_at": "2026-05-20", "prerelease": False},
                {"version": "1.0.0", "published_at": "2026-05-10", "prerelease": False},
            ],
        )
        app = _get_app()
        result = runner.invoke(app, ["self", "downgrade", "--list"])
        assert "1.1.0" in result.output
        assert "1.0.0" in result.output

    def test_downgrade_target_is_newer(self, mock_version, monkeypatch):
        """Downgrade to a newer version should error."""
        app = _get_app()
        result = runner.invoke(app, ["self", "downgrade", "--version", "1.3.0"])
        assert "not older" in result.output.lower() or "upgrade" in result.output.lower()

    def test_downgrade_pipx_install(self, mock_version, mock_lock, mock_auto_update_config, monkeypatch):
        from observal_cli.install_detector import InstallInfo, InstallMethod

        info = InstallInfo(InstallMethod.PIPX, Path("/home/user/.local/bin/observal"), True, "pipx")
        install_called = {}
        monkeypatch.setattr("observal_cli.install_detector.detect", lambda: info)
        monkeypatch.setattr(
            "observal_cli.cmd_ops._do_install",
            lambda i, target, direction, output: install_called.update(i=i, target=target, direction=direction),
        )

        app = _get_app()
        result = runner.invoke(app, ["self", "downgrade", "--version", "1.1.0", "--force"])
        assert result.exit_code == 0
        assert install_called == {"i": info, "target": "1.1.0", "direction": "downgrade"}
        assert mock_auto_update_config["auto_update"] is False
        assert "legacy version pinned" in result.output

    def test_downgrade_curl_install(self, mock_version, mock_lock, mock_auto_update_config, monkeypatch):
        from observal_cli.install_detector import InstallInfo, InstallMethod

        info = InstallInfo(InstallMethod.BINARY, Path("/usr/local/bin/observal"), True, "curl")
        install_called = {}
        monkeypatch.setattr("observal_cli.install_detector.detect", lambda: info)
        monkeypatch.setattr(
            "observal_cli.cmd_ops._do_install",
            lambda i, target, direction, output: install_called.update(i=i, target=target, direction=direction),
        )

        app = _get_app()
        result = runner.invoke(app, ["self", "downgrade", "--version", "1.1.0", "--force"])
        assert result.exit_code == 0
        assert install_called == {"i": info, "target": "1.1.0", "direction": "downgrade"}
        assert mock_auto_update_config["auto_update"] is False

    def test_downgrade_restores_auto_update_when_install_fails(
        self, mock_version, mock_install_uv, mock_lock, mock_auto_update_config, monkeypatch
    ):
        mock_auto_update_config["auto_update"] = True

        def fail_install(*args, **kwargs):
            raise RuntimeError("install failed")

        monkeypatch.setattr("observal_cli.cmd_ops._do_install", fail_install)

        result = runner.invoke(_get_app(), ["self", "downgrade", "--version", "1.1.0", "--force"])

        assert result.exit_code != 0
        assert mock_auto_update_config["auto_update"] is True

    def test_downgrade_warns_when_auto_update_restore_fails(
        self, mock_version, mock_install_uv, mock_lock, mock_auto_update_config, monkeypatch
    ):
        save_calls = []

        def save_config(updates):
            save_calls.append(updates)
            if len(save_calls) == 2:
                raise SystemExit("permission denied")
            mock_auto_update_config.update(updates)

        def fail_install(*args, **kwargs):
            raise RuntimeError("install failed")

        monkeypatch.setattr("observal_cli.cmd_ops.config.save", save_config)
        monkeypatch.setattr("observal_cli.cmd_ops._do_install", fail_install)

        result = runner.invoke(_get_app(), ["self", "downgrade", "--version", "1.1.0", "--force"])

        assert result.exit_code == 9
        assert save_calls == [{"auto_update": False}, {"auto_update": True}]
        assert "automatic-update setting" in result.output
        assert "not be restored" in result.output

    def test_modern_downgrade_does_not_change_auto_update(
        self, mock_install_uv, mock_lock, mock_auto_update_config, monkeypatch
    ):
        monkeypatch.setattr("observal_cli.version_check.get_current_version", lambda: "1.11.0")
        mock_auto_update_config["auto_update"] = True
        monkeypatch.setattr("observal_cli.cmd_ops._do_install", lambda *args, **kwargs: None)

        result = runner.invoke(_get_app(), ["self", "downgrade", "--version", "1.10.4", "--force"])

        assert result.exit_code == 0
        assert mock_auto_update_config["auto_update"] is True
        assert "legacy version pinned" not in result.output


class TestInstallVerification:
    def test_verify_install_checks_target_executable(self, monkeypatch, capsys):
        from subprocess import CompletedProcess

        from observal_cli.install_detector import InstallInfo, InstallMethod
        from observal_cli.upgrade_executor import _verify_install

        info = InstallInfo(InstallMethod.UV_TOOL, Path("/tools/observal"), True, "uv")
        run_calls = []

        def mock_run(command, **kwargs):
            run_calls.append(command)
            return CompletedProcess(command, 0, stdout="observal 1.9.6\n", stderr="")

        monkeypatch.setattr("observal_cli.upgrade_executor.subprocess.run", mock_run)

        _verify_install(info, "1.9.6", "downgrade")

        assert run_calls == [["/tools/observal", "--version"]]
        assert "Downgraded to v1.9.6" in capsys.readouterr().out

    def test_verify_install_rejects_wrong_version(self, monkeypatch, capsys):
        from subprocess import CompletedProcess

        import typer

        from observal_cli.install_detector import InstallInfo, InstallMethod
        from observal_cli.upgrade_executor import _verify_install

        info = InstallInfo(InstallMethod.UV_TOOL, Path("/tools/observal"), True, "uv")
        monkeypatch.setattr(
            "observal_cli.upgrade_executor.subprocess.run",
            lambda command, **kwargs: CompletedProcess(command, 0, stdout="observal 1.11.0\n", stderr=""),
        )

        with pytest.raises(typer.Exit):
            _verify_install(info, "1.9.6", "downgrade")

        output = capsys.readouterr().out
        assert "expected v1.9.6" in output
        assert "reports" in output
        assert "v1.11.0" in output
        assert "/tools/observal" in output


class TestSelfRollback:
    def test_rollback_no_backup(self, monkeypatch, tmp_path):
        monkeypatch.setattr("observal_cli.config.CONFIG_DIR", tmp_path)
        from observal_cli.install_detector import InstallInfo, InstallMethod

        info = InstallInfo(InstallMethod.BINARY, Path("/usr/local/bin/observal"), True, None)
        monkeypatch.setattr("observal_cli.install_detector.detect", lambda: info)
        monkeypatch.setattr("observal_cli.install_detector._cached_info", info)

        app = _get_app()
        result = runner.invoke(app, ["self", "rollback"])
        assert "No CLI rollback backup" in result.output


class TestSelfStatus:
    def test_status_shows_version(self, mock_version, mock_install_uv, monkeypatch):
        monkeypatch.setattr(
            "observal_cli.version_check._fetch_from_github",
            lambda include_pre=False: {"latest_version": "1.3.0", "source": "github"},
        )
        app = _get_app()
        result = runner.invoke(app, ["self", "status"])
        assert "1.2.0" in result.output
