# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Disposable-home exact Claude Code profile updates through the normal pull."""

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

AGENT = "11111111-1111-4111-8111-111111111111"


@pytest.fixture()
def instance(tmp_path: Path):
    state = {
        "latest": "1.0.0",
        "extra": None,
        "install_calls": 0,
        "profile_contents": {},
        "delegation": False,
        "skill": False,
        "drop": False,
    }

    def release_components(version: str) -> list[dict]:
        return (
            [{"component_type": "skill", "component_id": "s1", "resolved_version": version, "name": "review"}]
            if state["skill"] and not (state["drop"] and version == "2.0.0")
            else []
        )

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
                        "version": state["latest"],
                        "latest_approved_version": state["latest"],
                        "component_links": [],
                    }
                )
            if self.path in (f"/api/v1/agents/{AGENT}/versions/1.0.0", f"/api/v1/agents/{AGENT}/versions/2.0.0"):
                return self.respond(
                    {
                        "version": self.path.rsplit("/", 1)[-1],
                        "status": "approved",
                        "supported_harnesses": ["claude-code"],
                        "components": release_components(self.path.rsplit("/", 1)[-1]),
                    }
                )
            self.send_error(404)

        def do_POST(self) -> None:
            if self.path == "/api/v1/layer-snapshots":
                return self.respond({"hash": "test"})
            if self.path == f"/api/v1/agents/{AGENT}/install":
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                version = request.get("version") or state["latest"]
                assert request["harness"] == "claude-code" and request["strict"] is True
                state["install_calls"] += 1
                snippet = {
                    "scope": "user",
                    "agent_profile": {
                        "path": "~/.claude/agents/reviewer.md",
                        "content": state["profile_contents"].get(version, f"# reviewer {version}\n"),
                    },
                    "mcp_config": {},
                    "mcp_setup_commands": [],
                }
                if state["delegation"]:
                    from observal_cli.automatic_claude_plan import DELEGATION_ARGS

                    snippet["mcp_config"] = {
                        "observal-agents": {"command": sys.executable, "args": DELEGATION_ARGS, "env": {}}
                    }
                    snippet["mcp_setup_commands"] = [
                        ["claude", "mcp", "add", "observal-agents", "--", sys.executable, *DELEGATION_ARGS]
                    ]
                if state["skill"] and not (state["drop"] and version == "2.0.0"):
                    snippet["skill_components"] = [
                        {
                            "id": "s1",
                            "name": "review",
                            "skill_md_content": f"# skill {version}\n",
                            "script_content": f"echo {version}\n",
                            "script_filename": "run.sh",
                        }
                    ]
                if version == "2.0.0" and state["extra"] == "skill":
                    snippet["skill_components"] = [{"name": "new", "skill_md_content": "# skill"}]
                if version == "2.0.0" and state["extra"] == "setup":
                    snippet["mcp_setup_commands"] = [["sh", "-c", "touch /tmp/should-not-execute"]]
                if version == "2.0.0" and state["extra"] == "path":
                    snippet["agent_profile"]["path"] = "~/.claude/agents/other.md"
                return self.respond(
                    {
                        "agent_id": AGENT,
                        "harness": "claude-code",
                        "version": version,
                        "config_snippet": snippet,
                        "lock": {
                            "status": "locked",
                            "digest": f"digest-{version}",
                            "components": [{"type": "skill", "id": "s1", "version": version}]
                            if state["skill"] and not (state["drop"] and version == "2.0.0")
                            else [],
                            "problems": [],
                        },
                    }
                )
            self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Registry)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    state["url"] = url
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
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

    def cli(*argv: str) -> dict:
        process = subprocess.run(
            [sys.executable, "-m", "observal_cli", *argv], cwd=root, env=env, capture_output=True, text=True, timeout=25
        )
        assert process.returncode == 0, process.stdout + process.stderr
        return json.loads(process.stdout) if process.stdout.startswith("{") else {}

    def apply(session: str) -> dict:
        key = notice_key(url, "alice", session)
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "observal_cli",
                "_startup-apply-claude",
                "--cwd",
                str(root),
                "--session-id",
                session,
                "--notice-key",
                key,
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        return json.loads((home / ".observal/update-notices" / f"{key}.json").read_text())

    try:
        yield state, home, root, cli, apply, env
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def seed(cli, root: Path, *, pin: bool = False) -> None:
    cli(
        "agent",
        "pull",
        AGENT,
        "--harness",
        "claude-code",
        "--scope",
        "user",
        "--dir",
        str(root),
        "--strict",
        "--no-prompt",
        "--output",
        "json",
        *("--version", "1.0.0") if pin else ("--upgrade",),
    )


