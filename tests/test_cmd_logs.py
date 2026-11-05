# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Behavioral coverage for the local and remote log viewer."""

from __future__ import annotations

import builtins
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, call

import httpx
import pytest
import typer
from rich.text import Text
from typer.testing import CliRunner

from observal_cli import client, config
from observal_cli import cmd_logs as logs

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()
LONG_OPTION = "-" * 2


class RecordingConsole:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def print(self, *values: object, **kwargs: object) -> None:
        self.calls.append((values, kwargs))


class StreamResponse:
    def __init__(self, status_code: int = 200, lines: list[object] | None = None) -> None:
        self.status_code = status_code
        self.lines = lines or []
        self.enter_count = 0
        self.iter_count = 0
        self.exits: list[tuple[type[BaseException] | None, BaseException | None]] = []

    def __enter__(self):
        self.enter_count += 1
        return self

    def __exit__(self, exc_type, exc, _traceback) -> bool:
        self.exits.append((exc_type, exc))
        return False

    def iter_lines(self):
        self.iter_count += 1
        for item in self.lines:
            if isinstance(item, BaseException):
                raise item
            yield item


class TrackedFile:
    def __init__(self, *, iter_lines: list[str] | None = None, reads: list[object] | None = None) -> None:
        self.iter_lines = iter_lines or []
        self.reads = iter(reads or [])
        self.enter_count = 0
        self.exits: list[type[BaseException] | None] = []
        self.seek_calls: list[tuple[int, int]] = []
        self.readline_count = 0

    def __enter__(self):
        self.enter_count += 1
        return self

    def __exit__(self, exc_type, _exc, _traceback) -> bool:
        self.exits.append(exc_type)
        return False

    def __iter__(self):
        return iter(self.iter_lines)

    def seek(self, offset: int, whence: int) -> None:
        self.seek_calls.append((offset, whence))

    def readline(self) -> str:
        self.readline_count += 1
        try:
            item = next(self.reads)
        except StopIteration:
            return ""
        if isinstance(item, BaseException):
            raise item
        return item


def _blocked(name: str):
    def fail(*args, **kwargs):
        raise AssertionError(f"unmocked boundary: {name}, args={args}, kwargs={kwargs}")

    return fail


@pytest.fixture
def boundaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    console = RecordingConsole()
    console_factory = MagicMock(return_value=console)
    stream = MagicMock(side_effect=_blocked("httpx.stream"))
    get_config = MagicMock(side_effect=_blocked("config.get_or_exit"))
    get_version = MagicMock(side_effect=_blocked("client._get_cli_version"))
    sleep = MagicMock(side_effect=_blocked("time.sleep"))
    json_lines: list[dict] = []

    monkeypatch.setattr(logs, "Console", console_factory)
    monkeypatch.setattr(logs, "LOG_PATH", tmp_path / "dev.log")
    monkeypatch.setattr(logs.time, "sleep", sleep)
    monkeypatch.setattr(logs, "output_json_line", json_lines.append)
    monkeypatch.setattr(httpx, "stream", stream)
    monkeypatch.setattr(config, "get_or_exit", get_config)
    monkeypatch.setattr(client, "_get_cli_version", get_version)

    return SimpleNamespace(
        console=console,
        console_factory=console_factory,
        stream=stream,
        get_config=get_config,
        get_version=get_version,
        sleep=sleep,
        log_path=logs.LOG_PATH,
        json_lines=json_lines,
    )


def _allow_remote(boundaries: SimpleNamespace, response: StreamResponse) -> None:
    boundaries.get_config.side_effect = None
    boundaries.get_config.return_value = {
        "server_url": "https://registry.example.test///",
        "access_token": "fake-access-token",
    }
    boundaries.get_version.side_effect = None
    boundaries.get_version.return_value = "4.2.1"
    boundaries.stream.side_effect = None
    boundaries.stream.return_value = response


