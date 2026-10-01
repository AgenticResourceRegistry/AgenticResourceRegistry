# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Published, user-scoped component activity derived only from canonical source rows.

MCP calls, skill evidence and hook evidence are separate projections on the same rails.
"""

from .hook_projector import project_session_hook_evidence
from .projector import MATCHER_VERSION, PROJECTION_VERSION, project_session_activity, publication_version
from .skill_projector import project_session_skill_evidence

__all__ = (
    "MATCHER_VERSION",
    "PROJECTION_VERSION",
    "project_session_activity",
    "project_session_hook_evidence",
    "project_session_skill_evidence",
    "publication_version",
)
