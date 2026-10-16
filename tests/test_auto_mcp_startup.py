# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Disposable Pi managed MCP references: explicit ownership and gated startup."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

MCPS = ("22222222-2222-4222-8222-222222222222", "33333333-3333-4333-8333-333333333333")


@pytest.fixture()
def instance(tmp_path: Path):
    state = {"latest": "1.0.0", "credentials": False, "changed_key": False, "fixed_command": False}

    class Registry(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

        def respond(self, data: dict) -> None:
            raw = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if self.path == "/api/v1/config/version":
                return self.respond({"server_version": "dev"})
            for index, mcp_id in enumerate(MCPS):
                name = ("search", "browse")[index]
                if self.path == f"/api/v1/mcps/{mcp_id}":
                    return self.respond(
                        {
                            "id": mcp_id,
                            "namespace": "alice",
                            "slug": name,
                            "name": name,
                            "version": state["latest"],
                            "environment_variables": (
                                [{"name": "TOKEN", "required": True}] if state["credentials"] else []
                            ),
                            "headers": [],
                        }
                    )
                if self.path in (f"/api/v1/mcps/{mcp_id}/versions/1.0.0", f"/api/v1/mcps/{mcp_id}/versions/2.0.0"):
                    version = self.path.rsplit("/", 1)[-1]
                    return self.respond(
                        {
                            "id": "44444444-4444-4444-8444-444444444444",
                            "version": version,
                            "status": "approved",
                            "supported_harnesses": ["pi", "claude-code"],
                            "environment_variables": [],
                            "headers": [],
                        }
                    )
            self.send_error(404)

        def do_POST(self) -> None:
            for index, mcp_id in enumerate(MCPS):
                if self.path == f"/api/v1/mcps/{mcp_id}/install":
                    request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    version = request.get("version") or state["latest"]
                    name = request["local_name"]
                    if request["harness"] == "claude-code":
                        return self.respond(
                            {
                                "listing_id": mcp_id,
                                "harness": "claude-code",
                                "version": version,
                                "version_id": "44444444-4444-4444-8444-444444444444",
                                "digest": f"digest-{version}",
                                "config_snippet": {
                                    "command": [
                                        "claude",
                                        "mcp",
                                        "add",
                                        name,
                                        "--",
                                        f"/bin/{index}-{'fixed' if state['fixed_command'] else version}",
                                    ],
                                    "type": "shell_command",
                                },
                            }
                        )
                    if state["changed_key"] and version == "2.0.0":
                        name += "-other"
                    return self.respond(
                        {
                            "listing_id": mcp_id,
                            "harness": "pi",
                            "version": version,
                            "version_id": "44444444-4444-4444-8444-444444444444",
                            "digest": f"digest-{version}",
                            "config_snippet": {
                                "mcpServers": {name: {"command": f"/bin/{index}-{version}", "args": []}}
                            },
                        }
                    )
            self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Registry)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    home = tmp_path / "home"
    home.mkdir()
    (home / ".observal").mkdir()
    (home / ".observal/config.json").write_text(
        json.dumps({"server_url": url, "user_id": "alice", "access_token": "test-token"})
    )
    repo = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "PYTHONPATH": os.pathsep.join([str(repo), str(repo / "packages/observal-shared")]),
    }

    def cli(*argv: str, success: bool = True) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            [sys.executable, "-m", "observal_cli", *argv],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=25,
        )
        if success:
            assert proc.returncode == 0, proc.stdout + proc.stderr
        return proc

    def apply(session: str) -> dict:
        key = hashlib.sha256(f"{url}\0alice\0{session}".encode()).hexdigest()
        cli("_startup-apply", "--cwd", str(tmp_path), "--session-id", session, "--notice-key", key)
        return json.loads((home / ".observal/update-notices" / f"{key}.json").read_text())

    try:
        yield state, home, cli, apply, env
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_managed_pi_mcp_install_and_update_respects_frozen_and_other_entries(instance) -> None:
    state, home, cli, apply, _env = instance
    path = home / ".pi/agent/mcp.json"
    cli("registry", "mcp", "install", MCPS[0], "--harness", "pi", "--no-prompt", "--output", "json")
    assert not path.exists(), "The legacy snippet command must not write"
    for mcp_id in MCPS:
        cli("registry", "mcp", "install", mcp_id, "--harness", "pi", "--managed", "--output", "json")
    before = json.loads(path.read_text())["mcpServers"]
    assert len(before) == 2
    state["latest"] = "2.0.0"
    assert all(row["status"] != "updated" for row in apply("frozen")["items"])
    assert json.loads(path.read_text())["mcpServers"] == before
    cli("unfreeze")
    notice = apply("opted-in")
    assert [row["status"] for row in notice["items"]] == ["updated", "updated"], notice
    after = json.loads(path.read_text())["mcpServers"]
    assert set(after) == set(before)
    assert all(row["command"].endswith("2.0.0") for row in after.values())
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.getenv("OBSERVAL_RUN_LIVE_PI") != "1", reason="explicit live Pi/RPC opt-in")
def test_pi_rpc_bridge_updates_owned_mcp_after_unfreeze(instance) -> None:
    """Exercise the installed extension and detached worker in a disposable home."""
    import shutil

    from tests.test_auto_update_live_pi import CLI, _rpc_session

    if not shutil.which("pi") or not CLI.exists():
        pytest.skip("Requires installed Pi and editable Observal CLI")
    state, home, cli, _apply, env = instance
    path = home / ".pi/agent/mcp.json"
    cli("registry", "mcp", "install", MCPS[0], "--harness", "pi", "--managed")
    before = path.read_bytes()
    state["latest"] = "2.0.0"
    env["OBSERVAL_CLI_BIN"] = str(CLI)
    assert _rpc_session(home, env, expected="update available")
    assert path.read_bytes() == before
    cli("unfreeze")
    assert _rpc_session(home, env, expected="installed on disk")
    assert b"2.0.0" in path.read_bytes()


