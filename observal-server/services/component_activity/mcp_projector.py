# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""MCP invocation evidence on the same publication rails as skills and hooks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from services.session_parsers.invocations import extract_invocations

from . import projector
from .evidence_projector import EvidenceSpec, project_session_evidence
from .matcher import match_invocations

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from services.session_parsers.invocation_types import SourceInvocation

    from .projector import SourceSnapshot


@dataclass(frozen=True)
class MCPExtraction:
    status: str
    evidence: tuple[SourceInvocation, ...]
    malformed_source_records: int


def _extract(harness: str, rows: Sequence[Mapping[str, object]]) -> MCPExtraction:
    extracted = extract_invocations(harness, rows)
    return MCPExtraction(extracted.status, extracted.invocations, extracted.malformed_source_records)


def _counts(extraction: MCPExtraction) -> int:
    return extraction.malformed_source_records + sum(call.is_mcp_candidate for call in extraction.evidence)


def _decorate(result: dict, revision: str) -> dict:
    if result["status"] in {"complete", "already_complete"}:
        result = {**result, "layer_stability": "sender_cached_hash_not_proven_stable"}
        if result["status"] == "complete":
            result["source_revision"] = revision
    return result


async def project_session_activity(
    project_id: str,
    user_id: str,
    harness: str,
    session_id: str,
    *,
    force: bool = False,
    source_snapshot: SourceSnapshot | None = None,
) -> dict:
    spec = EvidenceSpec(
        evidence_type="mcp",
        registry_key="invocation_extractor",
        extract=_extract,
        match=lambda extraction, candidates, hashes: match_invocations(
            extraction.evidence, candidates, hashes, malformed_source_records=extraction.malformed_source_records
        ),
        unknown_results=lambda extraction: sum(
            call.is_mcp_candidate and call.result_state == "unknown" for call in extraction.evidence
        ),
        matcher_version=projector.MATCHER_VERSION,
        candidate_count=_counts,
        default_evidence_kind="call",
        decorate_result=_decorate,
        race_message="Canonical source or published layer mapping changed during activity publication",
    )
    return await project_session_evidence(
        spec, project_id, user_id, harness, session_id, force=force, source_snapshot=source_snapshot
    )
