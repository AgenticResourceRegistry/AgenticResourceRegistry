# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Local archive installer retains a previous package and config before touching it."""

import os
import subprocess
import tarfile
import time
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[1] / "install-server.sh"


def _run_upgrade(tmp_path, *, fail_setup=False, old_install=True, delay_setup=False, real_setup=False, run=True):
    source = tmp_path / "package" / "server"
    source.mkdir(parents=True)
    setup = "#!/bin/bash\n"
    if delay_setup:
        setup += "sleep 0.5\n"
    setup += "exit 7\n" if fail_setup else "exit 0\n"
    (source / "setup.sh").write_bytes(
        (INSTALLER.parent / "docker/server-package/setup.sh").read_bytes() if real_setup else setup.encode()
    )
    (source / "version.txt").write_text("new package")
    archive = tmp_path / "package.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname="server")
    shims = tmp_path / "shims"
    shims.mkdir()
    curl = shims / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = "-o" ]; then shift; cp "$TEST_PACKAGE" "$1"; exit $?; fi\n'
        "  shift\n"
        "done\nexit 1\n"
    )
    curl.chmod(0o755)
    docker = shims / "docker"
    docker.write_text('#!/bin/sh\n[ "$1" = "compose" ] && [ "$2" = "version" ]\n')
    docker.chmod(0o755)
    install = tmp_path / "installed"
    if old_install:
        install.mkdir()
        (install / "version.txt").write_text("old package")
        (install / ".env").write_text("TEST_OLD_CONFIGURATION=yes\n")
        (install / "secrets").mkdir()
        (install / "secrets" / "key").write_text("old-key")
    env = {
        **os.environ,
        "PATH": f"{shims}:{os.environ['PATH']}",
        "TEST_PACKAGE": str(archive),
        "OBSERVAL_BASE_URL": "https://not-used.invalid",
    }
    command = ["bash", str(INSTALLER), "--version", "v-test", "--install-dir", str(install), "--force"]
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=False) if run else None
    return install, command, env, result


def test_package_upgrade_preserves_old_binary_env_and_secrets(tmp_path):
    install, _, _, result = _run_upgrade(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (install / "version.txt").read_text() == "new package"
    assert (install / ".env").read_text() == "TEST_OLD_CONFIGURATION=yes\n"
    assert (install / "secrets" / "key").read_text() == "old-key"
    backups = list((tmp_path / "installed.backups").iterdir())
    assert len(backups) == 1
    assert (backups[0] / "version.txt").read_text() == "old package"
    assert (backups[0] / ".env").read_text() == "TEST_OLD_CONFIGURATION=yes\n"
    assert not (tmp_path / "installed.upgrade-in-progress").exists()


def test_config_preserving_package_upgrade_reports_staged_not_deployed(tmp_path):
    install, _, _, result = _run_upgrade(tmp_path, real_setup=True)
    assert result.returncode == 0, result.stderr
    assert (install / "version.txt").read_text() == "new package"
    assert "services were NOT upgraded or migrated" in result.stdout
    assert "TEST_OLD_CONFIGURATION=yes" in (install / ".env").read_text()
    # The real setup exits before any Compose up or migration; cutover is
    # intentionally a separate supervised step after stopping old processes.
    assert "Starting Observal services" not in result.stdout


def test_first_package_install_creates_no_unnecessary_backup(tmp_path):
    install, _, _, result = _run_upgrade(tmp_path, old_install=False)
    assert result.returncode == 0, result.stderr
    assert (install / "version.txt").read_text() == "new package"
    assert not (tmp_path / "installed.backups").exists()
    assert not (tmp_path / "installed.upgrade-in-progress").exists()


def test_setup_failure_restores_old_package_and_retains_failed_bytes(tmp_path):
    install, _, _, result = _run_upgrade(tmp_path, fail_setup=True)
    assert result.returncode != 0
    assert (install / "version.txt").read_text() == "old package"
    assert (install / "secrets" / "key").read_text() == "old-key"
    assert len(list(tmp_path.glob("installed.failed-*"))) == 1
    assert len(list((tmp_path / "installed.backups").iterdir())) == 1
    assert not (tmp_path / "installed.upgrade-in-progress").exists()


def test_interrupted_first_install_requires_manual_recovery(tmp_path):
    install, command, env, result = _run_upgrade(tmp_path, fail_setup=True, old_install=False)
    assert result.returncode != 0
    assert not install.exists()
    marker = tmp_path / "installed.upgrade-in-progress"
    assert marker.exists()
    retry = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    assert retry.returncode != 0
    assert "Interrupted upgrade marker" in retry.stderr


def test_two_simultaneous_package_upgrades_cannot_both_replace_tree(tmp_path):
    install, command, env, _ = _run_upgrade(tmp_path, delay_setup=True, run=False)
    first = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    marker = tmp_path / "installed.upgrade-in-progress"
    try:
        for _ in range(100):
            if marker.exists():
                break
            time.sleep(0.01)
        assert marker.exists(), "first installer should claim the destination"
        second = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
        assert second.returncode != 0
        assert "Interrupted upgrade marker" in second.stderr
        stdout, stderr = first.communicate(timeout=10)
        assert first.returncode == 0, stderr + stdout
        assert (install / "version.txt").read_text() == "new package"
        assert len(list((tmp_path / "installed.backups").iterdir())) == 1
    finally:
        if first.poll() is None:
            first.kill()
            first.communicate(timeout=5)


@pytest.mark.parametrize("protected", [".env", "secrets"])
def test_upgrade_refuses_symlinked_configuration(tmp_path, protected):
    install = tmp_path / "installed"
    install.mkdir()
    (install / protected).symlink_to(tmp_path / "outside")
    # Preflight must refuse before any install swap even with --force.
    _, _, _, result = _run_upgrade(tmp_path, old_install=False)
    assert result.returncode != 0
    assert f"Existing {protected} is a symlink" in result.stderr
    assert (install / protected).is_symlink()
