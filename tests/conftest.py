# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import tempfile
from pathlib import Path

# Run every test against a throwaway home. CLI modules resolve ~/.observal and the
# harness config directories when they are imported, so a test that forgets to
# patch one must still never write the developer's real config, lockfile, or
# harness settings. Set before anything imports observal_cli.
os.environ["HOME"] = os.environ["USERPROFILE"] = tempfile.mkdtemp(prefix="observal-test-home-")

# Add server source to path so `from config import settings` works
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "observal-server"))
sys.path.insert(0, str(ROOT / "packages" / "observal-shared"))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_capability_lock(tmp_path, monkeypatch):
    """Keep every test away from the developer's real ~/.observal/capability_lock.jsonl.

    Install paths and session upload both touch the lock as a side effect, so a
    test that exercises them would otherwise leave fixture data in the real file.
    """
    from observal_cli import capability_lock

    monkeypatch.setattr(capability_lock, "LOCK_PATH", tmp_path / "capability_lock.jsonl")


AUTO_SYNC_DISABLED = "Automatic sync is disabled in tests."


@pytest.fixture(autouse=True)
def _no_cli_auto_sync(monkeypatch):
    """Stop any test from reinstalling the CLI.

    A CLI/server version mismatch makes the CLI install the server's version
    over itself and re-run the command. Tests that reach a mismatch get the
    "could not sync" outcome instead. tests/test_auto_sync.py exercises the real
    functions, which it captures at import time, with the installer mocked.
    """
    from observal_cli import auto_sync

    monkeypatch.setattr(auto_sync, "sync_to_server", lambda *_args, **_kwargs: AUTO_SYNC_DISABLED)
    monkeypatch.setattr(auto_sync, "sync_other_installs", lambda *_args, **_kwargs: None)


@pytest.fixture(autouse=True)
def _installers_through_subprocess_run(monkeypatch):
    """Tests fake package installers by patching ``subprocess.run``; route installer runs through it.

    The real ``upgrade_executor.run_installer`` detaches the installer from this
    process's signals with ``Popen``. tests/test_upgrade_executor.py exercises it
    with real processes, through the reference it captures at import time.
    """
    import subprocess

    from observal_cli import upgrade_executor

    def run_installer(argv, *, timeout, env=None):
        extra = {} if env is None else {"env": env}
        return subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, **extra
        )

    monkeypatch.setattr(upgrade_executor, "run_installer", run_installer)


@pytest.fixture
def no_open_review(monkeypatch):
    """For route tests that mock the database: no review is open for a draft save to update.

    Draft saves push a new revision to an open review (services.review.cutover). A mocked
    session cannot answer that lookup, so these tests state there is nothing to push to.
    """
    from unittest.mock import AsyncMock

    import services.review.cutover as cutover

    monkeypatch.setattr(cutover, "_draft_already_under_review", AsyncMock(return_value=None))
    for module in (
        "api.routes.mcp",
        "api.routes.hook",
        "api.routes.prompt",
        "api.routes.sandbox",
        "api.routes.skill",
        "api.routes.skill_files",
        "api.routes.agent.draft",
        "api.routes.agent.crud",
    ):
        monkeypatch.setattr(f"{module}.push_draft_edit", AsyncMock(return_value=None))