def _assert_remote_request(boundaries: SimpleNamespace, *, params: dict[str, str]) -> None:
    boundaries.stream.assert_called_once_with(
        "GET",
        "https://registry.example.test/api/v1/admin/logs/stream",
        params=params,
        headers={
            "Authorization": "Bearer fake-access-token",
            "X-Observal-CLI-Version": "4.2.1",
        },
        timeout=None,
    )
    boundaries.get_config.assert_called_once_with()
    boundaries.get_version.assert_called_once_with()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("trace", 0),
        ("DEBUG", 1),
        ("Info", 2),
        ("warning", 3),
        ("error", 4),
        ("critical", 5),
        ("unknown", 0),
    ],
)
def test_level_rank_is_case_insensitive_and_unknown_levels_use_lowest_rank(value: str, expected: int) -> None:
    assert logs._level_rank(value) == expected


@pytest.mark.parametrize("level", list(logs.LEVEL_STYLES))
def test_parse_level_recognizes_supported_levels(level: str) -> None:
    assert logs._parse_level(f"12:00:00 | {level} | worker:run:1") == level
    assert logs._parse_level(f"12:00:00 | {level}") == level


def test_parse_level_returns_none_for_unstructured_lines() -> None:
    assert logs._parse_level("unstructured output") is None


def test_print_line_supports_plain_colored_and_unstructured_output() -> None:
    console = RecordingConsole()

    logs._print_line(console, "12:00 | ERROR | worker:run:1 - failed   ", no_color=True)
    logs._print_line(console, "12:00 | ERROR | worker:run:1 - failed\n", no_color=False)
    logs._print_line(console, "unstructured line\n", no_color=False)

    assert console.calls[0] == (("12:00 | ERROR | worker:run:1 - failed",), {"highlight": False})
    colored = console.calls[1][0][0]
    assert isinstance(colored, Text)
    assert colored.plain == "12:00 | ERROR | worker:run:1 - failed"
    assert [(span.start, span.end, span.style) for span in colored.spans] == [
        (0, len(colored), logs.LEVEL_STYLES["ERROR"])
    ]
    assert console.calls[1][1] == {}
    assert console.calls[2] == (("unstructured line",), {})


def test_print_entry_formats_server_json_for_plain_and_colored_output() -> None:
    console = RecordingConsole()
    entry = {
        "timestamp": "2026-05-17T12:34:56.789Z",
        "level": "WARNING",
        "event": "queue delayed",
        "logger_name": "services.worker",
        "function": "drain",
        "line": 42,
    }

    logs._print_entry(console, entry, no_color=True)
    logs._print_entry(console, entry, no_color=False)
    logs._print_entry(console, {"timestamp": "short", "event": "defaults"}, no_color=True)

    formatted = "12:34:56.789 | WARNING | services.worker:drain:42 - queue delayed"
    assert console.calls[0] == ((formatted,), {"highlight": False})
    colored = console.calls[1][0][0]
    assert isinstance(colored, Text)
    assert colored.plain == formatted
    assert [(span.start, span.end, span.style) for span in colored.spans] == [
        (0, len(colored), logs.LEVEL_STYLES["WARNING"])
    ]
    assert console.calls[2] == (("short | INFO    | :: - defaults",), {"highlight": False})


@pytest.mark.parametrize(
    ("level", "filter_text", "expected_params"),
    [
        ("WARNING", "ClickHouse", {"level": "WARNING", "filter": "ClickHouse"}),
        ("debug", "", {"level": "debug"}),
        ("verbose", "", {"level": "verbose"}),
    ],
)
def test_remote_stream_builds_exact_authenticated_request_and_finishes_cleanly(
    boundaries: SimpleNamespace,
    level: str,
    filter_text: str,
    expected_params: dict[str, str],
) -> None:
    response = StreamResponse()
    _allow_remote(boundaries, response)

    result = logs._stream_remote(
        boundaries.console,
        level=level,
        filter_text=filter_text,
        no_color=True,
    )

    assert result is None
    _assert_remote_request(boundaries, params=expected_params)
    assert response.enter_count == 1
    assert response.iter_count == 1
    assert response.exits == [(None, None)]
    assert boundaries.console.calls == [
        (("[dim]Connecting to https://registry.example.test …[/dim]",), {}),
        (("[dim]- Streaming (Ctrl+C to stop) -[/dim]\n",), {}),
    ]
    boundaries.sleep.assert_not_called()


