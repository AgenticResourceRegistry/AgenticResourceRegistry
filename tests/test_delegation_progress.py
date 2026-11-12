# SPDX-FileCopyrightText: 2026 MAOLVLHL <3523624716@qq.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the delegation progress sidecar."""

from __future__ import annotations

import io
import json
import sys
import time
import uuid
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from observal_cli import cmd_delegate
from observal_cli.delegation import local, mcp_server, progress, service, tasks

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the task store and the progress offsets at a temporary directory."""
    monkeypatch.setattr(tasks, "STORE_DIR", tmp_path / "delegations")
    monkeypatch.setattr(service, "SPAWN", None)
    monkeypatch.setattr(service, "check_ready", lambda *args, **kwargs: None)
    monkeypatch.setattr(progress, "_OFFSETS", {})


class FakeAdapter:
    harness_name = "claude-code"

    def __init__(self, script: str) -> None:
        self.script = script

    def headless_command(self, request):
        from observal_cli.harness.protocol import HeadlessPlan

        return HeadlessPlan(argv=[sys.executable, "-c", self.script], stdin=request.message, session_id="child-1")

    def parse_headless_output(self, plan, stdout: str):
        from observal_cli.harness.base import BaseAdapter

        return BaseAdapter.parse_headless_output(self, plan, stdout)


def _task(cwd: Path, state: str = tasks.STATE_WORKING) -> dict:
    """A task in the temporary store, ready to have progress written for it."""
    task = tasks.new_task(
        message="add a test",
        observal={"target": "acme/reviewer", "kind": "agent", "harness": "claude-code", "cwd": str(cwd)},
    )
    tasks.set_status(task, state, "Running.")
    return tasks.save(task)


def _fake_materialize(task, ws, *, harness, adapter):
    """Materialize an agent without contacting the registry."""
    (ws.path / "AGENT.md").write_text("agent profile")
    return "reviewer", {}, "Review carefully."


def _write_events(task_id: str, texts: list[str], *, kind: str = "line", start: int = 1) -> None:
    """Append ready-made progress records without going through the writer."""
    path = progress.progress_path(task_id)
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for offset, text in enumerate(texts):
            handle.write(json.dumps({"seq": start + offset, "kind": kind, "text": text}) + "\n")


def _wait_for(task_id: str, count: int, *, since: int = 0, timeout: float = 5.0) -> list[dict]:
    """Read the sidecar until it holds count events, or the timeout expires."""
    deadline = time.monotonic() + timeout
    seen = since
    events: list[dict] = []
    while time.monotonic() < deadline:
        batch, seen = progress.read(task_id, seen)
        events.extend(batch)
        if len(events) >= count:
            return events
        time.sleep(0.05)
    return events


def test_writer_publishes_each_line_while_the_child_runs(tmp_path: Path) -> None:
    """Lines reach the sidecar while the child is still running."""
    task_id = str(uuid.uuid4())
    stdout_log = tmp_path / "stdout.log"
    writer = progress.ProgressWriter(task_id, stdout_log).start()
    try:
        with stdout_log.open("ab") as handle:
            handle.write(b"first\n")
        events = _wait_for(task_id, 1)
        assert [event["text"] for event in events] == ["first"]
        assert events[0]["seq"] == 1 and events[0]["kind"] == "line"

        with stdout_log.open("ab") as handle:
            handle.write(b'{"type":"assistant","message":"hi"}\n')
        events = _wait_for(task_id, 1, since=1)
        assert [event["kind"] for event in events] == ["status"]
        assert events[0]["event"] == "assistant"
    finally:
        writer.stop()


def test_writer_publishes_the_childs_stderr_under_its_own_stream(tmp_path: Path) -> None:
    """A harness that dies says why on stderr, and the sidecar has to carry it."""
    task_id = str(uuid.uuid4())
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    writer = progress.ProgressWriter(task_id, stdout_log, stderr_log).start()
    try:
        with stdout_log.open("ab") as handle:
            handle.write(b"about to fail\n")
        with stderr_log.open("ab") as handle:
            handle.write(b"error: missing API key\n")
        events = _wait_for(task_id, 2)
    finally:
        writer.stop()
    assert [event["stream"] for event in events] == ["stdout", "stderr"]
    assert events[1]["text"] == "error: missing API key"


def test_writer_waits_for_the_rest_of_a_half_written_line(tmp_path: Path) -> None:
    """A line split across two reads is published once, whole."""
    task_id = str(uuid.uuid4())
    stdout_log = tmp_path / "stdout.log"
    writer = progress.ProgressWriter(task_id, stdout_log).start()
    try:
        with stdout_log.open("ab") as handle:
            handle.write(b"half")
        time.sleep(0.6)
        assert progress.recent(task_id) == []

        with stdout_log.open("ab") as handle:
            handle.write(b" a line\n")
        events = _wait_for(task_id, 1)
        assert [event["text"] for event in events] == ["half a line"]
    finally:
        writer.stop()


def test_stop_publishes_a_final_line_that_never_got_its_newline(tmp_path: Path) -> None:
    """A child killed by a timeout usually leaves its last line unterminated."""
    task_id = str(uuid.uuid4())
    stdout_log = tmp_path / "stdout.log"
    writer = progress.ProgressWriter(task_id, stdout_log).start()
    stdout_log.write_bytes(b"last words")
    writer.stop()
    assert [event["text"] for event in progress.recent(task_id)] == ["last words"]


def test_long_lines_are_capped() -> None:
    """One huge line is truncated instead of growing the sidecar."""
    clean, truncated = progress._clean("x" * 4000)
    assert truncated is True
    assert len(clean) == progress.MAX_LINE_CHARS


def test_credentials_never_reach_the_sidecar() -> None:
    """A token printed by the child is redacted before it is stored."""
    # A line that is only a credential is redacted whole, which is the discovery
    # layer's fail-closed rule; the sidecar inherits it unchanged.
    clean, truncated = progress._clean("leaked sk-proj-" + "A" * 60)
    assert "sk-proj-" not in clean
    assert truncated is False


def test_kind_uses_the_issue_vocabulary_not_the_harness_one() -> None:
    """The four kinds are stable across harnesses; the raw event name rides along."""
    assert progress._classify('{"type":"tool_execution_start","name":"shell"}') == ("tool_call", "tool_execution_start")
    assert progress._classify('{"type":"tool_execution_end"}') == ("tool_result", "tool_execution_end")
    assert progress._classify('{"type":"message_end"}') == ("status", "message_end")
    assert progress._classify('{"type":"assistant","message":"hi"}') == ("status", "assistant")
    assert progress._classify('{"kind":"tool_call"}') == ("tool_call", "tool_call")
    assert progress._classify('{"event":"session_start"}') == ("status", "session_start")
    assert progress._classify('{"seq":1}') == ("status", None)
    assert progress._classify("{not json") == ("line", None)
    assert progress._classify("plain words") == ("line", None)


def test_writer_stops_at_the_size_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The sidecar stops at its size cap and says so in a final record."""
    monkeypatch.setattr(progress, "MAX_FILE_BYTES", 300)
    task_id = str(uuid.uuid4())
    stdout_log = tmp_path / "stdout.log"
    writer = progress.ProgressWriter(task_id, stdout_log).start()
    try:
        with stdout_log.open("ab") as handle:
            handle.write(("a line of output\n" * 200).encode())
        deadline = time.monotonic() + 5.0
        records: list[dict] = []
        while time.monotonic() < deadline:
            records = progress.recent(task_id, limit=500)
            if records and records[-1]["kind"] == "truncated":
                break
            time.sleep(0.05)
    finally:
        writer.stop()
    assert records[-1]["kind"] == "truncated"
    assert records[-1]["stream"] == "stdout"
    assert "read the task logs" in records[-1]["text"]


