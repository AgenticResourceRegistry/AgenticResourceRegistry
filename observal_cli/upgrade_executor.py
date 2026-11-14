# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""CLI upgrade executor - handles uv/pipx/pip/binary install paths.

Extracted from cmd_ops.py for testability and readability.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx
import typer
from rich import print as rprint

from observal_cli import config
from observal_cli.constants import REDIRECT_ALLOWLIST
from observal_cli.install_detector import SOURCE_SPEC, InstallInfo, InstallMethod, install_spec, pip_install_prefix

GITHUB_REPO = "AgenticResourceRegistry/AgenticResourceRegistry"

# What the last successful package install in this process came from (for example
# `git+https://...@<commit>`). A development build's source is chosen during the
# install, so the caller reads it here to record what was really installed.
last_installed_source: str | None = None


class InstallerStillRunning(subprocess.SubprocessError):
    """An installer outlasted its wait. It was left running: stopping it part-way would leave no CLI."""

    def __init__(self, argv: list[str], pid: int, timeout: float) -> None:
        super().__init__(f"{argv[0]} is still installing (PID {pid}) after {int(timeout)} s")
        self.pid = pid
        self.timeout = timeout


def run_installer(argv: list[str], *, timeout: float, env: dict[str, str] | None = None):
    """Run a package installer to completion, even when this process is interrupted, killed or tired of waiting.

    `uv tool install --force` and `pip install` remove the installed CLI before
    writing the new one; stopped in between, they leave no CLI at all. So:

    - The installer runs in its own session (its own process group on Windows):
      a Ctrl+C in the terminal, or a harness killing this command's process
      group at its timeout, does not reach it. Ctrl+C here waits for it.
    - Its output goes to files, not pipes: a pipe whose reader died makes the
      installer's next progress line fail (uv aborts, pip raises BrokenPipeError).
    - It keeps the upgrade lock this process holds (POSIX), so if this process
      dies, no other one starts installing over it until it is done.
    - Past ``timeout`` it is not killed. :class:`InstallerStillRunning` is raised
      and the lock is handed to it (POSIX), released when it exits.
    """
    from observal_cli import upgrade_lock

    options: dict = {}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
        options["pass_fds"] = upgrade_lock.held_lock_fds()
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err, env=env, **options)
        deadline = time.monotonic() + timeout
        warned = False
        while True:
            try:
                process.wait(timeout=max(0.0, deadline - time.monotonic()))
                break
            except KeyboardInterrupt:
                if not warned:
                    print(
                        "Finishing the CLI install first: stopping it now would leave no working CLI.",
                        file=sys.stderr,
                        flush=True,
                    )
                    warned = True
            except subprocess.TimeoutExpired:
                upgrade_lock.hand_off_held_locks()
                raise InstallerStillRunning(argv, process.pid, timeout) from None
        outputs = []
        for stream in (out, err):
            stream.seek(0)
            outputs.append(stream.read().decode("utf-8", errors="replace"))
    return subprocess.CompletedProcess(argv, process.returncode, *outputs)


def execute(
    install_info: InstallInfo,
    target_version: str,
    direction: str,
    spinner,
    *,
    interactive: bool = True,
    commit: str | None = None,
) -> None:
    """Execute the actual version change for uv/pip/binary installs.

    Args:
        install_info: Detected install method and path.
        target_version: Target version string (e.g. "0.7.0").
        direction: "upgrade" or "downgrade".
        spinner: Context manager for progress display.
        interactive: Whether checksum fallback may ask for confirmation.
        commit: Build commit a server reported, for development versions.
    """
    global last_installed_source
    last_installed_source = None
    # An older CLI must not pick up dependency releases newer than the ones in use now.
    ceiling = _dependency_ceiling() if direction == "downgrade" else None
    if install_info.method == InstallMethod.UV_TOOL:
        _install_via_uv(
            target_version,
            direction,
            spinner,
            commit=commit,
            ceiling=ceiling,
            in_place=_is_running_windows_launcher(install_info.path),
        )
    elif install_info.method == InstallMethod.PIPX:
        with _running_launcher_set_aside(install_info.path):
            _install_via_pipx(target_version, direction, spinner, commit=commit, ceiling=ceiling)
    elif install_info.method == InstallMethod.PIP:
        with _running_launcher_set_aside(install_info.path):
            _install_via_pip(target_version, direction, spinner, install_info, commit=commit, ceiling=ceiling)
    elif install_info.method == InstallMethod.BINARY:
        _install_binary(install_info, target_version, direction, spinner, interactive=interactive)
    else:
        rprint(f"[red]Cannot {direction} - unsupported install method: {install_info.method.value}[/red]")
        raise typer.Exit(1)

    # Post-install verification
    _verify_install(install_info, target_version, direction)