def test_existing_pasted_file_or_credentials_cannot_be_adopted(instance) -> None:
    state, home, cli, _apply, _env = instance
    path = home / ".pi/agent/mcp.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mcpServers": {"mine": {"command": "mine"}}}))
    original = path.read_bytes()
    assert cli("registry", "mcp", "install", MCPS[0], "--harness", "pi", "--managed", success=False).returncode != 0
    assert path.read_bytes() == original
    path.unlink()
    state["credentials"] = True
    assert cli("registry", "mcp", "install", MCPS[0], "--harness", "pi", "--managed", success=False).returncode != 0
    assert not path.exists()


def test_pinned_mcp_and_changed_server_key_stay_manual(instance) -> None:
    state, home, cli, apply, _env = instance
    path = home / ".pi/agent/mcp.json"
    cli("registry", "mcp", "install", MCPS[0], "--harness", "pi", "--managed", "--version", "1.0.0")
    state["latest"] = "2.0.0"
    original = path.read_bytes()
    cli("unfreeze")
    assert apply("pinned")["items"][0]["status"] != "updated"
    assert path.read_bytes() == original
    # A separate explicit unpinned install clears the pin, without adopting
    # the changed destination key returned by a later release.
    state["latest"] = "1.0.0"
    cli("registry", "mcp", "install", MCPS[0], "--harness", "pi", "--managed")
    state["latest"] = "2.0.0"
    state["changed_key"] = True
    assert apply("renamed")["items"][0]["status"] != "updated"
    assert path.read_bytes() == original


@pytest.mark.parametrize("foreign", [False, True])
def test_stopped_mcp_install_restores_only_verified_original(instance, foreign: bool) -> None:
    state, home, cli, apply, env = instance
    path = home / ".pi/agent/mcp.json"
    cli("registry", "mcp", "install", MCPS[0], "--harness", "pi", "--managed")
    old = path.read_bytes()
    cli("unfreeze")
    state["latest"] = "2.0.0"
    # Simulate a failure after the normal writer but before metadata advances.
    # The shared recovery helper can prove and restore the old whole-file bytes.
    injection = home / "injection"
    injection.mkdir()
    (injection / "sitecustomize.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "from observal_cli import lockfile\n"
        "def stopped(*args, **kwargs):\n"
        "    if os.environ.get('FOREIGN_EDIT') == '1':\n"
        "        (Path.home() / '.pi/agent/mcp.json').write_text('{\\\"mcpServers\\\":{\\\"mine\\\":{}}}')\n"
        "    raise OSError('stopped before lock commit')\n"
        "lockfile.upsert_standalone = stopped\n"
    )
    env["PYTHONPATH"] = os.pathsep.join([str(injection), env["PYTHONPATH"]])
    env["FOREIGN_EDIT"] = "1" if foreign else "0"
    notice = apply(f"stopped-{foreign}")
    backups = list((home / ".observal/update-backups").glob("*/manifest.json"))
    if foreign:
        assert notice["items"][0]["status"] == "failed", notice
        assert notice["outcome_final"] is False
        assert backups and path.read_bytes() != old
        assert apply("retry-after-foreign")["items"][0]["status"] == "skipped"
    else:
        assert notice["items"][0]["status"] == "skipped", notice
        assert path.read_bytes() == old
        assert not backups


