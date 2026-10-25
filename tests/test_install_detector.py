# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for observal_cli.install_detector."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from observal_cli.install_detector import (
    InstallInfo,
    InstallMethod,
    _detect_from_path,
    _write_install_metadata,
    detect,
    pip_interpreter,
    upgrade_command,
)


class TestDetectFromPath:
    def test_system_package_detected(self):
        path = Path("/usr/bin/observal")
        with patch("os.access", return_value=False):
            result = _detect_from_path(path, str(path).lower())
        assert result.method == InstallMethod.SYSTEM_PACKAGE
        assert result.writable is False

    def test_binary_frozen(self, monkeypatch):
        monkeypatch.setattr("sys.frozen", True, raising=False)
        path = Path("/home/user/bin/observal")
        with patch("os.access", return_value=True):
            result = _detect_from_path(path, str(path).lower())
        assert result.method == InstallMethod.BINARY
        assert result.writable is True

    def test_uv_tool_dir(self, monkeypatch, tmp_path):
        uv_dir = tmp_path / ".local" / "share" / "uv" / "tools"
        uv_dir.mkdir(parents=True)
        binary = uv_dir / "observal"
        binary.touch()

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        result = _detect_from_path(binary, str(binary).lower())
        assert result.method == InstallMethod.UV_TOOL
        assert result.managed_by == "uv"

    def test_xdg_uv_tool_dir(self, monkeypatch, tmp_path):
        tools = tmp_path / "custom-data" / "uv" / "tools" / "observal-cli" / "bin"
        tools.mkdir(parents=True)
        binary = tools / "observal"
        binary.touch()
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "custom-data"))
        assert _detect_from_path(binary, str(binary).lower()).method == InstallMethod.UV_TOOL

    def test_uv_copied_executable_outside_tool_dir(self, monkeypatch, tmp_path):
        from types import SimpleNamespace

        binary = tmp_path / "uv-bin" / "observal.exe"
        binary.parent.mkdir()
        binary.touch()
        monkeypatch.setattr("observal_cli.install_detector.shutil.which", lambda _: "/bin/uv")
        monkeypatch.setattr(
            "observal_cli.install_detector.subprocess.run",
            lambda *a, **kw: SimpleNamespace(
                returncode=0,
                stdout=f"observal-cli v1.16.0 ({tmp_path / 'tools'})\n- observal ({binary})\n",
            ),
        )
        assert _detect_from_path(binary, str(binary).lower()).method == InstallMethod.UV_TOOL

    def test_other_uv_install_does_not_own_this_pip_entry_point(self, monkeypatch, tmp_path):
        from types import SimpleNamespace

        binary = tmp_path / "pip-venv" / "observal"
        binary.parent.mkdir()
        binary.touch()
        monkeypatch.setattr("observal_cli.install_detector.shutil.which", lambda _: "/bin/uv")
        monkeypatch.setattr(
            "observal_cli.install_detector.subprocess.run",
            lambda *a, **kw: SimpleNamespace(returncode=0, stdout=f"- observal ({tmp_path / 'uv-bin' / 'observal'})\n"),
        )
        assert _detect_from_path(binary, str(binary).lower()).method == InstallMethod.PIP

    def test_pipx_path_detected(self, monkeypatch, tmp_path):
        pipx_dir = tmp_path / ".local" / "share" / "pipx" / "venvs" / "observal-cli" / "bin"
        pipx_dir.mkdir(parents=True)
        binary = pipx_dir / "observal"
        binary.touch()

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        result = _detect_from_path(binary, str(binary).lower())
        assert result.method == InstallMethod.PIPX
        assert result.managed_by == "pipx"

    def test_curl_metadata_detected(self, monkeypatch, tmp_path):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        binary = tmp_path / "bin" / "observal"
        binary.parent.mkdir()
        binary.touch()
        _write_install_metadata(InstallInfo(InstallMethod.BINARY, binary, True, "curl"))
        monkeypatch.setattr("sys.frozen", True, raising=False)

        result = _detect_from_path(binary, str(binary).lower())
        assert result.method == InstallMethod.BINARY
        assert result.managed_by == "curl"

    def test_fallback_pip(self, monkeypatch):
        monkeypatch.delattr("sys.frozen", raising=False)
        path = Path("/home/user/.venv/bin/observal")
        with (
            patch("os.access", return_value=True),
        ):
            result = _detect_from_path(path, str(path).lower())
        assert result.method == InstallMethod.PIP
        assert result.managed_by == "pip"


class TestWritableCheck:
    def test_writable_true(self):
        path = Path("/tmp/observal")
        with (
            patch("os.access", return_value=True),
        ):
            result = _detect_from_path(path, str(path).lower())
        assert result.writable is True

    def test_writable_false(self):
        path = Path("/usr/bin/observal")
        with patch("os.access", return_value=False):
            result = _detect_from_path(path, str(path).lower())
        assert result.writable is False


def test_detect_python_module_uses_running_interpreter(monkeypatch, tmp_path):
    from observal_cli import install_detector

    interpreter = tmp_path / "python"
    interpreter.touch()
    monkeypatch.setattr("sys.argv", ["/elsewhere/observal_cli/main.py", "auth", "login"])
    monkeypatch.setattr("sys.executable", str(interpreter))
    monkeypatch.setattr(install_detector, "_cached_info", None)
    monkeypatch.setattr(
        install_detector, "_detect_from_path", lambda path, _: InstallInfo(InstallMethod.PIP, path, True, "pip")
    )
    assert detect().path == interpreter.resolve()