@contextmanager
def _running_launcher_set_aside(launcher: Path):
    """Let an installer replace the `observal.exe` that is running this process.

    Windows refuses to overwrite a running executable, so pipx and pip fail to
    write the new launcher when the CLI upgrades itself. It usually allows
    renaming one: move the launcher aside, and put it back if the installer did
    not write a replacement. The renamed file is deleted by a later run, once
    the process that held it has exited. Best effort: when the rename is refused
    too, the installer runs as it would have. uv's launcher always refuses, see
    `_install_via_uv`.
    """
    from observal_cli.install_detector import ENTRY_POINT_NAMES

    if os.name != "nt" or launcher.suffix.lower() != ".exe" or launcher.name.lower() not in ENTRY_POINT_NAMES:
        yield
        return
    for leftover in launcher.parent.glob(f"{launcher.name}.old-*"):
        try:
            leftover.unlink()
        except OSError:
            pass  # still running in another process
    aside = launcher.with_name(f"{launcher.name}.old-{os.getpid()}")
    try:
        launcher.rename(aside)
    except OSError:
        yield
        return
    try:
        yield
    finally:
        if not launcher.exists():
            try:
                aside.rename(launcher)
            except OSError:
                pass


def _is_running_windows_launcher(path: Path) -> bool:
    """Whether ``path`` is the `observal.exe` this process was started through, on Windows."""
    from observal_cli.install_detector import ENTRY_POINT_NAMES

    return os.name == "nt" and path.suffix.lower() == ".exe" and path.name.lower() in ENTRY_POINT_NAMES


def _dependency_ceiling() -> Path | None:
    """Constraints file capping every dependency at the version this CLI runs with now.

    Older CLI versions declared open-ended dependencies (`typer>=0.12.0`), written
    before later releases of those dependencies existed. Installing one today
    resolves the newest release, which that old code may not run on: a 1.17
    development build picks up Typer 0.29 and crashes on every command. The
    dependencies the running CLI uses are known to work with a CLI at least this
    new, so they are the newest an older one is allowed.
    """
    from importlib.metadata import distributions

    from packaging.version import InvalidVersion, Version

    lines = set()
    for distribution in distributions():
        name = distribution.metadata["Name"]
        if not name or name.lower().replace("_", "-") == "observal-cli":
            continue
        try:
            version = Version(distribution.version)
        except InvalidVersion:
            continue
        if not version.local:
            lines.add(f"{name}<={version}")
    try:
        config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        path = config.CONFIG_DIR / "sync-constraints.txt"
        path.write_text("\n".join(sorted(lines)) + "\n")
    except OSError:
        return None
    return path


def _with_ceiling(argv: list[str], ceiling: Path) -> tuple[list[str], dict[str, str] | None]:
    """``argv`` and environment that apply the constraints file."""
    if argv[0] != "pipx":
        return [*argv, "-c", str(ceiling)], None
    # pipx forwards pip options only as one shell-split string, so the file goes through
    # pip's own variable. pip splits that on whitespace: a path with spaces (a Windows
    # profile such as `C:\Users\First Last`) is passed as a file URL, which has none.
    location = str(ceiling)
    if any(character.isspace() for character in location):
        location = ceiling.resolve().as_uri()
    return argv, {**os.environ, "PIP_CONSTRAINT": location}


