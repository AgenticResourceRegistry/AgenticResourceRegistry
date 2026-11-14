# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Keep the installed CLI on the connected server's exact version, automatically.

The server rejects any CLI whose version differs from its own. Instead of
printing a command and exiting, the CLI installs the server's version over
itself and re-runs the command that hit the mismatch.

Triggers (all call :func:`sync_to_server`):
  - ``observal auth login``, before credentials are asked for
  - the version gate on the first authenticated request of any command
  - a 426 response from the server

Rules:
  - There is no prompt and no opt-out. `observal self ...` is exempt so a manual
    version change is never fought.
  - One attempt per invocation: the re-run carries ``OBSERVAL_CLI_SYNCED_TO``
    and a second mismatch falls through to the ordinary error.
  - The server supplies a version and, for development builds, a commit. The
    repository the source comes from is never taken from the server.
  - Every other copy of the CLI on PATH is brought to the same version, using
    the installer that owns it. Copies no installer owns are reported.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from contextlib import nullcontext, redirect_stdout
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import NoReturn

import httpx
import typer
from loguru import logger as optic

from observal_cli import config

SYNC_ENV = "OBSERVAL_CLI_SYNCED_TO"
LOCK_WAIT_SECONDS = 600
LAST_SYNC_FILE = config.CONFIG_DIR / "last_sync.json"
# CLIs older than this still auto-update themselves to the latest release.
LEGACY_AUTO_UPDATE_BEFORE = "1.10.4"
# The first version that syncs itself. A sync down to an older one leaves a CLI that, after
# the server's next upgrade, needs one manual `observal self upgrade`. (Development builds of
# 1.18.0 from before this landed lack it too; their version cannot tell them apart.)
AUTO_SYNC_SINCE = "1.18.0.dev0"
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_SAFE_VERSION = re.compile(r"[0-9A-Za-z.+!-]{1,64}")

# Set by the `observal` entry point (main's callback). Hooks, the delegation runner
# and MCP servers import the CLI's modules without passing through it, and never sync:
# re-running would replace them with an `observal` command built from their arguments.
_command_process = False


class _InputRecorder:
    """Piped standard input that notes whether the command has read anything from it.

    A re-run gets the same standard input, minus whatever this process already
    read, so a command that read its input is synced but not re-run.
    """

    def __init__(self, stream) -> None:
        self._stream = stream
        self.consumed = False

    def _note(self, data):
        if data:
            self.consumed = True
        return data

    def read(self, *args):
        return self._note(self._stream.read(*args))

    def readline(self, *args):
        return self._note(self._stream.readline(*args))

    def readlines(self, *args):
        return self._note(self._stream.readlines(*args))

    def __iter__(self):
        return self

    def __next__(self):
        return self._note(next(self._stream))

    @property
    def buffer(self):
        self.consumed = True  # Reads below the text layer cannot be seen; assume they happen.
        return self._stream.buffer

    def __getattr__(self, name):
        return getattr(self._stream, name)


def begin_command() -> None:
    """Mark this process as an `observal` command, the only kind that syncs, and watch its piped input."""
    global _command_process
    _command_process = True
    stream = sys.stdin
    try:
        piped = stream is not None and not stream.isatty()
    except (AttributeError, ValueError, OSError):
        piped = False
    if piped and not isinstance(stream, _InputRecorder):
        sys.stdin = _InputRecorder(stream)


def _input_consumed() -> bool:
    stream = sys.stdin
    return isinstance(stream, _InputRecorder) and stream.consumed


@dataclass(frozen=True)
class ServerBuild:
    version: str
    commit: str | None


def fetch_server_build(server_url: str) -> ServerBuild | None:
    """The version, and build commit when reported, of the server at ``server_url``."""
    # Any failure means "unknown", never a crash: this runs ahead of every command.
    try:
        response = httpx.get(f"{server_url.rstrip('/')}/api/v1/config/version", timeout=10)
        if response.status_code != 200:
            return None
        data = response.json()
        version = data.get("server_version")
        commit = data.get("build_commit")
    except Exception:
        return None
    if not isinstance(version, str) or not version:
        return None
    commit = commit if isinstance(commit, str) and _COMMIT_RE.fullmatch(commit) else None
    return ServerBuild(version=version, commit=commit)


