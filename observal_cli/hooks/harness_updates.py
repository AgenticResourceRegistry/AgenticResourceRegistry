# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Session hook entry point for Codex, Kiro and Cursor startup updates.

Usage: ``python -m observal_cli.hooks.harness_updates --harness codex``.
The shared bridge lives in ``observal_cli.hooks.claude_updates``. Codex shows
results as a user-facing ``systemMessage`` at session start. Kiro and Cursor
print nothing, because their hook stdout reaches the model; the CLI shows
their results instead.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from observal_cli.hooks import claude_updates

if TYPE_CHECKING:
    from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) != 2 or args[0] != "--harness" or args[1] not in {"codex", "kiro", "cursor"}:
            return
        raw = sys.stdin.read(65537)
        if len(raw) > 65536:
            return
        ack: list[Path] = []
        output = claude_updates.handle(json.loads(raw), ack=ack, harness=args[1])
        if output:
            print(json.dumps(output), flush=True)
        for delivered in ack:
            delivered.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError, RuntimeError):
        pass  # A notice failure must never break session start.


if __name__ == "__main__":
    main()
