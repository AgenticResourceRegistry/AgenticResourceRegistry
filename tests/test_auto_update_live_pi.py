# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Opt-in, isolated real-process Pi RPC + CLI apply smoke test.

Run: OBSERVAL_RUN_LIVE_PI=1 PYTHONPATH=. .venv/bin/pytest tests/test_auto_update_live_pi.py -v
No Docker, cloud credentials, provider key, or real user home is touched.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

AGENT = "11111111-1111-4111-8111-111111111111"
EXTENSION = Path(__file__).resolve().parents[1] / "packages/pi-extension/extensions/observal.ts"
CLI = Path(sys.executable).parent / "observal"


class Registry(str):
    def __new__(cls, value: str) -> Registry:
        result = str.__new__(cls, value)
        result.pause = False
        result.latest = "1.0.0"
        result.unsafe = False
        result.retry_after = False
        result.trickle = False
        result.install_calls = 0
        result.snapshots = []
        result.started = threading.Event()
        result.release = threading.Event()
        return result


@pytest.fixture()
def registry() -> Iterator[Registry]:
    control: Registry | None = None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

        def send(self, value: dict, code: int = 200) -> None:
            body = json.dumps(value).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/api/v1/config/version":
                return self.send({"server_version": "dev"})
            if self.path == f"/api/v1/agents/{AGENT}":
                if control is not None and control.trickle:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", "1000")
                    self.end_headers()
                    try:
                        for _ in range(200):
                            self.wfile.write(b" ")
                            self.wfile.flush()
                            time.sleep(0.12)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                if control is not None and control.retry_after:
                    self.send_response(503)
                    self.send_header("Retry-After", "3600")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                return self.send(
                    {
                        "id": AGENT,
                        "name": "reviewer",
                        "latest_approved_version": control.latest,
                        "namespace": "alice",
                        "slug": "reviewer",
                    }
                )
            if self.path == f"/api/v1/agents/{AGENT}/versions/1.0.0":
                return self.send(
                    {"version": "1.0.0", "status": "approved", "supported_harnesses": ["pi"], "components": []}
                )
            if self.path == f"/api/v1/agents/{AGENT}/versions/2.0.0":
                return self.send(
                    {
                        "version": "2.0.0",
                        "status": "approved",
                        "supported_harnesses": ["pi"],
                        "description": "Author release notes",
                        "components": [],
                    }
                )
            self.send({}, 404)

        def do_POST(self) -> None:
            if self.path == "/api/v1/layer-snapshots":
                snapshot = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if control is not None:
                    control.snapshots.append(snapshot)
                return self.send({"hash": snapshot["hash"]})
            if self.path == f"/api/v1/agents/{AGENT}/install":
                content = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                # Initial --upgrade selects the latest approved release, so
                # normal pull omits the explicit version in the POST body.
                requested = content.get("version") or control.latest
                assert requested in {"1.0.0", "2.0.0"} and content["strict"] is True
                if requested == "1.0.0":
                    return self.send(
                        {
                            "agent_id": AGENT,
                            "harness": "pi",
                            "version": "1.0.0",
                            "config_snippet": {
                                "agent_profile": {
                                    "path": "~/.pi/agent/agents/reviewer/AGENTS.md",
                                    "content": "old profile",
                                }
                            },
                            "lock": {"status": "locked", "digest": "old-digest", "components": [], "problems": []},
                        }
                    )
                if control is not None:
                    control.install_calls += 1
                if control is not None and control.pause:
                    control.started.set()
                    control.release.wait(timeout=10)
                if control is not None and control.unsafe:
                    return self.send(
                        {"agent_id": AGENT, "harness": "pi", "version": "2.0.0", "warnings": ["review required"]}
                    )
                return self.send(
                    {
                        "agent_id": AGENT,
                        "harness": "pi",
                        "version": "2.0.0",
                        "config_snippet": {
                            "agent_profile": {"path": "~/.pi/agent/agents/reviewer/AGENTS.md", "content": "new profile"}
                        },
                        "lock": {"status": "locked", "digest": "new-digest", "components": [], "problems": []},
                    }
                )
            self.send({}, 404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    control = Registry(f"http://127.0.0.1:{server.server_port}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield control
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _tui_notice(home: Path, env: dict, *, expected: str) -> None:
    """Exercise Pi's actual terminal renderer, not a mocked ctx.ui.notify."""
    import fcntl
    import pty
    import struct
    import termios

    pi = shutil.which("pi")
    assert pi
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    proc = subprocess.Popen(
        [
            pi,
            "--offline",
            "--no-skills",
            "--no-context-files",
            "--no-extensions",
            "--extension",
            str(EXTENSION),
            "--tui-mode",
            "regular",
        ],
        cwd=home,
        env={**env, "TERM": "xterm-256color"},
        stdin=slave,
        stdout=slave,
        stderr=slave,
        start_new_session=True,
    )
    os.close(slave)
    output = bytearray()
    try:
        until = time.monotonic() + 12
        while time.monotonic() < until:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    output.extend(os.read(master, 65536))
                except OSError:
                    break
                if expected.encode() in output:
                    return
        pytest.fail(f"Pi TUI did not render {expected!r}: {bytes(output[-500:])!r}")
    finally:
        if proc.poll() is None:
            try:
                os.write(master, b"\x03")
            except OSError:
                pass  # Pi may close the PTY between poll and input.
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
        os.close(master)


def _tui_reselect(home: Path, env: dict, *, name: str = "reviewer") -> bytes:
    """Use the real Pi terminal command/confirm flow after a saved-profile update."""
    import fcntl
    import pty
    import struct
    import termios

    pi = shutil.which("pi")
    assert pi
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    proc = subprocess.Popen(
        [
            pi,
            "--offline",
            "--no-skills",
            "--no-context-files",
            "--no-extensions",
            "--extension",
            str(EXTENSION),
            "--tui-mode",
            "regular",
        ],
        cwd=home,
        env={**env, "TERM": "xterm-256color"},
        stdin=slave,
        stdout=slave,
        stderr=slave,
        start_new_session=True,
    )
    os.close(slave)
    output = bytearray()

    def until_text(expected: bytes, seconds: float = 12, start: int = 0) -> None:
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    output.extend(os.read(master, 65536))
                except OSError:
                    break
                if expected in output[start:]:
                    return
        pytest.fail(f"Pi TUI did not show {expected!r}: {bytes(output[-1200:])!r}")

    try:
        until_text(b"observal")  # Wait for Pi's initialized Observal status.
        time.sleep(0.5)
        os.write(master, f"/agent {name}\r".encode())
        until_text(b"Reload session now?")
        start = len(output)
        os.write(master, b"\r")  # Confirm's default selection is Yes; it is not a y/n text prompt.
        until_text(b"Reloaded keybindings", seconds=8, start=start)
        return bytes(output)
    finally:
        if proc.poll() is None:
            try:
                os.write(master, b"\x03")
            except OSError:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
        os.close(master)


def _rpc_session(home: Path, env: dict, *, expected: str, also_expected: str | None = None) -> list[str]:
    pi = shutil.which("pi")
    assert pi
    proc = subprocess.Popen(
        [
            pi,
            "--mode",
            "rpc",
            "--no-session",
            "--offline",
            "--no-skills",
            "--no-context-files",
            "--no-extensions",
            "--extension",
            str(EXTENSION),
        ],
        cwd=home,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin and proc.stdout and proc.stderr
    lines: queue.Queue[dict] = queue.Queue()

    def collect() -> None:
        for row in proc.stdout:
            lines.put(json.loads(row))

    reader = threading.Thread(target=collect, daemon=True)
    reader.start()
    messages = []
    try:
        until = time.monotonic() + 15
        while time.monotonic() < until:
            try:
                record = lines.get(timeout=0.3)
            except queue.Empty:
                continue
            if record.get("type") == "extension_ui_request" and record.get("method") == "notify":
                messages.append(record["message"])
                if any(expected in message for message in messages) and (
                    also_expected is None or any(also_expected in message for message in messages)
                ):
                    break
        else:
            pytest.fail(f"Pi RPC did not deliver {expected!r}: {messages!r}")
    finally:
        proc.stdin.close()  # Orderly RPC shutdown writes the session marker.
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        reader.join(timeout=2)
    assert proc.returncode == 0, proc.stderr.read().decode()[:1000]
    return messages


@pytest.mark.skipif(os.getenv("OBSERVAL_RUN_LIVE_PI") != "1", reason="explicit live Pi/RPC opt-in")
def test_frozen_then_real_apply_in_isolated_pi_rpc(tmp_path: Path, registry: Registry) -> None:
    if not shutil.which("pi") or not CLI.exists():
        pytest.skip("Requires installed Pi and editable Observal CLI")
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    profile = home / ".pi/agent/agents/reviewer/AGENTS.md"
    config = home / ".observal/config.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"server_url": registry, "access_token": "local-token", "user_id": "alice"}))
    repo = Path(__file__).resolve().parents[1]
    python_path = os.pathsep.join([str(repo), str(repo / "packages/observal-shared")])
    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "PYTHONPATH": python_path,
    }
    # Seed the disposable profile through a real manual CLI pull, not a
    # fabricated lock/baseline. --upgrade selects the approved v1 unpinned.
    pull = [
        str(CLI),
        "agent",
        "pull",
        AGENT,
        "--harness",
        "pi",
        "--scope",
        "user",
        "--dir",
        str(root),
        "--upgrade",
        "--strict",
        "--no-prompt",
        "--output",
        "json",
    ]
    seeded = subprocess.run(pull, cwd=root, env=env, capture_output=True, text=True)
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    assert profile.read_text() == "old profile"
    proof = (
        "import json; from observal_cli import lockfile, install_baseline; "
        "print(json.dumps({'entry': lockfile.get_all_entries('pi')[0], "
        f"'files': install_baseline.verified_files(registry={str(registry)!r}, harness='pi', "
        f"agent_id={AGENT!r}, scope='user', root={str(root)!r}, version='1.0.0', "
        "lock_digest='old-digest')}))"
    )
    baseline = json.loads(
        subprocess.run(
            [sys.executable, "-c", proof], cwd=root, env=env, check=True, capture_output=True, text=True
        ).stdout
    )
    assert baseline["entry"]["pin_known"] is True and not baseline["entry"].get("requested_version")
    assert str(profile) in baseline["files"]
    registry.latest = "2.0.0"
    # Real Pi RPC bridge starts a detached worker; only account-scoped
    # `observal unfreeze` authorizes it to change files.
    env["OBSERVAL_CLI_BIN"] = str(CLI)

    messages = _rpc_session(home, env, expected="update available")
    assert "Author release notes" in "\n".join(messages)
    assert profile.read_text() == "old profile", "frozen startup must not install"
    if os.name != "nt":
        _tui_notice(home, env, expected="Author release notes")
        assert profile.read_text() == "old profile", "TUI notice must not install while frozen"
    # An explicit same-version manual pull records a pin. The real Pi bridge
    # must notice, but never replace, the pinned installed version.
    pinned = [arg for arg in pull if arg != "--upgrade"] + ["--version", "1.0.0"]
    pin_result = subprocess.run(pinned, cwd=root, env=env, capture_output=True, text=True)
    assert pin_result.returncode == 0, pin_result.stdout + pin_result.stderr
    subprocess.run([str(CLI), "unfreeze"], cwd=home, env=env, check=True, capture_output=True)
    calls = registry.install_calls
    pinned_messages = _rpc_session(home, env, expected="automatic update skipped")
    assert any("pin" in message.lower() for message in pinned_messages), pinned_messages
    assert profile.read_text() == "old profile" and registry.install_calls == calls
    registry.latest = "1.0.0"
    unpin_result = subprocess.run(pull, cwd=root, env=env, capture_output=True, text=True)
    assert unpin_result.returncode == 0, unpin_result.stdout + unpin_result.stderr
    registry.latest = "2.0.0"
    subprocess.run([str(CLI), "freeze"], cwd=home, env=env, check=True, capture_output=True)
    frozen_again = _rpc_session(home, env, expected="update available")
    assert frozen_again and profile.read_text() == "old profile"
    subprocess.run([str(CLI), "unfreeze"], cwd=home, env=env, check=True, capture_output=True)
    profile.write_text("local edit")
    messages = _rpc_session(home, env, expected="automatic update skipped")
    assert profile.read_text() == "local edit", "dirty managed files must not be overwritten"
    profile.write_text("old profile")  # Explicitly restore known baseline for this isolated test.
    registry.retry_after = True
    started = time.monotonic()
    messages = _rpc_session(home, env, expected="automatic update skipped")
    assert time.monotonic() - started < 15, "a long Retry-After must not park the apply worker"
    assert profile.read_text() == "old profile"
    registry.retry_after = False
    if os.name != "nt":
        # A peer can drip body bytes faster than httpx's inactivity timeout.
        # Shrink only this isolated worker's admission budget through a
        # sitecustomize test hook; ordinary users cannot alter the cutoff.
        injection = home / "short-budget"
        injection.mkdir()
        (injection / "sitecustomize.py").write_text(
            "import sys\n"
            "if '_startup-apply' in sys.argv:\n"
            "    from observal_cli import startup_update_apply as worker\n"
            "    worker.APPLY_SECONDS = 2.5\n"
            "    worker.RECOVERY_RESERVE_SECONDS = 1.0\n"
        )
        env["PYTHONPATH"] = f"{injection}{os.pathsep}{env['PYTHONPATH']}"
        registry.trickle = True
        calls = registry.install_calls
        started = time.monotonic()
        try:
            _rpc_session(home, env, expected="automatic update skipped")
        finally:
            registry.trickle = False
            env["PYTHONPATH"] = python_path
        assert time.monotonic() - started < 8, "trickling registry must not hold the apply gate indefinitely"
        assert registry.install_calls == calls and profile.read_text() == "old profile"

    # Pause the real registry's install response, close Pi while the worker is
    # waiting, then release it. Pi must exit without killing the worker; the
    # marker must prevent any new file replacement after the response arrives.
    registry.pause = True
    pi = shutil.which("pi")
    assert pi
    departed = subprocess.Popen(
        [
            pi,
            "--mode",
            "rpc",
            "--no-session",
            "--offline",
            "--no-skills",
            "--no-context-files",
            "--no-extensions",
            "--extension",
            str(EXTENSION),
        ],
        cwd=home,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        assert registry.started.wait(10), "apply worker did not reach registry install"
        assert departed.stdin
        departed.stdin.close()
        assert departed.wait(timeout=4) == 0, "Pi waited for an in-flight install"
    finally:
        registry.release.set()
        registry.pause = False
        if departed.poll() is None:
            departed.kill()
            departed.wait(timeout=5)
    notices = home / ".observal/update-notices"
    until = time.monotonic() + 10
    while time.monotonic() < until:
        rows = [json.loads(file.read_text()) for file in notices.glob("*.json")]
        if any(item.get("status") == "skipped" for row in rows for item in row["items"]):
            break
        time.sleep(0.1)
    else:
        pytest.fail("departed worker did not persist its shutdown result")
    assert profile.read_text() == "old profile"

    registry.unsafe = True
    messages = _rpc_session(home, env, expected="automatic update skipped")
    assert any("previous Pi session" in message and "automatic update skipped" in message for message in messages)
    assert profile.read_text() == "old profile", "unsafe install payload cannot change profile files"
    registry.unsafe = False
    if os.name != "nt":
        # Kill a real apply worker after it has reserved its journal but before
        # the delayed install response. The next session must replay uncertainty
        # and refuse another install until the journal is manually reconciled.
        pidfile = home / "worker-pid"
        wrapper = home / "test-worker"
        wrapper.write_text(f"#!/bin/sh\necho $$ > '{pidfile}'\nexec '{CLI}' \"$@\"\n")
        wrapper.chmod(0o700)
        env["OBSERVAL_CLI_BIN"] = str(wrapper)
        registry.started.clear()
        registry.release.clear()
        registry.pause = True
        crashed = subprocess.Popen(
            [
                pi,
                "--mode",
                "rpc",
                "--no-session",
                "--offline",
                "--no-skills",
                "--no-context-files",
                "--no-extensions",
                "--extension",
                str(EXTENSION),
            ],
            cwd=home,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            assert registry.started.wait(10), "apply worker did not reach delayed response"
            until = time.monotonic() + 5
            while not pidfile.exists() and time.monotonic() < until:
                time.sleep(0.05)
            assert pidfile.exists()
            os.kill(int(pidfile.read_text().strip()), signal.SIGKILL)
            assert crashed.stdin
            crashed.stdin.close()
            assert crashed.wait(timeout=5) == 0
        finally:
            registry.release.set()
            registry.pause = False
            if crashed.poll() is None:
                crashed.kill()
                crashed.wait(timeout=5)
            env["OBSERVAL_CLI_BIN"] = str(CLI)
        journals = list(notices.glob("*.pending"))
        assert len(journals) == 1 and profile.read_text() == "old profile"
        calls = registry.install_calls
        messages = _rpc_session(home, env, expected="automatic update skipped")
        assert any("outcome pending from a Pi session" in message for message in messages)
        assert registry.install_calls == calls, "unresolved outcome must block later Pi installs"
        assert profile.read_text() == "old profile"
        assert (
            json.loads((home / ".observal/lockfile.json").read_text())["registries"][registry]["harnesses"]["pi"][
                "agents"
            ][0]["version"]
            == "1.0.0"
        )
        journals[0].unlink()  # Test-only manual reconciliation after verifying bytes and installed version.

        # A known partial write can be restored from durable pre-write bytes
        # after the child dies. Neither a successful install nor a pending
        # unknown outcome may be shown when originals verify again.
        injection = home / "crash-injection"
        injection.mkdir()
        (injection / "sitecustomize.py").write_text(
            "import os, signal, sys\n"
            "if 'agent' in sys.argv and 'pull' in sys.argv:\n"
            "    original = os.replace\n"
            "    def crash_after_replace(src, dst):\n"
            "        original(src, dst)\n"
            "        if str(dst).endswith('/AGENTS.md'):\n"
            "            os.kill(os.getpid(), signal.SIGKILL)\n"
            "    os.replace = crash_after_replace\n"
        )
        env["PYTHONPATH"] = f"{injection}{os.pathsep}{env['PYTHONPATH']}"
        restored = _rpc_session(home, env, expected="verified original managed files were restored")
        assert any("automatic update skipped" in message for message in restored)
        assert profile.read_text() == "old profile"
        assert not list(notices.glob("*.pending"))
        assert not list((home / ".observal/update-backups").glob("*/manifest.json"))

        # If a foreign edit appears after the replacement, its bytes match
        # neither the old nor planned version. Never overwrite it: retain the
        # original backup and pending record for explicit manual repair.
        (injection / "sitecustomize.py").write_text(
            "import os, signal, sys\n"
            "from pathlib import Path\n"
            "if 'agent' in sys.argv and 'pull' in sys.argv:\n"
            "    original = os.replace\n"
            "    def edit_after_replace(src, dst):\n"
            "        original(src, dst)\n"
            "        if str(dst).endswith('/AGENTS.md'):\n"
            "            Path(dst).write_text('foreign edit')\n"
            "            os.kill(os.getpid(), signal.SIGKILL)\n"
            "    os.replace = edit_after_replace\n"
        )
        _rpc_session(home, env, expected="outcome pending from a Pi session")
        env["PYTHONPATH"] = python_path
        journals = list(notices.glob("*.pending"))
        assert len(journals) == 1 and profile.read_text() == "foreign edit"
        pending = json.loads(journals[0].read_text())
        backup = Path(pending["backup_dir"])
        assert (backup / "manifest.json").is_file()
        original_bytes = (backup / "0").read_bytes()
        assert original_bytes == b"old profile"
        assert not (notices / f"{journals[0].stem}.complete").exists()
        state = json.loads((home / ".observal/lockfile.json").read_text())
        assert state["registries"][registry]["harnesses"]["pi"]["agents"][0]["version"] == "1.0.0"
        calls = registry.install_calls
        _rpc_session(home, env, expected="outcome pending from a Pi session")
        assert registry.install_calls == calls
        profile.write_bytes(original_bytes)  # Test-only manual repair after inspecting the lock and backup.
        journals[0].unlink()
        shutil.rmtree(backup)
    active = home / ".pi/agent/AGENTS.md"
    active.write_text("old profile")  # Saved update must not silently replace the active copy.
    # Pre-release Pi check caches "current" for 24h. The next opted-in
    # startup must still discover and apply a newly approved release without
    # asking the user to run `outdated` to invalidate that check-only cache.
    cache_dir = home / ".observal/update-check-cache"
    for cached in cache_dir.glob("*.json"):
        cached.unlink()
    registry.latest = "1.0.0"
    session_before_release = "check-before-new-release"
    key = hashlib.sha256(f"{registry}\0alice\0{session_before_release}".encode()).hexdigest()
    seeded_check = subprocess.run(
        [str(CLI), "_startup-check", "--cwd", str(root), "--session-id", session_before_release, "--notice-key", key],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert seeded_check.returncode == 0, seeded_check.stdout + seeded_check.stderr
    assert json.loads(next(cache_dir.glob("*.json")).read_text())[0]["latest_version"] == "1.0.0"
    registry.latest = "2.0.0"
    messages = _rpc_session(home, env, expected="installed on disk")
    assert "Re-select" in "\n".join(messages) or "re-select" in "\n".join(messages)
    assert profile.read_text() == "new profile"
    state = json.loads((home / ".observal/lockfile.json").read_text())
    assert state["registries"][registry]["harnesses"]["pi"]["agents"][0]["version"] == "2.0.0"
    assert active.read_text() == "old profile", "saved-profile update is not active until /agent re-selection"
    assert json.loads(config.read_text())["active_agent"]["version"] == "1.0.0"
    if os.name != "nt":
        _tui_reselect(home, env)
        assert active.read_text() == "new profile"
        selected = json.loads(config.read_text())["active_agent"]
        assert selected["id"] == AGENT and selected["version"] == "2.0.0"
        assert any(
            item["path"] == "user:AGENTS.md" and item.get("content") == "new profile"
            for snapshot in registry.snapshots
            for item in snapshot["harnesses"]["pi"]
        ), "the re-selected active layer must reflect the new profile"
