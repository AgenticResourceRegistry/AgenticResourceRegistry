# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Isolated behavioral tests for the CLI upgrade executor."""

from __future__ import annotations

import hashlib
import signal
import stat
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
import typer
from packaging.version import InvalidVersion

from observal_cli import upgrade_executor as executor
from observal_cli.install_detector import InstallInfo, InstallMethod
from observal_cli.shared import launcher

LONG_OPTION = "-" * 2
VERSION_OPTION = f"{LONG_OPTION}version"


class RecordingSpinner:
    def __init__(self, events: list[object] | None = None) -> None:
        self.events = events if events is not None else []

    def __call__(self, message: str):
        @contextmanager
        def active():
            self.events.append(("spinner enter", message))
            try:
                yield
            finally:
                self.events.append(("spinner exit", message))

        return active()


def completed(returncode: int = 0, *, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def install_info(
    method: InstallMethod,
    path: Path = Path("/opt/observal/bin/observal"),
    *,
    writable: bool = True,
    managed_by: str | None = None,
) -> InstallInfo:
    return InstallInfo(method=method, path=path, writable=writable, managed_by=managed_by)


def blocked(name: str):
    def fail(*args: object, **kwargs: object):
        raise AssertionError(f"unmocked boundary: {name}, args={args}, kwargs={kwargs}")

    return fail


@pytest.fixture
def messages(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    output: list[str] = []
    monkeypatch.setattr(executor, "rprint", output.append)
    return output


@pytest.mark.parametrize(
    ("method", "installer_name"),
    [
        (InstallMethod.UV_TOOL, "_install_via_uv"),
        (InstallMethod.PIPX, "_install_via_pipx"),
        (InstallMethod.PIP, "_install_via_pip"),
        (InstallMethod.BINARY, "_install_binary"),
    ],
)
def test_execute_selects_installer_then_verifies(
    method: InstallMethod,
    installer_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    info = install_info(method)
    spinner = RecordingSpinner()

    def install(*args: object, **kwargs: object) -> None:
        events.append(("install", args, kwargs))

    def verify(*args: object) -> None:
        events.append(("verify", args))

    for name in ("_install_via_uv", "_install_via_pipx", "_install_via_pip", "_install_binary"):
        monkeypatch.setattr(executor, name, install if name == installer_name else blocked(name))
    monkeypatch.setattr(executor, "_verify_install", verify)

    assert executor.execute(info, "2.4.1", "upgrade", spinner) is None

    installer_args = (
        (info, "2.4.1", "upgrade", spinner)
        if method == InstallMethod.BINARY
        else ("2.4.1", "upgrade", spinner, info)
        if method == InstallMethod.PIP
        else ("2.4.1", "upgrade", spinner)
    )
    installer_kwargs = {"interactive": True} if method == InstallMethod.BINARY else {"commit": None, "ceiling": None}
    if method == InstallMethod.UV_TOOL:
        installer_kwargs["in_place"] = False
    assert events == [
        ("install", installer_args, installer_kwargs),
        ("verify", (info, "2.4.1", "upgrade")),
    ]


@pytest.mark.parametrize(
    "method",
    [InstallMethod.SYSTEM_PACKAGE, InstallMethod.UNKNOWN],
)
def test_execute_rejects_unsupported_installations_without_verification(
    method: InstallMethod,
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verify = MagicMock()
    monkeypatch.setattr(executor, "_verify_install", verify)

    with pytest.raises(typer.Exit) as error:
        executor.execute(install_info(method), "2.4.1", "downgrade", RecordingSpinner())

    assert error.value.exit_code == 1
    assert messages == [f"[red]Cannot downgrade - unsupported install method: {method.value}[/red]"]
    verify.assert_not_called()


def test_execute_preserves_installer_failure_and_skips_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = typer.Exit(7)
    verify = MagicMock()
    monkeypatch.setattr(executor, "_install_via_uv", MagicMock(side_effect=failure))
    monkeypatch.setattr(executor, "_verify_install", verify)

    with pytest.raises(typer.Exit) as error:
        executor.execute(install_info(InstallMethod.UV_TOOL), "2.4.1", "upgrade", RecordingSpinner())

    assert error.value is failure
    verify.assert_not_called()


@pytest.mark.parametrize(
    ("installer_name", "direction", "expected_progress", "expected_command"),
    [
        (
            "_install_via_uv",
            "upgrade",
            "Upgrading to v2.4.1...",
            ["uv", "tool", "install", "observal-cli==2.4.1", f"{LONG_OPTION}force"],
        ),
        (
            "_install_via_pipx",
            "downgrade",
            "Downgrading to v2.4.1...",
            ["pipx", "install", "observal-cli==2.4.1", f"{LONG_OPTION}force"],
        ),
        (
            "_install_via_pip",
            "upgrade",
            "Upgrading to v2.4.1...",
            ["/venv/bin/python", "-m", "pip", "install", "observal-cli==2.4.1", f"{LONG_OPTION}quiet"],
        ),
    ],
)
def test_package_installers_build_exact_commands_and_progress(
    installer_name: str,
    direction: str,
    expected_progress: str,
    expected_command: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    spinner = RecordingSpinner(events)
    monkeypatch.setattr(executor, "pip_install_prefix", lambda _: ["/venv/bin/python", "-m", "pip", "install"])
    monkeypatch.setenv("PIP_INDEX_URL", "https://packages.example.test/simple")

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        events.append(("run", command, kwargs))
        return completed()

    monkeypatch.setattr(executor.subprocess, "run", run)

    args = (install_info(InstallMethod.PIP),) if installer_name == "_install_via_pip" else ()
    result = getattr(executor, installer_name)("2.4.1", direction, spinner, *args)

    assert result is None
    assert events == [
        ("spinner enter", expected_progress),
        (
            "run",
            expected_command,
            {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace", "timeout": 120},
        ),
        ("spinner exit", expected_progress),
    ]


@pytest.mark.parametrize(
    ("installer_name", "direction", "returncode", "stderr", "expected_stderr"),
    [
        ("_install_via_uv", "upgrade", 7, " registry denied \n", "registry denied"),
        ("_install_via_pipx", "downgrade", -signal.SIGTERM, "terminated\n", "terminated"),
        ("_install_via_pip", "upgrade", 1, "resolver failed", "resolver failed"),
    ],
)
def test_package_installer_failure_tries_release_tag_then_fails_loudly(
    installer_name: str,
    direction: str,
    returncode: int,
    stderr: str,
    expected_stderr: str,
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MagicMock(return_value=completed(returncode, stderr=stderr))
    monkeypatch.setattr(executor.subprocess, "run", run)
    monkeypatch.setattr(executor, "pip_install_prefix", lambda _: ["/venv/bin/python", "-m", "pip", "install"])

    args = (install_info(InstallMethod.PIP),) if installer_name == "_install_via_pip" else ()
    with pytest.raises(typer.Exit) as error:
        getattr(executor, installer_name)("2.4.1", direction, RecordingSpinner(), *args)

    assert error.value.exit_code == 1
    assert run.call_count == 2
    assert "observal-cli==2.4.1" in " ".join(run.call_args_list[0].args[0])
    assert "git+https://github.com/AgenticResourceRegistry/AgenticResourceRegistry@v2.4.1" in " ".join(
        run.call_args_list[1].args[0]
    )
    assert messages == [
        f"[red]{direction.capitalize()} failed; the installed CLI was left unchanged.[/red]",
        f"  observal-cli==2.4.1: {expected_stderr}",
        f"  git+https://github.com/AgenticResourceRegistry/AgenticResourceRegistry@v2.4.1: {expected_stderr}",
    ]


@pytest.mark.parametrize(
    ("installer_name", "failure"),
    [
        # A missing installer is named instead (test_auto_sync.test_an_installer_missing_from_path_is_named).
        ("_install_via_uv", PermissionError("uv is not executable")),
        ("_install_via_pipx", subprocess.TimeoutExpired(["pipx", "install"], 120)),
        ("_install_via_pip", KeyboardInterrupt()),
    ],
)
def test_package_installer_boundary_exceptions_propagate_unchanged(
    installer_name: str,
    failure: BaseException,
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spinner = RecordingSpinner()
    monkeypatch.setattr(executor.subprocess, "run", MagicMock(side_effect=failure))
    monkeypatch.setattr(executor, "pip_install_prefix", lambda _: ["/venv/bin/python", "-m", "pip", "install"])

    args = (install_info(InstallMethod.PIP),) if installer_name == "_install_via_pip" else ()
    with pytest.raises(type(failure)) as error:
        getattr(executor, installer_name)("2.4.1", "upgrade", spinner, *args)

    assert error.value is failure
    assert spinner.events == [
        ("spinner enter", "Upgrading to v2.4.1..."),
        ("spinner exit", "Upgrading to v2.4.1..."),
    ]
    assert messages == []


@pytest.mark.parametrize(
    ("system", "machine", "artifact_name", "suffix"),
    [
        ("Linux", "x86_64", "observal-linux-x64", ""),
        ("Linux", "arm64", "observal-linux-arm64", ""),
        ("Darwin", "aarch64", "observal-macos-arm64", ""),
        ("Windows", "AMD64", "observal-windows-x64.exe", ".exe"),
    ],
)
def test_binary_install_resolves_platform_and_runs_stages_in_order(
    system: str,
    machine: str,
    artifact_name: str,
    suffix: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    spinner = RecordingSpinner(events)
    info = install_info(InstallMethod.BINARY)
    artifact_url = f"https://github.com/releases/{artifact_name}"
    checksum_url = "https://github.com/releases/checksums.txt"
    assets = {artifact_name: artifact_url, "checksums.txt": checksum_url}
    release = MagicMock(status_code=200)
    release.json.side_effect = lambda: (
        events.append("release json")
        or {
            "assets": [
                {"name": artifact_name, "browser_download_url": artifact_url},
                {"name": "checksums.txt", "browser_download_url": checksum_url},
            ]
        }
    )
    monkeypatch.setattr(executor.platform, "system", lambda: system)
    monkeypatch.setattr(executor.platform, "machine", lambda: machine)

    def get(url: str, **kwargs: object) -> MagicMock:
        events.append(("get", url, kwargs))
        return release

    def fetch(actual_assets: dict[str, str]) -> dict[str, str]:
        events.append(("checksums", actual_assets))
        return {artifact_name: "digest"}

    def download(url: str, actual_spinner: RecordingSpinner, name: str) -> bytes:
        events.append(("download", url, actual_spinner, name))
        return b"new binary"

    def verify(content: bytes, checksums: dict[str, str], name: str, *, interactive: bool = True) -> None:
        events.append(("verify", content, checksums, name, interactive))

    def replace(
        actual_info: InstallInfo,
        content: bytes,
        target: str,
        actual_system: str,
        actual_suffix: str,
    ) -> None:
        events.append(("replace", actual_info, content, target, actual_system, actual_suffix))

    monkeypatch.setattr(executor.httpx, "get", get)
    monkeypatch.setattr(executor, "_fetch_checksums", fetch)
    monkeypatch.setattr(executor, "_download_binary", download)
    monkeypatch.setattr(executor, "_verify_checksum", verify)
    monkeypatch.setattr(executor, "_replace_binary", replace)

    assert executor._install_binary(info, "2.4.1", "upgrade", spinner) is None

    release_url = f"https://api.github.com/repos/{executor.GITHUB_REPO}/releases/tags/v2.4.1"
    assert events == [
        ("spinner enter", "Fetching release info..."),
        (
            "get",
            release_url,
            {"timeout": 15, "headers": {"Accept": "application/vnd.github+json"}},
        ),
        ("spinner exit", "Fetching release info..."),
        "release json",
        ("checksums", assets),
        ("download", artifact_url, spinner, artifact_name),
        ("verify", b"new binary", {artifact_name: "digest"}, artifact_name, True),
        ("replace", info, b"new binary", "2.4.1", system.lower(), suffix),
    ]


@pytest.mark.parametrize(
    ("system", "machine", "expected_message"),
    [
        ("Linux", "riscv64", "[red]Unsupported architecture: riscv64[/red]"),
        ("FreeBSD", "x86_64", "[red]Unsupported OS: freebsd[/red]"),
    ],
)
def test_binary_install_rejects_unsupported_platform_before_network(
    system: str,
    machine: str,
    expected_message: str,
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get = MagicMock()
    spinner = RecordingSpinner()
    monkeypatch.setattr(executor.platform, "system", lambda: system)
    monkeypatch.setattr(executor.platform, "machine", lambda: machine)
    monkeypatch.setattr(executor.httpx, "get", get)

    with pytest.raises(typer.Exit) as error:
        executor._install_binary(install_info(InstallMethod.BINARY), "2.4.1", "upgrade", spinner)

    assert error.value.exit_code == 1
    assert messages == [expected_message]
    assert spinner.events == []
    get.assert_not_called()


def test_binary_install_rejects_missing_release_before_parsing_assets(
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = MagicMock(status_code=404)
    response.json.side_effect = blocked("response.json")
    get = MagicMock(return_value=response)
    spinner = RecordingSpinner()
    monkeypatch.setattr(executor.platform, "system", lambda: "Linux")
    monkeypatch.setattr(executor.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(executor.httpx, "get", get)

    with pytest.raises(typer.Exit) as error:
        executor._install_binary(install_info(InstallMethod.BINARY), "9.9.9", "upgrade", spinner)

    assert error.value.exit_code == 1
    get.assert_called_once_with(
        f"https://api.github.com/repos/{executor.GITHUB_REPO}/releases/tags/v9.9.9",
        timeout=15,
        headers={"Accept": "application/vnd.github+json"},
    )
    assert spinner.events == [
        ("spinner enter", "Fetching release info..."),
        ("spinner exit", "Fetching release info..."),
    ]
    assert messages == ["[red]Release v9.9.9 not found on GitHub.[/red]"]


def test_binary_install_rejects_release_without_platform_artifact(
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "assets": [{"name": "checksums.txt", "browser_download_url": "https://github.com/checksums.txt"}]
    }
    fetch = MagicMock()
    monkeypatch.setattr(executor.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(executor.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(executor.httpx, "get", MagicMock(return_value=response))
    monkeypatch.setattr(executor, "_fetch_checksums", fetch)

    with pytest.raises(typer.Exit) as error:
        executor._install_binary(install_info(InstallMethod.BINARY), "2.4.1", "upgrade", RecordingSpinner())

    assert error.value.exit_code == 1
    assert messages == ["[red]Binary 'observal-macos-arm64' not found in release assets.[/red]"]
    fetch.assert_not_called()


def test_fetch_checksums_skips_request_when_asset_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    get = MagicMock(side_effect=blocked("httpx.get"))
    monkeypatch.setattr(executor.httpx, "get", get)

    assert executor._fetch_checksums({"observal-linux-x64": "https://github.com/binary"}) == {}
    get.assert_not_called()


@pytest.mark.parametrize(
    ("status_code", "text", "expected"),
    [
        (404, "aaa ignored", {}),
        (
            200,
            "aaa artifact\nignored\nbbb other extra\nccc artifact\nddd windows.exe\n",
            {"artifact": "ccc", "windows.exe": "ddd"},
        ),
    ],
)
def test_fetch_checksums_obeys_status_and_parses_two_column_rows(
    status_code: int,
    text: str,
    expected: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(status_code=status_code, text=text)
    get = MagicMock(return_value=response)
    monkeypatch.setattr(executor.httpx, "get", get)

    assert executor._fetch_checksums({"checksums.txt": "https://github.com/checksums"}) == expected
    get.assert_called_once_with("https://github.com/checksums", timeout=15, follow_redirects=True)


def test_fetch_checksums_preserves_network_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = httpx.ReadTimeout("checksums timed out")
    monkeypatch.setattr(executor.httpx, "get", MagicMock(side_effect=failure))

    with pytest.raises(httpx.ReadTimeout) as error:
        executor._fetch_checksums({"checksums.txt": "https://github.com/checksums"})

    assert error.value is failure


@pytest.mark.parametrize(
    "final_url",
    [
        "https://objects.githubusercontent.com/release/observal-linux-x64",
        "/relative-download",
    ],
)
def test_download_binary_returns_content_from_allowed_or_relative_url(
    final_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(status_code=200, url=final_url, content=b"downloaded")
    get = MagicMock(return_value=response)
    spinner = RecordingSpinner()
    monkeypatch.setattr(executor.httpx, "get", get)

    assert executor._download_binary("https://github.com/download", spinner, "artifact") == b"downloaded"
    get.assert_called_once_with("https://github.com/download", timeout=120, follow_redirects=True)
    assert spinner.events == [
        ("spinner enter", "Downloading artifact..."),
        ("spinner exit", "Downloading artifact..."),
    ]


def test_download_binary_rejects_http_failure(
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(status_code=503)
    monkeypatch.setattr(executor.httpx, "get", MagicMock(return_value=response))

    with pytest.raises(typer.Exit) as error:
        executor._download_binary("https://github.com/download", RecordingSpinner(), "artifact")

    assert error.value.exit_code == 1
    assert messages == ["[red]Download failed.[/red]"]


def test_download_binary_rejects_untrusted_final_host(
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(status_code=200, url="https://evil.example.test/payload", content=b"payload")
    monkeypatch.setattr(executor.httpx, "get", MagicMock(return_value=response))

    with pytest.raises(typer.Exit) as error:
        executor._download_binary("https://github.com/download", RecordingSpinner(), "artifact")

    assert error.value.exit_code == 1
    assert messages == ["[red]Download redirected to untrusted host: evil.example.test[/red]"]


def test_download_binary_preserves_timeout_after_closing_progress(
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = httpx.ReadTimeout("download timed out")
    spinner = RecordingSpinner()
    monkeypatch.setattr(executor.httpx, "get", MagicMock(side_effect=failure))

    with pytest.raises(httpx.ReadTimeout) as error:
        executor._download_binary("https://github.com/download", spinner, "artifact")

    assert error.value is failure
    assert spinner.events == [
        ("spinner enter", "Downloading artifact..."),
        ("spinner exit", "Downloading artifact..."),
    ]
    assert messages == []


def test_verify_checksum_accepts_match_without_confirmation(
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"trusted binary"
    digest = hashlib.sha256(content).hexdigest()
    confirm = MagicMock(side_effect=blocked("typer.confirm"))
    monkeypatch.setattr(executor.typer, "confirm", confirm)

    assert executor._verify_checksum(content, {"artifact": digest}, "artifact") is None
    assert messages == [f"[dim]SHA-256 verified: {digest[:16]}...[/dim]"]
    confirm.assert_not_called()


def test_verify_checksum_rejects_mismatch_with_exact_diagnostics(messages: list[str]) -> None:
    content = b"corrupt binary"
    expected = "0" * 64
    actual = hashlib.sha256(content).hexdigest()

    with pytest.raises(typer.Exit) as error:
        executor._verify_checksum(content, {"artifact": expected}, "artifact")

    assert error.value.exit_code == 1
    assert messages == [
        "[red]CHECKSUM MISMATCH - download may be corrupted or tampered.[/red]",
        f"  Expected: {expected}",
        f"  Got:      {actual}",
    ]


@pytest.mark.parametrize("confirmed", [False, True])
def test_verify_checksum_requires_explicit_consent_when_missing(
    confirmed: bool,
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    confirm = MagicMock(return_value=confirmed)
    monkeypatch.setattr(executor.typer, "confirm", confirm)

    if confirmed:
        assert executor._verify_checksum(b"unsigned", {}, "artifact") is None
    else:
        with pytest.raises(typer.Abort):
            executor._verify_checksum(b"unsigned", {}, "artifact")

    confirm.assert_called_once_with("Install without verification?", default=False)
    assert messages == ["[yellow]No checksum available for verification.[/yellow]"]


def test_verify_checksum_rejects_missing_checksum_without_prompt(
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    confirm = MagicMock(side_effect=blocked("typer.confirm"))
    monkeypatch.setattr(executor.typer, "confirm", confirm)

    with pytest.raises(typer.Exit) as error:
        executor._verify_checksum(b"unsigned", {}, "artifact", interactive=False)

    assert error.value.exit_code == 1
    confirm.assert_not_called()
    assert messages == [
        "[yellow]No checksum available for verification.[/yellow]",
        "[red]Non-interactive installation requires a published checksum.[/red]",
    ]


def test_replace_binary_backs_up_and_atomically_replaces_in_order(
    tmp_path: Path,
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "config"
    target = tmp_path / "tools" / "observal"
    target.parent.mkdir()
    target.write_bytes(b"old binary")
    target.chmod(0o700)
    info = install_info(InstallMethod.BINARY, target)
    events: list[object] = []

    monkeypatch.setattr(executor.config, "CONFIG_DIR", config_dir)
    real_copy2 = executor.shutil.copy2
    real_mkstemp = executor.tempfile.mkstemp
    real_write = executor.os.write
    real_close = executor.os.close
    real_chmod = executor.os.chmod
    real_rename = Path.rename

    def copy2(source: str, destination: str):
        events.append(("copy", source, destination))
        return real_copy2(source, destination)

    def mkstemp(*args: object, **kwargs: object):
        result = real_mkstemp(*args, **kwargs)
        events.append(("mkstemp", args, kwargs, result))
        return result

    def write(fd: int, content: bytes) -> int:
        events.append(("write", fd, content))
        return real_write(fd, content)

    def close(fd: int) -> None:
        events.append(("close", fd))
        real_close(fd)

    def chmod(path: str, mode: int, **kwargs: object) -> None:
        if kwargs:
            real_chmod(path, mode, **kwargs)
            return
        events.append(("chmod", path, mode))
        real_chmod(path, mode)

    def rename(path: Path, destination: Path):
        events.append(("rename", str(path), str(destination)))
        return real_rename(path, destination)

    monkeypatch.setattr(executor.shutil, "copy2", copy2)
    monkeypatch.setattr(executor.tempfile, "mkstemp", mkstemp)
    monkeypatch.setattr(executor.os, "write", write)
    monkeypatch.setattr(executor.os, "close", close)
    monkeypatch.setattr(executor.os, "chmod", chmod)
    monkeypatch.setattr(Path, "rename", rename)

    assert executor._replace_binary(info, b"new binary", "2.4.1", "linux", "") is None

    backup = config_dir / "bin" / "observal.prev"
    staged_path = Path(events[1][3][1])
    fd = events[1][3][0]
    assert [event[0] for event in events] == ["copy", "mkstemp", "write", "close", "chmod", "rename"]
    assert events[0] == ("copy", str(target), str(backup))
    assert events[1][1:3] == ((), {"dir": target.parent, "prefix": ".observal-update-", "suffix": ""})
    assert events[2] == ("write", fd, b"new binary")
    assert events[3] == ("close", fd)
    assert events[4] == ("chmod", str(staged_path), 0o755)
    assert events[5] == ("rename", str(staged_path), str(target))
    assert target.read_bytes() == b"new binary"
    assert backup.read_bytes() == b"old binary"
    assert stat.S_IMODE(target.stat().st_mode) == 0o755
    assert not staged_path.exists()
    assert messages == []


def test_replace_binary_windows_keeps_backup_and_old_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "config"
    target = tmp_path / "tools" / "observal.exe"
    target.parent.mkdir()
    target.write_bytes(b"old windows binary")
    info = install_info(InstallMethod.BINARY, target)
    monkeypatch.setattr(executor.config, "CONFIG_DIR", config_dir)

    executor._replace_binary(info, b"new windows binary", "2.4.1", "windows", ".exe")

    assert target.read_bytes() == b"new windows binary"
    assert target.with_suffix(".old").read_bytes() == b"old windows binary"
    assert (config_dir / "bin" / "observal.prev").read_bytes() == b"old windows binary"
    assert stat.S_IMODE(target.stat().st_mode) == 0o755


def test_replace_binary_rejects_unwritable_target_before_filesystem_changes(
    tmp_path: Path,
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "config"
    target = tmp_path / "tools" / "observal"
    monkeypatch.setattr(executor.config, "CONFIG_DIR", config_dir)
    mkstemp = MagicMock()
    monkeypatch.setattr(executor.tempfile, "mkstemp", mkstemp)

    with pytest.raises(typer.Exit) as error:
        executor._replace_binary(
            install_info(InstallMethod.BINARY, target, writable=False),
            b"new binary",
            "2.4.1",
            "linux",
            "",
        )

    assert error.value.exit_code == 1
    assert messages == [f"[red]Cannot write to {target} - permission denied.[/red]"]
    assert not config_dir.exists()
    mkstemp.assert_not_called()


def test_replace_binary_failure_cleans_stage_and_preserves_current_and_backup(
    tmp_path: Path,
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "config"
    target = tmp_path / "tools" / "observal"
    target.parent.mkdir()
    target.write_bytes(b"current")
    staged: list[Path] = []
    real_mkstemp = executor.tempfile.mkstemp

    def mkstemp(*args: object, **kwargs: object):
        fd, name = real_mkstemp(*args, **kwargs)
        staged.append(Path(name))
        return fd, name

    failure = PermissionError("mode denied")
    real_chmod = executor.os.chmod

    def chmod(path: str, mode: int, **kwargs: object) -> None:
        if kwargs:
            real_chmod(path, mode, **kwargs)
            return
        raise failure

    monkeypatch.setattr(executor.config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(executor.tempfile, "mkstemp", mkstemp)
    monkeypatch.setattr(executor.os, "chmod", chmod)

    with pytest.raises(typer.Exit) as error:
        executor._replace_binary(
            install_info(InstallMethod.BINARY, target),
            b"replacement",
            "2.4.1",
            "linux",
            "",
        )

    assert error.value.exit_code == 1
    assert target.read_bytes() == b"current"
    assert (config_dir / "bin" / "observal.prev").read_bytes() == b"current"
    assert len(staged) == 1
    assert not staged[0].exists()
    assert messages == [f"[red]Failed to replace binary: {failure}[/red]"]


def test_replace_binary_preserves_primary_error_when_cleanup_fails(
    tmp_path: Path,
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "tools" / "observal"
    failure = OSError("mode denied")
    write = MagicMock(return_value=len(b"replacement"))
    close = MagicMock()
    chmod = MagicMock(side_effect=failure)
    unlink = MagicMock(side_effect=OSError("cleanup denied"))
    monkeypatch.setattr(executor.config, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(executor.tempfile, "mkstemp", lambda **kwargs: (73, str(tmp_path / "staged")))
    monkeypatch.setattr(executor.os, "write", write)
    monkeypatch.setattr(executor.os, "close", close)
    monkeypatch.setattr(executor.os, "chmod", chmod)
    monkeypatch.setattr(executor.os, "unlink", unlink)

    with pytest.raises(typer.Exit) as error:
        executor._replace_binary(
            install_info(InstallMethod.BINARY, target),
            b"replacement",
            "2.4.1",
            "linux",
            "",
        )

    assert error.value.exit_code == 1
    write.assert_called_once_with(73, b"replacement")
    close.assert_called_once_with(73)
    chmod.assert_called_once_with(str(tmp_path / "staged"), 0o755)
    unlink.assert_called_once_with(str(tmp_path / "staged"))
    assert messages == [f"[red]Failed to replace binary: {failure}[/red]"]


def test_replace_binary_temp_creation_error_propagates_after_backup(
    tmp_path: Path,
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "config"
    target = tmp_path / "tools" / "observal"
    target.parent.mkdir()
    target.write_bytes(b"current")
    failure = OSError("temporary directory unavailable")
    monkeypatch.setattr(executor.config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(executor.tempfile, "mkstemp", MagicMock(side_effect=failure))

    with pytest.raises(OSError) as error:
        executor._replace_binary(
            install_info(InstallMethod.BINARY, target),
            b"replacement",
            "2.4.1",
            "linux",
            "",
        )

    assert error.value is failure
    assert target.read_bytes() == b"current"
    assert (config_dir / "bin" / "observal.prev").read_bytes() == b"current"
    assert messages == []


@pytest.mark.parametrize(
    ("stdout", "stderr", "target", "direction", "expected_message"),
    [
        ("observal 2.4.1\n", "", "2.4.1", "upgrade", "[green]Upgraded to v2.4.1[/green]"),
        (
            "",
            "Observal CLI v2.4.1+build.7\n",
            "2.4.1+build.7",
            "downgrade",
            "[green]Downgraded to v2.4.1+build.7[/green]",
        ),
        (
            "observal v2.4.1-rc.1\n",
            "",
            "2.4.1rc1",
            "upgrade",
            "[green]Upgraded to v2.4.1rc1[/green]",
        ),
    ],
)
def test_verify_install_uses_target_executable_and_accepts_expected_version(
    stdout: str,
    stderr: str,
    target: str,
    direction: str,
    expected_message: str,
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = Path("/managed/tools/observal")
    run = MagicMock(return_value=completed(stdout=stdout, stderr=stderr))
    monkeypatch.setattr(executor.subprocess, "run", run)

    assert executor._verify_install(install_info(InstallMethod.UV_TOOL, path), target, direction) is None

    run.assert_called_once_with(
        [str(path), VERSION_OPTION],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        env=launcher.script_env(),
    )
    assert messages == [expected_message]


@pytest.mark.parametrize(
    ("result", "expected_detail"),
    [
        (completed(2, stdout="fallback", stderr=" denied \n"), "denied"),
        (completed(3, stdout=" bad output \n", stderr=""), "bad output"),
        (completed(-signal.SIGTERM), f"exit code {-signal.SIGTERM}"),
    ],
)
def test_verify_install_reports_process_failures_with_detail_precedence(
    result: SimpleNamespace,
    expected_detail: str,
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = Path("/managed/tools/observal")
    monkeypatch.setattr(executor.subprocess, "run", MagicMock(return_value=result))

    with pytest.raises(typer.Exit) as error:
        executor._verify_install(install_info(InstallMethod.PIPX, path), "2.4.1", "downgrade")

    assert error.value.exit_code == 1
    assert messages == [
        f"[red]Downgrade verification failed:[/red] {expected_detail}",
        f"[dim]Executable: {path}[/dim]",
    ]


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.TimeoutExpired(["/managed/tools/observal", VERSION_OPTION], 10),
        FileNotFoundError("executable missing"),
        PermissionError("not executable"),
    ],
)
def test_verify_install_translates_execution_errors_and_chains_cause(
    failure: OSError | subprocess.TimeoutExpired,
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = Path("/managed/tools/observal")
    run = MagicMock(side_effect=failure)
    monkeypatch.setattr(executor.subprocess, "run", run)

    with pytest.raises(typer.Exit) as error:
        executor._verify_install(install_info(InstallMethod.PIP, path), "2.4.1", "upgrade")

    assert error.value.exit_code == 1
    assert error.value.__cause__ is failure
    run.assert_called_once_with(
        [str(path), VERSION_OPTION],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        env=launcher.script_env(),
    )
    assert messages == [
        f"[red]Upgrade verification failed:[/red] {failure}",
        f"[dim]Executable: {path}[/dim]",
    ]


def test_verify_install_rejects_unparseable_output(
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = Path("/managed/tools/observal")
    monkeypatch.setattr(
        executor.subprocess,
        "run",
        MagicMock(return_value=completed(stdout="Observal development build\n")),
    )

    with pytest.raises(typer.Exit) as error:
        executor._verify_install(install_info(InstallMethod.PIP, path), "2.4.1", "upgrade")

    assert error.value.exit_code == 1
    assert messages == [
        "[red]Upgrade verification failed:[/red] could not parse version from 'Observal development build'",
        f"[dim]Executable: {path}[/dim]",
    ]


@pytest.mark.parametrize(
    ("output", "target"),
    [("observal 2.4.1-..\n", "2.4.1"), ("observal 2.4.1\n", "not-a-version")],
)
def test_verify_install_rejects_invalid_reported_or_target_version(
    output: str,
    target: str,
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = Path("/managed/tools/observal")
    monkeypatch.setattr(executor.subprocess, "run", MagicMock(return_value=completed(stdout=output)))

    with pytest.raises(typer.Exit) as error:
        executor._verify_install(install_info(InstallMethod.PIP, path), target, "upgrade")

    assert error.value.exit_code == 1
    assert isinstance(error.value.__cause__, InvalidVersion)
    assert messages == [
        f"[red]Upgrade verification failed:[/red] invalid version output {output.strip()!r}",
        f"[dim]Executable: {path}[/dim]",
    ]


def test_verify_install_rejects_wrong_version_with_path_guidance(
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = Path("/managed/tools/observal")
    monkeypatch.setattr(
        executor.subprocess,
        "run",
        MagicMock(return_value=completed(stdout="observal 2.5.0\n")),
    )

    with pytest.raises(typer.Exit) as error:
        executor._verify_install(install_info(InstallMethod.UV_TOOL, path), "2.4.1", "downgrade")

    assert error.value.exit_code == 1
    assert messages == [
        f"[red]Downgrade verification failed:[/red] expected v2.4.1, but {path} reports v2.5.0.",
        "[dim]Check for multiple Observal installations on PATH.[/dim]",
    ]


# ── Installers run to completion ────────────────────────────────────────────
# conftest routes installer runs through subprocess.run; these use the real function.
_real_run_installer = executor.run_installer


def _fake_installer(tmp_path: Path, seconds: float) -> tuple[list[str], Path]:
    """An installer as uv and pip behave: it removes the installed CLI, works for ``seconds`` while printing
    progress to stdout and stderr, then writes the new CLI. Stopped part-way, there is no CLI."""
    cli = tmp_path / "observal"
    cli.write_text("old")
    code = (
        "import pathlib, sys, time\n"
        f"cli = pathlib.Path({str(cli)!r})\n"
        "cli.unlink()\n"
        f"end = time.monotonic() + {seconds}\n"
        "while time.monotonic() < end:\n"
        "    print('Resolved 30 packages', flush=True)\n"
        "    print('Installing...', file=sys.stderr, flush=True)\n"
        "    time.sleep(0.05)\n"
        "cli.write_text('new')\n"
    )
    return [sys.executable, "-c", code], cli


def _installer_parent(tmp_path: Path, installer: list[str], *, lock: bool = False) -> subprocess.Popen:
    """A CLI-like parent in its own process group (as a terminal or harness starts one) running ``installer``.

    Python ignores Ctrl+C when its parent shell started it with SIGINT ignored (a background job); a
    terminal's foreground command has the default handler, which the parent restores.
    """
    import os

    from observal_cli.shared import launcher

    code = (
        "import signal\n"
        "signal.signal(signal.SIGINT, signal.default_int_handler)\n"
        "from pathlib import Path\n"
        "from observal_cli import upgrade_executor, upgrade_lock\n"
        f"upgrade_lock.CONFIG_DIR = Path({str(tmp_path)!r})\n"
        + ("upgrade_lock.acquire_lock('cli')\n" if lock else "")
        + f"result = upgrade_executor.run_installer({installer!r}, timeout=60)\n"
        "print('installer exit', result.returncode, flush=True)\n"
    )
    env = {**os.environ, "PYTHONPATH": launcher.package_root()}
    return subprocess.Popen(
        [sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
    )


def _wait_until_removed(path: Path, seconds: float = 30) -> None:
    """Block until the fake installer has removed the CLI: it is now mid-install."""
    import time

    deadline = time.monotonic() + seconds
    while path.exists():
        assert time.monotonic() < deadline, "the installer never started"
        time.sleep(0.02)


def _wait_for(path: Path, content: str, seconds: float = 15) -> bool:
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists() and path.read_text() == content:
            return True
        time.sleep(0.1)
    return False


@pytest.mark.skipif(sys.platform == "win32", reason="process groups and signals as a POSIX terminal sends them")
def test_ctrl_c_waits_for_the_installer_instead_of_leaving_no_cli(tmp_path: Path) -> None:
    """Live finding: `uv tool install --force` or `pip install` stopped part-way left no `observal` at all."""
    import os
    import time

    installer, cli = _fake_installer(tmp_path, seconds=2)
    parent = _installer_parent(tmp_path, installer)
    _wait_until_removed(cli)
    time.sleep(0.3)
    os.killpg(parent.pid, signal.SIGINT)  # Ctrl+C reaches the terminal's foreground process group
    stdout, stderr = parent.communicate(timeout=30)

    assert cli.read_text() == "new", "the installer was interrupted"
    assert b"installer exit 0" in stdout
    assert b"Finishing the CLI install first" in stderr


@pytest.mark.skipif(sys.platform == "win32", reason="process groups and signals as a POSIX harness sends them")
def test_a_killed_command_does_not_take_the_installer_with_it(tmp_path: Path) -> None:
    """An agent's tool timeout kills the command's process group; the install still completes.

    Second review: with pipes, the installer's next progress line after the kill failed (no reader left),
    and it died between removing the old CLI and writing the new one. Its output now goes to files.
    """
    import os
    import time

    installer, cli = _fake_installer(tmp_path, seconds=2)
    parent = _installer_parent(tmp_path, installer)
    _wait_until_removed(cli)
    time.sleep(0.3)
    os.killpg(parent.pid, signal.SIGKILL)
    parent.communicate(timeout=30)

    assert _wait_for(cli, "new"), "the installer died with the command"


@pytest.mark.skipif(sys.platform == "win32", reason="the lock outlives its process through fork on POSIX")
def test_an_installer_keeps_the_lock_when_its_command_dies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A retried command must not start a second install over one still running."""
    import os
    import time

    from observal_cli import upgrade_lock

    installer, cli = _fake_installer(tmp_path, seconds=3)
    parent = _installer_parent(tmp_path, installer, lock=True)
    _wait_until_removed(cli)
    time.sleep(0.3)
    os.killpg(parent.pid, signal.SIGKILL)
    parent.wait(timeout=30)

    monkeypatch.setattr(upgrade_lock, "CONFIG_DIR", tmp_path)
    with pytest.raises(upgrade_lock.UpgradeLockError):
        upgrade_lock.acquire_lock("cli")  # the orphaned installer still holds it
    assert _wait_for(cli, "new")
    time.sleep(0.3)
    upgrade_lock.release_lock(upgrade_lock.acquire_lock("cli"))  # released once it finished


def test_installer_output_and_status_are_returned(tmp_path: Path) -> None:
    result = _real_run_installer(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"], timeout=30
    )
    assert (result.returncode, result.stdout.strip(), result.stderr.strip()) == (3, "out", "err")


def test_an_installer_past_its_timeout_is_left_to_finish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Second review: the timeout killed the installer, which may already have removed the CLI."""
    from observal_cli import upgrade_lock

    monkeypatch.setattr(upgrade_lock, "CONFIG_DIR", tmp_path)
    installer, cli = _fake_installer(tmp_path, seconds=2)
    lock = upgrade_lock.acquire_lock("cli")
    try:
        with pytest.raises(executor.InstallerStillRunning) as running:
            _real_run_installer(installer, timeout=0.5)
    finally:
        upgrade_lock.release_lock(lock)

    assert running.value.pid > 0
    if sys.platform != "win32":
        # Releasing a handed-off lock only closed this process's handle; the installer still holds it.
        with pytest.raises(upgrade_lock.UpgradeLockError):
            upgrade_lock.acquire_lock("cli")
    assert _wait_for(cli, "new"), "the installer was stopped"
    import time

    time.sleep(0.3)
    upgrade_lock.release_lock(upgrade_lock.acquire_lock("cli"))


def test_an_install_still_running_is_reported_and_not_retried(
    messages: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second source must not be installed over one still being written."""
    calls = []

    def still_running(argv, **_kwargs):
        calls.append(argv)
        raise executor.InstallerStillRunning(argv, 4242, 120)

    monkeypatch.setattr(executor, "run_installer", still_running)

    with pytest.raises(typer.Exit):
        executor._install_via_uv("2.4.1", "upgrade", RecordingSpinner())

    assert len(calls) == 1
    assert messages == [
        "[red]The install of v2.4.1 is still running in the background (PID 4242) after 120 s. "
        "Run the command again once it finishes.[/red]"
    ]
