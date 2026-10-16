# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Disposable-home integration for the guarded normal Pi skill installer."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture()
def instance(tmp_path: Path):
    state = {"latest": "1.0.0", "script": False, "script_v1": False, "mismatch": False, "bundle": False}

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
            if self.path == f"/api/v1/skills/{ID}":
                return self.respond(
                    {"id": ID, "name": "review", "namespace": "alice", "slug": "review", "version": state["latest"]}
                )
            if self.path in (f"/api/v1/skills/{ID}/versions/1.0.0", f"/api/v1/skills/{ID}/versions/2.0.0"):
                version = self.path.rsplit("/", 1)[-1]
                release = {
                    "version": version,
                    "status": "approved",
                    "supported_harnesses": ["pi", "claude-code"],
                    "skill_md_content": f"# {'different' if state['mismatch'] else 'review'} {version}\n",
                }
                if (version == "1.0.0" and state["script_v1"]) or (version == "2.0.0" and state["script"]):
                    release.update(script_content=f"echo {version}\n", script_filename="run.sh")
                return self.respond(release)
            self.send_error(404)

        def do_POST(self) -> None:
            if self.path == f"/api/v1/skills/{ID}/install":
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                version = request.get("version") or state["latest"]
                skill = {
                    "id": ID,
                    "name": request["local_name"],
                    "delivery_mode": "registry_direct",
                    "skill_md_content": f"# review {version}\n",
                }
                if (version == "1.0.0" and state["script_v1"]) or (version == "2.0.0" and state["script"]):
                    skill.update(script_content=f"echo {version}\n", script_filename="run.sh")
                response = {"version": version, "digest": f"sha256:{version}", "config_snippet": {"skill": skill}}
                if version == "2.0.0" and state["bundle"]:
                    # The release became a complete reviewed folder (main's managed receipts).
                    skill["bundle_version_id"] = "33333333-3333-4333-8333-333333333333"
                    response["bundle"] = {"files": [{"path": "review/SKILL.md", "content": "# review 2.0.0\n"}]}
                return self.respond(response)
            self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Registry)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    home = tmp_path / "home"
    home.mkdir()
    config = home / ".observal" / "config.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"server_url": url, "user_id": "alice", "access_token": "test-token"}))
    project = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "PYTHONPATH": os.pathsep.join([str(project), str(project / "packages/observal-shared")]),
    }

    def cli(*args: str) -> dict:
        proc = subprocess.run(
            [sys.executable, "-m", "observal_cli", *args],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return json.loads(proc.stdout) if proc.stdout.startswith("{") else {}

    def apply(session: str) -> dict:
        import hashlib

        key = hashlib.sha256(f"{url}\0alice\0{session}".encode()).hexdigest()
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "observal_cli",
                "_startup-apply",
                "--cwd",
                str(tmp_path),
                "--session-id",
                session,
                "--notice-key",
                key,
            ],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return json.loads((home / ".observal" / "update-notices" / f"{key}.json").read_text())

    try:
        yield state, home, cli, apply, env, url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_frozen_then_unfrozen_skill_uses_normal_installer(instance) -> None:
    state, home, cli, apply, _env, url = instance
    cli("registry", "skill", "install", ID, "--harness", "pi", "--output", "json")
    file = home / ".pi/agent/skills/review/SKILL.md"
    assert file.read_text() == "# review 1.0.0\n"
    baseline = list((home / ".observal/install-baselines").glob("*.json"))
    assert len(baseline) == 1
    assert json.loads(baseline[0].read_text())["modes"] == {str(file): file.stat().st_mode & 0o777}
    state["latest"] = "2.0.0"
    assert apply("frozen")["items"][0]["status"] != "updated"
    assert file.read_text() == "# review 1.0.0\n"
    cli("unfreeze")
    notice = apply("enabled")
    assert notice["items"][0]["status"] == "updated", notice
    assert file.read_text() == "# review 2.0.0\n"
    assert "reload Pi" in notice["items"][0]["reason"]
    assert not list((home / ".observal/update-backups").glob("*/manifest.json"))


