# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""User-scoped hook evidence on the shared evidence rails (``evidence_projector``).

Rows are ``evidence_kind = 'hook_*'`` (recorded runs, plus one
``hook_context_*`` row per verified hook saying whether it could run) and
publications ``evidence_type = 'hook'``. A harness without a verified hook
extractor is ``unsupported``.
"""

from __future__ import annotations

from services.session_parsers.hook_evidence import extract_hook_evidence

from .evidence_projector import EvidenceSpec, project_session_evidence
from .hook_matcher import HOOK_MATCHER_VERSION, match_hook_evidence

HOOK_SPEC = EvidenceSpec(
    evidence_type="hook",
    registry_key="hook_evidence_extractor",
    extract=extract_hook_evidence,
    match=match_hook_evidence,
    unknown_results=lambda extraction: 0,
    matcher_version=HOOK_MATCHER_VERSION,
)


async def project_session_hook_evidence(
    project_id: str, user_id: str, harness: str, session_id: str, *, force: bool = False
) -> dict:
    """Project one scoped session's hook evidence; only an acknowledged attempt is visible."""
    return await project_session_evidence(HOOK_SPEC, project_id, user_id, harness, session_id, force=force)
