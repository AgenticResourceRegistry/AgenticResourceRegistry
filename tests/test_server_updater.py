# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Faeka Ansari <97490463+fykaa@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0

"""Isolated behavioral tests for the standalone server binary updater."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from observal_cli.server import updater


class RecordingConsole:
    """Capture Rich markup before rendering changes it."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def print(self, *values: object) -> None:
        self.messages.append(" ".join(str(value) for value in values))


class DownloadResponse:
    """Deterministic streaming response with optional failure injection."""

    def __init__(
        self,
        chunks: list[bytes],
        *,
        events: list[object] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.chunks = chunks
        self.events = events
        self.error = error

    def __enter__(self):
        if self.events is not None:
            self.events.append("stream enter")
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if self.events is not None:
            self.events.append("stream exit")
        return False

    def raise_for_status(self) -> None:
        if self.events is not None:
            self.events.append("status")
        if self.error is not None:
            raise self.error

    def iter_bytes(self, *, chunk_size: int):
        if self.events is not None:
            self.events.append(("chunks", chunk_size))
        yield from self.chunks


def blocked(name: str):
    def fail(*args: object, **kwargs: object):
        raise AssertionError(f"unmocked boundary: {name}, args={args}, kwargs={kwargs}")

    return fail


def status_error(status_code: int, url: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("download rejected", request=request, response=response)


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Redirect paths and block every network boundary by default."""
    root = tmp_path / "home" / ".observal"
    binary = root / "bin" / "observal"
    backup = root / "bin" / "observal.bak"
    cache = root / ".update-check"
    console = RecordingConsole()
    temp_paths: list[Path] = []
    real_named_temporary_file = tempfile.NamedTemporaryFile

    def named_temporary_file(**kwargs: object):
        handle = real_named_temporary_file(dir=tmp_path, **kwargs)
        temp_paths.append(Path(handle.name))
        return handle

    monkeypatch.setattr(updater, "OBSERVAL_HOME", root)
    monkeypatch.setattr(updater, "UPDATE_CHECK_CACHE", cache)
    monkeypatch.setattr(updater, "console", console)
    monkeypatch.setattr(updater, "get_current_version", lambda: "1.0.0")
    monkeypatch.setattr(updater, "_get_binary_path", lambda: binary)
    monkeypatch.setattr(updater, "_get_backup_path", lambda: backup)
    monkeypatch.setattr(updater, "_get_artifact_name", lambda: "observal-server-linux-x64")
    monkeypatch.setattr(updater.tempfile, "NamedTemporaryFile", named_temporary_file)
    monkeypatch.setattr(updater.httpx, "get", blocked("httpx.get"))
    monkeypatch.setattr(updater.httpx, "stream", blocked("httpx.stream"))

    return SimpleNamespace(
        root=root,
        binary=binary,
        backup=backup,
        cache=cache,
        console=console,
        temp_paths=temp_paths,
        artifact="observal-server-linux-x64",
    )


def test_version_and_path_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    version = MagicMock(return_value="2.3.4")
    monkeypatch.setattr(importlib.metadata, "version", version)

    assert updater.get_current_version() == "2.3.4"
    version.assert_called_once_with("observal-cli")

    monkeypatch.setattr(importlib.metadata, "version", MagicMock(side_effect=RuntimeError("metadata unavailable")))
    assert updater.get_current_version() == "0.0.0"

    binary = tmp_path / "actual-observal"
    binary.touch()
    link = tmp_path / "observal"
    link.symlink_to(binary)
    monkeypatch.setattr(updater.sys, "executable", str(link))
    monkeypatch.setattr(updater, "OBSERVAL_HOME", tmp_path / "home")

    assert updater._get_binary_path() == binary.resolve()
    assert updater._get_backup_path() == tmp_path / "home" / "bin" / "observal.bak"


@pytest.mark.parametrize(
    ("system", "platform_result", "expected"),
    [
        ("Linux", ("linux", "x64"), "observal-server-linux-x64"),
        ("Darwin", ("macos", "arm64"), "observal-server-macos-arm64"),
        ("Windows", ("windows", "x64"), "observal-server-windows-x64.exe"),
    ],
)
def test_artifact_name_uses_detected_platform(
    system: str,
    platform_result: tuple[str, str],
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detect = MagicMock(return_value=platform_result)
    monkeypatch.setattr(updater, "detect_platform", detect)
    monkeypatch.setattr(updater.platform, "system", lambda: system)

    assert updater._get_artifact_name() == expected
    detect.assert_called_once_with()


@pytest.mark.parametrize(
    ("release", "expected"),
    [({"latest_version": "2.0.1"}, "v2.0.1"), (None, None), ({}, None)],
)
def test_fetch_latest_version_delegates_to_unified_version_check(
    release: dict[str, str] | None,
    expected: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from observal_cli import version_check

    fetch = MagicMock(return_value=release)
    monkeypatch.setattr(version_check, "_fetch_from_github", fetch)

    assert updater.fetch_latest_version() == expected
    fetch.assert_called_once_with()


def test_fetch_latest_version_preserves_malformed_release_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from observal_cli import version_check

    monkeypatch.setattr(version_check, "_fetch_from_github", lambda: {"source": "github"})

    with pytest.raises(KeyError, match="latest_version"):
        updater.fetch_latest_version()


def test_fresh_update_cache_is_returned_without_network_or_output(
    runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime.cache.parent.mkdir(parents=True)
    content = json.dumps({"latest_version": "v2.0.0", "checked_at": 900.0})
    runtime.cache.write_text(content)
    monkeypatch.setattr(updater.time, "time", lambda: 1000.0)
    fetch = MagicMock()
    monkeypatch.setattr(updater, "fetch_latest_version", fetch)

    assert updater.check_for_update(quiet=False) == "v2.0.0"
    fetch.assert_not_called()
    assert runtime.cache.read_text() == content
    assert runtime.console.messages == []


@pytest.mark.parametrize("latest", [None, "v1.0.0"])
def test_fresh_cache_without_an_update_returns_none(
    latest: str | None,
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime.cache.parent.mkdir(parents=True)
    runtime.cache.write_text(json.dumps({"latest_version": latest, "checked_at": 900.0}))
    monkeypatch.setattr(updater.time, "time", lambda: 1000.0)
    fetch = MagicMock()
    monkeypatch.setattr(updater, "fetch_latest_version", fetch)

    assert updater.check_for_update() is None
    fetch.assert_not_called()
    assert runtime.console.messages == []


@pytest.mark.parametrize("quiet", [False, True])
def test_network_update_writes_cache_and_honors_quiet(
    quiet: bool,
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(updater.time, "time", lambda: 1234.5)
    fetch = MagicMock(return_value="v2.0.0")
    monkeypatch.setattr(updater, "fetch_latest_version", fetch)

    assert updater.check_for_update(quiet=quiet) == "v2.0.0"
    fetch.assert_called_once_with()
    assert json.loads(runtime.cache.read_text()) == {"latest_version": "v2.0.0", "checked_at": 1234.5}
    expected = (
        []
        if quiet
        else [
            "[yellow]Update available:[/yellow] v1.0.0 → [bold]v2.0.0[/bold]. Run: [cyan]observal self upgrade[/cyan]"
        ]
    )
    assert runtime.console.messages == expected


def test_malformed_cache_is_replaced_after_a_successful_check(
    runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime.cache.parent.mkdir(parents=True)
    runtime.cache.write_text("not json")
    monkeypatch.setattr(updater.time, "time", lambda: 2000.0)
    fetch = MagicMock(return_value="v1.0.0")
    monkeypatch.setattr(updater, "fetch_latest_version", fetch)

    assert updater.check_for_update() is None
    fetch.assert_called_once_with()
    assert json.loads(runtime.cache.read_text()) == {"latest_version": "v1.0.0", "checked_at": 2000.0}
    assert runtime.console.messages == []


def test_failed_refresh_preserves_stale_cache(runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime.cache.parent.mkdir(parents=True)
    content = '{"latest_version":"v9.0.0","checked_at":0}'
    runtime.cache.write_text(content)
    monkeypatch.setattr(updater.time, "time", lambda: updater.UPDATE_CHECK_INTERVAL + 1.0)
    fetch = MagicMock(return_value=None)
    monkeypatch.setattr(updater, "fetch_latest_version", fetch)

    assert updater.check_for_update() is None
    fetch.assert_called_once_with()
    assert runtime.cache.read_text() == content
    assert runtime.console.messages == []


def test_non_mapping_cache_preserves_attribute_error_contract(
    runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime.cache.parent.mkdir(parents=True)
    runtime.cache.write_text("[]")
    fetch = MagicMock()
    monkeypatch.setattr(updater, "fetch_latest_version", fetch)

    with pytest.raises(AttributeError, match="get"):
        updater.check_for_update()

    fetch.assert_not_called()
    assert runtime.cache.read_text() == "[]"


def test_cache_write_error_is_not_silenced(runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime.cache.mkdir(parents=True)
    monkeypatch.setattr(updater, "fetch_latest_version", lambda: "v2.0.0")
    monkeypatch.setattr(updater.time, "time", lambda: 1000.0)

    with pytest.raises(IsADirectoryError):
        updater.check_for_update()


def test_fetch_checksums_parses_only_two_column_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    response = MagicMock()
    response.text = "aaa artifact\nignored\nbbb other\nthree columns here\nccc artifact\n"
    get = MagicMock(return_value=response)
    monkeypatch.setattr(updater.httpx, "get", get)

    assert updater._fetch_checksums("v2.0.0") == {"artifact": "ccc", "other": "bbb"}
    get.assert_called_once_with(
        f"https://github.com/{updater.GITHUB_REPO}/releases/download/v2.0.0/checksums.txt",
        follow_redirects=True,
        timeout=30,
    )
    response.raise_for_status.assert_called_once_with()


@pytest.mark.parametrize("failure_at", ["request", "status"])
def test_fetch_checksums_returns_empty_on_http_errors(
    failure_at: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if failure_at == "request":
        get = MagicMock(side_effect=httpx.ConnectError("offline"))
    else:
        response = MagicMock()
        response.raise_for_status.side_effect = status_error(503, "https://example.test/checksums.txt")
        get = MagicMock(return_value=response)
    monkeypatch.setattr(updater.httpx, "get", get)

    assert updater._fetch_checksums("v2.0.0") == {}
    assert get.call_count == 1


def test_verify_binary_accepts_matching_checksum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    console = RecordingConsole()
    monkeypatch.setattr(updater, "console", console)
    path = tmp_path / "binary"
    content = b"a" * 70000 + b"tail"
    path.write_bytes(content)

    assert updater._verify_binary(path, {"artifact": hashlib.sha256(content).hexdigest()}, "artifact") is True
    assert console.messages == []


def test_verify_binary_rejects_mismatch_with_exact_diagnostics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    console = RecordingConsole()
    monkeypatch.setattr(updater, "console", console)
    path = tmp_path / "binary"
    content = b"downloaded"
    path.write_bytes(content)
    expected = "0" * 64
    actual = hashlib.sha256(content).hexdigest()

    assert updater._verify_binary(path, {"artifact": expected}, "artifact") is False
    assert console.messages == [
        "[red]Checksum verification failed![/red]",
        f"  Expected: {expected}",
        f"  Got:      {actual}",
    ]


def test_verify_binary_allows_missing_checksum_without_opening_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    console = RecordingConsole()
    monkeypatch.setattr(updater, "console", console)
    missing = tmp_path / "missing"

    assert updater._verify_binary(missing, {"other": "hash"}, "artifact") is True
    assert console.messages == ["[yellow]Warning:[/yellow] No checksum available, skipping verification"]


def test_verify_binary_propagates_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        updater._verify_binary(tmp_path / "missing", {"artifact": "hash"}, "artifact")


def test_update_reports_latest_resolution_failure_without_creating_temp_file(
    runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetch = MagicMock(return_value=None)
    monkeypatch.setattr(updater, "fetch_latest_version", fetch)

    assert updater.update() is False
    fetch.assert_called_once_with()
    assert runtime.temp_paths == []
    assert runtime.console.messages == ["[red]Error:[/red] Could not determine latest version"]


def test_update_short_circuits_when_target_is_current(
    runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = MagicMock()
    monkeypatch.setattr(updater.httpx, "stream", stream)

    assert updater.update(version="v1.0.0") is True
    stream.assert_not_called()
    assert runtime.temp_paths == []
    assert runtime.console.messages == ["[green]✓[/green] Already up to date (v1.0.0)"]


def test_successful_update_resolves_downloads_verifies_backs_up_and_replaces_in_order(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime.binary.parent.mkdir(parents=True)
    runtime.binary.write_bytes(b"old binary")
    runtime.cache.parent.mkdir(parents=True, exist_ok=True)
    runtime.cache.write_text("stale")
    chunks = [b"new ", b"binary"]
    content = b"".join(chunks)
    events: list[object] = []
    response = DownloadResponse(chunks, events=events)
    stream = MagicMock(return_value=response)
    monkeypatch.setattr(updater.httpx, "stream", stream)
    monkeypatch.setattr(updater, "fetch_latest_version", lambda: events.append("latest") or "v2.0.0")
    monkeypatch.setattr(
        updater,
        "_fetch_checksums",
        lambda version: (
            events.append(("checksums", version)) or {runtime.artifact: hashlib.sha256(content).hexdigest()}
        ),
    )

    real_verify = updater._verify_binary

    def verify(path: Path, checksums: dict[str, str], artifact_name: str) -> bool:
        events.append(("verify", path, artifact_name))
        return real_verify(path, checksums, artifact_name)

    monkeypatch.setattr(updater, "_verify_binary", verify)
    real_copy = updater.shutil.copy2

    def copy(source: str, destination: str):
        events.append(("copy", source, destination))
        return real_copy(source, destination)

    monkeypatch.setattr(updater.shutil, "copy2", copy)
    real_replace = updater.os.replace

    def replace(source: str, destination: str) -> None:
        events.append(("replace", source, destination))
        real_replace(source, destination)

    monkeypatch.setattr(updater.os, "replace", replace)

    assert updater.update() is True

    temp_path = runtime.temp_paths[0]
    download_url = f"https://github.com/{updater.GITHUB_REPO}/releases/download/v2.0.0/{runtime.artifact}"
    stream.assert_called_once_with("GET", download_url, follow_redirects=True, timeout=300)
    assert events == [
        "latest",
        "stream enter",
        "status",
        ("chunks", 65536),
        "stream exit",
        ("checksums", "v2.0.0"),
        ("verify", temp_path, runtime.artifact),
        ("copy", str(runtime.binary), str(runtime.backup)),
        ("replace", str(temp_path), str(runtime.binary)),
    ]
    assert runtime.binary.read_bytes() == content
    assert runtime.backup.read_bytes() == b"old binary"
    assert stat.S_IMODE(runtime.binary.stat().st_mode) == 0o755
    assert not temp_path.exists()
    assert not runtime.cache.exists()
    assert runtime.console.messages == [
        "[blue]==>[/blue] Updating: v1.0.0 → v2.0.0",
        f"[blue]==>[/blue] Downloading {runtime.artifact}...",
        "[blue]==>[/blue] Verified checksum",
        "[green]✓[/green] Updated to v2.0.0",
        f"  Binary: {runtime.binary}",
        f"  Backup: {runtime.backup}",
        "",
        "  If the server is running, restart it: [cyan]observal server stop && observal server start[/cyan]",
    ]


def test_update_installs_when_current_binary_and_checksums_are_missing(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = DownloadResponse([b"first install"])
    stream = MagicMock(return_value=response)
    copy = MagicMock()
    verify = MagicMock()
    monkeypatch.setattr(updater.httpx, "stream", stream)
    monkeypatch.setattr(updater, "_fetch_checksums", lambda version: {})
    monkeypatch.setattr(updater, "_verify_binary", verify)
    monkeypatch.setattr(updater.shutil, "copy2", copy)

    assert updater.update(version="v2.0.0") is True

    assert runtime.binary.read_bytes() == b"first install"
    assert stat.S_IMODE(runtime.binary.stat().st_mode) == 0o755
    assert not runtime.backup.exists()
    copy.assert_not_called()
    verify.assert_not_called()
    assert "[yellow]==>[/yellow] Checksum unavailable; skipping verification" in runtime.console.messages
    assert "[blue]==>[/blue] Verified checksum" not in runtime.console.messages


def test_checksum_failure_preserves_binary_cache_and_skips_backup(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime.binary.parent.mkdir(parents=True)
    runtime.binary.write_bytes(b"old binary")
    runtime.cache.write_text("keep cache")
    response = DownloadResponse([b"corrupt binary"])
    monkeypatch.setattr(updater.httpx, "stream", MagicMock(return_value=response))
    monkeypatch.setattr(updater, "_fetch_checksums", lambda version: {runtime.artifact: "0" * 64})
    copy = MagicMock()
    replace = MagicMock()
    monkeypatch.setattr(updater.shutil, "copy2", copy)
    monkeypatch.setattr(updater.os, "replace", replace)

    assert updater.update(version="v2.0.0") is False

    assert runtime.binary.read_bytes() == b"old binary"
    assert runtime.cache.read_text() == "keep cache"
    assert not runtime.backup.exists()
    assert not runtime.temp_paths[0].exists()
    copy.assert_not_called()
    replace.assert_not_called()
    assert "[red]Checksum verification failed![/red]" in runtime.console.messages
    assert not any("Updated to" in message for message in runtime.console.messages)


def test_http_status_failure_reports_url_and_cleans_download(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    download_url = f"https://github.com/{updater.GITHUB_REPO}/releases/download/v9.9.9/{runtime.artifact}"
    response = DownloadResponse([], error=status_error(404, download_url))
    stream = MagicMock(return_value=response)
    monkeypatch.setattr(updater.httpx, "stream", stream)

    assert updater.update(version="v9.9.9") is False

    stream.assert_called_once_with("GET", download_url, follow_redirects=True, timeout=300)
    assert not runtime.temp_paths[0].exists()
    assert not runtime.binary.exists()
    assert not runtime.backup.exists()
    assert runtime.console.messages == [
        "[blue]==>[/blue] Updating: v1.0.0 → v9.9.9",
        f"[blue]==>[/blue] Downloading {runtime.artifact}...",
        "[red]Error:[/red] Download failed (HTTP 404)",
        f"  URL: {download_url}",
        "  Ensure version v9.9.9 exists and has server binaries.",
    ]


def test_network_failure_uses_generic_error_contract_and_cleans_download(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = MagicMock(side_effect=httpx.ConnectError("offline"))
    monkeypatch.setattr(updater.httpx, "stream", stream)

    assert updater.update(version="v2.0.0") is False

    assert not runtime.temp_paths[0].exists()
    assert runtime.console.messages == [
        "[blue]==>[/blue] Updating: v1.0.0 → v2.0.0",
        f"[blue]==>[/blue] Downloading {runtime.artifact}...",
        "[red]Error:[/red] Update failed: offline",
    ]


def test_backup_failure_preserves_current_binary_and_cache(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime.binary.parent.mkdir(parents=True)
    runtime.binary.write_bytes(b"old binary")
    runtime.cache.write_text("keep cache")
    monkeypatch.setattr(updater.httpx, "stream", MagicMock(return_value=DownloadResponse([b"new binary"])))
    monkeypatch.setattr(updater, "_fetch_checksums", lambda version: {})
    copy = MagicMock(side_effect=PermissionError("backup denied"))
    replace = MagicMock()
    monkeypatch.setattr(updater.shutil, "copy2", copy)
    monkeypatch.setattr(updater.os, "replace", replace)

    assert updater.update(version="v2.0.0") is False

    copy.assert_called_once_with(str(runtime.binary), str(runtime.backup))
    replace.assert_not_called()
    assert runtime.binary.read_bytes() == b"old binary"
    assert runtime.cache.read_text() == "keep cache"
    assert not runtime.backup.exists()
    assert not runtime.temp_paths[0].exists()
    assert runtime.console.messages[-1] == "[red]Error:[/red] Update failed: backup denied"


def test_replace_failure_keeps_current_binary_and_completed_backup(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime.binary.parent.mkdir(parents=True)
    runtime.binary.write_bytes(b"old binary")
    monkeypatch.setattr(updater.httpx, "stream", MagicMock(return_value=DownloadResponse([b"new binary"])))
    monkeypatch.setattr(updater, "_fetch_checksums", lambda version: {})
    replace = MagicMock(side_effect=OSError("rename denied"))
    monkeypatch.setattr(updater.os, "replace", replace)

    assert updater.update(version="v2.0.0") is False

    replace.assert_called_once_with(str(runtime.temp_paths[0]), str(runtime.binary))
    assert runtime.binary.read_bytes() == b"old binary"
    assert runtime.backup.read_bytes() == b"old binary"
    assert not runtime.temp_paths[0].exists()
    assert runtime.console.messages[-1] == "[red]Error:[/red] Update failed: rename denied"


def test_temp_file_creation_error_propagates_before_update_guard(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        updater.tempfile,
        "NamedTemporaryFile",
        MagicMock(side_effect=OSError("temporary directory unavailable")),
    )

    with pytest.raises(OSError, match="temporary directory unavailable"):
        updater.update(version="v2.0.0")

    assert runtime.console.messages == ["[blue]==>[/blue] Updating: v1.0.0 → v2.0.0"]


def test_rollback_reports_missing_backup_without_touching_binary(runtime: SimpleNamespace) -> None:
    assert updater.rollback() is False
    assert not runtime.binary.exists()
    assert runtime.console.messages == ["[red]Error:[/red] No backup found. Cannot rollback."]


def test_rollback_atomically_moves_backup_over_current(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime.binary.parent.mkdir(parents=True)
    runtime.binary.write_bytes(b"current")
    runtime.backup.write_bytes(b"previous")
    real_replace = os.replace
    replace = MagicMock(side_effect=real_replace)
    monkeypatch.setattr(updater.os, "replace", replace)

    assert updater.rollback() is True

    replace.assert_called_once_with(str(runtime.backup), str(runtime.binary))
    assert runtime.binary.read_bytes() == b"previous"
    assert not runtime.backup.exists()
    assert runtime.console.messages == [
        "[green]✓[/green] Rolled back to previous version",
        f"  Binary: {runtime.binary}",
    ]


def test_rollback_failure_preserves_both_files(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime.binary.parent.mkdir(parents=True)
    runtime.binary.write_bytes(b"current")
    runtime.backup.write_bytes(b"previous")
    replace = MagicMock(side_effect=PermissionError("read only"))
    monkeypatch.setattr(updater.os, "replace", replace)

    assert updater.rollback() is False

    replace.assert_called_once_with(str(runtime.backup), str(runtime.binary))
    assert runtime.binary.read_bytes() == b"current"
    assert runtime.backup.read_bytes() == b"previous"
    assert runtime.console.messages == ["[red]Error:[/red] Rollback failed: read only"]
