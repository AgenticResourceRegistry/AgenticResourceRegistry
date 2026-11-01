# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING

from observal_cli.harness import ensure_loaded, get_adapter
from observal_cli.harness.protocol import SessionSource
from observal_cli.harness_specs.kiro_hooks_spec import build_kiro_hooks
from observal_cli.hooks import session_push
from observal_cli.sessions.kiro import read_kiro_agent_name

if TYPE_CHECKING:
    from pathlib import Path


def make_session(
    home: Path,
    session_id: str = "kiro-session",
    agent_name: str = "kiro_default",
    cwd: str = "/work",
) -> Path:
    root = home / ".kiro" / "sessions" / "cli"
    root.mkdir(parents=True, exist_ok=True)
    transcript = root / f"{session_id}.jsonl"
    transcript.write_text('{"kind":"Prompt","data":{"content":[{"kind":"text","data":"hello"}]}}\n')
    (root / f"{session_id}.json").write_text(
        json.dumps(
            {
                "cwd": cwd,
                "session_state": {
                    "agent_name": agent_name,
                    "conversation_metadata": {
                        "user_turn_metadatas": [
                            {
                                "loop_id": {"agent_id": {"name": agent_name}},
                                "metering_usage": [{"unit": "credit", "value": 1.25}],
                            },
                            {
                                "loop_id": {"agent_id": {"name": agent_name}},
                                "metering_usage": [{"unit": "credit", "value": 0.75}],
                            },
                        ]
                    },
                },
            }
        )
    )
    return transcript


def write_config(home: Path) -> None:
    root = home / ".observal"
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text(
        json.dumps({"server_url": "http://server", "access_token": "token", "user_id": "user"})
    )


def test_kiro_adapter_resolves_explicit_session_without_singleton(tmp_path: Path):
    transcript = make_session(tmp_path)
    ensure_loaded()
    adapter = get_adapter("kiro")

    source = adapter.resolve_session_source(
        {"session_id": "kiro-session", "cwd": "/hook-work"},
        home=tmp_path,
    )

    assert source is not None and source.path == transcript
    assert source.cwd == "/work"
    assert not (tmp_path / ".observal" / ".kiro-session").exists()


def test_kiro_adapter_ignores_missing_id_and_stale_singleton(tmp_path: Path):
    make_session(tmp_path, "stale-session")
    state_file = tmp_path / ".observal" / ".kiro-session"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(json.dumps({"session_id": "stale-session"}))
    ensure_loaded()
    adapter = get_adapter("kiro")

    assert adapter.resolve_session_source({"event": "stop"}, home=tmp_path) is None
    assert json.loads(state_file.read_text()) == {"session_id": "stale-session"}


def test_uncorrelated_stop_does_not_select_concurrent_kiro_session(tmp_path: Path):
    make_session(tmp_path, "session-a")
    make_session(tmp_path, "session-b")
    ensure_loaded()
    adapter = get_adapter("kiro")

    source_a = adapter.resolve_session_source({"session_id": "session-a"}, home=tmp_path)
    source_b = adapter.resolve_session_source({"session_id": "session-b"}, home=tmp_path)

    assert source_a is not None and source_a.session_id == "session-a"
    assert source_b is not None and source_b.session_id == "session-b"
    assert adapter.resolve_session_source({"event": "stop"}, home=tmp_path) is None


def test_kiro_adapter_discovers_recent_sessions_and_credits(tmp_path: Path):
    recent = make_session(tmp_path)
    old = make_session(tmp_path, "old")
    old_time = time.time() - 10 * 24 * 3600
    os.utime(old, (old_time, old_time))
    ensure_loaded()
    adapter = get_adapter("kiro")

    sources = adapter.discover_session_sources(home=tmp_path, since_hours=24)

    assert [source.path for source in sources] == [recent]
    assert sources[0].cwd == "/work"
    assert adapter.session_extra_fields(sources[0], {}, True, home=tmp_path) == {"total_credits": 2.0}


def test_kiro_reads_active_agent_with_latest_turn_fallback(tmp_path: Path):
    transcript = make_session(tmp_path, agent_name="top-level-agent")
    assert read_kiro_agent_name(transcript) == "top-level-agent"

    companion = transcript.with_suffix(".json")
    companion.write_text(
        json.dumps(
            {
                "session_state": {
                    "conversation_metadata": {
                        "user_turn_metadatas": [
                            {"loop_id": {"agent_id": {"name": "old-agent"}}},
                            {"loop_id": {"agent_id": {"name": "latest-agent"}}},
                        ]
                    }
                }
            }
        )
    )
    assert read_kiro_agent_name(transcript) == "latest-agent"