def test_frozen_then_unfrozen_profile_uses_normal_pull(instance) -> None:
    state, home, root, cli, apply, _env = instance
    seed(cli, root)
    file = home / ".claude/agents/reviewer.md"
    assert file.read_text() == "# reviewer 1.0.0\n"
    baseline = list((home / ".observal/install-baselines").glob("*.json"))
    assert len(baseline) == 1
    assert json.loads(baseline[0].read_text())["modes"] == {str(file): file.stat().st_mode & 0o777}
    state["latest"] = "2.0.0"
    calls = state["install_calls"]
    assert apply("frozen")["items"][0]["status"] != "updated"
    assert state["install_calls"] == calls and file.read_text() == "# reviewer 1.0.0\n"
    cli("unfreeze")
    notice = apply("enabled")
    assert notice["items"][0]["status"] == "updated", notice
    assert notice["outcome_final"] is True
    assert file.read_text() == "# reviewer 2.0.0\n"
    assert "new session" in notice["items"][0]["reason"]
    assert not list((home / ".observal/update-backups").glob("*/manifest.json"))


def test_session_end_prevents_new_write_admission(instance) -> None:
    state, home, root, cli, apply, env = instance
    seed(cli, root)
    cli("unfreeze")
    state["latest"] = "2.0.0"
    session = "closed-session"
    ended = subprocess.run(
        [sys.executable, "-m", "observal_cli.hooks.claude_updates"],
        input=json.dumps({"hook_event_name": "SessionEnd", "session_id": session}),
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=8,
    )
    assert ended.returncode == 0 and not ended.stdout
    calls = state["install_calls"]
    result = apply(session)
    assert result["items"][0]["status"] == "skipped", result
    assert state["install_calls"] == calls
    assert (home / ".claude/agents/reviewer.md").read_text() == "# reviewer 1.0.0\n"


@pytest.mark.parametrize("tamper", [None, "changed", "missing", "symlink"])
def test_existing_claude_delegation_is_a_verified_noop(instance, tamper: str | None) -> None:
    import shutil

    if not shutil.which("claude"):
        pytest.skip("The normal Claude MCP registration command is unavailable")
    state, home, root, cli, apply, env = instance
    state["delegation"] = True
    env["CLAUDE_CONFIG_DIR"] = str(home / ".claude")
    env["DISABLE_AUTOUPDATER"] = "1"
    seed(cli, root)
    registration = home / ".claude/.claude.json"
    assert registration.is_file()
    original = registration.read_bytes()
    mode = registration.stat().st_mode & 0o777
    inode = registration.stat().st_ino
    cli("unfreeze")
    state["latest"] = "2.0.0"
    if tamper == "changed":
        data = json.loads(registration.read_text())
        data["projects"][str(root)]["mcpServers"]["observal-agents"]["command"] = "/bin/false"
        registration.write_text(json.dumps(data))
    elif tamper == "missing":
        registration.unlink()
    elif tamper == "symlink":
        foreign = home / "other-claude.json"
        foreign.write_bytes(registration.read_bytes())
        registration.unlink()
        registration.symlink_to(foreign)
    expected = registration.read_bytes() if registration.exists() else None
    result = apply(f"delegation-{tamper}")
    assert result["items"][0]["status"] == ("updated" if tamper is None else "skipped"), result
    assert (home / ".claude/agents/reviewer.md").read_text() == (
        "# reviewer 2.0.0\n" if tamper is None else "# reviewer 1.0.0\n"
    )
    assert (registration.read_bytes() if registration.exists() else None) == expected
    if tamper == "symlink":
        assert registration.is_symlink()
    if tamper is None:
        assert registration.stat().st_ino == inode
        assert registration.stat().st_mode & 0o777 == mode
        assert registration.read_bytes() == original


