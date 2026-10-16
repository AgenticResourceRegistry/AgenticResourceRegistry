# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Gated Pi apply worker lifetime and durable notice contract."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from observal_cli import auto_update_policy, client, cmd_update, installed_updates
from observal_cli import startup_update_apply as worker
from observal_cli import startup_update_check as check

REGISTRY = "https://registry.example"
KEY = worker.expected_notice_key(REGISTRY, "alice", "session-a")


@pytest.fixture()
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr(worker, "SHUTDOWN_DIR", tmp_path / "shutdown")
    monkeypatch.setattr(auto_update_policy, "GATE_DIR", tmp_path / "gates")
    monkeypatch.setattr(check, "NOTICE_DIR", tmp_path / "notices")
    monkeypatch.setattr(check, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(auto_update_policy, "active_registry", lambda: REGISTRY)
    monkeypatch.setattr(auto_update_policy, "active_account", lambda: "alice")
    monkeypatch.setattr(auto_update_policy, "policy_status", lambda _: {"effective": True})
    entry = {
        "id": "agent-id",
        "type": "agent",
        "scope": "user",
        "harness": "pi",
        "directory": str(tmp_path),
        "current_version": "1.0",
        "lock_digest": "digest",
    }
    finding = {
        **entry,
        "qualified_name": "alice/code",
        "latest_version": "2.0",
        "outdated": True,
        "release_verified": True,
        "status": "outdated",
        "release": {"description": "Author's notes", "changelog": "Release notes"},
    }
    monkeypatch.setattr(installed_updates, "inventory_for_context", lambda *args: [entry])
    monkeypatch.setattr(installed_updates, "compare", lambda *args, **kwargs: [finding])
    apply = MagicMock(return_value={"status": "updated", "reason": "Saved profile updated."})
    monkeypatch.setattr(cmd_update, "apply_startup_pi_agent", apply)
    return {"tmp": tmp_path, "apply": apply}


def result() -> dict:
    return json.loads((check.NOTICE_DIR / f"{KEY}.json").read_text())


def test_two_sessions_first_unsealed_result_blocks_second_before_admission(
    setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_inside = threading.Event()
    allow_first_to_finish = threading.Event()
    checks: list[str] = []
    scan = worker._unresolved_pending

    def tracked_scan(registry: str, account: str) -> bool:
        checks.append(threading.current_thread().name)
        return scan(registry, account)

    def install(*_args: object, **_kwargs: object) -> dict:
        first_inside.set()
        assert allow_first_to_finish.wait(timeout=5)
        (setup["tmp"] / "AGENTS.md").write_text("first worker updated files")
        return {"status": "updated"}

    monkeypatch.setattr(worker, "_unresolved_pending", tracked_scan)
    setup["apply"].side_effect = install
    write = check._write_json

    def fail_first_final(path: Path, data: object, limit: int) -> None:
        if path == check.NOTICE_DIR / f"{KEY}.json":
            raise OSError("first result cannot be sealed")
        write(path, data, limit)

    monkeypatch.setattr(check, "_write_json", fail_first_final)
    second_key = worker.expected_notice_key(REGISTRY, "alice", "session-b")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker.apply_pi, str(setup["tmp"]), "session-a", KEY)
        assert first_inside.wait(timeout=5)
        second = pool.submit(worker.apply_pi, str(setup["tmp"]), "session-b", second_key)
        try:
            time.sleep(0.1)
            assert not second.done(), "second session must wait through first worker's final outcome"
            assert len(checks) == 1, "second session must not inspect the journal before the worker gate"
        finally:
            allow_first_to_finish.set()
        with pytest.raises(OSError, match="cannot be sealed"):
            first.result(timeout=5)
        second.result(timeout=5)
    setup["apply"].assert_called_once()
    assert (setup["tmp"] / "AGENTS.md").read_text() == "first worker updated files"
    assert (check.NOTICE_DIR / f"{KEY}.pending").exists()
    second_notice = json.loads((check.NOTICE_DIR / f"{second_key}.json").read_text())
    assert second_notice["items"][0]["status"] == "skipped"
    assert "unresolved" in second_notice["items"][0]["reason"]


def test_apply_worker_scopes_network_budget_before_recovery_window(setup: dict) -> None:
    seen: list[float] = []

    def check_cutoff(*_args: object, **_kwargs: object) -> dict:
        cutoff = client._NETWORK_CUTOFF.get()
        assert cutoff is not None
        seen.append(cutoff - time.monotonic())
        return {"status": "updated"}

    setup["apply"].side_effect = check_cutoff
    worker.apply_pi(str(setup["tmp"]), "session-a", KEY)
    assert len(seen) == 1 and 0 < seen[0] <= worker.APPLY_SECONDS - worker.RECOVERY_RESERVE_SECONDS
    assert client._NETWORK_CUTOFF.get() is None


def test_cannot_reserve_spool_refuses_install(setup: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*_args: object) -> None:
        raise OSError("disk full; do not expose this detail")

    monkeypatch.setattr(worker, "_reserve_pending", unavailable)
    worker.apply_pi(str(setup["tmp"]), "session-a", KEY)
    setup["apply"].assert_not_called()
    assert result()["items"][0]["status"] == "skipped"
    assert "no installation was started" in result()["items"][0]["reason"]
    assert not list(check.NOTICE_DIR.glob("*.pending"))


def test_final_spool_failure_leaves_durable_uncertain_journal(setup: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    target = setup["tmp"] / "AGENTS.md"

    def mutate(*_args: object, **_kwargs: object) -> dict:
        pending = check.NOTICE_DIR / f"{KEY}.pending"
        assert json.loads(pending.read_text())["state"] == "pending"
        assert pending.stat().st_mode & 0o777 == 0o600
        target.write_text("installed on disk")
        return {"status": "updated"}

    setup["apply"].side_effect = mutate
    write = check._write_json

    def disk_full(path: Path, data: object, limit: int) -> None:
        if path == check.NOTICE_DIR / f"{KEY}.json":
            raise OSError("outcome spool became unwritable")
        write(path, data, limit)

    monkeypatch.setattr(check, "_write_json", disk_full)
    with pytest.raises(OSError, match="unwritable"):
        worker.apply_pi(str(setup["tmp"]), "session-a", KEY)
    assert target.read_text() == "installed on disk"
    pending = json.loads((check.NOTICE_DIR / f"{KEY}.pending").read_text())
    assert pending["item"]["latest_version"] == "2.0"
    assert not (check.NOTICE_DIR / f"{KEY}.complete").exists()
    assert not (check.NOTICE_DIR / f"{KEY}.json").exists()
    setup["apply"].reset_mock()
    monkeypatch.setattr(check, "_write_json", write)
    worker.apply_pi(str(setup["tmp"]), "session-a", KEY)
    setup["apply"].assert_not_called()
    assert json.loads((check.NOTICE_DIR / f"{KEY}.pending").read_text()) == pending
    assert not (check.NOTICE_DIR / f"{KEY}.json").exists(), "retry must not replace an unresolved outcome"


def test_final_directory_sync_failure_keeps_journal_and_no_seal(setup: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    sync = check._sync_directory

    def fail_after_final_rename(directory: Path) -> None:
        if directory == check.NOTICE_DIR and (directory / f"{KEY}.json").exists():
            raise OSError("directory sync failed after final rename")
        sync(directory)

    monkeypatch.setattr(check, "_sync_directory", fail_after_final_rename)
    with pytest.raises(OSError, match="sync failed"):
        worker.apply_pi(str(setup["tmp"]), "session-a", KEY)
    setup["apply"].assert_called_once()
    assert (check.NOTICE_DIR / f"{KEY}.pending").exists()
    assert (check.NOTICE_DIR / f"{KEY}.json").exists()
    assert not (check.NOTICE_DIR / f"{KEY}.complete").exists(), "an unsealed result must not be delivered as final"


def test_completion_seal_failure_does_not_clear_pending(setup: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    reserve = worker._reserve_pending

    def fail_seal(path: Path, payload: dict) -> None:
        if path.suffix == ".complete":
            raise OSError("disk full after final outcome")
        reserve(path, payload)

    monkeypatch.setattr(worker, "_reserve_pending", fail_seal)
    with pytest.raises(OSError, match="disk full"):
        worker.apply_pi(str(setup["tmp"]), "session-a", KEY)
    setup["apply"].assert_called_once()
    assert result()["items"][0]["status"] == "updated"
    assert result()["journaled"] is True
    assert (check.NOTICE_DIR / f"{KEY}.pending").exists()
    assert not (check.NOTICE_DIR / f"{KEY}.complete").exists()


def test_unfsynced_completion_seal_is_removed(setup: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    sync = check._sync_directory

    def fail_seal_directory_sync(directory: Path) -> None:
        if directory == check.NOTICE_DIR and (directory / f"{KEY}.complete").exists():
            raise OSError("completion seal was not durable")
        sync(directory)

    monkeypatch.setattr(check, "_sync_directory", fail_seal_directory_sync)
    with pytest.raises(OSError, match="not durable"):
        worker.apply_pi(str(setup["tmp"]), "session-a", KEY)
    setup["apply"].assert_called_once()
    assert (check.NOTICE_DIR / f"{KEY}.pending").exists()
    assert not (check.NOTICE_DIR / f"{KEY}.complete").exists()


def test_pending_unlink_failure_keeps_sealed_result_for_replay(setup: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    original = Path.unlink

    def deny_pending(path: Path, *args: object, **kwargs: object) -> None:
        if path == check.NOTICE_DIR / f"{KEY}.pending":
            raise OSError("pending directory became unwritable")
        original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_pending)
    with pytest.raises(OSError, match="unwritable"):
        worker.apply_pi(str(setup["tmp"]), "session-a", KEY)
    assert result()["items"][0]["status"] == "updated"
    assert (check.NOTICE_DIR / f"{KEY}.complete").exists()
    assert (check.NOTICE_DIR / f"{KEY}.pending").exists()


def test_verified_success_is_durable_and_not_active(setup: dict) -> None:
    worker.apply_pi(str(setup["tmp"]), "session-a", KEY)
    notice = result()
    assert notice["items"][0]["status"] == "updated"
    assert "Re-select the agent with `/agent`" in notice["items"][0]["reason"]
    assert notice["effective_in_current_session"] == "no"
    assert (check.NOTICE_DIR / f"{KEY}.json").stat().st_mode & 0o777 == 0o600
    assert notice["journaled"] is True and notice["outcome_final"] is True
    assert (check.NOTICE_DIR / f"{KEY}.complete").exists()
    assert not (check.NOTICE_DIR / f"{KEY}.pending").exists()
    assert setup["apply"].call_args.kwargs["shutdown_requested"]() is False
    assert setup["apply"].call_args.args[0]["lock_digest"] == "digest"


def test_other_valid_key_cannot_bypass_shutdown_marker(setup: dict) -> None:
    worker.SHUTDOWN_DIR.mkdir(mode=0o700)
    worker.shutdown_marker(KEY).write_text("{}")
    wrong = "b" * 64
    assert wrong != KEY
    with pytest.raises(ValueError, match="does not match"):
        worker.apply_pi(str(setup["tmp"]), "session-a", wrong)
    setup["apply"].assert_not_called()
    assert not (check.NOTICE_DIR / f"{wrong}.json").exists()


def test_session_or_identity_cannot_reuse_key(setup: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="does not match"):
        worker.apply_pi(str(setup["tmp"]), "session-b", KEY)
    monkeypatch.setattr(auto_update_policy, "active_account", lambda: "bob")
    with pytest.raises(ValueError, match="does not match"):
        worker.apply_pi(str(setup["tmp"]), "session-a", KEY)
    setup["apply"].assert_not_called()


def test_shutdown_prevents_new_install_and_still_delivers_notice(setup: dict) -> None:
    worker.SHUTDOWN_DIR.mkdir(mode=0o700)
    worker.shutdown_marker(KEY).write_text("{}")
    worker.apply_pi(str(setup["tmp"]), "session-a", KEY)
    setup["apply"].assert_not_called()
    assert result()["items"][0]["status"] == "skipped"


def test_prior_session_uncertain_outcome_blocks_new_auto_installs(setup: dict) -> None:
    earlier = worker.expected_notice_key(REGISTRY, "alice", "session-before")
    previous = check.NOTICE_DIR / f"{earlier}.pending"
    worker._reserve_pending(
        previous,
        {
            "schema": 1,
            "state": "pending",
            "registry": REGISTRY,
            "account_id": "alice",
            "session_id": "session-before",
            "item": {"name": "alice/code"},
        },
    )
    worker.apply_pi(str(setup["tmp"]), "session-a", KEY)
    setup["apply"].assert_not_called()
    assert previous.exists()
    assert "unresolved" in result()["warning"]
    assert result()["outcome_final"] is False


def test_opted_in_apply_ignores_a_pre_release_check_only_cache(setup: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = installed_updates.inventory_for_context("pi", str(setup["tmp"]))[0]
    latest = "1.0"
    calls: list[bool] = []

    def compare(entries: list[dict], *, verify_releases: bool = False) -> list[dict]:
        assert entries == [entry]
        calls.append(verify_releases)
        if latest == "1.0":
            return [{**entry, "latest_version": latest, "outdated": False, "status": "current"}]
        return [
            {
                **entry,
                "qualified_name": "alice/code",
                "latest_version": latest,
                "outdated": True,
                "release_verified": True,
                "status": "outdated",
                "release": {"description": "newly approved"},
            }
        ]

    monkeypatch.setattr(installed_updates, "compare", compare)
    check.check_pi(str(setup["tmp"]), "session-before-release", "a" * 64)
    cache = next(check.CACHE_DIR.glob("*.json"))
    assert json.loads(cache.read_text())[0]["latest_version"] == "1.0"

    latest = "2.0"  # The registry approves a new release after that cached check.
    worker.apply_pi(str(setup["tmp"]), "session-a", KEY)
    assert calls == [True, True], "apply must fetch live instead of using the stale check-only cache"
    setup["apply"].assert_called_once()
    assert setup["apply"].call_args.args[0]["latest_version"] == "2.0"
    assert result()["items"][0]["status"] == "updated"
    assert json.loads(cache.read_text())[0]["latest_version"] == "1.0", "apply need not modify notice-only cache"


def test_failed_fresh_check_never_uses_stale_cache_for_install(setup: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    check.check_pi(str(setup["tmp"]), "before-outage", "a" * 64)
    assert next(check.CACHE_DIR.glob("*.json")).exists()
    monkeypatch.setattr(installed_updates, "compare", lambda *_, **__: (_ for _ in ()).throw(ValueError("secret")))
    worker.apply_pi(str(setup["tmp"]), "session-a", KEY)
    setup["apply"].assert_not_called()
    assert "automatic update skipped" in result()["warning"]
    assert "secret" not in json.dumps(result())
    assert not list(check.NOTICE_DIR.glob("*.pending"))


def test_frozen_is_notice_only(setup: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_update_policy, "policy_status", lambda _: {"effective": False})
    worker.apply_pi(str(setup["tmp"]), "session-a", KEY)
    setup["apply"].assert_not_called()
    assert "frozen" in result()["items"][0]["reason"]


def test_failed_installer_keeps_pending_record_without_claiming_rollback(setup: dict) -> None:
    setup["apply"].return_value = {"status": "failed", "reason": "Install may have changed managed files."}
    worker.apply_pi(str(setup["tmp"]), "session-a", KEY)
    text = (check.NOTICE_DIR / f"{KEY}.json").read_text()
    assert "secret" not in text
    item = result()["items"][0]
    assert item["status"] == "failed" and "may have changed" in item["reason"]
    assert result()["outcome_final"] is False
    assert not (check.NOTICE_DIR / f"{KEY}.complete").exists()
    pending = json.loads((check.NOTICE_DIR / f"{KEY}.pending").read_text())
    assert pending["item"]["latest_version"] == "2.0"
    later = worker.expected_notice_key(REGISTRY, "alice", "session-later")
    worker.apply_pi(str(setup["tmp"]), "session-later", later)
    setup["apply"].assert_called_once()
    assert "unresolved" in json.loads((check.NOTICE_DIR / f"{later}.json").read_text())["warning"]


def test_unexpected_error_after_admission_stays_unsealed(setup: dict) -> None:
    def unknown(*_args: object, **_kwargs: object) -> dict:
        raise RuntimeError("unverified worker error")

    setup["apply"].side_effect = unknown
    worker.apply_pi(str(setup["tmp"]), "session-a", KEY)
    assert result()["items"][0]["status"] == "failed"
    assert result()["outcome_final"] is False
    assert (check.NOTICE_DIR / f"{KEY}.pending").exists()
    assert not (check.NOTICE_DIR / f"{KEY}.complete").exists()


def test_oversized_failed_result_retains_pending_record(setup: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check, "MAX_NOTICE_BYTES", 900)
    monkeypatch.setattr(
        installed_updates,
        "compare",
        lambda *_, **__: [
            {
                "id": "agent-id",
                "type": "agent",
                "scope": "user",
                "harness": "pi",
                "directory": str(setup["tmp"]),
                "current_version": "1.0",
                "latest_version": "2.0",
                "outdated": True,
                "release_verified": True,
                "status": "outdated",
                "release": {"description": "x" * 600, "changelog": "y" * 600},
            }
        ],
    )
    setup["apply"].return_value = {"status": "failed", "reason": "Install may have changed managed files."}
    worker.apply_pi(str(setup["tmp"]), "session-a", KEY)
    notice = result()
    assert notice["items"][0]["status"] == "failed"
    assert notice["items"][0]["status"] == "failed"
    assert "notice limit" in notice["warning"]
    assert (check.NOTICE_DIR / f"{KEY}.pending").exists()
    assert not (check.NOTICE_DIR / f"{KEY}.complete").exists()


def test_expired_admission_never_calls_installer(setup: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    real_monotonic = time.monotonic
    calls = 0

    def clock() -> float:
        nonlocal calls
        calls += 1
        return real_monotonic() + (100 if calls > 1 else 0)

    monkeypatch.setattr(worker.time, "monotonic", clock)
    worker.apply_pi(str(setup["tmp"]), "session-a", KEY)
    setup["apply"].assert_not_called()
    assert result()["items"][0]["status"] == "skipped"