def test_kiro_agent_metadata_fails_safely(tmp_path: Path):
    transcript = make_session(tmp_path)
    transcript.with_suffix(".json").write_text("not json")
    assert read_kiro_agent_name(transcript) is None
    transcript.with_suffix(".json").write_text("[]")
    assert read_kiro_agent_name(transcript) is None
    transcript.with_suffix(".json").write_text("null")
    assert read_kiro_agent_name(transcript) is None
    assert read_kiro_agent_name(tmp_path / "missing.jsonl") is None
    assert read_kiro_agent_name(None) is None


def test_kiro_recovery_attributes_each_session_from_its_metadata(tmp_path: Path, monkeypatch):
    pulled = make_session(tmp_path, "agent-session", agent_name="pulled-agent")
    default = make_session(tmp_path, "default-session", agent_name="kiro_default")
    old_time = time.time() - 180
    os.utime(pulled, (old_time, old_time))
    os.utime(default, (old_time, old_time))
    write_config(tmp_path)
    recovered: dict[str, tuple[str | None, str | None, bool]] = {}

    def lookup(name, harness, directory=None):
        assert name == "pulled-agent"
        assert directory == "/work"
        assert harness == "kiro"
        return {"id": "pulled-uuid", "version": "1.2.0"}

    def capture(source, _config, **_kwargs):
        from observal_cli.sessions.base import _resolve_agent

        assert source.session_id not in recovered
        identity = _resolve_agent(source.cwd, [], source.path, harness="kiro")
        recovered[source.session_id] = (*identity, _kwargs["final"])
        return True

    monkeypatch.setenv("OBSERVAL_AGENT_ID", "triggering-hook-uuid")
    monkeypatch.setattr("observal_cli.lockfile.get_agent_by_name", lookup)
    monkeypatch.setattr(session_push, "drain_outbox", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(session_push, "read_cursor_state", lambda *_args, **_kwargs: (0, 0, False))
    monkeypatch.setattr(session_push, "drain_session_source", capture)

    session_push._recover_sessions("kiro", home=tmp_path)

    assert recovered == {
        "agent-session": ("pulled-uuid", "1.2.0", False),
        "default-session": (None, None, False),
    }


def test_kiro_recovery_skips_acknowledged_eof(tmp_path: Path, monkeypatch):
    transcript = make_session(tmp_path)
    old_time = time.time() - 180
    os.utime(transcript, (old_time, old_time))
    write_config(tmp_path)
    recovered: list[str] = []

    monkeypatch.setattr(session_push, "drain_outbox", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        session_push,
        "read_cursor_state",
        lambda *_args, **_kwargs: (transcript.stat().st_size, 1, False),
    )
    monkeypatch.setattr(
        session_push,
        "drain_session_source",
        lambda source, *_args, **_kwargs: recovered.append(source.session_id) or True,
    )

    session_push._recover_sessions("kiro", home=tmp_path)

    assert recovered == []


def test_kiro_recovery_delivers_growth_non_finally(tmp_path: Path, monkeypatch):
    transcript = make_session(tmp_path)
    acknowledged_offset = transcript.stat().st_size
    with transcript.open("a") as file:
        file.write('{"kind":"Response","data":{"content":"later"}}\n')
    old_time = time.time() - 180
    os.utime(transcript, (old_time, old_time))
    write_config(tmp_path)
    recovered: list[tuple[str, bool]] = []

    monkeypatch.setattr(session_push, "drain_outbox", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        session_push,
        "read_cursor_state",
        lambda *_args, **_kwargs: (acknowledged_offset, 1, False),
    )
    monkeypatch.setattr(
        session_push,
        "drain_session_source",
        lambda source, *_args, **kwargs: recovered.append((source.session_id, kwargs["final"])) or True,
    )

    session_push._recover_sessions("kiro", home=tmp_path)

    assert recovered == [("kiro-session", False)]


def test_kiro_stop_routes_credits_through_shared_engine(tmp_path: Path, monkeypatch):
    make_session(tmp_path)
    write_config(tmp_path)
    drained: list[dict] = []
    spawned: list[tuple[tuple[str, ...], str]] = []

    def capture(_source, _config, **kwargs):
        drained.append(kwargs)
        return True

    monkeypatch.setattr(session_push, "drain_session_source", capture)
    monkeypatch.setattr(
        session_push,
        "_spawn_worker",
        lambda *args, harness: spawned.append((args, harness)),
    )

    session_push._run_hook(
        {"session_id": "kiro-session", "cwd": "/work", "event": "stop"},
        harness="kiro",
        home=tmp_path,
    )

    assert drained[0]["extra_fields"] == {"total_credits": 2.0}
    assert spawned == [(("--finalize-session", "kiro-session", "--cwd", "/work"), "kiro")]


def test_kiro_hook_spec_uses_shared_engine_with_uuid_attribution():
    hooks = build_kiro_hooks(agent_id="agent-uuid")
    command = hooks["userPromptSubmit"][0]["command"]

    assert "OBSERVAL_AGENT_ID=agent-uuid" in command or 'set "OBSERVAL_AGENT_ID=agent-uuid"' in command
    assert "observal_cli.hooks.session_push --harness kiro" in command
    assert hooks["stop"][0]["command"] == command


# ── Kiro IDE transcript layout ────────────────────────────────────
#
# The IDE stores sessions as
# ``~/.kiro/sessions/<workspaceHash>/<session_id>/messages.jsonl`` with a
# sibling ``session.json``, enveloping each record as ``{id, timestamp,
# payload}``. Nothing about that matches the CLI layout, so these pin the
# discovery and metadata paths that make IDE sessions deliverable at all.


def make_ide_session(
    home: Path,
    session_id: str = "sess_ide-1",
    workspace: str = "/work/project",
    bucket: str = "a1b2c3d4",
    credits: tuple[float, ...] = (0.25, 0.75),
    sub_agents: tuple[str, ...] = (),
) -> Path:
    session_dir = home / ".kiro" / "sessions" / bucket / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    records = [
        {"id": "r1", "payload": {"type": "session_start", "agentType": "vibe"}},
        {"id": "r2", "payload": {"type": "user", "content": "hello"}},
        {"id": "r3", "payload": {"type": "assistant", "content": "hi", "operationType": "Say"}},
    ]
    for index, name in enumerate(sub_agents):
        records.append(
            {
                "id": f"sub{index}",
                "payload": {"type": "sub_agent_start", "subAgentName": name, "prompt": "go"},
            }
        )
    for value in credits:
        records.append(
            {
                "id": f"u{value}",
                "payload": {
                    "type": "usage_summary",
                    "promptTurnSummaries": [{"unit": "credit", "usage": value}],
                },
            }
        )
    transcript = session_dir / "messages.jsonl"
    transcript.write_text("".join(json.dumps(r) + "\n" for r in records))
    (session_dir / "session.json").write_text(json.dumps({"id": session_id, "workspacePaths": [workspace]}))
    return transcript


def test_kiro_finds_ide_transcript_across_workspace_buckets(tmp_path: Path):
    """The workspace hash is not derivable from the session id, so buckets are scanned."""
    from observal_cli.sessions.kiro import find_kiro_jsonl

    make_ide_session(tmp_path, session_id="sess_a", bucket="bucket-one")
    expected = make_ide_session(tmp_path, session_id="sess_b", bucket="bucket-two")

    assert find_kiro_jsonl("sess_b", home=tmp_path) == expected
    assert find_kiro_jsonl("sess_missing", home=tmp_path) is None


def test_kiro_prefers_cli_layout_when_both_exist(tmp_path: Path):
    cli = make_session(tmp_path, session_id="dup")
    make_ide_session(tmp_path, session_id="dup")

    from observal_cli.sessions.kiro import find_kiro_jsonl

    assert find_kiro_jsonl("dup", home=tmp_path) == cli


def test_kiro_ide_cwd_comes_from_workspace_paths(tmp_path: Path):
    """The IDE records ``workspacePaths``; there is no ``cwd`` key to read."""
    from observal_cli.sessions.kiro import read_kiro_session_cwd

    transcript = make_ide_session(tmp_path, workspace="/work/project")

    assert read_kiro_session_cwd(transcript) == "/work/project"


def test_kiro_ide_credits_sum_usage_summaries(tmp_path: Path):
    """IDE credits live on per-turn ``usage_summary`` payloads, not the companion file."""
    from observal_cli.sessions.kiro import read_kiro_credits

    make_ide_session(tmp_path, session_id="sess_credits", credits=(0.25, 0.75))

    assert read_kiro_credits("sess_credits", home=tmp_path) == 1.0


def test_kiro_ide_credits_absent_before_first_turn_completes(tmp_path: Path):
    from observal_cli.sessions.kiro import read_kiro_credits

    make_ide_session(tmp_path, session_id="sess_new", credits=())

    assert read_kiro_credits("sess_new", home=tmp_path) is None


def test_kiro_resolves_ide_session_source_from_hook_event(tmp_path: Path):
    """End of the chain: a hook event for an IDE session must yield a source."""
    make_ide_session(tmp_path, session_id="sess_hooked", workspace="/work/ide")

    ensure_loaded()
    source = get_adapter("kiro").resolve_session_source({"session_id": "sess_hooked"}, home=tmp_path)

    assert source is not None
    assert source.session_id == "sess_hooked"
    assert source.path.name == "messages.jsonl"
    assert source.cwd == "/work/ide"


def test_kiro_discovers_both_layouts_for_reconcile(tmp_path: Path):
    make_session(tmp_path, session_id="cli-one")
    make_ide_session(tmp_path, session_id="sess_ide-one")

    ensure_loaded()
    found = {s.session_id for s in get_adapter("kiro").discover_session_sources(home=tmp_path)}

    assert found == {"cli-one", "sess_ide-one"}


def test_kiro_ide_partial_line_does_not_break_credit_read(tmp_path: Path):
    """A transcript being appended to can end mid-line; that must not raise."""
    from observal_cli.sessions.kiro import read_kiro_credits

    transcript = make_ide_session(tmp_path, session_id="sess_partial", credits=(0.5,))
    with transcript.open("a") as handle:
        handle.write('{"id":"trunc","payl')

    assert read_kiro_credits("sess_partial", home=tmp_path) == 0.5


# ── Kiro IDE agent attribution ────────────────────────────────────
#
# An IDE session always runs as Kiro itself. A registry agent only participates
# when Kiro delegates a turn to it, recorded as a ``sub_agent_start`` payload;
# nothing else on disk names an agent.


def test_kiro_ide_attributes_session_to_delegated_agent(tmp_path: Path):
    from observal_cli.sessions.kiro import read_kiro_agent_name

    transcript = make_ide_session(tmp_path, sub_agents=("pikachu-dude-agent",))

    assert read_kiro_agent_name(transcript) == "pikachu-dude-agent"


def test_kiro_ide_uses_most_recent_delegation(tmp_path: Path):
    """Attribution is session-level, so the latest delegation wins."""
    from observal_cli.sessions.kiro import read_kiro_agent_name

    transcript = make_ide_session(tmp_path, sub_agents=("first-agent", "second-agent", "third-agent"))

    assert read_kiro_agent_name(transcript) == "third-agent"


def test_kiro_ide_without_delegation_stays_unattributed(tmp_path: Path):
    """A bare Kiro conversation belongs to no registry agent."""
    from observal_cli.sessions.kiro import read_kiro_agent_name

    transcript = make_ide_session(tmp_path, sub_agents=())

    assert read_kiro_agent_name(transcript) is None


def test_kiro_ide_identity_resolves_through_lockfile(tmp_path: Path, monkeypatch):
    """Only the delegated child, not the chat that called it, gets its registry id."""
    import observal_cli.lockfile as lockfile

    monkeypatch.setattr(
        lockfile,
        "get_agent_by_name",
        lambda name, harness, directory=None: (
            {"id": "agent-uuid", "version": "2.0.0"} if name == "pikachu-dude-agent" else None
        ),
    )
    transcript = make_ide_session(tmp_path, sub_agents=("pikachu-dude-agent",), workspace="/work/ide")
    child = _write_subexecution(transcript, "sub-1")
    _link_subexecution(transcript, "sub-1", "pikachu-dude-agent")
    ensure_loaded()

    assert get_adapter("kiro").resolve_session_agent_identity(transcript, "/work/ide") == (None, None)
    assert get_adapter("kiro").resolve_session_agent_identity(child, "/work/ide") == ("agent-uuid", "2.0.0")


def test_kiro_ide_unknown_agent_is_left_unattributed(tmp_path: Path, monkeypatch):
    """An agent that was never pulled has no local id; do not invent one."""
    import observal_cli.lockfile as lockfile

    monkeypatch.setattr(lockfile, "get_agent_by_name", lambda name, harness, directory=None: None)
    transcript = make_ide_session(tmp_path, sub_agents=("never-pulled-agent",))
    ensure_loaded()

    assert get_adapter("kiro").resolve_session_agent_identity(transcript, "/work") == (None, None)


# ── Attribution must not outlive the registry entry ───────────────
#
# The lockfile is local and can carry agents the current registry no longer
# has. Attributing to one produces a session tagged with an id nothing can
# resolve, which surfaces as an agent id with a blank name.


def test_kiro_skips_attribution_for_an_agent_the_registry_lost(tmp_path: Path, monkeypatch):
    """Reconciliation marked it "unavailable": the server reported it not found."""
    import observal_cli.lockfile as lockfile

    monkeypatch.setattr(
        lockfile,
        "get_agent_by_name",
        lambda name, harness, directory=None: {
            "id": "stale-uuid",
            "version": "1.0.0",
            "registry_status": "unavailable",
        },
    )
    transcript = make_ide_session(tmp_path, sub_agents=("deleted-agent",))
    ensure_loaded()

    assert get_adapter("kiro").resolve_session_agent_identity(transcript, "/work") == (None, None)


def test_kiro_attributes_when_reconciliation_has_not_run(tmp_path: Path, monkeypatch):
    """No status means unknown, not disqualified - it must still attribute."""
    import observal_cli.lockfile as lockfile

    monkeypatch.setattr(
        lockfile,
        "get_agent_by_name",
        lambda name, harness, directory=None: {"id": "agent-uuid", "version": "1.0.0"},
    )
    transcript = _agent_session(tmp_path, "some-agent")
    ensure_loaded()

    assert get_adapter("kiro").resolve_session_agent_identity(transcript, "/work") == (
        "agent-uuid",
        "1.0.0",
    )


def test_registry_backed_guard_covers_every_harness(monkeypatch):
    """The same guard protects the shared OBSERVAL_AGENT_ID path, not just Kiro.

    Every other harness bakes an agent id into its hook command at pull time and
    trusts the lockfile entry it resolves to, so it is exposed to exactly the
    same staleness.
    """
    import observal_cli.sessions.base as base

    monkeypatch.setenv("OBSERVAL_AGENT_ID", "stale-uuid")
    monkeypatch.setattr(
        base,
        "_lookup_lockfile_agent_by_id",
        lambda agent_id, harness=None: {
            "id": "stale-uuid",
            "version": "1.0.0",
            "registry_status": "unavailable",
        },
    )

    assert base._resolve_agent("/work", [], None, harness="claude-code") == (None, None)


# ── Sub-executions carry the delegated agent's work ───────────────
#
# A delegating IDE session records only sub_agent_start/complete markers in its
# own messages.jsonl. The delegate's actual turns land in a sibling
# sub-executions/<subExecutionId>.jsonl, so the parent trace is missing the
# delegate's output until those are read as related sources.


def write_sub_execution(transcript: Path, sub_execution_id: str, text: str = "Hi!") -> Path:
    sub_dir = transcript.parent / "sub-executions"
    sub_dir.mkdir(parents=True, exist_ok=True)
    path = sub_dir / f"{sub_execution_id}.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "s1",
                "payload": {
                    "type": "assistant",
                    "content": text,
                    "operationType": "Say",
                    "subExecutionId": sub_execution_id,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _source(transcript: Path, session_id: str = "sess_ide-1") -> SessionSource:
    return SessionSource("kiro", session_id, transcript, cwd="/work/project")


def test_ide_sub_executions_are_returned_as_related_sources(tmp_path: Path):
    transcript = make_ide_session(tmp_path, sub_agents=("helper",))
    write_sub_execution(transcript, "83ed4d5e-4916-45ac-b7db-39a392d3daad")
    ensure_loaded()

    related = get_adapter("kiro").related_session_sources(_source(transcript))

    assert len(related) == 1
    assert related[0].session_id == "83ed4d5e-4916-45ac-b7db-39a392d3daad"
    assert related[0].parent_session_id == "sess_ide-1"
    assert related[0].cursor_key == "sess_ide-1__sub__83ed4d5e-4916-45ac-b7db-39a392d3daad"
    assert related[0].cwd == "/work/project"


def test_ide_sub_executions_are_sorted_and_all_returned(tmp_path: Path):
    transcript = make_ide_session(tmp_path, sub_agents=("a", "b"))
    write_sub_execution(transcript, "bbbb")
    write_sub_execution(transcript, "aaaa")
    ensure_loaded()

    related = get_adapter("kiro").related_session_sources(_source(transcript))

    assert [source.session_id for source in related] == ["aaaa", "bbbb"]


def test_a_sub_execution_does_not_recurse_into_further_children(tmp_path: Path):
    """Guard against a child re-scanning its parent's directory forever."""
    transcript = make_ide_session(tmp_path, sub_agents=("helper",))
    write_sub_execution(transcript, "child")
    ensure_loaded()
    adapter = get_adapter("kiro")

    child = adapter.related_session_sources(_source(transcript))[0]

    assert adapter.related_session_sources(child) == []


def test_a_session_without_sub_executions_has_no_related_sources(tmp_path: Path):
    transcript = make_ide_session(tmp_path)
    ensure_loaded()

    assert get_adapter("kiro").related_session_sources(_source(transcript)) == []


def test_cli_transcripts_have_no_related_sources(tmp_path: Path):
    """kiro-cli does not delegate, so it writes no sub-executions.

    Its session directories do exist and hold a tasks/ directory, so the
    sibling directory is real - it simply never contains sub-executions.
    """
    cli_dir = tmp_path / ".kiro" / "sessions" / "cli"
    cli_dir.mkdir(parents=True, exist_ok=True)
    transcript = cli_dir / "cli-1.jsonl"
    transcript.write_text("", encoding="utf-8")
    (cli_dir / "cli-1" / "tasks").mkdir(parents=True, exist_ok=True)
    ensure_loaded()

    assert get_adapter("kiro").related_session_sources(_source(transcript, "cli-1")) == []


# ── Collection and attribution ────────────────────────────────────
#
# The shared hooks collect all sessions. Only recognized agent sessions and
# delegated sub-executions receive an agent id.


def _write_subexecution(transcript: Path, sub_id: str, text: str = "done") -> Path:
    sub_dir = transcript.parent / "sub-executions"
    sub_dir.mkdir(parents=True, exist_ok=True)
    path = sub_dir / f"{sub_id}.jsonl"
    path.write_text(
        json.dumps({"id": "s1", "payload": {"type": "assistant", "content": text, "operationType": "Say"}}) + "\n",
        encoding="utf-8",
    )
    return path


def _link_subexecution(transcript: Path, sub_id: str, agent_name: str) -> None:
    """Record in the parent that this sub-execution ran as this agent."""
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "id": f"link-{sub_id}",
                    "timestamp": "2026-01-01T00:00:00.000Z",
                    "payload": {
                        "type": "sub_agent_start",
                        "subSessionId": sub_id,
                        "subAgentName": agent_name,
                        "prompt": "go",
                    },
                }
            )
            + "\n"
        )