@pytest.mark.parametrize(
    "change",
    [
        "pin",
        "edit",
        "chmod",
        "missing-baseline",
        "extra-file",
        "shared",
        "same-id-other-root",
        "config-dir",
        "setup",
        "path",
    ],
)
def test_unsupported_shape_or_dirty_file_stays_manual(instance, change: str) -> None:
    state, home, root, cli, apply, env = instance
    seed(cli, root, pin=change == "pin")
    file = home / ".claude/agents/reviewer.md"
    state["latest"] = "2.0.0"
    cli("unfreeze")
    if change == "edit":
        file.write_text("# local edit\n")
    elif change == "chmod":
        file.chmod(0o777)
    elif change == "missing-baseline":
        for baseline in (home / ".observal/install-baselines").glob("*.json"):
            baseline.unlink()
    elif change == "extra-file":
        # A baseline containing a second path cannot be adopted as a one-profile plan.
        for baseline in (home / ".observal/install-baselines").glob("*.json"):
            body = json.loads(baseline.read_text())
            body["files"][str(home / ".claude/agents/other.md")] = "0" * 64
            body["modes"][str(home / ".claude/agents/other.md")] = 0o600
            baseline.write_text(json.dumps(body))
    elif change in {"shared", "same-id-other-root"}:
        # Another tracked install claiming the same local name, without its
        # own baseline, must not inherit this install's ownership evidence.
        from observal_cli import lockfile

        lock = home / ".observal" / lockfile.LOCKFILE_PATH.name
        body = json.loads(lock.read_text())
        entries = body["registries"][state["url"]]["harnesses"]["claude-code"]["agents"]
        entries.append(
            {
                "id": AGENT if change == "same-id-other-root" else "other-agent",
                "name": "different",
                "local_name": "reviewer",
                "scope": "user",
                "directory": str(root.parent / "other-root"),
            }
        )
        lock.write_text(json.dumps(body))
    elif change == "config-dir":
        env["CLAUDE_CONFIG_DIR"] = str(home / "other-claude")
    elif change in {"setup", "path"}:
        state["extra"] = change
    previous = file.read_bytes()
    notice = apply(change)
    assert notice["items"][0]["status"] != "updated", notice
    assert file.read_bytes() == previous
    assert not list((home / ".observal/update-backups").glob("*/manifest.json"))


def test_profile_with_hook_commands_is_planned_exactly_as_the_writer_pins_them(instance) -> None:
    """Real server profiles carry hook commands; the writer pins the interpreter, and the plan must too."""
    state, home, root, cli, apply, env = instance

    def hooked(version: str) -> str:
        return (
            "---\nname: reviewer\nhooks:\n  Stop:\n    - hooks:\n        - type: command\n"
            '          command: "python3 -m observal_cli.hooks.session_push"\n---\n\n'
            f"reviewer {version}\n"
        )

    state["profile_contents"] = {"1.0.0": hooked("1.0.0"), "2.0.0": hooked("2.0.0")}
    seed(cli, root)
    cli("unfreeze")
    state["latest"] = "2.0.0"
    notice = apply("hooked")
    assert notice["items"][0]["status"] == "updated", notice["items"][0].get("reason")
    assert "reviewer 2.0.0" in (home / ".claude/agents/reviewer.md").read_text()
    assert not list((home / ".observal/update-backups").glob("*/manifest.json"))


@pytest.mark.parametrize("interference", ["none", "content", "mode"])
def test_stopped_normal_pull_restores_only_planned_bytes(instance, interference: str) -> None:
    state, home, root, cli, apply, env = instance
    seed(cli, root)
    cli("unfreeze")
    state["latest"] = "2.0.0"
    profile = home / ".claude/agents/reviewer.md"
    mode = profile.stat().st_mode & 0o777
    injection = home / "injection"
    injection.mkdir()
    (injection / "sitecustomize.py").write_text(
        "import os\nfrom pathlib import Path\nfrom observal_cli import lockfile\n"
        "def stopped(*args, **kwargs):\n"
        "    f = Path.home() / '.claude/agents/reviewer.md'\n"
        "    if os.environ.get('FOREIGN_EDIT') == 'content':\n"
        "        f.write_text('# foreign edit\\n')\n"
        "    if os.environ.get('FOREIGN_EDIT') == 'mode':\n"
        "        f.chmod(0o777)\n"
        "    raise OSError('injected stopped lockfile write')\n"
        "lockfile.upsert_agent = stopped\n"
    )
    env["PYTHONPATH"] = os.pathsep.join([str(injection), env["PYTHONPATH"]])
    env["FOREIGN_EDIT"] = interference
    result = apply(f"stopped-{interference}")
    backups = list((home / ".observal/update-backups").glob("*/manifest.json"))
    if interference != "none":
        assert result["items"][0]["status"] == "failed", result
        assert result["outcome_final"] is False and backups
        if interference == "content":
            assert profile.read_text() == "# foreign edit\n"
        else:
            assert profile.stat().st_mode & 0o777 == 0o777
        assert apply("blocked")["items"][0]["status"] == "skipped"
    else:
        assert result["items"][0]["status"] == "skipped", result
        assert profile.read_text() == "# reviewer 1.0.0\n"
        assert profile.stat().st_mode & 0o777 == mode
        assert not backups


