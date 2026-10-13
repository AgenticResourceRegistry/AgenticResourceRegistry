# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Isolated behavioral tests for the CLI upgrade executor."""

from __future__ import annotations

import hashlib
import signal
import stat
import subprocess
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
        else (
            "2.4.1",
            "upgrade",
            spinner,
        )
    )
    installer_kwargs = {"interactive": True} if method == InstallMethod.BINARY else {}
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
    monkeypatch.setattr(executor.sys, "executable", "/venv/bin/python")
    monkeypatch.setenv("PIP_INDEX_URL", "https://packages.example.test/simple")

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        events.append(("run", command, kwargs))
        return completed()

    monkeypatch.setattr(executor.subprocess, "run", run)

    result = getattr(executor, installer_name)("2.4.1", direction, spinner)

    assert result is None
    assert events == [
        ("spinner enter", expected_progress),
        (
            "run",
            expected_command,
            {"capture_output": True, "text": True, "timeout": 120},
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

    with pytest.raises(typer.Exit) as error:
        getattr(executor, installer_name)("2.4.1", direction, RecordingSpinner())

    assert error.value.exit_code == 1
    assert run.call_count == 2
    assert "observal-cli==2.4.1" in " ".join(run.call_args_list[0].args[0])
    assert "git+https://github.com/Observal/Observal@v2.4.1" in " ".join(run.call_args_list[1].args[0])
    assert messages == [
        f"[red]{direction.capitalize()} failed; the installed CLI was left unchanged.[/red]",
        f"  observal-cli==2.4.1: {expected_stderr}",
        f"  git+https://github.com/Observal/Observal@v2.4.1: {expected_stderr}",
    ]


@pytest.mark.parametrize(
    ("installer_name", "failure"),
    [
        ("_install_via_uv", FileNotFoundError("uv is missing")),
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

    with pytest.raises(type(failure)) as error:
        getattr(executor, installer_name)("2.4.1", "upgrade", spinner)

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
        timeout=10,
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
    run.assert_called_once_with([str(path), VERSION_OPTION], capture_output=True, text=True, timeout=10)
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