def test_existing_owned_pi_script_is_updated_without_executing_it(instance) -> None:
    state, home, cli, apply, _env, _url = instance
    state["script_v1"] = state["script"] = True
    cli("registry", "skill", "install", ID, "--harness", "pi", "--output", "json")
    file = home / ".pi/agent/skills/review/SKILL.md"
    script = file.parent / "scripts/run.sh"
    assert script.read_text() == "echo 1.0.0\n"
    baseline = next((home / ".observal/install-baselines").glob("*.json"))
    assert set(json.loads(baseline.read_text())["files"]) == {str(file), str(script)}
    state["latest"] = "2.0.0"
    cli("unfreeze")
    assert apply("script-owned")["items"][0]["status"] == "updated"
    assert script.read_text() == "echo 2.0.0\n"
    assert script.stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize("change", ["edit", "chmod", "extra"])
def test_existing_script_with_foreign_changes_is_not_overwritten(instance, change: str) -> None:
    state, home, cli, apply, _env, _url = instance
    state["script_v1"] = state["script"] = True
    cli("registry", "skill", "install", ID, "--harness", "pi", "--output", "json")
    script = home / ".pi/agent/skills/review/scripts/run.sh"
    if change == "edit":
        script.write_text("# local edit\n")
    elif change == "chmod":
        script.chmod(0o600)
    else:
        (script.parent / "my-script.sh").write_text("# mine\n")
    previous = (script.read_bytes(), script.stat().st_mode & 0o777)
    cli("unfreeze")
    state["latest"] = "2.0.0"
    assert apply(f"script-{change}")["items"][0]["status"] != "updated"
    assert (script.read_bytes(), script.stat().st_mode & 0o777) == previous


@pytest.mark.parametrize(
    "change", ["edit", "chmod", "script", "mismatch", "extra", "pin", "missing-baseline", "shared", "shared-local-name"]
)
def test_unsafe_skill_remains_notice_only(instance, change: str) -> None:
    state, home, cli, apply, _env, _url = instance
    args = ["registry", "skill", "install", ID, "--harness", "pi", "--output", "json"]
    if change == "pin":
        args.extend(["--version", "1.0.0"])
    cli(*args)
    file = home / ".pi/agent/skills/review/SKILL.md"
    original = file.read_bytes()
    cli("unfreeze")
    state["latest"] = "2.0.0"
    if change == "edit":
        file.write_text("# my local edit\n")
    elif change == "chmod":
        file.chmod(0o600 if file.stat().st_mode & 0o777 != 0o600 else 0o644)
    elif change == "extra":
        (file.parent / "custom.txt").write_text("my work")
    elif change == "script":
        state["script"] = True
    elif change == "mismatch":
        state["mismatch"] = True
    elif change == "missing-baseline":
        for baseline in (home / ".observal/install-baselines").glob("*.json"):
            baseline.unlink()
    elif change in {"shared", "shared-local-name"}:
        path = home / ".observal/lockfile.json"
        lock = json.loads(path.read_text())
        other = {"type": "skill", "scope": "user", "id": "another-skill", "name": "review"}
        if change == "shared-local-name":
            # A different display name still writes to the same destination.
            # This second tracked entry has no ownership baseline of its own.
            other.update(name="something-else", local_name="review")
        lock["registries"]["https://other.example"] = {"harnesses": {"pi": {"standalone": [other]}}}
        path.write_text(json.dumps(lock))
    notice = apply(change)
    assert notice["items"][0]["status"] != "updated", notice
    if change in {"shared", "shared-local-name"}:
        assert "Another installed skill" in notice["items"][0]["reason"]
    assert file.read_bytes() == (b"# my local edit\n" if change == "edit" else original)
    assert not list((home / ".observal/update-backups").glob("*/manifest.json"))


@pytest.mark.parametrize("case", ["release-bundle", "receipt-owned"])
def test_complete_folder_skills_are_never_automatic(instance, case: str) -> None:
    """Folder bundles and receipt-owned folders upgrade only through the reviewed --upgrade flow."""
    state, home, cli, apply, _env, _url = instance
    cli("registry", "skill", "install", ID, "--harness", "pi", "--output", "json")
    file = home / ".pi/agent/skills/review/SKILL.md"
    original = file.read_bytes()
    if case == "release-bundle":
        state["bundle"] = True
    else:
        path = home / ".observal/lockfile.json"
        lock = json.loads(path.read_text())
        for registry in lock["registries"].values():
            for row in registry["harnesses"]["pi"]["standalone"]:
                row["folder_receipt"] = {"version_id": "v", "target": str(file.parent)}
        path.write_text(json.dumps(lock))
    cli("unfreeze")
    state["latest"] = "2.0.0"
    notice = apply(case)
    assert notice["items"][0]["status"] != "updated", notice
    assert "complete folder bundle" in notice["items"][0]["reason"], notice
    assert file.read_bytes() == original
    assert not list((home / ".observal/update-backups").glob("*/manifest.json"))


