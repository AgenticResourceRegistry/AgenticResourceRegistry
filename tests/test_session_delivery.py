# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from observal_cli import telemetry_buffer
from observal_cli.harness import SessionSource
from observal_cli.sessions import base


def config() -> dict:
    return {
        "server_url": "http://server",
        "access_token": "token",
        "user_id": "user",
    }


def disable_payload_metadata(monkeypatch):
    monkeypatch.setattr(base, "_resolve_agent", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(base, "_get_cached_layer_hash", lambda *_args, **_kwargs: None)


def test_read_new_records_excludes_partial_line_and_tracks_bytes(tmp_path: Path):
    source = tmp_path / "session.jsonl"
    source.write_bytes(b'{"a":1}\n\n{"b":2}')

    lines, end_offsets, consumed = base.read_new_records(source, 0)

    assert lines == ['{"a":1}']
    assert end_offsets == [8]
    assert consumed == 9


@pytest.mark.parametrize("local_state", ["missing", "corrupt", "stale"])
def test_server_checkpoint_recovers_local_cursor(tmp_path: Path, monkeypatch, local_state: str):
    disable_payload_metadata(monkeypatch)
    source_path = tmp_path / "session.jsonl"
    records = ['{"n":0}\n', '{"n":1}\n', '{"n":2}\n']
    source_path.write_text("".join(records))
    if local_state == "corrupt":
        state = tmp_path / ".observal" / "sync_state.json"
        state.parent.mkdir()
        state.write_text("not-json")
    elif local_state == "stale":
        base.write_cursor("session", source_path.stat().st_size, 3, finalized=True, home=tmp_path)
    db = tmp_path / "outbox.db"
    source = SessionSource("claude-code", "session", source_path)
    acknowledged_offset = len(records[0].encode()) + len(records[1].encode())

    assert base.drain_session_source(
        source,
        config(),
        hook_event="Reconcile",
        spool_only=True,
        recover_from_server=True,
        checkpoint_fetch=lambda _source, _config: {
            "acknowledged_line": 1,
            "acknowledged_offset": acknowledged_offset,
        },
        home=tmp_path,
        db_path=db,
    )

    item = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)[0]
    assert item.start_line == item.end_line == 2
    assert item.payload["lines"] == ['{"n":2}']
    assert base.read_cursor("session", home=tmp_path) == (acknowledged_offset, 2)
    assert base.read_cursor_state("session", home=tmp_path)[2] is False


def test_reconcile_spool_cannot_replace_built_layer_hash_with_extra_field(tmp_path: Path, monkeypatch):
    disable_payload_metadata(monkeypatch)
    built_hash = "v2_" + "a" * 60
    monkeypatch.setattr(base, "_get_cached_layer_hash", lambda *_args: built_hash)
    source_path = tmp_path / "session.jsonl"
    source_path.write_text('{"n":0}\n')
    source = SessionSource("claude-code", "fixture-reconcile", source_path, cwd=str(tmp_path))
    db_path = tmp_path / "outbox.db"
    assert base.drain_session_source(
        source,
        config(),
        hook_event="Reconcile",
        spool_only=True,
        extra_fields={"layer_hash": "unpaired-v1"},
        home=tmp_path,
        db_path=db_path,
    )
    stored = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db_path)
    assert stored[0].payload["layer_hash"] == built_hash


def test_server_checkpoint_without_byte_offset_maps_source_line(tmp_path: Path):
    source_path = tmp_path / "session.jsonl"
    source_path.write_text('{"n":0}\n\n{"n":1}\n')
    source = SessionSource("claude-code", "session", source_path)

    recovered = base.recover_cursor_from_server(
        source,
        config(),
        home=tmp_path,
        fetch=lambda _source, _config: {"acknowledged_line": 0, "acknowledged_offset": 0},
    )

    assert recovered == (8, 1)


def test_invalid_server_byte_checkpoint_does_not_skip_local_source(tmp_path: Path):
    source_path = tmp_path / "session.jsonl"
    source_path.write_text('{"n":0}\n')
    source = SessionSource("claude-code", "session", source_path)

    recovered = base.recover_cursor_from_server(
        source,
        config(),
        home=tmp_path,
        fetch=lambda _source, _config: {"acknowledged_line": 4, "acknowledged_offset": 999},
    )

    assert recovered is None
    assert base.read_cursor("session", home=tmp_path) == (0, 0)


def test_offline_delivery_spools_before_post_and_keeps_cursor(tmp_path: Path, monkeypatch):
    disable_payload_metadata(monkeypatch)
    source_path = tmp_path / "session.jsonl"
    source_path.write_text('{"type":"system","content":"one"}\n')
    db = tmp_path / "outbox.db"
    source = SessionSource("claude-code", "session", source_path)
    observed_pending: list[int] = []

    def offline(_payload, _config):
        observed_pending.append(telemetry_buffer.stats(db_path=db)["pending"])
        return None

    assert not base.drain_session_source(
        source,
        config(),
        hook_event="UserPromptSubmit",
        home=tmp_path,
        db_path=db,
        post=offline,
    )

    assert observed_pending == [1]
    assert base.read_cursor("session", home=tmp_path) == (0, 0)
    assert telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)[0].attempts == 1


@pytest.mark.parametrize("status_code", [400, 409, 413, 415, 422])
def test_payload_rejection_status_is_permanent(monkeypatch, status_code: int):
    import httpx

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            return type("Response", (), {"status_code": status_code})()

    monkeypatch.setattr(httpx, "Client", lambda **_kwargs: Client())

    with pytest.raises(base.PermanentIngestRejectionError):
        base.post_to_server_ack("http://server", "token", {"session_id": "bad", "lines": []})


def test_permanent_rejection_is_quarantined_without_blocking_later_sessions(tmp_path: Path):
    db = tmp_path / "outbox.db"
    for session_id, line in (("bad", "invalid"), ("good", "valid")):
        telemetry_buffer.enqueue(
            {
                "harness": "claude-code",
                "session_id": session_id,
                "lines": [line],
                "start_offset": 0,
                "end_byte_offsets": [len(line)],
            },
            destination="http://server",
            user_id="user",
            db_path=db,
        )

    def post(payload, _config):
        if payload["session_id"] == "bad":
            raise base.PermanentIngestRejectionError(422)
        return {"acknowledged_line": 0, "acknowledged_offset": len("valid")}

    rejections = []
    assert not base.drain_outbox(config(), home=tmp_path, db_path=db, post=post, rejections=rejections)
    assert rejections == [("claude-code", "bad", 422)]
    pending = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
    assert pending == []
    assert telemetry_buffer.stats(db_path=db)["failed"] == 1
    entries = list(db.with_suffix(".rejected.d").glob("*.json"))
    assert len(entries) == 1
    assert json.loads(entries[0].read_text())["payload"]["lines"] == ["invalid"]


def _enqueue_lines(db: Path, session_id: str, start_line: int, count: int) -> None:
    telemetry_buffer.enqueue(
        {
            "harness": "claude-code",
            "session_id": session_id,
            "lines": [f"line-{start_line + i}" for i in range(count)],
            "start_offset": start_line,
            "end_byte_offsets": [10 * (start_line + i + 1) for i in range(count)],
        },
        destination="http://server",
        user_id="user",
        db_path=db,
    )


def _ack_behind_gap(payload: dict, _config: dict) -> dict:
    # Lines 3-4 of "gappy" were quarantined, so its checkpoint stays at line 2.
    acknowledged = 2 if payload["session_id"] == "gappy" else payload["start_offset"] + len(payload["lines"]) - 1
    return {"acknowledged_line": acknowledged, "ingested": len(payload["lines"]), "errors": 0}


def test_batch_past_a_quarantined_gap_is_accepted_without_blocking_later_sessions(tmp_path: Path):
    db = tmp_path / "outbox.db"
    _enqueue_lines(db, "gappy", 5, 2)
    _enqueue_lines(db, "next", 0, 1)
    posted = []

    def post(payload, cfg):
        posted.append(payload["session_id"])
        return _ack_behind_gap(payload, cfg)

    assert base.drain_outbox(config(), home=tmp_path, db_path=db, post=post)
    assert posted == ["gappy", "next"]
    assert telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db) == []
    assert base.read_cursor("gappy", home=tmp_path)[1] == 7


def test_batch_past_a_gap_is_accepted_when_the_server_skipped_some_lines(tmp_path: Path):
    # Re-sent lines and ignorable records count as skipped, not ingested, yet they are stored.
    db = tmp_path / "outbox.db"
    _enqueue_lines(db, "gappy", 5, 2)

    def post(payload, _cfg):
        return {"acknowledged_line": 2, "ingested": 1, "skipped": 1, "errors": 0}

    assert base.drain_outbox(config(), home=tmp_path, db_path=db, post=post)
    assert telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db) == []
    assert base.read_cursor("gappy", home=tmp_path)[1] == 7