def _scope(path: Path, session_id: str = "sess_ide-1") -> bool:
    ensure_loaded()
    return get_adapter("kiro").should_capture_session(SessionSource("kiro", session_id, path, cwd="/work/project"))


def _registry(monkeypatch, *names: str) -> None:
    """Make exactly these names resolve to agents the registry still has."""
    import observal_cli.lockfile as lockfile

    monkeypatch.setattr(
        lockfile,
        "get_agent_by_name",
        lambda name, harness, directory=None: (
            {"id": f"{name}-uuid", "version": "1.0.0", "registry_status": "approved"} if name in names else None
        ),
    )


def test_ide_conversation_is_collected_even_when_it_delegates(tmp_path: Path, monkeypatch):
    _registry(monkeypatch, "test")
    transcript = make_ide_session(tmp_path, sub_agents=("test",))

    assert _scope(transcript) is True
    assert get_adapter("kiro").resolve_session_agent_identity(transcript, "/work/project") == (None, None)


def test_plain_ide_chat_is_collected_without_agent_attribution(tmp_path: Path, monkeypatch):
    _registry(monkeypatch, "test")
    transcript = make_ide_session(tmp_path)

    assert _scope(transcript) is True
    assert get_adapter("kiro").resolve_session_agent_identity(transcript, "/work/project") == (None, None)