def _notice(message: str) -> None:
    """Progress goes to stderr, and nowhere in JSON mode: there stderr carries exactly one error object."""
    from observal_cli.errors import machine_output_requested

    if not machine_output_requested(sys.argv[1:]):
        print(message, file=sys.stderr, flush=True)


def _positional() -> list[str]:
    return [argument for argument in sys.argv[1:] if not argument.startswith("-")]


def _self_managed() -> bool:
    positional = _positional()
    # The same groups the version gate exempts: `self` changes the CLI version by hand,
    # and `server` manages a local server whose version may be changing under it.
    return bool(positional) and positional[0] in ("self", "server")


def sync_to_server(server_url: str, *, rerun: bool = True) -> str | None:
    """Install the server's CLI version over this one, then re-run the current command.

    Does not return when the sync succeeds and the command is re-run: the
    process is replaced by (or exits with the status of) the same command on
    the new CLI. Returns None when the sync succeeded without a re-run:
    ``rerun`` is off, or the command already read its piped standard input
    (see :func:`fail_synced_without_rerun`). Otherwise returns, as a sentence,
    why the CLI was not synced, so the caller can report the mismatch with the
    manual command.
    """
    from packaging.version import InvalidVersion, Version

    from observal_cli import install_detector, version_check
    from observal_cli.install_detector import InstallMethod
    from observal_cli.upgrade_lock import release_lock

    if not _command_process:
        return (
            "Only an `observal` command updates the CLI, not a hook, MCP server or background worker. "
            "Run any `observal` command to sync it."
        )
    if _positional()[:2] == ["delegate", "mcp"]:
        # Re-running would start a second server on a connection the client already initialized.
        return "The observal-agents MCP server does not update the CLI while it serves."
    if _self_managed():
        return "`observal self` and `observal server` commands are not synced automatically."
    build = fetch_server_build(server_url)
    if build is None or build.version == "dev":
        return "The server did not report a version."
    current = version_check.get_current_version()
    try:
        target = Version(build.version)
        if current == "0.0.0":
            return "This CLI runs from an unversioned source checkout."
        if Version(current) == target:
            return "The CLI already reports the server's version."
    except InvalidVersion:
        return f"The server reported an unusable version: {build.version[:40]!r}."
    if not version_check.check_version_floor(str(target)):
        return f"Server version {target} is below the supported floor {version_check.VERSION_FLOOR}."
    # Any earlier attempt ends it, not only one for this target: a server mid-rollout
    # could otherwise answer with alternating versions and restart the command forever.
    if previous := os.environ.get(SYNC_ENV):
        return f"This command already synced the CLI to {previous}; it now reports {current}."

    install = install_detector.detect()
    if install.method == InstallMethod.SYSTEM_PACKAGE:
        return f"This CLI is managed by {install.managed_by or 'the system package manager'}."
    if install.method == InstallMethod.UNKNOWN:
        return "The CLI install method could not be determined."
    if install.method == InstallMethod.BINARY and target.is_devrelease:
        return f"{target} is a development build and has no standalone binary."
    if reason := version_check.missing_git_reason(build.version):
        return reason

    direction = "upgrade" if target > Version(current) else "downgrade"
    source = " (development build, from source)" if target.is_devrelease else ""
    _notice(f"Observal CLI {current} does not match server {build.version}. Syncing the CLI to it{source}...")

    lock = _wait_for_lock()
    if lock is None:
        return "Another CLI version change did not finish in time."
    try:
        # Another process may have synced while this one waited for the lock.
        if not version_check._same_version(install_detector.installed_version(install.path), str(target)):
            failure = _install(install, str(target), direction, build.commit)
            if failure:
                return failure
            from observal_cli import upgrade_executor

            _record(current, str(target), build.commit, upgrade_executor.last_installed_source, install)
        elif problem := _start_failure(install):
            # Reporting the right version is not enough: that process's install may have failed its start check.
            return f"Version {target} is installed but does not start: {problem}"
        try:
            version_check.CACHE_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        sync_other_installs(str(target), build.commit, install, lock_held=True)
    finally:
        release_lock(lock)

    _notice(f"Observal CLI is now {target}.")
    if _is_older(str(target), AUTO_SYNC_SINCE):
        _notice(
            f"Version {target} predates automatic sync: when the server is upgraded, run "
            "`observal self upgrade --version <server version> --force` once."
        )
    global last_rerun_blocker
    last_rerun_blocker = _rerun_blocker()
    if rerun and last_rerun_blocker is None:
        _rerun(install, str(target))
    return None


