#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Validate alembic migration chain integrity.

Checks:
  1. No duplicate revision IDs
  2. No multiple heads (branches that fork and never merge)
  3. Every down_revision references an existing revision
  4. Linear chain from root to head with no orphans

Run: python scripts/check_migrations.py
Exit code 0 = clean, 1 = problems found.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parent.parent / "observal-server" / "alembic" / "versions"

RE_REVISION = re.compile(r'^revision(?:.*?)?\s*=\s*["\'](.+?)["\']', re.MULTILINE)
RE_DOWN = re.compile(r"^down_revision(?:.*?)?\s*=\s*(.+)", re.MULTILINE)


def parse_revision(text: str) -> str | None:
    m = RE_REVISION.search(text)
    return m.group(1) if m else None


def parse_down_revision(text: str) -> str | None:
    m = RE_DOWN.search(text)
    if not m:
        return None
    raw = m.group(1).strip().rstrip(",")
    if raw == "None":
        return None
    return raw.strip("\"'")


def main() -> int:
    if not VERSIONS_DIR.is_dir():
        print(f"ERROR: versions directory not found: {VERSIONS_DIR}")
        return 1

    migrations: dict[str, Path] = {}
    down_map: dict[str, str | None] = {}
    errors: list[str] = []

    for path in sorted(VERSIONS_DIR.glob("*.py")):
        if path.name.startswith("__"):
            continue
        text = path.read_text()
        rev = parse_revision(text)
        down = parse_down_revision(text)

        if rev is None:
            errors.append(f"{path.name}: no 'revision' found")
            continue

        if rev in migrations:
            errors.append(f"DUPLICATE revision '{rev}' in:\n  - {migrations[rev].name}\n  - {path.name}")
        else:
            migrations[rev] = path
            down_map[rev] = down

    for rev, down in down_map.items():
        if down is not None and down not in migrations:
            errors.append(f"{migrations[rev].name}: down_revision '{down}' not found in any migration file")

    # Revisions that nothing else points to as its down_revision = heads
    all_downs = set(down_map.values()) - {None}
    heads = [rev for rev in migrations if rev not in all_downs]
    if len(heads) > 1:
        head_files = [f"  - {migrations[h].name} (revision='{h}')" for h in heads]
        errors.append("MULTIPLE HEADS detected (parallel branches):\n" + "\n".join(head_files))

    roots = [rev for rev, down in down_map.items() if down is None]
    if len(roots) > 1:
        root_files = [f"  - {migrations[r].name} (revision='{r}')" for r in roots]
        errors.append("MULTIPLE ROOTS detected:\n" + "\n".join(root_files))

    if len(roots) == 1 and len(heads) == 1:
        visited = set()
        reverse_map = {down: rev for rev, down in down_map.items() if down is not None}
        current: str | None = roots[0]
        while current:
            visited.add(current)
            current = reverse_map.get(current)
        orphans = set(migrations.keys()) - visited
        if orphans:
            orphan_files = [f"  - {migrations[o].name} (revision='{o}')" for o in orphans]
            errors.append("ORPHAN migrations not reachable from root:\n" + "\n".join(orphan_files))

    if errors:
        print(f"Migration chain validation FAILED ({len(errors)} issue(s)):\n")
        for e in errors:
            print(f"  {e}\n")
        return 1

    print(f"Migration chain OK: {len(migrations)} migrations, head='{heads[0]}', linear chain intact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
