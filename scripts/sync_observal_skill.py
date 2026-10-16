#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regenerate the auto-generated command reference block in the Observal skill.

Run: python scripts/sync_observal_skill.py

This walks the Typer command tree exposed by ``observal_cli.main:app`` and
rewrites the section delimited by ``<!-- BEGIN AUTO-GENERATED ... -->`` and
``<!-- END AUTO-GENERATED ... -->`` sentinels in
``observal_cli/skills/observal/SKILL.md`` so the bundled skill stays in sync
with the actual CLI surface.

Enforced by ``tests/test_observal_skill_sync.py`` in CI. If the test fails,
run this script to regenerate.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to path so we can import observal_cli without installation.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import typer  # noqa: E402, TC002

from observal_cli.main import app  # noqa: E402

SKILL_PATH = ROOT / "observal_cli" / "skills" / "observal" / "references" / "commands.md"

BEGIN_SENTINEL = "<!-- BEGIN AUTO-GENERATED COMMAND REFERENCE -->"
END_SENTINEL = "<!-- END AUTO-GENERATED COMMAND REFERENCE -->"


def _first_line(text: str | None) -> str:
    """Return the first non-empty line of help text, stripped."""
    if not text:
        return ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _command_help(cmd: typer.models.CommandInfo) -> str:
    """Extract a one-line summary for a Typer command."""
    if cmd.help:
        return _first_line(cmd.help)
    if cmd.callback and cmd.callback.__doc__:
        return _first_line(cmd.callback.__doc__)
    return ""


def _group_help(group: typer.models.TyperInfo) -> str:
    """Extract a one-line summary for a Typer group."""
    inst = group.typer_instance
    if inst is None:
        return ""
    info = inst.info
    if info and info.help:
        return _first_line(info.help)
    return ""


def _walk(prefix: str, group_app: typer.Typer, lines: list[str], depth: int = 0) -> None:
    """Recursively render commands under ``group_app`` into ``lines``."""
    # Sub-groups first (alphabetical), then commands (alphabetical).
    indent = "  " * depth
    for sub in sorted(group_app.registered_groups, key=lambda g: g.name or ""):
        name = sub.name or ""
        full = f"{prefix} {name}".strip()
        summary = _group_help(sub)
        lines.append(f"{indent}- `{full}`: {summary}" if summary else f"{indent}- `{full}`")
        if sub.typer_instance is not None:
            _walk(full, sub.typer_instance, lines, depth + 1)

    for cmd in sorted(group_app.registered_commands, key=lambda c: c.name or ""):
        if cmd.hidden:
            continue
        name = cmd.name or (cmd.callback.__name__ if cmd.callback else "")
        full = f"{prefix} {name}".strip()
        summary = _command_help(cmd)
        lines.append(f"{indent}- `{full}`: {summary}" if summary else f"{indent}- `{full}`")


def generate_reference() -> str:
    """Produce the markdown block that lives between the sentinels."""
    lines: list[str] = []
    lines.append(
        "Every command available in the installed CLI. This block is generated "
        "from the Typer app by `scripts/sync_observal_skill.py`. If a flag you "
        "need is missing here, run `<command> --help` for full options."
    )
    lines.append("")

    # Root-level commands (scan, use, profile, uninstall, etc.) come first.
    root_lines: list[str] = []
    for cmd in sorted(app.registered_commands, key=lambda c: c.name or ""):
        if cmd.hidden:
            continue
        name = cmd.name or (cmd.callback.__name__ if cmd.callback else "")
        summary = _command_help(cmd)
        root_lines.append(f"- `observal {name}`: {summary}" if summary else f"- `observal {name}`")
    if root_lines:
        lines.append("**Root commands**")
        lines.append("")
        lines.extend(root_lines)
        lines.append("")

    # Group sections.
    for group in sorted(app.registered_groups, key=lambda g: g.name or ""):
        name = group.name or ""
        summary = _group_help(group)
        header = f"**`observal {name}`**"
        if summary:
            header = f"{header}: {summary}"
        lines.append(header)
        lines.append("")
        sub_lines: list[str] = []
        if group.typer_instance is not None:
            _walk(f"observal {name}", group.typer_instance, sub_lines)
        if sub_lines:
            lines.extend(sub_lines)
        else:
            lines.append("- (no subcommands)")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def rewrite_skill(skill_text: str, reference: str) -> str:
    """Return ``skill_text`` with the auto-gen block replaced by ``reference``."""
    begin_idx = skill_text.find(BEGIN_SENTINEL)
    end_idx = skill_text.find(END_SENTINEL)

    block = f"{BEGIN_SENTINEL}\n{reference}{END_SENTINEL}"

    if begin_idx == -1 or end_idx == -1:
        # First-time setup: append the block to the end if neither sentinel
        # exists. Callers that want a different placement should add the
        # sentinels manually before running the script.
        sep = "" if skill_text.endswith("\n") else "\n"
        return f"{skill_text}{sep}\n{block}\n"

    if end_idx < begin_idx:
        raise SystemExit("SKILL.md sentinels are out of order: END appears before BEGIN. Fix the file by hand.")

    head = skill_text[:begin_idx]
    tail = skill_text[end_idx + len(END_SENTINEL) :]
    return f"{head}{block}{tail}"


def main() -> int:
    if not SKILL_PATH.exists():
        print(f"✗ {SKILL_PATH} does not exist", file=sys.stderr)
        return 1

    reference = generate_reference()
    original = SKILL_PATH.read_text(encoding="utf-8")
    updated = rewrite_skill(original, reference)

    if original == updated:
        print(f"✓ {SKILL_PATH.relative_to(ROOT)} already in sync")
        return 0

    SKILL_PATH.write_text(updated, encoding="utf-8")
    print(f"✓ Regenerated {SKILL_PATH.relative_to(ROOT)} ({len(reference)} bytes in reference block)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
