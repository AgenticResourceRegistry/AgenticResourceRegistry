# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
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


def test_422_validation_reason_is_bounded_and_safe_in_logs_and_quarantine(tmp_path: Path, monkeypatch):
    import httpx

    secret = "sensitive-session-content-and-token"
    detail = {
        "detail": [
            {
                "loc": ["body", "lines", 0, secret],
                "type": "string_too_long",
                "msg": f"Value error: {secret}",
                "input": secret,
                "ctx": {"max_length": 1_048_576, "error": secret},
            }
        ]
    }
    _install_http_transport(monkeypatch, lambda _request: httpx.Response(422, json=detail))
    db = tmp_path / "outbox.db"
    telemetry_buffer.enqueue(
        {"harness": "claude-code", "session_id": "bad", "lines": ["bad"], "start_offset": 0, "end_byte_offsets": [3]},
        destination="https://server.example",
        user_id="user",
        db_path=db,
    )
    cfg = {**config(), "server_url": "https://server.example"}
    assert base.drain_outbox(cfg, home=tmp_path, db_path=db)

    rejection = json.loads(db.with_suffix(".rejected.jsonl").read_text())
    assert "body.lines[0].field: string_too_long (max_length=1048576)" in rejection["reason"]
    log = (tmp_path / ".observal" / "sync.log").read_text()
    assert "string_too_long" in log
    assert secret not in rejection["reason"] + log


@pytest.mark.parametrize(
    "detail",
    [
        "contains secret",
        [{"loc": ["body"], "type": "bad secret"}],
        [{"loc": ["body", "lines", 0], "type": "sk_live_supersecrettoken"}],
    ],
)
def test_422_rejection_with_unstructured_or_untrusted_detail_stays_generic(monkeypatch, detail):
    import httpx

    _install_http_transport(monkeypatch, lambda _request: httpx.Response(422, json={"detail": detail}))
    with pytest.raises(base.PermanentIngestRejectionError) as exc:
        base.post_to_server_ack("https://server.example", "token", {"session_id": "bad", "lines": []})
    assert exc.value.reason is None
    assert "secret" not in str(exc.value)


def test_untrusted_validation_code_never_reaches_quarantine_or_sync_log(tmp_path: Path, monkeypatch):
    import httpx

    secret = "sk_live_supersecrettoken"
    _install_http_transport(
        monkeypatch,
        lambda _request: httpx.Response(422, json={"detail": [{"loc": ["body", "lines", 0], "type": secret}]}),
    )
    db = tmp_path / "outbox.db"
    telemetry_buffer.enqueue(
        {"harness": "claude-code", "session_id": "bad", "lines": ["bad"], "start_offset": 0, "end_byte_offsets": [3]},
        destination="https://server.example",
        user_id="user",
        db_path=db,
    )
    assert base.drain_outbox({**config(), "server_url": "https://server.example"}, home=tmp_path, db_path=db)
    quarantine = db.with_suffix(".rejected.jsonl").read_text()
    sync_log = (tmp_path / ".observal" / "sync.log").read_text()
    assert "status 422" in quarantine
    assert secret not in quarantine + sync_log


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
    assert base.drain_outbox(config(), home=tmp_path, db_path=db, post=post, rejections=rejections)
    assert rejections == [("claude-code", "bad", 422)]
    assert telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db) == []
    assert telemetry_buffer.stats(db_path=db)["failed"] == 1
    rejected = json.loads(db.with_suffix(".rejected.jsonl").read_text())
    assert rejected["session_id"] == "bad"
    assert rejected["reason"].endswith("status 422")


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

    assert base.post_to_server_ack("https://server.example", "token", {"session_id": "session", "lines": []}) is None


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
