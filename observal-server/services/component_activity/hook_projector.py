# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""User-scoped hook evidence on the shared evidence rails (``evidence_projector``).

Rows are ``evidence_kind = 'hook_*'`` (recorded runs, plus one
``hook_context_*`` row per verified hook saying whether it could run) and
publications ``evidence_type = 'hook'``. A harness without a verified hook
extractor is ``unsupported``.

A subagent's own transcript does not name its agent. When the harness links it
to its parent session, the parent's own record of the spawn names the agent
(``resolve_subagent``). Only the same project, user and harness is searched,
only records that mention the subagent's id or its spawn call are read, and
anything unreadable, missing or disagreeing leaves the agent unknown.
"""

from __future__ import annotations

import dataclasses

from loguru import logger as optic

from services.session_parsers.hook_evidence import HookEvidenceExtraction, extract_hook_evidence, hook_extractor

from .evidence_projector import EvidenceSpec, project_session_evidence
from .hook_matcher import HOOK_MATCHER_VERSION, match_hook_evidence
from .projector import _params, _query

# Parent records that may mention one subagent id or its spawn calls. A real
# session has a handful; more is not a session this lookup can read safely.
_MAX_LINK_ROWS = 64
_MAX_SPAWN_CALLS = 8
# Subagent sessions re-projected when their parent session arrives (``jobs.activity``).
MAX_SUBAGENT_SESSIONS = 256


async def _rows_mentioning(
    project_id: str, user_id: str, harness: str, session_id: str, needle: str
) -> list[dict] | None:
    """Canonical source rows of one scoped session whose stored line contains ``needle``; None if too many."""
    rows = await _query(
        """SELECT line_offset, raw_line, raw_line_truncated
        FROM session_events FINAL
        WHERE project_id = {project_id:String} AND user_id = {user_id:String}
          AND harness = {harness:String} AND session_id = {session_id:String}
          AND is_source_record = 1 AND position(raw_line, {needle:String}) > 0
        ORDER BY line_offset LIMIT {limit:UInt16} FORMAT JSON""",
        _params(project_id, user_id, harness, session_id) | {"param_needle": needle, "param_limit": _MAX_LINK_ROWS + 1},
    )
    return None if len(rows) > _MAX_LINK_ROWS else rows


async def resolve_subagent(
    extraction: HookEvidenceExtraction, project_id: str, user_id: str, harness: str, session_id: str
) -> HookEvidenceExtraction:
    """Name a subagent transcript's agent from its parent session's record of the spawn, if it is certain."""
    session = extraction.session
    parent, subagent_id = session.parent_session_id, session.subagent_id
    if not session.subagent or not parent or not subagent_id or parent == session_id:
        return extraction
    extractor = hook_extractor(harness)
    find_results = getattr(extractor, "subagent_results", None)
    find_calls = getattr(extractor, "agent_tool_calls", None)
    resolve = getattr(extractor, "resolve_agent", None)
    if find_results is None or find_calls is None or resolve is None:
        return extraction
    result_rows = await _rows_mentioning(project_id, user_id, harness, parent, subagent_id)
    results = find_results(result_rows, parent, subagent_id) if result_rows is not None else None
    if not results or len(results) > _MAX_SPAWN_CALLS:
        return extraction
    call_rows: list[dict] = []
    for tool_use_id in sorted(results):
        rows = await _rows_mentioning(project_id, user_id, harness, parent, tool_use_id)
        if rows is None:
            return extraction
        call_rows.extend(rows)
    calls = find_calls(call_rows, parent, frozenset(results))
    agent = resolve(results, calls) if calls is not None else ""
    if not agent:
        optic.debug("subagent hook context unresolved: rows={} results={}", len(result_rows or ()), len(results))
        return extraction
    return dataclasses.replace(extraction, session=dataclasses.replace(session, subagent_agent=agent))


async def subagent_sessions(project_id: str, user_id: str, harness: str, session_id: str) -> list[str]:
    """Scoped sessions ingested as subagents of ``session_id`` (bounded)."""
    rows = await _query(
        """SELECT DISTINCT session_id
        FROM session_events
        WHERE project_id = {project_id:String} AND user_id = {user_id:String}
          AND harness = {harness:String} AND parent_session_id = {session_id:String}
          AND session_id != {session_id:String} AND is_source_record = 1
        ORDER BY session_id LIMIT {limit:UInt16} FORMAT JSON""",
        _params(project_id, user_id, harness, session_id) | {"param_limit": MAX_SUBAGENT_SESSIONS},
    )
    return [row["session_id"] for row in rows if isinstance(row.get("session_id"), str) and row["session_id"]]


HOOK_SPEC = EvidenceSpec(
    evidence_type="hook",
    registry_key="hook_evidence_extractor",
    extract=extract_hook_evidence,
    match=match_hook_evidence,
    unknown_results=lambda extraction: 0,
    matcher_version=HOOK_MATCHER_VERSION,
    resolve=resolve_subagent,
)


async def project_session_hook_evidence(
    project_id: str, user_id: str, harness: str, session_id: str, *, force: bool = False
) -> dict:
    """Project one scoped session's hook evidence; only an acknowledged attempt is visible."""
    return await project_session_evidence(HOOK_SPEC, project_id, user_id, harness, session_id, force=force)
