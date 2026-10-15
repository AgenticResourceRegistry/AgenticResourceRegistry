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
            patch("observal_cli.install_detector._check_uv_tool_list", return_value=False),
            patch("observal_cli.install_detector._check_pipx_list", return_value=False),
        ):
            result = _detect_from_path(path, str(path).lower())
        assert result.method == InstallMethod.PIP
        assert result.managed_by == "pip"


class TestWritableCheck:
    def test_writable_true(self):
        path = Path("/tmp/observal")
        with (
            patch("os.access", return_value=True),
            patch("observal_cli.install_detector._check_uv_tool_list", return_value=False),
            patch("observal_cli.install_detector._check_pipx_list", return_value=False),
        ):
            result = _detect_from_path(path, str(path).lower())
        assert result.writable is True

    def test_writable_false(self):
        path = Path("/usr/bin/observal")
        with patch("os.access", return_value=False):
            result = _detect_from_path(path, str(path).lower())
        assert result.writable is False


class TestUpgradeCommand:
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