def test_foreign_edit_or_added_key_blocks_managed_mcp_update(instance) -> None:
    state, home, cli, apply, _env = instance
    path = home / ".pi/agent/mcp.json"
    cli("registry", "mcp", "install", MCPS[0], "--harness", "pi", "--managed")
    cli("unfreeze")
    state["latest"] = "2.0.0"
    config = json.loads(path.read_text())
    config["mcpServers"]["mine"] = {"command": "mine"}
    path.write_text(json.dumps(config))
    original = path.read_bytes()
    assert apply("foreign")["items"][0]["status"] != "updated"
    assert path.read_bytes() == original


SHIM = """#!{python}
import json, os, sys
from pathlib import Path
path = Path.home() / ".claude.json"
data = json.loads(path.read_text()) if path.exists() else {{}}
a = sys.argv[1:]
assert a[:2] in (["mcp", "add"], ["mcp", "remove"]) and a[2:4] == ["-s", "user"], a
servers = data.setdefault("mcpServers", {{}})
if a[1] == "add":
    name, cmd = a[4], a[6:]
    if os.environ.get("FAIL_ADD_CONTAINING") and os.environ["FAIL_ADD_CONTAINING"] in " ".join(cmd):
        sys.exit(3)
    servers[name] = {{"type": "stdio", "command": cmd[0], "args": cmd[1:], "env": {{}}}}
else:
    servers.pop(a[4], None)
path.write_text(json.dumps(data, indent=2))
"""


@pytest.fixture()
def claude_instance(instance):
    state, home, cli, apply, env = instance
    bin_dir = home / "bin"
    bin_dir.mkdir()
    if os.getenv("OBSERVAL_RUN_LIVE_CLAUDE_CLI") == "1":
        real = shutil.which("claude")  # The real CLI, still confined to the disposable HOME.
        assert real, "claude is not installed"
        # A pass-through wrapper: everything reaches the real CLI except one
        # chosen `mcp add`, so recovery is exercised with the real remove/add.
        shim = bin_dir / "claude"
        shim.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = mcp ] && [ "$2" = add ] && [ -n "$FAIL_ADD_CONTAINING" ]; then\n'
            '  case "$*" in *"$FAIL_ADD_CONTAINING"*) exit 3;; esac\n'
            "fi\n"
            f'exec "{real}" "$@"\n'
        )
        shim.chmod(0o755)
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    else:
        shim = bin_dir / "claude"
        shim.write_text(SHIM.format(python=sys.executable))
        shim.chmod(0o755)
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    url = json.loads((home / ".observal/config.json").read_text())["server_url"]

    def apply_claude(session: str) -> dict:
        key = hashlib.sha256(f"claude-code\0{url}\0alice\0{session}".encode()).hexdigest()
        cli("_startup-apply-claude", "--cwd", str(home), "--session-id", session, "--notice-key", key)
        return json.loads((home / ".observal/update-notices" / f"{key}.json").read_text())

    return state, home, cli, apply_claude, env


def _entry(home: Path, name: str = "search"):
    data = json.loads((home / ".claude.json").read_text())
    return data.get("mcpServers", {}).get(name)