def test_a_delegated_sub_execution_is_captured(tmp_path: Path, monkeypatch):
    """The agent's own work, attributed from the parent's sub_agent_start."""
    _registry(monkeypatch, "test")
    transcript = make_ide_session(tmp_path, sub_agents=("test",))
    sub = _write_subexecution(transcript, "sub-1")
    _link_subexecution(transcript, "sub-1", "test")

    assert _scope(sub, session_id="sub-1") is True


def test_shared_hook_collects_ordinary_cli_chat(tmp_path: Path, monkeypatch):
    make_session(tmp_path, agent_name="kiro_default")
    write_config(tmp_path)
    drained = []
    monkeypatch.setattr(
        session_push, "drain_session_source", lambda source, *_args, **_kwargs: drained.append(source) or True
    )
    monkeypatch.setattr(session_push, "_spawn_worker", lambda *_args, **_kwargs: None)

    session_push._run_hook({"session_id": "kiro-session", "event": "userPromptSubmit"}, harness="kiro", home=tmp_path)

    assert [source.session_id for source in drained] == ["kiro-session"]
    assert get_adapter("kiro").resolve_session_agent_identity(drained[0].path, "/work") == (None, None)


def test_shared_hook_collects_plain_chat_and_delegated_work(tmp_path: Path, monkeypatch):
    transcript = make_ide_session(tmp_path, sub_agents=("test",))
    _write_subexecution(transcript, "sub-1")
    _link_subexecution(transcript, "sub-1", "test")
    write_config(tmp_path)
    drained = []
    monkeypatch.setattr(
        session_push, "drain_session_source", lambda source, *_args, **_kwargs: drained.append(source) or True
    )
    monkeypatch.setattr(session_push, "_spawn_worker", lambda *_args, **_kwargs: None)

    session_push._run_hook({"session_id": "sess_ide-1", "event": "UserPromptSubmit"}, harness="kiro", home=tmp_path)

    assert [source.session_id for source in drained] == ["sess_ide-1", "sub-1"]
    assert drained[1].parent_session_id == "sess_ide-1"


