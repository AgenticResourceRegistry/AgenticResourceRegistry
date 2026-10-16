# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""A real CLI pull cannot use startup mode to overwrite a dirty Pi profile."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

AGENT = "11111111-1111-4111-8111-111111111111"


@pytest.mark.parametrize("changed_mcp", [False, True])
def test_normal_pull_startup_guard_uses_existing_owned_files(tmp_path: Path, changed_mcp: bool) -> None:
    release_components: list[dict] = []
    generated_components: list[dict] = []
    manual_started = threading.Event()
    manual_release = threading.Event()

    class Registry(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

        def respond(self, data: dict) -> None:
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/api/v1/config/version":
                return self.respond({"server_version": "dev"})
            if self.path == f"/api/v1/agents/{AGENT}":
                return self.respond(
                    {
                        "id": AGENT,
                        "name": "reviewer",
                        "namespace": "alice",
                        "slug": "reviewer",
                        "version": "2.0.0",
                        "latest_approved_version": "2.0.0",
                        "component_links": [],
                    }
                )
            if self.path == f"/api/v1/agents/{AGENT}/versions/2.0.0":
                return self.respond(
                    {
                        "version": "2.0.0",
                        "status": "approved",
                        "supported_harnesses": ["pi"],
                        "components": release_components,
                        "description": "Updated reviewer",
                    }
                )
            if self.path == f"/api/v1/agents/{AGENT}/versions/1.0.0":
                return self.respond(
                    {"version": "1.0.0", "status": "approved", "supported_harnesses": ["pi"], "components": []}
                )
            self.send_error(404)

        def do_POST(self) -> None:
            if self.path == f"/api/v1/agents/{AGENT}/install":
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                assert request["version"] in {"1.0.0", "2.0.0"} and request["strict"] is True
                if request["version"] == "1.0.0":
                    manual_started.set()
                    assert manual_release.wait(timeout=30)
                return self.respond(
                    {
                        "version": request["version"],
                        "config_snippet": {
                            "agent_profile": {
                                "path": "~/.pi/agent/agents/reviewer/AGENTS.md",
                                "content": "old profile" if request["version"] == "1.0.0" else "new profile",
                            },
                            "mcp_config": {
                                "path": "~/.pi/agent/agents/reviewer/mcp.json",
                                "content": {
                                    "mcpServers": {
                                        "observal-agents": {
                                            "command": "/tmp/updated"
                                            if changed_mcp and request["version"] == "2.0.0"
                                            else "/tmp/python",
                                            "args": [],
                                        }
                                    }
                                },
                            },
                        },
                        "lock": {
                            "status": "locked",
                            "digest": "old-digest" if request["version"] == "1.0.0" else "new-digest",
                            "components": generated_components if request["version"] == "2.0.0" else [],
                            "problems": [],
                        },
                    }
                )
            if self.path == "/api/v1/layer-snapshots":
                return self.respond({"hash": "test"})
            self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Registry)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    profile = home / ".pi/agent/agents/reviewer/AGENTS.md"
    profile.parent.mkdir(parents=True)
    profile.write_text("old profile")
    mcp = profile.with_name("mcp.json")
    mcp.write_text(
        json.dumps({"mcpServers": {"observal-agents": {"command": "/tmp/python", "args": []}}}, indent=2) + "\n"
    )
    registry = f"http://127.0.0.1:{server.server_port}"
    config = home / ".observal/config.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"server_url": registry, "access_token": "test-token", "user_id": "alice"}))
    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "PYTHONPATH": os.pathsep.join(
            (
                str(Path(__file__).resolve().parents[1]),
                str(Path(__file__).resolve().parents[1] / "packages/observal-shared"),
            )
        ),
        "OBSERVAL_AUTO_UPDATE_INSTALL": "1",
        "OBSERVAL_AUTO_UPDATE_EXPECTED_VERSION": "1.0.0",
    }
    bootstrap = f"""
from observal_cli import lockfile, install_baseline
lockfile.upsert_agent('pi', name='reviewer', agent_id={AGENT!r}, version='1.0.0', scope='user',
    directory={str(project)!r}, components=[], namespace='alice', slug='reviewer',
    local_name='reviewer', lock_status='locked', lock_digest='old-digest', pin_known=True)
install_baseline.capture(registry={registry!r}, harness='pi', agent_id={AGENT!r}, scope='user',
    root={str(project)!r}, version='1.0.0', lock_digest='old-digest', written_paths=[{str(profile)!r}, {str(mcp)!r}])
"""
    argv = [
        sys.executable,
        "-m",
        "observal_cli",
        "agent",
        "pull",
        AGENT,
        "--harness",
        "pi",
        "--scope",
        "user",
        "--dir",
        str(project),
        "--version",
        "2.0.0",
        "--strict",
        "--no-prompt",
        "--output",
        "json",
    ]
    try:
        subprocess.run([sys.executable, "-c", bootstrap], env=env, cwd=project, check=True, capture_output=True)
        profile.write_text("local edit")
        refused = subprocess.run(argv, cwd=project, env=env, capture_output=True, text=True)
        assert refused.returncode != 0 and profile.read_text() == "local edit", refused.stdout
        profile.write_text("old profile")
        original_mcp = mcp.read_bytes()
        mcp.write_text('{"mcpServers":{"observal-agents":{"command":"foreign"}}}\n')
        refused_mcp = subprocess.run(argv, cwd=project, env=env, capture_output=True, text=True)
        assert refused_mcp.returncode != 0 and mcp.read_text().endswith('"foreign"}}}\n')
        assert profile.read_text() == "old profile"
        mcp.write_bytes(original_mcp)
        # Keep the *same* component identity as the approved target. This is
        # eligible at parent preflight; only the generated /install lock drifts.
        seed_identity = f"""
from observal_cli import lockfile
lockfile.upsert_agent('pi', name='reviewer', agent_id={AGENT!r}, version='1.0.0', scope='user',
    directory={str(project)!r}, components=[{{'type': 'skill', 'id': 'skill-1', 'version': '1.0.0'}}],
    namespace='alice', slug='reviewer', local_name='reviewer', lock_status='locked',
    lock_digest='old-digest', pin_known=True)
"""
        subprocess.run([sys.executable, "-c", seed_identity], cwd=project, env=env, check=True, capture_output=True)
        release_components[:] = [
            {"component_type": "skill", "component_id": "skill-1", "resolved_version": "2.0.0", "name": "review"}
        ]
        for generated in (
            {"type": "skill", "id": "skill-1", "version": "3.0.0"},
            {"type": "skill", "id": "skill-2", "version": "2.0.0"},
        ):
            generated_components[:] = [generated]
            mismatch = subprocess.run(argv, cwd=project, env=env, capture_output=True, text=True)
            assert mismatch.returncode != 0, mismatch.stdout + mismatch.stderr
            assert profile.read_text() == "old profile", "mismatched generated pins wrote the profile"
        release_components.clear()
        generated_components.clear()
        reset_identity = f"""
from observal_cli import lockfile
lockfile.upsert_agent('pi', name='reviewer', agent_id={AGENT!r}, version='1.0.0', scope='user',
    directory={str(project)!r}, components=[], namespace='alice', slug='reviewer',
    local_name='reviewer', lock_status='locked', lock_digest='old-digest', pin_known=True)
"""
        subprocess.run([sys.executable, "-c", reset_identity], cwd=project, env=env, check=True, capture_output=True)
        injection = tmp_path / "assert-no-alarm-during-write"
        injection.mkdir()
        child_ready = tmp_path / "auto-child-started"
        env["OBSERVAL_TEST_STARTUP_CHILD_READY"] = str(child_ready)
        (injection / "sitecustomize.py").write_text(
            "import os, sys\n"
            "from pathlib import Path\n"
            "if 'agent' in sys.argv and 'pull' in sys.argv:\n"
            "    if os.environ.get('OBSERVAL_AUTO_UPDATE_INSTALL') == '1':\n"
            "        Path(os.environ['OBSERVAL_TEST_STARTUP_CHILD_READY']).write_text('waiting for Pi lock')\n"
            "    from observal_cli import client, cmd_pull\n"
            "    original = cmd_pull.write_install_snippet\n"
            "    def checked(*args, **kwargs):\n"
            "        assert client._NETWORK_CUTOFF.get() is None, 'network alarm active during write'\n"
            "        return original(*args, **kwargs)\n"
            "    cmd_pull.write_install_snippet = checked\n"
        )
        env["PYTHONPATH"] = f"{injection}{os.pathsep}{env['PYTHONPATH']}"
        startup = f"""
import json, time
from pathlib import Path
from observal_cli import auto_update_policy as policy, client, cmd_update
policy.set_policy({registry!r}, enabled=True)
item = cmd_update._entries('pi')[0]
deadline = time.monotonic() + 90
with client.bounded_requests(deadline - 15):
    print(json.dumps(cmd_update.apply_startup_pi_agent({{**item, 'latest_version': '2.0.0'}},
        registry={registry!r}, account='alice', deadline=deadline,
        shutdown_requested=lambda: False, marker=Path({str(home / "no-shutdown-marker")!r}))))
"""
        # A real manual pull owns Pi's install lock while its registry response
        # is delayed. The startup runner has already checked an unpinned entry
        # when it spawns the child, which must wait for that same lock. Manual
        # --version pins the *same* installed version before releasing it.
        explicit_env = {k: v for k, v in env.items() if not k.startswith("OBSERVAL_AUTO_UPDATE_")}
        manual_argv = argv.copy()
        manual_argv[manual_argv.index("2.0.0")] = "1.0.0"
        manual_pin = subprocess.Popen(
            manual_argv, cwd=project, env=explicit_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        auto = None
        try:
            if not manual_started.wait(timeout=15):
                if manual_pin.poll() is not None:
                    stdout, stderr = manual_pin.communicate(timeout=2)
                    raise AssertionError(f"manual pull exited before acquiring Pi lock: {stdout} {stderr}")
                raise AssertionError("manual pull did not reach the registry while waiting for Pi lock")
            auto = subprocess.Popen(
                [sys.executable, "-c", startup],
                cwd=project,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(300):
                if child_ready.exists():
                    break
                time.sleep(0.05)
            assert child_ready.exists(), "automatic child was not admitted before the manual pin"
        except BaseException:
            manual_release.set()
            if auto is not None:
                auto.terminate()
                auto.communicate(timeout=10)
            manual_pin.communicate(timeout=10)
            raise
        finally:
            manual_release.set()
        manual_out, manual_err = manual_pin.communicate(timeout=10)
        assert manual_pin.returncode == 0, manual_out + manual_err
        assert auto is not None
        auto_out, auto_err = auto.communicate(timeout=10)
        assert auto.returncode == 0, auto_out + auto_err
        assert json.loads(auto_out)["status"] == "skipped", auto_out + auto_err
        assert "manual version pin" in json.loads(auto_out)["reason"]
        assert profile.read_text() == "old profile", "the automatic child overrode a concurrent manual pin"
        pinned = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json; from observal_cli.lockfile import get_all_entries; "
                "print(json.dumps(get_all_entries('pi')))",
            ],
            cwd=project,
            env=explicit_env,
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(pinned.stdout)[0]["requested_version"] == "1.0.0"
        # Test-only reconciliation: restore unpinned metadata for the success
        # path below, after verifying the manual pin won this interleaving.
        unpin = f"""
from observal_cli import lockfile
lockfile.upsert_agent('pi', name='reviewer', agent_id={AGENT!r}, version='1.0.0', scope='user',
    directory={str(project)!r}, components=[], namespace='alice', slug='reviewer',
    local_name='reviewer', lock_status='locked', lock_digest='old-digest', pin_known=True)
"""
        subprocess.run([sys.executable, "-c", unpin], cwd=project, env=explicit_env, check=True, capture_output=True)
        mcp_inode = mcp.stat().st_ino
        installed = subprocess.run(
            [sys.executable, "-c", startup], cwd=project, env=env, capture_output=True, text=True
        )
        assert installed.returncode == 0, installed.stdout + installed.stderr
        assert json.loads(installed.stdout)["status"] == "updated", installed.stdout + installed.stderr
        assert profile.read_text() == "new profile"
        if changed_mcp:
            assert json.loads(mcp.read_text())["mcpServers"]["observal-agents"]["command"] == "/tmp/updated"
            assert mcp.stat().st_ino != mcp_inode, "the changed owned reference was not replaced"
        else:
            assert mcp.stat().st_ino == mcp_inode, "automatic pull rewrote an unchanged delegation MCP config"
        proof = f"""
from observal_cli import install_baseline
import json
print(json.dumps(install_baseline.verified_files(registry={registry!r}, harness='pi', agent_id={AGENT!r},
    scope='user', root={str(project)!r}, version='2.0.0', lock_digest='new-digest')))
"""
        verified = subprocess.run(
            [sys.executable, "-c", proof], cwd=project, env=env, check=True, capture_output=True, text=True
        )
        assert str(profile) in json.loads(verified.stdout)
        explicit_env = {k: v for k, v in env.items() if not k.startswith("OBSERVAL_AUTO_UPDATE_")}
        batch = subprocess.run(
            argv,
            cwd=project,
            env={**explicit_env, "OBSERVAL_UPDATE_EXACT_TARGET": "1"},
            capture_output=True,
            text=True,
        )
        assert batch.returncode == 0, batch.stdout + batch.stderr
        unpinned = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json; from observal_cli.lockfile import get_all_entries; "
                "print(json.dumps(get_all_entries('pi')))",
            ],
            cwd=project,
            env=explicit_env,
            check=True,
            capture_output=True,
            text=True,
        )
        assert "requested_version" not in json.loads(unpinned.stdout)[0]
        manual = subprocess.run(argv, cwd=project, env=explicit_env, capture_output=True, text=True)
        assert manual.returncode == 0, manual.stdout + manual.stderr
        pin = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json; from observal_cli.lockfile import get_all_entries; "
                "print(json.dumps(get_all_entries('pi')))",
            ],
            cwd=project,
            env=explicit_env,
            check=True,
            capture_output=True,
            text=True,
        )
        row = json.loads(pin.stdout)[0]
        assert row["requested_version"] == "2.0.0" and row["pin_known"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
