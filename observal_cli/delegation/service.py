# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Find agents to delegate to, start a delegation, and follow it.

Used by ``observal delegate`` and by the observal-agents MCP server. Every
start is checked before anything runs:

- only approved, delegable entries (``obs:delegable`` from ARD search);
- depth: a delegated agent may delegate again, up to
  ``OBSERVAL_DELEGATION_MAX_DEPTH`` levels (default 2);
- cycles: an agent already in the chain, or the caller itself, is refused.

The work itself runs in a detached worker (``delegation.runner``) so a task
outlives the MCP call or CLI process that started it; callers wait as long as
they like and pick the result up later by task id.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger as optic

from observal_cli import capability_lock, client
from observal_cli.delegation import tasks
from observal_cli.errors import CliError

if TYPE_CHECKING:
    from collections.abc import Callable

MEDIA_AGENT = "application/vnd.observal.agent+json"
MEDIA_A2A = "application/a2a-agent-card+json"
DEFAULT_MAX_DEPTH = 2
MAX_WAIT_SECONDS = 30 * 60
WAIT_POLL_SECONDS = 0.5
MAX_MESSAGE_CHARS = 100_000
# A delegated agent runs headless with no permission prompt, so its fan-out is capped.
MAX_CHILD_TASKS = 3

# Tests replace this to run the worker inline instead of detaching a process.
SPAWN: Callable[[str], int | None] | None = None