def test_a_non_registry_sub_execution_is_collected_but_unattributed(tmp_path: Path, monkeypatch):
    _registry(monkeypatch, "test")
    transcript = make_ide_session(tmp_path, sub_agents=("someone-elses-agent",))
    sub = _write_subexecution(transcript, "sub-1")
    _link_subexecution(transcript, "sub-1", "someone-elses-agent")

    assert _scope(sub, session_id="sub-1") is True
    assert get_adapter("kiro").resolve_session_agent_identity(sub, "/work/project") == (None, None)


def test_an_orphan_sub_execution_is_collected_unattributed(tmp_path: Path, monkeypatch):
    _registry(monkeypatch, "test")
    transcript = make_ide_session(tmp_path, sub_agents=("test",))
    sub = _write_subexecution(transcript, "unlinked")

    assert _scope(sub, session_id="unlinked") is True
    assert get_adapter("kiro").resolve_session_agent_identity(sub, "/work/project") == (None, None)


def test_cli_sessions_are_not_gated(tmp_path: Path, monkeypatch):
    """Ordinary CLI sessions can still contain MCP and skill activity."""
    _registry(monkeypatch, "test")

    assert _scope(make_session(tmp_path, agent_name="kiro_default"), session_id="cli-1") is True
    assert _scope(make_session(tmp_path, "other", agent_name="test"), session_id="other") is True