def test_respooled_earlier_lines_are_sent_before_later_batches(tmp_path: Path):
    db = tmp_path / "outbox.db"
    _enqueue_lines(db, "gappy", 5, 2)
    _enqueue_lines(db, "other", 0, 1)
    _enqueue_lines(db, "gappy", 3, 2)  # spooled again after its later lines
    checkpoints = {"gappy": 2}
    posted = []

    def contiguous_server(payload, _cfg):
        session_id, start = payload["session_id"], payload["start_offset"]
        ack = checkpoints.get(session_id, start - 1)
        if start <= ack + 1:
            ack = max(ack, start + len(payload["lines"]) - 1)
        checkpoints[session_id] = ack
        posted.append((session_id, start))
        return {"acknowledged_line": ack, "ingested": len(payload["lines"]), "errors": 0}

    assert base.drain_outbox(config(), home=tmp_path, db_path=db, post=contiguous_server)
    assert posted == [("gappy", 3), ("gappy", 5), ("other", 0)]
    assert checkpoints["gappy"] == 6


def test_batch_past_a_gap_waits_when_the_server_reports_errors(tmp_path: Path):
    db = tmp_path / "outbox.db"
    _enqueue_lines(db, "gappy", 5, 2)

    def post(payload, cfg):
        return {**_ack_behind_gap(payload, cfg), "errors": 1}

    assert not base.drain_outbox(config(), home=tmp_path, db_path=db, post=post)
    pending = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
    assert [(item.start_line, item.end_line) for item in pending] == [(5, 6)]


def test_reconcile_delivery_reports_rejection_without_advancing_cursor(tmp_path: Path, monkeypatch):
    disable_payload_metadata(monkeypatch)
    source_path = tmp_path / "session.jsonl"
    source_path.write_text('{"role":"user"}\n')
    source = SessionSource("kiro", "bad", source_path)
    db = tmp_path / "outbox.db"
    rejections = []

    delivered = base.drain_session_source(
        source,
        config(),
        hook_event="Reconcile",
        final=True,
        home=tmp_path,
        db_path=db,
        post=lambda _payload, _config: (_ for _ in ()).throw(base.PermanentIngestRejectionError(422)),
        rejections=rejections,
    )

    assert delivered is False
    assert rejections == [("kiro", "bad", 422)]
    assert base.read_cursor(source.checkpoint_key, home=tmp_path) == (0, 0)
    assert telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db) == []
    assert telemetry_buffer.stats(db_path=db)["failed"] == 1


def test_server_failure_skips_session_but_retains_order_across_restart(tmp_path: Path, monkeypatch):
    db = tmp_path / "outbox.db"
    for session, start in (("bad", 0), ("bad", 1), ("good", 0)):
        telemetry_buffer.enqueue(
            {
                "harness": "claude-code",
                "session_id": session,
                "lines": [str(start)],
                "start_offset": start,
                "end_byte_offsets": [(start + 1) * 10],
            },
            destination="http://server",
            user_id="user",
            db_path=db,
        )
    posted = []

    def post(payload, _config):
        posted.append((payload["session_id"], payload["start_offset"]))
        if payload["session_id"] == "bad":
            raise base.SessionServerError("500")
        return {"acknowledged_line": 0, "acknowledged_offset": 10}

    base.drain_outbox(config(), home=tmp_path, db_path=db, post=post)
    assert posted == [("bad", 0), ("good", 0)]
    posted.clear()
    base.drain_outbox(config(), home=tmp_path, db_path=db, post=post)
    assert posted == []
    assert [
        (item.session_id, item.start_line)
        for item in telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
    ] == [("bad", 0), ("bad", 1)]

    monkeypatch.setattr(telemetry_buffer.time, "time", lambda: 10**12)
    posted.clear()
    base.drain_outbox(
        config(),
        home=tmp_path,
        db_path=db,
        post=lambda payload, cfg: (
            posted.append(payload["start_offset"])
            or {"acknowledged_line": payload["start_offset"], "acknowledged_offset": payload["end_byte_offsets"][-1]}
        ),
    )
    assert posted == [0, 1]


def test_backoff_skips_eight_sessions_and_visits_ninth_on_next_drain(tmp_path: Path):
    db = tmp_path / "outbox.db"
    for number in range(9):
        telemetry_buffer.enqueue(
            {
                "harness": "claude-code",
                "session_id": f"s{number}",
                "lines": ["x"],
                "start_offset": 0,
                "end_byte_offsets": [1],
            },
            destination="http://server",
            user_id="user",
            db_path=db,
        )
    visited = []

    def post(payload, _config):
        visited.append(payload["session_id"])
        if payload["session_id"] != "s8":
            raise base.SessionServerError("500")
        return {"acknowledged_line": 0, "acknowledged_offset": 1}

    assert not base.drain_outbox(config(), home=tmp_path, db_path=db, post=post)
    assert visited == [f"s{n}" for n in range(8)]
    assert not base.drain_outbox(config(), home=tmp_path, db_path=db, post=post)
    assert visited[-1] == "s8"


def test_many_healthy_sessions_finish_across_bounded_drains(tmp_path: Path):
    db = tmp_path / "outbox.db"
    for number in range(60):
        telemetry_buffer.enqueue(
            {
                "harness": "claude-code",
                "session_id": f"healthy-{number}",
                "lines": ["x"],
                "start_offset": 0,
                "end_byte_offsets": [1],
            },
            destination="http://server",
            user_id="user",
            db_path=db,
        )
    posted = []

    def post(payload, _config):
        posted.append(payload["session_id"])
        return {"acknowledged_line": 0, "acknowledged_offset": 1}

    assert not base.drain_outbox(config(), home=tmp_path, db_path=db, post=post)
    assert len(posted) == 50
    assert base.drain_outbox(config(), home=tmp_path, db_path=db, post=post)
    assert len(set(posted)) == 60
    assert telemetry_buffer.stats(db_path=db)["pending"] == 0


def test_rejected_gap_survives_restart_and_explicit_recovery(tmp_path: Path):
    db = tmp_path / "outbox.db"
    ids = [
        telemetry_buffer.enqueue(
            {
                "harness": "claude-code",
                "session_id": "bad",
                "lines": [line],
                "start_offset": index,
                "end_byte_offsets": [index + 1],
            },
            destination="http://server",
            user_id="user",
            db_path=db,
        )
        for index, line in enumerate(("invalid", "later"))
    ]
    posted = []

    def reject(payload, _config):
        posted.append(payload["start_offset"])
        raise base.PermanentIngestRejectionError(422)

    assert not base.drain_outbox(config(), home=tmp_path, db_path=db, post=reject)
    assert posted == [0]
    assert not base.drain_outbox(config(), home=tmp_path, db_path=db, post=reject)
    assert posted == [0]
    assert telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db) == []
    entries = list(db.with_suffix(".rejected.d").glob("*.json"))
    assert len(entries) == 1
    assert entries[0].stat().st_mode & 0o777 == 0o600
    with pytest.raises(telemetry_buffer.OutboxConflictError):
        telemetry_buffer.recover_quarantined(
            ids[0],
            db_path=db,
            payload={"harness": "claude-code", "session_id": "other", "lines": ["x"], "start_offset": 0},
        )
    assert telemetry_buffer.is_rejected(
        destination="http://server",
        user_id="user",
        harness="claude-code",
        session_id="bad",
        db_path=db,
    )
    assert telemetry_buffer.recover_quarantined(ids[0], db_path=db) > 0
    with pytest.raises(telemetry_buffer.OutboxError, match="already recovered"):
        telemetry_buffer.recover_quarantined(ids[0], db_path=db)
    assert [
        item.start_line
        for item in telemetry_buffer.pending(
            destination="http://server",
            user_id="user",
            db_path=db,
        )
    ] == [0, 1]
    # Recovery must post the missing source line before later same-session records.
    posted.clear()
    assert base.drain_outbox(
        config(),
        home=tmp_path,
        db_path=db,
        post=lambda payload, _cfg: (
            posted.append(payload["start_offset"])
            or {"acknowledged_line": payload["start_offset"], "acknowledged_offset": payload["end_byte_offsets"][-1]}
        ),
    )
    assert posted == [0, 1]


def test_ack_cannot_delete_unposted_final_metadata_or_future_source(tmp_path: Path):
    db = tmp_path / "outbox.db"
    for payload in (
        {"lines": ["zero"], "start_offset": 0, "end_byte_offsets": [10]},
        {"lines": ["one"], "start_offset": 1, "end_byte_offsets": [20]},
        {"lines": [], "start_offset": 2, "final": True, "total_offset": 20},
    ):
        telemetry_buffer.enqueue(
            {"harness": "claude-code", "session_id": "s", **payload},
            destination="http://server",
            user_id="user",
            db_path=db,
        )

    def ack(payload, _config):
        return {"acknowledged_line": 999, "acknowledged_offset": 999999}

    assert base.drain_outbox(config(), home=tmp_path, db_path=db, post=ack)
    assert base.read_cursor("s", home=tmp_path) == (20, 2)
    assert telemetry_buffer.stats(db_path=db)["pending"] == 0


