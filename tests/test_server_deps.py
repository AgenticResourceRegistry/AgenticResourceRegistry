# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Isolated behavioral tests for standalone server dependency installation."""

from __future__ import annotations

import hashlib
import stat
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from observal_cli.server import deps


class RecordingConsole:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def print(self, *values: object) -> None:
        self.messages.append(" ".join(str(value) for value in values))


class StreamingResponse:
    def __init__(
        self,
        events: list[object],
        chunks: list[bytes],
        *,
        content_length: str | None = None,
        status_error: Exception | None = None,
        iteration_error: Exception | None = None,
    ) -> None:
        self.events = events
        self.chunks = chunks
        self.headers = {} if content_length is None else {"content-length": content_length}
        self.status_error = status_error
        self.iteration_error = iteration_error

    def __enter__(self):
        self.events.append("response enter")
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.events.append(("response exit", exc))
        return False

    def raise_for_status(self) -> None:
        self.events.append("status")
        if self.status_error is not None:
            raise self.status_error

    def iter_bytes(self, *, chunk_size: int):
        self.events.append(("iterate", chunk_size))
        yield from self.chunks
        if self.iteration_error is not None:
            raise self.iteration_error


class RecordingProgress:
    def __init__(self, events: list[object], columns: tuple[object, ...]) -> None:
        self.events = events
        self.events.append(("progress columns", [type(column).__name__ for column in columns]))

    def __enter__(self):
        self.events.append("progress enter")
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.events.append(("progress exit", exc))
        return False

    def add_task(self, description: str, *, total: int | None) -> int:
        self.events.append(("add task", description, total))
        return 17

    def update(self, task: int, *, advance: int) -> None:
        self.events.append(("update", task, advance))


class TemporaryDirectoryFactory:
    def __init__(self, base: Path, events: list[object]) -> None:
        self.base = base
        self.events = events
        self.count = 0
        self.exits: list[BaseException | None] = []

    def __call__(self):
        self.count += 1
        path = self.base / f"temporary-{self.count}"
        self.events.append(("temporary create", path))
        factory = self

        class TemporaryDirectory:
            def __enter__(self):
                path.mkdir(parents=True)
                factory.events.append(("temporary enter", path))
                return str(path)

            def __exit__(self, exc_type, exc, traceback) -> bool:
                factory.events.append(("temporary exit", path, exc))
                factory.exits.append(exc)
                return False

        return TemporaryDirectory()


def blocked(name: str):
    def fail(*args: object, **kwargs: object):
        raise AssertionError(f"unmocked boundary: {name}, args={args}, kwargs={kwargs}")

    return fail


def status_error(url: str, status_code: int = 503) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("request rejected", request=request, response=response)


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    root = tmp_path / "home" / ".observal"
    console = RecordingConsole()
    monkeypatch.setattr(deps, "BIN_DIR", root / "bin")
    monkeypatch.setattr(deps, "console", console)
    monkeypatch.setattr(deps.httpx, "get", blocked("httpx.get"))
    monkeypatch.setattr(deps.httpx, "stream", blocked("httpx.stream"))
    monkeypatch.setattr(deps, "get_bin_paths", blocked("get_bin_paths"))
    monkeypatch.setattr(deps, "get_dep_urls", blocked("get_dep_urls"))
    return SimpleNamespace(root=root, bin_dir=root / "bin", console=console, tmp=tmp_path)


def configure_download(
    monkeypatch: pytest.MonkeyPatch,
    events: list[object],
    response: StreamingResponse,
) -> MagicMock:
    monkeypatch.setattr(deps, "Progress", lambda *columns: RecordingProgress(events, columns))

    def stream(method: str, url: str, **kwargs: object) -> StreamingResponse:
        events.append(("stream", method, url, kwargs))
        return response

    stream_mock = MagicMock(side_effect=stream)
    monkeypatch.setattr(deps.httpx, "stream", stream_mock)
    return stream_mock


def configure_temporary_directories(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    events: list[object],
) -> TemporaryDirectoryFactory:
    factory = TemporaryDirectoryFactory(runtime.tmp, events)
    monkeypatch.setattr(deps.tempfile, "TemporaryDirectory", factory)
    return factory


