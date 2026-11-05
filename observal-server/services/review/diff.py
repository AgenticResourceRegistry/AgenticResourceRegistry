# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Structured, numbered server-side hunks, shared by future CLI and web API."""

from difflib import SequenceMatcher

MAX_BYTES = 400_000
MAX_LINES = 10_000


def _lines(file):
    return (file or {}).get("content", "").splitlines()


def diff_files(base: dict, head: dict, *, context: int = 3, full: bool = False) -> list[dict]:
    if context < 0 or context > 100:
        raise ValueError("context must be between 0 and 100")
    result = []
    for path in sorted(base.keys() | head.keys()):
        before, after = base.get(path), head.get(path)
        old, new = _lines(before), _lines(after)
        status = (
            "added" if before is None else "removed" if after is None else "unchanged" if old == new else "modified"
        )
        large = not full and (
            max(len((before or {}).get("content", "")), len((after or {}).get("content", ""))) > MAX_BYTES
            or max(len(old), len(new)) > MAX_LINES
        )
        additions = deletions = 0
        hunks = []
        if status != "unchanged" and not large:
            matcher = SequenceMatcher(None, old, new, autojunk=False)
            for tag, b1, b2, h1, h2 in matcher.get_opcodes():
                if tag != "equal":
                    additions += h2 - h1
                    deletions += b2 - b1
            for group in matcher.get_grouped_opcodes(max(len(old), len(new)) if full else context):
                b_start, _, h_start, _ = group[0][1:]
                entries = []
                for tag, b1, b2, h1, h2 in group:
                    if tag == "equal":
                        entries.extend(
                            {"t": "ctx", "b": i + 1, "h": h1 + i - b1 + 1, "s": old[i]} for i in range(b1, b2)
                        )
                    else:
                        entries.extend({"t": "del", "b": i + 1, "s": old[i]} for i in range(b1, b2))
                        entries.extend({"t": "add", "h": j + 1, "s": new[j]} for j in range(h1, h2))
                hunks.append(
                    {
                        "base_start": b_start + 1,
                        "base_lines": group[-1][2] - b_start,
                        "head_start": h_start + 1,
                        "head_lines": group[-1][4] - h_start,
                        "lines": entries,
                    }
                )
        result.append(
            {
                "path": path,
                "status": status,
                "lang": (after or before)["lang"],
                "pinned": (after or before).get("pinned", False),
                "generated": (after or before).get("generated", False),
                "additions": None if large else additions,
                "deletions": None if large else deletions,
                "too_large": large,
                "hunks": hunks,
            }
        )
    # Pair unambiguous content-identical paths as renames rather than an add
    # and a delete. Ambiguous duplicates stay separate so the UI never guesses.
    removed = {}
    for entry in result:
        if entry["status"] == "removed":
            content = base[entry["path"]]["content"]
            removed.setdefault(content, []).append(entry)
    for entry in result[:]:
        if entry["status"] != "added":
            continue
        content = head[entry["path"]]["content"]
        matches = removed.get(content, []) if content else []
        if (
            len(matches) == 1
            and sum(r["status"] == "added" and head[r["path"]]["content"] == content for r in result) == 1
        ):
            original = matches[0]
            entry.update(status="renamed", old_path=original["path"], additions=0, deletions=0, hunks=[])
            result.remove(original)
    return result