def test_detect_uses_invoked_entry_point_not_another_observal_on_path(monkeypatch, tmp_path):
    from observal_cli import install_detector

    script = tmp_path / "observal"
    script.write_text("#!/usr/bin/python3\n")
    monkeypatch.setattr("sys.argv", [str(script), "auth", "login"])
    monkeypatch.setattr(install_detector.shutil, "which", lambda _: "/elsewhere/observal")
    monkeypatch.setattr(install_detector, "_cached_info", None)
    monkeypatch.setattr(
        install_detector, "_detect_from_path", lambda path, _: InstallInfo(InstallMethod.PIP, path, True, "pip")
    )
    assert detect().path == script.resolve()


class TestUpgradeCommand:
    def test_pip_command_uses_entry_point_interpreter(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "observal_cli.install_detector.subprocess.run", lambda *a, **kw: type("Result", (), {"returncode": 0})()
        )
        python = tmp_path / "python"
        python.touch()
        script = tmp_path / "observal"
        script.write_text(f"#!{python}\n")
        info = InstallInfo(InstallMethod.PIP, script, True, "pip")
        assert pip_interpreter(info) == str(python)
        assert upgrade_command("1.2.0", info) == f"{python} -m pip install observal-cli==1.2.0"

    def test_self_upgrade_targets_the_same_interpreter(self, monkeypatch, tmp_path):
        from contextlib import nullcontext
        from unittest.mock import MagicMock

        from observal_cli import upgrade_executor

        python = tmp_path / "python"
        python.touch()
        script = tmp_path / "observal"
        script.write_text(f"#!{python}\n")
        info = InstallInfo(InstallMethod.PIP, script, True, "pip")
        run = MagicMock(return_value=MagicMock(returncode=0, stderr=""))
        monkeypatch.setattr(upgrade_executor.subprocess, "run", run)
        upgrade_executor._install_via_pip("1.14.0", "upgrade", lambda _: nullcontext(), info)
        assert run.call_args.args[0][:4] == [str(python), "-m", "pip", "install"]

    def test_uv_targets_pipless_cli_interpreter(self, monkeypatch, tmp_path):
        from observal_cli.install_detector import pip_install_prefix

        interpreter = tmp_path / "python"
        interpreter.touch()
        script = tmp_path / "observal"
        script.write_text(f"#!{interpreter}\n")
        monkeypatch.setattr(
            "observal_cli.install_detector.shutil.which", lambda name: "/bin/uv" if name == "uv" else None
        )
        info = InstallInfo(InstallMethod.PIP, script, True, "pip")
        assert pip_install_prefix(info) == ["uv", "pip", "install", "--python", str(interpreter)]
        assert upgrade_command("1.14.0", info) == f"uv pip install --python {interpreter} observal-cli==1.14.0"

    def test_shell_wrapper_uses_running_python(self, monkeypatch, tmp_path):
        monkeypatch.setattr("sys.executable", "/venv/bin/python")
        script = tmp_path / "observal"
        script.write_text("#!/bin/sh\n'''exec' /venv/bin/python \"$0\" \"$@\"\n")
        info = InstallInfo(InstallMethod.PIP, script, True, "pip")
        assert pip_interpreter(info) == "/venv/bin/python"

    def test_python_shebang_with_flags_uses_its_interpreter(self, tmp_path):
        interpreter = tmp_path / "python3.14"
        interpreter.touch()
        script = tmp_path / "observal"
        script.write_text(f"#!{interpreter} -E\n")
        info = InstallInfo(InstallMethod.PIP, script, True, "pip")
        assert pip_interpreter(info) == str(interpreter)

    def test_other_shell_shebang_falls_back_to_running_python(self, monkeypatch, tmp_path):
        interpreter = tmp_path / "zsh"
        interpreter.touch()
        script = tmp_path / "observal"
        script.write_text(f"#!{interpreter}\n")
        monkeypatch.setattr("sys.executable", "/venv/bin/python")
        info = InstallInfo(InstallMethod.PIP, script, True, "pip")
        assert pip_interpreter(info) == "/venv/bin/python"

    def test_pip_command_falls_back_for_missing_entry_point(self, monkeypatch, tmp_path):
        monkeypatch.setattr("sys.executable", "/fallback/python")
        info = InstallInfo(InstallMethod.PIP, tmp_path / "missing", True, "pip")
        assert pip_interpreter(info) == "/fallback/python"

    def test_pipx_command(self):
        info = InstallInfo(InstallMethod.PIPX, Path("/fake/observal"), True, "pipx")
        assert upgrade_command("1.2.0", info) == "pipx install --force observal-cli==1.2.0"

    def test_curl_command_uses_installer(self):
        info = InstallInfo(InstallMethod.BINARY, Path("/fake/observal"), True, "curl")
        assert upgrade_command("1.2.0", info) == (
            "curl -fsSL https://raw.githubusercontent.com/Observal/Observal/main/install.sh "
            "| bash -s -- --version v1.2.0"
        )

    def test_unknown_command_uses_noninteractive_self_upgrade(self):
        info = InstallInfo(InstallMethod.UNKNOWN, Path("/fake/observal"), True, None)
        assert upgrade_command("1.2.0", info) == "observal self upgrade --version 1.2.0 --force"
