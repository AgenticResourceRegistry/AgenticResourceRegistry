# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Agent-to-agent delegation on the CLI side: tasks, isolation, headless runs, A2A client, MCP server."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import httpx
import pytest

from observal_cli import capability_lock
from observal_cli.cmd_pull import rewrite_observal_interpreter
from observal_cli.delegation import a2a_client, local, mcp_server, runner, service, tasks, workspace
from observal_cli.harness import NotSupportedError, ensure_loaded, get_adapter
from observal_cli.harness.protocol import HeadlessPlan, HeadlessRequest

AGENT_ID = "5f2c1a9e-8b1d-4d0c-9c4e-1f2a3b4c5d6e"
AGENT_URN = f"urn:air:observal.example.com:agent:{AGENT_ID}"
A2A_URN = "urn:air:agents.acme.com:a2a:incident-triage"
HAS_GIT = shutil.which("git") is not None
REAL_CHECK_READY = service.check_ready


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "STORE_DIR", tmp_path / "delegations")
    for name in (
        "OBSERVAL_DELEGATION_DEPTH",
        "OBSERVAL_DELEGATION_CHAIN",
        "OBSERVAL_DELEGATION_MAX_DEPTH",
        "OBSERVAL_DELEGATION_TASK_ID",
        "OBSERVAL_HARNESS",
        "CLAUDECODE",
        "CODEX_THREAD_ID",
        "KIRO_CLI_IS_CHILD_PROCESS",
        "KIRO_SESSION_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(service, "SPAWN", None)
    # Never ask the harness CLIs on the machine running the tests whether they are logged in.
    monkeypatch.setattr(service, "check_ready", lambda _harness: None)


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t"}
    env["GIT_COMMITTER_EMAIL"] = "t@t"
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=env, check=True).stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "app.py").write_text("print('v1')\n")
    (repo / ".gitignore").write_text("secret.env\n.claude/\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init")
    return repo


# ── Tasks ────────────────────────────────────────────────────────────────


def test_task_round_trip_and_state_normalisation():
    task = tasks.new_task(message="review auth", observal={"target": AGENT_URN})
    tasks.save(task)
    loaded = tasks.load(task["id"])
    assert loaded["status"]["state"] == tasks.STATE_SUBMITTED
    assert tasks.message_text(loaded["history"][0]) == "review auth"
    assert loaded["history"][0]["role"] == "ROLE_USER"
    assert tasks.normalize_state("input-required") == tasks.STATE_INPUT_REQUIRED
    assert tasks.normalize_state("TASK_STATE_COMPLETED") == tasks.STATE_COMPLETED
    assert tasks.load("../../etc/passwd") is None  # ids are UUIDs, never paths


# ── Workspace isolation ──────────────────────────────────────────────────


@pytest.mark.skipif(not HAS_GIT, reason="git not installed")
def test_workspace_mirrors_the_callers_tree_and_returns_only_the_childs_changes(tmp_path):
    repo = _repo(tmp_path)
    (repo / "app.py").write_text("print('v2 uncommitted')\n")
    (repo / "notes.md").write_text("untracked notes\n")
    (repo / "secret.env").write_text("TOKEN=x\n")

    _git(repo, "config", "diff.noprefix", "true")  # user diff config must not break the snapshot
    ws = workspace.create(repo)
    try:
        assert (ws.path / "app.py").read_text() == "print('v2 uncommitted')\n"
        # Git commands the child runs stay in the copy: no remote, and its refs are its own.
        assert _git(ws.path, "remote") == ""
        _git(ws.path, "branch", "child-branch")
        assert "child-branch" not in _git(repo, "branch")
        assert (ws.path / "notes.md").exists()
        assert not (ws.path / "secret.env").exists()  # ignored files stay behind
        (ws.path / ".claude" / "agents").mkdir(parents=True)
        (ws.path / ".claude" / "agents" / "reviewer.md").write_text("config")
        (ws.path / "AGENT_CONFIG.json").write_text("{}")  # agent config written before the baseline
        workspace.mark_baseline(ws)

        (ws.path / "app.py").write_text("print('v3 from child')\n")
        (ws.path / "new_test.py").write_text("def test_x():\n    pass\n")
        (ws.path / "AGENT_CONFIG.json").write_text('{"edited": true}')
        patch = workspace.changes(ws)
    finally:
        workspace.destroy(ws)

    assert "v3 from child" in patch and "new_test.py" in patch
    assert "AGENT_CONFIG.json" not in patch
    assert any("AGENT_CONFIG.json" in note for note in ws.notes)  # dropped edits are reported
    # The caller's own tree is untouched and the worktree is gone.
    assert (repo / "app.py").read_text() == "print('v2 uncommitted')\n"
    assert not (repo / "new_test.py").exists()
    assert "observal-delegate" not in _git(repo, "worktree", "list")
    # The patch applies cleanly to the caller's tree.
    subprocess.run(["git", "-C", str(repo), "apply", "-"], input=patch, text=True, check=True)
    assert (repo / "app.py").read_text() == "print('v3 from child')\n"