@pytest.mark.parametrize("foreign", [False, True])
@pytest.mark.parametrize("with_script", [False, True])
def test_stopped_skill_install_restores_only_attributable_bytes(instance, foreign: bool, with_script: bool) -> None:
    state, home, cli, apply, env, _url = instance
    state["script_v1"] = state["script"] = with_script
    cli("registry", "skill", "install", ID, "--harness", "pi", "--output", "json")
    file = home / ".pi/agent/skills/review/SKILL.md"
    script = file.parent / "scripts/run.sh" if with_script else None
    original_mode = file.stat().st_mode & 0o777
    state["latest"] = "2.0.0"
    cli("unfreeze")

    # A disposable child-process injection, not a production failpoint: fail
    # after the real writer but before the normal lockfile commit.
    injection = home / "injection"
    injection.mkdir()
    (injection / "sitecustomize.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "from observal_cli import lockfile\n"
        "def stopped(*args, **kwargs):\n"
        "    if os.environ.get('FOREIGN_EDIT') == '1':\n"
        "        f = Path.home() / '.pi/agent/skills/review/SKILL.md'\n"
        "        f.write_text('# foreign edit\\n')\n"
        "        f.chmod(0o777)\n"
        "    raise OSError('injected stopped lock write')\n"
        "lockfile.upsert_standalone = stopped\n"
    )
    env["PYTHONPATH"] = os.pathsep.join([str(injection), env["PYTHONPATH"]])
    env["FOREIGN_EDIT"] = "1" if foreign else "0"
    notice = apply(f"stopped-{foreign}")
    item = notice["items"][0]
    backups = list((home / ".observal/update-backups").glob("*/manifest.json"))
    if foreign:
        assert item["status"] == "failed", notice
        assert notice["outcome_final"] is False
        assert backups
        assert file.read_text() == "# foreign edit\n"
        assert file.stat().st_mode & 0o777 == 0o777
        if script:
            assert script.read_text() == "echo 2.0.0\n"
        # An unresolved result must block the next automatic attempt.
        later = apply("retry-after-foreign")
        assert later["items"][0]["status"] == "skipped"
        assert file.read_text() == "# foreign edit\n"
    else:
        assert item["status"] == "skipped", notice
        assert notice["outcome_final"] is True
        assert not backups
        assert file.read_text() == "# review 1.0.0\n"
        assert file.stat().st_mode & 0o777 == original_mode
        if script:
            assert script.read_text() == "echo 1.0.0\n"
            assert script.stat().st_mode & 0o777 == 0o755


def test_foreign_bytes_adopted_by_new_baseline_do_not_count_as_success(instance) -> None:
    state, home, cli, apply, env, _url = instance
    cli("registry", "skill", "install", ID, "--harness", "pi", "--output", "json")
    state["latest"] = "2.0.0"
    cli("unfreeze")
    injection = home / "injection"
    injection.mkdir()
    (injection / "sitecustomize.py").write_text(
        "from pathlib import Path\n"
        "from observal_cli import install_baseline\n"
        "original = install_baseline.capture\n"
        "def capture(**kwargs):\n"
        "    (Path.home() / '.pi/agent/skills/review/SKILL.md').write_text('# foreign edit\\n')\n"
        "    return original(**kwargs)\n"
        "install_baseline.capture = capture\n"
    )
    env["PYTHONPATH"] = os.pathsep.join([str(injection), env["PYTHONPATH"]])
    notice = apply("adopted-foreign-bytes")
    assert notice["items"][0]["status"] == "failed", notice
    assert notice["outcome_final"] is False
    assert (home / ".pi/agent/skills/review/SKILL.md").read_text() == "# foreign edit\n"
    assert list((home / ".observal/update-backups").glob("*/manifest.json"))
    assert list((home / ".observal/update-notices").glob("*.pending"))


