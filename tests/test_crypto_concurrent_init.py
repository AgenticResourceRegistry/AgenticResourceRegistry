# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""API workers sharing a key directory must agree on one JWT signing key.

Regression: on a fresh volume every uvicorn worker ran KeyManager.initialize()
at once, each generated its own key and the last write won, so tokens signed by
one worker failed on another ("Unknown key id") until a restart.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1] / "observal-server"
SHARED = Path(__file__).resolve().parents[1] / "packages" / "observal-shared"
WORKERS = 8

_CHILD = """
import os, sys, time
from pathlib import Path
from services.crypto import KeyManager
go = Path(sys.argv[2])
while not go.exists():
    time.sleep(0.001)
manager = KeyManager(key_dir=sys.argv[1], algorithm=sys.argv[3])
manager.initialize()
print(manager.get_kid())
"""


def _start_workers(key_dir: Path, go: Path, algorithm: str) -> list[subprocess.Popen]:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(SERVER), str(SHARED)])}
    return [
        subprocess.Popen(
            [sys.executable, "-c", _CHILD, str(key_dir), str(go), algorithm],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(WORKERS)
    ]


def _kids(workers: list[subprocess.Popen]) -> list[str]:
    kids = []
    for worker in workers:
        out, err = worker.communicate(timeout=60)
        assert worker.returncode == 0, err[-500:]
        kids.append(out.strip())
    return kids


@pytest.mark.parametrize("algorithm", ["ES256", "RS256"])
def test_workers_starting_together_on_a_fresh_key_dir_agree_on_one_key(tmp_path, algorithm):
    from services.crypto import KeyManager

    for attempt in range(3):
        key_dir, go = tmp_path / f"keys{attempt}", tmp_path / f"go{attempt}"
        workers = _start_workers(key_dir, go, algorithm)
        time.sleep(1.0)  # every worker is importing and waiting on the start signal
        go.touch()
        kids = _kids(workers)
        on_disk = KeyManager(key_dir=str(key_dir), algorithm=algorithm)
        on_disk.initialize()
        assert set(kids) == {on_disk.get_kid()}, f"attempt {attempt}: workers signed with {len(set(kids))} keys"
        assert not list(key_dir.glob("*.tmp*")), "no temporary key files left behind"


def test_workers_switching_algorithm_together_retire_one_key_and_agree_on_the_new_one(tmp_path):
    from services.crypto import KeyManager

    key_dir, go = tmp_path / "keys", tmp_path / "go"
    old = KeyManager(key_dir=str(key_dir), algorithm="ES256")
    old.initialize()
    workers = _start_workers(key_dir, go, "RS256")
    time.sleep(1.0)
    go.touch()
    kids = _kids(workers)
    current = KeyManager(key_dir=str(key_dir), algorithm="RS256")
    current.initialize()
    assert set(kids) == {current.get_kid()}
    assert current.find_public_key(old.get_kid()) is not None, "the old key still verifies"


class _FakeMsvcrt:
    """Stands in for msvcrt so the Windows lock path runs on any platform."""

    LK_LOCK, LK_NBLCK, LK_UNLCK = 1, 2, 0

    def __init__(self, error_number: int):
        self.error_number = error_number
        self.attempts = 0

    def locking(self, descriptor, mode, nbytes):
        if mode == self.LK_UNLCK:
            return
        self.attempts += 1
        raise OSError(self.error_number, os.strerror(self.error_number))


def _windows(monkeypatch, fake: _FakeMsvcrt) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "msvcrt", fake)


def test_a_persistent_windows_lock_failure_raises_at_once_instead_of_hanging(tmp_path, monkeypatch):
    import errno

    from services.crypto import _exclusive_lock

    fake = _FakeMsvcrt(errno.EBADF)
    _windows(monkeypatch, fake)
    started = time.monotonic()
    with pytest.raises(OSError) as raised, _exclusive_lock(tmp_path / ".signing.lock"):
        pass
    assert raised.value.errno == errno.EBADF and fake.attempts == 1
    assert time.monotonic() - started < 5


def test_a_windows_lock_held_past_the_timeout_raises_timeout_error(tmp_path, monkeypatch):
    import errno

    from services.crypto import _exclusive_lock

    fake = _FakeMsvcrt(errno.EACCES)
    _windows(monkeypatch, fake)
    started = time.monotonic()
    with (
        pytest.raises(TimeoutError, match=r"\.signing\.lock"),
        _exclusive_lock(tmp_path / ".signing.lock", timeout=0.3),
    ):
        pass
    assert fake.attempts > 1, "contention is retried until the deadline"
    assert 0.3 <= time.monotonic() - started < 5
