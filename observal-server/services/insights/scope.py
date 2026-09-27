# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Identity of a session used by every Insights telemetry read and cache."""

from __future__ import annotations

from dataclasses import dataclass

from observal_shared.migration.constants import DEFAULT_PROJECT_ID


@dataclass(frozen=True, slots=True)
class SessionKey:
    project_id: str
    user_id: str
    harness: str
    session_id: str

    @classmethod
    def from_row(cls, row: dict) -> SessionKey:
        return cls(*(str(row[field]) for field in ("project_id", "user_id", "harness", "session_id")))

    def as_tuple(self) -> tuple[str, str, str, str]:
        return self.project_id, self.user_id, self.harness, self.session_id


@dataclass(frozen=True, slots=True)
class InsightScope:
    """Explicit report subject; agent selection retains its existing eligibility rules."""

    subject_type: str
    project_id: str = DEFAULT_PROJECT_ID
    agent_id: str | None = None
    component_type: str | None = None
    component_id: str | None = None
    component_version_id: str | None = None
