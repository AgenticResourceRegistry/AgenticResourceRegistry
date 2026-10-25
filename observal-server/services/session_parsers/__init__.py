# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Session JSONL parsers -- READ path (raw ClickHouse rows -> frontend events).

Dispatches to format-specific parsers based on the ``session_parser`` key in
``observal_shared.harness_registry.HARNESS_REGISTRY``. Dispatch is **strict**: an unknown harness
raises ``KeyError`` so new harnesses cannot silently fall through to a wrong parser.

When adding a new harness:
  1. Add its entry to ``observal_shared/harness_registry.py`` with a ``"session_parser"`` key.
  2. If the harness needs a new parser, add a module under ``session_parsers/`` and
     register it in ``_PARSERS`` below.
  3. If the harness re-uses an existing format (e.g. Claude Code), point its
     ``"session_parser"`` at the existing parser ID.

Public API
----------
parse_raw_events(rows)  -- consumed by api/routes/sessions.py
meta_records(harness, entries)  -- consumed by services/insights/session_meta_extractor.py
"""

from __future__ import annotations

from collections.abc import Callable

from .antigravity import parse_rows as _parse_antigravity
from .claude_code import meta_records as _claude_code_meta
from .claude_code import parse_rows as _parse_claude_code
from .codex import meta_records as _codex_meta
from .codex import parse_rows as _parse_codex
from .copilot_cli import parse_rows as _parse_copilot_cli
from .cursor import parse_rows as _parse_cursor
from .goose import parse_rows as _parse_goose
from .kiro import parse_rows as _parse_kiro
from .opencode import parse_rows as _parse_opencode
from .pi import parse_rows as _parse_pi

# Maps session_parser ID -> parse_rows callable.
# Add new entries here when implementing a new JSONL format.
_ParseFn = Callable[[list[dict]], list[dict]]
_PARSERS: dict[str, _ParseFn] = {
    "claude-code": _parse_claude_code,
    "codex": _parse_codex,
    "copilot-cli": _parse_copilot_cli,
    "cursor": _parse_cursor,
    "goose": _parse_goose,
    "kiro": _parse_kiro,
    "opencode": _parse_opencode,
    "pi": _parse_pi,
    "antigravity": _parse_antigravity,
}


def parse_raw_events(rows: list[dict]) -> list[dict]:
    """Parse raw_line JSONL rows into normalised frontend events.

    Looks up the harness from the first row, resolves the ``session_parser`` key
    from ``harness_registry``, and dispatches to the matching parser.

    Raises ``KeyError`` for unregistered harnesses or unimplemented parsers.
    """
    if not rows:
        return []
    harness = rows[0].get("harness", "")

    from observal_shared.harness_registry import HARNESS_REGISTRY

    parser_id = HARNESS_REGISTRY[harness]["session_parser"]  # KeyError = unknown harness
    if parser_id is None:
        return []
    parser = _PARSERS[parser_id]  # KeyError = unimplemented parser
    return parser(rows)


# Maps session_parser ID -> converter from parsed transcript lines to the
# Pi-style records the insight meta extractor reads. Pi lines already have that
# shape and the extractor reads Kiro lines itself; other formats are not yet
# converted, so their sessions report only a time range.
_MetaFn = Callable[[list[dict]], list[dict]]
_META_RECORDS: dict[str, _MetaFn] = {
    "claude-code": _claude_code_meta,
    "codex": _codex_meta,
}


def meta_records(harness: str, entries: list[dict]) -> list[dict]:
    """Return *entries* (parsed transcript lines) as insight meta records for *harness*."""
    from observal_shared.harness_registry import HARNESS_REGISTRY

    parser_id = HARNESS_REGISTRY[harness]["session_parser"]  # KeyError = unknown harness
    convert = _META_RECORDS.get(parser_id or "")
    return convert(entries) if convert else entries