def test_spool_only_never_blocks_hook_on_network(tmp_path: Path, monkeypatch):
    disable_payload_metadata(monkeypatch)
    source_path = tmp_path / "session.jsonl"
    source_path.write_text('{"role":"user","message":{"content":[]}}\n')
    db = tmp_path / "outbox.db"
    posts: list[dict] = []

    assert base.drain_session_source(
        SessionSource("cursor", "session", source_path),
        config(),
        hook_event="stop",
        extra_records=(json.dumps({"role": "assistant", "message": {"usage": {"input_tokens": 1}}}),),
        spool_only=True,
        home=tmp_path,
        db_path=db,
        post=lambda payload, _config: posts.append(payload),
    )

    assert posts == []
    assert base.read_cursor("session", home=tmp_path) == (0, 0)
    item = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)[0]
    assert item.start_line == 0
    assert item.end_line == 1


def test_offline_growth_spools_only_records_after_pending_checkpoint(tmp_path: Path, monkeypatch):
    disable_payload_metadata(monkeypatch)
    source_path = tmp_path / "session.jsonl"
    source_path.write_text('{"type":"system","content":"one"}\n')
    db = tmp_path / "outbox.db"
    source = SessionSource("claude-code", "session", source_path)

    def offline(_payload, _config):
        return None

    base.drain_session_source(
        source,
        config(),
        hook_event="UserPromptSubmit",
        home=tmp_path,
        db_path=db,
        post=offline,
    )
    with source_path.open("a") as file:
        file.write('{"type":"system","content":"two"}\n')
    base.drain_session_source(
        source,
        config(),
        hook_event="Stop",
        final=True,
        home=tmp_path,
        db_path=db,
        post=offline,
    )

    items = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
    assert [(item.start_line, item.end_line) for item in items] == [(0, 0), (1, 1)]
    assert items[-1].final
    assert base.read_cursor("session", home=tmp_path) == (0, 0)


def test_restart_drains_acknowledged_records_and_finalizes_cursor(tmp_path: Path, monkeypatch):
    disable_payload_metadata(monkeypatch)
    source_path = tmp_path / "session.jsonl"
    source_path.write_text('{"type":"system","content":"one"}\n\n')
    db = tmp_path / "outbox.db"
    source = SessionSource("claude-code", "session", source_path)

    base.drain_session_source(
        source,
        config(),
        hook_event="Stop",
        final=True,
        home=tmp_path,
        db_path=db,
        post=lambda _payload, _config: None,
    )

    def acknowledge(payload, _config):
        return {
            "acknowledged_line": payload["start_offset"] + len(payload["lines"]) - 1,
            "acknowledged_offset": payload["end_byte_offsets"][-1],
        }

    assert base.drain_outbox(config(), home=tmp_path, db_path=db, post=acknowledge)
    assert telemetry_buffer.stats(db_path=db)["pending"] == 0
    assert base.read_cursor("session", home=tmp_path) == (source_path.stat().st_size, 1)
    state = json.loads((tmp_path / ".observal" / "sync_state.json").read_text())
    assert state["session"]["finalized"] is True


def test_metadata_only_final_batch_is_spooled_and_acknowledged(tmp_path: Path, monkeypatch):
    disable_payload_metadata(monkeypatch)
    source_path = tmp_path / "session.jsonl"
    source_path.write_text("")
    db = tmp_path / "outbox.db"
    captured: list[dict] = []

    def acknowledge(payload, _config):
        captured.append(payload)
        return {"acknowledged_line": -1, "acknowledged_offset": 0}

    assert base.drain_session_source(
        SessionSource("kiro", "session", source_path),
        config(),
        hook_event="Stop",
        final=True,
        extra_fields={"total_credits": 2.0},
        home=tmp_path,
        db_path=db,
        post=acknowledge,
    )
    assert captured[0]["lines"] == []
    assert captured[0]["total_credits"] == 2.0
    assert telemetry_buffer.stats(db_path=db)["pending"] == 0


def test_final_drain_with_no_new_records_marks_acknowledged_cursor_final(tmp_path: Path, monkeypatch):
    disable_payload_metadata(monkeypatch)
    source_path = tmp_path / "session.jsonl"
    source_path.write_text('{"type":"system","content":"one"}\n')
    db = tmp_path / "outbox.db"
    source = SessionSource("claude-code", "session", source_path)

    def acknowledge(payload, _config):
        return {
            "acknowledged_line": payload["start_offset"] + len(payload["lines"]) - 1,
            "acknowledged_offset": payload.get("end_byte_offsets", [])[-1]
            if payload.get("end_byte_offsets")
            else payload["total_offset"],
        }

    assert base.drain_session_source(
        source,
        config(),
        hook_event="UserPromptSubmit",
        home=tmp_path,
        db_path=db,
        post=acknowledge,
    )
    assert base.drain_session_source(
        source,
        config(),
        hook_event="Stop",
        final=True,
        home=tmp_path,
        db_path=db,
        post=acknowledge,
    )
    state = json.loads((tmp_path / ".observal" / "sync_state.json").read_text())
    assert state["session"]["finalized"] is True


def test_final_hash_mismatch_rewinds_and_repairs_source_range(tmp_path: Path, monkeypatch):
    disable_payload_metadata(monkeypatch)
    source_path = tmp_path / "session.jsonl"
    source_path.write_text('{"n":0}\n{"n":1}\n{"n":2}\n')
    source = SessionSource("claude-code", "session", source_path)
    db = tmp_path / "outbox.db"
    starts: list[int] = []

    def audit(payload, _config):
        starts.append(payload["start_offset"])
        if len(starts) == 1:
            return {
                "acknowledged_line": 0,
                "acknowledged_offset": 8,
                "integrity_ok": False,
                "repair_from_line": 1,
            }
        return {
            "acknowledged_line": 2,
            "acknowledged_offset": source_path.stat().st_size,
            "integrity_ok": True,
        }

    assert base.drain_session_source(
        source,
        config(),
        hook_event="Stop",
        final=True,
        home=tmp_path,
        db_path=db,
        post=audit,
    )
    assert starts == [0, 1]
    assert telemetry_buffer.stats(db_path=db)["pending"] == 0
    assert base.read_cursor("session", home=tmp_path) == (source_path.stat().st_size, 3)


@pytest.mark.parametrize("interrupt", ["write", "accept"])
def test_repair_interruption_preserves_final_replay(tmp_path: Path, monkeypatch, interrupt: str):
    disable_payload_metadata(monkeypatch)
    path = tmp_path / "session.jsonl"
    path.write_text('{"n":0}\n{"n":1}\n')
    source = SessionSource("claude-code", "session", path)
    db = tmp_path / "outbox.db"
    base.write_cursor("session", path.stat().st_size, 2, finalized=True, home=tmp_path)
    telemetry_buffer.enqueue(
        {
            "harness": "claude-code",
            "session_id": "session",
            "start_offset": 0,
            "lines": ['{"n":0}', '{"n":1}'],
            "end_byte_offsets": [8, path.stat().st_size],
            "final": True,
            "total_line_count": 2,
            "total_offset": path.stat().st_size,
        },
        destination="http://server",
        user_id="user",
        db_path=db,
    )
    real_write = base._write_cursor_unlocked
    real_accept = telemetry_buffer.accept_item

    def interrupted_write(*args, **kwargs):
        raise RuntimeError("interrupted before cursor replacement")

    def interrupted_accept(*args, **kwargs):
        raise RuntimeError("interrupted after cursor replacement")

    monkeypatch.setattr(base, "_write_cursor_unlocked", interrupted_write if interrupt == "write" else real_write)
    monkeypatch.setattr(telemetry_buffer, "accept_item", interrupted_accept if interrupt == "accept" else real_accept)

    def repair(*_args):
        return {"acknowledged_line": 0, "acknowledged_offset": 8, "repair_from_line": 1}

    with pytest.raises(RuntimeError, match="interrupted"):
        base.drain_outbox(config(), home=tmp_path, db_path=db, post=repair)
    queued = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
    assert len(queued) == 1 and queued[0].final
    assert base.read_cursor_state("session", home=tmp_path) == (
        (path.stat().st_size, 2, True) if interrupt == "write" else (8, 1, False)
    )
    monkeypatch.setattr(base, "_write_cursor_unlocked", real_write)
    monkeypatch.setattr(telemetry_buffer, "accept_item", real_accept)
    posts = []

    def resume(payload, _config):
        posts.append(payload["start_offset"])
        return {
            "acknowledged_line": payload["start_offset"] + len(payload["lines"]) - 1,
            "acknowledged_offset": payload.get("end_byte_offsets", [payload.get("total_offset")])[-1],
        }

    assert base.drain_session_source(
        source, config(), hook_event="Stop", final=True, home=tmp_path, db_path=db, post=resume
    )
    assert posts[0] == 0
    assert base.read_cursor_state("session", home=tmp_path) == (path.stat().st_size, 2, True)