@pytest.mark.skipif(not HAS_GIT, reason="git not installed")
def test_workspace_paths_are_resolved_and_a_failure_leaves_no_worktree(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    ws = workspace.create(repo)
    try:
        assert ws.path == ws.path.resolve()
    finally:
        workspace.destroy(ws)

    def broken(*_a, **_k):
        raise workspace.WorkspaceError("git write-tree failed")

    monkeypatch.setattr(workspace, "_write_tree", broken)
    with pytest.raises(workspace.WorkspaceError):
        workspace.create(repo)
    assert "observal-delegate" not in _git(repo, "worktree", "list")


def test_workspace_outside_git_is_an_empty_directory(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "file.txt").write_text("x")
    ws = workspace.create(plain)
    try:
        assert not ws.is_git and list(ws.path.iterdir()) == []
        assert workspace.changes(ws) == ""
    finally:
        workspace.destroy(ws)


# ── Local headless runs ──────────────────────────────────────────────────


class FakeAdapter:
    harness_name = "claude-code"

    def __init__(self, script: str) -> None:
        self.script = script

    def headless_command(self, request: HeadlessRequest) -> HeadlessPlan:
        return HeadlessPlan(argv=[sys.executable, "-c", self.script], stdin=request.message, session_id="child-1")

    def parse_headless_output(self, plan: HeadlessPlan, stdout: str):
        from observal_cli.harness.base import BaseAdapter

        return BaseAdapter.parse_headless_output(self, plan, stdout)


def _local_task(cwd: Path) -> dict:
    task = tasks.new_task(
        message="add a test",
        observal={"target": AGENT_URN, "kind": "agent", "agentId": AGENT_ID, "harness": "claude-code", "cwd": str(cwd)},
    )
    return tasks.save(task)


def _fake_materialize(task, ws, *, harness, adapter):
    (ws.path / "AGENT.md").write_text("agent profile")
    return "reviewer", {}, "Review carefully."


@pytest.mark.skipif(not HAS_GIT, reason="git not installed")
def test_local_run_returns_answer_and_patch_without_touching_the_caller(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    script = textwrap.dedent(
        """
        import os, sys
        brief = sys.stdin.read()
        open("app.py", "w").write("print('fixed')\\n")
        print("did it: " + brief + " depth=" + os.environ["OBSERVAL_DELEGATION_DEPTH"])
        """
    )
    monkeypatch.setattr(local, "get_adapter", lambda _h: FakeAdapter(script))
    monkeypatch.setattr(local, "materialize", _fake_materialize)
    task = local.run(_local_task(repo), save=tasks.save, should_cancel=lambda: False)

    assert task["status"]["state"] == tasks.STATE_COMPLETED, task["status"]
    result, patch = task["artifacts"]
    assert tasks.message_text(result) == "did it: add a test depth=1"
    assert patch["name"] == "changes.patch" and "print('fixed')" in tasks.message_text(patch)
    assert "AGENT.md" not in tasks.message_text(patch)
    m = tasks.meta(task)
    assert Path(m["patchPath"]).is_file() and m["childSessionId"] == "child-1"
    assert (repo / "app.py").read_text() == "print('v1')\n"


@pytest.mark.skipif(not HAS_GIT, reason="git not installed")
def test_child_sessions_stay_attributed_after_the_workspace_is_gone(tmp_path, monkeypatch):
    from observal_cli.sessions.base import _resolve_agent

    repo = _repo(tmp_path)
    (repo / "pkg").mkdir()
    (repo / "pkg" / "mod.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "pkg")
    monkeypatch.setattr(local, "get_adapter", lambda _h: FakeAdapter("import os; print(os.getcwd())"))
    monkeypatch.setattr(local, "materialize", _fake_materialize)
    monkeypatch.delenv("OBSERVAL_AGENT_ID", raising=False)
    task = _local_task(repo / "pkg")  # a caller in a subdirectory works in the same subdirectory
    tasks.meta(task)["version"] = "1.2.0"
    task = local.run(tasks.save(task), save=tasks.save, should_cancel=lambda: False)

    # Deliveries after cleanup: no hook env, no lockfile entry, the workspace is gone.
    child_cwd = tasks.message_text(task["artifacts"][0]).strip()
    assert not Path(child_cwd).exists()
    expected = (AGENT_ID, "1.2.0")
    assert _resolve_agent(child_cwd, [], None, harness="claude-code") == expected  # hook: cwd
    assert _resolve_agent(str(Path(child_cwd) / "src"), [], None, harness="kiro") == expected
    assert _resolve_agent("", [], None, session_ids=("child-1", None)) == expected  # reconcile: id only
    assert _resolve_agent("", [], None, session_ids=("sub-7", "child-1")) == expected  # the child's subagent
    assert tasks.delegated_agent(session_ids=("other", "../child-1")) is None
    assert tasks.delegated_agent(str(tmp_path / "observal-delegate-lookalike" / "worktree")) is None


@pytest.mark.skipif(not HAS_GIT, reason="git not installed")
def test_harness_rewriting_its_own_config_stays_out_of_the_patch(tmp_path, monkeypatch):
    # OpenCode adds "$schema" to the opencode.json the install wrote when it starts.
    repo = _repo(tmp_path)
    script = textwrap.dedent(
        """
        open("AGENT.md", "a").write("rewritten by the harness\\n")
        open("app.py", "w").write("print('fixed')\\n")
        print("done")
        """
    )
    monkeypatch.setattr(local, "get_adapter", lambda _h: FakeAdapter(script))
    monkeypatch.setattr(local, "materialize", _fake_materialize)
    task = local.run(_local_task(repo), save=tasks.save, should_cancel=lambda: False)

    assert task["status"]["state"] == tasks.STATE_COMPLETED, task["status"]
    patch = tasks.message_text(task["artifacts"][1])
    assert "app.py" in patch and "AGENT.md" not in patch
    applied = subprocess.run(["git", "-C", str(repo), "apply", "--check", "-"], input=patch.encode(), check=False)
    assert applied.returncode == 0


@pytest.mark.skipif(not HAS_GIT, reason="git not installed")
def test_local_run_can_be_canceled(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(local, "get_adapter", lambda _h: FakeAdapter("import time; time.sleep(60)"))
    monkeypatch.setattr(local, "materialize", _fake_materialize)
    started = time.monotonic()
    calls = {"n": 0}

    def cancel_soon():
        calls["n"] += 1
        return calls["n"] > 1

    task = local.run(_local_task(repo), save=tasks.save, should_cancel=cancel_soon)
    assert task["status"]["state"] == tasks.STATE_CANCELED
    assert time.monotonic() - started < 30


@pytest.mark.skipif(not HAS_GIT, reason="git not installed")
def test_local_run_reports_a_failing_child(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    script = "import sys; sys.stderr.write('model quota exceeded'); sys.exit(3)"
    monkeypatch.setattr(local, "get_adapter", lambda _h: FakeAdapter(script))
    monkeypatch.setattr(local, "materialize", _fake_materialize)
    task = local.run(_local_task(repo), save=tasks.save, should_cancel=lambda: False)
    assert task["status"]["state"] == tasks.STATE_FAILED
    assert "model quota exceeded" in tasks.message_text(task["status"]["message"])


@pytest.mark.skipif(not HAS_GIT, reason="git not installed")
def test_local_run_with_no_answer_and_no_changes_fails_even_on_exit_zero(tmp_path, monkeypatch):
    # Kiro prints an expired-login error to stderr and exits 0.
    repo = _repo(tmp_path)
    script = "import sys; sys.stderr.write('\\x1b[38;5;9mAuthentication failed.\\x1b[0m')"
    monkeypatch.setattr(local, "get_adapter", lambda _h: FakeAdapter(script))
    monkeypatch.setattr(local, "materialize", _fake_materialize)
    task = local.run(_local_task(repo), save=tasks.save, should_cancel=lambda: False)
    assert task["status"]["state"] == tasks.STATE_FAILED
    assert tasks.message_text(task["status"]["message"]) == (
        "claude-code, the harness the agent ran in, reported: Authentication failed."
    )


def test_command_line_guard_refuses_what_the_os_would_mangle(monkeypatch):
    too_long = "x" * (local.MAX_ARG_BYTES + 1)
    # The advice names the harnesses whose adapters pass the task on stdin.
    with pytest.raises(local.LocalRunError, match=r"too long.*\(Claude Code or Pi\)"):
        local.check_command_line(["codex", too_long], "/usr/bin/codex", "codex", prompt_in_argv=True)
    monkeypatch.setattr(local.sys, "platform", "win32")
    with pytest.raises(local.LocalRunError, match="batch file"):
        local.check_command_line(["codex", "task"], r"C:\npm\codex.cmd", "codex", prompt_in_argv=True)
    local.check_command_line(["claude", "-p"], r"C:\npm\claude.cmd", "claude-code", prompt_in_argv=False)
    with pytest.raises(local.LocalRunError, match="too long"):
        local.check_command_line(["claude", "y" * 40_000], r"C:\claude.exe", "claude-code", prompt_in_argv=False)


def test_mcp_servers_come_from_either_snippet_shape():
    ensure_loaded()
    claude = get_adapter("claude-code")
    assert local.mcp_servers_from_snippet({"mcp_config": {"gh": {"command": "gh-mcp"}}}, claude) == {
        "gh": {"command": "gh-mcp"}
    }
    kiro = get_adapter("kiro")
    snippet = {"mcp_config": {"path": ".kiro/settings/mcp.json", "content": {"mcpServers": {"gh": {"command": "x"}}}}}
    assert local.mcp_servers_from_snippet(snippet, kiro) == {"gh": {"command": "x"}}


# ── Service guards ───────────────────────────────────────────────────────


def _agent_entry(**overrides) -> dict:
    entry = {
        "identifier": AGENT_URN,
        "displayName": "Security Reviewer",
        "type": service.MEDIA_AGENT,
        "version": "1.0.0",
        "obs:nativeRef": "acme/security-reviewer@1.0.0",
        "obs:lifecycle": "approved",
        "obs:delegable": True,
        "obs:supportedHarnesses": ["claude-code", "kiro"],
    }
    entry.update(overrides)
    return entry


def test_start_refuses_past_the_depth_limit(monkeypatch):
    monkeypatch.setenv("OBSERVAL_DELEGATION_DEPTH", "2")
    with pytest.raises(service.DelegationError, match="depth limit"):
        service.start(AGENT_URN, "do it")


def test_start_refuses_loops_and_unapproved_agents(monkeypatch):
    monkeypatch.setattr(service.client, "get", lambda *_a, **_k: _agent_entry())
    monkeypatch.setenv("OBSERVAL_DELEGATION_CHAIN", AGENT_URN)
    with pytest.raises(service.DelegationError, match="loop"):
        service.start(AGENT_URN, "do it")
    monkeypatch.delenv("OBSERVAL_DELEGATION_CHAIN")
    with pytest.raises(service.DelegationError, match="loop"):
        service.start(AGENT_URN, "do it", parent_agent_id=AGENT_ID)
    monkeypatch.setattr(service.client, "get", lambda *_a, **_k: _agent_entry(**{"obs:lifecycle": "pending"}))
    with pytest.raises(service.DelegationError, match="not approved"):
        service.start(AGENT_URN, "do it")


def test_choose_harness_runs_in_the_callers_harness(monkeypatch):
    monkeypatch.setattr(service, "headless_harnesses", lambda: ["kiro", "claude-code", "codex"])
    assert service.choose_harness(["claude-code", "kiro"], "kiro") == "kiro"
    assert service.choose_harness([], "codex") == "codex"
    # Only one harness could run it, so there is nothing to choose.
    assert service.choose_harness(["codex"], None) == "codex"
    with pytest.raises(service.DelegationError, match="only kiro, claude-code, codex"):
        service.choose_harness(["goose"], None)
    monkeypatch.setattr(service, "headless_harnesses", lambda: [])
    with pytest.raises(service.DelegationError, match="No harness"):
        service.choose_harness([], None)


def test_choose_harness_never_swaps_the_callers_harness_for_another(monkeypatch):
    # OBS-1407: a Kiro caller and an agent without Kiro support ran in Claude Code, unannounced,
    # and Claude Code's own expired login came back as the task's 401.
    monkeypatch.setattr(service, "headless_harnesses", lambda: ["kiro", "claude-code", "codex"])
    with pytest.raises(service.DelegationError) as refused:
        service.choose_harness(["claude-code", "codex", "goose"], "kiro")
    assert refused.value.message == (
        "You are delegating from kiro, which the agent does not support. Choose the harness to run it in with "
        "--harness (the delegate tool's harness argument): claude-code, codex."
    )
    with pytest.raises(service.DelegationError, match="from pi, which cannot run an agent headless"):
        service.choose_harness([], "pi")
    with pytest.raises(service.DelegationError, match=r"Could not tell which harness.*: kiro, claude-code, codex"):
        service.choose_harness([], None)
    with pytest.raises(service.DelegationError, match=r"from kiro, which the agent does not support. The agent sup"):
        service.choose_harness(["goose"], "kiro")


def test_caller_harness_comes_from_observal_harness_or_the_harness_markers(monkeypatch):
    assert service.caller_harness() is None
    monkeypatch.setenv("KIRO_CLI_IS_CHILD_PROCESS", "1")
    assert service.caller_harness() == "kiro"
    # One harness started inside another: neither is guessed.
    monkeypatch.setenv("CLAUDECODE", "1")
    assert service.caller_harness() is None
    # A delegated child and the observal-agents server name their harness outright.
    monkeypatch.setenv("OBSERVAL_HARNESS", "codex")
    assert service.caller_harness() == "codex"
    monkeypatch.setenv("OBSERVAL_HARNESS", "not-a-harness")
    assert service.caller_harness() is None


def test_delegate_run_passes_the_callers_harness(monkeypatch):
    from typer.testing import CliRunner

    from observal_cli.main import app

    captured = {}

    def fake_start(agent, message, **kwargs):
        captured.update(kwargs)
        return tasks.new_task(message=message, observal={"target": agent})

    monkeypatch.setattr(service, "start", fake_start)
    monkeypatch.setenv("KIRO_CLI_IS_CHILD_PROCESS", "1")
    out = CliRunner().invoke(app, ["delegate", "run", "acme/reviewer", "review it", "--output", "json"])
    assert out.exit_code == 0, out.output
    assert captured["parent_harness"] == "kiro" and captured["harness"] is None


def test_choose_harness_never_swaps_an_explicit_harness(monkeypatch):
    monkeypatch.setattr(service, "headless_harnesses", lambda: ["claude-code", "pi"])
    assert service.choose_harness(["claude-code", "pi"], "claude-code", required="pi") == "pi"
    with pytest.raises(service.DelegationError, match="supports claude-code, not pi"):
        service.choose_harness(["claude-code"], None, required="pi")
    with pytest.raises(service.DelegationError, match="kiro cannot run an agent headless"):
        service.choose_harness([], None, required="kiro")


def test_headless_not_ready_asks_the_harness_its_own_login_command(monkeypatch):
    from observal_cli.harness import base

    ensure_loaded()
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if "TIMEOUT" in os.environ:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return subprocess.CompletedProcess(argv, int(os.environ.get("RC", "0")), b"", b"")

    monkeypatch.setattr(base.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(base.subprocess, "run", fake_run)
    kiro, claude = get_adapter("kiro"), get_adapter("claude-code")
    assert kiro.headless_not_ready() is None and claude.headless_not_ready() is None
    assert calls == [["/bin/kiro-cli", "whoami"], ["/bin/claude", "auth", "status"]]
    monkeypatch.setenv("RC", "1")
    assert "kiro-cli login" in kiro.headless_not_ready()
    calls.clear()
    monkeypatch.setenv("KIRO_API_KEY", "k")  # headless API-key auth needs no login, so the check is not asked
    assert kiro.headless_not_ready() is None and calls == []
    monkeypatch.delenv("KIRO_API_KEY")
    assert "claude auth login" in claude.headless_not_ready()
    # A check that does not answer never blocks; a harness with no reliable check is never asked.
    monkeypatch.setenv("TIMEOUT", "1")
    assert kiro.headless_not_ready() is None
    calls.clear()
    assert get_adapter("codex").headless_not_ready() is None and calls == []


def test_start_refuses_a_harness_that_is_not_logged_in(tmp_path, monkeypatch):
    ensure_loaded()
    monkeypatch.setattr(service, "check_ready", REAL_CHECK_READY)
    monkeypatch.setattr(service.client, "get", lambda *_a, **_k: _agent_entry())
    monkeypatch.setattr(service, "headless_harnesses", lambda: ["kiro"])
    adapter = type(get_adapter("kiro"))
    monkeypatch.setattr(adapter, "headless_not_ready", lambda _self: "Kiro is not logged in.")
    monkeypatch.setattr(service, "SPAWN", lambda _task_id: pytest.fail("a worker started"))
    with pytest.raises(service.DelegationError) as refused:
        service.start(AGENT_URN, "Review src/auth", harness="kiro", cwd=tmp_path)
    assert refused.value.message == (
        "kiro cannot run the delegated agent: Kiro is not logged in. To use another harness, pass --harness."
    )
    assert tasks.list_tasks(10) == []


def test_start_records_the_delegation_and_runs_the_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(service.client, "get", lambda *_a, **_k: _agent_entry())
    monkeypatch.setattr(service, "headless_harnesses", lambda: ["claude-code", "kiro"])

    def fake_worker(task_id):
        task = tasks.load(task_id)
        task["artifacts"] = [tasks.text_artifact("result", "No auth bugs found.")]
        tasks.save(tasks.set_status(task, tasks.STATE_COMPLETED, "Done."))
        return None

    monkeypatch.setattr(service, "SPAWN", fake_worker)
    task = service.start(AGENT_URN, "Review src/auth", parent_harness="kiro", cwd=tmp_path, wait_seconds=5)
    assert task["status"]["state"] == tasks.STATE_COMPLETED
    m = tasks.meta(task)
    assert m["harness"] == "kiro" and m["parentHarness"] == "kiro"
    assert m["depth"] == 0 and m["agentId"] == AGENT_ID
    summary = service.summarize(task)
    assert "No auth bugs found." in summary and "TASK_STATE_COMPLETED" in summary

    uses = capability_lock.read_all()
    assert [(u.kind, u.mode, u.identifier, u.harness) for u in uses] == [("agent", "delegated", AGENT_URN, "kiro")]
    assert uses[0].extra["task_id"] == task["id"]


def test_a_delegated_agent_can_start_only_a_few_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(service.client, "get", lambda *_a, **_k: _agent_entry())
    monkeypatch.setattr(service, "headless_harnesses", lambda: ["claude-code"])
    monkeypatch.setattr(service, "SPAWN", lambda _task_id: None)
    parent = tasks.save(tasks.new_task(message="parent", observal={"target": AGENT_URN}))
    monkeypatch.setenv("OBSERVAL_DELEGATION_TASK_ID", parent["id"])
    for _ in range(service.MAX_CHILD_TASKS):
        child = service.start(AGENT_URN, "sub-task", cwd=tmp_path)
        assert tasks.meta(child)["parentTaskId"] == parent["id"]
    with pytest.raises(service.DelegationError, match="at most"):
        service.start(AGENT_URN, "one more", cwd=tmp_path)


def test_dead_worker_is_reported_instead_of_hanging(monkeypatch, tmp_path):
    task = tasks.save(tasks.new_task(message="x", observal={"target": AGENT_URN}))
    tasks.task_dir(task["id"]).joinpath("worker.pid").write_text("999999")
    monkeypatch.setattr(service, "_pid_alive", lambda _pid: False)
    assert service.get(task["id"])["status"]["state"] == tasks.STATE_FAILED


@pytest.mark.skipif(not HAS_GIT or sys.platform == "win32", reason="needs git and POSIX process groups")
def test_a_dead_workers_child_and_worktree_are_cleaned_up(tmp_path):
    repo = _repo(tmp_path)
    ws = workspace.create(repo)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=ws.path, start_new_session=True)
    task = tasks.new_task(message="x", observal={"target": AGENT_URN, "childPid": child.pid})
    tasks.record_workspace(task, ws.root)
    tasks.save(task)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    tasks.task_dir(task["id"]).joinpath("worker.pid").write_text(str(dead.pid))

    service._reap_abandoned()  # what the next delegation does before it starts

    assert tasks.load(task["id"])["status"]["state"] == tasks.STATE_FAILED
    assert child.wait(timeout=10) != 0
    assert not ws.root.exists()


@pytest.mark.skipif(not HAS_GIT, reason="git not installed")
def test_guards_hold_when_the_harness_drops_the_environment(tmp_path, monkeypatch):
    # Codex starts MCP servers with an allowlisted environment; the workspace still says where we are.
    ws = workspace.create(_repo(tmp_path))
    try:
        task = tasks.new_task(
            message="x", observal={"target": AGENT_URN, "agentId": AGENT_ID, "depth": 1, "chain": ["urn:first"]}
        )
        tasks.record_workspace(task, ws.root)
        tasks.save(task)
        for name in ("OBSERVAL_DELEGATION_DEPTH", "OBSERVAL_DELEGATION_CHAIN", "OBSERVAL_DELEGATION_TASK_ID"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv("OBSERVAL_AGENT_ID", raising=False)
        monkeypatch.chdir(ws.path)
        assert service.current_depth() == 2
        assert service.current_chain() == ["urn:first", AGENT_URN, AGENT_ID]
        assert service.parent_task_id() == task["id"]
        assert service.parent_agent_id() == AGENT_ID
    finally:
        workspace.destroy(ws)


@pytest.mark.skipif(not HAS_GIT or sys.platform == "win32", reason="needs git and POSIX processes")
def test_a_child_that_cannot_be_verified_keeps_its_workspace_until_it_exits(tmp_path, monkeypatch):
    ws = workspace.create(_repo(tmp_path))
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=ws.path, start_new_session=True)
    task = tasks.new_task(message="x", observal={"target": AGENT_URN, "childPid": child.pid})
    tasks.record_workspace(task, ws.root)
    monkeypatch.setattr(service, "_ran_in", lambda *_a: False)  # as on macOS and Windows
    service._reap(task)
    assert ws.root.exists() and child.poll() is None  # neither killed nor pulled out from under it
    child.kill()
    child.wait()
    service._reap(task)
    assert not ws.root.exists()


def test_the_wait_budget_counts_from_when_the_call_began(monkeypatch):
    task = tasks.save(tasks.new_task(message="x", observal={"target": AGENT_URN}))
    monkeypatch.setattr(service, "_worker_pid", lambda _id: None)

    def no_sleep(_s):
        raise AssertionError("waited past the caller's budget")

    monkeypatch.setattr(service.time, "sleep", no_sleep)
    # A lookup and launch that took 20 s used up a 10 s budget.
    assert service.wait(task["id"], 10, since=service.time.monotonic() - 20)["id"] == task["id"]


def test_a_worker_that_cannot_start_fails_the_task(monkeypatch):
    def broken(_task_id):
        raise OSError("no python")

    monkeypatch.setattr(service, "SPAWN", broken)
    task = tasks.new_task(message="x", observal={"target": AGENT_URN})
    with pytest.raises(service.DelegationError, match="could not be started"):
        service._launch(task)
    assert tasks.load(task["id"])["status"]["state"] == tasks.STATE_FAILED


# ── Remote A2A ───────────────────────────────────────────────────────────


def _a2a_entry(version="1.0", schemes=None) -> dict:
    return {
        "obs:a2aInterface": {
            "url": "https://agents.acme.com/a2a/v1",
            "protocolBinding": "JSONRPC",
            "protocolVersion": version,
        },
        "obs:agentCard": {"name": "Incident Triage", "securitySchemes": schemes or {}},
    }


def _a2a_task() -> dict:
    return tasks.save(tasks.new_task(message="triage INC-42", observal={"target": A2A_URN, "kind": "a2a"}))


def test_a2a_v1_send_then_poll_until_completed(monkeypatch):
    monkeypatch.setattr(a2a_client, "POLL_SECONDS", 0)
    monkeypatch.setenv("OBSERVAL_A2A_TOKEN_AGENTS_ACME_COM", "tok")
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append({"method": body["method"], "headers": dict(request.headers), "params": body["params"]})
        if body["method"] == "SendMessage":
            task = {"id": "r-1", "contextId": "c-1", "status": {"state": "TASK_STATE_WORKING"}}
        else:
            task = {
                "id": "r-1",
                "contextId": "c-1",
                "status": {"state": "TASK_STATE_COMPLETED"},
                "artifacts": [{"artifactId": "a", "name": "report", "parts": [{"text": "Root cause: bad deploy"}]}],
            }
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"task": task}})

    entry = _a2a_entry(schemes={"bearer": {"httpAuthSecurityScheme": {"scheme": "Bearer"}}})
    task = a2a_client.run(
        _a2a_task(), entry=entry, save=tasks.save, should_cancel=lambda: False, transport=httpx.MockTransport(handler)
    )
    assert task["status"]["state"] == tasks.STATE_COMPLETED
    assert tasks.message_text(task["artifacts"][0]) == "Root cause: bad deploy"
    assert [s["method"] for s in seen] == ["SendMessage", "GetTask"]
    first = seen[0]
    assert first["headers"]["a2a-version"] == "1.0"
    assert first["headers"]["authorization"] == "Bearer tok"
    assert first["params"]["message"]["role"] == "ROLE_USER"
    assert first["params"]["message"]["parts"] == [{"text": "triage INC-42"}]
    assert first["params"]["configuration"] == {"returnImmediately": True}
    assert tasks.meta(task)["remoteTaskId"] == "r-1"


def test_a2a_v03_agent_uses_legacy_methods_and_can_ask_for_input():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body["method"])
        assert body["params"]["message"]["parts"] == [{"kind": "text", "text": "triage INC-42"}]
        result = {
            "kind": "task",
            "id": "r-9",
            "contextId": "c-9",
            "status": {
                "state": "input-required",
                "message": {"role": "agent", "parts": [{"kind": "text", "text": "Which region?"}]},
            },
        }
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    task = a2a_client.run(
        _a2a_task(),
        entry=_a2a_entry(version="0.3.0"),
        save=tasks.save,
        should_cancel=lambda: False,
        transport=httpx.MockTransport(handler),
    )
    assert seen == ["message/send"]
    assert task["status"]["state"] == tasks.STATE_INPUT_REQUIRED
    assert "Which region?" in service.summarize(task)


def test_cancelling_a_task_that_waits_for_input_tells_the_remote_agent(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body["method"])
        task = {"id": "r-9", "status": {"state": "TASK_STATE_CANCELED"}}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"task": task}})

    task = _a2a_task()
    tasks.meta(task)["remoteTaskId"] = "r-9"
    tasks.set_status(task, tasks.STATE_INPUT_REQUIRED, "Which region?")
    tasks.save(task)
    real_cancel = a2a_client.cancel
    monkeypatch.setattr(service.client, "get", lambda *_a, **_k: {**_a2a_entry(), "obs:lifecycle": "approved"})
    monkeypatch.setattr(
        a2a_client, "cancel", lambda t, *, entry: real_cancel(t, entry=entry, transport=httpx.MockTransport(handler))
    )
    canceled = service.cancel(task["id"])
    assert seen == ["CancelTask"]
    assert canceled["status"]["state"] == tasks.STATE_CANCELED
    assert tasks.load(task["id"])["status"]["state"] == tasks.STATE_CANCELED

    # An agent that lost approval is never contacted (no credentials sent); the task still ends.
    seen.clear()
    tasks.save(tasks.set_status(tasks.load(task["id"]), tasks.STATE_INPUT_REQUIRED, "Which region?"))
    monkeypatch.setattr(service.client, "get", lambda *_a, **_k: {**_a2a_entry(), "obs:lifecycle": "rejected"})
    ended = service.cancel(task["id"])
    assert seen == [] and ended["status"]["state"] == tasks.STATE_CANCELED
    assert "not told" in tasks.message_text(ended["status"]["message"])

    # A remote agent that does not confirm is reported, not hidden.
    def refusing(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "error": {"message": "no such task"}})

    again = tasks.set_status(tasks.load(task["id"]), tasks.STATE_INPUT_REQUIRED, "Which region?")
    result = real_cancel(again, entry=_a2a_entry(), transport=httpx.MockTransport(refusing))
    assert "did not confirm" in tasks.message_text(result["status"]["message"])