def _install_via_uv(
    target_version: str,
    direction: str,
    spinner,
    commit: str | None = None,
    in_place: bool = False,
    ceiling: Path | None = None,
) -> None:
    """Install through uv's tool environment.

    ``in_place`` is for a CLI upgrading itself through uv's Windows launcher.
    That launcher keeps its own file open, so `uv tool install` updates the
    environment and then fails writing the launcher, which can be neither
    overwritten nor renamed while it runs. The launcher is a version-independent
    stub, so installing into the tool's environment is the whole change. uv's
    receipt keeps the previous source until the next `uv tool install`.
    """
    if in_place:
        argv_for = lambda spec: ["uv", "pip", "install", "--python", sys.executable, spec]  # noqa: E731
    else:
        argv_for = lambda spec: ["uv", "tool", "install", spec, "--force"]  # noqa: E731
    _install_with_source_fallback(argv_for, target_version, direction, spinner, commit=commit, ceiling=ceiling)


def _install_via_pipx(
    target_version: str, direction: str, spinner, commit: str | None = None, ceiling: Path | None = None
) -> None:
    _install_with_source_fallback(
        lambda spec: ["pipx", "install", spec, "--force"],
        target_version,
        direction,
        spinner,
        commit=commit,
        ceiling=ceiling,
    )


def _install_via_pip(
    target_version: str,
    direction: str,
    spinner,
    install_info: InstallInfo,
    commit: str | None = None,
    ceiling: Path | None = None,
) -> None:
    prefix = pip_install_prefix(install_info)
    _install_with_source_fallback(
        lambda spec: [*prefix, spec, "--quiet"], target_version, direction, spinner, commit=commit, ceiling=ceiling
    )


def package_install_argv(install_info: InstallInfo, spec: str, interpreter: str | None = None) -> list[str]:
    """Command that installs ``spec`` over a uv, pipx or pip installation, using the tool that owns it."""
    if install_info.method == InstallMethod.UV_TOOL:
        return ["uv", "tool", "install", spec, "--force"]
    if install_info.method == InstallMethod.PIPX:
        return ["pipx", "install", spec, "--force"]
    return [*pip_install_prefix(install_info, interpreter), spec, "--quiet"]


def _install_with_source_fallback(
    argv_for,
    target_version: str,
    direction: str,
    spinner,
    repo: str | None = None,
    commit: str | None = None,
    ceiling: Path | None = None,
) -> None:
    """Install from PyPI; if the release is missing there, build its git tag; else leave the install alone.

    Development versions are built from the commit that declares them. With a
    ``ceiling`` (see `_dependency_ceiling`) each source is tried under it first,
    then without it, in case the target needs a dependency newer than the cap.
    """
    from observal_cli.install_detector import SOURCE_REPO, install_specs

    global last_installed_source
    from observal_cli.version_check import dev_source_refs, missing_git_reason

    if reason := missing_git_reason(target_version):
        rprint(f"[red]{reason}[/red] The installed CLI was left unchanged.")
        raise typer.Exit(1)
    specs = install_specs(target_version, dev_source_refs(target_version, commit), repo or SOURCE_REPO)
    if not specs:
        # Checked before anything is installed: there is no rollback afterwards.
        rprint(
            f"[red]Could not find a published commit whose source declares v{target_version}; "
            "the installed CLI was left unchanged.[/red] Development builds come only from commits on main or a "
            "release branch, and finding one needs git and access to github.com."
        )
        raise typer.Exit(1)
    errors = []
    for spec in specs:
        attempts: list[tuple[list[str], dict]] = []
        if ceiling is not None and (capped := _with_ceiling(argv_for(spec), ceiling)) is not None:
            attempts.append((capped[0], {"env": capped[1]} if capped[1] else {}))
        attempts.append((argv_for(spec), {}))
        for argv, extra in attempts:
            with spinner(f"{direction.removesuffix('e').capitalize()}ing to v{target_version}..."):
                try:
                    result = run_installer(argv, timeout=300 if "@" in spec else 120, **extra)
                except FileNotFoundError:
                    # Started outside the shell that installed it (a harness, a GUI), PATH may lack the tool.
                    rprint(
                        f"[red]{argv[0]} is not on PATH, so it cannot change the CLI it installed; "
                        "the installed CLI was left unchanged.[/red]"
                    )
                    raise typer.Exit(1) from None
                except InstallerStillRunning as running:
                    # Not stopped and not retried: a second installer would write over the first.
                    rprint(
                        f"[red]The install of v{target_version} is still running in the background (PID "
                        f"{running.pid}) after {int(running.timeout)} s. Run the command again once it finishes.[/red]"
                    )
                    raise typer.Exit(1) from None
            if result.returncode == 0:
                last_installed_source = spec
                return
        errors.append(f"{spec}: {result.stderr.strip()}")
    rprint(f"[red]{direction.capitalize()} failed; the installed CLI was left unchanged.[/red]")
    for error in errors:
        rprint(f"  {error}")
    raise typer.Exit(1)


