# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import asyncio
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.routes import ingest as ingest_route
from api.routes.ingest import SessionIngestRequest
from observal_cli import telemetry_buffer
from observal_cli.harness import SessionSource, ensure_loaded, get_adapter
from observal_cli.sessions import base
from services import clickhouse, session_ingest


def _config():
    return {"server_url": "http://server", "user_id": "user", "access_token": "token"}


def _queue(db: Path, session: str, *, final: bool = True):
    telemetry_buffer.enqueue(
        {
            "harness": "claude-code",
            "session_id": session,
            "lines": ['{"n":0}', '{"n":1}'] if final else ['{"n":0}'],
            "start_offset": 0,
            "end_byte_offsets": [8, 16] if final else [8],
            "final": final,
            "total_line_count": 2 if final else None,
            "total_offset": 16 if final else None,
            "session_hash": "old-audit" if final else None,
        },
        destination="http://server",
        user_id="user",
        db_path=db,
    )


def test_negative_final_integrity_keeps_audit_then_corrected_response(tmp_path: Path):
    db = tmp_path / "outbox.db"
    _queue(db, "bad")
    _queue(db, "healthy", final=False)
    seen = []

    def negative(payload, _config):
        seen.append(payload["session_id"])
        if payload["session_id"] == "bad":
            return {"acknowledged_line": 1, "acknowledged_offset": 15, "integrity_ok": False}
        return {"acknowledged_line": 0, "acknowledged_offset": 8}

    assert not base.drain_outbox(_config(), home=tmp_path, db_path=db, post=negative)
    assert seen == ["bad", "healthy"]
    pending = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
    assert len(pending) == 1 and pending[0].final and pending[0].payload["session_hash"] == "old-audit"
    assert base.read_cursor_state("bad", home=tmp_path)[2] is False
    assert "integrity failed" in (tmp_path / ".observal" / "sync.log").read_text()

    # Backoff avoids hammering a bad final, but a later corrected audit can be retried.
    telemetry_buffer.clear_replay_backoff(pending, db_path=db)
    assert base.drain_outbox(
        _config(),
        home=tmp_path,
        db_path=db,
        post=lambda *_: {"acknowledged_line": 1, "acknowledged_offset": 16, "integrity_ok": True},
    )
    assert base.read_cursor_state("bad", home=tmp_path) == (16, 2, True)


@pytest.mark.parametrize("file_text", ['{"type":"system"}\n', "\n\n"])
@pytest.mark.parametrize("replay_event", ["Stop", "Reconcile"])
def test_cursor_hook_suffix_survives_metadata_final_repair_without_extra_records(
    tmp_path: Path, file_text: str, replay_event: str
):
    ensure_loaded()
    adapter = get_adapter("cursor")
    db = tmp_path / "outbox.db"
    path = tmp_path / "session.jsonl"
    path.write_text(file_text)
    source = SessionSource("cursor", "session", path)
    event = {"conversationId": "session", "event": "Stop", "input_tokens": 5}
    # Match the actual hook: the Stop event spools usage, then its delayed
    # worker (and reconcile) finalize without the original event payload.
    synthetic = adapter.session_extra_records(source, event, True, home=tmp_path)
    assert synthetic, "Cursor Stop test must exercise a real usage record"
    assert base.drain_session_source(
        source, _config(), hook_event="Stop", extra_records=synthetic, spool_only=True, home=tmp_path, db_path=db
    )
    seen = []
    file_count = len(base.read_new_records(path, 0)[0])
    total = file_count + len(synthetic)

    def server(payload, _config):
        seen.append(payload)
        if payload.get("final") and len([p for p in seen if p.get("final")]) == 1:
            return {"acknowledged_line": -1, "acknowledged_offset": 0, "repair_from_line": 0, "integrity_ok": False}
        return {"acknowledged_line": total - 1, "acknowledged_offset": len(file_text.encode()), "integrity_ok": True}

    assert base.drain_session_source(
        source, _config(), hook_event=replay_event, final=True, home=tmp_path, db_path=db, post=server
    )
    finals = [payload for payload in seen if payload.get("final")]
    assert len(finals) == 2
    assert finals[0]["lines"] == [] and finals[1]["lines"] == []
    assert finals[1]["total_line_count"] == total
    assert finals[1]["hashed_line_count"] == file_count
    assert sum(len(payload["lines"]) for payload in seen) == total + file_count
    assert base.read_cursor_state("session", home=tmp_path)[2]
    assert telemetry_buffer.stats(db_path=db)["pending"] == 0