def test_a_worker_never_acts_on_a_task_that_is_already_settled(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(local, "run", lambda *_a, **_k: calls.append("run"))
    done = tasks.save(
        tasks.set_status(tasks.new_task(message="x", observal={"target": AGENT_URN}), tasks.STATE_CANCELED)
    )
    assert runner.run_task(done["id"])["status"]["state"] == tasks.STATE_CANCELED
    pending = tasks.save(tasks.new_task(message="x", observal={"target": AGENT_URN}))
    tasks.request_cancel(pending["id"])  # canceled while the worker was starting
    assert runner.run_task(pending["id"])["status"]["state"] == tasks.STATE_CANCELED
    assert calls == []


def test_a2a_direct_message_answer_and_errors():
    def message_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        msg = {"messageId": "m", "role": "ROLE_AGENT", "parts": [{"text": "Hi there"}]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"message": msg}})

    task = a2a_client.run(
        _a2a_task(),
        entry=_a2a_entry(),
        save=tasks.save,
        should_cancel=lambda: False,
        transport=httpx.MockTransport(message_handler),
    )
    assert task["status"]["state"] == tasks.STATE_COMPLETED
    assert tasks.message_text(task["artifacts"][0]) == "Hi there"

    def denied(_request):
        return httpx.Response(401, json={})

    task = a2a_client.run(
        _a2a_task(),
        entry=_a2a_entry(),
        save=tasks.save,
        should_cancel=lambda: False,
        transport=httpx.MockTransport(denied),
    )
    assert task["status"]["state"] == tasks.STATE_FAILED
    assert "OBSERVAL_A2A_TOKEN" in tasks.message_text(task["status"]["message"])


