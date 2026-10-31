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


def _run_upgrade(
    tmp_path, *, fail_setup=False, old_install=True, delay_setup=False, real_setup=False, setup_script=None, run=True
):
    source = tmp_path / "package" / "server"
    source.mkdir(parents=True)
    setup = "#!/bin/bash\n"
    if delay_setup:
        setup += "sleep 0.5\n"
    setup += "exit 7\n" if fail_setup else "exit 0\n"
    (source / "setup.sh").write_bytes(
        (INSTALLER.parent / "docker/server-package/setup.sh").read_bytes()
        if real_setup
        else (setup_script or setup).encode()
    )
    (source / "version.txt").write_text("new package")
    (source / "clickhouse/users.d").mkdir(parents=True)
    (source / "clickhouse/users.d/memory.xml").write_text("new memory settings")
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
    docker.write_text(
        '#!/bin/sh\nif [ "$1" = "compose" ] && [ "$2" = "version" ]; then exit 0; fi\n'
        'printf "%s\\n" "$*" >> "$TEST_DOCKER_CALLS"\n'
    )
    docker.chmod(0o755)
    install = tmp_path / "installed"
    if old_install:
        install.mkdir()
        (install / "version.txt").write_text("old package")
        (install / ".env").write_text("TEST_OLD_CONFIGURATION=yes\n")
        (install / "secrets").mkdir()
        (install / "secrets" / "key").write_text("old-key")
        (install / "clickhouse/users.d").mkdir(parents=True)
        (install / "clickhouse/users.d/generated-password.xml").write_text("old password hash")
    env = {
        **os.environ,
        "PATH": f"{shims}:{os.environ['PATH']}",
        "TEST_PACKAGE": str(archive),
        "TEST_DOCKER_CALLS": str(tmp_path / "docker-calls"),
        "TEST_SETUP_CALLED": str(tmp_path / "setup-called"),
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
    assert (install / "clickhouse/users.d/generated-password.xml").read_text() == "old password hash"
    assert (install / "clickhouse/users.d/memory.xml").read_text() == "new memory settings"
    backups = list((tmp_path / "installed.backups").iterdir())
    assert len(backups) == 1
    assert (backups[0] / "version.txt").read_text() == "old package"
    assert (backups[0] / ".env").read_text() == "TEST_OLD_CONFIGURATION=yes\n"
    assert not (tmp_path / "installed.upgrade-in-progress").exists()
    assert "Only .env, secrets, and generated ClickHouse XML" in result.stdout
    assert "inspect and reapply" in result.stdout


def test_config_preserving_package_upgrade_reports_staged_not_deployed(tmp_path):
    install, _, _, result = _run_upgrade(tmp_path, real_setup=True)
    assert result.returncode == 0, result.stderr
    assert (install / "version.txt").read_text() == "new package"
    assert "services were NOT upgraded or migrated" in result.stdout
    assert (install / ".env").read_text() == "TEST_OLD_CONFIGURATION=yes\n"
    assert "OBSERVAL_BIND_ADDRESS" not in (install / ".env").read_text()
    assert not (tmp_path / "docker-calls").exists()


def test_old_archive_setup_cannot_rewrite_env_or_start_docker(tmp_path):
    # Published archives predate the setup guard: answering Yes there would
    # rewrite the preserved config and start Compose, even with no terminal.
    old_setup = (
        "#!/bin/bash\n"
        'printf "OBSERVAL_BIND_ADDRESS=0.0.0.0\\n" >> "$OBSERVAL_INSTALL_DIR/.env"\n'
        "docker compose up -d\n"
        'touch "$TEST_SETUP_CALLED"\n'
    )
    install, _, _, result = _run_upgrade(tmp_path, setup_script=old_setup)
    assert result.returncode == 0, result.stderr
    assert (install / "version.txt").read_text() == "new package"
    assert (install / ".env").read_text() == "TEST_OLD_CONFIGURATION=yes\n"
    assert "OBSERVAL_BIND_ADDRESS" not in (install / ".env").read_text()
    assert (install / "secrets" / "key").read_text() == "old-key"
    assert not (tmp_path / "setup-called").exists()
    assert not (tmp_path / "docker-calls").exists()
    assert "services were NOT upgraded or migrated" in result.stdout


@pytest.mark.parametrize("force", [False, True])
def test_nonempty_install_without_env_refuses_old_setup_before_swap(tmp_path, force):
    # Older published setup scripts unconditionally start Compose. A pre-existing
    # tree without .env must not be interpreted as an unconfigured first install.
    old_setup = (
        "#!/bin/bash\n"
        'printf "OBSERVAL_BIND_ADDRESS=0.0.0.0\\n" >> "$OBSERVAL_INSTALL_DIR/.env"\n'
        "docker compose up -d\n"
        'touch "$TEST_SETUP_CALLED"\n'
    )
    install, command, env, _ = _run_upgrade(tmp_path, old_install=False, setup_script=old_setup, run=False)
    install.mkdir()
    (install / "version.txt").write_text("old package")
    if not force:
        command.remove("--force")
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "nonempty" in result.stderr
    assert ".env" in result.stderr
    assert "Back up and replace?" not in result.stdout
    assert "Downloading server package" not in result.stdout
    assert (install / "version.txt").read_text() == "old package"
    assert not (install / ".env").exists()
    assert not (tmp_path / "setup-called").exists()
    assert not (tmp_path / "docker-calls").exists()
    assert not (tmp_path / "installed.backups").exists()
    assert not (tmp_path / "installed.upgrade-in-progress").exists()
    assert not list(tmp_path.glob("installed.stage.*"))


def test_upgrade_warns_custom_config_is_only_in_backup(tmp_path):
    install, command, env, _ = _run_upgrade(tmp_path, run=False)
    (install / "nginx.conf").write_text("old custom nginx config")
    (install / "clickhouse/users.d/custom.xml").write_text("old custom ClickHouse user")
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert not (install / "nginx.conf").exists()
    assert not (install / "clickhouse/users.d/custom.xml").exists()
    backups = list((tmp_path / "installed.backups").iterdir())
    assert len(backups) == 1
    assert (backups[0] / "nginx.conf").read_text() == "old custom nginx config"
    assert (backups[0] / "clickhouse/users.d/custom.xml").read_text() == "old custom ClickHouse user"
    assert result.stdout.count("Only .env, secrets, and generated ClickHouse XML") == 2
    assert result.stdout.count("inspect and reapply") == 2
    assert result.stdout.index("inspect and reapply") < result.stdout.index("Unpacking to staging directory")
    assert result.stdout.rindex("inspect and reapply") > result.stdout.index(
        "Previous package and configuration retained"
    )
    assert "before cutover" in result.stdout
    assert not (tmp_path / "docker-calls").exists()


def test_first_package_install_creates_no_unnecessary_backup(tmp_path):
    install, _, _, result = _run_upgrade(tmp_path, old_install=False)
    assert result.returncode == 0, result.stderr
    assert (install / "version.txt").read_text() == "new package"
    assert not (tmp_path / "installed.backups").exists()
    assert not (tmp_path / "installed.upgrade-in-progress").exists()


def test_empty_existing_directory_allows_first_install(tmp_path):
    install, command, env, _ = _run_upgrade(tmp_path, old_install=False, run=False)
    install.mkdir()
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert (install / "version.txt").read_text() == "new package"
    assert not (tmp_path / "installed.upgrade-in-progress").exists()


def test_first_install_setup_failure_retains_failed_bytes(tmp_path):
    install, _, _, result = _run_upgrade(tmp_path, fail_setup=True, old_install=False)
    assert result.returncode != 0
    assert not install.exists()
    failed = list(tmp_path.glob("installed.failed-*"))
    assert len(failed) == 1
    assert (failed[0] / "version.txt").read_text() == "new package"
    assert (tmp_path / "installed.upgrade-in-progress").exists()


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
    install, command, env, _ = _run_upgrade(tmp_path, delay_setup=True, old_install=False, run=False)
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
        assert not (tmp_path / "installed.backups").exists()
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
