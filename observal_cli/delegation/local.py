# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Run a registry Agent headless on one task.

The agent is installed with project scope into a throwaway workspace (the
same server install call and file writer ``observal agent pull`` uses, minus
setup commands, the lockfile and active-agent state, so the caller's setup is
untouched), then its harness runs one prompt non-interactively. The answer,
the child's session id and the patch of anything it changed become the task's
artifacts.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger as optic

from observal_cli import client
from observal_cli.delegation import tasks, workspace
from observal_cli.errors import CliError
from observal_cli.harness import ensure_loaded, get_adapter
from observal_cli.harness.base import ANSI_RE
from observal_cli.harness.protocol import HeadlessRequest

if TYPE_CHECKING:
    from collections.abc import Callable

DEFAULT_TIMEOUT_SECONDS = 30 * 60
POLL_SECONDS = 1.0
_SAFE_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-_")


class LocalRunError(RuntimeError):
    """A failure whose message is safe to show the calling agent."""


def local_agent_name(slug: str) -> str:
    name = "".join(ch if ch in _SAFE_CHARS else "-" for ch in (slug or "agent").lower()).strip("-")
    return name[:64] or "agent"


def child_environment(task: dict, *, harness: str) -> dict[str, str]:
    """The child inherits the caller's environment plus its place in the delegation chain."""
    m = tasks.meta(task)
    # Both spellings of the child go on the chain, so a loop back to it is caught whichever way it is named.
    chain = [*m.get("chain", []), m.get("target", ""), str(m.get("agentId") or "")]
    env = dict(os.environ)
    for inherited in ("OBSERVAL_AGENT_ID", "OBSERVAL_AGENT_VERSION"):  # the caller's own, when it is a child
        env.pop(inherited, None)
    env.update(
        {
            "OBSERVAL_DELEGATION_DEPTH": str(int(m.get("depth", 0)) + 1),
            "OBSERVAL_DELEGATION_CHAIN": ",".join(c for c in chain if c),
            "OBSERVAL_DELEGATION_TASK_ID": task["id"],
            "OBSERVAL_HARNESS": harness,
        }
    )
    if m.get("agentId"):
        env["OBSERVAL_AGENT_ID"] = str(m["agentId"])
    if m.get("version"):
        env["OBSERVAL_AGENT_VERSION"] = str(m["version"])  # read by harness extensions that attribute sessions
    return env


def mcp_servers_from_snippet(snippet: dict, adapter) -> dict:
    """MCP servers the materialized agent declares, whichever way its harness receives them."""
    mcp_cfg = snippet.get("mcp_config")
    if not isinstance(mcp_cfg, dict):
        return {}
    if "path" in mcp_cfg:
        content = mcp_cfg.get("content")
        return dict(adapter.extract_mcp_servers(content)) if isinstance(content, dict) else {}
    return dict(mcp_cfg)


def _terminate(proc: subprocess.Popen) -> None:
    """Stop the child and everything it started."""
    if proc.poll() is not None:
        return
    with contextlib.suppress(OSError, ProcessLookupError):
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError, ProcessLookupError):
            if sys.platform != "win32":
                os.killpg(proc.pid, signal.SIGKILL)
            proc.kill()


# Linux caps one argument at 128 KiB (MAX_ARG_STRLEN); Windows caps a whole command line
# at 32,767 characters. Harnesses that read the task from stdin avoid both.
MAX_ARG_BYTES = 120_000
MAX_WINDOWS_COMMAND_CHARS = 30_000


def _stdin_harnesses() -> str:
    from observal_cli.harness import ensure_loaded, get_all_adapters
    from observal_shared.harness_registry import HARNESS_REGISTRY

    ensure_loaded()
    names = [HARNESS_REGISTRY[n]["display_name"] for n, a in get_all_adapters().items() if a.headless_task_on_stdin]
    return " or ".join(names)