class DelegationError(Exception):
    """A refusal or failure whose message is safe to show the calling agent."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# ── Chain ────────────────────────────────────────────────────────────────


def _workspace_delegation() -> dict | None:
    """The delegated task this process runs inside, found from its working directory.

    The delegation passes its place in the chain to the child in environment
    variables, but some harnesses start MCP servers with only an allowlisted
    environment (Codex). The workspace is the fallback, so the depth, cycle and
    breadth guards hold whatever the harness forwards.
    """
    try:
        return tasks.delegation_at(str(Path.cwd()))
    except OSError:
        return None


def current_depth() -> int:
    if "OBSERVAL_DELEGATION_DEPTH" not in os.environ:
        m = _workspace_delegation()
        return int(m.get("depth", 0)) + 1 if m else 0
    try:
        return max(0, int(os.environ["OBSERVAL_DELEGATION_DEPTH"]))
    except ValueError:
        return 0


def parent_task_id() -> str | None:
    if os.environ.get("OBSERVAL_DELEGATION_TASK_ID"):
        return os.environ["OBSERVAL_DELEGATION_TASK_ID"]
    m = _workspace_delegation()
    return m.get("taskId") if m else None


def parent_agent_id() -> str | None:
    """The agent this process runs as when it is a delegated child."""
    if os.environ.get("OBSERVAL_AGENT_ID"):
        return os.environ["OBSERVAL_AGENT_ID"]
    m = _workspace_delegation()
    return str(m["agentId"]) if m and m.get("agentId") else None


def max_depth() -> int:
    try:
        return max(0, int(os.environ.get("OBSERVAL_DELEGATION_MAX_DEPTH", str(DEFAULT_MAX_DEPTH))))
    except ValueError:
        return DEFAULT_MAX_DEPTH


def current_chain() -> list[str]:
    if "OBSERVAL_DELEGATION_CHAIN" not in os.environ:
        m = _workspace_delegation()
        if not m:
            return []
        return [c for c in [*m.get("chain", []), m.get("target"), m.get("agentId")] if c]
    return [c for c in os.environ["OBSERVAL_DELEGATION_CHAIN"].split(",") if c]


# ── Find ─────────────────────────────────────────────────────────────────


def _kind(entry: dict) -> str:
    return "a2a" if entry.get("type") == MEDIA_A2A else "agent"


def find_agents(text: str, *, limit: int = 5) -> list[dict]:
    """Approved agents (registry and remote A2A) that can take a task, best match first."""
    text = (text or "").strip()
    if not text:
        raise DelegationError("Describe the task to find an agent for it.")
    body = {
        "query": {"text": text[:2000], "filter": {"type": [MEDIA_AGENT, MEDIA_A2A], "obs:lifecycle": ["approved"]}},
        "federation": "none",
        "pageSize": min(100, max(limit * 3, 10)),
    }
    data = client.post("/api/v1/ard/search", body, operation="Find agents to delegate to", resource="discovery search")
    results = [r for r in (data.get("results") or []) if r.get("obs:delegable")]
    return [
        {
            "identifier": r.get("identifier"),
            "name": r.get("displayName"),
            "kind": _kind(r),
            "description": r.get("description"),
            "score": r.get("score"),
            "version": r.get("version"),
            "harnesses": r.get("obs:supportedHarnesses") or [],
            "ref": r.get("obs:nativeRef"),
            "recommended": bool(r.get("obs:recommended")),
        }
        for r in results[:limit]
    ]


# ── Resolve ──────────────────────────────────────────────────────────────


def _entry_for(reference: str) -> dict:
    ref = reference.strip()
    if ref.startswith(("urn:air:", "urn:ai:")):
        return client.get(f"/api/v1/ard/entries/{ref}", operation="Delegate to agent", resource=ref)
    # A registry reference (namespace/slug, UUID, alias): shape the agent detail like an entry.
    agent_id = client.resolve_registry_reference("agent", ref)
    detail = client.get(f"/api/v1/agents/{agent_id}", operation="Delegate to agent", resource=ref)
    status = str(detail.get("status") or "")
    return {
        "identifier": None,
        "displayName": detail.get("name"),
        "type": MEDIA_AGENT,
        "version": detail.get("latest_approved_version") or detail.get("version"),
        "obs:kind": "agent",
        "obs:nativeRef": f"{detail.get('qualified_name') or ref}@{detail.get('version', '')}",
        "obs:lifecycle": "approved" if status == "approved" or detail.get("latest_approved_version") else status,
        "obs:supportedHarnesses": detail.get("supported_harnesses") or detail.get("inferred_supported_harnesses") or [],
        "obs:delegable": True,
        "_agentId": str(detail.get("id") or agent_id),
    }


def _agent_id(entry: dict) -> str | None:
    if entry.get("_agentId"):
        return entry["_agentId"]
    identifier = entry.get("identifier") or ""
    parts = identifier.split(":")
    if len(parts) == 5 and parts[3] == "agent":
        return parts[4]
    return None


def headless_harnesses() -> list[str]:
    """Harnesses that can run a delegated agent on this machine right now."""
    from observal_cli.harness import ensure_loaded, get_all_adapters
    from observal_shared.harness_registry import HARNESS_REGISTRY

    ensure_loaded()
    out = []
    for name, adapter in get_all_adapters().items():
        binary = getattr(adapter, "headless_binary", None)
        if HARNESS_REGISTRY.get(name, {}).get("headless_run") and binary and shutil.which(binary):
            out.append(name)
    return out


def caller_harness() -> str | None:
    """The harness this process was started from, or None when that cannot be told.

    ``OBSERVAL_HARNESS`` wins (a delegated child and the observal-agents server
    carry it); otherwise the harness whose own environment markers are set. Markers
    of several harnesses mean one was started inside another, so none is guessed.
    """
    from observal_cli.harness import ensure_loaded, get_all_adapters
    from observal_shared.harness_registry import HARNESS_REGISTRY

    named = (os.environ.get("OBSERVAL_HARNESS") or "").strip()
    if named in HARNESS_REGISTRY:
        return named
    ensure_loaded()
    found = [
        name
        for name, adapter in get_all_adapters().items()
        if any(os.environ.get(marker) for marker in adapter.caller_env_markers)
    ]
    return found[0] if len(found) == 1 else None


def choose_harness(supported: list[str], preferred: str | None, *, required: str | None = None) -> str:
    """Pick the harness a delegated agent runs in.

    ``required`` (an explicit ``--harness``) is used as given or the delegation
    fails. Otherwise the caller's own harness (``preferred``) runs it. When the
    caller's harness cannot, or the caller is unknown and several harnesses could,
    the delegation fails and names the choices: a harness nobody picked fails
    with its own login and model errors, which read as the caller's.
    """
    available = headless_harnesses()
    if required:
        if required not in available:
            raise DelegationError(
                f"{required} cannot run an agent headless on this machine."
                + (f" Usable here: {', '.join(available)}." if available else "")
            )
        if supported and required not in supported:
            raise DelegationError(f"The agent supports {', '.join(supported)}, not {required}.")
        return required
    if not available:
        from observal_shared.harness_registry import HARNESS_REGISTRY, get_harnesses_with_fact

        names = ", ".join(HARNESS_REGISTRY[h]["display_name"] for h in get_harnesses_with_fact("headless_run"))
        raise DelegationError(f"No harness on this machine can run an agent headless. Install one of: {names}.")
    usable = [h for h in available if not supported or h in supported]
    if preferred in usable:
        return preferred
    if not preferred and len(usable) == 1:
        return usable[0]
    if preferred and supported and preferred not in supported:
        problem = f"You are delegating from {preferred}, which the agent does not support."
    elif preferred:
        problem = f"You are delegating from {preferred}, which cannot run an agent headless on this machine."
    else:
        problem = "Could not tell which harness you are delegating from."
    if usable:
        raise DelegationError(
            f"{problem} Choose the harness to run it in with --harness (the delegate tool's harness argument): "
            f"{', '.join(usable)}."
        )
    unsupported = f"The agent supports {', '.join(supported)}, but only {', '.join(available)} can run headless here."
    raise DelegationError(f"{problem} {unsupported}" if preferred else unsupported)


# ── Worker ───────────────────────────────────────────────────────────────


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _spawn(task_id: str) -> int | None:
    if SPAWN is not None:
        return SPAWN(task_id)
    log = tasks.task_dir(task_id) / "worker.log"
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    else:
        kwargs["start_new_session"] = True
    with log.open("ab") as handle:
        proc = subprocess.Popen(
            [sys.executable, "-m", "observal_cli.delegation.runner", task_id],
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            close_fds=True,
            **kwargs,
        )
    return proc.pid


def _worker_pid(task_id: str) -> int | None:
    try:
        return int((tasks.store_dir() / task_id / "worker.pid").read_text().strip())
    except (OSError, ValueError):
        return None


def _launch(task: dict) -> None:
    tasks.clear_cancel(task["id"])
    # A previous worker's pid (a reply resumes a task) must not make the new, not yet started
    # worker look dead to a concurrent get().
    with contextlib.suppress(FileNotFoundError):
        (tasks.store_dir() / task["id"] / "worker.pid").unlink()
    tasks.save(task)
    try:
        pid = _spawn(task["id"])
    except OSError as exc:
        tasks.save(tasks.set_status(task, tasks.STATE_FAILED, "The delegation worker could not be started."))
        raise DelegationError(f"The delegation worker could not be started: {exc}") from None
    if pid:
        # A side file, not the task: the worker owns the task file once it starts. The worker
        # also writes it first thing, in case this process dies before getting here.
        tasks.task_dir(task["id"]).joinpath("worker.pid").write_text(str(pid), encoding="utf-8")


# ── Start, follow, cancel ────────────────────────────────────────────────


def start(
    target: str,
    message: str,
    *,
    harness: str | None = None,
    parent_harness: str | None = None,
    parent_agent_id: str | None = None,
    cwd: str | Path | None = None,
    wait_seconds: float = 0,
) -> dict:
    started = time.monotonic()
    message = (message or "").strip()
    if not message:
        raise DelegationError("A delegation needs a message describing the task.")
    if len(message) > MAX_MESSAGE_CHARS:
        raise DelegationError(f"The message is longer than {MAX_MESSAGE_CHARS} characters.")
    depth = current_depth()
    if depth >= max_depth():
        raise DelegationError(f"Delegation depth limit reached ({max_depth()}). Do this part of the task yourself.")
    parent_task = parent_task_id()
    if parent_task and tasks.count_children(parent_task) >= MAX_CHILD_TASKS:
        raise DelegationError(
            f"A delegated agent can start at most {MAX_CHILD_TASKS} tasks of its own. Do the rest of this task yourself."
        )

    entry = _entry_for(target)
    if entry.get("obs:lifecycle") != "approved":
        raise DelegationError(f"{entry.get('displayName') or target} is not approved, so it cannot be delegated to.")
    if not entry.get("obs:delegable", True):
        raise DelegationError(f"{entry.get('displayName') or target} cannot take delegated tasks.")

    kind = _kind(entry)
    identifier = entry.get("identifier") or ""
    agent_id = _agent_id(entry)
    chain = current_chain()
    if (identifier and identifier in chain) or (agent_id and (agent_id == parent_agent_id or agent_id in chain)):
        raise DelegationError(
            f"{entry.get('displayName') or target} is already working on this task higher up the chain; "
            "delegating to it again would loop."
        )

    observal: dict = {
        "target": identifier or entry.get("obs:nativeRef") or target,
        "targetName": entry.get("displayName"),
        "kind": kind,
        "version": entry.get("version"),
        "nativeRef": entry.get("obs:nativeRef"),
        "parentHarness": parent_harness,
        "parentAgentId": parent_agent_id,
        "parentTaskId": parent_task,
        "depth": depth,
        "chain": chain,
        "cwd": str(Path(cwd or Path.cwd()).resolve()),
    }
    if kind == "agent":
        if not agent_id:
            raise DelegationError("Could not tell which registry agent that identifier names.")
        observal["agentId"] = agent_id
        observal["harness"] = choose_harness(
            list(entry.get("obs:supportedHarnesses") or []), parent_harness, required=harness
        )
    else:
        observal["remoteUrl"] = (entry.get("obs:a2aInterface") or {}).get("url")

    task = tasks.new_task(message=message, observal=observal)
    try:
        capability_lock.record(
            kind="external" if kind == "a2a" else "agent",
            mode=capability_lock.MODE_DELEGATED,
            source="delegate",
            harness=parent_harness,
            cwd=observal["cwd"],
            identifier=identifier or None,
            component_id=agent_id,
            native_ref=entry.get("obs:nativeRef"),
            version=entry.get("version"),
            digest=entry.get("obs:artifactDigest"),
            extra={"task_id": task["id"], "child_harness": observal.get("harness")},
        )
    except (OSError, ValueError) as exc:  # evidence only; the delegation itself still runs
        optic.warning("capability lock not updated for delegation task={}: {}", task["id"], exc)
    _reap_abandoned()
    _launch(task)
    return wait(task["id"], wait_seconds, since=started)


def reply(task_id: str, message: str, *, wait_seconds: float = 0) -> dict:
    """Answer a remote task that asked for more input, and resume it."""
    started = time.monotonic()
    task = get(task_id)
    state = (task.get("status") or {}).get("state")
    if tasks.meta(task).get("kind") != "a2a" or state not in tasks.INTERRUPTED_STATES:
        raise DelegationError("Only a remote task that is waiting for input can take a reply.")
    message = (message or "").strip()
    if not message:
        raise DelegationError("The reply is empty.")
    tasks.task_dir(task_id).joinpath("reply.txt").write_text(message, encoding="utf-8")
    task.setdefault("history", []).append(tasks.text_message("ROLE_USER", message, task_id=task_id))
    tasks.set_status(task, tasks.STATE_SUBMITTED)
    _launch(task)
    return wait(task_id, wait_seconds, since=started)


def _ran_in(pid: int, root: str) -> bool:
    """Whether ``pid`` is still the child that ran in ``root``, not a process that reused its id."""
    if not Path("/proc/self/cwd").exists():
        # ponytail: without /proc (macOS, Windows) identity cannot be proven, so the orphaned child is
        # left to finish on its own; record and compare the process start time if that ever matters.
        return False
    try:
        return os.readlink(f"/proc/{pid}/cwd").startswith(root)
    except OSError:
        return False


def _reap(task: dict) -> None:
    """Stop the child a dead worker left running and remove its workspace.

    A child that is still running and cannot be proven to be ours (no /proc) is
    left alone with its workspace; a later sweep removes the workspace once it exits.
    """
    m = tasks.meta(task)
    root = m.get("workspaceRoot")
    pid = m.get("childPid")
    alive = bool(root and pid and _pid_alive(pid))
    if alive and _ran_in(int(pid), root):
        with contextlib.suppress(OSError):
            os.killpg(int(pid), signal.SIGKILL)  # the child leads its own process group
        alive = False
    if alive:
        return
    m.pop("childPid", None)
    if root and Path(root).exists():
        from observal_cli.delegation import workspace

        workspace.remove(Path(root))


def _reap_abandoned() -> None:
    """Settle tasks whose worker died while nobody was watching, so leftovers do not pile up.

    A live task's workspace exists too; only settled tasks are cleaned up.
    """
    from observal_cli.delegation import workspace

    parent = workspace.workspaces_dir()
    if not parent.is_dir():
        return
    for root in parent.iterdir():
        try:
            task_id = (tasks.store_dir() / "workspaces" / root.name).read_text(encoding="utf-8").strip()
            task = get(task_id)  # settles a task whose worker died
            state = (task.get("status") or {}).get("state")
            if state in tasks.TERMINAL_STATES:
                _reap(task)
                tasks.save(task)
        except (DelegationError, OSError, ValueError) as exc:
            optic.warning("could not settle abandoned delegation workspace {}: {}", root.name, exc)


WORKER_START_SECONDS = 60


def _age_seconds(task: dict) -> float:
    from datetime import UTC, datetime

    stamp = str((task.get("status") or {}).get("timestamp") or "")
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds()
    except ValueError:
        return 0.0


def get(task_id: str) -> dict:
    task = tasks.load(task_id)
    if task is None:
        raise DelegationError(f"No delegated task with id {task_id}.")
    # A worker that died without writing a final state would leave the task stuck. So would one
    # that never started: the worker writes its pid first thing.
    pid = _worker_pid(task["id"])
    never_started = pid is None and _age_seconds(task) > WORKER_START_SECONDS
    if not tasks.is_final(task) and ((pid and not _pid_alive(pid)) or never_started):
        task = tasks.load(task_id) or task
        if not tasks.is_final(task):
            _reap(task)
            tasks.set_status(task, tasks.STATE_FAILED, "The delegation worker stopped unexpectedly.")
            tasks.save(task)
    return task


def wait(task_id: str, seconds: float, *, since: float | None = None) -> dict:
    """Wait for the task to settle, at most ``seconds`` counted from ``since`` (default: now).

    Starting the clock when the call began keeps lookup and launch inside the caller's budget:
    MCP clients time the whole tool call.
    """
    start = time.monotonic() if since is None else since
    deadline = start + max(0.0, min(float(seconds or 0), MAX_WAIT_SECONDS))
    task = get(task_id)
    while not tasks.is_final(task) and time.monotonic() < deadline:
        time.sleep(WAIT_POLL_SECONDS)
        task = get(task_id)
    return task


def cancel(task_id: str) -> dict:
    task = get(task_id)
    m = tasks.meta(task)
    if m.get("kind") == "a2a" and (task.get("status") or {}).get("state") in tasks.INTERRUPTED_STATES:
        # Waiting for input: no worker is running, so tell the remote agent from here.
        from observal_cli.delegation import a2a_client

        try:
            entry = client.get(
                f"/api/v1/ard/entries/{m['target']}", operation="Cancel delegated task", resource=m["target"]
            )
        except CliError as exc:
            entry, reason = None, exc.message
        else:
            reason = "it is no longer approved"
        if entry is None or entry.get("obs:lifecycle") != "approved":
            # Never send credentials to an agent that lost approval; the local task still ends.
            text = f"Canceled by the caller, but the remote agent was not told: {reason}"
            return tasks.save(tasks.set_status(task, tasks.STATE_CANCELED, text))
        return tasks.save(a2a_client.cancel(task, entry=entry))
    if tasks.is_final(task):
        return task
    tasks.request_cancel(task["id"])
    pid = _worker_pid(task["id"])
    if pid and not _pid_alive(pid):  # no pid yet: the worker is starting and checks the marker first
        tasks.set_status(task, tasks.STATE_CANCELED, "Canceled by the caller.")
        tasks.save(task)
        return task
    return wait(task_id, 15)


# ── Presenting results to a model ────────────────────────────────────────


def summarize(task: dict) -> str:
    """A compact, model-readable account of a task: state, answer, and what to do next."""
    m = tasks.meta(task)
    status = task.get("status") or {}
    state = status.get("state", "")
    lines = [
        f"task_id: {task.get('id')}",
        f"agent: {m.get('targetName') or m.get('target')} ({m.get('kind')}"
        + (f" via {m['harness']}" if m.get("harness") else "")
        + ")",
        f"state: {state}",
    ]
    note = tasks.message_text(status.get("message"))
    if note:
        lines.append(f"status: {note}")
    for artifact in task.get("artifacts") or []:
        name = artifact.get("name") or artifact.get("artifactId") or "artifact"
        text = tasks.message_text(artifact)
        if name == "changes.patch":
            lines.append(f"\n## Proposed changes ({m.get('patchPath')})\n```diff\n{text}\n```")
        else:
            lines.append(f"\n## {name}\n{text}")
    for extra in m.get("notes") or []:
        lines.append(f"note: {extra}")
    if state in (tasks.STATE_SUBMITTED, tasks.STATE_WORKING):
        lines.append(
            "\nStill running. Call get_task with this task_id (optionally wait_seconds) to collect the result."
        )
    elif state in tasks.INTERRUPTED_STATES:
        lines.append("\nThe agent needs more input. Call delegate again with task_id and your answer as the message.")
    elif m.get("patchPath"):
        lines.append(
            "\nThe changes were made in an isolated copy and have NOT been applied. Review them; to apply, run "
            f"`git apply {m['patchPath']}` in the repository."
        )
    return "\n".join(lines)