def test_claude_managed_mcp_installs_updates_and_refuses_pasted_entries(claude_instance) -> None:
    state, home, cli, apply, _env = claude_instance
    config = home / ".claude.json"
    config.write_text(json.dumps({"mcpServers": {"search": {"command": "mine", "args": []}}}))
    refused = cli("registry", "mcp", "install", MCPS[0], "--harness", "claude-code", "--managed", success=False)
    assert refused.returncode != 0
    assert _entry(home, "search") == {"command": "mine", "args": []}, "pasted entry must be untouched"
    config.unlink()
    cli("registry", "mcp", "install", MCPS[0], "--harness", "claude-code", "--managed")
    name = next(iter(json.loads(config.read_text())["mcpServers"]))
    assert json.loads(config.read_text())["mcpServers"][name]["command"] == "/bin/0-1.0.0"
    state["latest"] = "2.0.0"
    assert apply("frozen")["items"][0]["status"] != "updated"
    assert json.loads(config.read_text())["mcpServers"][name]["command"] == "/bin/0-1.0.0"
    cli("unfreeze")
    notice = apply("opted-in")
    assert notice["items"][0]["status"] == "updated", notice["items"][0].get("reason")
    assert json.loads(config.read_text())["mcpServers"][name]["command"] == "/bin/0-2.0.0"
    assert not list((home / ".observal/update-backups").glob("*/*"))


def test_claude_mcp_edited_by_user_is_not_replaced_and_says_why(claude_instance) -> None:
    state, home, cli, apply, _env = claude_instance
    config = home / ".claude.json"
    cli("registry", "mcp", "install", MCPS[0], "--harness", "claude-code", "--managed")
    data = json.loads(config.read_text())
    name = next(iter(data["mcpServers"]))
    data["mcpServers"][name]["command"] = "/my/own/build"
    config.write_text(json.dumps(data))
    state["latest"] = "2.0.0"
    cli("unfreeze")
    item = apply("edited")["items"][0]
    assert item["status"] != "updated"
    assert "edited" in item["reason"] or "changed" in item["reason"], item
    assert json.loads(config.read_text())["mcpServers"][name]["command"] == "/my/own/build"


def test_claude_mcp_add_failure_after_remove_restores_the_original(claude_instance) -> None:
    state, home, cli, apply, env = claude_instance
    config = home / ".claude.json"
    cli("registry", "mcp", "install", MCPS[0], "--harness", "claude-code", "--managed")
    name = next(iter(json.loads(config.read_text())["mcpServers"]))
    state["latest"] = "2.0.0"
    cli("unfreeze")
    env["FAIL_ADD_CONTAINING"] = "2.0.0"
    notice = apply("add-fails")
    assert notice["items"][0]["status"] != "updated", notice
    assert "put back" in notice["items"][0]["reason"], notice
    assert json.loads(config.read_text())["mcpServers"][name]["command"] == "/bin/0-1.0.0"


def test_managed_pi_credentials_are_only_carried_forward() -> None:
    from observal_cli import automatic_mcp_plan as plan

    old = {"command": "x", "env": {"KEY": "s3cret"}, "headers": {"Authorization": "Bearer t"}}
    ok = {"command": "y", "env": {"KEY": "s3cret", "OPT": ""}, "headers": {"Authorization": "Bearer t"}}
    plan.check_credentials(old, ok, required={"KEY"}, required_headers={"Authorization"}, automatic=True)
    for new in (
        {"command": "y", "env": {"KEY": "other"}, "headers": old["headers"]},
        {"command": "y", "env": {"KEY": "s3cret", "NEW": "v"}, "headers": old["headers"]},
        {"command": "y", "env": old["env"], "headers": {"Authorization": "Bearer changed"}},
    ):
        with pytest.raises(plan.McpPlanError, match="saved value"):
            plan.check_credentials(old, new, required=set(), required_headers=set(), automatic=True)
    # A fresh or manual install may use any value the user typed, but a required one cannot be blank.
    plan.check_credentials(None, {"env": {"KEY": "typed"}}, required={"KEY"}, required_headers=set(), automatic=False)
    for blank in ("", "<KEY>"):
        with pytest.raises(plan.McpPlanError, match="requires a value"):
            plan.check_credentials(
                None, {"env": {"KEY": blank}}, required={"KEY"}, required_headers=set(), automatic=False
            )


def _record_entry(home: Path) -> dict:
    (path,) = list((home / ".observal/managed-claude-mcp").glob("*.json"))
    return json.loads(path.read_text())["entry"]