def test_api_key_scheme_uses_its_header(monkeypatch):
    monkeypatch.setenv("OBSERVAL_A2A_TOKEN_AGENTS_ACME_COM", "k")
    card = {"securitySchemes": {"key": {"apiKeySecurityScheme": {"location": "header", "name": "X-Agent-Key"}}}}
    assert a2a_client.auth_headers(card, "https://agents.acme.com/a2a") == {"X-Agent-Key": "k"}
    assert a2a_client.auth_headers({"securitySchemes": {}}, "https://agents.acme.com/a2a") == {}


def test_a_token_only_reaches_the_host_it_is_named_for(monkeypatch):
    monkeypatch.setenv("OBSERVAL_A2A_TOKEN_AGENTS_ACME_COM", "acme-secret")
    monkeypatch.setenv("OBSERVAL_A2A_TOKEN", "catch-all")  # no longer read
    card = {"securitySchemes": {"bearer": {"httpAuthSecurityScheme": {"scheme": "Bearer"}}}}
    assert a2a_client.auth_headers(card, "https://agents.acme.com/a2a") == {"Authorization": "Bearer acme-secret"}
    assert a2a_client.auth_headers(card, "https://evil.example/a2a") == {}
    assert a2a_client.auth_headers(card, "https://agents.acme.com.evil.example/a2a") == {}
    assert a2a_client.auth_headers(card, "https://agents-acme.com/a2a") == {}  # "-" and "." never collide