def test_reconcile_collects_parent_and_sub_execution(tmp_path: Path, monkeypatch):
    from observal_cli import cmd_reconcile_cli
    from observal_cli.sessions import base

    transcript = make_ide_session(tmp_path, sub_agents=("test",))
    _write_subexecution(transcript, "sub-1")
    _link_subexecution(transcript, "sub-1", "test")
    adapter = get_adapter("kiro")
    discover = adapter.discover_session_sources
    monkeypatch.setattr(
        adapter, "discover_session_sources", lambda since_hours: discover(home=tmp_path, since_hours=since_hours)
    )
    monkeypatch.setattr(cmd_reconcile_cli, "get_adapter", lambda _name: adapter)
    monkeypatch.setattr(cmd_reconcile_cli, "read_cursor_state", lambda _key: (0, 0, False))
    monkeypatch.setattr(base, "read_cursor_state", lambda _key, home=None: (0, 0, False))
    monkeypatch.setattr(cmd_reconcile_cli, "recover_cursor_from_server", lambda _source, _cfg: (0, 0))
    drained = []
    monkeypatch.setattr(
        cmd_reconcile_cli,
        "drain_session_source",
        lambda source, _cfg, **kwargs: drained.append((source, kwargs)) or True,
    )

    result = cmd_reconcile_cli._reconcile_harness("kiro", {"user_id": "user"}, 24, False)

    assert result["pushed"] == 2
    assert [source.session_id for source, _kwargs in drained] == ["sess_ide-1", "sub-1"]
    assert drained[1][0].parent_session_id == "sess_ide-1"
    assert len(drained[1][1]["extra_records"]) == 1