def _lock_version(home: Path) -> str:
    data = json.loads((home / ".observal/lockfile.json").read_text())
    rows = [
        row
        for registry in data["registries"].values()
        for row in registry["harnesses"]["claude-code"].get("standalone", [])
        if row["type"] == "mcp"
    ]
    (row,) = rows
    return row["version"]


@pytest.mark.parametrize("stop", ["before-lock-write", "after-lock-write"])
def test_claude_mcp_stopped_before_the_lock_finishes_restores_entry_record_and_lock(claude_instance, stop: str) -> None:
    """The ownership record and installed lock must never claim a version the entry does not have."""
    state, home, cli, apply, env = claude_instance
    config = home / ".claude.json"
    cli("registry", "mcp", "install", MCPS[0], "--harness", "claude-code", "--managed")
    name = next(iter(json.loads(config.read_text())["mcpServers"]))
    original = json.loads(config.read_text())["mcpServers"][name]
    state["latest"] = "2.0.0"
    cli("unfreeze")
    injection = home / "injection"
    injection.mkdir()
    (injection / "sitecustomize.py").write_text(
        "import os\n"
        "from observal_cli import lockfile\n"
        "_real = lockfile.upsert_standalone\n"
        "def stopped(*args, **kwargs):\n"
        "    if os.environ.get('STOP') and kwargs.get('version') == '2.0.0':\n"
        "        if os.environ['STOP'] == 'after-lock-write':\n"
        "            _real(*args, **kwargs)\n"
        "        raise OSError('injected stop')\n"
        "    return _real(*args, **kwargs)\n"
        "lockfile.upsert_standalone = stopped\n"
    )
    env["PYTHONPATH"] = os.pathsep.join([str(injection), env["PYTHONPATH"]])
    env["STOP"] = stop
    notice = apply("stopped")
    item = notice["items"][0]
    assert item["status"] == "skipped" and "restored" in item["reason"], item
    # All three are the originals again, so the notice is true.
    assert json.loads(config.read_text())["mcpServers"][name] == original
    assert _record_entry(home)["command"] == "/bin/0-1.0.0"
    assert _lock_version(home) == "1.0.0"
    assert not list((home / ".observal/update-backups").glob("*/*"))
    # And the next, uninterrupted attempt succeeds: the record was not left lying.
    env.pop("STOP")
    assert apply("retry")["items"][0]["status"] == "updated"
    assert json.loads(config.read_text())["mcpServers"][name]["command"] == "/bin/0-2.0.0"
    assert _record_entry(home)["command"] == "/bin/0-2.0.0" and _lock_version(home) == "2.0.0"


def test_claude_mcp_recovery_is_not_claimed_over_an_edited_entry(claude_instance) -> None:
    state, home, cli, apply, env = claude_instance
    config = home / ".claude.json"
    cli("registry", "mcp", "install", MCPS[0], "--harness", "claude-code", "--managed")
    name = next(iter(json.loads(config.read_text())["mcpServers"]))
    state["latest"] = "2.0.0"
    cli("unfreeze")
    injection = home / "injection"
    injection.mkdir()
    (injection / "sitecustomize.py").write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "from observal_cli import lockfile\n"
        "def stopped(*args, **kwargs):\n"
        "    f = Path.home() / '.claude.json'\n"
        "    d = json.loads(f.read_text())\n"
        "    for key in d['mcpServers']:\n"
        "        d['mcpServers'][key]['command'] = '/my/own'\n"
        "    f.write_text(json.dumps(d))\n"
        "    raise OSError('injected stop')\n"
        "lockfile.upsert_standalone = stopped\n"
    )
    env["PYTHONPATH"] = os.pathsep.join([str(injection), env["PYTHONPATH"]])
    item = apply("edited")["items"][0]
    assert item["status"] == "failed", item
    assert json.loads(config.read_text())["mcpServers"][name]["command"] == "/my/own"
    assert list((home / ".observal/update-backups").glob("*/*")), "the private recovery file must stay"


