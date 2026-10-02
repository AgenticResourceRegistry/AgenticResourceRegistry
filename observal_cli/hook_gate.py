# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Agent hook gate: run one hook command only while its agent is active.

Written into settings.json by ``agent pull --hooks=settings`` (``observal_cli.agent_hooks``).

Claude Code runs agent-frontmatter hooks only interactively. A settings-file
hook runs in every session, headless included, so an agent's hook moved there
must be gated on the agent. Claude Code puts ``agent_type`` in the hook input
for every event while an ``--agent`` runs, and for a subagent's own events.
In the recorded plain sessions ``agent_type`` is absent on every event
(Claude Code 2.1.286, ``tests/fixtures/component_insights/claude_code/hook_inputs``).

Decision (from the hook input JSON on stdin):

* ``agent_type`` equals ``--agent``: run the command.
* ``agent_type`` is another non-empty string: a different agent, so exit 0 silently.
* ``agent_type`` absent from a well-formed hook input: a plain main-thread
  event, so exit 0 silently.
* Anything else is unknown: unparseable input, a missing or empty
  ``hook_event_name`` or ``session_id``, or a present but non-string or empty
  ``agent_type``. ``--on-unknown`` decides. The default ``skip`` keeps agent
  isolation (never run one agent's hook in another agent's session) and
  writes a short diagnostic to stderr. ``run`` must be chosen explicitly. No
  policy guarantees both isolation and blocking on unknown input, so a
  safety-critical blocking hook needs an explicit decision, or should not be
  migrated.

When it runs, the gate replaces itself with ``/bin/sh -c <command>``, the
shell Claude Code itself uses for hook commands, with the exact stdin bytes
on a pipe fed by a forked writer. Exit code, signals, timeouts, stdout, stderr, working directory
and environment are therefore those of the command itself. The gate never
logs or stores the input or the command.

Imports stay minimal: this runs once per configured hook event, including
in sessions where the agent is not active.
"""

from __future__ import annotations

import json
import os
import shlex
import sys

SHELL = "/bin/sh"
_MISSING = object()
_UNKNOWN_DIAGNOSTIC = "observal hook gate: unrecognized hook input; hook skipped (--on-unknown skip)\n"


def decide(raw: bytes, agent: str) -> str:
    """``run``, ``skip`` or ``unknown`` for one hook input."""
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return "unknown"
    if not isinstance(data, dict) or not all(
        isinstance(data.get(key), str) and data[key] for key in ("hook_event_name", "session_id")
    ):
        return "unknown"
    agent_type = data.get("agent_type", _MISSING)
    if agent_type is _MISSING:
        return "skip"
    if not isinstance(agent_type, str) or not agent_type:
        return "unknown"
    return "run" if agent_type == agent else "skip"


def _write_all(fd: int, raw: bytes) -> None:
    """Write every byte, however many each write() accepts."""
    view = memoryview(raw)
    while view:
        view = view[os.write(fd, view) :]


def _exec_with_stdin(command: str, raw: bytes) -> None:
    """exec /bin/sh -c with ``raw`` as stdin, never assuming any pipe capacity.

    A forked writer holding only the pipe's write end feeds the shell, so the
    pipe can never fill before the shell starts reading, and partial writes
    are retried.
    """
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:  # writer
        try:
            os.close(read_fd)
            for fd in (0, 1, 2):
                os.close(fd)
            _write_all(write_fd, raw)
        except OSError:
            pass  # the command stopped reading (EPIPE); nothing to report
        os._exit(0)
    os.close(write_fd)
    os.dup2(read_fd, 0)
    if read_fd != 0:
        os.close(read_fd)
    os.execv(SHELL, [SHELL, "-c", command])


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        agent = args[args.index("--agent") + 1]
        command = args[args.index("--command") + 1]
        on_unknown = args[args.index("--on-unknown") + 1] if "--on-unknown" in args else "skip"
    except (ValueError, IndexError):
        sys.stderr.write("observal hook gate: usage: --agent NAME --command CMD [--on-unknown run|skip]\n")
        return 1  # non-blocking: a broken gate must not block the session
    if not agent or not command or on_unknown not in ("run", "skip"):
        sys.stderr.write("observal hook gate: invalid arguments\n")
        return 1
    raw = sys.stdin.buffer.read()
    decision = decide(raw, agent)
    if decision == "unknown":
        decision = on_unknown
        if decision == "skip":
            sys.stderr.write(_UNKNOWN_DIAGNOSTIC)  # never the input or the command
    if decision != "run":
        return 0
    sys.stdout.flush()
    _exec_with_stdin(command, raw)
    return 127  # unreachable unless exec failed


def _python_cmd() -> str:
    """The interpreter prefix, resolved like every other observal_cli launcher (isolated check, quoted)."""
    from observal_cli.shared.launcher import posix_prefix

    return posix_prefix()


def gated_command(agent: str, command: str, *, on_unknown: str = "skip") -> str:
    """The settings-file command for one agent hook: the original passes as a single quoted argument.

    POSIX shells only (Claude Code runs hook commands with ``/bin/sh -c``).
    """
    if sys.platform == "win32":
        raise NotImplementedError("the agent hook gate is POSIX-only")
    if on_unknown not in ("run", "skip"):
        raise ValueError("on_unknown must be run or skip")
    from observal_cli.shared.launcher import isolation_flag

    # -I (installed) or -P with an explicit PYTHONPATH (source checkout): neither the
    # project directory nor an inherited PYTHONPATH can supply observal_cli.
    parts = [
        _python_cmd(),
        isolation_flag(),
        "-m",
        "observal_cli.hook_gate",
        "--agent",
        shlex.quote(agent),
        "--command",
        shlex.quote(command),
    ]
    if on_unknown != "skip":
        parts += ["--on-unknown", on_unknown]
    return " ".join(parts)


if __name__ == "__main__":
    sys.exit(main())