def test_recovery_delivers_parent_and_sub_execution(tmp_path: Path, monkeypatch):
    """Recovery sends the conversation and separately stored delegated work."""
    _registry(monkeypatch, "test")
    transcript = make_ide_session(tmp_path, sub_agents=("test",))
    sub = _write_subexecution(transcript, "sub-1")
    _link_subexecution(transcript, "sub-1", "test")
    old_time = time.time() - 180
    for path in (transcript, sub):
        os.utime(path, (old_time, old_time))
    write_config(tmp_path)
    drained: list[str] = []

    monkeypatch.setattr(session_push, "drain_outbox", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(session_push, "read_cursor_state", lambda *_args, **_kwargs: (0, 0, False))
    monkeypatch.setattr(
        session_push,
        "drain_session_source",
        lambda source, *_args, **_kwargs: drained.append(os.path.basename(str(source.path))) or True,
    )

    session_push._recover_sessions("kiro", home=tmp_path)

    assert drained == ["messages.jsonl", "sub-1.jsonl"]


# ── Sessions started as an agent ──────────────────────────────────
#
# Picking an agent from the IDE dropdown runs the whole conversation as that
# agent. There is no sub-execution to fall back on, so the conversation itself
# is the agent's work and is captured.


def _agent_session(home: Path, agent: str, session_id: str = "sess_agent-1", with_start: bool = True) -> Path:
    """Write an IDE transcript for a session started as *agent*."""
    session_dir = home / ".kiro" / "sessions" / "bucket" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    records = []
    if with_start:
        records.append({"id": "r1", "payload": {"type": "session_start", "agentType": agent}})
    records += [
        {"id": "r2", "payload": {"type": "ContextualHookInvoked", "hookId": f"{agent}#hook-0", "name": "stop-0"}},
        {"id": "r3", "payload": {"type": "user", "content": "whats up"}},
        {"id": "r4", "payload": {"type": "assistant", "content": "hi", "operationType": "Say"}},
    ]
    transcript = session_dir / "messages.jsonl"
    transcript.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return transcript


def test_a_session_started_as_an_agent_is_captured(tmp_path: Path, monkeypatch):
    _registry(monkeypatch, "pika")

    assert _scope(_agent_session(tmp_path, "pika"), session_id="sess_agent-1") is True


def test_an_agent_session_is_recognised_without_session_start(tmp_path: Path, monkeypatch):
    """A transcript can begin mid-session; the agent's own hooks still fire.

    Keying only on session_start.agentType would silently drop these.
    """
    _registry(monkeypatch, "pika")
    transcript = _agent_session(tmp_path, "pika", with_start=False)

    assert _scope(transcript, session_id="sess_agent-1") is True


def test_non_registry_agent_session_is_collected_unattributed(tmp_path: Path, monkeypatch):
    _registry(monkeypatch, "pika")
    transcript = _agent_session(tmp_path, "someone-elses-agent")

    assert _scope(transcript, session_id="sess_agent-1") is True
    assert get_adapter("kiro").resolve_session_agent_identity(transcript, "/work/project") == (None, None)


def test_an_agent_session_is_attributed_to_that_agent(tmp_path: Path, monkeypatch):
    """It has no sub_agent_start, so delegation lookup alone would miss it."""
    import observal_cli.lockfile as lockfile

    monkeypatch.setattr(
        lockfile,
        "get_agent_by_name",
        lambda name, harness, directory=None: (
            {"id": "pika-uuid", "version": "2.0.0", "registry_status": "approved"} if name == "pika" else None
        ),
    )
    ensure_loaded()
    transcript = _agent_session(tmp_path, "pika")

    assert get_adapter("kiro").resolve_session_agent_identity(transcript, "/work") == ("pika-uuid", "2.0.0")


def test_vibe_session_with_delegated_agent_hook_is_not_misattributed(tmp_path: Path, monkeypatch):
    _registry(monkeypatch, "pika")
    transcript = make_ide_session(tmp_path, sub_agents=("pika",))
    with transcript.open("a") as handle:
        handle.write(json.dumps({"payload": {"type": "ContextualHookInvoked", "hookId": "pika#hook-0"}}) + "\n")

    assert _scope(transcript) is True
    assert get_adapter("kiro").resolve_session_agent_identity(transcript, "/work/project") == (None, None)


# ── The injected prompt is written once ───────────────────────────


def test_the_sub_execution_prompt_is_injected_only_once(tmp_path: Path, monkeypatch):
    """Extra records take a fresh line offset on every drain.

    Deduplication is keyed on that offset, so re-emitting stacks duplicate
    prompts instead of replacing them and inflates prompt_count on every hook
    fire - which feeds the insight aggregates.
    """
    _registry(monkeypatch, "test")
    ensure_loaded()
    transcript = make_ide_session(tmp_path, sub_agents=("test",))
    sub = _write_subexecution(transcript, "sub-1")
    _link_subexecution(transcript, "sub-1", "test")
    adapter = get_adapter("kiro")
    source = SessionSource("kiro", "sub-1", sub, cwd="/work/project", cursor_key="parent__sub__sub-1")

    first = adapter.session_extra_records(source, {}, True, home=tmp_path)

    observal_dir = tmp_path / ".observal"
    observal_dir.mkdir(parents=True, exist_ok=True)
    (observal_dir / "sync_state.json").write_text(
        json.dumps({source.checkpoint_key: {"offset": 400, "line_count": 3, "finalized": False}}),
        encoding="utf-8",
    )
    second = adapter.session_extra_records(source, {}, True, home=tmp_path)

    assert len(first) == 1
    assert second == ()


def test_the_injected_prompt_carries_the_time_the_agent_was_asked(tmp_path: Path, monkeypatch):
    """Without it the record inherits upload time, sorts after the reply it
    prompted, and the session renders a negative duration."""
    _registry(monkeypatch, "test")
    ensure_loaded()
    transcript = make_ide_session(tmp_path, sub_agents=("test",))
    sub = _write_subexecution(transcript, "sub-1")
    _link_subexecution(transcript, "sub-1", "test")
    source = SessionSource("kiro", "sub-1", sub, cwd="/work/project", cursor_key="parent__sub__sub-1")

    record = json.loads(get_adapter("kiro").session_extra_records(source, {}, True, home=tmp_path)[0])

    assert record["timestamp"] == "2026-01-01T00:00:00.000Z"
    assert record["payload"]["content"] == "go"