@pytest.mark.parametrize("count", [12, 512])
def test_earlier_final_repair_respooled_before_queued_neighbors(tmp_path: Path, monkeypatch, count: int):
    disable_payload_metadata(monkeypatch)
    path = tmp_path / "session.jsonl"
    path.write_text("".join(f'{{"n":{n}}}  \n' for n in range(count)))
    source = SessionSource("claude-code", "session", path)
    db = tmp_path / "outbox.db"
    lines, offsets, _ = base.read_new_records(path, 0)
    assert offsets[4] == 50
    base.write_cursor("session", offsets[9], 10, finalized=True, home=tmp_path)
    for start in (10, 11):
        telemetry_buffer.enqueue(
            {
                "harness": "claude-code",
                "session_id": "session",
                "start_offset": start,
                "lines": [lines[start]],
                "end_byte_offsets": [offsets[start]],
                "final": start == 10,
                "total_line_count": count,
                "total_offset": offsets[-1],
            },
            destination="http://server",
            user_id="user",
            db_path=db,
        )
    posted = []

    def audit(payload, _config):
        posted.append(payload["start_offset"])
        if len(posted) == 1:
            return {"acknowledged_line": 4, "acknowledged_offset": offsets[4], "repair_from_line": 5}
        return {
            "acknowledged_line": payload["start_offset"] + len(payload["lines"]) - 1,
            "acknowledged_offset": payload["end_byte_offsets"][-1],
        }

    assert base.drain_session_source(
        source, config(), hook_event="Stop", final=True, home=tmp_path, db_path=db, post=audit
    )
    assert posted == ([10, 0] if count == 12 else [10, 0, 500])
    assert telemetry_buffer.stats(db_path=db)["pending"] == 0
    assert base.read_cursor_state("session", home=tmp_path) == (offsets[-1], count, True)


def test_earlier_repair_survives_interrupted_chunk_spool(tmp_path: Path, monkeypatch):
    disable_payload_metadata(monkeypatch)
    path = tmp_path / "session.jsonl"
    path.write_text("".join(f'{{"n":{n}}}\n' for n in range(512)))
    source = SessionSource("claude-code", "session", path)
    db = tmp_path / "outbox.db"
    lines, offsets, _ = base.read_new_records(path, 0)
    base.write_cursor("session", offsets[9], 10, finalized=True, home=tmp_path)
    telemetry_buffer.enqueue(
        {
            "harness": "claude-code",
            "session_id": "session",
            "start_offset": 10,
            "lines": [lines[10]],
            "end_byte_offsets": [offsets[10]],
            "final": True,
        },
        destination="http://server",
        user_id="user",
        db_path=db,
    )
    real_enqueue = telemetry_buffer.enqueue
    interrupted = False

    def crash_on_second_chunk(payload, **kwargs):
        nonlocal interrupted
        if payload["start_offset"] == 500 and not interrupted:
            interrupted = True
            raise RuntimeError("interrupted spool")
        return real_enqueue(payload, **kwargs)

    monkeypatch.setattr(telemetry_buffer, "enqueue", crash_on_second_chunk)
    attempts = []

    def audit(payload, _config):
        attempts.append(payload["start_offset"])
        if payload["start_offset"] == 10:
            return {"acknowledged_line": 4, "acknowledged_offset": offsets[4], "repair_from_line": 5}
        return {
            "acknowledged_line": payload["start_offset"] + len(payload["lines"]) - 1,
            "acknowledged_offset": payload["end_byte_offsets"][-1],
        }

    with pytest.raises(RuntimeError, match="interrupted spool"):
        base.drain_session_source(
            source, config(), hook_event="Stop", final=True, home=tmp_path, db_path=db, post=audit
        )
    monkeypatch.setattr(telemetry_buffer, "enqueue", real_enqueue)
    assert telemetry_buffer.stats(db_path=db)["pending"] == 2
    assert base.drain_session_source(
        source, config(), hook_event="Stop", final=True, home=tmp_path, db_path=db, post=audit
    )
    assert attempts[-2:] == [0, 500]
    assert base.read_cursor_state("session", home=tmp_path) == (offsets[-1], 512, True)
    assert telemetry_buffer.stats(db_path=db)["pending"] == 0


def test_earlier_final_repair_without_source_does_not_starve_other_sessions(tmp_path: Path, monkeypatch):
    db = tmp_path / "outbox.db"
    for session_id, start in (("missing", 10), ("healthy", 0)):
        telemetry_buffer.enqueue(
            {
                "harness": "claude-code",
                "session_id": session_id,
                "start_offset": start,
                "lines": ["record"],
                "end_byte_offsets": [100 if start else 6],
                "final": session_id == "missing",
                "total_line_count": 11 if start else 1,
                "total_offset": 100 if start else 6,
                "session_hash": "final-audit" if start else None,
            },
            destination="http://server",
            user_id="user",
            db_path=db,
        )
    base.write_cursor("missing", 90, 10, finalized=True, home=tmp_path)
    posted = []

    def post(payload, _config):
        posted.append(payload["session_id"])
        if payload["session_id"] == "missing":
            # The remote offset is not a local source boundary and cannot be trusted.
            return {"acknowledged_line": 4, "acknowledged_offset": 42, "repair_from_line": 5}
        return {"acknowledged_line": 0, "acknowledged_offset": 6}

    repairs = []
    assert not base.drain_outbox(config(), home=tmp_path, db_path=db, post=post, repairs=repairs)
    assert posted == ["missing", "healthy"]
    assert repairs == [("claude-code", "missing")]
    assert base.read_cursor_state("missing", home=tmp_path) == (0, 0, False)
    assert base.read_cursor("healthy", home=tmp_path) == (6, 1)
    pending = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
    assert len(pending) == 1 and pending[0].session_id == "missing" and pending[0].final
    assert pending[0].payload["session_hash"] == "final-audit"
    assert pending[0].payload["total_offset"] == 100
    assert pending[0].attempts == 1 and pending[0].retry_after > 0

    assert not base.drain_outbox(config(), home=tmp_path, db_path=db, post=post)
    assert posted == ["missing", "healthy"]
    telemetry_buffer.enqueue(
        {
            "harness": "claude-code",
            "session_id": "healthy-later",
            "start_offset": 0,
            "lines": ["record"],
            "end_byte_offsets": [6],
        },
        destination="http://server",
        user_id="user",
        db_path=db,
    )
    monkeypatch.setattr(telemetry_buffer.time, "time", lambda: 10**12)
    assert not base.drain_outbox(config(), home=tmp_path, db_path=db, post=post)
    assert posted == ["missing", "healthy", "missing", "healthy-later"]
    assert not base.drain_outbox(config(), home=tmp_path, db_path=db, post=post)
    assert posted == ["missing", "healthy", "missing", "healthy-later"]
    pending = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
    assert len(pending) == 1 and pending[0].final and pending[0].attempts == 2


@pytest.mark.parametrize("source_present", [False, True])
def test_line_zero_final_repair_keeps_audit_until_replay(tmp_path: Path, monkeypatch, source_present: bool):
    disable_payload_metadata(monkeypatch)
    db = tmp_path / "outbox.db"
    path = tmp_path / "session.jsonl"
    if source_present:
        path.write_text('{"n":0}\n')
    source = SessionSource("claude-code", "session", path)
    telemetry_buffer.enqueue(
        {
            "harness": "claude-code",
            "session_id": "session",
            "start_offset": 0,
            "lines": ['{"n":0}'],
            "end_byte_offsets": [8],
            "final": True,
            "total_line_count": 1,
            "total_offset": 8,
            "session_hash": "old-audit",
        },
        destination="http://server",
        user_id="user",
        db_path=db,
    )
    base.write_cursor("session", 8, 1, finalized=True, home=tmp_path)
    assert not base.drain_outbox(
        config(),
        home=tmp_path,
        db_path=db,
        post=lambda *_: {"acknowledged_line": -1, "acknowledged_offset": 0, "repair_from_line": 0},
    )
    queued = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
    assert len(queued) == 1 and queued[0].final and queued[0].payload["session_hash"] == "old-audit"
    assert base.read_cursor_state("session", home=tmp_path) == (0, 0, False)
    if not source_present:
        assert not base.drain_session_source(
            source, config(), hook_event="Stop", final=True, spool_only=True, home=tmp_path, db_path=db
        )
        assert telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)[0].final
    else:
        assert base.drain_session_source(
            source, config(), hook_event="Stop", final=True, spool_only=True, home=tmp_path, db_path=db
        )
        replayed = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
        assert len(replayed) == 1 and replayed[0].final
        assert replayed[0].payload["session_hash"] == base.hash_session_source(path)[0]


