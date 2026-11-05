# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Which session a recorded capability use belongs to."""

from datetime import UTC, datetime, timedelta

import pytest

from observal_cli import capability_lock as lock

T0 = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
CWD = "/work/project"


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(lock, "LOCK_PATH", tmp_path / "capability_lock.jsonl")
    return tmp_path / "capability_lock.jsonl", tmp_path / "capability_claims.json"


def _record(mode, at, *, hint=None, harness="claude-code", cwd=CWD, component="c-1"):
    return lock.record(
        kind="mcp",
        mode=mode,
        source="install",
        harness=harness,
        cwd=cwd,
        component_id=component,
        session_hint=hint,
        now=at,
    )


def _for(session, started, *, harness="claude-code", cwd=CWD, transcript=True):
    return [
        (use.component_id, use.mode, confidence)
        for use, confidence in lock.for_session(
            session_id=session, harness=harness, cwd=cwd, started_at=started, has_transcript=transcript
        )
    ]


def test_a_hinted_use_belongs_only_to_its_session(paths, monkeypatch):
    monkeypatch.delenv("OBSERVAL_SESSION_ID", raising=False)
    _record(lock.MODE_CONTEXT, T0, hint="session-a")
    # Session B shares the harness, directory and time window, but not the hint.
    assert _for("session-a", T0 - timedelta(minutes=5)) == [("c-1", "context", "exact")]
    assert _for("session-b", T0 - timedelta(minutes=5)) == []


def test_a_hint_does_not_cross_harnesses_or_directories(paths, monkeypatch):
    """The same session ID in another harness or project is a different session."""
    monkeypatch.delenv("OBSERVAL_SESSION_ID", raising=False)
    _record(lock.MODE_CONTEXT, T0, hint="shared-id", harness="cursor")
    assert _for("shared-id", T0, harness="pi") == []
    assert _for("shared-id", T0, harness="cursor") == [("c-1", "context", "exact")]
    _record(lock.MODE_CONTEXT, T0, hint="shared-id", harness="pi", cwd="/work/other", component="c-2")
    assert _for("shared-id", T0, harness="pi") == []


def test_next_session_install_is_a_candidate_for_every_later_session(paths):
    """The server keeps the earliest-started candidate, so every later session
    must carry it, whichever order sessions are uploaded or reconciled in."""
    _record(lock.MODE_NEXT_SESSION, T0, hint="installing")
    assert _for("installing", T0 - timedelta(minutes=30)) == [], "never the session that ran the install"
    assert _for("already-running", T0 - timedelta(hours=2)) == [], "never a session running at install time"
    # Uploaded out of order: the later-started session first.
    assert _for("two-hours-later", T0 + timedelta(hours=2)) == [("c-1", "next-session", "window")]
    assert _for("one-hour-later", T0 + timedelta(hours=1)) == [("c-1", "next-session", "window")]


def test_next_session_install_respects_harness_and_directory(paths):
    # Installed from a subdirectory of the project the next session runs in.
    _record(lock.MODE_NEXT_SESSION, T0, cwd=f"{CWD}/pkg")
    assert _for("pi-session", T0 + timedelta(minutes=1), harness="pi") == []
    assert _for("elsewhere", T0 + timedelta(minutes=1), cwd="/work/other") == []
    assert _for("deeper", T0 + timedelta(minutes=1), cwd=f"{CWD}/pkg/inner") == [], "not above the session root"
    assert _for("project", T0 + timedelta(minutes=1)) == [("c-1", "next-session", "window")]


def test_next_session_install_needs_a_known_session_start(paths):
    _record(lock.MODE_NEXT_SESSION, T0)
    assert _for("unknown-start", None) == []
    assert _for("known-start", T0 + timedelta(minutes=1)) == [("c-1", "next-session", "window")]


def test_unhinted_context_load_uses_the_start_window(paths, monkeypatch):
    monkeypatch.delenv("OBSERVAL_SESSION_ID", raising=False)
    _record(lock.MODE_CONTEXT, T0)
    assert _for("started-just-after", T0 + timedelta(minutes=10)) == [("c-1", "context", "window")]
    assert _for("started-much-later", T0 + timedelta(hours=1)) == []
    monkeypatch.setattr(lock, "_now", lambda: T0 + timedelta(hours=1))
    # Confidence says whether the sender has the transcript, not whether the start is known.
    assert _for("start-unknown", None) == [("c-1", "context", "window")]
    assert _for("no-transcript", None, transcript=False) == [("c-1", "context", "loose")]
    monkeypatch.setattr(lock, "_now", lambda: T0 + timedelta(hours=30))
    assert _for("start-unknown", None) == [], "unknown start: only recent loads"


def test_attribution_writes_no_shared_state(paths):
    """Nothing is claimed locally, so concurrent senders cannot race."""
    lock_path, claims_path = paths
    _record(lock.MODE_NEXT_SESSION, T0)
    before = sorted(p.name for p in lock_path.parent.iterdir())
    _for("a", T0 + timedelta(minutes=1))
    _for("b", T0 + timedelta(minutes=2))
    assert sorted(p.name for p in lock_path.parent.iterdir()) == before
    assert not claims_path.exists()


def test_linux_transcript_without_a_first_line_timestamp_still_gets_its_install(tmp_path, monkeypatch):
    """No file birth time (Linux) and a first record without a timestamp: the
    start comes from a later line, so the next-session install still attaches."""
    from observal_cli.sessions import base as sessions_base

    monkeypatch.setattr(lock, "LOCK_PATH", tmp_path / "capability_lock.jsonl")
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        '{"type": "summary"}\n{"type": "user", "timestamp": "2026-09-30T13:00:00Z"}\n', encoding="utf-8"
    )

    class _NoBirth:
        st_birthtime = None

    class _LinuxPath(type(transcript)):  # only this transcript reports no birth time
        def stat(self, *args, **kwargs):
            return _NoBirth()

    transcript = _LinuxPath(transcript)
    assert sessions_base._session_started_at(transcript) == datetime(2026, 9, 30, 13, 0, tzinfo=UTC)
    _record(lock.MODE_NEXT_SESSION, T0)  # installed at 12:00, one hour earlier
    used = sessions_base._capabilities_for_session("next", CWD, "claude-code", transcript)
    assert [(u["component_id"], u["mode"], u["confidence"]) for u in used] == [("c-1", "next-session", "window")]