@pytest.mark.parametrize("blank_change", ["grow", "shrink"])
def test_cursor_metadata_only_replay_reconciles_old_server_suffix(tmp_path: Path, monkeypatch, blank_change: str):
    ensure_loaded()
    db = tmp_path / "outbox.db"
    path = tmp_path / "session.jsonl"
    line = '{"type":"system"}'
    path.write_text(line + "\n" + ("\n\n" if blank_change == "shrink" else ""))
    old_end = path.stat().st_size
    source = SessionSource("cursor", "session", path)
    usage = get_adapter("cursor").session_extra_records(source, {"input_tokens": 5}, True)
    assert usage
    # The server already has usage, but the local outbox only retains a
    # metadata-only final. The suffix cannot be synthesized on replay.
    manifest = [(0, old_end, hashlib.sha256(line.encode()).hexdigest())]
    manifest.extend(
        (index, old_end, hashlib.sha256(record.encode()).hexdigest()) for index, record in enumerate(usage, 1)
    )
    assert len(manifest) == 2
    checkpoint = [1, old_end]
    monkeypatch.setattr(
        session_ingest, "query_session_source_manifest", AsyncMock(side_effect=lambda *_: list(manifest))
    )
    monkeypatch.setattr(
        session_ingest, "advance_session_checkpoint", AsyncMock(side_effect=lambda *_: tuple(checkpoint))
    )
    monkeypatch.setattr(clickhouse, "query_session_error_offsets", AsyncMock(return_value=[]))

    async def ingest_rows(**kwargs):
        if kwargs["lines"]:
            assert kwargs["lines"] == [line]  # no synthetic usage sent again
            manifest[0] = (0, kwargs["end_byte_offsets"][0], manifest[0][2])
        return session_ingest.IngestResult(ingested=0, skipped=len(kwargs["lines"]), errors=0)

    async def save_checkpoint(_session, _project, _user, _harness, line_index, byte_offset):
        checkpoint[:] = [line_index, byte_offset]

    monkeypatch.setattr(session_ingest, "ingest_session_lines", ingest_rows)
    monkeypatch.setattr(clickhouse, "insert_session_checkpoint", save_checkpoint)
    base.write_cursor(source.checkpoint_key, old_end, 2, home=tmp_path)
    assert base.drain_session_source(
        source, _config(), hook_event="Stop", final=True, spool_only=True, home=tmp_path, db_path=db
    )
    queued = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
    assert len(queued) == 1 and queued[0].final and queued[0].payload["lines"] == []
    telemetry_buffer.set_replay_hold(queued[0], db_path=db)
    if blank_change == "grow":
        with path.open("a") as file:
            file.write("\n\n")
    else:
        path.write_text(line + "\n")
    new_end = path.stat().st_size
    assert old_end != new_end
    sent = []

    def post(payload, _config):
        sent.append(payload)
        req = SessionIngestRequest(**payload)
        response = asyncio.run(ingest_route.ingest_session.__wrapped__(req, MagicMock(), MagicMock(id="user")))
        return response.model_dump()

    delivered = base.drain_session_source(
        source, _config(), hook_event="Reconcile", final=True, home=tmp_path, db_path=db, post=post
    )
    if blank_change == "shrink":
        # Even a blank-only shrink is witnessed unsafe; do not replay the
        # metadata final or claim the server's synthetic suffix was repaired.
        assert not delivered and not sent
        assert base.cursor_source_shrunk(source.checkpoint_key, home=tmp_path)
        pending = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
        assert len(pending) == 1 and pending[0].final and pending[0].id == queued[0].id
        assert telemetry_buffer.replay_held(
            destination="http://server", user_id="user", harness="cursor", session_id="session", db_path=db
        )
        assert manifest[1][1] == old_end and checkpoint == [1, old_end]
        session_ingest.query_session_source_manifest.assert_not_awaited()
        return
    assert delivered
    assert [payload["lines"] for payload in sent] == [[line], []]
    assert sent[-1]["hashed_line_count"] == 1 and sent[-1]["total_line_count"] == 2
    assert manifest[0][1] == new_end and manifest[1][1] == old_end
    assert checkpoint == [1, new_end]
    assert base.read_cursor_state(source.checkpoint_key, home=tmp_path)[2]
    assert telemetry_buffer.stats(db_path=db)["pending"] == 0
    session_ingest.query_session_source_manifest.assert_awaited_once_with(
        "session", ingest_route.DEFAULT_PROJECT_ID, "user", "cursor"
    )


