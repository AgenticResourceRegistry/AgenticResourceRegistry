# SPDX-FileCopyrightText: 2026 Dheirav Prakash <dheirav2005@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the Claude Code session helpers behind the session push hook.

The hook (``observal_cli/hooks/session_push.py``) now delegates to
``observal_cli.sessions``. Cursor state, config loading, record reading and
payload building are covered in ``test_session_delivery.py``; this file covers
the helpers that had no tests: locating the session JSONL, detecting subagent
sessions, and attributing a session to a pulled agent.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest

from observal_cli.hooks import session_push
from observal_cli.sessions import base
from observal_cli.sessions.agent_marker import read_agent_marker
from observal_cli.sessions.claude_code import find_jsonl_file, get_parent_session_id, project_key_from_cwd


def test_recovery_pacer_stays_below_60_batches_per_minute(monkeypatch, tmp_path: Path):
    from observal_cli import telemetry_buffer

    now = [100.0]
    monkeypatch.setattr(session_push.time, "time", lambda: now[0])
    monkeypatch.setattr(session_push.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", tmp_path / "outbox.db")

    db = tmp_path / "outbox.db"
    for index in range(61):
        telemetry_buffer.enqueue(
            {"harness": "claude-code", "session_id": "old", "lines": [str(index)], "start_offset": index},
            destination="http://server",
            user_id="user",
            db_path=db,
        )
    pace = session_push._recovery_pacer({"server_url": "http://server", "user_id": "user"})
    assert pace() == 0
    posted = []
    assert base.drain_outbox(
        {"server_url": "http://server", "user_id": "user", "_pace_recovery_delivery": pace},
        home=tmp_path,
        db_path=db,
        post=lambda payload, _cfg: posted.append(now[0]) or {"acknowledged_line": payload["start_offset"]},
    )
    assert len(posted) == 61
    assert posted[0] == 100.0
    assert posted[-1] == pytest.approx(166.0)
    assert all(later - earlier >= 1.1 - 1e-8 for earlier, later in pairwise(posted))


def test_recovery_worker_lock_skips_overlapping_workers(tmp_path: Path):
    with session_push._recovery_worker_lock("claude-code", tmp_path) as first:
        assert first
        with session_push._recovery_worker_lock("claude-code", tmp_path) as second:
            assert second is False
        with session_push._recovery_worker_lock("pi", tmp_path) as other_harness:
            assert other_harness
    with session_push._recovery_worker_lock("claude-code", tmp_path) as next_worker:
        assert next_worker


def test_background_recovery_retries_after_429_without_another_hook(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from observal_cli import telemetry_buffer

    now = [1000.0]
    monkeypatch.setattr(session_push.time, "time", lambda: now[0])
    monkeypatch.setattr(session_push.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", tmp_path / "outbox.db")
    monkeypatch.setattr(session_push, "ensure_loaded", lambda: None)
    monkeypatch.setattr(
        session_push,
        "get_adapter",
        lambda _harness: SimpleNamespace(aged_recovery_final=lambda: False, discover_session_sources=lambda **_kw: []),
    )
    monkeypatch.setattr(session_push, "load_config", lambda **_kw: {"server_url": "http://server", "user_id": "user"})
    calls = []

    def post(_config, **_kw):
        calls.append(now[0])
        if len(calls) == 1:
            telemetry_buffer.pause_delivery("http://server", "user", 30)
        return len(calls) > 1

    monkeypatch.setattr(session_push, "drain_outbox", post)
    session_push._recover_sessions("claude-code", home=tmp_path)
    assert calls == [1000.0, 1030.0]


def test_recovery_spools_old_sessions_before_waiting_for_rate_limit(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from observal_cli import telemetry_buffer

    source_file = tmp_path / "old.jsonl"
    source_file.write_text('"old"\n')
    source = SimpleNamespace(session_id="old", path=source_file, checkpoint_key="old")
    adapter = SimpleNamespace(
        aged_recovery_final=lambda: False,
        discover_session_sources=lambda **_kw: [source],
        should_capture_session=lambda _source, **_kw: True,
        session_extra_fields=lambda *_args, **_kw: {},
        related_session_sources=lambda *_args, **_kw: [],
    )
    now = [session_push.time.time() + 200]
    monkeypatch.setattr(session_push.time, "time", lambda: now[0])
    monkeypatch.setattr(session_push.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", tmp_path / "outbox.db")
    monkeypatch.setattr(session_push, "ensure_loaded", lambda: None)
    monkeypatch.setattr(session_push, "get_adapter", lambda _harness: adapter)
    monkeypatch.setattr(session_push, "load_config", lambda **_kw: {"server_url": "http://server", "user_id": "user"})
    monkeypatch.setattr(session_push, "read_cursor_state", lambda *_args, **_kw: (0, 0, False))
    spooled = []
    monkeypatch.setattr(
        session_push, "drain_session_source", lambda _source, _config, **kw: spooled.append(kw["spool_only"]) or True
    )
    calls = []

    def post(_config, **_kw):
        calls.append(now[0])
        if len(calls) == 1:
            telemetry_buffer.pause_delivery("http://server", "user", 30)
        return len(calls) > 1

    monkeypatch.setattr(session_push, "drain_outbox", post)
    session_push._recover_sessions("claude-code", home=tmp_path)
    assert spooled == [True]
    assert len(calls) == 2
    assert calls[1] - calls[0] == 30


def test_recovery_drains_spooled_source_when_pause_expires_during_capture(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from observal_cli import telemetry_buffer

    source_file = tmp_path / "old.jsonl"
    source_file.write_text('"old"\n')
    source = SimpleNamespace(session_id="old", path=source_file, checkpoint_key="old")
    adapter = SimpleNamespace(
        aged_recovery_final=lambda: False,
        discover_session_sources=lambda **_kw: [source],
        should_capture_session=lambda _source, **_kw: True,
        session_extra_fields=lambda *_args, **_kw: {},
        related_session_sources=lambda *_args, **_kw: [],
    )
    now = [source_file.stat().st_mtime + 200]
    monkeypatch.setattr(session_push.time, "time", lambda: now[0])
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", tmp_path / "outbox.db")
    monkeypatch.setattr(session_push, "ensure_loaded", lambda: None)
    monkeypatch.setattr(session_push, "get_adapter", lambda _harness: adapter)
    monkeypatch.setattr(session_push, "load_config", lambda **_kw: {"server_url": "http://server", "user_id": "user"})
    monkeypatch.setattr(session_push, "read_cursor_state", lambda *_args, **_kw: (0, 0, False))
    calls = []

    def drain(_config, **_kw):
        calls.append("drain")
        if len(calls) == 1:
            telemetry_buffer.pause_delivery("http://server", "user", 30)
        return True

    def capture(_source, _config, **kw):
        assert kw["spool_only"] is True
        calls.append("spool")
        now[0] += 31
        return True

    monkeypatch.setattr(session_push, "drain_outbox", drain)
    monkeypatch.setattr(session_push, "drain_session_source", capture)
    session_push._recover_sessions("claude-code", home=tmp_path)
    assert calls == ["drain", "spool", "drain"]


def test_recovery_discovers_sources_before_draining_aged_backlog(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from observal_cli import telemetry_buffer

    monkeypatch.setattr(telemetry_buffer, "DB_PATH", tmp_path / "outbox.db")
    source_file = tmp_path / "near-cutoff.jsonl"
    source_file.write_text('"old"\n')
    source = SimpleNamespace(session_id="near-cutoff", path=source_file, checkpoint_key="near-cutoff")
    cutoff = [True]
    observed = []

    def discover(**_kw):
        observed.append("discover")
        return [source] if cutoff[0] else []

    adapter = SimpleNamespace(
        aged_recovery_final=lambda: False,
        discover_session_sources=discover,
        should_capture_session=lambda _source, **_kw: True,
        session_extra_fields=lambda *_args, **_kw: {},
        related_session_sources=lambda *_args, **_kw: [],
    )
    monkeypatch.setattr(session_push, "ensure_loaded", lambda: None)
    monkeypatch.setattr(session_push, "get_adapter", lambda _harness: adapter)
    monkeypatch.setattr(session_push, "load_config", lambda **_kw: {"server_url": "http://server", "user_id": "user"})
    monkeypatch.setattr(session_push, "read_cursor_state", lambda *_args, **_kw: (0, 0, False))
    monkeypatch.setattr(session_push.time, "time", lambda: source_file.stat().st_mtime + 200)

    def slow_drain(_config, **_kw):
        observed.append("drain")
        cutoff[0] = False
        return True

    monkeypatch.setattr(session_push, "drain_outbox", slow_drain)
    monkeypatch.setattr(session_push, "drain_session_source", lambda *_args, **_kw: observed.append("spool") or True)
    session_push._recover_sessions("claude-code", home=tmp_path)
    assert observed == ["discover", "drain", "spool", "drain"]


def test_two_accounts_drain_while_first_post_exceeds_lock_wait(tmp_path: Path, monkeypatch):
    import time

    from observal_cli import telemetry_buffer

    db = tmp_path / "outbox.db"
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", db)
    for user in ("account-a", "account-b"):
        telemetry_buffer.enqueue(
            {"harness": "claude-code", "session_id": user, "lines": ["{}"], "start_offset": 0},
            destination="http://server/",
            user_id=user,
            db_path=db,
        )

    entered_a, release_a, posted_b = threading.Event(), threading.Event(), threading.Event()
    errors = []
    monkeypatch.setattr(
        session_push,
        "load_config",
        lambda **_kw: {"server_url": "http://server/", "user_id": threading.current_thread().name},
    )

    def post(_destination, _token, payload, **_kw):
        if payload["session_id"] == "account-a":
            entered_a.set()
            assert release_a.wait(10)
        else:
            posted_b.set()
        return {"acknowledged_line": 0}

    monkeypatch.setattr(base, "post_to_server_ack", post)

    def worker():
        try:
            session_push._drain_pending(home=tmp_path)
        except Exception as exc:
            errors.append(exc)

    first = threading.Thread(target=worker, name="account-a")
    second = threading.Thread(target=worker, name="account-b")
    first.start()
    try:
        assert entered_a.wait(3)
        started = time.monotonic()
        second.start()
        assert posted_b.wait(2), "account B must post before A releases its lock"
        second.join(2)
        assert not second.is_alive()
        time.sleep(max(0, 5.2 - (time.monotonic() - started)))
        assert not release_a.is_set()
    finally:
        release_a.set()
        first.join(3)
        if second.ident is not None:
            second.join(3)
    assert not first.is_alive() and not second.is_alive()
    assert not errors
    assert telemetry_buffer.stats(db_path=db)["pending"] == 0


def test_outbox_worker_lock_keeps_distinct_account_hashes(tmp_path: Path):
    from observal_cli import telemetry_buffer

    scopes = [telemetry_buffer.delivery_scope("http://server", user) for user in ("account-a", "account-b")]
    assert scopes[0] != scopes[1]
    for scope in scopes:
        with session_push._recovery_worker_lock(f"outbox-{scope}", tmp_path) as acquired:
            assert acquired
    lock_dir = tmp_path / ".observal"
    for scope in scopes:
        assert (lock_dir / f"session_recovery_outbox-{scope}.lock").exists()


def test_outbox_worker_retries_lock_after_empty_drainer_exits(tmp_path: Path, monkeypatch):
    empty = threading.Event()
    release = threading.Event()
    contended = threading.Event()
    calls = []
    errors = []
    monkeypatch.setattr(session_push, "load_config", lambda **_kw: {"server_url": "http://server", "user_id": "user"})
    real_lock = session_push._recovery_worker_lock
    from observal_cli import telemetry_buffer

    outbox_lock = f"outbox-{telemetry_buffer.delivery_scope('http://server', 'user')}"

    @contextmanager
    def observed_lock(name, home):
        with real_lock(name, home) as acquired:
            if name == outbox_lock and not acquired:
                contended.set()
            yield acquired

    monkeypatch.setattr(session_push, "_recovery_worker_lock", observed_lock)

    def drain(_config, **_kw):
        calls.append(threading.current_thread().name)
        if len(calls) == 1:
            empty.set()
            assert release.wait(3)
        return True

    monkeypatch.setattr(session_push, "drain_outbox", drain)

    def worker():
        try:
            session_push._drain_pending(home=tmp_path)
        except Exception as exc:
            errors.append(exc)

    first = threading.Thread(target=worker, name="first")
    second = threading.Thread(target=worker, name="second")
    first.start()
    try:
        assert empty.wait(3)
        second.start()
        assert contended.wait(3)
    finally:
        release.set()
        first.join(3)
        if second.ident is not None:
            second.join(3)
    assert not first.is_alive() and not second.is_alive()
    assert not errors
    assert calls == ["first", "second"]


def test_outbox_lock_wait_is_bounded(tmp_path: Path, monkeypatch):
    from observal_cli import telemetry_buffer

    outbox_lock = f"outbox-{telemetry_buffer.delivery_scope('http://server', 'user')}"
    waiter_lock = f"outbox-waiter-{telemetry_buffer.delivery_scope('http://server', 'user')}"
    now = [100.0]
    monkeypatch.setattr(session_push.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(session_push.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    monkeypatch.setattr(session_push, "load_config", lambda **_kw: {"server_url": "http://server", "user_id": "user"})
    monkeypatch.setattr(session_push, "drain_outbox", lambda *_args, **_kw: pytest.fail("lock was held"))
    with session_push._recovery_worker_lock(outbox_lock, tmp_path) as acquired:
        assert acquired
        session_push._drain_pending(home=tmp_path)
        assert now[0] <= 100 + session_push._OUTBOX_LOCK_WAIT_SECONDS + session_push._OUTBOX_LOCK_RETRY_SECONDS
    assert now[0] >= 100 + session_push._OUTBOX_LOCK_WAIT_SECONDS
    with session_push._recovery_worker_lock(outbox_lock, tmp_path) as acquired:
        assert acquired
        with session_push._recovery_worker_lock(waiter_lock, tmp_path) as waiting:
            assert waiting
            before = now[0]
            session_push._drain_pending(home=tmp_path)
            assert now[0] == before
    monkeypatch.setattr(session_push, "drain_outbox", lambda *_args, **_kw: True)
    session_push._drain_pending(home=tmp_path)


def test_final_hook_background_drain_waits_through_repeated_429s(tmp_path: Path, monkeypatch):
    from observal_cli import telemetry_buffer

    now = [1000.0]
    monkeypatch.setattr(session_push.time, "time", lambda: now[0])
    monkeypatch.setattr(session_push.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", tmp_path / "outbox.db")
    monkeypatch.setattr(session_push, "load_config", lambda **_kw: {"server_url": "http://server", "user_id": "user"})
    calls = []

    def post(_config, **_kw):
        calls.append(now[0])
        if len(calls) < 7:
            telemetry_buffer.pause_delivery("http://server", "user", 30)
            return False
        return True

    monkeypatch.setattr(session_push, "drain_outbox", post)
    session_push._drain_pending(home=tmp_path)
    assert len(calls) == 7
    assert calls[-1] == 1180.0


def _session_file(home: Path, project_key: str, session_id: str, *subdirs: str) -> Path:
    path = home / ".claude" / "projects" / project_key / Path(*subdirs) / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"type":"user"}\n')
    return path


def _write_marker(cwd: Path, **fields: str) -> None:
    marker = cwd / ".observal" / "agent"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps(fields))


# -- project_key_from_cwd ----------------------------------------------------


def test_project_key_replaces_every_separator():
    assert project_key_from_cwd("/home/ada/code/proj") == "-home-ada-code-proj"


def test_project_key_locates_the_session_file_written_for_that_cwd(tmp_path: Path):
    cwd = "/home/ada/code/proj"
    expected = _session_file(tmp_path, project_key_from_cwd(cwd), "s1")

    assert find_jsonl_file("s1", project_key_from_cwd(cwd), home=tmp_path) == expected


# -- find_jsonl_file ---------------------------------------------------------


def test_find_jsonl_prefers_the_primary_project_path(tmp_path: Path):
    primary = _session_file(tmp_path, "-work-app", "s1")
    _session_file(tmp_path, "-work-other", "s1")

    assert find_jsonl_file("s1", "-work-app", home=tmp_path) == primary


def test_find_jsonl_falls_back_to_a_search_across_projects(tmp_path: Path):
    # The cwd a hook reports can differ from the one Claude Code keyed the file by.
    elsewhere = _session_file(tmp_path, "-work-moved", "s1")

    assert find_jsonl_file("s1", "-work-app", home=tmp_path) == elsewhere


def test_find_jsonl_fallback_reaches_subagent_files(tmp_path: Path):
    subagent = _session_file(tmp_path, "-work-app", "child", "parent", "subagents")

    assert find_jsonl_file("child", "-work-app", home=tmp_path) == subagent


def test_find_jsonl_returns_none_when_no_file_matches(tmp_path: Path):
    _session_file(tmp_path, "-work-app", "other")

    assert find_jsonl_file("s1", "-work-app", home=tmp_path) is None


def test_find_jsonl_returns_none_without_a_projects_directory(tmp_path: Path):
    assert find_jsonl_file("s1", "-work-app", home=tmp_path) is None


# -- get_parent_session_id ---------------------------------------------------


def test_parent_session_id_comes_from_a_subagent_path():
    path = Path("/home/ada/.claude/projects/-work-app/parent-123/subagents/child-456.jsonl")

    assert get_parent_session_id(path) == "parent-123"


def test_top_level_session_has_no_parent():
    path = Path("/home/ada/.claude/projects/-work-app/session-123.jsonl")

    assert get_parent_session_id(path) is None


def test_path_too_short_to_hold_a_parent_has_none():
    assert get_parent_session_id(Path("subagents/child.jsonl")) is None


# -- read_new_lines ----------------------------------------------------------


def test_read_new_lines_past_end_of_file_returns_nothing(tmp_path: Path):
    source = tmp_path / "s1.jsonl"
    source.write_text('{"a":1}\n')

    assert base.read_new_lines(source, 1000) == ([], 0)


# -- read_agent_marker -------------------------------------------------------


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """read_agent_marker reads the push cursor from Path.home(), so give it a temp home."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    # Path.home() reads USERPROFILE on Windows, so both have to point at the same place.
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    return home_dir


def test_marker_missing_gives_no_agent(tmp_path: Path, home: Path):
    assert read_agent_marker(str(tmp_path)) == (None, None)


def test_malformed_marker_gives_no_agent(tmp_path: Path, home: Path):
    marker = tmp_path / ".observal" / "agent"
    marker.parent.mkdir()
    marker.write_text("{not json")

    assert read_agent_marker(str(tmp_path)) == (None, None)


def test_marker_without_pulled_at_attributes_the_session(tmp_path: Path, home: Path):
    _write_marker(tmp_path, agent_id="agent-1", agent_version="1.2.0")

    assert read_agent_marker(str(tmp_path)) == ("agent-1", "1.2.0")


def test_new_session_started_before_the_pull_is_not_attributed(tmp_path: Path, home: Path):
    # A session that already existed when the agent was pulled did not use it.
    session = tmp_path / "s1.jsonl"
    session.write_text('{"type":"user"}\n')
    pulled_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    _write_marker(tmp_path, agent_id="agent-1", agent_version="1.2.0", pulled_at=pulled_at)

    assert read_agent_marker(str(tmp_path), session) == (None, None)


def test_new_session_started_after_the_pull_is_attributed(tmp_path: Path, home: Path):
    session = tmp_path / "s1.jsonl"
    session.write_text('{"type":"user"}\n')
    pulled_at = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    _write_marker(tmp_path, agent_id="agent-1", agent_version="1.2.0", pulled_at=pulled_at)

    assert read_agent_marker(str(tmp_path), session) == ("agent-1", "1.2.0")


def test_resumed_session_skips_the_pulled_at_guard(tmp_path: Path, home: Path):
    # Once part of a session has been pushed (offset > 0), attribution no longer
    # depends on when the file was created.
    session = tmp_path / "s1.jsonl"
    session.write_text('{"type":"user"}\n')
    base.write_cursor("s1", 16, 1, home=home)
    pulled_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    _write_marker(tmp_path, agent_id="agent-1", agent_version="1.2.0", pulled_at=pulled_at)

    assert (home / ".observal" / "sync_state.json").exists()
    assert read_agent_marker(str(tmp_path), session) == ("agent-1", "1.2.0")
