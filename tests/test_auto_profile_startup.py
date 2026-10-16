# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Disposable-home guarded Kiro, Cursor and Codex profile updates through the normal pull.

Each test runs the real CLI (manual ``agent pull``, ``unfreeze``) and the real
``_startup-apply-hook`` worker against a stub registry whose snippets mirror
the server adapters' output. ``updated`` is only reported after the pull's own
post-write check compared every owned byte and mode with the plan, so an
``updated`` outcome also proves the plan and the writer agree.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from observal_cli.hooks.claude_updates import notice_key

AGENT = "33333333-3333-4333-8333-333333333333"
OTHER = "44444444-4444-4444-8444-444444444444"
HARNESSES = ("kiro", "cursor", "codex")
PUSH = "python3 -m observal_cli.hooks.session_push --harness {h}"


def profile_path(harness: str, home: Path, root: Path, name: str = "reviewer") -> Path:
    return {
        "kiro": home / ".kiro/agents" / f"{name}.json",
        # Cursor writes "user" profiles under the pull directory (allow_home_agent_profile=False).
        "cursor": root / ".cursor/agents" / f"{name}.md",
        "codex": home / ".codex/agents" / f"{name}.toml",
    }[harness]


def shared_paths(harness: str, home: Path) -> list[Path]:
    return {
        "kiro": [home / ".kiro/hooks/observal.json"],
        "cursor": [home / ".cursor/mcp.json", home / ".cursor/hooks.json"],
        "codex": [home / ".codex/config.toml", home / ".codex/hooks.json"],
    }[harness]


def snippet(harness: str, version: str, state: dict, name: str = "reviewer") -> dict:
    """Shapes copied from observal-server/services/harness/{kiro,cursor,codex}.py."""
    mcp_command = "/bin/changed" if state["mcp_changed"] and version == "2.0.0" else "/bin/echo"
    servers = {"tools": {"command": mcp_command, "args": ["serve"]}} if state["mcp"] else {}
    if harness == "kiro":
        if state["kiro_new_mcp"] and version == "2.0.0":
            servers = {**servers, "lint": {"command": "/bin/echo", "args": ["lint"]}}
            if state["kiro_mcp_secret"]:
                servers["lint"]["env"] = {"TOKEN": "abc"}
        hooks = [
            {
                "name": f"observal-session-push-{t.lower()}",
                "trigger": t,
                "action": {"type": "command", "command": PUSH.format(h="kiro")},
            }
            for t in ("UserPromptSubmit", "Stop")
        ]
        if state["hook_changed"] and version == "2.0.0":
            hooks.append({"name": "lint", "trigger": "Stop", "action": {"type": "command", "command": "/bin/true"}})
        result = {
            "agent_profile": {
                "path": f"~/.kiro/agents/{name}.json",
                "content": {
                    "name": name,
                    "description": f"{name} {version}",
                    "prompt": f"You are {name} {version}.",
                    "mcpServers": servers,
                    "tools": ["*"],
                    "toolAliases": {},
                    "resources": ["file://AGENTS.md"],
                    "includeMcpJson": True,
                    "model": None,
                },
            },
            "hooks_config": {"path": "~/.kiro/hooks/observal.json", "content": {"version": "v1", "hooks": hooks}},
            "scope": "user",
        }
    elif harness == "cursor":
        hooks = {
            "beforeSubmitPrompt": [{"command": PUSH.format(h="cursor"), "type": "command"}],
            "stop": [{"command": PUSH.format(h="cursor"), "type": "command"}],
        }
        if state["hook_changed"] and version == "2.0.0":
            hooks["stop"].append({"command": "/bin/true", "type": "command"})
        result = {
            "mcp_config": {"path": "~/.cursor/mcp.json", "content": {"mcpServers": servers}},
            "scope": "user",
            "agent_profile": {
                "path": f"~/.cursor/agents/{name}.md",
                "content": f"---\nname: {name}\ndescription: '{name}'\nmodel: inherit\n---\n\nYou are {name} {version}.",
            },
            "hooks_config": {"path": "~/.cursor/hooks.json", "content": {"version": 1, "hooks": hooks}, "merge": True},
        }
    else:
        hooks = {
            "UserPromptSubmit": [{"matcher": "", "hooks": [{"type": "command", "command": PUSH.format(h="codex")}]}],
            "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": PUSH.format(h="codex")}]}],
        }
        if state["hook_changed"] and version == "2.0.0":
            hooks["Stop"].append({"matcher": "", "hooks": [{"type": "command", "command": "/bin/true"}]})
        result = {
            "agent_profile": {
                "path": f"~/.codex/agents/{name}.toml",
                "content": f'name = "{name}"\ndescription = "{name}"\ndeveloper_instructions = "You are {name} {version}."\n',
            },
            "mcp_config": {"path": "~/.codex/config.toml", "content": {"mcp_servers": servers}},
            "hooks_config": {"path": "~/.codex/hooks.json", "content": hooks and {"hooks": hooks}},
            "scope": "user",
        }
    if state["skill"]:
        result["skill_components"] = [
            {
                "id": "s1",
                "name": "review",
                "skill_md_content": f"# review skill {version}\n",
                "script_content": f"echo {version}\n",
                "script_filename": "run.sh",
            }
        ]
    return result