def test_read_returns_only_new_events_and_advances() -> None:
    """Reading from a cursor returns only the events past it."""
    task_id = str(uuid.uuid4())
    _write_events(task_id, ["e1", "e2", "e3"])
    events, highest = progress.read(task_id, 0)
    assert [event["text"] for event in events] == ["e1", "e2", "e3"]
    assert highest == 3
    assert progress.read(task_id, highest) == ([], 3)

    _write_events(task_id, ["e4"], start=4)
    events, highest = progress.read(task_id, 3)
    assert [event["text"] for event in events] == ["e4"]
    assert highest == 4


def test_read_ignores_a_rewritten_file() -> None:
    """A rewritten sidecar is read from its first line again."""
    task_id = str(uuid.uuid4())
    _write_events(task_id, ["e1", "e2"])
    progress.read(task_id, 0)
    path = progress.progress_path(task_id)
    assert path is not None
    path.write_text(json.dumps({"seq": 1, "kind": "line", "text": "new"}) + "\n", encoding="utf-8")
    events, _ = progress.read(task_id, 0)
    assert [event["text"] for event in events] == ["new"]


def test_unknown_task_ids_never_name_a_path() -> None:
    """A task id that is not a UUID never resolves to a path."""
    assert progress.progress_path("../../etc/passwd") is None
    assert progress.read("../../etc/passwd") == ([], 0)
    assert progress.recent("../../etc/passwd") == []


