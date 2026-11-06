# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
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
from .kiro import meta_records as _kiro_meta
from .kiro import parse_rows as _parse_kiro
from .kiro import transcript_rows as _kiro_transcript
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
# shape; other formats are not yet
# converted, so their sessions report only a time range.
_MetaFn = Callable[[list[dict]], list[dict]]
_META_RECORDS: dict[str, _MetaFn] = {
    "claude-code": _claude_code_meta,
    "codex": _codex_meta,
    "kiro": _kiro_meta,
}


def meta_records(harness: str, entries: list[dict]) -> list[dict]:
    """Return *entries* (parsed transcript lines) as insight meta records for *harness*."""
    from observal_shared.harness_registry import HARNESS_REGISTRY

    parser_id = HARNESS_REGISTRY[harness]["session_parser"]  # KeyError = unknown harness
    convert = _META_RECORDS.get(parser_id or "")
    return convert(entries) if convert else entries


_TRANSCRIPT_ROWS: dict[str, _MetaFn] = {"kiro": _kiro_transcript}


def transcript_rows(harness: str, rows: list[dict]) -> list[dict]:
    """Expand native transcript records when a parser needs multi-event formatting."""
    from observal_shared.harness_registry import HARNESS_REGISTRY

    parser_id = HARNESS_REGISTRY[harness]["session_parser"]
    convert = _TRANSCRIPT_ROWS.get(parser_id or "")
    return convert(rows) if convert else rows


def instruction_evidence(harness: str, raw_lines: list[str], expected: dict | None) -> dict:
    """Dispatch only to formats that actually persist prompt-loading evidence."""
    import json

    from observal_shared.harness_registry import HARNESS_REGISTRY

    from .pi_instructions import instruction_evidence as pi_evidence

    def codex_evidence(entries, contract):
        """Reuse Codex normalization while binding cache identity to its full raw source."""
        result = pi_evidence(_codex_meta(entries), contract)
        if result.get("cache_key"):
            import hashlib

            result["cache_key"] = hashlib.sha256(
                json.dumps({"source": entries, "contract": contract, "policy": 1}, sort_keys=True, default=str).encode()
            ).hexdigest()
        return result

    extractors = {"pi": pi_evidence, "codex": codex_evidence}
    parser = HARNESS_REGISTRY.get(harness, {}).get("session_parser")
    extract = extractors.get(parser)
    if extract is None:
        return {"state": "unknown", "reason": "harness_instruction_format_unavailable"}
    entries = []
    try:
        for line in raw_lines:
            if line.strip():
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    return {"state": "unknown", "reason": "malformed_source_record"}
                entries.append(entry)
    except (ValueError, RecursionError):
        return {"state": "unknown", "reason": "malformed_source_record"}
    try:
        return extract(entries, expected)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return {"state": "unknown", "reason": "unsupported_instruction_record_shape"}