@pytest.mark.parametrize(("status", "exit_code"), [(401, 3), (403, 4), (418, 9), (503, 9)])
def test_remote_http_errors_are_categorized_and_close_response(
    boundaries: SimpleNamespace,
    status: int,
    exit_code: int,
) -> None:
    response = StreamResponse(status_code=status)
    _allow_remote(boundaries, response)

    with pytest.raises(typer.Exit) as raised:
        logs._stream_remote(boundaries.console, level="ERROR", filter_text="", no_color=False)

    assert raised.value.exit_code == exit_code
    assert response.enter_count == 1
    assert response.iter_count == 0
    assert response.exits[0][1] is raised.value
    assert boundaries.console.calls == [(("[dim]Connecting to https://registry.example.test …[/dim]",), {})]
    boundaries.sleep.assert_not_called()


@pytest.mark.parametrize("failure_during_iteration", [False, True])
def test_connection_failure_makes_one_attempt_then_exits_without_retry(
    boundaries: SimpleNamespace,
    failure_during_iteration: bool,
) -> None:
    failure = httpx.ConnectError("offline", request=httpx.Request("GET", "https://registry.example.test"))
    response = StreamResponse(lines=[failure])
    _allow_remote(boundaries, response)
    if not failure_during_iteration:
        boundaries.stream.side_effect = failure

    with pytest.raises(typer.Exit) as raised:
        logs._stream_remote(boundaries.console, level="DEBUG", filter_text="", no_color=True)

    assert raised.value.exit_code == 9
    assert boundaries.stream.call_count == 1
    expected_console = [(("[dim]Connecting to https://registry.example.test …[/dim]",), {})]
    if failure_during_iteration:
        expected_console.append((("[dim]- Streaming (Ctrl+C to stop) -[/dim]\n",), {}))
    assert boundaries.console.calls == expected_console
    boundaries.sleep.assert_not_called()
    if failure_during_iteration:
        assert response.exits == [(httpx.ConnectError, failure)]
    else:
        assert response.enter_count == 0
        assert response.exits == []


def test_read_timeout_is_categorized_and_response_is_closed(boundaries: SimpleNamespace) -> None:
    failure = httpx.ReadTimeout("stalled", request=httpx.Request("GET", "https://registry.example.test"))
    response = StreamResponse(lines=[failure])
    _allow_remote(boundaries, response)

    with pytest.raises(typer.Exit) as raised:
        logs._stream_remote(boundaries.console, level="INFO", filter_text="", no_color=True)

    assert raised.value.exit_code == 9
    assert boundaries.stream.call_count == 1
    assert response.exits == [(httpx.ReadTimeout, failure)]
    assert boundaries.console.calls == [
        (("[dim]Connecting to https://registry.example.test …[/dim]",), {}),
        (("[dim]- Streaming (Ctrl+C to stop) -[/dim]\n",), {}),
    ]
    boundaries.sleep.assert_not_called()


def test_configuration_failure_propagates_before_version_or_http_boundaries(boundaries: SimpleNamespace) -> None:
    failure = typer.Exit(1)
    boundaries.get_config.side_effect = failure

    with pytest.raises(typer.Exit) as raised:
        logs._stream_remote(boundaries.console, level="DEBUG", filter_text="", no_color=True)

    assert raised.value is failure
    boundaries.get_config.assert_called_once_with()
    boundaries.get_version.assert_not_called()
    boundaries.stream.assert_not_called()
    assert boundaries.console.calls == []


def test_recent_remote_logs_use_finite_endpoint_and_limit(boundaries: SimpleNamespace, monkeypatch) -> None:
    entry = {"timestamp": "2026-05-17T12:34:56Z", "level": "INFO", "event": "ready"}
    get = MagicMock(return_value={"entries": [entry], "count": 1, "buffer_size": 10})
    monkeypatch.setattr(client, "get", get)

    logs._recent_remote(
        boundaries.console,
        level="INFO",
        filter_text="worker",
        lines=2,
        no_color=True,
        output="json",
    )

    get.assert_called_once_with(
        "/api/v1/admin/logs",
        {"level": "INFO", "limit": 2, "filter": "worker"},
        operation="Read recent server logs",
        resource="server logs",
    )
    assert boundaries.json_lines == [{"event": "log", "source": "remote", "log": entry}]
    assert boundaries.console.calls == []


