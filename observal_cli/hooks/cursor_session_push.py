# SPDX-FileCopyrightText: 2026 Hussain Alsaibai <hussain.alsaibai@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Cursor bridge for the shared session-push hook.

Cursor needs a bridge here for a reason the other harnesses do not: its
``preToolUse`` event is a *permission* hook. Cursor's documented behaviour is
that on exit code 0 the hook's stdout is parsed, and for permission hooks
invalid JSON -- or JSON that does not match the permission schema -- blocks
the tool call.

The shared push hook writes nothing to stdout, so running it directly for
Cursor would block every tool call. This module therefore runs the shared
delivery and then prints a valid allow response:

    {"continue": true, "permission": "allow"}

``allow`` is the right value here. The hook only observes; it never denies a
tool call, and it must not change what Cursor is allowed to do. The
permission-bearing events are kept in a named tuple so the intent stays
visible and testable rather than being implied by the event list.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from observal_cli.hooks.session_push import main as _shared_main

if TYPE_CHECKING:
    from pathlib import Path


def _allow_response() -> str:
    """Return the JSON Cursor expects back from a permission hook."""
    return json.dumps({"continue": True, "permission": "allow"})


def main(home: Path | None = None) -> None:
    """Run shared delivery, then answer Cursor's permission protocol.

    The response is written even when delivery fails. A permission hook that
    exits without valid JSON blocks the tool call, so a failed delivery must
    never cost the user their tool call.
    """
    try:
        _shared_main(home=home, harness="cursor")
    finally:
        sys.stdout.write(_allow_response() + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