def test_claude_mcp_release_with_an_identical_entry_still_updates_cleanly(claude_instance) -> None:
    """A version bump whose generated command is unchanged saves no recovery plan, yet is a success."""
    state, home, cli, apply, _env = claude_instance
    state["fixed_command"] = True
    config = home / ".claude.json"
    cli("registry", "mcp", "install", MCPS[0], "--harness", "claude-code", "--managed")
    name = next(iter(json.loads(config.read_text())["mcpServers"]))
    before = json.loads(config.read_text())["mcpServers"][name]
    state["latest"] = "2.0.0"
    cli("unfreeze")
    notice = apply("identical")
    assert notice["items"][0]["status"] == "updated", notice["items"][0].get("reason")
    assert notice.get("outcome_final") is not False
    assert json.loads(config.read_text())["mcpServers"][name] == before
    assert _lock_version(home) == "2.0.0"
    assert not list((home / ".observal/update-notices").glob("*.pending")), "must not block later installs"
    assert not list((home / ".observal/update-backups").glob("*/*"))
    assert all(item["status"] != "updated" for item in apply("again")["items"]), "nothing left to update"


@pytest.mark.parametrize("foreign", ["lock-row", "ownership-record"])
def test_claude_mcp_recovery_never_overwrites_a_foreign_change_to_its_bookkeeping(
    claude_instance, foreign: str
) -> None:
    """Unknown metadata means keep the backup and report an unresolved outcome, not "restored"."""
    state, home, cli, apply, env = claude_instance
    config = home / ".claude.json"
    cli("registry", "mcp", "install", MCPS[0], "--harness", "claude-code", "--managed")
    name = next(iter(json.loads(config.read_text())["mcpServers"]))
    state["latest"] = "2.0.0"
    cli("unfreeze")
    injection = home / "injection"
    injection.mkdir()
    (injection / "sitecustomize.py").write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "from observal_cli import lockfile\n"
        "_real = lockfile.upsert_standalone\n"
        "def stopped(*args, **kwargs):\n"
        "    if os.environ.get('FOREIGN') and kwargs.get('version') == '2.0.0':\n"
        "        if os.environ['FOREIGN'] == 'lock-row':\n"
        "            f = Path.home() / '.observal/lockfile.json'\n"
        "            d = json.loads(f.read_text())\n"
        "            for reg in d['registries'].values():\n"
        "                for row in reg['harnesses']['claude-code'].get('standalone', []):\n"
        "                    if row.get('type') == 'mcp':\n"
        "                        row['version'] = 'MANUAL-PIN'\n"
        "            f.write_text(json.dumps(d))\n"
        "        else:\n"
        "            (rec,) = (Path.home() / '.observal/managed-claude-mcp').glob('*.json')\n"
        "            d = json.loads(rec.read_text())\n"
        "            d['entry'] = {'command': '/my/recorded', 'args': []}\n"
        "            rec.write_text(json.dumps(d))\n"
        "        raise OSError('injected stop')\n"
        "    return _real(*args, **kwargs)\n"
        "lockfile.upsert_standalone = stopped\n"
    )
    env["PYTHONPATH"] = os.pathsep.join([str(injection), env["PYTHONPATH"]])
    env["FOREIGN"] = foreign
    item = apply("foreign-bookkeeping")["items"][0]
    assert item["status"] == "failed", item
    assert "restored" not in item["reason"].lower(), item
    # Nothing was overwritten: the foreign value is still there.
    if foreign == "lock-row":
        assert _lock_version(home) == "MANUAL-PIN"
    else:
        assert _record_entry(home) == {"command": "/my/recorded", "args": []}
    # The entry was not touched either, and the private recovery file is kept.
    assert json.loads(config.read_text())["mcpServers"][name]["command"] == "/bin/0-2.0.0"
    assert list((home / ".observal/update-backups").glob("*/*"))


