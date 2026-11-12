# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Explicit session-parent declarations, never inferred from nullable stats columns.

Declarations are scoped and monotonic: replays agree; contradictory claims remain
unknown. No marker is backfilled from historical parentless transcripts.
"""

import hashlib
import json
import time


def validate_parent_evidence(session_id, parent_session_id, known):
    """Reject contradictory declarations before any transcript writes occur."""
    if type(known) is not bool:
        raise ValueError("parent_metadata_known must be a boolean")
    if parent_session_id == session_id:
        raise ValueError("A session cannot be its own parent")
    if (
        known
        and parent_session_id is not None
        and (not isinstance(parent_session_id, str) or not parent_session_id.strip())
    ):
        raise ValueError("Known parent must be a nonempty ID or explicit null")


async def record_parent_evidence(session_id, project_id, user_id, harness, parent_session_id, known=False):
    """Persist an accepted declaration; legacy positive child links remain evidence.

    A caller with no parent and no explicit known flag contributes no evidence.
    This operation is required before acknowledgement, even on source-only replay.
    """
    validate_parent_evidence(session_id, parent_session_id, known)
    if parent_session_id is not None and (not isinstance(parent_session_id, str) or not parent_session_id.strip()):
        if known:
            raise ValueError("Known parent must be a nonempty ID or explicit null")
        return
    if parent_session_id == session_id:
        raise ValueError("A session cannot be its own parent")
    if not known and not parent_session_id:
        return
    from services.clickhouse import _query

    row = {
        "project_id": project_id,
        "user_id": user_id,
        "harness": harness,
        "session_id": session_id,
        "relation": "child" if parent_session_id else "top_level",
        "parent_session_id": parent_session_id or "",
        "evidence_version": time.time_ns() // 1000,
    }
    response = await _query(
        "INSERT INTO session_parent_evidence (project_id, user_id, harness, session_id, relation, "
        "parent_session_id, evidence_version) FORMAT JSONEachRow",
        {"wait_for_async_insert": "1"},
        data=json.dumps(row),
    )
    response.raise_for_status()


def _clickhouse_strings(values):
    """Serialize the bound Array(String) parameter, not SQL interpolation."""
    return "[" + ",".join("'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'" for value in values) + "]"


async def load_parent_evidence(keys, query):
    """Read bounded scoped declarations; inaccessible evidence must not promote a root."""
    from services.insights.scope import SessionKey

    result = {}
    for start in range(0, len(keys), 1000):
        batch = keys[start : start + 1000]
        response = await query(
            "SELECT project_id, user_id, harness, session_id, "
            "groupUniqArray(2)(tuple(relation, parent_session_id)) AS declarations "
            "FROM session_parent_evidence FINAL WHERE (project_id, user_id, harness, session_id) "
            "IN (SELECT arrayJoin(arrayZip({parent_projects:Array(String)}, {parent_users:Array(String)}, "
            "{parent_harnesses:Array(String)}, {parent_sessions:Array(String)}))) "
            "GROUP BY project_id, user_id, harness, session_id FORMAT JSON",
            {
                f"param_parent_{name}": _clickhouse_strings([key.as_tuple()[i] for key in batch])
                for i, name in enumerate(("projects", "users", "harnesses", "sessions"))
            },
        )
        response.raise_for_status()
        for row in response.json().get("data", []):
            key = SessionKey.from_row(row)
            if key in batch:
                result[key] = row.get("declarations") or []
    return result


def parent_decision(reported_parent, declarations):
    """Reconcile explicit declarations and legacy child links without guessing roots."""
    claims = set()
    malformed = False
    for claim in declarations:
        if not isinstance(claim, (list, tuple)) or len(claim) != 2:
            malformed = True
            continue
        relation, parent = claim
        if (relation == "top_level" and parent == "") or (
            relation == "child" and isinstance(parent, str) and parent.strip()
        ):
            claims.add((relation, parent))
        else:
            malformed = True
    if reported_parent:
        claims.add(("child", reported_parent))
    known = not malformed and len(claims) == 1
    parent = next(iter(claims))[1] if known else reported_parent
    reason = (
        "explicit_parent_declaration"
        if known
        else "conflicting_parent_evidence"
        if claims or malformed
        else "parent_evidence_unavailable"
    )
    fingerprint = hashlib.sha256(
        json.dumps({"policy": 2, "claims": sorted(claims), "malformed": malformed}).encode()
    ).hexdigest()
    return {
        "parent_session_id": parent,
        "parent_metadata_known": known,
        "parent_metadata_reason": reason,
        "parent_evidence_key": fingerprint,
    }