def test_line_zero_final_survives_crash_before_replay_spool(tmp_path: Path, monkeypatch):
    disable_payload_metadata(monkeypatch)
    db = tmp_path / "outbox.db"
    path = tmp_path / "session.jsonl"
    path.write_text('{"n":0}\n')
    source = SessionSource("claude-code", "session", path)
    base.write_cursor("session", 8, 1, finalized=True, home=tmp_path)
    telemetry_buffer.enqueue(
        {
            "harness": "claude-code",
            "session_id": "session",
            "start_offset": 0,
            "lines": ['{"n":0}'],
            "end_byte_offsets": [8],
            "final": True,
            "total_line_count": 1,
            "session_hash": "audit",
        },
        destination="http://server",
        user_id="user",
        db_path=db,
    )
    base.drain_outbox(
        config(),
        home=tmp_path,
        db_path=db,
        post=lambda *_: {"acknowledged_line": -1, "acknowledged_offset": 0, "repair_from_line": 0},
    )

    def crash(*_args, **_kwargs):
        raise RuntimeError("before spool")

    monkeypatch.setattr(telemetry_buffer, "enqueue", crash)
    with pytest.raises(RuntimeError, match="before spool"):
        base.drain_session_source(
            source, config(), hook_event="Stop", final=True, spool_only=True, home=tmp_path, db_path=db
        )
    queued = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
    assert len(queued) == 1 and queued[0].final and queued[0].payload["session_hash"] == "audit"


def test_replayed_intermediate_final_is_demoted_after_growth(tmp_path: Path, monkeypatch):
    disable_payload_metadata(monkeypatch)
    db = tmp_path / "outbox.db"
    path = tmp_path / "session.jsonl"
    path.write_text("".join(f'{{"n":{n}}}\n' for n in range(1500)))
    source = SessionSource("claude-code", "session", path)
    lines, offsets, _ = base.read_new_records(path, 0)
    base.write_cursor("session", offsets[999], 1000, finalized=True, home=tmp_path)
    telemetry_buffer.enqueue(
        {
            "harness": "claude-code",
            "session_id": "session",
            "start_offset": 500,
            "lines": lines[500:1000],
            "end_byte_offsets": offsets[500:1000],
            "final": True,
            "total_line_count": 1000,
            "total_offset": offsets[999],
        },
        destination="http://server",
        user_id="user",
        db_path=db,
    )
    posted = []

    def audit(payload, _config):
        posted.append((payload["start_offset"], payload.get("final", False), payload.get("total_line_count")))
        if len(posted) == 1:
            return {"acknowledged_line": -1, "acknowledged_offset": 0, "repair_from_line": 0}
        return {
            "acknowledged_line": payload["start_offset"] + len(payload["lines"]) - 1,
            "acknowledged_offset": payload["end_byte_offsets"][-1],
        }

    assert base.drain_session_source(
        source, config(), hook_event="Stop", final=True, home=tmp_path, db_path=db, post=audit
    )
    assert posted == [(500, True, 1000), (0, False, None), (500, False, None), (1000, True, 1500)]
    assert base.read_cursor_state("session", home=tmp_path) == (offsets[-1], 1500, True)


def test_earlier_repair_rejects_far_ahead_offset(tmp_path: Path):
    db = tmp_path / "outbox.db"
    telemetry_buffer.enqueue(
        {
            "harness": "claude-code",
            "session_id": "s",
            "start_offset": 10,
            "lines": ["record"],
            "end_byte_offsets": [100],
            "final": True,
        },
        destination="http://server",
        user_id="user",
        db_path=db,
    )
    base.write_cursor("s", 90, 10, finalized=True, home=tmp_path)
    assert not base.drain_outbox(
        config(),
        home=tmp_path,
        db_path=db,
        post=lambda *_: {"acknowledged_line": 4, "acknowledged_offset": 999, "repair_from_line": 5},
    )
    assert base.read_cursor_state("s", home=tmp_path) == (90, 10, True)
    assert telemetry_buffer.stats(db_path=db)["pending"] == 1


def test_server_commit_before_local_delete_is_safe_to_retry(tmp_path: Path, monkeypatch):
    disable_payload_metadata(monkeypatch)
    source_path = tmp_path / "session.jsonl"
    source_path.write_text('{"type":"system","content":"one"}\n')
    db = tmp_path / "outbox.db"
    source = SessionSource("claude-code", "session", source_path)
    posts = 0

    base.drain_session_source(
        source,
        config(),
        hook_event="UserPromptSubmit",
        home=tmp_path,
        db_path=db,
        post=lambda _payload, _config: None,
    )

    real_acknowledge = telemetry_buffer.acknowledge

    def crash_after_server_ack(**_kwargs):
        raise RuntimeError("process died before local delete")

    monkeypatch.setattr(telemetry_buffer, "acknowledge", crash_after_server_ack)

    def server_ack(payload, _config):
        nonlocal posts
        posts += 1
        return {
            "acknowledged_line": payload["start_offset"] + len(payload["lines"]) - 1,
            "acknowledged_offset": payload["end_byte_offsets"][-1],
        }

    with pytest.raises(RuntimeError):
        base.drain_outbox(config(), home=tmp_path, db_path=db, post=server_ack)

    assert telemetry_buffer.stats(db_path=db)["pending"] == 1
    monkeypatch.setattr(telemetry_buffer, "acknowledge", real_acknowledge)
    assert base.drain_outbox(config(), home=tmp_path, db_path=db, post=server_ack)
    assert posts == 2
    assert telemetry_buffer.stats(db_path=db)["pending"] == 0


def test_outbox_is_drained_before_new_source_batch(tmp_path: Path, monkeypatch):
    disable_payload_metadata(monkeypatch)
    db = tmp_path / "outbox.db"
    telemetry_buffer.enqueue(
        {
            "session_id": "older",
            "harness": "claude-code",
            "lines": ['{"type":"system","content":"old"}'],
            "start_offset": 0,
            "end_byte_offsets": [10],
        },
        destination="http://server",
        user_id="user",
        db_path=db,
    )
    source_path = tmp_path / "new.jsonl"
    source_path.write_text('{"type":"system","content":"new"}\n')
    order: list[str] = []

    def acknowledge(payload, _config):
        order.append(payload["session_id"])
        return {
            "acknowledged_line": payload["start_offset"] + len(payload["lines"]) - 1,
            "acknowledged_offset": payload["end_byte_offsets"][-1],
        }

    assert base.drain_session_source(
        SessionSource("claude-code", "new", source_path),
        config(),
        hook_event="UserPromptSubmit",
        home=tmp_path,
        db_path=db,
        post=acknowledge,
    )
    assert order == ["older", "new"]


def _install_http_transport(monkeypatch, handler) -> None:
    import httpx

    client_class = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client_class(transport=transport, **kwargs))


def test_cursor_state_defaults_to_home_and_validates_entries(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    base.write_cursor("session", 10, 2, finalized=True)
    base.write_cursor("session", 20, 3)
    assert base.read_cursor_status("session") == (20, 3, True, True)

    base.write_cursor("session", 5, 1, preserve_finalized=False)
    assert base.read_cursor_status("session") == (5, 1, False, True)

    state_file = tmp_path / ".observal" / "sync_state.json"
    state_file.write_text(json.dumps({"session": {"offset": -1, "line_count": 1}}))
    assert base.read_cursor_status("session") == (0, 0, False, False)

    state_file.write_text("not-json")
    assert base.read_cursor_status("session") == (0, 0, False, False)


def test_jsonl_readers_handle_symlinks_corruption_offsets_and_partial_records(tmp_path: Path):
    source = tmp_path / "source.jsonl"
    source.write_bytes(b'{"ok":1}\r\nnot-json\n\xff\npartial')
    link = tmp_path / "linked.jsonl"
    link.symlink_to(source)

    assert base.read_new_records(link, 0) == (['{"ok":1}', "not-json", "�"], [10, 19, 21], 21)
    assert base.read_new_lines(link, 10) == (["not-json", "�"], 11)

    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    assert base.read_new_records(empty, 0) == ([], [], 0)

    partial = tmp_path / "partial.jsonl"
    partial.write_text("unfinished")
    assert base.read_new_records(partial, 0) == ([], [], 0)

    with pytest.raises(FileNotFoundError):
        base.read_new_records(tmp_path / "missing.jsonl", 0)


def test_session_hash_uses_complete_nonempty_records(tmp_path: Path):
    source = tmp_path / "session.jsonl"
    source.write_text('{"a":1}\n\nnot-json\ntrailing')

    assert base.hash_session_source(source) == (
        "43e55b074ecfbeee48dc828e6c4b0eb5648f73ce319f71fd4391c50673220364",
        2,
    )


def test_load_config_expands_home_trims_values_and_prefers_api_key(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    config_path = tmp_path / ".observal" / "config.json"
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps(
            {
                "server_url": " https://server.example/ ",
                "api_key": " long-token ",
                "access_token": "short-token",
                "refresh_token": " refresh ",
                "user_id": 42,
            }
        )
    )

    assert base.load_config() == {
        "server_url": "https://server.example/",
        "access_token": "long-token",
        "refresh_token": "refresh",
        "user_id": "42",
        "_config_path": str(config_path),
    }

    assert base.load_config(tmp_path / "missing-home") is None
    config_path.write_text("not-json")
    assert base.load_config(tmp_path) is None
    config_path.write_text(json.dumps({"server_url": "https://server.example", "access_token": ""}))
    assert base.load_config(tmp_path) is None


def test_load_config_rejects_an_unsupported_top_level_shape_softly(tmp_path: Path):
    config_path = tmp_path / ".observal" / "config.json"
    config_path.parent.mkdir()
    config_path.write_text("[]")

    assert base.load_config(tmp_path) is None


def test_refresh_access_token_updates_the_config_and_tolerates_persist_failure(tmp_path: Path, monkeypatch):
    import httpx

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"access_token": "fresh", "refresh_token": "rotated"})

    _install_http_transport(monkeypatch, handler)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"access_token": "old", "refresh_token": "refresh"}))

    assert base._refresh_access_token("https://server.example/", "refresh", str(config_path)) == "fresh"
    assert json.loads(config_path.read_text()) == {"access_token": "fresh", "refresh_token": "rotated"}
    assert requests[0].url == httpx.URL("https://server.example/api/v1/auth/token/refresh")
    assert json.loads(requests[0].content) == {"refresh_token": "refresh"}

    assert base._refresh_access_token("https://server.example", "refresh", str(tmp_path / "missing.json")) == "fresh"


