# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for observal_cli.upgrade_lock: an OS lock, taken and released by real processes."""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
import time

import pytest

from observal_cli import upgrade_lock
from observal_cli.shared import launcher
from observal_cli.upgrade_lock import (
    UpgradeLockError,
    acquire_lock,
    release_lock,
)


@pytest.fixture(autouse=True)
def isolated_lock_dir(tmp_path, monkeypatch):
    """Redirect lock files to tmp dir."""
    monkeypatch.setattr(upgrade_lock, "CONFIG_DIR", tmp_path)
    return tmp_path


def _contender(lock_dir, go_file, hold_seconds: float) -> subprocess.Popen:
    """A separate process that waits for ``go_file``, tries the real lock once, holds it, then releases it."""
    code = f"""
import json, os, sys, time
from pathlib import Path
from observal_cli import upgrade_lock
upgrade_lock.CONFIG_DIR = Path({str(lock_dir)!r})
go = Path({str(go_file)!r})
while not go.exists():
    time.sleep(0.001)
attempt = time.time()
try:
    lock = upgrade_lock.acquire_lock("cli")
except upgrade_lock.UpgradeLockError as error:
    print(json.dumps({{"pid": os.getpid(), "held": False, "error": str(error), "attempt": attempt}}), flush=True)
    sys.exit(0)
print(json.dumps({{"pid": os.getpid(), "held": True, "start": time.time()}}), flush=True)
time.sleep({hold_seconds})
print(json.dumps({{"pid": os.getpid(), "end": time.time()}}), flush=True)
upgrade_lock.release_lock(lock)
"""
    env = {**os.environ, "PYTHONPATH": launcher.package_root()}
    return subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, text=True)


class TestAcquireLock:
    def test_holder_is_described_in_the_lock_file(self, isolated_lock_dir):
        lock = acquire_lock("cli")
        try:
            data = json.loads(lock.path.read_text())
            assert data["pid"] == os.getpid()
            assert "timestamp" in data
        finally:
            release_lock(lock)

    def test_released_lock_can_be_taken_again(self, isolated_lock_dir):
        release_lock(acquire_lock("cli"))
        lock = acquire_lock("cli")
        release_lock(lock)
        # The file stays: deleting it would let a waiter and a newcomer lock different files.
        assert lock.path.exists()
        assert lock.path.read_text() == ""

    def test_second_acquire_while_held_fails_and_names_the_holder(self, isolated_lock_dir):
        lock = acquire_lock("cli")
        try:
            with pytest.raises(UpgradeLockError, match=rf"Another upgrade is in progress \(PID {os.getpid()}, started"):
                acquire_lock("cli")
        finally:
            release_lock(lock)

    def test_scopes_are_independent(self, isolated_lock_dir):
        cli, server = acquire_lock("cli"), acquire_lock("server")
        release_lock(cli)
        release_lock(server)


class TestLeftoverLockFiles:
    """The file's contents never lock anything; only a live holder's OS lock does."""

    @pytest.mark.parametrize(
        ("pid", "age"),
        [(None, 0), (2147483647, 3600), ("not json", 0)],
        ids=["live-pid-from-an-older-cli", "dead-pid", "corrupt"],
    )
    def test_a_leftover_file_does_not_block(self, isolated_lock_dir, pid, age):
        if pid == "not json":
            contents = "not json"
        else:
            contents = json.dumps({"pid": pid or os.getpid(), "timestamp": time.time() - age})
        (isolated_lock_dir / ".cli-upgrade.lock").write_text(contents)

        release_lock(acquire_lock("cli"))

    def test_a_holder_that_crashed_released_the_lock(self, isolated_lock_dir):
        code = (
            "import os, sys\nfrom pathlib import Path\nfrom observal_cli import upgrade_lock\n"
            f"upgrade_lock.CONFIG_DIR = Path({str(isolated_lock_dir)!r})\n"
            "upgrade_lock.acquire_lock('cli')\nos._exit(9)\n"
        )
        env = {**os.environ, "PYTHONPATH": launcher.package_root()}
        assert subprocess.run([sys.executable, "-c", code], env=env, check=False).returncode == 9
        # Its description is still in the file, but nothing holds the lock: no stale-lock wait.
        assert json.loads((isolated_lock_dir / ".cli-upgrade.lock").read_text())["pid"] != os.getpid()

        release_lock(acquire_lock("cli"))


class TestConcurrentProcesses:
    def test_processes_racing_for_the_lock_never_hold_it_together(self, isolated_lock_dir, tmp_path):
        """The audit's case: every process checked "no lock", then every one wrote one and went ahead."""
        go = tmp_path / "go"
        contenders = [_contender(isolated_lock_dir, go, hold_seconds=1.5) for _ in range(8)]
        time.sleep(2)  # every contender is started and polling
        go.touch()
        outcomes = [[json.loads(line) for line in proc.communicate(timeout=60)[0].splitlines()] for proc in contenders]

        holders = [lines for lines in outcomes if lines[0]["held"]]
        refused = [lines[0] for lines in outcomes if not lines[0]["held"]]
        assert holders and refused, "the contenders did not race"
        # Released before another contender held it: no two holding periods overlap.
        periods = sorted((lines[0]["start"], lines[1]["end"], lines[0]["pid"]) for lines in holders)
        assert all(end <= next_start for (_, end, _), (next_start, _, _) in itertools.pairwise(periods))
        # Each refusal happened while someone held the lock, and names that holder when it says a PID
        # (one refused before the holder wrote its description gets the message without one).
        for outcome in refused:
            assert outcome["error"].startswith("Another upgrade is in progress")
            if "PID" in outcome["error"]:
                end = next(end for _, end, pid in periods if f"PID {pid}," in outcome["error"])
                assert outcome["attempt"] <= end  # refused while that holder still held the lock

    def test_a_refused_process_cannot_release_the_holders_lock(self, isolated_lock_dir, tmp_path):
        """The old release deleted the file whoever held it; a refused contender now never holds anything."""
        go = tmp_path / "go"
        go.touch()
        holder = acquire_lock("cli")
        try:
            contender = _contender(isolated_lock_dir, go, hold_seconds=0)
            (outcome,) = [json.loads(line) for line in contender.communicate(timeout=60)[0].splitlines()]
            assert outcome["held"] is False
            with pytest.raises(UpgradeLockError):
                acquire_lock("cli")
        finally:
            release_lock(holder)
