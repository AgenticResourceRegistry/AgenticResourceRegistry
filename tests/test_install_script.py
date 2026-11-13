# SPDX-FileCopyrightText: 2026 shivv23
# SPDX-License-Identifier: Apache-2.0

"""Behavioral tests for the standalone POSIX installer."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX installer")

INSTALL_SCRIPT = Path(__file__).resolve().parents[1] / "install.sh"


def _write_command_shims(tmp_path: Path) -> tuple[Path, Path]:
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    curl = shim_dir / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        'case "$*" in *checksums.txt) exit 22 ;; esac\n'
        'while [ "$#" -gt 0 ]; do\n'
        '    if [ "$1" = "-o" ]; then shift; output="$1"; fi\n'
        "    shift\n"
        "done\n"
        'printf "test-observal-binary" > "$output"\n',
        encoding="utf-8",
    )
    curl.chmod(0o755)
    sudo = shim_dir / "sudo"
    sudo.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$SUDO_MARKER"\n'
        'command_name="$1"\n'
        "shift\n"
        'if [ "$command_name" = "mkdir" ]; then\n'
        "    for target do :; done\n"
        '    chmod u+w "$(dirname "$target")"\n'
        "fi\n"
        'exec "$command_name" "$@"\n',
        encoding="utf-8",
    )
    sudo.chmod(0o755)
    return shim_dir, tmp_path / "sudo-calls"


def _run_installer(tmp_path: Path, bin_dir: Path, *, use_environment: bool) -> subprocess.CompletedProcess[str]:
    shim_dir, sudo_marker = _write_command_shims(tmp_path)
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "PATH": os.pathsep.join((str(shim_dir), os.environ.get("PATH", ""))),
        "OBSERVAL_BASE_URL": "https://example.invalid/release",
        "OBSERVAL_VERSION": "1.2.3",
        "SUDO_MARKER": str(sudo_marker),
    }
    Path(env["HOME"]).mkdir()
    args = ["--version", "1.2.3"]
    if use_environment:
        env["OBSERVAL_BIN_DIR"] = str(bin_dir)
    else:
        args.extend(("--bin-dir", str(bin_dir)))
    result = subprocess.run(
        [shutil.which("bash") or "bash", str(INSTALL_SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return result


@pytest.mark.parametrize("use_environment", [False, True], ids=["flag", "environment"])
@pytest.mark.parametrize("precreate_directory", [False, True], ids=["missing", "existing"])
def test_custom_install_directory_installs_without_sudo(tmp_path, use_environment, precreate_directory):
    bin_dir = tmp_path / "custom bin" / "nested"
    if precreate_directory:
        bin_dir.mkdir(parents=True)

    result = _run_installer(tmp_path, bin_dir, use_environment=use_environment)

    assert result.returncode == 0, result.stderr
    binary = bin_dir / "observal"
    assert binary.read_bytes() == b"test-observal-binary"
    assert os.access(binary, os.X_OK)
    metadata = json.loads((tmp_path / "home" / ".observal" / "install.json").read_text(encoding="utf-8"))
    assert metadata["path"] == str(binary)
    assert not (tmp_path / "sudo-calls").exists()


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="requires an unprivileged user")
def test_missing_privileged_install_directory_uses_sudo_to_create_it(tmp_path):
    parent = tmp_path / "protected"
    parent.mkdir()
    parent.chmod(0o555)
    bin_dir = parent / "nested"

    result = _run_installer(tmp_path, bin_dir, use_environment=False)

    assert result.returncode == 0, result.stderr
    assert (bin_dir / "observal").read_bytes() == b"test-observal-binary"
    assert (tmp_path / "sudo-calls").read_text(encoding="utf-8").splitlines() == [f"mkdir -p {bin_dir}"]


def test_install_directory_path_must_be_a_directory(tmp_path):
    bin_dir = tmp_path / "not-a-directory"
    bin_dir.write_text("file", encoding="utf-8")

    result = _run_installer(tmp_path, bin_dir, use_environment=False)

    assert result.returncode != 0
    assert f"Install path exists and is not a directory: {bin_dir}" in result.stderr
    assert not (tmp_path / "sudo-calls").exists()