def test_credentials_are_never_sent_over_plain_http(monkeypatch):
    card = {"securitySchemes": {"bearer": {"httpAuthSecurityScheme": {"scheme": "Bearer"}}}}
    monkeypatch.setenv("OBSERVAL_A2A_TOKEN_AGENTS_ACME_COM", "tok")
    monkeypatch.setenv("OBSERVAL_A2A_TOKEN_LOCALHOST", "tok")
    with pytest.raises(a2a_client.A2aError, match="plain http"):
        a2a_client.A2aClient({"url": "http://agents.acme.com/a2a"}, card)
    a2a_client.A2aClient({"url": "http://localhost:9100/a2a"}, card).close()
    monkeypatch.delenv("OBSERVAL_A2A_TOKEN_AGENTS_ACME_COM")
    a2a_client.A2aClient({"url": "http://agents.acme.com/a2a"}, card).close()


def test_runner_rejects_a_remote_agent_that_lost_approval(monkeypatch):
    task = _a2a_task()
    monkeypatch.setattr(runner.client, "get", lambda *_a, **_k: {"obs:lifecycle": "rejected"})
    done = runner.run_task(task["id"])
    assert done["status"]["state"] == tasks.STATE_REJECTED


# ── MCP server ───────────────────────────────────────────────────────────