@pytest.mark.parametrize("fixed_command", [False, True], ids=["changed-entry", "identical-entry"])
@pytest.mark.parametrize("tamper", ["lock-row", "ownership-record"])
def test_claude_mcp_success_requires_the_planned_bookkeeping_not_just_a_newer_version(
    claude_instance, tamper: str, fixed_command: bool
) -> None:
    """A child that exits 0 but leaves a foreign lock-row field or record is not a verified success."""
    state, home, cli, apply, env = claude_instance
    state["fixed_command"] = fixed_command
    config = home / ".claude.json"
    cli("registry", "mcp", "install", MCPS[0], "--harness", "claude-code", "--managed")
    name = next(iter(json.loads(config.read_text())["mcpServers"]))
    entry_before = json.loads(config.read_text())["mcpServers"][name]
    state["latest"] = "2.0.0"
    cli("unfreeze")
    injection = home / "injection"
    injection.mkdir()
    (injection / "sitecustomize.py").write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "from observal_cli import lockfile\n"
        "_real = lockfile.upsert_standalone\n"
        "def tampered(*args, **kwargs):\n"
        "    result = _real(*args, **kwargs)\n"
        "    if os.environ.get('TAMPER') and kwargs.get('version') == '2.0.0':\n"
        "        if os.environ['TAMPER'] == 'lock-row':\n"
        "            f = Path.home() / '.observal/lockfile.json'\n"
        "            d = json.loads(f.read_text())\n"
        "            for reg in d['registries'].values():\n"
        "                for row in reg['harnesses']['claude-code'].get('standalone', []):\n"
        "                    if row.get('type') == 'mcp':\n"
        "                        row['digest'] = 'tampered'\n"
        "            f.write_text(json.dumps(d))\n"
        "        else:\n"
        "            (rec,) = (Path.home() / '.observal/managed-claude-mcp').glob('*.json')\n"
        "            d = json.loads(rec.read_text())\n"
        "            d['entry'] = {'command': '/my/recorded', 'args': []}\n"
        "            rec.write_text(json.dumps(d))\n"
        "    return result\n"
        "lockfile.upsert_standalone = tampered\n"
    )
    env["PYTHONPATH"] = os.pathsep.join([str(injection), env["PYTHONPATH"]])
    env["TAMPER"] = tamper
    item = apply("tampered-success")["items"][0]
    assert item["status"] == "failed", item
    assert "restored" not in item["reason"].lower(), item
    # Nothing foreign was overwritten, and the private recovery file is kept for inspection.
    if tamper == "lock-row":
        data = json.loads((home / ".observal/lockfile.json").read_text())
        rows = [
            row
            for registry in data["registries"].values()
            for row in registry["harnesses"]["claude-code"].get("standalone", [])
            if row["type"] == "mcp"
        ]
        assert rows[0]["digest"] == "tampered"
    else:
        assert _record_entry(home) == {"command": "/my/recorded", "args": []}
    live = json.loads(config.read_text())["mcpServers"][name]
    if fixed_command:
        assert live == entry_before
    else:
        assert live["command"] == "/bin/0-2.0.0", "recovery must not roll back over foreign bookkeeping"
    assert list((home / ".observal/update-backups").glob("*/*"))


@pytest.mark.parametrize("revoked", ["consent", "session-marker"])
def test_claude_mcp_identical_entry_release_is_still_admission_checked_in_the_child(
    claude_instance, revoked: str
) -> None:
    """The identical-entry path takes the same guard as a changed entry, so it writes nothing once admission is gone."""
    state, home, cli, apply, env = claude_instance
    state["fixed_command"] = True
    cli("registry", "mcp", "install", MCPS[0], "--harness", "claude-code", "--managed")
    state["latest"] = "2.0.0"
    cli("unfreeze")
    injection = home / "injection"
    injection.mkdir()
    (injection / "sitecustomize.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "from observal_cli import auto_update_policy\n"
        "if os.environ.get('OBSERVAL_AUTO_UPDATE_INSTALL') == '1':\n"
        "    if os.environ.get('REVOKE') == 'consent':\n"
        "        _real = auto_update_policy.policy_status\n"
        "        auto_update_policy.policy_status = lambda r: {**_real(r), 'effective': False}\n"
        "    else:\n"
        "        Path(os.environ['OBSERVAL_AUTO_UPDATE_SHUTDOWN_MARKER']).write_text('x')\n"
    )
    env["PYTHONPATH"] = os.pathsep.join([str(injection), env["PYTHONPATH"]])
    env["REVOKE"] = "consent" if revoked == "consent" else "marker"
    item = apply("revoked")["items"][0]
    assert item["status"] != "updated", item
    assert _lock_version(home) == "1.0.0", "no metadata may advance once admission is gone"
    assert not list((home / ".observal/update-backups").glob("*/*"))