def components(harness: str, version: str, state: dict, *, installed: bool) -> list[dict]:
    rows = []
    if state["skill"]:
        rows.append(("skill", "s1"))
    if state["mcp"]:
        rows.append(("mcp", "m1"))
    if harness == "kiro" and state["kiro_new_mcp"] and version == "2.0.0":
        rows.append(("mcp", "m2"))
    if installed:
        return [{"type": kind, "id": cid, "version": version} for kind, cid in rows]
    return [
        {"component_type": kind, "component_id": cid, "resolved_version": version, "name": cid} for kind, cid in rows
    ]


@pytest.fixture(params=HARNESSES)
def instance(request, tmp_path: Path):
    harness = request.param
    state = {
        "harness": harness,
        "latest": "1.0.0",
        "install_calls": 0,
        "skill": False,
        "mcp": True,
        "mcp_changed": False,
        "hook_changed": False,
        "kiro_new_mcp": False,
        "kiro_mcp_secret": False,
    }

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

        def _agent(self) -> str | None:
            return next((a for a in (AGENT, OTHER) if f"/api/v1/agents/{a}" in self.path), None)

        def do_GET(self) -> None:
            if self.path == "/api/v1/config/version":
                return self.respond({"server_version": "dev"})
            if self.path.startswith("/api/v1/mcps/"):
                # Credential-free MCP listings, read for install requirements.
                mcp_id = self.path.split("/")[4]
                return self.respond({"id": mcp_id, "name": mcp_id, "slug": mcp_id, "namespace": "alice"})
            agent = self._agent()
            name = "reviewer" if agent == AGENT else "helper"
            if agent and self.path == f"/api/v1/agents/{agent}":
                return self.respond(
                    {
                        "id": agent,
                        "name": name,
                        "namespace": "alice",
                        "slug": name,
                        "version": state["latest"],
                        "latest_approved_version": state["latest"],
                        "component_links": [],
                    }
                )
            if agent and "/versions/" in self.path:
                version = self.path.rsplit("/", 1)[-1]
                return self.respond(
                    {
                        "version": version,
                        "status": "approved",
                        "supported_harnesses": [harness],
                        "components": components(harness, version, state, installed=False),
                    }
                )
            self.send_error(404)

        def do_POST(self) -> None:
            if self.path == "/api/v1/layer-snapshots":
                return self.respond({"hash": "test"})
            agent = self._agent()
            if agent and self.path.endswith("/install"):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                version = request.get("version") or state["latest"]
                assert request["harness"] == harness and request["strict"] is True
                state["install_calls"] += 1
                name = "reviewer" if agent == AGENT else "helper"
                return self.respond(
                    {
                        "agent_id": agent,
                        "harness": harness,
                        "version": version,
                        "config_snippet": snippet(harness, version, state, name),
                        "lock": {
                            "status": "locked",
                            "digest": f"digest-{agent[:4]}-{version}",
                            "components": components(harness, version, state, installed=True),
                            "problems": [],
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
    root = tmp_path / "project"
    root.mkdir()
    for directory in (".kiro", ".cursor", ".codex"):
        (home / directory).mkdir()
    config = home / ".observal/config.json"
    config.parent.mkdir(mode=0o700)
    config.write_text(json.dumps({"server_url": url, "user_id": "alice", "access_token": "fake-token"}))
    repo = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "PYTHONPATH": os.pathsep.join([str(repo), str(repo / "packages/observal-shared")]),
    }

    def cli(*argv: str) -> subprocess.CompletedProcess:
        process = subprocess.run(
            [sys.executable, "-m", "observal_cli", *argv], cwd=root, env=env, capture_output=True, text=True, timeout=40
        )
        assert process.returncode == 0, process.stdout + process.stderr
        return process

    def pull(agent: str = AGENT) -> dict:
        process = cli(
            "agent", "pull", agent, "--harness", harness, "--scope", "user", "--dir", str(root),
            "--strict", "--no-prompt", "--output", "json", "--upgrade",
        )  # fmt: skip
        return json.loads(process.stdout)

    def apply(session: str) -> dict:
        key = notice_key(url, "alice", session, harness=harness)
        process = subprocess.run(
            [
                sys.executable, "-m", "observal_cli", "_startup-apply-hook", "--harness", harness,
                "--cwd", str(root), "--session-id", session, "--notice-key", key,
            ],
            cwd=root, env=env, capture_output=True, text=True, timeout=60,
        )  # fmt: skip
        assert process.returncode == 0, process.stdout + process.stderr
        return json.loads((home / ".observal/update-notices" / f"{key}.json").read_text())

    try:
        yield state, home, root, cli, pull, apply, env
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _host_edits(harness: str, home: Path) -> dict[Path, bytes]:
    """What the host or the user typically adds to a shared config after a pull."""
    if harness == "codex":
        config = home / ".codex/config.toml"
        config.write_text(
            config.read_text()
            + '\n[hooks.state."/x/hooks.json:stop:0:0"]\ntrusted_hash = "sha256:abc"\n'
            + '\n[projects."/somewhere"]\ntrust_level = "trusted"\n'
        )
    elif harness == "cursor":
        mcp = home / ".cursor/mcp.json"
        data = json.loads(mcp.read_text())
        data["mcpServers"]["mine"] = {"command": "/usr/bin/mine"}
        mcp.write_text(json.dumps(data, indent=4))
    else:
        hooks = home / ".kiro/hooks/observal.json"
        hooks.write_text(json.dumps(json.loads(hooks.read_text()), indent=4))  # host reformatted it
    return {path: path.read_bytes() for path in shared_paths(harness, home)}


def test_profile_updates_only_after_unfreeze_and_keeps_shared_configs(instance) -> None:
    state, home, root, cli, pull, apply, _env = instance
    harness = state["harness"]
    pull()
    profile = profile_path(harness, home, root)
    assert "1.0.0" in profile.read_text()
    baseline = json.loads(next((home / ".observal/install-baselines").glob("*.json")).read_text())
    assert not set(baseline["files"]) & {str(p) for p in shared_paths(harness, home)}, "shared configs are not owned"
    edits = _host_edits(harness, home)
    state["latest"] = "2.0.0"
    calls = state["install_calls"]
    assert apply("frozen")["items"][0]["status"] != "updated"
    assert state["install_calls"] == calls and "1.0.0" in profile.read_text()
    cli("unfreeze")
    notice = apply("enabled")
    item = notice["items"][0]
    assert item["status"] == "updated", json.dumps(notice)
    assert notice["outcome_final"] is True
    assert "2.0.0" in profile.read_text()
    assert "verified" in item["reason"] and ("new" in item["reason"].lower())
    for path, before in edits.items():
        assert path.read_bytes() == before, f"{path.name} must not be rewritten"
    assert not list((home / ".observal/update-backups").glob("*/manifest.json"))
    # The refreshed evidence still owns only the agent's own files.
    refreshed = json.loads(next((home / ".observal/install-baselines").glob("*.json")).read_text())
    assert refreshed["version"] == "2.0.0" and str(profile) in refreshed["files"]


def test_a_change_to_a_shared_config_stays_manual(instance) -> None:
    state, home, root, cli, pull, apply, _env = instance
    harness = state["harness"]
    pull()
    profile = profile_path(harness, home, root)
    before = {p: p.read_bytes() for p in [profile, *shared_paths(harness, home)]}
    cli("unfreeze")
    state["latest"] = "2.0.0"
    state["mcp_changed" if harness != "kiro" else "hook_changed"] = True
    item = apply("shared-change")["items"][0]
    assert item["status"] == "skipped", json.dumps(item)
    assert "shared" in item["reason"], json.dumps(item)
    assert {p: p.read_bytes() for p in before} == before


def test_a_shared_hook_change_stays_manual(instance) -> None:
    state, home, root, cli, pull, apply, _env = instance
    pull()
    cli("unfreeze")
    state["latest"] = "2.0.0"
    state["hook_changed"] = True
    item = apply("hook-change")["items"][0]
    assert item["status"] == "skipped" and "shared" in item["reason"], json.dumps(item)
    assert "1.0.0" in profile_path(state["harness"], home, root).read_text()


def test_an_edited_profile_is_never_overwritten(instance) -> None:
    state, home, root, cli, pull, apply, _env = instance
    pull()
    profile = profile_path(state["harness"], home, root)
    profile.write_text(profile.read_text() + "\n# my local edit\n" if profile.suffix != ".json" else "{}\n")
    edited = profile.read_bytes()
    cli("unfreeze")
    state["latest"] = "2.0.0"
    item = apply("edited")["items"][0]
    assert item["status"] == "skipped", json.dumps(item)
    assert profile.read_bytes() == edited


def test_bundled_skill_is_updated_with_the_profile(instance) -> None:
    state, home, root, cli, pull, apply, _env = instance
    from observal_cli.cmd_skill import _user_skill_dest

    harness = state["harness"]
    state["skill"] = True
    env_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        folder = _user_skill_dest(harness, "review")
    finally:
        os.environ["HOME"] = env_home or ""
    pull()
    assert (folder / "SKILL.md").read_text() == "# review skill 1.0.0\n"
    cli("unfreeze")
    state["latest"] = "2.0.0"
    item = apply("skill")["items"][0]
    assert item["status"] == "updated", json.dumps(item)
    assert (folder / "SKILL.md").read_text() == "# review skill 2.0.0\n"
    script = folder / "scripts/run.sh"
    assert script.read_text() == "echo 2.0.0\n" and script.stat().st_mode & 0o777 == 0o755


def test_two_agents_on_one_harness_both_record_ownership(instance) -> None:
    """Before shared configs left the evidence, the second agent's baseline was always refused."""
    state, home, _root, _cli, pull, _apply, _env = instance
    pull(AGENT)
    result = pull(OTHER)
    assert not any("Ownership evidence" in str(w) for w in result.get("warnings") or []), result
    assert len(list((home / ".observal/install-baselines").glob("*.json"))) == 2


def test_kiro_agent_mcps_live_in_the_profile(instance) -> None:
    state, home, root, cli, pull, apply, _env = instance
    if state["harness"] != "kiro":
        pytest.skip("Only Kiro keeps an agent's MCPs inside its own profile")
    pull()
    cli("unfreeze")
    state["latest"] = "2.0.0"
    state["kiro_new_mcp"] = True
    state["kiro_mcp_secret"] = True
    item = apply("secret")["items"][0]
    assert item["status"] == "skipped" and "credentials" in item["reason"], json.dumps(item)
    state["kiro_mcp_secret"] = False
    item = apply("plain")["items"][0]
    assert item["status"] == "updated", json.dumps(item)
    assert "lint" in json.loads(profile_path("kiro", home, root).read_text())["mcpServers"]


def test_cursor_and_codex_mcp_additions_stay_manual(instance) -> None:
    state, home, root, cli, pull, apply, _env = instance
    if state["harness"] == "kiro":
        pytest.skip("Kiro MCPs are owned by the profile")
    state["mcp"] = False
    pull()
    cli("unfreeze")
    state["latest"] = "2.0.0"
    state["mcp"] = True
    item = apply("mcp-added")["items"][0]
    assert item["status"] == "skipped" and "shared with other agents" in item["reason"], json.dumps(item)