def test_local_run_publishes_progress_for_the_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A local run publishes the child's output as it arrives."""
    script = "import time\nfor i in range(5):\n    print(f'step {i}', flush=True)\n    time.sleep(0.02)\n"
    monkeypatch.setattr(local, "get_adapter", lambda _h: FakeAdapter(script))
    monkeypatch.setattr(local, "materialize", _fake_materialize)
    task = local.run(_task(tmp_path), save=tasks.save, should_cancel=lambda: False)

    assert task["status"]["state"] == tasks.STATE_COMPLETED, task["status"]
    records = progress.recent(task["id"], limit=10)
    assert [record["text"] for record in records] == [f"step {index}" for index in range(5)]
    assert [record["seq"] for record in records] == [1, 2, 3, 4, 5]


def test_local_run_records_the_childs_stderr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A child that fails publishes the reason it wrote to stderr, not just its stdout."""
    script = (
        "import sys\nprint('working', flush=True)\nsys.stderr.write('error: no model configured\\n')\nsys.exit(2)\n"
    )
    monkeypatch.setattr(local, "get_adapter", lambda _h: FakeAdapter(script))
    monkeypatch.setattr(local, "materialize", _fake_materialize)
    task = local.run(_task(tmp_path), save=tasks.save, should_cancel=lambda: False)

    assert task["status"]["state"] == tasks.STATE_FAILED
    records = progress.recent(task["id"], limit=10)
    assert [record["stream"] for record in records] == ["stdout", "stderr"]
    assert "no model configured" in records[1]["text"]


def test_follow_prints_events_to_stderr_and_returns_the_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """follow prints events to stderr and still returns the task."""
    task = _task(tmp_path)
    _write_events(task["id"], ["reading files"])
    buffer = io.StringIO()
    monkeypatch.setattr(cmd_delegate, "_events_console", Console(file=buffer, width=200))

    result = cmd_delegate._print_progress(task, 0)

    assert "reading files" in buffer.getvalue()
    assert result["id"] == task["id"]


def test_mcp_forwards_progress_events_when_a_token_is_present(tmp_path: Path) -> None:
    """A progress token turns sidecar events into notifications."""
    task = _task(tmp_path, tasks.STATE_COMPLETED)
    _write_events(task["id"], ["call 1", "call 2"], kind="tool")
    out = io.StringIO()
    server = mcp_server.Server(harness="claude-code", parent_id=None, out=out)
    request = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {
            "name": "get_task",
            "arguments": {"task_id": task["id"]},
            "_meta": {"progressToken": "p1"},
        },
    }
    server.serve(io.StringIO(json.dumps(request) + "\n"))

    messages = [json.loads(line) for line in out.getvalue().splitlines()]
    notifications = [message for message in messages if message.get("method") == "notifications/progress"]
    assert [message["params"]["progress"] for message in notifications] == [1, 2]
    assert notifications[0]["params"]["progressToken"] == "p1"
    assert "call 1" in notifications[0]["params"]["message"]
    assert messages[-1]["id"] == 7
    assert "task_id:" in messages[-1]["result"]["content"][0]["text"]


def test_mcp_resumes_a_polled_task_instead_of_replaying_its_backlog(tmp_path: Path) -> None:
    """A client polling get_task sees each record once, not the whole backlog every call."""
    task = _task(tmp_path, tasks.STATE_COMPLETED)
    _write_events(task["id"], ["call 1", "call 2"], kind="tool")
    out = io.StringIO()
    server = mcp_server.Server(harness="claude-code", parent_id=None, out=out)
    request = {
        "jsonrpc": "2.0",
        "id": 11,
        "method": "tools/call",
        "params": {
            "name": "get_task",
            "arguments": {"task_id": task["id"]},
            "_meta": {"progressToken": "p4"},
        },
    }
    server.serve(io.StringIO(json.dumps(request) + "\n"))
    first = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [message["params"]["progress"] for message in first if "method" in message] == [1, 2]

    _write_events(task["id"], ["call 3"], kind="tool", start=3)
    out.seek(0)
    out.truncate(0)
    server.serve(io.StringIO(json.dumps(request) + "\n"))
    second = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [message["params"]["progress"] for message in second if "method" in message] == [3]
    assert second[-1]["id"] == 11


def test_mcp_emits_no_progress_without_a_token(tmp_path: Path) -> None:
    """Without a token the server sends no notifications."""
    task = _task(tmp_path, tasks.STATE_COMPLETED)
    _write_events(task["id"], ["call 1"], kind="tool")
    out = io.StringIO()
    server = mcp_server.Server(harness="claude-code", parent_id=None, out=out)
    request = {
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/call",
        "params": {"name": "get_task", "arguments": {"task_id": task["id"]}},
    }
    server.serve(io.StringIO(json.dumps(request) + "\n"))
    messages = [json.loads(line) for line in out.getvalue().splitlines()]
    assert len(messages) == 1
    assert messages[0]["id"] == 8


def test_read_serves_a_second_reader_from_the_beginning() -> None:
    """The per-process offset cache must not hide earlier events from a new caller."""
    task_id = str(uuid.uuid4())
    _write_events(task_id, ["e1", "e2", "e3"])
    progress.read(task_id, 0)

    events, highest = progress.read(task_id, 0)

    assert [event["text"] for event in events] == ["e1", "e2", "e3"]
    assert highest == 3


def test_read_hands_back_a_backlog_page_by_page() -> None:
    """More events than one page must not advance the cursor past the page."""
    task_id = str(uuid.uuid4())
    _write_events(task_id, [f"e{index}" for index in range(1, 251)])

    first, highest = progress.read(task_id, 0)
    assert len(first) == progress.READ_LIMIT
    assert highest == progress.READ_LIMIT

    second, highest = progress.read(task_id, highest)
    assert [event["text"] for event in second] == [f"e{index}" for index in range(201, 251)]
    assert highest == 250


def test_follow_prints_the_backlog_before_returning_a_settled_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A child that finished between two polls still gets its last lines shown."""
    task = _task(tmp_path, tasks.STATE_COMPLETED)
    _write_events(task["id"], [f"e{index}" for index in range(1, 251)])
    buffer = io.StringIO()
    monkeypatch.setattr(cmd_delegate, "_events_console", Console(file=buffer, width=200))

    result = cmd_delegate._print_progress(task, 0)

    assert result["id"] == task["id"]
    assert "e250" in buffer.getvalue()


