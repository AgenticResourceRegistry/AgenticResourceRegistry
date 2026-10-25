# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING

from observal_cli.harness import ensure_loaded, get_adapter
from observal_cli.harness_specs.codex_hooks_spec import build_codex_hooks
from observal_cli.hooks import session_push

if TYPE_CHECKING:
    from pathlib import Path

SESSION_ID = "019e8c1d-c2ce-79f1-afbf-5c3ff02d5fdc"


def make_session(home: Path, session_id: str = SESSION_ID, *, codex_root: Path | None = None) -> Path:
    root = (codex_root or home / ".codex") / "sessions" / "2026" / "07" / "18"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"rollout-2026-07-18T10-00-00-{session_id}.jsonl"
    path.write_text('{"type":"event_msg","payload":{"type":"user_message","message":"hello"}}\n')
    return path


def write_config(home: Path) -> None:
    root = home / ".observal"
    root.mkdir(parents=True)
    (root / "config.json").write_text(
        json.dumps({"server_url": "http://server", "access_token": "token", "user_id": "user"})
    )


def test_codex_adapter_resolves_exact_and_latest_rollout(tmp_path: Path):
    path = make_session(tmp_path)
    ensure_loaded()
    adapter = get_adapter("codex")

    exact = adapter.resolve_session_source({"session_id": SESSION_ID, "cwd": "/work"}, home=tmp_path)
    latest = adapter.resolve_session_source({"cwd": "/work"}, home=tmp_path)

    assert exact is not None and exact.path == path
    assert exact.session_id == SESSION_ID
    assert latest is not None and latest.session_id == SESSION_ID


def test_codex_adapter_discovers_only_recent_rollouts(tmp_path: Path):
    recent = make_session(tmp_path)
    old = make_session(tmp_path, "11111111-2222-3333-4444-555555555555")
    old_time = time.time() - 10 * 24 * 3600
    os.utime(old, (old_time, old_time))
    ensure_loaded()

    sources = get_adapter("codex").discover_session_sources(home=tmp_path, since_hours=24)

    assert [source.path for source in sources] == [recent]


def test_codex_home_env_selects_custom_sessions_for_hook_and_discovery(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    custom_root = tmp_path / "isolated-codex"
    custom = make_session(home, codex_root=custom_root)
    make_session(home, "11111111-2222-3333-4444-555555555555")
    write_config(home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(custom_root))
    ensure_loaded()
    adapter = get_adapter("codex")

    source = adapter.resolve_session_source({"session_id": SESSION_ID})
    assert source is not None and source.path == custom
    assert [s.path for s in adapter.discover_session_sources(since_hours=24)] == [custom]

    delivered = []
    monkeypatch.setattr(
        session_push, "drain_session_source", lambda source, _config, **kw: delivered.append(source.path) or True
    )
    monkeypatch.setattr(session_push, "_spawn_worker", lambda *args, **kwargs: None)
    session_push._run_hook({"session_id": SESSION_ID, "event": "Stop", "cwd": str(tmp_path)}, harness="codex")
    assert delivered == [custom]


def test_relative_codex_home_is_absolute_in_detached_worker(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    elsewhere = tmp_path / "elsewhere"
    for directory in (home, project, elsewhere):
        directory.mkdir()
    session = make_session(home, codex_root=project / "isolated-codex")
    write_config(home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", "isolated-codex")
    monkeypatch.chdir(project)
    ensure_loaded()
    adapter = get_adapter("codex")
    assert adapter.resolve_session_source({"session_id": SESSION_ID}).path == session

    from observal_cli.shared import launcher

    spawned = []
    monkeypatch.setattr(session_push, "drain_session_source", lambda *_args, **_kwargs: True)
    # The launcher probes its own interpreter once per process; a cold probe would be captured as the worker.
    monkeypatch.setattr(launcher, "importable_in_isolation", lambda: True)
    monkeypatch.setattr(session_push.subprocess, "Popen", lambda argv, **kwargs: spawned.append((argv, kwargs)))
    session_push._run_hook({"session_id": SESSION_ID, "event": "Stop"}, harness="codex")

    assert len(spawned) == 1
    argv, kwargs = spawned[0]
    assert "--finalize-session" in argv
    assert kwargs["env"]["CODEX_HOME"] == str(project / "isolated-codex")
    assert os.environ["CODEX_HOME"] == "isolated-codex"  # the hook process does not rewrite its caller's setting

    # Even if the detached worker changes directories, it uses the same sessions root.
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("CODEX_HOME", kwargs["env"]["CODEX_HOME"])
    assert adapter.resolve_session_source({"session_id": SESSION_ID}).path == session
    assert [source.path for source in adapter.discover_session_sources(since_hours=24)] == [session]


def test_codex_home_falls_back_to_default_sessions(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    default = make_session(home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    ensure_loaded()
    adapter = get_adapter("codex")
    assert adapter.resolve_session_source({"session_id": SESSION_ID}).path == default
    assert [s.path for s in adapter.discover_session_sources(since_hours=24)] == [default]
    monkeypatch.setenv("CODEX_HOME", "")
    assert adapter.resolve_session_source({"session_id": SESSION_ID}).path == default


def test_explicit_home_overrides_codex_home_for_isolated_calls(tmp_path: Path, monkeypatch):
    session = make_session(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "other"))
    ensure_loaded()
    adapter = get_adapter("codex")
    assert adapter.resolve_session_source({"session_id": SESSION_ID}, home=tmp_path).path == session
    assert [s.path for s in adapter.discover_session_sources(home=tmp_path, since_hours=24)] == [session]


def test_codex_stop_uses_shared_engine_and_finalizer(tmp_path: Path, monkeypatch):
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
        {"session_id": SESSION_ID, "cwd": "/work", "event": "Stop"},
        harness="codex",
        home=tmp_path,
    )

    assert drained == [(SESSION_ID, False)]
    assert spawned == [(("--finalize-session", SESSION_ID, "--cwd", "/work"), "codex")]


def test_codex_hook_spec_uses_shared_engine():
    hooks = build_codex_hooks()["hooks"]
    commands = {hook["command"] for groups in hooks.values() for group in groups for hook in group["hooks"]}
    assert len(commands) == 1
    assert "observal_cli.hooks.session_push --harness codex" in commands.pop()