@pytest.mark.skipif(
    os.getenv("OBSERVAL_RUN_LIVE_CLAUDE_BEDROCK") != "1", reason="requires explicit real Bedrock host opt-in"
)
def test_bedrock_session_activation_and_saved_profile_boundary(instance) -> None:
    """Prove one running session keeps v1, while a new selected session loads v2.

    Three budget-limited Bedrock prompts; all installs, policy, Claude config
    and Observal state live in a disposable home. Provider credentials are
    passed in process env, never written to the test settings or logs.
    """
    import queue
    import secrets
    import shutil
    import time

    if not shutil.which("claude"):
        pytest.skip("Claude Code is unavailable")
    configured = os.getenv("OBSERVAL_LIVE_CLAUDE_SETTINGS_FILE")
    if not configured:
        pytest.skip("Supply an explicit Bedrock Claude settings path for this opt-in test")
    real_settings = Path(configured).resolve(strict=True)
    original = real_settings.read_bytes()
    settings = json.loads(original)
    provider = settings.get("env", {})
    if provider.get("CLAUDE_CODE_USE_BEDROCK") != "1" or not provider.get("AWS_BEARER_TOKEN_BEDROCK"):
        pytest.skip("A Bedrock-authenticated Claude Code configuration is required")
    state, home, root, cli, apply, env = instance
    before = f"PROFILE_V1_{secrets.token_hex(8).upper()}"
    after = f"PROFILE_V2_{secrets.token_hex(8).upper()}"

    def profile(marker: str) -> str:
        return (
            "---\nname: reviewer\ndescription: Disposable Observal profile activation test\n---\n"
            f"When asked for your configured marker, answer exactly {marker} and nothing else.\n"
        )

    state["profile_contents"] = {"1.0.0": profile(before), "2.0.0": profile(after)}
    host_env = {k: v for k, v in env.items() if not k.startswith(("OBSERVAL_", "ANTHROPIC_", "CLAUDE_", "AWS_"))}
    host_env.update(provider)
    host_env.update({"HOME": str(home), "CLAUDE_CONFIG_DIR": str(home / ".claude"), "DISABLE_AUTOUPDATER": "1"})
    auth = subprocess.run(
        ["claude", "auth", "status"], cwd=root, env=host_env, capture_output=True, text=True, timeout=10
    )
    assert auth.returncode == 0
    identity = json.loads(auth.stdout)
    assert identity.get("loggedIn") is True and identity.get("apiProvider") == "bedrock"

    model_args = ["--model", settings["model"]] if settings.get("model") else []
    prompt = "What is your configured marker? Answer with only the marker."

    def fresh_answer() -> str:
        command = [
            "claude",
            "--setting-sources",
            "user",
            "--print",
            "--agent",
            "reviewer",
            "--output-format",
            "json",
            "--max-turns",
            "1",
            "--max-budget-usd",
            "0.12",
            *model_args,
            prompt,
        ]
        completed = subprocess.run(command, cwd=root, env=host_env, capture_output=True, text=True, timeout=100)
        assert completed.returncode == 0, f"Claude host failed (exit {completed.returncode})"
        result = json.loads(completed.stdout)
        assert result.get("is_error") is False, "Claude host reported an error"
        return str(result.get("result", "")).strip()

    try:
        seed(cli, root)
        stream = [
            "claude",
            "--setting-sources",
            "user",
            "--print",
            "--agent",
            "reviewer",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-turns",
            "1",
            "--max-budget-usd",
            "0.25",
            *model_args,
        ]
        proc = subprocess.Popen(
            stream,
            cwd=root,
            env=host_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        received: queue.Queue[dict] = queue.Queue()

        def read_stdout() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                try:
                    received.put(json.loads(line))
                except ValueError:
                    continue

        reader = threading.Thread(target=read_stdout, daemon=True)
        reader.start()

        def active_answer() -> str:
            assert proc.stdin is not None and proc.poll() is None, "Claude exited before its next prompt"
            proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n")
            proc.stdin.flush()
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                try:
                    event = received.get(timeout=0.5)
                except queue.Empty:
                    if proc.poll() is not None:
                        break
                    continue
                if event.get("type") == "result":
                    assert event.get("is_error") is False, "Claude host reported an error"
                    return str(event.get("result", "")).strip()
            raise AssertionError("Claude did not finish the prompt in the running session")

        try:
            assert active_answer() == before, "The running Claude session did not load v1"
            cli("unfreeze")
            state["latest"] = "2.0.0"
            changed = apply("while-claude-runs")
            assert changed["items"][0]["status"] == "updated", changed
            assert (home / ".claude/agents/reviewer.md").read_text() == profile(after)
            # This is observable user-facing behavior, not proof of Claude's
            # internal prompt cache: the old conversation also remembers v1.
            assert active_answer() == before, "The running Claude conversation changed its profile response"
        finally:
            if proc.stdin:
                proc.stdin.close()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.terminate()
                proc.wait(timeout=5)
            reader.join(timeout=2)
        assert fresh_answer() == after, "A new Claude session did not load the selected saved v2 profile"
    finally:
        assert real_settings.read_bytes() == original, "Real Claude settings unexpectedly changed"


def test_bundled_skill_files_update_with_profile_only_when_clean(instance) -> None:
    state, home, root, cli, apply, _env = instance
    state["skill"] = True
    seed(cli, root)
    skill = home / ".claude/skills/review/SKILL.md"
    script = skill.parent / "scripts/run.sh"
    assert skill.read_text() == "# skill 1.0.0\n" and script.read_text() == "echo 1.0.0\n"
    state["latest"] = "2.0.0"
    cli("unfreeze")
    skill.write_text("# my edit\n")
    dirty = apply("dirty")["items"][0]
    assert dirty["status"] != "updated"
    assert "changed" in dirty["reason"], dirty
    assert skill.read_text() == "# my edit\n"
    skill.write_text("# skill 1.0.0\n")
    notice = apply("clean")
    assert notice["items"][0]["status"] == "updated", notice
    assert skill.read_text() == "# skill 2.0.0\n" and script.read_text() == "echo 2.0.0\n"
    assert script.stat().st_mode & 0o777 == 0o755
    assert "2.0.0" in (home / ".claude/agents/reviewer.md").read_text()


def test_release_that_adds_a_skill_creates_it_and_tracks_ownership(instance) -> None:
    state, home, root, cli, apply, _env = instance
    seed(cli, root)
    state["latest"] = "2.0.0"
    state["extra"] = "skill"  # v2 also ships a skill the profile does not own yet
    cli("unfreeze")
    item = apply("adds-skill")["items"][0]
    assert item["status"] == "updated", item
    created = home / ".claude/skills/new/SKILL.md"
    assert created.read_text() == "# skill"
    # Ownership is recorded: the next update sees a clean, owned file set.
    baseline = next((home / ".observal/install-baselines").glob("*.json"))
    assert str(created) in json.loads(baseline.read_text())["files"]


def test_added_skill_never_overwrites_an_existing_file_and_says_why(instance) -> None:
    state, home, root, cli, apply, _env = instance
    seed(cli, root)
    state["latest"] = "2.0.0"
    state["extra"] = "skill"
    cli("unfreeze")
    squatter = home / ".claude/skills/new/SKILL.md"
    squatter.parent.mkdir(parents=True)
    squatter.write_text("# someone else's skill\n")
    item = apply("collision")["items"][0]
    assert item["status"] != "updated"
    assert "exists" in item["reason"] or "another install" in item["reason"], item
    assert squatter.read_text() == "# someone else's skill\n"
    assert "1.0.0" in (home / ".claude/agents/reviewer.md").read_text()


def test_skill_dropped_by_release_is_removed_only_when_unedited(instance) -> None:
    state, home, root, cli, apply, _env = instance
    state["skill"] = True
    seed(cli, root)
    skill = home / ".claude/skills/review/SKILL.md"
    script = skill.parent / "scripts/run.sh"
    state["latest"] = "2.0.0"
    state["drop"] = True
    cli("unfreeze")
    skill.write_text("# my edit\n")
    assert apply("edited-drop")["items"][0]["status"] != "updated"
    assert skill.read_text() == "# my edit\n" and script.exists()
    skill.write_text("# skill 1.0.0\n")
    done = apply("clean-drop")["items"][0]
    assert done["status"] == "updated", done.get("reason")
    assert not skill.exists() and not script.exists()
