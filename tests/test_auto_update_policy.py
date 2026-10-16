# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Freeze/unfreeze policy contracts; account consent gates Pi apply."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from observal_cli import auto_update_policy as policy
from observal_cli import config
from observal_cli.cmd_freeze import register_freeze
from observal_cli.errors import ErrorHandlingGroup, ExitCode

REGISTRY = "https://example.test"


@pytest.fixture()
def isolated_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(policy, "POLICY_PATH", tmp_path / "auto-update-policy.json")
    monkeypatch.setattr(policy, "GATE_DIR", tmp_path / "gates")
    credentials = {"server_url": REGISTRY + "/", "user_id": "alice", "access_token": "alice-token"}
    monkeypatch.setattr(config, "load", lambda: dict(credentials))
    monkeypatch.setattr(config, "load_persisted", lambda: dict(credentials))
    return tmp_path


@pytest.fixture()
def cli() -> typer.Typer:
    app = typer.Typer(name="observal", cls=ErrorHandlingGroup)

    @app.callback()
    def root() -> None:
        pass

    register_freeze(app)
    return app


def call(cli: typer.Typer, *args: str) -> tuple[int, dict]:
    result = CliRunner().invoke(cli, [*args, "--output", "json"])
    payload = json.loads(result.stdout) if result.stdout else json.loads(result.stderr)
    return result.exit_code, payload


def test_default_frozen_and_independent_registries(isolated_policy: Path, cli: typer.Typer) -> None:
    assert policy.policy_status(REGISTRY)["effective"] is False
    code, enabled = call(cli, "unfreeze")
    assert code == 0
    assert enabled == {
        "registry": REGISTRY,
        "scope": "user",
        "project": None,
        "auto_update": True,
        "effective": True,
    }
    assert policy.policy_status("https://elsewhere.test")["effective"] is False
    assert call(cli, "unfreeze") == (0, enabled)  # idempotent
    text = CliRunner().invoke(cli, ["unfreeze"]).output
    assert "can now be updated at interactive startup" in text and "observal freeze" in text
    code, frozen = call(cli, "freeze")
    assert code == 0
    assert frozen["effective"] is False
    assert policy.policy_status(REGISTRY)["effective"] is False
    assert policy.POLICY_PATH.stat().st_mode & 0o077 == 0


def test_project_grant_requires_global_opt_in_and_is_exact(isolated_policy: Path, cli: typer.Typer) -> None:
    root = isolated_policy / "project"
    nested = root / "nested"
    nested.mkdir(parents=True)
    code, granted = call(cli, "unfreeze", "--project", "--dir", str(root))
    assert code == 0
    assert granted["auto_update"] is True
    assert granted["effective"] is False
    assert policy.policy_status(REGISTRY, root=str(nested))["effective"] is False
    assert call(cli, "unfreeze")[0] == 0
    assert policy.policy_status(REGISTRY, root=str(root))["effective"] is True
    assert policy.policy_status(REGISTRY, root=str(nested))["effective"] is False
    assert call(cli, "freeze")[0] == 0
    assert policy.policy_status(REGISTRY, root=str(root))["auto_update"] is True
    assert policy.policy_status(REGISTRY, root=str(root))["effective"] is False
    assert call(cli, "unfreeze")[0] == 0
    assert call(cli, "freeze", "--project", "--dir", str(root))[1]["effective"] is False
    assert policy.policy_status(REGISTRY)["effective"] is True


def test_invalid_dir_and_malformed_policy_fail_closed(isolated_policy: Path, cli: typer.Typer) -> None:
    assert call(cli, "unfreeze", "--dir", str(isolated_policy))[0] == ExitCode.VALIDATION
    assert call(cli, "unfreeze", "--project", "--dir", str(isolated_policy / "missing"))[0] == ExitCode.VALIDATION
    policy.POLICY_PATH.write_text("{not json")
    assert policy.policy_status(REGISTRY)["effective"] is False
    assert "warning" in policy.policy_status(REGISTRY)
    code, error = call(cli, "unfreeze")
    assert code == ExitCode.VALIDATION
    assert error["error"]["category"] == "validation"
    assert policy.POLICY_PATH.read_text() == "{not json"


def test_gate_prevents_freeze_from_returning_before_inflight_install(
    isolated_policy: Path,
) -> None:
    policy.set_policy(REGISTRY, enabled=True)
    entered = threading.Event()
    finish = threading.Event()
    finished = threading.Event()

    def simulated_install() -> None:
        with policy.registry_gate(REGISTRY):
            assert policy.policy_status(REGISTRY)["effective"] is True
            entered.set()
            assert finish.wait(timeout=5)
        finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        installing = executor.submit(simulated_install)
        assert entered.wait(timeout=5)
        with pytest.raises(policy.GateBusyError):
            policy.set_policy(REGISTRY, enabled=False, timeout=0.05)
        assert policy.policy_status(REGISTRY)["effective"] is True
        freezing = executor.submit(policy.set_policy, REGISTRY, enabled=False)
        time.sleep(0.08)
        assert not freezing.done()
        finish.set()
        installing.result(timeout=5)
        assert finished.is_set()
        assert freezing.result(timeout=5)["effective"] is False
    assert policy.policy_status(REGISTRY)["effective"] is False