def _install_binary(
    install_info: InstallInfo,
    target_version: str,
    direction: str,
    spinner,
    *,
    interactive: bool = True,
) -> None:
    """Download and install standalone binary with checksum verification."""
    if install_spec(target_version) == SOURCE_SPEC:
        rprint(
            f"[red]v{target_version} is a development build with no standalone binary.[/red]\n"
            f"Install it from source: uv tool install --force {SOURCE_SPEC}"
        )
        raise typer.Exit(1)
    system = platform.system().lower()
    machine = platform.machine().lower()

    if machine in ("x86_64", "amd64"):
        arch = "x64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        rprint(f"[red]Unsupported architecture: {machine}[/red]")
        raise typer.Exit(1)

    os_name = {"linux": "linux", "darwin": "macos", "windows": "windows"}.get(system)
    if not os_name:
        rprint(f"[red]Unsupported OS: {system}[/red]")
        raise typer.Exit(1)

    suffix = ".exe" if system == "windows" else ""
    artifact_name = f"observal-{os_name}-{arch}{suffix}"

    # Fetch release by tag
    with spinner("Fetching release info..."):
        resp = httpx.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/v{target_version}",
            timeout=15,
            headers={"Accept": "application/vnd.github+json"},
        )
    if resp.status_code != 200:
        rprint(f"[red]Release v{target_version} not found on GitHub.[/red]")
        raise typer.Exit(1)

    release_data = resp.json()
    assets = {a["name"]: a["browser_download_url"] for a in release_data.get("assets", [])}

    if artifact_name not in assets:
        rprint(f"[red]Binary '{artifact_name}' not found in release assets.[/red]")
        raise typer.Exit(1)

    # Download checksums
    checksums = _fetch_checksums(assets)

    # Download binary with redirect validation
    bin_content = _download_binary(assets[artifact_name], spinner, artifact_name)

    # Verify checksum
    _verify_checksum(bin_content, checksums, artifact_name, interactive=interactive)

    # Backup and replace
    _replace_binary(install_info, bin_content, target_version, system, suffix)


def _fetch_checksums(assets: dict[str, str]) -> dict[str, str]:
    """Download checksums.txt from release assets."""
    checksums: dict[str, str] = {}
    if "checksums.txt" in assets:
        ck_resp = httpx.get(assets["checksums.txt"], timeout=15, follow_redirects=True)
        if ck_resp.status_code == 200:
            for line in ck_resp.text.strip().splitlines():
                parts = line.split()
                if len(parts) == 2:
                    checksums[parts[1]] = parts[0]
    return checksums


def _download_binary(download_url: str, spinner, artifact_name: str) -> bytes:
    """Download binary with redirect domain validation."""
    with spinner(f"Downloading {artifact_name}..."):
        bin_resp = httpx.get(download_url, timeout=120, follow_redirects=True)

    if bin_resp.status_code != 200:
        rprint("[red]Download failed.[/red]")
        raise typer.Exit(1)

    # Validate final URL domain
    final_host = urlparse(str(bin_resp.url)).hostname
    if final_host and final_host not in REDIRECT_ALLOWLIST:
        rprint(f"[red]Download redirected to untrusted host: {final_host}[/red]")
        raise typer.Exit(1)

    return bin_resp.content


