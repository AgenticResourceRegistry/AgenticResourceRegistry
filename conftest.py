# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Repository-wide pytest fixtures.

Lives at the repo root so it applies to every suite (``tests/``,
``observal_cli/tests/``, ``observal-server/tests/``) rather than just one.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_registry_lockfile(tmp_path, monkeypatch):
    """Keep every test away from the developer's real ~/.observal/lockfile.json.

    ``observal agent pull`` records each install in the lockfile as a side
    effect, so a test that exercises a pull leaves an entry pointing at its own
    ``tmp_path`` behind. Those entries outlive the temp directory and then break
    ``observal doctor patch``, which walks the lockfile and reports every one of
    them as a missing profile instead of patching the user's real agents.
    """
    try:
        from observal_cli import lockfile
    except ImportError:  # server-only environments do not ship the CLI
        return

    lock_dir = tmp_path / "observal-lockfile"
    lock_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(lockfile, "LOCKFILE_PATH", lock_dir / "lockfile.json")
    monkeypatch.setattr(lockfile, "_LOCKFILE_LOCK", lock_dir / "lockfile.lock")


@pytest.fixture(autouse=True)
def _isolated_cli_config(tmp_path, monkeypatch):
    """Keep every test away from the developer's real ~/.observal/config.json.

    Tests that exercise login, ``config set`` or any save path rewrite the real
    file, and ``server_url`` is the field that moves. A test leaving it pointing
    somewhere else silently breaks the next CLI command and every lockfile
    lookup keyed on the registry URL, with no indication that a test did it.
    """
    try:
        from observal_cli import config
    except ImportError:  # server-only environments do not ship the CLI
        return

    config_dir = tmp_path / "observal-config"
    config_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config, "CONFIG_FILE", config_dir / "config.json")