_HOLD_GATE = """
import sys
import time
from pathlib import Path
from observal_cli import auto_update_policy as policy
policy.GATE_DIR = Path(sys.argv[1])
entered, release = Path(sys.argv[2]), Path(sys.argv[3])
gate = policy.apply_worker_gate(sys.argv[4], 'alice') if sys.argv[5] == 'apply' else policy.registry_gate(sys.argv[4])
with gate:
    entered.touch()
    deadline = time.monotonic() + 5
    while not release.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
"""


@contextmanager
def _held_process_gate(root: Path, kind: str):
    # An inline child exercises the real interprocess lock without pickling a
    # test-module function. CI invokes pytest from observal-server/, where
    # tests.test_auto_update_policy is not importable by a spawned child.
    entered, release = root / "entered", root / "release"
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, (repo, os.environ.get("PYTHONPATH"))))}
    child = subprocess.Popen(
        [sys.executable, "-c", _HOLD_GATE, str(policy.GATE_DIR), str(entered), str(release), REGISTRY, kind],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        deadline = time.monotonic() + 5
        while not entered.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert entered.exists(), child.stderr.read() if child.poll() is not None else "Gate child did not start"
        yield
    finally:
        release.touch()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.terminate()
            child.wait(timeout=5)
        assert child.returncode == 0, child.stderr.read()
        child.stderr.close()


def test_apply_worker_gate_is_account_scoped_and_cross_process(isolated_policy: Path) -> None:
    with _held_process_gate(isolated_policy, "apply"):
        with pytest.raises(policy.GateBusyError), policy.apply_worker_gate(REGISTRY, "alice", timeout=0.05):
            pytest.fail("two workers for the same account passed the admission gate")
        with policy.apply_worker_gate(REGISTRY, "bob", timeout=0.05):
            pass
        # Freeze takes only the policy gate; it must not wait for the outer
        # worker gate (an installer holding both is covered elsewhere).
        with policy.registry_gate(REGISTRY, timeout=0.05):
            pass
    with policy.apply_worker_gate(REGISTRY, "alice", timeout=0.05):
        pass


def test_gate_is_shared_across_processes(isolated_policy: Path) -> None:
    with _held_process_gate(isolated_policy, "registry"), pytest.raises(policy.GateBusyError):
        policy.set_policy(REGISTRY, enabled=True, timeout=0.05)
    assert policy.set_policy(REGISTRY, enabled=True)["effective"] is True


def test_account_switch_token_override_and_logout_fail_closed(
    isolated_policy: Path, cli: typer.Typer, monkeypatch: pytest.MonkeyPatch
) -> None:
    accounts = {"server_url": REGISTRY, "user_id": "alice", "access_token": "alice-token"}
    monkeypatch.setattr(config, "load_persisted", lambda: dict(accounts))
    monkeypatch.setattr(config, "load", lambda: dict(accounts))
    assert call(cli, "unfreeze")[1]["effective"] is True
    accounts.update(user_id="bob", access_token="bob-token")
    assert policy.policy_status(REGISTRY)["effective"] is False
    assert call(cli, "unfreeze")[1]["effective"] is True
    accounts.update(user_id="alice", access_token="alice-token")
    assert policy.policy_status(REGISTRY)["effective"] is True
    monkeypatch.setattr(config, "load", lambda: {**accounts, "access_token": "other-account-token"})
    assert policy.policy_status(REGISTRY)["effective"] is False
    assert call(cli, "unfreeze")[0] == ExitCode.AUTH
    accounts.pop("access_token")
    monkeypatch.setattr(config, "load", lambda: dict(accounts))
    assert policy.policy_status(REGISTRY)["effective"] is False


def test_legacy_unscoped_consent_is_discarded(isolated_policy: Path, cli: typer.Typer) -> None:
    policy.POLICY_PATH.write_text(
        json.dumps(
            {
                "version": 1,
                "registries": {REGISTRY: {"enabled": True, "projects": {str(isolated_policy): True}}},
            }
        )
    )
    assert policy.policy_status(REGISTRY)["effective"] is False
    assert "Legacy" in policy.policy_status(REGISTRY)["warning"]
    assert call(cli, "freeze")[1]["effective"] is False
    assert json.loads(policy.POLICY_PATH.read_text())["version"] == 2
    assert call(cli, "unfreeze")[1]["effective"] is True
    assert policy.policy_status(REGISTRY, root=str(isolated_policy))["effective"] is False


def test_help_and_missing_registry(isolated_policy: Path, cli: typer.Typer, monkeypatch: pytest.MonkeyPatch) -> None:
    assert "observal freeze" in CliRunner().invoke(cli, ["freeze", "--help"]).output
    assert "observal unfreeze" in CliRunner().invoke(cli, ["unfreeze", "--help"]).output
    monkeypatch.setattr(config, "load", lambda: {"server_url": ""})
    assert call(cli, "freeze")[0] == ExitCode.AUTH