def test_metadata_final_shrink_ack_then_append_reads_entire_record(tmp_path: Path):
    db = tmp_path / "outbox.db"
    path = tmp_path / "session.jsonl"
    first = '{"type":"system"}\n'
    assert len(first.encode()) == 18
    path.write_text(first + "\n" * 16)
    source = SessionSource("claude-code", "session", path)
    base.write_cursor(source.checkpoint_key, 18, 1, home=tmp_path)
    assert base.drain_session_source(
        source, _config(), hook_event="Stop", final=True, spool_only=True, home=tmp_path, db_path=db
    )
    old = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)[0]
    assert old.payload["lines"] == [] and old.end_offset == 34

    path.write_text(first)
    assert base.drain_session_source(
        source, _config(), hook_event="Stop", final=True, spool_only=True, home=tmp_path, db_path=db
    )
    replacement = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)[0]
    assert replacement.id == old.id and replacement.payload["total_offset"] == 18
    assert replacement.end_offset == 18
    assert base.drain_outbox(
        _config(),
        home=tmp_path,
        db_path=db,
        post=lambda *_: {"acknowledged_line": 0, "acknowledged_offset": 18, "integrity_ok": True},
    )
    assert base.read_cursor_state(source.checkpoint_key, home=tmp_path) == (18, 1, True)

    appended = '{"type":"user"}'
    with path.open("a") as file:
        file.write(appended + "\n")
    sent = []

    def post(payload, _config):
        sent.extend(payload["lines"])
        return {"acknowledged_line": 1, "acknowledged_offset": path.stat().st_size}

    assert base.drain_session_source(source, _config(), hook_event="Reconcile", home=tmp_path, db_path=db, post=post)
    assert sent == [appended]
    assert base.read_cursor(source.checkpoint_key, home=tmp_path) == (path.stat().st_size, 2)


def test_missing_server_suffix_does_not_fabricate_cursor_usage(tmp_path: Path):
    ensure_loaded()
    db = tmp_path / "outbox.db"
    path = tmp_path / "session.jsonl"
    path.write_text('{"type":"system"}\n')
    source = SessionSource("cursor", "session", path)
    usage = get_adapter("cursor").session_extra_records(source, {"input_tokens": 5}, True)
    assert base.drain_session_source(
        source, _config(), hook_event="Stop", extra_records=usage, spool_only=True, home=tmp_path, db_path=db
    )
    assert base.drain_outbox(
        _config(),
        home=tmp_path,
        db_path=db,
        post=lambda *_: {"acknowledged_line": 1, "acknowledged_offset": path.stat().st_size},
    )
    seen = []

    def missing_suffix(payload, _config):
        seen.append(payload)
        if payload.get("final"):
            return {
                "acknowledged_line": 0,
                "acknowledged_offset": path.stat().st_size,
                "repair_from_line": 1,
                "integrity_ok": False,
            }
        return {"acknowledged_line": 0, "acknowledged_offset": path.stat().st_size}

    assert not base.drain_session_source(
        source, _config(), hook_event="Reconcile", final=True, home=tmp_path, db_path=db, post=missing_suffix
    )
    assert all(usage[0] not in payload["lines"] for payload in seen)
    assert telemetry_buffer.replay_held(
        destination="http://server", user_id="user", harness="cursor", session_id="session", db_path=db
    )
    assert any(item.final for item in telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db))
    assert not base.read_cursor_state("session", home=tmp_path)[2]


