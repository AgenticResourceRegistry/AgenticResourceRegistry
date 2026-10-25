#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Validate alembic migration chain integrity.

Checks:
  1. No duplicate revision IDs
  2. No multiple heads (branches that fork and never merge)
  3. Every down_revision references an existing revision
  4. Every revision is reachable from the single head through valid parents

Alembic merge revisions may name multiple parents; a joined graph is valid.

Run: python scripts/check_migrations.py
Exit code 0 = clean, 1 = problems found.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parent.parent / "observal-server" / "alembic" / "versions"

RE_REVISION = re.compile(r'^revision(?:.*?)?\s*=\s*["\'](.+?)["\']', re.MULTILINE)
RE_DOWN = re.compile(r"^down_revision(?:.*?)?\s*=\s*(.+)", re.MULTILINE)


def parse_revision(text: str) -> str | None:
    m = RE_REVISION.search(text)
    return m.group(1) if m else None


def parse_down_revision(text: str) -> tuple[str, ...]:
    m = RE_DOWN.search(text)
    if not m:
        return ()
    value = ast.literal_eval(m.group(1).strip())
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, tuple) and all(isinstance(parent, str) for parent in value):
        return value
    raise ValueError(f"Invalid down_revision: {value!r}")


def main() -> int:
    if not VERSIONS_DIR.is_dir():
        print(f"ERROR: versions directory not found: {VERSIONS_DIR}")
        return 1

    migrations: dict[str, Path] = {}
    down_map: dict[str, tuple[str, ...]] = {}
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

    for rev, parents in down_map.items():
        for parent in parents:
            if parent not in migrations:
                errors.append(f"{migrations[rev].name}: down_revision '{parent}' not found in any migration file")

    # Revisions that nothing else points to as a parent are heads.
    all_downs = {parent for parents in down_map.values() for parent in parents}
    heads = [rev for rev in migrations if rev not in all_downs]
    if len(heads) > 1:
        head_files = [f"  - {migrations[h].name} (revision='{h}')" for h in heads]
        errors.append("MULTIPLE HEADS detected (parallel branches):\n" + "\n".join(head_files))

    roots = [rev for rev, parents in down_map.items() if not parents]
    if len(roots) > 1:
        root_files = [f"  - {migrations[r].name} (revision='{r}')" for r in roots]
        errors.append("MULTIPLE ROOTS detected:\n" + "\n".join(root_files))

    if len(heads) == 1:
        visited: set[str] = set()
        pending = [heads[0]]
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            pending.extend(parent for parent in down_map[current] if parent in migrations)
        orphans = set(migrations) - visited
        if orphans:
            orphan_files = [f"  - {migrations[o].name} (revision='{o}')" for o in sorted(orphans)]
            errors.append("ORPHAN migrations not reachable from head:\n" + "\n".join(orphan_files))

    if errors:
        print(f"Migration chain validation FAILED ({len(errors)} issue(s)):\n")
        for e in errors:
            print(f"  {e}\n")
        return 1

    print(f"Migration graph OK: {len(migrations)} migrations, head='{heads[0]}', all revisions reachable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