def _rpc(server: mcp_server.Server, method: str, params: dict | None = None, req_id: int = 1) -> dict:
    return server.handle({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})


def test_mcp_initialize_and_tools(monkeypatch):
    import io

    server = mcp_server.Server(harness="kiro", parent_id=AGENT_ID, out=io.StringIO())
    init = _rpc(server, "initialize", {"protocolVersion": "2025-03-26"})["result"]
    assert init["protocolVersion"] == "2025-03-26" and init["serverInfo"]["name"] == "observal-agents"
    names = [t["name"] for t in _rpc(server, "tools/list")["result"]["tools"]]
    assert names == ["find_agents", "delegate", "get_task", "cancel_task"]
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None

    monkeypatch.setattr(service, "find_agents", lambda text, limit: [{"identifier": AGENT_URN, "name": "Reviewer"}])
    found = _rpc(server, "tools/call", {"name": "find_agents", "arguments": {"task": "review"}})["result"]
    assert found["isError"] is False and AGENT_URN in found["content"][0]["text"]

    captured = {}

    def fake_start(agent, message, **kwargs):
        captured.update(kwargs, agent=agent)
        raise service.DelegationError("Delegation depth limit reached (2).")

    monkeypatch.setattr(service, "start", fake_start)
    refused = _rpc(server, "tools/call", {"name": "delegate", "arguments": {"agent": AGENT_URN, "message": "x"}})
    assert refused["result"]["isError"] is True and "depth limit" in refused["result"]["content"][0]["text"]
    assert captured["parent_harness"] == "kiro" and captured["parent_agent_id"] == AGENT_ID
    assert captured["harness"] is None
    _rpc(
        server,
        "tools/call",
        {"name": "delegate", "arguments": {"agent": AGENT_URN, "message": "x", "harness": "codex"}},
    )
    assert captured["harness"] == "codex"

    missing = _rpc(server, "tools/call", {"name": "delegate", "arguments": {"message": "x"}})["result"]
    assert missing["isError"] is True
    assert _rpc(server, "bogus")["error"]["code"] == -32601