def test_cursor_retained_synthetic_suffix_is_replayed_from_outbox(tmp_path: Path):
    ensure_loaded()
    db = tmp_path / "outbox.db"
    path = tmp_path / "session.jsonl"
    path.write_text('{"type":"system"}\n')
    source = SessionSource("cursor", "session", path)
    synthetic = get_adapter("cursor").session_extra_records(source, {"input_tokens": 5}, True)
    assert base.drain_session_source(
        source,
        _config(),
        hook_event="Stop",
        final=True,
        extra_records=synthetic,
        spool_only=True,
        home=tmp_path,
        db_path=db,
    )
    seen = []

    def server(payload, _config):
        seen.append(payload)
        if len(seen) == 1:
            return {"acknowledged_line": -1, "acknowledged_offset": 0, "repair_from_line": 0, "integrity_ok": False}
        return {"acknowledged_line": 1, "acknowledged_offset": path.stat().st_size, "integrity_ok": True}

    assert base.drain_session_source(
        source, _config(), hook_event="Reconcile", final=True, home=tmp_path, db_path=db, post=server
    )
    assert len(seen) == 2
    assert seen[1]["lines"] == ['{"type":"system"}', *synthetic]
    assert seen[1]["hashed_line_count"] == 1
    assert telemetry_buffer.stats(db_path=db)["pending"] == 0


@pytest.mark.parametrize("replay_event", ["Stop", "Reconcile"])
@pytest.mark.parametrize("blank_change", ["grow", "shrink"])
def test_cursor_retained_usage_rebases_after_blank_byte_change(tmp_path: Path, replay_event: str, blank_change: str):
    ensure_loaded()
    db = tmp_path / "outbox.db"
    path = tmp_path / "session.jsonl"
    path.write_text('{"type":"system"}\n' + ("\n\n" if blank_change == "shrink" else ""))
    source = SessionSource("cursor", "session", path)
    synthetic = get_adapter("cursor").session_extra_records(source, {"input_tokens": 5}, True)
    assert base.drain_session_source(
        source,
        _config(),
        hook_event="Stop",
        final=True,
        extra_records=synthetic,
        spool_only=True,
        home=tmp_path,
        db_path=db,
    )
    old_hash = base.hash_session_source(path)
    old = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)[0]
    old_offset = path.stat().st_size
    # The audit has been held, but the process dies before it can rewind the
    # finalized cursor or respool. Only trailing blank bytes change.
    base.write_cursor(source.checkpoint_key, old_offset, 2, finalized=True, home=tmp_path)
    telemetry_buffer.set_replay_hold(old, db_path=db)
    if blank_change == "grow":
        with path.open("a") as file:
            file.write("\n\n")
    else:
        path.write_text('{"type":"system"}\n')
    assert base.hash_session_source(path) == old_hash
    seen = []

    def post(payload, _config):
        seen.append(payload)
        return {"acknowledged_line": 1, "acknowledged_offset": path.stat().st_size, "integrity_ok": True}

    delivered = base.drain_session_source(
        source,
        _config(),
        hook_event=replay_event,
        final=True,
        home=tmp_path,
        db_path=db,
        post=post,
        checkpoint_fetch=lambda *_: pytest.fail("held replay must not use server cursor"),
        recover_from_server=True,
    )
    if blank_change == "shrink":
        assert not delivered and not seen
        assert base.cursor_source_shrunk(source.checkpoint_key, home=tmp_path)
        pending = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
        assert len(pending) == 1 and pending[0].final and pending[0].id == old.id
        assert telemetry_buffer.replay_held(
            destination="http://server", user_id="user", harness="cursor", session_id="session", db_path=db
        )
        return
    assert delivered
    assert len(seen) == 1
    assert seen[0]["lines"] == ['{"type":"system"}', *synthetic]
    assert seen[0]["end_byte_offsets"][-1] == path.stat().st_size
    assert seen[0]["session_hash"] == old_hash[0]
    assert seen[0]["hashed_line_count"] == old_hash[1]
    assert not telemetry_buffer.replay_held(
        destination="http://server", user_id="user", harness="cursor", session_id="session", db_path=db
    )