def check_command_line(argv: list[str], binary: str, harness: str, *, prompt_in_argv: bool) -> None:
    """Refuse a launch the OS would reject, or one that passes the task through cmd.exe."""
    if sys.platform == "win32":
        if prompt_in_argv and Path(binary).suffix.lower() in {".cmd", ".bat"}:
            raise LocalRunError(
                f"{harness} is installed as a batch file ({Path(binary).name}), so on Windows cmd.exe would parse "
                f"the task text. Delegate to a harness that reads the task from stdin ({_stdin_harnesses()})."
            )
        too_long = sum(len(arg) + 3 for arg in argv) > MAX_WINDOWS_COMMAND_CHARS
    else:
        too_long = max((len(arg.encode("utf-8")) for arg in argv), default=0) > MAX_ARG_BYTES
    if too_long:
        raise LocalRunError(
            f"The task and the agent's instructions are too long for the {harness} command line. "
            f"Shorten the task or delegate to a harness that reads it from stdin ({_stdin_harnesses()})."
        )


def materialize(task: dict, ws: workspace.Workspace, *, harness: str, adapter) -> tuple[str, dict, str]:
    """Install the agent into the workspace. Returns (local name, mcp servers, instructions)."""
    from observal_cli.cmd_pull import rewrite_observal_interpreter, write_install_snippet

    m = tasks.meta(task)
    agent_id = str(m["agentId"])
    detail = client.get(f"/api/v1/agents/{agent_id}", operation="Delegate to agent", resource=agent_id)
    name = local_agent_name(detail.get("slug") or detail.get("name") or "agent")
    body: dict = {
        "harness": harness,
        "env_values": {},
        "header_values": {},
        "options": {"scope": "project", "local_name": name},
        "platform": sys.platform,
        "supported_features": ["skill_extra_files_v1"],
    }
    if m.get("version"):
        body["version"] = m["version"]
    result = client.post_public(
        f"/api/v1/agents/{agent_id}/install", body, operation="Delegate to agent", resource=agent_id
    )
    snippet = rewrite_observal_interpreter(result.get("config_snippet") or {})
    if not snippet:
        raise LocalRunError("The server returned an empty agent configuration.")
    with redirect_stdout(io.StringIO()):
        _written, failed = write_install_snippet(
            snippet,
            harness=harness,
            adapter=adapter,
            target_dir=ws.path,
            agent_id=agent_id,
            is_user_scope=False,
            quiet=True,
            skill_bundles=result.get("skill_bundles"),
            lock=result.get("lock"),
        )
    if failed:
        ws.notes.append(f"Skills that could not be installed for the delegated agent: {', '.join(failed)}.")
        raise LocalRunError(
            "Required agent skills did not install; refusing to launch a partially configured delegate."
        )
    m["agentName"] = detail.get("name") or name
    # The version the install resolved, so the child's sessions carry what actually ran.
    m["version"] = result.get("version") or m.get("version") or detail.get("version")
    return name, mcp_servers_from_snippet(snippet, adapter), str(detail.get("prompt") or "")


