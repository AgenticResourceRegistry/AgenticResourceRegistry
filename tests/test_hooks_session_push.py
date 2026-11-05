# SPDX-FileCopyrightText: 2026 Dheirav Prakash <dheirav2005@gmail.com>
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
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from observal_cli.sessions import base
from observal_cli.sessions.agent_marker import read_agent_marker
from observal_cli.sessions.claude_code import find_jsonl_file, get_parent_session_id, project_key_from_cwd


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
