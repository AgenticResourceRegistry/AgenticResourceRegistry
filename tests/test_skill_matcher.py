# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""A skill fact is attributed only to the verified install at the exact location it names."""

from __future__ import annotations

import hashlib

from services.component_activity.skill_matcher import match_skill_evidence
from services.session_parsers.skill_evidence import SkillEvidence

HASH = "v2_" + "d" * 60
OURS = hashlib.sha256(b"/home/me/.pi/agent/skills/review/SKILL.md").hexdigest()
FOREIGN = hashlib.sha256(b"/tmp/another-user/.pi/agent/skills/review/SKILL.md").hexdigest()


def _installed(**overrides) -> dict:
    return {
        "local_name": "review",
        "scope": "user",
        "component_id": "skill-1",
        "component_version_id": "",
        "identity_status": "resolved",
        "verification_status": "verified",
        "location_sha256": OURS,
    } | overrides


def _fact(location: str) -> SkillEvidence:
    return SkillEvidence("load", "user", "review", 5, "skill-load:0", None, "success", "", location)


def _match(fact: SkillEvidence, *installed: dict):
    return match_skill_evidence([fact], {HASH: list(installed)}, {5: HASH})


def test_the_verified_location_is_attributed():
    result = _match(_fact(OURS), _installed())
    assert [row["component_id"] for row in result.rows] == ["skill-1"]


def test_same_scope_and_alias_at_a_foreign_path_is_unmatched():
    result = _match(_fact(FOREIGN), _installed())
    assert (result.rows, result.unmatched_count) == ((), 1)


def test_a_missing_location_on_either_side_never_matches():
    assert _match(_fact(""), _installed(location_sha256="")).rows == ()
    assert _match(_fact(OURS), _installed(location_sha256="")).rows == ()
    assert _match(_fact(""), _installed()).rows == ()


def test_unverified_installs_at_the_right_location_are_not_attributed():
    assert _match(_fact(OURS), _installed(verification_status="unverified")).rows == ()