def _verify_checksum(
    content: bytes,
    checksums: dict[str, str],
    artifact_name: str,
    *,
    interactive: bool = True,
) -> None:
    """Verify SHA-256 checksum. Aborts on mismatch."""
    actual_hash = hashlib.sha256(content).hexdigest()
    expected_hash = checksums.get(artifact_name)
    if expected_hash:
        if actual_hash != expected_hash:
            rprint("[red]CHECKSUM MISMATCH - download may be corrupted or tampered.[/red]")
            rprint(f"  Expected: {expected_hash}")
            rprint(f"  Got:      {actual_hash}")
            raise typer.Exit(1)
        rprint(f"[dim]SHA-256 verified: {actual_hash[:16]}...[/dim]")
    else:
        rprint("[yellow]No checksum available for verification.[/yellow]")
        if not interactive:
            rprint("[red]Non-interactive installation requires a published checksum.[/red]")
            raise typer.Exit(1)
        if not typer.confirm("Install without verification?", default=False):
            raise typer.Abort()


def _replace_binary(install_info: InstallInfo, content: bytes, target_version: str, system: str, suffix: str) -> None:
    """Backup current binary and atomically replace with new one."""
    target_path = install_info.path
    if not install_info.writable:
        rprint(f"[red]Cannot write to {target_path} - permission denied.[/red]")
        raise typer.Exit(1)

    # Backup current
    backup_dir = config.CONFIG_DIR / "bin"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / "observal.prev"
    if target_path.exists():
        shutil.copy2(str(target_path), str(backup_path))

    # Write to temp file then atomic rename
    fd, tmp_path = tempfile.mkstemp(
        dir=target_path.parent,
        prefix=".observal-update-",
        suffix=suffix,
    )
    try:
        os.write(fd, content)
        os.close(fd)
        os.chmod(tmp_path, 0o755)

        if system == "windows":
            old_path = target_path.with_suffix(".old")
            target_path.rename(old_path)
            Path(tmp_path).rename(target_path)
        else:
            Path(tmp_path).rename(target_path)
    except OSError as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        rprint(f"[red]Failed to replace binary: {e}[/red]")
        raise typer.Exit(1)


def _verify_install(install_info: InstallInfo, target_version: str, direction: str) -> None:
    """Verify that the updated executable reports the requested version."""
    from packaging.version import InvalidVersion, Version

    from observal_cli.install_detector import reported_version, version_argv
    from observal_cli.shared import launcher

    executable = str(install_info.path)
    try:
        result = subprocess.run(
            version_argv(install_info.path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            env=launcher.script_env(),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        rprint(f"[red]{direction.capitalize()} verification failed:[/red] {exc}")
        rprint(f"[dim]Executable: {executable}[/dim]")
        raise typer.Exit(1) from exc

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        rprint(f"[red]{direction.capitalize()} verification failed:[/red] {detail}")
        rprint(f"[dim]Executable: {executable}[/dim]")
        raise typer.Exit(1)

    output = result.stdout.strip() or result.stderr.strip()
    token = reported_version(output)
    if not token:
        rprint(f"[red]{direction.capitalize()} verification failed:[/red] could not parse version from {output!r}")
        rprint(f"[dim]Executable: {executable}[/dim]")
        raise typer.Exit(1)

    try:
        actual = Version(token)
        expected = Version(target_version)
    except InvalidVersion as exc:
        rprint(f"[red]{direction.capitalize()} verification failed:[/red] invalid version output {output!r}")
        rprint(f"[dim]Executable: {executable}[/dim]")
        raise typer.Exit(1) from exc

    if actual != expected:
        rprint(
            f"[red]{direction.capitalize()} verification failed:[/red] "
            f"expected v{expected}, but {executable} reports v{actual}."
        )
        rprint("[dim]Check for multiple Observal installations on PATH.[/dim]")
        raise typer.Exit(1)

    rprint(f"[green]{direction.capitalize()}d to v{actual}[/green]")