def test_remote_sse_ignores_framing_and_formats_valid_json(boundaries: SimpleNamespace) -> None:
    entry = {
        "timestamp": "2026-05-17T12:34:56.789Z",
        "level": "ERROR",
        "event": "first line\nsecond line",
        "logger_name": "services.ingest",
        "function": "push",
        "line": 73,
    }
    response = StreamResponse(
        lines=[
            "",
            ": keepalive",
            ":another-comment",
            "event: log",
            "id: 19",
            "retry: 5000",
            f"data:{json.dumps(entry)}",
            f"data: {json.dumps(entry)}",
            "",
        ]
    )
    _allow_remote(boundaries, response)

    logs._stream_remote(boundaries.console, level="ERROR", filter_text="ingest", no_color=True)

    assert response.exits == [(None, None)]
    assert boundaries.console.calls[-1] == (
        ("12:34:56.789 | ERROR   | services.ingest:push:73 - first line\nsecond line",),
        {"highlight": False},
    )
    assert len(boundaries.console.calls) == 4


def test_malformed_sse_json_is_categorized(boundaries: SimpleNamespace) -> None:
    response = StreamResponse(lines=["data: {not-json", "data: "])
    _allow_remote(boundaries, response)

    with pytest.raises(typer.Exit) as raised:
        logs._stream_remote(boundaries.console, level="DEBUG", filter_text="", no_color=False)

    assert raised.value.exit_code == 9
    assert response.exits[0][1] is raised.value


def test_multiline_sse_json_fails_on_the_first_malformed_record(boundaries: SimpleNamespace) -> None:
    first = '{"timestamp":"2026-05-17T12:34:56.789Z","level":"INFO",'
    second = '"event":"joined","logger_name":"svc","function":"run","line":7}'
    response = StreamResponse(lines=[f"data: {first}", f"data: {second}", ""])
    _allow_remote(boundaries, response)

    with pytest.raises(typer.Exit) as raised:
        logs._stream_remote(boundaries.console, level="INFO", filter_text="", no_color=True)

    assert raised.value.exit_code == 9
    assert response.exits[0][1] is raised.value


@pytest.mark.parametrize("payload", ["[]", '"text"', "null"])
def test_json_payloads_that_are_not_objects_propagate_shape_error_and_close_stream(
    boundaries: SimpleNamespace,
    payload: str,
) -> None:
    response = StreamResponse(lines=[f"data: {payload}"])
    _allow_remote(boundaries, response)

    with pytest.raises(typer.Exit) as raised:
        logs._stream_remote(boundaries.console, level="INFO", filter_text="", no_color=True)

    assert raised.value.exit_code == 9
    assert response.exits[0][1] is raised.value
    assert len(boundaries.console.calls) == 2