@pytest.mark.parametrize("failure", ["rejected", "missing-token", "offline"])
def test_refresh_access_token_failures_are_soft(monkeypatch, failure: str):
    import httpx

    def handler(request):
        if failure == "offline":
            raise httpx.ConnectError("offline", request=request)
        if failure == "rejected":
            return httpx.Response(503)
        return httpx.Response(200, json={})

    _install_http_transport(monkeypatch, handler)

    assert base._refresh_access_token("https://server.example", "refresh", "/missing") is None


def test_rejected_refresh_token_is_not_retried_until_a_new_login(tmp_path: Path, monkeypatch):
    import httpx

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(401, json={"detail": "Refresh token has been revoked or expired"})

    _install_http_transport(monkeypatch, handler)
    config_path = tmp_path / ".observal" / "config.json"
    config_path.parent.mkdir()
    config = {"server_url": "https://server.example", "access_token": "old", "refresh_token": "revoked"}
    config_path.write_text(json.dumps(config))

    assert not base.login_expired(tmp_path)
    for _ in range(3):
        assert base._refresh_access_token("https://server.example", "revoked", str(config_path)) is None
    assert len(requests) == 1
    assert base.login_expired(tmp_path)

    # A new login stores a different refresh token, which is tried again.
    config_path.write_text(json.dumps({**config, "refresh_token": "new-login"}))
    assert not base.login_expired(tmp_path)
    assert base._refresh_access_token("https://server.example", "new-login", str(config_path)) is None
    assert len(requests) == 2


@pytest.mark.parametrize("expired", [True, False])
def test_cli_warns_when_hooks_found_the_login_expired(monkeypatch, capsys, expired: bool):
    from observal_cli import main

    monkeypatch.setattr(base, "login_expired", lambda: expired)
    main._warn_if_login_expired()

    assert ("observal auth login" in capsys.readouterr().err) is expired


def test_post_to_server_refreshes_once_and_returns_the_exact_acknowledgement(monkeypatch):
    import httpx

    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={"acknowledged_line": 3, "acknowledged_offset": 99})

    _install_http_transport(monkeypatch, handler)
    refreshes = []
    monkeypatch.setattr(
        base,
        "_refresh_access_token",
        lambda server, token, path: refreshes.append((server, token, path)) or "fresh-token",
    )
    config_data = {"refresh_token": "refresh", "_config_path": "/config.json"}
    payload = {"session_id": "session-long-id", "lines": ["one", "two"]}

    assert base.post_to_server_ack("https://server.example/", "old-token", payload, config=config_data) == {
        "acknowledged_line": 3,
        "acknowledged_offset": 99,
    }
    assert refreshes == [("https://server.example/", "refresh", "/config.json")]
    assert config_data["access_token"] == "fresh-token"
    assert [request.headers["Authorization"] for request in requests] == ["Bearer old-token", "Bearer fresh-token"]
    assert all(request.url == httpx.URL("https://server.example/api/v1/ingest/session") for request in requests)


@pytest.mark.parametrize("failure", ["rejected", "invalid-ack", "offline"])
def test_post_to_server_transient_and_malformed_responses_are_soft(monkeypatch, failure: str):
    import httpx

    def handler(request):
        if failure == "offline":
            raise httpx.ConnectError("offline", request=request)
        if failure == "rejected":
            return httpx.Response(503)
        return httpx.Response(200, json={"acknowledged_line": "zero"})

    _install_http_transport(monkeypatch, handler)

    if failure == "rejected":
        with pytest.raises(base.SessionServerError):
            base.post_to_server_ack("https://server.example", "token", {"session_id": "session", "lines": []})
    else:
        assert (
            base.post_to_server_ack("https://server.example", "token", {"session_id": "session", "lines": []}) is None
        )


def test_get_server_checkpoint_refreshes_and_preserves_source_identity(tmp_path: Path, monkeypatch):
    import httpx

    source = SessionSource("cursor", "child", tmp_path / "child.jsonl")
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={"acknowledged_line": 4, "acknowledged_offset": 80})

    _install_http_transport(monkeypatch, handler)
    monkeypatch.setattr(base, "_refresh_access_token", lambda *_args: "fresh-token")
    config_data = {
        "server_url": "https://server.example/",
        "access_token": "old-token",
        "refresh_token": "refresh",
        "_config_path": "/config.json",
    }

    assert base.get_server_checkpoint(source, config_data) == {"acknowledged_line": 4, "acknowledged_offset": 80}
    assert config_data["access_token"] == "fresh-token"
    assert [request.url.params for request in requests] == [
        httpx.QueryParams({"session_id": "child", "harness": "cursor"}),
        httpx.QueryParams({"session_id": "child", "harness": "cursor"}),
    ]
    assert [request.headers["Authorization"] for request in requests] == ["Bearer old-token", "Bearer fresh-token"]


@pytest.mark.parametrize("failure", ["rejected", "invalid-ack", "offline"])
def test_get_server_checkpoint_failures_are_soft(tmp_path: Path, monkeypatch, failure: str):
    import httpx

    def handler(request):
        if failure == "offline":
            raise httpx.ConnectError("offline", request=request)
        if failure == "rejected":
            return httpx.Response(503)
        return httpx.Response(200, json={"acknowledged_line": "four"})

    _install_http_transport(monkeypatch, handler)
    source = SessionSource("kiro", "session", tmp_path / "session.jsonl")

    assert base.get_server_checkpoint(source, {"server_url": "https://server", "access_token": "token"}) is None


def test_get_server_checkpoint_requires_server_and_token(tmp_path: Path):
    source = SessionSource("kiro", "session", tmp_path / "session.jsonl")

    assert base.get_server_checkpoint(source, {}) is None
    assert base.get_server_checkpoint(source, {"server_url": "https://server"}) is None


def test_checkpoint_offsets_and_recovery_fallbacks_are_exact(tmp_path: Path):
    source_path = tmp_path / "session.jsonl"
    source_path.write_bytes(b"one\n\ntwo\n")

    assert base._checkpoint_byte_offset(source_path, 0, 0) == 0
    assert base._checkpoint_byte_offset(source_path, 1, 1) is None
    assert base._checkpoint_byte_offset(source_path, 99, 0) is None
    assert base._checkpoint_byte_offset(tmp_path / "missing.jsonl", 1, 0) is None

    base.write_cursor("custom-key", 4, 1, home=tmp_path)
    source = SessionSource("claude-code", "session", source_path, cursor_key="custom-key")
    assert base.recover_cursor_from_server(source, config(), home=tmp_path, fetch=lambda *_args: None) == (4, 1)
    assert base.recover_cursor_from_server(SessionSource("claude-code", "session"), config(), home=tmp_path) is None


def test_drain_outbox_rejects_incomplete_configuration_and_partial_acknowledgement(tmp_path: Path):
    db = tmp_path / "outbox.db"
    assert base.drain_outbox({}, home=tmp_path, db_path=db) is False

    telemetry_buffer.enqueue(
        {
            "harness": "claude-code",
            "session_id": "session",
            "lines": ["one"],
            "start_offset": 0,
            "end_byte_offsets": [4],
        },
        destination="http://server",
        user_id="user",
        db_path=db,
    )

    assert not base.drain_outbox(
        config(),
        home=tmp_path,
        db_path=db,
        post=lambda *_args: {"acknowledged_line": -1, "acknowledged_offset": 0},
    )
    pending = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
    assert [(item.session_id, item.start_line, item.end_line) for item in pending] == [("session", 0, 0)]
    assert base.read_cursor("session", home=tmp_path) == (0, 0)