def test_cursor_missing_retained_usage_does_not_clear_hold_after_blank_append(tmp_path: Path):
    ensure_loaded()
    db = tmp_path / "outbox.db"
    path = tmp_path / "session.jsonl"
    path.write_text('{"type":"system"}\n')
    source = SessionSource("cursor", "session", path)
    synthetic = get_adapter("cursor").session_extra_records(source, {"input_tokens": 5}, True)
    assert base.drain_session_source(
        source,
        _config(),
        hook_event="Stop",
        final=True,
        extra_records=synthetic,
        spool_only=True,
        home=tmp_path,
        db_path=db,
    )
    old = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)[0]
    telemetry_buffer.set_replay_hold(old, db_path=db)
    # The retained audit claims a suffix, but its record is not available.
    # No success may be reported merely because the source hash is unchanged.
    import json

    payload = dict(old.payload)
    payload["lines"] = payload["lines"][:1]
    with telemetry_buffer._connect(db) as conn:
        conn.execute("UPDATE session_outbox SET payload = ? WHERE id = ?", (json.dumps(payload), old.id))
    with path.open("a") as file:
        file.write("\n")
    sent = []
    assert not base.drain_session_source(
        source,
        _config(),
        hook_event="Reconcile",
        final=True,
        home=tmp_path,
        db_path=db,
        post=lambda payload, _cfg: sent.append(payload) or None,
    )
    assert sent == []
    assert telemetry_buffer.replay_held(
        destination="http://server", user_id="user", harness="cursor", session_id="session", db_path=db
    )


def test_inflight_ack_cannot_retire_a_held_final(tmp_path: Path):
    db = tmp_path / "outbox.db"
    _queue(db, "session")
    item = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)[0]

    def racing_ack(*_args):
        telemetry_buffer.set_replay_hold(item, db_path=db)
        return {"acknowledged_line": 1, "acknowledged_offset": 16, "integrity_ok": True}

    assert not base.drain_outbox(_config(), home=tmp_path, db_path=db, post=racing_ack)
    queued = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
    assert len(queued) == 1 and queued[0].final
    assert base.read_cursor_state("session", home=tmp_path)[2] is False


@pytest.mark.parametrize("unavailable", ["missing", "truncated", "crash"])
def test_inside_final_repair_retains_batch_until_full_restart_replay(tmp_path: Path, monkeypatch, unavailable: str):
    db = tmp_path / "outbox.db"
    path = tmp_path / "session.jsonl"
    source = SessionSource("claude-code", "session", path)
    _queue(db, "session")
    _queue(db, "healthy", final=False)
    base.write_cursor("session", 16, 2, finalized=True, home=tmp_path)
    seen = []

    def repair(payload, _config):
        seen.append(payload["session_id"])
        if payload["session_id"] == "session":
            return {"acknowledged_line": 0, "acknowledged_offset": 8, "repair_from_line": 1, "integrity_ok": False}
        return {"acknowledged_line": 0, "acknowledged_offset": 8}

    assert not base.drain_outbox(_config(), home=tmp_path, db_path=db, post=repair)
    assert seen == ["session", "healthy"]
    assert base.read_cursor_state("session", home=tmp_path) == (0, 0, False)
    assert telemetry_buffer.replay_held(
        destination="http://server", user_id="user", harness="claude-code", session_id="session", db_path=db
    )
    if unavailable == "truncated":
        path.write_text('{"n":0}\n')
    elif unavailable == "crash":
        path.write_text('{"n":0}\n{"n":1}\n')
        enqueue = telemetry_buffer.enqueue

        def interrupted(*args, **kwargs):
            raise RuntimeError("crash before durable replay")

        monkeypatch.setattr(telemetry_buffer, "enqueue", interrupted)
        with pytest.raises(RuntimeError, match="crash"):
            base.drain_session_source(
                source, _config(), hook_event="Stop", final=True, spool_only=True, home=tmp_path, db_path=db
            )
        monkeypatch.setattr(telemetry_buffer, "enqueue", enqueue)

    if unavailable != "crash":
        assert not base.drain_session_source(
            source, _config(), hook_event="Stop", final=True, home=tmp_path, db_path=db, post=repair
        )
    queued = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
    assert any(item.final and item.payload["session_hash"] == "old-audit" for item in queued)
    assert seen == ["session", "healthy"]

    path.write_text('{"n":0}\n{"n":1}\n')
    delivered = []

    def corrected(payload, _config):
        delivered.append(payload)
        return {"acknowledged_line": 1, "acknowledged_offset": 16, "integrity_ok": True}

    assert base.drain_session_source(
        source, _config(), hook_event="Stop", final=True, home=tmp_path, db_path=db, post=corrected
    )
    assert len(delivered) == 1
    assert delivered[0]["lines"] == ['{"n":0}', '{"n":1}']
    assert delivered[0]["session_hash"] == base.hash_session_source(path)[0]
    assert base.read_cursor_state("session", home=tmp_path) == (16, 2, True)
    assert telemetry_buffer.stats(db_path=db)["pending"] == 0