def test_advanced_lock_without_baseline_is_unresolved(instance) -> None:
    state, home, cli, apply, env, _url = instance
    cli("registry", "skill", "install", ID, "--harness", "pi", "--output", "json")
    cli("unfreeze")
    state["latest"] = "2.0.0"
    injection = home / "injection"
    injection.mkdir()
    (injection / "sitecustomize.py").write_text(
        "from observal_cli import install_baseline\n"
        "def stopped(**kwargs):\n"
        "    raise OSError('injected baseline failure after lock advance')\n"
        "install_baseline.capture = stopped\n"
    )
    env["PYTHONPATH"] = os.pathsep.join([str(injection), env["PYTHONPATH"]])
    notice = apply("advanced-metadata")
    assert notice["items"][0]["status"] == "failed", notice
    assert notice["outcome_final"] is False
    assert (home / ".pi/agent/skills/review/SKILL.md").read_text() == "# review 2.0.0\n"
    assert list((home / ".observal/update-backups").glob("*/manifest.json"))
    assert list((home / ".observal/update-notices").glob("*.pending"))


@pytest.mark.skipif(os.getenv("OBSERVAL_RUN_LIVE_PI") != "1", reason="explicit live Pi/RPC opt-in")
@pytest.mark.parametrize("with_script", [False, True])
def test_real_pi_rpc_bridge_updates_skill_only_after_unfreeze(instance, with_script: bool) -> None:
    """Launch the actual Pi extension and worker, not just `_startup-apply`."""
    import shutil

    from tests.test_auto_update_live_pi import CLI, _rpc_session

    if not shutil.which("pi") or not CLI.exists():
        pytest.skip("Requires installed Pi and editable Observal CLI")
    state, home, cli, _apply, env, _url = instance
    state["script_v1"] = state["script"] = with_script
    cli("registry", "skill", "install", ID, "--harness", "pi", "--output", "json")
    file = home / ".pi/agent/skills/review/SKILL.md"
    script = file.parent / "scripts/run.sh" if with_script else None
    state["latest"] = "2.0.0"
    env["OBSERVAL_CLI_BIN"] = str(CLI)
    frozen = _rpc_session(home, env, expected="update available")
    assert any("review" in message for message in frozen)
    assert file.read_text() == "# review 1.0.0\n"
    if script:
        assert script.read_text() == "echo 1.0.0\n"
    cli("unfreeze")
    updated = _rpc_session(home, env, expected="installed on disk")
    assert any("review" in message and "installed on disk" in message for message in updated)
    assert any("reload Pi" in message for message in updated)
    assert file.read_text() == "# review 2.0.0\n"
    if script:
        assert script.read_text() == "echo 2.0.0\n"


def _apply_claude(instance, session: str) -> dict:
    import hashlib

    _state, home, _cli, _apply, env, url = instance
    key = hashlib.sha256(f"claude-code\0{url}\0alice\0{session}".encode()).hexdigest()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "observal_cli",
            "_startup-apply-claude",
            "--cwd",
            str(home),
            "--session-id",
            session,
            "--notice-key",
            key,
        ],
        env=env,
        cwd=home,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads((home / ".observal/update-notices" / f"{key}.json").read_text())


@pytest.mark.parametrize("with_script", [False, True])
def test_claude_code_skill_updates_only_after_unfreeze_and_when_clean(instance, with_script: bool) -> None:
    state, home, cli, _apply, _env, _url = instance
    state["script_v1"] = state["script"] = with_script
    cli("registry", "skill", "install", ID, "--harness", "claude-code", "--output", "json")
    file = home / ".claude/skills/review/SKILL.md"
    assert file.read_text() == "# review 1.0.0\n"
    state["latest"] = "2.0.0"
    assert _apply_claude(instance, "frozen")["items"][0]["status"] != "updated"
    assert file.read_text() == "# review 1.0.0\n"
    cli("unfreeze")
    file.write_text("# my edit\n")
    assert _apply_claude(instance, "edited")["items"][0]["status"] != "updated"
    assert file.read_text() == "# my edit\n"
    file.write_text("# review 1.0.0\n")
    notice = _apply_claude(instance, "clean")
    assert notice["items"][0]["status"] == "updated", notice
    assert file.read_text() == "# review 2.0.0\n"
    if with_script:
        assert (file.parent / "scripts/run.sh").read_text() == "echo 2.0.0\n"