@pytest.mark.parametrize(
    "ack",
    [
        {"acknowledged_line": True},
        {"acknowledged_line": -2},
        {"acknowledged_line": 999, "acknowledged_offset": True},
        {"acknowledged_line": 999, "repair_from_line": True},
        {"acknowledged_line": 999, "repair_from_line": 1000},
    ],
)
def test_invalid_200_ack_keeps_batch_and_cursor(tmp_path: Path, ack: dict):
    db = tmp_path / "outbox.db"
    telemetry_buffer.enqueue(
        {"harness": "claude-code", "session_id": "s", "lines": ["record"], "start_offset": 0, "end_byte_offsets": [10]},
        destination="http://server",
        user_id="user",
        db_path=db,
    )
    assert not base.drain_outbox(config(), home=tmp_path, db_path=db, post=lambda *_args: ack)
    assert base.read_cursor("s", home=tmp_path) == (0, 0)
    assert telemetry_buffer.stats(db_path=db)["pending"] == 1


def test_drain_session_source_rejects_missing_inputs_and_failed_recovery(tmp_path: Path, monkeypatch):
    disable_payload_metadata(monkeypatch)
    source_path = tmp_path / "session.jsonl"
    source_path.write_text("{}\n")
    db = tmp_path / "outbox.db"

    assert not base.drain_session_source(
        SessionSource("claude-code", "session"),
        config(),
        hook_event="Reconcile",
        db_path=db,
    )
    assert not base.drain_session_source(
        SessionSource("claude-code", "session", source_path),
        {"server_url": "", "user_id": "user"},
        hook_event="Reconcile",
        db_path=db,
    )
    assert not base.drain_session_source(
        SessionSource("claude-code", "session", source_path),
        config(),
        hook_event="Reconcile",
        recover_from_server=True,
        checkpoint_fetch=lambda *_args: {"acknowledged_line": 9, "acknowledged_offset": 999},
        home=tmp_path,
        db_path=db,
    )
    assert "server checkpoint does not match local source" in (tmp_path / ".observal" / "sync.log").read_text()


def test_blank_only_source_is_a_successful_spool_noop(tmp_path: Path, monkeypatch):
    disable_payload_metadata(monkeypatch)
    source_path = tmp_path / "blank.jsonl"
    source_path.write_text("\n \n")
    db = tmp_path / "outbox.db"

    assert base.drain_session_source(
        SessionSource("claude-code", "blank", source_path),
        config(),
        hook_event="Reconcile",
        spool_only=True,
        home=tmp_path,
        db_path=db,
    )
    assert telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db) == []
    assert base.read_cursor("blank", home=tmp_path) == (0, 0)


def test_empty_final_source_replays_once_after_an_integrity_repair(tmp_path: Path, monkeypatch):
    disable_payload_metadata(monkeypatch)
    source_path = tmp_path / "empty.jsonl"
    source_path.write_text("")
    db = tmp_path / "outbox.db"
    acknowledgements = [
        {"acknowledged_line": -1, "acknowledged_offset": 0, "repair_from_line": 0},
        {"acknowledged_line": -1, "acknowledged_offset": 0},
    ]

    assert base.drain_session_source(
        SessionSource("claude-code", "empty", source_path),
        config(),
        hook_event="Stop",
        final=True,
        home=tmp_path,
        db_path=db,
        post=lambda *_args: acknowledgements.pop(0),
    )
    assert acknowledgements == []
    assert base.read_cursor_state("empty", home=tmp_path) == (0, 0, True)
    assert telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db) == []


def test_build_payload_caches_layer_metadata_and_evicts_it_on_stop(monkeypatch):
    base._layer_hash_cache.clear()
    hashes = []
    monkeypatch.setattr(base, "_resolve_agent", lambda *_args, **_kwargs: ("agent-id", "1.2.3"))
    monkeypatch.setattr(
        base,
        "_compute_layer_hash_safe",
        lambda cwd, harness: hashes.append((cwd, harness)) or "layer-hash",
    )

    payload = base.build_payload(
        "session",
        ["one"],
        2,
        "UserPromptSubmit",
        2,
        cwd="/repo",
        parent_session_id="parent",
        harness="cursor",
    )
    assert payload == {
        "session_id": "session",
        "harness": "claude-code",
        "agent_id": "agent-id",
        "agent_version": "1.2.3",
        "layer_hash": "layer-hash",
        "lines": ["one"],
        "start_offset": 2,
        "hook_event": "UserPromptSubmit",
        "parent_session_id": "parent",
    }

    base.build_payload("session", ["two"], 3, "UserPromptSubmit", 3, cwd="/repo")
    stopped = base.build_payload("session", ["three"], 4, "Stop", 4, new_offset=90, cwd="/repo")
    assert stopped["total_line_count"] == 5
    assert stopped["total_offset"] == 90
    assert stopped["final"] is True
    assert hashes == [("/repo", "cursor"), ("/repo", "claude-code"), ("/repo", "claude-code")]
    assert ("session", "/repo", "claude-code") not in base._layer_hash_cache
    assert base._layer_hash_cache[("session", "/repo", "cursor")] == "layer-hash"


def test_layer_hash_and_canonical_checks_are_fail_soft(monkeypatch):
    from observal_cli import layer, lockfile

    hash_calls = []
    monkeypatch.setattr(
        layer,
        "ensure_local_snapshot",
        lambda **kwargs: hash_calls.append(kwargs) or "layer-hash",
    )
    assert base._compute_layer_hash_safe("/repo", "cursor") == "layer-hash"
    assert hash_calls == [{"harness": None, "project_dir": "/repo"}]

    monkeypatch.setattr(layer, "ensure_local_snapshot", lambda **_kwargs: (_ for _ in ()).throw(OSError("broken")))
    assert base._compute_layer_hash_safe("", "cursor") is None

    manifests = []
    drift_calls = []
    monkeypatch.setattr(lockfile, "read_registry_lockfile", lambda: (Path("lock.json"), {"lock_version": 2}))
    monkeypatch.setattr(layer, "_detect_active_harnesses", lambda: ["claude-code", "kiro"])
    monkeypatch.setattr(
        layer,
        "build_layer_manifest",
        lambda harness, include_content: manifests.append((harness, include_content)) or [harness],
    )
    monkeypatch.setattr(
        layer,
        "_compute_drift",
        lambda lock, current: drift_calls.append((lock, current)) or {"is_canonical": False},
    )

    assert base._is_layer_canonical() is False
    assert manifests == [("claude-code", False), ("kiro", False)]
    assert drift_calls == [
        (
            {"lock_version": 2},
            {"claude-code": ["claude-code"], "kiro": ["kiro"]},
        )
    ]

    monkeypatch.setattr(lockfile, "read_registry_lockfile", lambda: (_ for _ in ()).throw(OSError("broken")))
    assert base._is_layer_canonical() is None


def test_layer_snapshot_upload_skips_unchanged_and_saves_success(tmp_path: Path, monkeypatch):
    import httpx

    from observal_cli import layer

    decisions = iter([False, True])
    builds = []
    saved = []
    requests = []
    monkeypatch.setattr(layer, "needs_upload", lambda layer_hash: next(decisions))
    monkeypatch.setattr(layer, "was_uploaded_for", lambda *_args: True)
    monkeypatch.setattr(layer, "set_last_uploaded_hash", lambda *_args: None)
    monkeypatch.setattr(
        layer,
        "build_upload_payload",
        lambda **kwargs: builds.append(kwargs) or {"hash": "layer-hash"},
    )
    monkeypatch.setattr(layer, "save_local_snapshot", saved.append)

    def handler(request):
        requests.append(request)
        return httpx.Response(201)

    _install_http_transport(monkeypatch, handler)

    base._maybe_upload_layer_snapshot("https://server.example", "token", "layer-hash", "cursor", "/repo")
    assert requests == []

    base._maybe_upload_layer_snapshot("https://server.example/", "token", "layer-hash", "cursor", "/repo")
    assert builds == [{"project_dir": "/repo"}, {"project_dir": "/repo"}]
    assert saved == [{"hash": "layer-hash"}]
    assert requests[0].url == httpx.URL("https://server.example/api/v1/layer-snapshots")
    assert requests[0].headers["Authorization"] == "Bearer token"
    assert json.loads(requests[0].content) == {"hash": "layer-hash"}


def test_layer_snapshot_upload_declines_hash_payload_mismatch(monkeypatch):
    from observal_cli import layer

    monkeypatch.setattr(layer, "build_upload_payload", lambda **_kwargs: {"hash": "v2_" + "b" * 60})
    monkeypatch.setattr(layer, "needs_upload", lambda _hash: True)
    monkeypatch.setattr(layer, "save_local_snapshot", lambda _payload: pytest.fail("mismatch saved"))
    base._maybe_upload_layer_snapshot("https://server.example", "token", "v2_" + "a" * 60, "claude-code", "/repo")