def test_mcp_server_speaks_newline_json_on_stdio(tmp_path):
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(root), str(root / "packages" / "observal-shared")])}
    proc = subprocess.run(
        [sys.executable, "-m", "observal_cli.delegation.mcp_server", "--harness", "claude-code"],
        input="".join(json.dumps(line) + "\n" for line in lines),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=str(tmp_path),
    )
    out = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert [o["id"] for o in out] == [1, 2]
    assert out[0]["result"]["protocolVersion"] == "2025-06-18"
    assert len(out[1]["result"]["tools"]) == 4


# ── Harness adapters ─────────────────────────────────────────────────────


def _request(tmp_path: Path, **overrides) -> HeadlessRequest:
    values = {
        "agent_name": "security-reviewer",
        "instructions": "You review code for auth bugs.",
        "message": "-- review src/auth",
        "workdir": tmp_path,
        "scratch_dir": tmp_path,
        "session_id": "11111111-1111-1111-1111-111111111111",
        "mcp_servers": {"github": {"command": "gh-mcp"}},
    }
    values.update(overrides)
    return HeadlessRequest(**values)


def test_claude_code_headless_command_and_output(tmp_path):
    ensure_loaded()
    adapter = get_adapter("claude-code")
    plan = adapter.headless_command(_request(tmp_path))
    assert plan.argv[:4] == ["claude", "-p", "--agent", "security-reviewer"]
    assert plan.stdin == "-- review src/auth"  # never on argv, so it cannot be read as a flag
    assert adapter.headless_task_on_stdin
    assert "--strict-mcp-config" in plan.argv and "mcp__github" in plan.argv
    assert json.loads((tmp_path / "mcp.json").read_text()) == {"mcpServers": {"github": {"command": "gh-mcp"}}}
    result = adapter.parse_headless_output(
        plan, '{"type":"result","result":"All clear","is_error":false,"session_id":"abc"}\n'
    )
    assert (result.text, result.session_id, result.error) == ("All clear", "abc", None)
    failed = adapter.parse_headless_output(plan, '{"type":"result","result":"","is_error":true}')
    assert failed.error