# Why the last successful sync did not re-run its command, for the caller's message.
last_rerun_blocker: str | None = None


def _rerun_blocker() -> str | None:
    """Why re-running the command could do something twice or differently, or None when it is safe."""
    from observal_cli import client

    # Applies to every trigger, not only a 426: the gate and login can follow a write sent outside client.py.
    if client._requests_completed:
        return "had already sent a request to the server"
    if _input_consumed():
        return "had already read its standard input"
    return None


def fail_synced_without_rerun(server_version: str, **context) -> NoReturn:
    """Report a sync that succeeded but did not re-run the command (see :func:`_rerun_blocker`)."""
    from observal_cli.errors import ErrorCategory, fail

    blocker = last_rerun_blocker or "could not be re-run safely"
    fail(
        ErrorCategory.VERSION,
        f"The CLI was synced to the server's version {server_version}, but this command {blocker}, "
        "so it was not re-run.",
        remediation="Check what the command already did, then run it again with the same input.",
        **context,
    )


def manual_fix(server_version: str, cli_version: str) -> str:
    """What to do by hand when the automatic sync did not complete.

    Built here, never taken from the server: a 426 body's `install_command` can
    name any commit, including an unmerged one this CLI refuses to build. For a
    development build it is `observal self ...`, which builds only a published
    commit, not a raw `uv tool install ...@main` that follows a moving branch and
    itself needs the git that may be missing.
    """
    from packaging.version import InvalidVersion, Version

    from observal_cli import version_check
    from observal_cli.install_detector import upgrade_command

    try:
        # Only a parsed version goes into a command: the string comes from the server.
        parsed = Version(server_version)
    except InvalidVersion:
        return (
            "The server reported a version that is not valid, so no install command can be given. "
            "Ask the server's administrator which CLI version it needs."
        )
    if not _SAFE_VERSION.fullmatch(server_version):
        server_version = str(parsed)  # parsed, but with characters a shell would read: use the canonical form
    if version_check.missing_git_reason(server_version):
        return (
            f"Install git with `{version_check.git_install_command()}`, open a new terminal, and run the command "
            "again; the CLI then syncs itself."
        )
    try:
        ahead = Version(cli_version) > parsed
    except InvalidVersion:
        ahead = False
    development = parsed.is_devrelease
    if ahead:
        return f"Run observal self downgrade --version {server_version} --force, then retry."
    if development:
        return f"Run observal self upgrade --version {server_version} --force, then retry."
    return f"Run {upgrade_command(server_version)}, then retry."


def _wait_for_lock():
    """Take the CLI upgrade lock, waiting out a version change another process started."""
    from observal_cli.upgrade_lock import UpgradeLockError, acquire_lock

    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            return acquire_lock("cli")
        except UpgradeLockError:
            if time.monotonic() >= deadline:
                return None
            time.sleep(2)