def test_checksum_url_uses_release_constants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "GITHUB_REPO", "example/project")
    monkeypatch.setattr(deps, "DEPS_RELEASE_TAG", "deps/v9")

    assert deps._checksum_url() == "https://github.com/example/project/releases/download/deps/v9/checksums.txt"


@pytest.mark.parametrize(("content_length", "expected_total"), [("7", 7), (None, None), ("0", None)])
def test_download_file_streams_chunks_with_exact_progress_contract(
    content_length: str | None,
    expected_total: int | None,
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    response = StreamingResponse(events, [b"abc", b"defg"], content_length=content_length)
    configure_download(monkeypatch, events, response)
    destination = runtime.tmp / "artifact.tar.gz"

    assert deps._download_file("https://downloads.example/artifact", destination, "  postgres") is None

    assert destination.read_bytes() == b"abcdefg"
    assert events == [
        ("progress columns", ["TextColumn", "BarColumn", "DownloadColumn", "TransferSpeedColumn"]),
        "progress enter",
        (
            "stream",
            "GET",
            "https://downloads.example/artifact",
            {"follow_redirects": True, "timeout": 300},
        ),
        "response enter",
        "status",
        ("add task", "  postgres", expected_total),
        ("iterate", 65536),
        ("update", 17, 3),
        ("update", 17, 4),
        ("response exit", None),
        ("progress exit", None),
    ]


def test_download_file_preserves_status_error_and_closes_contexts(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    failure = status_error("https://downloads.example/artifact")
    response = StreamingResponse(events, [], status_error=failure)
    configure_download(monkeypatch, events, response)
    destination = runtime.tmp / "artifact.tar.gz"

    with pytest.raises(httpx.HTTPStatusError) as error:
        deps._download_file("https://downloads.example/artifact", destination, "artifact")

    assert error.value is failure
    assert not destination.exists()
    assert events[-2:] == [("response exit", failure), ("progress exit", failure)]
    assert not any(isinstance(event, tuple) and event[0] == "add task" for event in events)


def test_download_file_preserves_timeout_and_partial_download(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    request = httpx.Request("GET", "https://downloads.example/artifact")
    failure = httpx.ReadTimeout("download stalled", request=request)
    response = StreamingResponse(events, [b"partial"], iteration_error=failure)
    configure_download(monkeypatch, events, response)
    destination = runtime.tmp / "artifact.tar.gz"

    with pytest.raises(httpx.ReadTimeout) as error:
        deps._download_file("https://downloads.example/artifact", destination, "artifact")

    assert error.value is failure
    assert destination.read_bytes() == b"partial"
    assert ("update", 17, 7) in events
    assert events[-2:] == [("response exit", failure), ("progress exit", failure)]


def test_download_file_rejects_malformed_content_length_before_opening_destination(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    response = StreamingResponse(events, [], content_length="many")
    configure_download(monkeypatch, events, response)
    destination = runtime.tmp / "artifact.tar.gz"

    with pytest.raises(ValueError, match="invalid literal"):
        deps._download_file("https://downloads.example/artifact", destination, "artifact")

    assert not destination.exists()
    assert events[-2][0] == "response exit"
    assert isinstance(events[-2][1], ValueError)
    assert events[-1][0] == "progress exit"


def test_download_file_preserves_destination_permission_error(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    response = StreamingResponse(events, [b"content"])
    configure_download(monkeypatch, events, response)
    destination = MagicMock(spec=Path)
    failure = PermissionError("read only")
    destination.open.side_effect = failure

    with pytest.raises(PermissionError) as error:
        deps._download_file("https://downloads.example/artifact", destination, "artifact")

    assert error.value is failure
    destination.open.assert_called_once_with("wb")
    assert events[-2:] == [("response exit", failure), ("progress exit", failure)]


def test_verify_checksum_rejects_missing_published_entry_without_reading_file(runtime: SimpleNamespace) -> None:
    missing = runtime.tmp / "missing.tar.gz"

    assert deps._verify_checksum(missing, {"other.tar.gz": "digest"}) is False
    assert runtime.console.messages == ["[red]No checksum published for missing.tar.gz.[/red]"]


def test_verify_checksum_accepts_large_matching_file(runtime: SimpleNamespace) -> None:
    path = runtime.tmp / "artifact.tar.gz"
    content = b"a" * 70000 + b"tail"
    path.write_bytes(content)

    assert deps._verify_checksum(path, {path.name: hashlib.sha256(content).hexdigest()}) is True
    assert runtime.console.messages == []


def test_verify_checksum_rejects_mismatch_with_exact_diagnostics(runtime: SimpleNamespace) -> None:
    path = runtime.tmp / "artifact.tar.gz"
    path.write_bytes(b"downloaded")
    expected = "0" * 64
    actual = hashlib.sha256(b"downloaded").hexdigest()

    assert deps._verify_checksum(path, {path.name: expected}) is False
    assert runtime.console.messages == [
        "[red]Checksum mismatch for artifact.tar.gz![/red]",
        f"  Expected: {expected}",
        f"  Got:      {actual}",
    ]


def test_verify_checksum_preserves_file_error(runtime: SimpleNamespace) -> None:
    missing = runtime.tmp / "missing.tar.gz"

    with pytest.raises(FileNotFoundError):
        deps._verify_checksum(missing, {missing.name: "digest"})

    assert runtime.console.messages == []


def test_fetch_checksums_uses_exact_request_and_parses_two_column_rows(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = MagicMock()
    response.text = "aaa first\nignored\nbbb second extra\nccc first\nddd windows.exe\n"
    get = MagicMock(return_value=response)
    monkeypatch.setattr(deps.httpx, "get", get)
    monkeypatch.setattr(deps, "GITHUB_REPO", "example/project")
    monkeypatch.setattr(deps, "DEPS_RELEASE_TAG", "deps/v3")

    assert deps._fetch_checksums() == {"first": "ccc", "windows.exe": "ddd"}
    get.assert_called_once_with(
        "https://github.com/example/project/releases/download/deps/v3/checksums.txt",
        follow_redirects=True,
        timeout=30,
    )
    response.raise_for_status.assert_called_once_with()
    assert runtime.console.messages == []


@pytest.mark.parametrize("failure_at", ["request", "status"])
def test_fetch_checksums_handles_http_failures_with_exact_warning(
    failure_at: str,
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = status_error(deps._checksum_url())
    if failure_at == "request":
        get = MagicMock(side_effect=failure)
    else:
        response = MagicMock()
        response.raise_for_status.side_effect = failure
        get = MagicMock(return_value=response)
    monkeypatch.setattr(deps.httpx, "get", get)

    with pytest.raises(RuntimeError, match="Could not fetch dependency checksums"):
        deps._fetch_checksums()
    assert get.call_count == 1
    assert runtime.console.messages == []


def test_fetch_checksums_handles_timeout(runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("GET", deps._checksum_url())
    timeout = httpx.ReadTimeout("slow", request=request)
    monkeypatch.setattr(deps.httpx, "get", MagicMock(side_effect=timeout))

    with pytest.raises(RuntimeError, match="Could not fetch dependency checksums"):
        deps._fetch_checksums()
    assert runtime.console.messages == []


def test_fetch_checksums_preserves_non_http_failure(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = RuntimeError("response decoder failed")
    monkeypatch.setattr(deps.httpx, "get", MagicMock(side_effect=failure))

    with pytest.raises(RuntimeError) as error:
        deps._fetch_checksums()

    assert error.value is failure
    assert runtime.console.messages == []


def test_extract_tarball_uses_gzip_mode_and_destination(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tarball = tmp_path / "artifact.tar.gz"
    destination = tmp_path / "staging"
    archive = MagicMock()
    archive.__enter__.return_value = archive
    archive.getmembers.return_value = []
    opened = MagicMock(return_value=archive)
    monkeypatch.setattr(deps.tarfile, "open", opened)

    assert deps._extract_tarball(tarball, destination) is None

    opened.assert_called_once_with(tarball, "r:gz")
    archive.extractall.assert_called_once_with(path=destination, filter="data")
    archive.__exit__.assert_called_once_with(None, None, None)


def test_extract_tarball_preserves_archive_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    failure = tarfile.ReadError("invalid archive")
    monkeypatch.setattr(deps.tarfile, "open", MagicMock(side_effect=failure))

    with pytest.raises(tarfile.ReadError) as error:
        deps._extract_tarball(tmp_path / "artifact.tar.gz", tmp_path / "staging")

    assert error.value is failure


def test_make_executable_adds_every_execute_bit_without_losing_mode(tmp_path: Path) -> None:
    path = tmp_path / "binary"
    path.write_bytes(b"binary")
    path.chmod(0o640)

    assert deps._make_executable(path) is None
    assert stat.S_IMODE(path.stat().st_mode) == 0o751


def test_make_executable_preserves_permission_error(monkeypatch: pytest.MonkeyPatch) -> None:
    path = MagicMock(spec=Path)
    path.stat.return_value.st_mode = 0o640
    failure = PermissionError("mode denied")
    path.chmod.side_effect = failure

    with pytest.raises(PermissionError) as error:
        deps._make_executable(path)

    assert error.value is failure
    path.chmod.assert_called_once_with(0o751)


@pytest.mark.parametrize(
    ("service", "present", "expected"),
    [
        ("postgres", {"postgres", "initdb"}, True),
        ("postgres", {"postgres"}, False),
        ("postgres", {"initdb"}, False),
        ("clickhouse", {"clickhouse"}, True),
        ("clickhouse", set(), False),
        ("redis", {"redis_server"}, True),
        ("redis", set(), False),
        ("unknown", {"postgres", "initdb", "clickhouse", "redis_server"}, False),
    ],
)
def test_is_installed_requires_service_files(
    service: str,
    present: set[str],
    expected: bool,
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = {
        name: runtime.bin_dir / filename
        for name, filename in {
            "postgres": "postgres",
            "initdb": "initdb",
            "clickhouse": "clickhouse",
            "redis_server": "redis-server",
        }.items()
    }
    runtime.bin_dir.mkdir(parents=True)
    for name in present:
        paths[name].write_bytes(b"")
        paths[name].chmod(0)
    get_paths = MagicMock(return_value=paths)
    monkeypatch.setattr(deps, "get_bin_paths", get_paths)

    assert deps.is_installed(service) is expected
    get_paths.assert_called_once_with()


@pytest.mark.parametrize(
    ("results", "expected", "calls"),
    [
        ({"postgres": True, "clickhouse": True, "redis": True}, True, ["postgres", "clickhouse", "redis"]),
        ({"postgres": False}, False, ["postgres"]),
        ({"postgres": True, "clickhouse": False}, False, ["postgres", "clickhouse"]),
    ],
)
def test_all_installed_checks_services_in_order_and_short_circuits(
    results: dict[str, bool],
    expected: bool,
    calls: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked: list[str] = []

    def installed(service: str) -> bool:
        checked.append(service)
        return results[service]

    monkeypatch.setattr(deps, "is_installed", installed)

    assert deps.all_installed() is expected
    assert checked == calls


def test_install_dependencies_skips_present_service_and_installs_others_in_url_order(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    urls = {
        "postgres": "https://downloads.example/pg.tar.gz",
        "clickhouse": "https://downloads.example/clickhouse.tar.gz",
        "redis": "https://downloads.example/redis.tar.gz",
    }
    extracted = {
        "pg.tar.gz": ["nested/postgres", "nested/initdb"],
        "redis.tar.gz": ["nested/redis-server"],
    }
    checksums = {name: f"digest-{name}" for name in ("pg.tar.gz", "clickhouse.tar.gz", "redis.tar.gz")}
    monkeypatch.setattr(deps, "get_dep_urls", lambda: events.append("urls") or urls)
    monkeypatch.setattr(deps, "_fetch_checksums", lambda: events.append("checksums") or checksums)

    def installed(service: str) -> bool:
        events.append(("installed", service))
        return service == "clickhouse"

    def download(url: str, destination: Path, label: str) -> None:
        events.append(("download", url, destination, label))
        destination.write_bytes(b"archive")

    def verify(path: Path, actual_checksums: dict[str, str]) -> bool:
        events.append(("verify", path, actual_checksums))
        return True

    def extract(tarball: Path, staging: Path) -> None:
        events.append(("extract", tarball, staging))
        for relative in extracted[tarball.name]:
            output = staging / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(relative.encode())
        (staging / "empty-directory").mkdir()

    real_move = deps.shutil.move
    real_make_executable = deps._make_executable

    def move(source: str, destination: str):
        events.append(("move", Path(source), Path(destination)))
        return real_move(source, destination)

    def make_executable(path: Path) -> None:
        events.append(("executable", path))
        real_make_executable(path)

    monkeypatch.setattr(deps, "is_installed", installed)
    monkeypatch.setattr(deps, "_download_file", download)
    monkeypatch.setattr(deps, "_verify_checksum", verify)
    monkeypatch.setattr(deps, "_extract_tarball", extract)
    monkeypatch.setattr(deps.shutil, "move", move)
    monkeypatch.setattr(deps, "_make_executable", make_executable)
    temporary = configure_temporary_directories(runtime, monkeypatch, events)

    assert deps.install_dependencies() is None

    assert runtime.bin_dir.is_dir()
    assert {path.name for path in runtime.bin_dir.iterdir()} == {"postgres", "initdb", "redis-server"}
    assert all(stat.S_IMODE(path.stat().st_mode) & 0o111 == 0o111 for path in runtime.bin_dir.iterdir())
    assert temporary.count == 2
    assert temporary.exits == [None, None]
    assert [event for event in events if isinstance(event, tuple) and event[0] == "installed"] == [
        ("installed", "postgres"),
        ("installed", "clickhouse"),
        ("installed", "redis"),
    ]
    assert [event[3] for event in events if isinstance(event, tuple) and event[0] == "download"] == [
        "  postgres",
        "  redis",
    ]
    assert [event[1].name for event in events if isinstance(event, tuple) and event[0] == "verify"] == [
        "pg.tar.gz",
        "redis.tar.gz",
    ]
    moved_names = [event[2].name for event in events if isinstance(event, tuple) and event[0] == "move"]
    assert set(moved_names[:2]) == {"postgres", "initdb"}
    assert moved_names[2:] == ["redis-server"]
    assert runtime.console.messages == [
        "[blue]==>[/blue] Downloading postgres...",
        "[green]✓[/green] postgres installed",
        "[green]✓[/green] clickhouse already installed",
        "[blue]==>[/blue] Downloading redis...",
        "[green]✓[/green] redis installed",
    ]


def test_install_dependencies_force_still_requires_published_checksum(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    url = "https://downloads.example/clickhouse.tar.gz"
    installed = MagicMock(side_effect=blocked("is_installed"))
    verify = MagicMock(return_value=True)
    monkeypatch.setattr(deps, "get_dep_urls", lambda: {"clickhouse": url})
    monkeypatch.setattr(deps, "_fetch_checksums", lambda: {"clickhouse.tar.gz": "digest"})
    monkeypatch.setattr(deps, "is_installed", installed)
    monkeypatch.setattr(deps, "_verify_checksum", verify)
    monkeypatch.setattr(deps, "_download_file", lambda _url, path, _label: path.write_bytes(b"archive"))

    def extract(_tarball: Path, staging: Path) -> None:
        (staging / "clickhouse").write_bytes(b"binary")

    monkeypatch.setattr(deps, "_extract_tarball", extract)
    configure_temporary_directories(runtime, monkeypatch, events)

    assert deps.install_dependencies(force=True) is None

    installed.assert_not_called()
    verify.assert_called_once()
    assert (runtime.bin_dir / "clickhouse").read_bytes() == b"binary"
    assert stat.S_IMODE((runtime.bin_dir / "clickhouse").stat().st_mode) & 0o111 == 0o111
    assert runtime.console.messages == [
        "[blue]==>[/blue] Downloading clickhouse...",
        "[green]✓[/green] clickhouse installed",
    ]


def test_install_dependencies_checksum_failure_stops_before_extraction(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    monkeypatch.setattr(deps, "get_dep_urls", lambda: {"redis": "https://downloads.example/redis.tar.gz"})
    monkeypatch.setattr(deps, "_fetch_checksums", lambda: {"redis.tar.gz": "digest"})
    monkeypatch.setattr(deps, "is_installed", lambda _service: False)
    monkeypatch.setattr(deps, "_download_file", lambda _url, path, _label: path.write_bytes(b"archive"))
    monkeypatch.setattr(deps, "_verify_checksum", lambda _path, _checksums: False)
    extract = MagicMock(side_effect=blocked("_extract_tarball"))
    monkeypatch.setattr(deps, "_extract_tarball", extract)
    temporary = configure_temporary_directories(runtime, monkeypatch, events)

    with pytest.raises(RuntimeError, match=r"^Checksum verification failed for redis$"):
        deps.install_dependencies()

    extract.assert_not_called()
    assert len(temporary.exits) == 1
    assert isinstance(temporary.exits[0], RuntimeError)
    assert runtime.console.messages == ["[blue]==>[/blue] Downloading redis..."]


def test_install_dependencies_preserves_download_timeout_and_stops_remaining_services(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    urls = {
        "postgres": "https://downloads.example/pg.tar.gz",
        "redis": "https://downloads.example/redis.tar.gz",
    }
    request = httpx.Request("GET", urls["postgres"])
    failure = httpx.ReadTimeout("download stalled", request=request)
    download = MagicMock(side_effect=failure)
    monkeypatch.setattr(deps, "get_dep_urls", lambda: urls)
    monkeypatch.setattr(deps, "_fetch_checksums", lambda: {})
    monkeypatch.setattr(deps, "is_installed", lambda _service: False)
    monkeypatch.setattr(deps, "_download_file", download)
    extract = MagicMock(side_effect=blocked("_extract_tarball"))
    monkeypatch.setattr(deps, "_extract_tarball", extract)
    temporary = configure_temporary_directories(runtime, monkeypatch, events)

    with pytest.raises(httpx.ReadTimeout) as error:
        deps.install_dependencies()

    assert error.value is failure
    assert download.call_count == 1
    assert download.call_args.args[0] == urls["postgres"]
    extract.assert_not_called()
    assert temporary.exits == [failure]
    assert runtime.console.messages == ["[blue]==>[/blue] Downloading postgres..."]


def test_install_dependencies_preserves_move_permission_error_without_success_message(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    failure = PermissionError("destination denied")
    monkeypatch.setattr(deps, "get_dep_urls", lambda: {"redis": "https://downloads.example/redis.tar.gz"})
    monkeypatch.setattr(deps, "_fetch_checksums", lambda: {"redis.tar.gz": "digest"})
    monkeypatch.setattr(deps, "is_installed", lambda _service: False)
    monkeypatch.setattr(deps, "_download_file", lambda _url, path, _label: path.write_bytes(b"archive"))
    monkeypatch.setattr(deps, "_verify_checksum", lambda _path, _checksums: True)

    def extract(_tarball: Path, staging: Path) -> None:
        (staging / "redis-server").write_bytes(b"binary")

    monkeypatch.setattr(deps, "_extract_tarball", extract)
    move = MagicMock(side_effect=failure)
    make_executable = MagicMock()
    monkeypatch.setattr(deps.shutil, "move", move)
    monkeypatch.setattr(deps, "_make_executable", make_executable)
    temporary = configure_temporary_directories(runtime, monkeypatch, events)

    with pytest.raises(PermissionError) as error:
        deps.install_dependencies()

    assert error.value is failure
    move.assert_called_once()
    make_executable.assert_not_called()
    assert temporary.exits == [failure]
    assert runtime.console.messages == ["[blue]==>[/blue] Downloading redis..."]


def test_install_single_returns_early_for_present_service(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = MagicMock(return_value=True)
    monkeypatch.setattr(deps, "is_installed", installed)

    assert deps.install_single("postgres") is None

    installed.assert_called_once_with("postgres")
    assert not runtime.bin_dir.exists()
    assert runtime.console.messages == ["[green]✓[/green] postgres already installed"]


def test_install_single_unknown_service_validates_after_setup_with_exact_error(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(deps, "is_installed", lambda service: events.append(f"installed {service}") or False)
    monkeypatch.setattr(deps, "get_dep_urls", lambda: events.append("urls") or {"postgres": "unused"})
    monkeypatch.setattr(deps, "_fetch_checksums", lambda: events.append("checksums") or {})

    with pytest.raises(ValueError, match=r"^Unknown service: mysql$"):
        deps.install_single("mysql")

    assert runtime.bin_dir.is_dir()
    assert events == ["installed mysql", "urls", "checksums"]
    assert runtime.console.messages == []


def test_install_single_force_installs_and_flattens_nested_binary_path(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    url = "https://downloads.example/redis-linux-x64.tar.gz"
    installed = MagicMock(side_effect=blocked("is_installed"))
    verify = MagicMock(return_value=True)
    download = MagicMock(side_effect=lambda _url, path, _label: path.write_bytes(b"archive"))
    monkeypatch.setattr(deps, "is_installed", installed)
    monkeypatch.setattr(deps, "get_dep_urls", lambda: {"redis": url})
    monkeypatch.setattr(deps, "_fetch_checksums", lambda: {"redis-linux-x64.tar.gz": "digest"})
    monkeypatch.setattr(deps, "_download_file", download)
    monkeypatch.setattr(deps, "_verify_checksum", verify)

    def extract(_tarball: Path, staging: Path) -> None:
        nested = staging / "redis/bin/redis-server"
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b"redis")

    monkeypatch.setattr(deps, "_extract_tarball", extract)
    temporary = configure_temporary_directories(runtime, monkeypatch, events)

    assert deps.install_single("redis", force=True) is None

    installed.assert_not_called()
    verify.assert_called_once()
    download.assert_called_once()
    assert download.call_args.args[0] == url
    assert download.call_args.args[1].name == "redis-linux-x64.tar.gz"
    assert download.call_args.args[2] == "  redis"
    installed_path = runtime.bin_dir / "redis-server"
    assert installed_path.read_bytes() == b"redis"
    assert stat.S_IMODE(installed_path.stat().st_mode) & 0o111 == 0o111
    assert temporary.exits == [None]
    assert runtime.console.messages == [
        "[blue]==>[/blue] Downloading redis...",
        "[green]✓[/green] redis installed",
    ]


def test_install_single_checksum_failure_raises_before_extracting(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    monkeypatch.setattr(deps, "is_installed", lambda _service: False)
    monkeypatch.setattr(deps, "get_dep_urls", lambda: {"clickhouse": "https://downloads.example/ch.tar.gz"})
    monkeypatch.setattr(deps, "_fetch_checksums", lambda: {"ch.tar.gz": "digest"})
    monkeypatch.setattr(deps, "_download_file", lambda _url, path, _label: path.write_bytes(b"archive"))
    verify = MagicMock(return_value=False)
    extract = MagicMock(side_effect=blocked("_extract_tarball"))
    monkeypatch.setattr(deps, "_verify_checksum", verify)
    monkeypatch.setattr(deps, "_extract_tarball", extract)
    temporary = configure_temporary_directories(runtime, monkeypatch, events)

    with pytest.raises(RuntimeError, match=r"^Checksum verification failed for clickhouse$"):
        deps.install_single("clickhouse")

    verify.assert_called_once()
    assert verify.call_args.args[0].name == "ch.tar.gz"
    assert verify.call_args.args[1] == {"ch.tar.gz": "digest"}
    extract.assert_not_called()
    assert len(temporary.exits) == 1
    assert isinstance(temporary.exits[0], RuntimeError)
    assert runtime.console.messages == ["[blue]==>[/blue] Downloading clickhouse..."]
