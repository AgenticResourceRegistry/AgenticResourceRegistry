# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""CI guard: SKILL.md auto-gen block must match what the sync script produces.

If the CLI grows or renames a command and the bundled skill is not regenerated,
this test fails with a clear instruction to run::

    cd observal-server && uv run --with typer --with rich --with loguru \\
        --with pyyaml python ../scripts/sync_observal_skill.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Load the sync script as a module so we can call its helpers without
# triggering the ``__main__`` write path.
_SYNC_PATH = ROOT / "scripts" / "sync_observal_skill.py"
_spec = importlib.util.spec_from_file_location("_sync_observal_skill", _SYNC_PATH)
_sync = importlib.util.module_from_spec(_spec)
sys.modules["_sync_observal_skill"] = _sync
_spec.loader.exec_module(_sync)  # type: ignore[union-attr]


def test_hidden_worker_not_advertised_as_user_command():
    assert "_startup-check" not in _sync.generate_reference()


def test_skill_reference_block_in_sync():
    """The reference block on disk equals what the sync script would emit."""
    expected = _sync.generate_reference()
    on_disk = _sync.SKILL_PATH.read_text(encoding="utf-8")

    begin = on_disk.find(_sync.BEGIN_SENTINEL)
    end = on_disk.find(_sync.END_SENTINEL)
    assert begin != -1 and end != -1 and begin < end, (
        "SKILL.md is missing the BEGIN/END auto-generated sentinels. Run: python scripts/sync_observal_skill.py"
    )

    actual_block = on_disk[begin + len(_sync.BEGIN_SENTINEL) : end].lstrip("\n")
    # ``generate_reference`` appends a single trailing newline; mirror that
    # for the comparison so whitespace alone never causes drift noise.
    if not actual_block.endswith("\n"):
        actual_block += "\n"

    assert actual_block == expected, (
        "SKILL.md auto-generated reference block is stale.\n"
        "Run: python scripts/sync_observal_skill.py\n\n"
        f"--- expected (first 400 chars) ---\n{expected[:400]}\n"
        f"--- actual   (first 400 chars) ---\n{actual_block[:400]}\n"
    )