def test_remote_interrupt_closes_stream_prints_stopped_and_exits_zero(
    boundaries: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = StreamResponse(lines=[KeyboardInterrupt()])
    _allow_remote(boundaries, response)
    exit_process = MagicMock(side_effect=SystemExit(0))
    monkeypatch.setattr(logs.sys, "exit", exit_process)

    assert logs._stream_remote(boundaries.console, level="DEBUG", filter_text="", no_color=True) is None

    assert response.exits == [(KeyboardInterrupt, response.lines[0])]
    assert boundaries.console.calls[-1] == (("\n[dim]Stopped.[/dim]",), {})
    exit_process.assert_not_called()


def test_remote_finite_mode_dispatches_without_opening_stream(boundaries: SimpleNamespace, monkeypatch) -> None:
    recent = MagicMock()
    stream = MagicMock()
    monkeypatch.setattr(logs, "_recent_remote", recent)
    monkeypatch.setattr(logs, "_stream_remote", stream)

    result = logs.logs(
        level="CRITICAL",
        filter_text="worker",
        lines=3,
        no_follow=True,
        remote=True,
        no_color=True,
    )

    assert result is None
    boundaries.console_factory.assert_called_once_with(stderr=True, no_color=True)
    recent.assert_called_once_with(
        boundaries.console,
        level="CRITICAL",
        filter_text="worker",
        lines=3,
        no_color=True,
        output="table",
    )
    stream.assert_not_called()
    boundaries.sleep.assert_not_called()


def test_missing_local_file_prints_guidance_and_exits_one(boundaries: SimpleNamespace) -> None:
    with pytest.raises(typer.Exit) as raised:
        logs.logs(
            level="DEBUG",
            filter_text="",
            lines=20,
            no_follow=False,
            remote=False,
            no_color=False,
        )

    assert raised.value.exit_code == 5
    boundaries.console_factory.assert_called_once_with(stderr=True, no_color=False)
    assert boundaries.console.calls == []


def test_finite_local_mode_tails_then_applies_level_and_case_insensitive_text_filters(
    boundaries: SimpleNamespace,
) -> None:
    boundaries.log_path.write_text(
        "old | ERROR | svc:run:1 - needle omitted by tail\n"
        "new | INFO | svc:run:2 - needle below level\n"
        "new | WARNING | svc:run:3 - unrelated\n"
        "new | ERROR | svc:run:4 - NEEDLE kept\n"
        "unstructured needle kept\n"
        "new | CRITICAL | svc:run:5 - needle kept\n"
    )

    result = logs.logs(
        level="WARNING",
        filter_text="needle",
        lines=5,
        no_follow=True,
        remote=False,
        no_color=True,
    )

    assert result is None
    assert boundaries.console.calls == [
        (("new | ERROR | svc:run:4 - NEEDLE kept",), {"highlight": False}),
        (("unstructured needle kept",), {"highlight": False}),
        (("new | CRITICAL | svc:run:5 - needle kept",), {"highlight": False}),
    ]
    boundaries.sleep.assert_not_called()


@pytest.mark.parametrize("lines", [0, -3])
def test_nonpositive_tail_length_reads_no_initial_lines(boundaries: SimpleNamespace, lines: int) -> None:
    boundaries.log_path.write_text("new | ERROR | svc:run:1 - message\n")

    result = logs.logs(
        level="DEBUG",
        filter_text="",
        lines=lines,
        no_follow=True,
        remote=False,
        no_color=True,
    )

    assert result is None
    assert boundaries.console.calls == []


def test_initial_local_read_error_is_categorized(
    boundaries: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundaries.log_path.touch()
    failure = OSError("permission denied")
    open_file = MagicMock(side_effect=failure)
    monkeypatch.setattr(builtins, "open", open_file)

    with pytest.raises(typer.Exit) as raised:
        logs.logs(
            level="DEBUG",
            filter_text="",
            lines=20,
            no_follow=True,
            remote=False,
            no_color=True,
        )

    assert raised.value.exit_code == 9
    open_file.assert_called_once_with(boundaries.log_path)
    assert boundaries.console.calls == []


def test_follow_mode_filters_new_lines_sleeps_closes_file_and_exits_zero_on_interrupt(
    boundaries: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundaries.log_path.touch()
    initial = TrackedFile()
    following = TrackedFile(
        reads=[
            "new | INFO | svc:run:1 - needle below level\n",
            "new | ERROR | svc:run:2 - NEEDLE kept\n",
            "",
        ]
    )
    open_file = MagicMock(side_effect=[initial, following])
    monkeypatch.setattr(builtins, "open", open_file)
    boundaries.sleep.side_effect = KeyboardInterrupt()
    exit_process = MagicMock(side_effect=SystemExit(0))
    monkeypatch.setattr(logs.sys, "exit", exit_process)

    with pytest.raises(SystemExit) as raised:
        logs.logs(
            level="WARNING",
            filter_text="needle",
            lines=0,
            no_follow=False,
            remote=False,
            no_color=True,
        )

    assert raised.value.code == 0
    assert open_file.call_args_list == [call(boundaries.log_path), call(boundaries.log_path)]
    assert initial.enter_count == 1
    assert initial.exits == [None]
    assert following.enter_count == 1
    assert following.seek_calls == [(0, 2)]
    assert following.readline_count == 3
    assert following.exits == [KeyboardInterrupt]
    assert boundaries.sleep.call_args_list == [call(0.1)]
    assert boundaries.console.calls == [
        ((f"\n[dim]- Following {boundaries.log_path} (Ctrl+C to stop) -[/dim]\n",), {}),
        (("new | ERROR | svc:run:2 - NEEDLE kept",), {"highlight": False}),
        (("\n[dim]Stopped.[/dim]",), {}),
    ]
    exit_process.assert_called_once_with(0)


def test_follow_open_error_propagates_after_initial_file_is_closed(
    boundaries: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundaries.log_path.touch()
    initial = TrackedFile()
    failure = OSError("file disappeared")
    open_file = MagicMock(side_effect=[initial, failure])
    monkeypatch.setattr(builtins, "open", open_file)

    with pytest.raises(typer.Exit) as raised:
        logs.logs(
            level="DEBUG",
            filter_text="",
            lines=0,
            no_follow=False,
            remote=False,
            no_color=True,
        )

    assert raised.value.exit_code == 9
    assert initial.exits == [None]
    assert open_file.call_args_list == [call(boundaries.log_path), call(boundaries.log_path)]
    assert boundaries.console.calls == [((f"\n[dim]- Following {boundaries.log_path} (Ctrl+C to stop) -[/dim]\n",), {})]
    boundaries.sleep.assert_not_called()


def test_user_facing_ops_logs_route_uses_defaults_and_returns_success(
    boundaries: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundaries.log_path.write_text("".join(f"line {index}\n" for index in range(21)))
    from observal_cli import main, optic

    setup_optic = MagicMock()
    migrate = MagicMock()
    lockfile = MagicMock()
    monkeypatch.setattr(optic, "setup_optic", setup_optic)
    monkeypatch.setattr(main, "_migrate_legacy_mcp_configs", migrate)
    monkeypatch.setattr(main, "_try_lockfile_migration", lockfile)

    result = runner.invoke(
        main.app,
        ["ops", "logs", f"{LONG_OPTION}no-follow", f"{LONG_OPTION}no-color"],
    )

    assert result.exit_code == 0, result.output
    assert result.exception is None
    setup_optic.assert_called_once_with(debug=False, verbose=False)
    migrate.assert_called_once_with()
    lockfile.assert_called_once_with()
    assert [values[0] for values, _kwargs in boundaries.console.calls] == [f"line {index}" for index in range(1, 21)]
    assert all(kwargs == {"highlight": False} for _values, kwargs in boundaries.console.calls)


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ([f"{LONG_OPTION}lines", "many"], "is not a valid integer"),
        ([f"{LONG_OPTION}output", "yaml"], "is not one of"),
    ],
)
def test_cli_validation_exits_two_before_constructing_console(
    boundaries: SimpleNamespace,
    arguments: list[str],
    message: str,
) -> None:
    result = runner.invoke(logs.logs_app, arguments)

    assert result.exit_code == 2, result.output
    assert message in result.output
    boundaries.console_factory.assert_not_called()


def test_remote_json_lines_emit_one_record_per_entry_without_human_banners(boundaries: SimpleNamespace) -> None:
    entry = {"timestamp": "2026-05-17T12:34:56Z", "level": "INFO", "event": "ready"}
    response = StreamResponse(lines=[f"data: {json.dumps(entry)}"])
    _allow_remote(boundaries, response)

    logs._stream_remote(
        boundaries.console,
        level="INFO",
        filter_text="",
        no_color=True,
        output="json",
    )

    assert boundaries.json_lines == [{"event": "log", "source": "remote", "log": entry}]
    assert boundaries.console.calls == []


def test_local_json_lines_emit_filtered_finite_records(boundaries: SimpleNamespace) -> None:
    boundaries.log_path.write_text("now | INFO | svc:run:1 - ignored\nnow | ERROR | svc:run:2 - kept\n")

    logs.logs(
        level="WARNING",
        filter_text="kept",
        lines=20,
        no_follow=True,
        remote=False,
        no_color=True,
        output="json",
    )

    assert boundaries.json_lines == [
        {
            "event": "log",
            "source": "local",
            "level": "ERROR",
            "line": "now | ERROR | svc:run:2 - kept",
        }
    ]
    assert boundaries.console.calls == []
