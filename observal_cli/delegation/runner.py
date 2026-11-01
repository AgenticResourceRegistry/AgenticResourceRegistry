# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Detached worker that drives one delegated task to a settled state.

    python -m observal_cli.delegation.runner <task-id>

Started by ``delegation.service``; it owns the task file until the task is
completed, failed, canceled, or waiting for input.
"""

from __future__ import annotations

import contextlib
import os
import sys

from loguru import logger as optic

from observal_cli import client
from observal_cli.delegation import a2a_client, local, tasks
from observal_cli.errors import CliError, _boundary_active


def run_task(task_id: str) -> dict | None:
    task = tasks.load(task_id)
    if task is None:
        return None
    m = tasks.meta(task)

    def should_cancel() -> bool:
        return tasks.cancel_requested(task_id)

    # Another process may have settled the task (canceled, or failed as a dead worker) before this
    # worker started; never overwrite that or act on it.
    if tasks.is_final(task):
        return task
    if should_cancel():
        tasks.clear_cancel(task_id)
        return tasks.save(tasks.set_status(task, tasks.STATE_CANCELED, "Canceled by the caller."))

    try:
        if m.get("kind") == "a2a":
            reply_path = tasks.task_dir(task_id) / "reply.txt"
            reply = None
            if reply_path.is_file():
                reply = reply_path.read_text(encoding="utf-8")
                with contextlib.suppress(OSError):
                    reply_path.unlink()
            entry = client.get(
                f"/api/v1/ard/entries/{m['target']}", operation="Delegate to agent", resource=m["target"]
            )
            if entry.get("obs:lifecycle") != "approved" or not entry.get("obs:delegable"):
                task = tasks.set_status(task, tasks.STATE_REJECTED, "The agent is no longer approved for delegation.")
            else:
                task = a2a_client.run(task, entry=entry, save=tasks.save, should_cancel=should_cancel, reply=reply)
        else:
            task = local.run(task, save=tasks.save, should_cancel=should_cancel)
    except CliError as exc:
        task = tasks.set_status(task, tasks.STATE_FAILED, exc.message)
    except Exception:
        optic.exception("delegation worker failed task={}", task_id)
        task = tasks.set_status(task, tasks.STATE_FAILED, "The delegation worker failed unexpectedly.")
    tasks.clear_cancel(task_id)
    return tasks.save(task)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m observal_cli.delegation.runner <task-id>", file=sys.stderr)
        return 2
    _boundary_active.set(True)  # errors become task states, not terminal output
    with contextlib.suppress(OSError, ValueError):  # so a starting worker is never mistaken for a missing one
        tasks.task_dir(args[0]).joinpath("worker.pid").write_text(str(os.getpid()), encoding="utf-8")
    return 0 if run_task(args[0]) is not None else 1


if __name__ == "__main__":
    sys.exit(main())
