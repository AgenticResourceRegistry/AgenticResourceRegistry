# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""observal reconcile finds Pi sessions in Pi's per-project session directories."""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING

from observal_cli.harness.pi import PiAdapter

if TYPE_CHECKING:
    from pathlib import Path


def _session(project_dir: Path, name: str, header: dict | None) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / name
    first = json.dumps(header) if header is not None else "not json"
    path.write_text(first + "\n" + json.dumps({"type": "message"}) + "\n", encoding="utf-8")
    return path


def test_discovers_recent_pi_sessions_by_header_id(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PI_CODING_AGENT_DIR", raising=False)
    sessions = tmp_path / ".pi" / "agent" / "sessions"
    repo = sessions / "--work-repo--"
    older = _session(repo, "2026-10-18T10-00-00-000Z_older.jsonl", {"type": "session", "id": "older", "cwd": "/work"})
    newer = _session(
        sessions / "--home-me--", "2026-10-19T10-00-00-000Z_newer.jsonl", {"type": "session", "id": "newer"}
    )
    stale = _session(repo, "2026-09-01T10-00-00-000Z_stale.jsonl", {"type": "session", "id": "stale"})
    _session(repo, "broken.jsonl", None)
    _session(repo, "no-header.jsonl", {"type": "message"})
    now = time.time()
    os.utime(older, (now - 3600, now - 3600))
    os.utime(stale, (now - 30 * 86400, now - 30 * 86400))

    sources = PiAdapter().discover_session_sources(home=tmp_path, since_hours=24)

    assert [(s.harness, s.session_id, s.path, s.cwd) for s in sources] == [
        ("pi", "newer", newer, ""),
        ("pi", "older", older, "/work"),
    ]


def test_discovery_follows_pi_coding_agent_dir(tmp_path: Path, monkeypatch):
    agent_dir = tmp_path / "custom-agent"
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent_dir))
    _session(agent_dir / "sessions" / "--repo--", "x_one.jsonl", {"type": "session", "id": "one"})

    assert [s.session_id for s in PiAdapter().discover_session_sources()] == ["one"]
    assert PiAdapter().discover_session_sources(home=tmp_path / "empty") == []
