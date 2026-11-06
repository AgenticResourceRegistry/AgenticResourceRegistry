# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Read-time cohort decisions; source telemetry/bindings and old reports stay unchanged."""

POLICY_VERSION = 1


def select_cohort(metas):
    """Separate eligible top-level evidence from bound, child and unknown records."""
    selected = []
    members = []
    top_level = []
    children = []
    identities = {m["session_key"] for m in metas}
    for meta in metas:
        key = meta["session_key"]
        evidence = meta.get("instruction_evidence") or {
            "state": "unknown",
            "reason": "instruction_evidence_unavailable",
        }
        parent = meta.get("parent_session_id", "")
        parent_known = meta.get("parent_metadata_known", False)
        if not parent_known:
            decision = "parent_metadata_unavailable"
        elif parent:
            children.append(meta)
            parent_key = type(key)(key.project_id, key.user_id, key.harness, parent)
            decision = "child_session" if parent_key in identities and parent_key != key else "child_parent_unresolved"
        else:
            top_level.append(meta)
            decision = "failed_run" if meta.get("failed_run") else evidence["state"]
            if decision == "verified":
                selected.append(meta)
        members.append(
            {
                "project_id": key.project_id,
                "user_id": key.user_id,
                "harness": key.harness,
                "session_id": key.session_id,
                "decision": decision,
                "instruction_state": evidence["state"],
                "reason": evidence["reason"],
            }
        )
    counts = {}
    for member in members:
        counts[member["decision"]] = counts.get(member["decision"], 0) + 1
    coverage = {
        "policy_version": POLICY_VERSION,
        "bound_sessions": len(metas),
        "verified_top_level_sessions": len(selected),
        "bound_top_level_sessions": len(top_level),
        "child_sessions": len(children),
        "decisions": counts,
        "members": members,
        "bound_operational": {
            "top_level_user_turn_records": sum(m.get("user_message_count", 0) for m in top_level),
            "top_level_reported_cost_usd": round(sum(m.get("total_cost", 0) for m in top_level), 4),
            "child_reported_cost_usd": round(sum(m.get("total_cost", 0) for m in children), 4),
        },
        "limitations": [
            "Recorded prompt presence is not cryptographic attestation, unique version identity, compliance or task relevance.",
            "Child records are separate evidence, not independent tasks. Child costs are reported separately, never added to parent spend.",
            "Missing instruction formats/checkpoints and parent metadata remain unknown; binding alone never qualifies a recommendation cohort.",
        ],
    }
    return selected, coverage


def qualified_cache(cached, metas):
    """Do not reuse pre-verification facets, another version's facets or stale source."""
    keys = {m["session_key"]: (m.get("instruction_evidence") or {}).get("cache_key") for m in metas}
    return {key: value for key, value in cached.items() if keys.get(key) and value.get("_cohort_key") == keys[key]}


def prompt_metrics(value):
    """Copy aggregate model input without per-session rosters at any nesting depth.

    Stored/API metrics retain their members; only the provider-bound copy drops
    them, including nested prior-version and previous-report cohort coverage.
    """
    if isinstance(value, dict):
        return {key: prompt_metrics(child) for key, child in value.items() if key != "members"}
    if isinstance(value, list):
        return [prompt_metrics(child) for child in value]
    return value