def run(
    task: dict,
    *,
    save: Callable[[dict], object],
    should_cancel: Callable[[], bool],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """Drive one local delegation to a terminal state. Always returns the task."""
    ensure_loaded()
    m = tasks.meta(task)
    harness = m["harness"]
    adapter = get_adapter(harness)
    message = tasks.message_text((task.get("history") or [{}])[0])

    ws: workspace.Workspace | None = None
    try:
        ws = workspace.create(Path(m["cwd"]))
        tasks.record_workspace(task, ws.root)
        save(task)
        name, servers, instructions = materialize(task, ws, harness=harness, adapter=adapter)
        workspace.mark_baseline(ws)
        request = HeadlessRequest(
            agent_name=name,
            instructions=instructions,
            message=message,
            workdir=ws.path,
            scratch_dir=ws.scratch,
            session_id=str(uuid.uuid4()),
            mcp_servers=servers,
            source_dir=Path(m["cwd"]),
        )
        plan = adapter.headless_command(request)
        binary = shutil.which(plan.argv[0])
        if binary is None:
            raise LocalRunError(f"{plan.argv[0]} is not on PATH; install {harness} or choose another harness.")
        check_command_line(plan.argv, binary, harness, prompt_in_argv=plan.stdin is None)

        log_dir = tasks.task_dir(task["id"])
        stdout_path = log_dir / "stdout.log"
        stderr_path = log_dir / "stderr.log"
        m["workspace"] = "git-worktree" if ws.is_git else "empty"
        tasks.record_child_session(task, plan.session_id)
        tasks.set_status(task, tasks.STATE_WORKING, f"Running {m.get('agentName', name)} in {harness}.")
        save(task)

        popen_kwargs: dict = {}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
            proc = subprocess.Popen(
                [binary, *plan.argv[1:]],
                cwd=str(ws.path),
                stdin=subprocess.PIPE if plan.stdin is not None else subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                env=child_environment(task, harness=harness),
                **popen_kwargs,
            )
            m["childPid"] = proc.pid
            save(task)
            if plan.stdin is not None and proc.stdin is not None:
                with contextlib.suppress(BrokenPipeError, OSError):
                    proc.stdin.write(plan.stdin.encode("utf-8"))
                    proc.stdin.close()
            deadline = time.monotonic() + timeout
            outcome = None
            while True:
                try:
                    proc.wait(timeout=POLL_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if should_cancel():
                    outcome = tasks.STATE_CANCELED
                elif time.monotonic() > deadline:
                    outcome = tasks.STATE_FAILED
                if outcome:
                    _terminate(proc)
                    break
        m.pop("childPid", None)

        stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
        if outcome == tasks.STATE_CANCELED:
            return tasks.set_status(task, tasks.STATE_CANCELED, "Canceled by the caller.")
        if outcome == tasks.STATE_FAILED:
            return tasks.set_status(task, tasks.STATE_FAILED, f"Timed out after {int(timeout)} seconds.")

        result = adapter.parse_headless_output(plan, stdout)
        tasks.record_child_session(task, result.session_id)
        patch = workspace.changes(ws)
        artifacts: list[dict] = []
        if result.text:
            artifacts.append(tasks.text_artifact("result", result.text))
        if patch:
            patch_path = log_dir / "changes.patch"
            patch_path.write_text(patch, encoding="utf-8")
            m["patchPath"] = str(patch_path)
            artifacts.append(tasks.text_artifact("changes.patch", patch, media_type="text/x-diff"))
        task["artifacts"] = artifacts
        if ws.notes:
            m["notes"] = ws.notes

        # Some harness CLIs exit 0 after an auth or quota error, printing only to
        # stderr; a run with no answer and no changes did not do the task.
        produced = bool(result.text.strip() or patch)
        if proc.returncode != 0 or result.error or not produced:
            stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
            tail = ANSI_RE.sub("", stderr).strip()[-800:]
            fallback = (
                f"{harness} exited with code {proc.returncode}."
                if proc.returncode != 0
                else f"{harness} returned no answer and made no changes."
            )
            return tasks.set_status(task, tasks.STATE_FAILED, result.error or tail or fallback)
        return tasks.set_status(
            task, tasks.STATE_COMPLETED, "Done." if not patch else "Done. Proposed changes attached."
        )
    except LocalRunError as exc:
        return tasks.set_status(task, tasks.STATE_FAILED, str(exc))
    except workspace.WorkspaceError as exc:
        return tasks.set_status(task, tasks.STATE_FAILED, f"Could not prepare the delegated workspace: {exc}")
    except CliError as exc:  # server and client failures carry a message safe to show
        return tasks.set_status(task, tasks.STATE_FAILED, exc.message)
    except Exception:
        optic.exception("local delegation failed task={}", task.get("id"))
        return tasks.set_status(task, tasks.STATE_FAILED, "The delegated run failed unexpectedly; see the task logs.")
    finally:
        if ws is not None:
            workspace.destroy(ws)