def test_follow_returns_at_once_when_the_wait_budget_is_spent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """follow returns immediately when no wait budget is left."""
    task = _task(tmp_path)
    buffer = io.StringIO()
    monkeypatch.setattr(cmd_delegate, "_events_console", Console(file=buffer, width=200))

    started = time.monotonic()
    result = cmd_delegate._print_progress(task, -1)

    assert result["id"] == task["id"]
    assert time.monotonic() - started < 2


def test_mcp_forwards_a_backlog_larger_than_one_page(tmp_path: Path) -> None:
    """A backlog bigger than one page still reaches the client."""
    task = _task(tmp_path, tasks.STATE_COMPLETED)
    _write_events(task["id"], [f"call {index}" for index in range(1, 251)])
    out = io.StringIO()
    server = mcp_server.Server(harness="claude-code", parent_id=None, out=out)
    request = {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {
            "name": "get_task",
            "arguments": {"task_id": task["id"]},
            "_meta": {"progressToken": "p2"},
        },
    }
    server.serve(io.StringIO(json.dumps(request) + "\n"))

    messages = [json.loads(line) for line in out.getvalue().splitlines()]
    notifications = [message for message in messages if message.get("method") == "notifications/progress"]
    assert [message["params"]["progress"] for message in notifications] == list(range(1, 251))


def test_follow_prints_a_settled_backlog_beyond_the_page_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A settled task's whole backlog is printed, not just the pages the budget covers."""
    task = _task(tmp_path, tasks.STATE_COMPLETED)
    _write_events(task["id"], [f"e{index}" for index in range(1, 8001)])
    buffer = io.StringIO()
    monkeypatch.setattr(cmd_delegate, "_events_console", Console(file=buffer, width=200))

    cmd_delegate._print_progress(task, 0)

    printed = buffer.getvalue()
    assert "e8000" in printed
    assert printed.count("\n") >= 8000


def test_drain_empties_a_settled_tasks_backlog() -> None:
    """Passing rounds=None drains a fixed backlog instead of stopping at the budget."""
    task_id = str(uuid.uuid4())
    _write_events(task_id, [f"e{index}" for index in range(1, 8001)])

    bounded, bounded_seq = progress.drain(task_id, 0)
    assert len(bounded) == progress.READ_LIMIT * progress.DRAIN_ROUNDS
    assert bounded_seq < 8000

    events, highest = progress.drain(task_id, 0, rounds=None)
    assert len(events) == 8000
    assert highest == 8000


def test_mcp_spends_the_calls_wait_budget_from_its_start(tmp_path: Path) -> None:
    """The budget covers the whole tool call, so a spent one returns without waiting."""
    task = _task(tmp_path)
    out = io.StringIO()
    server = mcp_server.Server(harness="claude-code", parent_id=None, out=out)

    started = time.monotonic()
    mcp_server.Server._with_progress(server, task, 45, "p3", started=started - 45)

    assert time.monotonic() - started < 2


def test_status_shows_recent_output_while_the_task_runs(tmp_path: Path) -> None:
    """A status check used to say nothing at all until the child exited."""
    from typer.testing import CliRunner

    task = _task(tmp_path)
    _write_events(task["id"], ["reading files", "editing app.py"])
    result = CliRunner().invoke(cmd_delegate.delegate_app, ["status", task["id"]], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "reading files" in result.output
    assert "editing app.py" in result.output