def _install(install, target: str, direction: str, commit: str | None) -> str | None:
    """Run the installer quietly. Returns what went wrong, or None on success."""
    from observal_cli.upgrade_executor import execute

    captured = StringIO()
    try:
        with redirect_stdout(captured):
            execute(install, target, direction, lambda _message=None: nullcontext(), interactive=False, commit=commit)
    except (typer.Exit, typer.Abort):
        lines = [line.strip() for line in captured.getvalue().splitlines() if line.strip()]
        # The cause is the installer's last line, so keep the tail.
        return " ".join(lines)[-600:] or "The installer failed."
    except (OSError, subprocess.SubprocessError, httpx.HTTPError) as error:
        return f"The installer failed: {type(error).__name__}."
    if _is_older(target, LEGACY_AUTO_UPDATE_BEFORE):
        config.save({"auto_update": False})
    problem = _start_failure(install)
    return f"Version {target} was installed but does not start: {problem}" if problem else None


def _start_failure(install) -> str | None:
    """Why the installed CLI cannot run a command, or None when it can.

    `--version` answers before any command is loaded, so it passes on an install
    whose dependencies are wrong. Asking a leaf command for its help loads the
    whole command tree and needs no server.
    """
    return _start_failure_at(install.path)


def _start_failure_at(path) -> str | None:
    import tempfile

    from observal_cli.install_detector import version_argv
    from observal_cli.shared import launcher

    argv = [*version_argv(path)[:-1], "auth", "whoami", "--help"]
    environment = {**launcher.script_env(), "OBSERVAL_NO_UPDATE_CHECK": "1"}
    try:
        # Run in an empty directory, not the user's. A CLI's first start migrates
        # legacy agent markers by scanning its working directory and that directory's
        # parent; from a large or network-backed checkout (a OneDrive folder seen
        # through WSL took minutes) that scan, not the CLI, decided the outcome.
        with tempfile.TemporaryDirectory(prefix="observal-start-check-") as scratch:
            workdir = Path(scratch) / "cwd"
            workdir.mkdir()
            result = subprocess.run(
                argv,
                cwd=workdir,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
    except (OSError, subprocess.SubprocessError) as error:
        return type(error).__name__
    if result.returncode == 0:
        return None
    lines = [line.strip() for line in (result.stderr or result.stdout or "").splitlines() if line.strip()]
    return lines[-1][:300] if lines else f"exit code {result.returncode}"


def _is_older(version: str, than: str) -> bool:
    from packaging.version import Version

    return Version(version) < Version(than)


def _record(previous: str, target: str, server_commit: str | None, installed_from: str | None, install) -> None:
    """Remember the version this sync replaced and what replaced it.

    ``server_commit`` is what the server reported; ``installed_from`` is the source
    actually installed. They differ when the reported commit was not published and
    another commit declaring the same version was built instead.
    """
    try:
        config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        LAST_SYNC_FILE.write_text(
            json.dumps(
                {
                    "previous_version": previous,
                    "version": target,
                    "server_commit": server_commit,
                    "installed_from": installed_from,
                    "install_method": install.method.value,
                    "path": str(install.path),
                    "synced_at": datetime.now(UTC).isoformat(),
                },
                indent=2,
            )
        )
    except OSError as error:
        optic.debug("could not record CLI sync: error_type={}", type(error).__name__)


def sync_other_installs(target: str, commit: str | None, current=None, *, lock_held: bool = False) -> None:
    """Bring every other Observal CLI on PATH to ``target``. Never raises: a stray copy must not block a command.

    Installs run under the CLI upgrade lock. A caller that does not hold it
    takes it without waiting; if another process holds it, that process is
    changing versions and syncs these copies itself, so this call does nothing.
    """
    from observal_cli.upgrade_lock import UpgradeLockError, acquire_lock, release_lock

    lock = None
    if not lock_held:
        try:
            lock = acquire_lock("cli")
        except (UpgradeLockError, OSError):
            return
    try:
        _sync_other_installs(target, commit, current)
    except Exception as error:
        optic.debug("syncing other CLI installs failed: error_type={}", type(error).__name__)
    finally:
        if lock is not None:
            release_lock(lock)


def _sync_other_installs(target: str, commit: str | None, current) -> None:
    from observal_cli import install_detector, upgrade_executor, version_check
    from observal_cli.install_detector import install_specs

    current = current or install_detector.detect()
    others = install_detector.find_other_installs(current)
    specs: list[str] | None = None
    ceiling = None
    for other in others:
        # The right version is not enough: a copy can report it and still crash on every command.
        if _copy_works(other.path, target):
            continue
        if other.info is None:
            _report_copy(other.path, target, "no installer this CLI can drive owns it")
            continue
        if specs is None:
            specs = install_specs(target, version_check.dev_source_refs(target, commit))
        if not specs:
            _report_copy(
                other.path,
                target,
                version_check.missing_git_reason(target) or "no published commit declares that version",
            )
            continue
        if ceiling is None:
            # This process now runs the target version (or was already on it), so the
            # dependencies it uses are ones that version is known to start with. Older
            # sources declare open-ended dependencies that newer releases break.
            ceiling = upgrade_executor._dependency_ceiling()
        _notice(f"Syncing another Observal CLI at {other.path} to {target}...")
        if not _install_other(other, specs, ceiling, target):
            _report_copy(other.path, target, "installing it did not produce a copy that starts")


def _copy_works(path, target: str) -> bool:
    from observal_cli import install_detector, version_check

    return version_check._same_version(install_detector.installed_version(path), target) and not _start_failure_at(path)


def _report_copy(path, target: str, reason: str) -> None:
    """A copy left broken or on the wrong version. A warning, so JSON mode reports it too, on stderr."""
    from observal_cli.errors import emit_warning

    emit_warning(
        "cli_copy_not_synced",
        f"Another Observal CLI at {path} is not working on version {target}: {reason.rstrip('.')}.",
        operation="Sync other Observal CLI installations",
        remediation=f"Remove {path}, or reinstall it as version {target} with the tool that installed it.",
    )


def _install_other(other, specs: list[str], ceiling, target: str) -> bool:
    """Install ``target`` into another copy: each source capped, then uncapped, until one starts."""
    from observal_cli import upgrade_executor

    for spec in specs:
        argv = upgrade_executor.package_install_argv(other.info, spec, other.interpreter)
        attempts: list[tuple[list[str], dict | None]] = []
        if ceiling is not None and (capped := upgrade_executor._with_ceiling(argv, ceiling)) is not None:
            attempts.append(capped)
        attempts.append((argv, None))
        for attempt, environment in attempts:
            if not _run_install(attempt, environment):
                continue
            if _copy_works(other.path, target):
                return True
    return False


def _run_install(argv: list[str], environment: dict | None = None) -> bool:
    from observal_cli import upgrade_executor

    try:
        return upgrade_executor.run_installer(argv, timeout=300, env=environment).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _rerun(install, target: str) -> None:
    """Run the current command again on the freshly installed CLI. Never returns."""
    from observal_cli.install_detector import is_python_interpreter
    from observal_cli.shared import launcher

    arguments = sys.argv[1:]
    if getattr(sys, "frozen", False) or not is_python_interpreter(install.path):
        # The installed executable, never an `observal_cli` from an inherited PYTHONPATH;
        # a parent started with `python -I <script>` passes that isolation on.
        argv, environment = [str(install.path), *arguments], launcher.script_env()
    else:
        # No entry point to run (the CLI was started as a module): launch isolated,
        # so the working directory cannot supply its own `observal_cli`.
        argv, environment = launcher.module_subprocess("observal_cli.main", *arguments)
    environment[SYNC_ENV] = target
    sys.stdout.flush()
    sys.stderr.flush()
    if os.name != "nt":
        os.execve(argv[0], argv, environment)
    # Windows has no exec: run the new CLI as a child and leave with its status.
    # This process still holds the old code, so it must not report anything more.
    os.environ["OBSERVAL_NO_UPDATE_CHECK"] = "1"
    raise typer.Exit(subprocess.run(argv, env=environment).returncode)