@pytest.mark.parametrize("failure", ["rejected", "offline"])
def test_layer_snapshot_upload_failures_are_soft(monkeypatch, failure: str):
    import httpx

    from observal_cli import layer

    monkeypatch.setattr(layer, "needs_upload", lambda _layer_hash: True)
    monkeypatch.setattr(layer, "build_upload_payload", lambda *_args, **_kwargs: {"hash": "layer-hash"})
    monkeypatch.setattr(layer, "save_local_snapshot", lambda _payload: pytest.fail("failed upload was saved"))

    def handler(request):
        if failure == "offline":
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(503)

    _install_http_transport(monkeypatch, handler)

    base._maybe_upload_layer_snapshot("https://server", "token", "layer-hash", "cursor", "")


def test_explicit_identity_adapters_do_not_fall_back_without_an_agent_id(monkeypatch):
    from observal_cli import harness

    class ExplicitIdentityAdapter:
        @staticmethod
        def requires_explicit_agent_id() -> bool:
            return True

    monkeypatch.setattr(harness, "ensure_loaded", lambda: None)
    monkeypatch.setattr(harness, "get_adapter", lambda _harness: ExplicitIdentityAdapter())
    monkeypatch.delenv("OBSERVAL_AGENT_ID", raising=False)
    monkeypatch.delenv("OBSERVAL_AGENT_NAME", raising=False)

    assert base._resolve_agent("/repo", [], None, harness="strict") == (None, None)


def test_lockfile_lookup_helpers_cover_success_fallback_and_errors(monkeypatch):
    from observal_cli import lockfile
    from observal_shared import harness_registry

    calls = []
    entry = {"id": "agent-id", "name": "agent", "version": "1.0.0", "scope": "project"}
    monkeypatch.setattr(
        lockfile,
        "get_agent_by_id",
        lambda agent_id, harness=None: calls.append((agent_id, harness)) or entry,
    )
    assert base._lookup_lockfile_agent_by_id("agent-id", harness="cursor") is entry
    assert calls == [("agent-id", "cursor")]

    monkeypatch.setattr(lockfile, "get_agent_by_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("bad")))
    assert base._lookup_lockfile_agent_by_id("missing") is None

    first = entry | {"directory": "/first"}
    second = entry | {"id": "second", "directory": "/second"}
    data = {"harnesses": {"cursor": {"agents": [first, second]}}}
    monkeypatch.setattr(lockfile, "read_registry_lockfile", lambda: (Path("lock.json"), data))
    monkeypatch.setattr(harness_registry, "get_valid_harnesses", lambda: ["cursor"])

    assert base._lookup_lockfile_agent("/second") is second
    assert base._lookup_lockfile_agent("/different", agent_name="agent") is first

    monkeypatch.setattr(lockfile, "read_registry_lockfile", lambda: (_ for _ in ()).throw(OSError("bad")))
    assert base._lookup_lockfile_agent("/repo") is None


def test_agent_setting_lines_skip_irrelevant_and_malformed_json():
    lines = [
        "{}",
        "agent-setting is not json",
        json.dumps({"type": "other", "agent-setting": "ignored"}),
        json.dumps({"type": "agent-setting", "agentName": "selected-agent"}),
    ]

    assert base._parse_agent_from_lines(lines) == "selected-agent"
    assert base._parse_agent_from_lines([json.dumps({"type": "agent-setting"})]) is None


def test_log_error_uses_default_home_and_never_masks_the_original_failure(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    base.log_error("delivery failed")

    line = (tmp_path / ".observal" / "sync.log").read_text()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} delivery failed\n", line)

    not_a_directory = tmp_path / "file"
    not_a_directory.write_text("content")
    base.log_error("ignored", home=not_a_directory)


def test_layer_snapshot_upload_passes_the_servers_cli_version_check(monkeypatch):
    """Regression: the hook's snapshot upload sent no X-Observal-CLI-Version, so the server's
    parity middleware answered 426 and presence was never published from real sessions."""
    import sys

    import httpx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "observal-server"))
    import version as server_version
    from middleware import configure_version_middleware
    from observal_cli import client as cli_client
    from observal_cli import layer

    monkeypatch.setattr(server_version, "get_server_version", lambda: "1.13.1")
    monkeypatch.setattr(cli_client, "_get_cli_version", lambda: "1.13.1")
    app = FastAPI()
    configure_version_middleware(app)
    app.post("/api/v1/layer-snapshots", status_code=201)(lambda: {"stored": True})
    server = TestClient(app)

    def handler(request):
        # A python-httpx user agent and a bearer token: exactly what the middleware checks.
        response = server.post(request.url.path, headers=dict(request.headers), content=request.content)
        return httpx.Response(response.status_code, content=response.content)

    saved = []
    monkeypatch.setattr(layer, "needs_upload", lambda _hash: True)
    monkeypatch.setattr(layer, "build_upload_payload", lambda **_kwargs: {"hash": "layer-hash"})
    monkeypatch.setattr(layer, "save_local_snapshot", saved.append)
    monkeypatch.setattr(layer, "set_last_uploaded_hash", lambda *_args: None)
    _install_http_transport(monkeypatch, handler)

    base._maybe_upload_layer_snapshot("https://server.example", "token", "layer-hash", "claude-code", "/repo")
    assert saved == [{"hash": "layer-hash"}], "the server accepted the upload"


def test_recovered_sessions_hash_the_recorded_project_or_send_no_layer_hash(tmp_path, monkeypatch):
    """Regression: recovery rebuilt Claude Code sources with no cwd, so the payload carried a
    user-scope-only layer hash (and the hook uploaded an empty snapshot) for a project session."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(base, "_compute_layer_hash_safe", lambda cwd, harness: f"hash-of:{cwd}")
    monkeypatch.setattr(base, "_resolve_agent", lambda *args, **kwargs: (None, None))
    monkeypatch.setattr(base, "_capabilities_for_session", lambda *args, **kwargs: None)

    recorded = [json.dumps({"type": "user", "cwd": str(project), "message": {"content": "hi"}})]
    payload = base.build_payload("s1", recorded, 0, "CrashRecovery", 0, cwd="")
    assert payload["layer_hash"] == f"hash-of:{project}"

    transcript = tmp_path / "s2.jsonl"
    transcript.write_text("\n".join(recorded) + "\n")
    later_chunk = [json.dumps({"type": "assistant", "message": {"content": "ok"}})]
    payload = base.build_payload("s2", later_chunk, 5, "CrashRecovery", 5, cwd="", session_jsonl=transcript)
    assert payload["layer_hash"] == f"hash-of:{project}", "found in the transcript's earlier records"

    unknown = [json.dumps({"type": "user", "cwd": str(tmp_path / "gone")})]
    assert base.build_payload("s3", unknown, 0, "CrashRecovery", 0, cwd="")["layer_hash"] is None

    live = base.build_payload("s4", later_chunk, 0, "UserPromptSubmit", 0, cwd="")
    assert live["layer_hash"] == "hash-of:", "live hook paths are unchanged"


@pytest.mark.parametrize("hook_event", ["Reconcile", "CrashRecovery"])
def test_sessions_rebuilt_from_disk_hash_and_upload_the_recorded_project(tmp_path, monkeypatch, hook_event):
    """Regression: `observal reconcile` (and recovery) rebuilt Claude Code sources with no cwd. Reconcile
    hashed "no project", so project-scope components were never pinned; and both uploaded the snapshot
    for no project, so a session the live hooks never saw had no snapshot for its hash at all."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(base, "_get_cached_layer_hash", lambda _session, cwd, _harness: f"hash-of:{cwd}")
    monkeypatch.setattr(base, "_resolve_agent", lambda *args, **kwargs: (None, None))
    monkeypatch.setattr(base, "_capabilities_for_session", lambda *args, **kwargs: None)
    monkeypatch.setattr(base, "drain_outbox", lambda *args, **kwargs: True)
    uploads: list[tuple[str, str]] = []
    monkeypatch.setattr(
        base,
        "_maybe_upload_layer_snapshot",
        lambda _url, _token, layer_hash, _harness, cwd, _config: uploads.append((layer_hash, cwd)),
    )
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps({"type": "user", "cwd": str(project), "message": {"content": "hi"}}) + "\n")
    db = tmp_path / "outbox.db"
    source = SessionSource("claude-code", "rebuilt", transcript)  # no cwd, as discovery builds it
    assert base.drain_session_source(source, config(), hook_event=hook_event, home=tmp_path, db_path=db)
    (item,) = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)
    assert item.payload["layer_hash"] == f"hash-of:{project}"
    assert uploads == [(f"hash-of:{project}", str(project))], "the snapshot uploaded is the one the payload names"