@pytest.mark.parametrize(
    ("harness", "binary", "inlined"),
    [
        ("kiro", "kiro-cli", False),
        ("cursor", "cursor-agent", True),
        ("codex", "codex", True),
        ("opencode", "opencode", False),
        ("antigravity", "agy", True),
        ("copilot-cli", "copilot", False),
        ("pi", "pi", True),
    ],
)
def test_other_headless_commands(tmp_path, monkeypatch, harness, binary, inlined):
    ensure_loaded()
    adapter = get_adapter(harness)
    _pi_trust(monkeypatch, tmp_path / "home", {str(tmp_path): True})
    plan = adapter.headless_command(_request(tmp_path, source_dir=tmp_path))
    assert plan.argv[0] == binary == adapter.headless_binary
    assert (plan.stdin is not None) is adapter.headless_task_on_stdin
    joined = "\n".join([*plan.argv, plan.stdin or ""])
    assert ("<agent-instructions>" in joined) is inlined
    assert "-- review src/auth" in joined


@pytest.mark.parametrize("harness", ["goose", "copilot"])
def test_harnesses_without_verified_headless_mode_refuse(tmp_path, harness):
    ensure_loaded()
    with pytest.raises(NotSupportedError):
        get_adapter(harness).headless_command(_request(tmp_path))


def _pi_trust(monkeypatch, home: Path, decisions: dict) -> None:
    monkeypatch.setenv("HOME", str(home))
    (home / ".pi" / "agent").mkdir(parents=True, exist_ok=True)
    (home / ".pi" / "agent" / "trust.json").write_text(json.dumps(decisions))


def test_pi_runs_only_where_pi_already_trusts_the_repository(tmp_path, monkeypatch):
    from observal_cli.errors import CliError

    ensure_loaded()
    adapter = get_adapter("pi")
    repo = tmp_path / "code" / "repo"
    repo.mkdir(parents=True)
    _pi_trust(monkeypatch, tmp_path / "home", {})
    with pytest.raises(CliError) as raised:
        adapter.headless_command(_request(tmp_path, source_dir=repo))
    assert "does not trust" in raised.value.message
    _pi_trust(monkeypatch, tmp_path / "home", {str(tmp_path / "code"): True, str(repo): False})
    with pytest.raises(CliError):  # the nearest decision wins
        adapter.headless_command(_request(tmp_path, source_dir=repo))
    _pi_trust(monkeypatch, tmp_path / "home", {str(tmp_path / "code"): True})
    assert "--approve" in adapter.headless_command(_request(tmp_path, source_dir=repo)).argv


def test_pi_runs_the_session_it_was_given(tmp_path, monkeypatch):
    ensure_loaded()
    adapter = get_adapter("pi")
    _pi_trust(monkeypatch, tmp_path / "home", {str(tmp_path): True})
    request = _request(tmp_path, source_dir=tmp_path)
    plan = adapter.headless_command(request)
    assert plan.argv == ["pi", "-p", "--approve", "--session-id", request.session_id]
    assert "-- review src/auth" in plan.stdin  # the task never reaches the command line
    result = adapter.parse_headless_output(plan, "\x1b[1mpong\x1b[0m\n")
    assert result.text == "pong" and result.session_id == request.session_id


def test_codex_answer_is_read_from_its_output_file(tmp_path):
    ensure_loaded()
    adapter = get_adapter("codex")
    plan = adapter.headless_command(_request(tmp_path))
    plan.output_file.write_text("final answer\n")
    assert adapter.parse_headless_output(plan, "noise").text == "final answer"


def test_rewrite_observal_interpreter_handles_entries_and_argv():
    snippet = {
        "mcp_config": {"observal-agents": {"command": "python3", "args": ["-m", "observal_cli.delegation.mcp_server"]}},
        "mcp_setup_commands": [["claude", "mcp", "add", "x", "--", "python3", "-m", "observal_cli.sandbox_mcp"]],
        "other": {"command": "python3", "args": ["-m", "http.server"]},
    }
    out = rewrite_observal_interpreter(snippet)
    assert out["mcp_config"]["observal-agents"]["command"] == sys.executable
    assert out["mcp_setup_commands"][0][5] == sys.executable
    assert out["other"]["command"] == "python3"


def test_delegate_list_shows_ids_that_status_accepts(monkeypatch):
    from typer.testing import CliRunner

    from observal_cli.main import app

    task = tasks.save(tasks.new_task(message="x", observal={"target": AGENT_URN, "targetName": "writer"}))
    monkeypatch.setenv("COLUMNS", "200")
    out = CliRunner().invoke(app, ["delegate", "list"])
    assert out.exit_code == 0, out.output
    assert task["id"] in out.output
