# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""User-scoped skill evidence on the shared evidence rails (``evidence_projector``).

Rows are ``evidence_kind = 'skill_*'`` and publications ``evidence_type =
'skill'``. A harness without a verified skill extractor is ``unsupported``.
"""

from __future__ import annotations

from services.session_parsers.skill_evidence import extract_skill_evidence

from .evidence_projector import EvidenceSpec, project_session_evidence
from .skill_matcher import SKILL_MATCHER_VERSION, match_skill_evidence

SKILL_SPEC = EvidenceSpec(
    evidence_type="skill",
    registry_key="skill_evidence_extractor",
    extract=extract_skill_evidence,
    match=lambda extraction, candidates, hashes: match_skill_evidence(extraction.evidence, candidates, hashes),
    unknown_results=lambda extraction: sum(
        e.kind == "load" and e.result_state == "unknown" for e in extraction.evidence
    ),
    matcher_version=SKILL_MATCHER_VERSION,
)


async def project_session_skill_evidence(
    project_id: str, user_id: str, harness: str, session_id: str, *, force: bool = False
) -> dict:
    """Project one scoped session's skill evidence; only an acknowledged attempt is visible."""
    return await project_session_evidence(SKILL_SPEC, project_id, user_id, harness, session_id, force=force)
