# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Map old line anchors to a new head; changed lines remain readable as outdated."""

from difflib import SequenceMatcher


def reanchor(threads, old_files: dict, new_files: dict) -> tuple[int, int]:
    """Adjust line ranges on head-side threads without changing their original revision id."""
    moved = outdated = 0
    for thread in threads:
        if thread.path is None or thread.side != "head" or thread.outdated:
            continue
        old = (old_files.get(thread.path) or {}).get("content", "").splitlines()
        new = (new_files.get(thread.path) or {}).get("content", "").splitlines()
        first, last = thread.start_line, thread.end_line
        if not first or not last or first < 1 or last > len(old) or thread.path not in new_files:
            thread.outdated = True
            outdated += 1
            continue
        mapping = {}
        for tag, b1, b2, h1, _ in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            if tag == "equal":
                mapping.update({i + 1: h1 + i - b1 + 1 for i in range(b1, b2)})
        positions = [mapping.get(i) for i in range(first, last + 1)]
        if None in positions or positions != list(range(positions[0], positions[0] + len(positions))):
            thread.outdated = True
            outdated += 1
        else:
            thread.start_line, thread.end_line = positions[0], positions[-1]
            moved += 1
    return moved, outdated
