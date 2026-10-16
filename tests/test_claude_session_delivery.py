# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING
from unittest.mock import patch

from observal_cli.harness import ensure_loaded, get_adapter
from observal_cli.hooks import session_push

if TYPE_CHECKING:
    from pathlib import Path


def make_session(home: Path, session_id: str = "parent") -> tuple[Path, Path]:
    project = home / ".claude" / "projects" / "-work-project"
    project.mkdir(parents=True)
    parent = project / f"{session_id}.jsonl"
    parent.write_text('{"type":"user","message":{"content":"hello"}}\n')
    subagents = project / session_id / "subagents"
    subagents.mkdir(parents=True)
    child = subagents / "agent-child.jsonl"
    child.write_text('{"type":"assistant","message":{"content":[]}}\n')
    return parent, child


def write_config(home: Path) -> None:
    config_dir = home / ".observal"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "server_url": "http://server",
                "access_token": "token",
                "user_id": "user",
            }
        )
    )


def test_claude_adapter_resolves_parent_and_related_subagents(tmp_path: Path):
    ensure_loaded()
    parent, child = make_session(tmp_path)
    adapter = get_adapter("claude-code")

    source = adapter.resolve_session_source(
        {"session_id": "parent", "cwd": "/work/project"},
        home=tmp_path,
    )

    assert source is not None
    assert source.path == parent
    related = adapter.related_session_sources(source, home=tmp_path)
    assert len(related) == 1
    assert related[0].path == child
    assert related[0].session_id == "child"
    assert related[0].checkpoint_key == "parent__sub__child"
    assert related[0].parent_session_id == "parent"


def test_claude_adapter_discovers_recent_parent_and_subagent(tmp_path: Path):
    ensure_loaded()
    parent, child = make_session(tmp_path)
    old = parent.parent / "old.jsonl"
    old.write_text("{}\n")
    old_time = time.time() - 10 * 24 * 3600
    os.utime(old, (old_time, old_time))

    sources = get_adapter("claude-code").discover_session_sources(home=tmp_path, since_hours=24)

    assert {source.path for source in sources} == {parent, child}


def test_stop_hook_drains_parent_and_child_then_spawns_finalizer(tmp_path: Path, monkeypatch):
    make_session(tmp_path)
    write_config(tmp_path)
    drained: list[tuple[str, bool]] = []
    spawned: list[tuple[tuple[str, ...], str]] = []

    monkeypatch.setattr(
        session_push,
        "drain_session_source",
        lambda source, _config, **kwargs: drained.append((source.session_id, kwargs["final"])) or True,
    )
    monkeypatch.setattr(
        session_push,
        "_spawn_worker",
        lambda *args, harness: spawned.append((args, harness)),
    )

    session_push._run_hook(
        {"session_id": "parent", "cwd": "/work/project", "hook_event_name": "Stop"},
        harness="claude-code",
        home=tmp_path,
    )

    assert drained == [("parent", False), ("child", False)]
    assert spawned == [(("--finalize-session", "parent", "--cwd", "/work/project"), "claude-code")]


def test_finalizer_waits_then_finalizes_parent_and_child(tmp_path: Path, monkeypatch):
    parent, child = make_session(tmp_path)
    write_config(tmp_path)
    waited: list[Path] = []
    drained: list[tuple[str, bool]] = []

    monkeypatch.setattr(session_push, "_wait_until_stable", waited.append)
    monkeypatch.setattr(
        session_push,
        "drain_session_source",
        lambda source, _config, **kwargs: drained.append((source.session_id, kwargs["final"])) or True,
    )

    session_push._finalize_session("claude-code", "parent", "/work/project", home=tmp_path)

    assert waited == [parent, child]
    assert drained == [("parent", True), ("child", True)]


def test_claude_hook_spec_uses_generic_harness_entrypoint():
    from observal_cli.harness_specs.claude_code_hooks_spec import get_desired_hooks

    commands = {
        hook["command"] for groups in get_desired_hooks().values() for group in groups for hook in group["hooks"]
    }
    assert len(commands) == 2
    assert any("observal_cli.hooks.session_push --harness claude-code" in cmd for cmd in commands)
    assert any("observal_cli.hooks.claude_updates" in cmd for cmd in commands)


def test_main_never_raises_on_invalid_input(monkeypatch):
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("not-json"))
    with patch.object(session_push.optic, "error"):
        session_push.main()
