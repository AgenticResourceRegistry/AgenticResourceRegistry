# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Auditable source references and candidate gates, not verification of model prose."""

import hashlib
import json
import re

from services.secrets_redactor import redact_secrets

RELEVANCE_SCHEMA = 1
_ALIAS = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def qualified(meta):
    """Consume the cohort contract; never infer it from an agent binding."""
    return (
        (meta.get("instruction_evidence") or {}).get("state") == "verified"
        and meta.get("parent_metadata_known") is True
        and not meta.get("parent_session_id")
        and not meta.get("failed_run")
    )


def role_hash(role):
    return hashlib.sha256(role.encode()).hexdigest() if isinstance(role, str) and role else ""


def mcp_target(name, arguments):
    """Explicit target attempts only; discovery and unknown gateways stay unknown."""
    args = arguments if isinstance(arguments, dict) else {}
    if not isinstance(name, str):
        return None, False
    alias = None
    if name.startswith("mcp__"):
        alias = name[5:].split("__", 1)[0]
    elif name == "mcp":
        if isinstance(args.get("tool"), str) and args["tool"] and isinstance(args.get("server"), str):
            alias = args["server"]
        else:
            return None, True
    else:
        return None, False
    return (
        alias if isinstance(alias, str) and _ALIAS.fullmatch(alias) and redact_secrets(alias) == alias else None
    ), True


def source_ref(key):
    return "s_" + hashlib.sha256(json.dumps(key.as_tuple()).encode()).hexdigest()[:20]


def sources(metas, facets, scope_hash):
    index = {}
    for meta in metas:
        if not qualified(meta):
            continue
        key = meta["session_key"]
        ref = source_ref(key)
        facet = facets.get(key) or {}
        relevance = facet.get("agent_task_relevance") or {}
        state = relevance.get("state", "unknown") if isinstance(relevance, dict) else "unknown"
        assessed = (
            facet.get("_relevance_schema") == RELEVANCE_SCHEMA and scope_hash and facet.get("_scope_hash") == scope_hash
        )
        index[ref] = {
            "project_id": key.project_id,
            "user_id": key.user_id,
            "harness": key.harness,
            "session_id": key.session_id,
            "agent_version": meta.get("agent_version", ""),
            "instruction_evidence": "recorded_match",
            "task_relevance": state if assessed and facet.get("_scope_complete") is True else "unknown",
            "task_relevance_basis": "model_assessed; not a deterministic fact",
            "eligible_for_changes": bool(assessed and facet.get("_scope_complete") is True and state == "in_scope"),
        }
    return index


def validate_narrative(narrative, index):
    """Drop unsupported actionable records; preserve source map and rejection accounting."""
    if not isinstance(narrative, dict):
        return {"source_index": index, "evidence_validation": {"state": "unavailable", "reason": "invalid_narrative"}}
    result = dict(narrative)
    rejected = []

    def grounded(items, section, actionable):
        kept = []
        for position, item in enumerate(items if isinstance(items, list) else []):
            refs = item.get("evidence_refs") if isinstance(item, dict) else None
            valid = isinstance(refs, list) and bool(refs) and all(isinstance(ref, str) and ref in index for ref in refs)
            if valid and actionable:
                valid = all(index[ref]["eligible_for_changes"] for ref in refs)
            if valid:
                kept.append({**item, "evidence_refs": list(dict.fromkeys(refs))})
            else:
                rejected.append(
                    {"section": section, "position": position, "reason": "missing_invalid_or_off_task_sources"}
                )
        return kept

    suggestions = result.get("suggestions") or {}
    if isinstance(suggestions, dict):
        result["suggestions"] = {
            **suggestions,
            **{
                key: grounded(suggestions.get(key), "suggestions." + key, True)
                for key in ("config_additions", "features_to_try", "usage_patterns")
            },
        }
    for section, key, actionable in (
        ("what_works", "strengths", False),
        ("friction_analysis", "categories", False),
        ("on_the_horizon", "opportunities", True),
    ):
        body = result.get(section)
        if isinstance(body, dict):
            result[section] = {**body, key: grounded(body.get(key), section + "." + key, actionable)}
    glance = result.get("at_a_glance")
    if isinstance(glance, dict):
        refs = glance.get("evidence_refs")
        grounded_refs = (
            isinstance(refs, list) and bool(refs) and all(isinstance(ref, str) and ref in index for ref in refs)
        )
        can_change = grounded_refs and all(index[ref]["eligible_for_changes"] for ref in refs)
        if not grounded_refs:
            result["at_a_glance"] = {
                "health": "mixed",
                "whats_working": "Operational records remain available.",
                "whats_hindering": "No valid source references support the generated summary.",
                "quick_win": "Inspect source coverage before changing agent instructions.",
                "ambitious_workflows": "Not assessed.",
            }
        elif not can_change:
            result["at_a_glance"] = {
                **glance,
                "quick_win": "No in-scope evidence supports an agent change in this sample.",
                "ambitious_workflows": "Not assessed from off-task or unknown relevance.",
            }
    result["source_index"] = index
    result["evidence_validation"] = {
        "version": 1,
        "state": "assessed" if index else "insufficient_instruction_evidence",
        "instruction_matched_sources": len(index),
        "change_eligible_sources": sum(s["eligible_for_changes"] for s in index.values()),
        "rejected_records": rejected,
        "limitations": [
            "References establish source identity, not factual accuracy or causal attribution.",
            "Task relevance is model-assessed. Unknown/off-task evidence cannot justify generated changes.",
            "Configured components are not observed use; MCP target counts are attempts, not successful component use.",
        ],
    }
    return result
