# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Session batches always fit the server's request and line limits."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from observal_cli import telemetry_buffer
from observal_cli.harness import SessionSource
from observal_cli.sessions import base

if TYPE_CHECKING:
    from pathlib import Path


def _config() -> dict:
    return {"server_url": "http://server", "access_token": "token", "user_id": "user"}


def _spool(tmp_path: Path, monkeypatch, records: list[dict]) -> list:
    monkeypatch.setattr(base, "_resolve_agent", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(base, "_get_cached_layer_hash", lambda *_args, **_kwargs: None)
    source_path = tmp_path / "session.jsonl"
    source_path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    db = tmp_path / "outbox.db"
    assert base.drain_session_source(
        SessionSource("claude-code", "session", source_path),
        _config(),
        hook_event="Stop",
        final=True,
        spool_only=True,
        home=tmp_path,
        db_path=db,
    )
    return telemetry_buffer.pending(destination="http://server", user_id="user", limit=100, db_path=db)


def test_short_lines_are_sent_unchanged():
    line = '{"type":"user","message":{"content":"hi"}}'

    assert base.fit_line(line) == line


def test_oversized_line_keeps_its_structure_without_the_long_value():
    image = "A" * 1_500_000
    line = json.dumps(
        {"type": "user", "message": {"content": [{"type": "image", "data": image}, {"type": "text", "text": "see"}]}}
    )

    fitted = base.fit_line(line)

    assert len(fitted) <= base.MAX_LINE_CHARS
    record = json.loads(fitted)
    assert record["type"] == "user"
    assert record["message"]["content"][1] == {"type": "text", "text": "see"}
    assert record["message"]["content"][0]["data"] == "[observal: 1500000 characters omitted]"


def test_oversized_text_that_is_not_json_becomes_a_marker():
    fitted = base.fit_line("x" * (base.MAX_LINE_CHARS + 1))

    assert json.loads(fitted) == {"observal_omitted_characters": base.MAX_LINE_CHARS + 1}


def test_large_sessions_are_split_into_batches_under_the_byte_limit(tmp_path: Path, monkeypatch):
    # Twelve 900 kB lines: each fits the line limit, together they exceed one request.
    records = [{"type": "assistant", "n": n, "text": "y" * 900_000} for n in range(12)]

    batches = _spool(tmp_path, monkeypatch, records)

    assert len(batches) > 1
    assert all(len(json.dumps(item.payload).encode()) < 10 * 1024 * 1024 for item in batches)
    starts = [item.payload["start_offset"] for item in batches]
    sizes = [len(item.payload["lines"]) for item in batches]
    assert starts == [sum(sizes[:i]) for i in range(len(sizes))]
    assert sum(sizes) == 12
    assert [bool(item.payload.get("final")) for item in batches] == [False] * (len(batches) - 1) + [True]


def test_final_hash_covers_the_lines_as_sent(tmp_path: Path, monkeypatch):
    records = [{"type": "user", "n": 0}, {"type": "assistant", "signature": "s" * 1_200_000}, {"type": "user", "n": 2}]

    batches = _spool(tmp_path, monkeypatch, records)

    sent = [line for item in batches for line in item.payload["lines"]]
    assert all(len(line) <= base.MAX_LINE_CHARS for line in sent)
    # The server hashes each stored line the same way when it checks a final batch.
    hasher = hashlib.sha256()
    for line in sent:
        hasher.update(hashlib.sha256(line.encode()).hexdigest().encode())
        hasher.update(b"\n")
    assert batches[-1].payload["session_hash"] == hasher.hexdigest()
