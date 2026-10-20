# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Version impact analysis for insight reports.

Cross-user layer correlation: groups sessions by layer_hash, compares
metrics between groups, and surfaces anonymized patterns about which
configurations lead to better outcomes.

Key concepts:
- "canonical": layer state matches lockfile integrity (no user modifications)
- "dirty": user modified files after install (drift detected)
- Groups are compared by metrics, not by user identity
"""

from __future__ import annotations

import json

import structlog

from observal_shared.migration.constants import DEFAULT_PROJECT_ID
from services.insight_version_filters import LEGACY_UNVERSIONED_AGENT_VERSION, agent_version_filter

from ._deps import get_query

logger = structlog.get_logger(__name__)


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _robust_outlier_labels(groups: list[dict], metric: str) -> dict[str, str]:
    """Label layer cohorts using median/MAD so outliers do not poison means."""
    values = [float(g.get(metric, 0) or 0) for g in groups]
    if len(values) < 3:
        return {g["layer_hash"]: "normal" for g in groups}
    med = _median(values)
    deviations = [abs(v - med) for v in values]
    mad = _median(deviations) or 1e-9
    labels: dict[str, str] = {}
    for group in groups:
        value = float(group.get(metric, 0) or 0)
        robust_z = 0.6745 * (value - med) / mad
        if robust_z >= 2.5:
            labels[group["layer_hash"]] = "positive_outlier"
        elif robust_z <= -2.5:
            labels[group["layer_hash"]] = "negative_outlier"
        else:
            labels[group["layer_hash"]] = "normal"
    return labels


def _confidence_for_groups(groups: list[dict], significant: bool) -> str:
    total_sessions = sum(int(g.get("sessions", 0)) for g in groups)
    multi_user_groups = sum(1 for g in groups if int(g.get("users", 0)) >= 2)
    if total_sessions < 10 or len(groups) < 2:
        return "insufficient_data"
    if significant and total_sessions >= 30 and multi_user_groups >= 2:
        return "high"
    if significant and total_sessions >= 15:
        return "medium"
    return "low"


async def detect_layer_groups(
    agent_id: str,
    period_start: str,
    period_end: str,
    agent_name: str = "",
    agent_version: str | None = None,
) -> list[dict]:
    """Group sessions by layer_hash and compute metrics per group.

    Returns groups ordered by session count (largest first):
    [
        {
            "layer_hash": "abc123...",
            "sessions": 45,
            "users": 12,
            "avg_prompts": 8.2,
            "avg_tool_calls": 15.4,
            "avg_duration_seconds": 420.0,
            "avg_cost": 0.12,
            "avg_tokens": 50000,
            "tool_error_rate": 0.05,
            "success_proxy": 0.82,
        },
        ...
    ]
    """
    query = get_query()

    # The version expression has its own alias: naming it agent_version would make
    # ClickHouse resolve the column inside it to the alias, and GROUP BY would fail.
    sql = """
        SELECT
            if(
                agent_version = '' AND {agent_version:String} = '__LEGACY_VERSION__',
                '__LEGACY_VERSION__',
                agent_version
            ) AS version_key,
            layer_hash,
            count() AS sessions,
            uniq(user_id) AS users,
            avg(prompt_count) AS avg_prompts,
            avg(tool_call_count) AS avg_tool_calls,
            avg(toFloat64(last_event_time - first_event_time)) AS avg_duration_seconds,
            sum(total_credits) / count() AS avg_cost,
            sum(input_tokens + output_tokens) / count() AS avg_tokens,
            -- Tool error proxy: sessions with high tool_result vs tool_call ratio
            -- (more results than calls = retries/errors)
            countIf(tool_result_count > tool_call_count * 1.5) / count() AS tool_error_rate,
            -- Success proxy: sessions that complete (have a stop event) with reasonable duration
            countIf(event_count > 5 AND prompt_count >= 1) / count() AS success_proxy
        FROM session_stats_agg FINAL
        WHERE (agent_id = {agent_id:String} OR agent_id = {agent_name:String})
          AND last_event_time >= {t_start:String}
          AND last_event_time <= {t_end:String}
          AND layer_hash != ''
          AND __AGENT_VERSION_FILTER__
        GROUP BY version_key, layer_hash
        HAVING sessions >= 3
        ORDER BY sessions DESC
        LIMIT 20
        FORMAT JSON
    """.replace("__LEGACY_VERSION__", LEGACY_UNVERSIONED_AGENT_VERSION).replace(
        "__AGENT_VERSION_FILTER__", agent_version_filter()
    )
    params = {
        "param_agent_id": agent_id,
        "param_agent_name": agent_name,
        "param_t_start": period_start,
        "param_t_end": period_end,
        "param_agent_version": agent_version or "",
    }

    try:
        r = await query(sql, params)
        r.raise_for_status()
        rows = r.json().get("data", [])
    except Exception as e:
        logger.warning("layer_groups_query_failed", error=str(e))
        return []

    return [
        {
            "agent_version": row.get("version_key", ""),
            "layer_hash": row["layer_hash"],
            "sessions": int(row.get("sessions", 0)),
            "users": int(row.get("users", 0)),
            "avg_prompts": round(float(row.get("avg_prompts", 0)), 1),
            "avg_tool_calls": round(float(row.get("avg_tool_calls", 0)), 1),
            "avg_duration_seconds": round(float(row.get("avg_duration_seconds", 0)), 0),
            "avg_cost": round(float(row.get("avg_cost", 0)), 4),
            "avg_tokens": int(float(row.get("avg_tokens", 0))),
            "tool_error_rate": round(float(row.get("tool_error_rate", 0)), 3),
            "success_proxy": round(float(row.get("success_proxy", 0)), 3),
        }
        for row in rows
    ]


async def fetch_layer_snapshots_for_groups(
    project_id: str,
    layer_hashes: list[str],
) -> dict[str, dict]:
    """Fetch stored layer snapshots for a list of hashes.

    Returns {hash: snapshot_content} for snapshots that exist.
    """
    if not layer_hashes:
        return {}

    query = get_query()

    # Validate hashes are strictly hex (prevent injection into Array literal)
    import re

    hex_re = re.compile(r"^[0-9a-fA-F]+$")
    safe_hashes = [h for h in layer_hashes if hex_re.match(h)]
    if not safe_hashes:
        return {}

    sql = """
        SELECT hash, content
        FROM layer_snapshots FINAL
        WHERE project_id = {project_id:String}
          AND hash IN ({hashes:Array(String)})
        FORMAT JSON
    """
    params = {
        "param_project_id": project_id,
        "param_hashes": "[" + ",".join(f"'{h}'" for h in safe_hashes) + "]",
    }

    try:
        r = await query(sql, params)
        r.raise_for_status()
        rows = r.json().get("data", [])
        return {row["hash"]: json.loads(row["content"]) for row in rows}
    except Exception as e:
        logger.warning("layer_snapshots_fetch_failed", error=str(e))
        return {}


def diff_snapshots(snap_a: dict, snap_b: dict) -> dict:
    """Diff two layer snapshots to find what's different.

    Returns a summary of differences (not full content, to keep prompt size sane).
    """
    files_a: dict[str, str] = {}
    files_b: dict[str, str] = {}

    # Flatten both snapshots to {harness/path: hash}
    for ide_name, files in (snap_a.get("harnesses") or {}).items():
        for f in files:
            files_a[f"{ide_name}/{f['path']}"] = f.get("hash", "")

    for ide_name, files in (snap_b.get("harnesses") or {}).items():
        for f in files:
            files_b[f"{ide_name}/{f['path']}"] = f.get("hash", "")

    paths_a = set(files_a.keys())
    paths_b = set(files_b.keys())

    return {
        "added": sorted(paths_b - paths_a),
        "removed": sorted(paths_a - paths_b),
        "modified": sorted(p for p in paths_a & paths_b if files_a[p] != files_b[p]),
    }


def extract_content_summary(snapshot: dict, max_chars: int = 1500) -> str:
    """Extract a brief content summary from a snapshot for LLM context.

    Focuses on rules/agents/skills content (the things that shape behavior).
    Anonymized: no user paths, no personal info.
    Only called when significant performance gap detected.
    """
    lines: list[str] = []
    char_count = 0

    for ide_name, files in (snapshot.get("harnesses") or {}).items():
        for f in files:
            path = f.get("path", "")
            content = f.get("content", "")
            if not content:
                continue

            # Only include behavioral files (rules, agents, skills)
            if not any(x in path for x in ("CLAUDE.md", "AGENTS.md", "agents/", "skills/", "rules/")):
                continue

            # Truncate individual file content
            snippet = content[:500] if len(content) > 500 else content
            entry = f"[{ide_name}:{path}]\n{snippet}"

            if char_count + len(entry) > max_chars:
                break
            lines.append(entry)
            char_count += len(entry)

    return "\n---\n".join(lines) if lines else "(no behavioral content captured)"


async def build_version_impact_data(
    agent_id: str,
    period_start: str,
    period_end: str,
    agent_name: str = "",
    agent_version: str | None = None,
    project_id: str = DEFAULT_PROJECT_ID,
) -> dict | None:
    """Build the complete version impact data block for the insight report.

    Cross-user analysis: compares layer groups and surfaces anonymized patterns.
    Returns None if insufficient data or no significant difference between groups.

    Only fetches layer snapshot content when metrics show >= 20% difference
    in success_proxy or tool_error_rate between best and worst groups.
    This avoids flooding LLM context with huge snapshots when configs don't matter.
    """
    groups = await detect_layer_groups(
        agent_id=agent_id,
        period_start=period_start,
        period_end=period_end,
        agent_name=agent_name,
        agent_version=agent_version,
    )

    if len(groups) < 2:
        return None

    # Check for significant difference before fetching snapshots
    best_success = max(g["success_proxy"] for g in groups)
    worst_success = min(g["success_proxy"] for g in groups)
    best_error = min(g["tool_error_rate"] for g in groups)
    worst_error = max(g["tool_error_rate"] for g in groups)

    success_gap = best_success - worst_success
    error_gap = worst_error - best_error
    outlier_labels = _robust_outlier_labels(groups, "success_proxy")

    # Threshold: at least 20% absolute gap in success or 15% in error rate.
    # Robust outlier labels add sensitivity without letting outliers poison means.
    significant = (
        success_gap >= 0.20 or error_gap >= 0.15 or any(label != "normal" for label in outlier_labels.values())
    )
    confidence = _confidence_for_groups(groups, significant)

    if not significant:
        # Return lightweight summary (no snapshot content, saves context)
        return {
            "group_count": len(groups),
            "total_sessions": sum(g["sessions"] for g in groups),
            "total_users": sum(g["users"] for g in groups),
            "significant": False,
            "confidence": confidence,
            "groups": [
                {
                    "layer_hash": g["layer_hash"],
                    "sessions": g["sessions"],
                    "users": g["users"],
                    "success_proxy": g["success_proxy"],
                    "tool_error_rate": g["tool_error_rate"],
                    "outlier_label": outlier_labels.get(g["layer_hash"], "normal"),
                }
                for g in groups[:5]
            ],
            "finding": "No significant performance difference between configurations.",
        }

    # Significant gap found: fetch snapshots to explain WHY
    top_hashes = [g["layer_hash"] for g in groups[:5]]
    snapshots = await fetch_layer_snapshots_for_groups(project_id, top_hashes)

    # Find best and worst performing groups
    groups_with_data = [g for g in groups if g["layer_hash"] in snapshots]
    if len(groups_with_data) < 2:
        return None

    canonical_sessions = 0
    dirty_sessions = 0
    canonical_users = 0
    dirty_users = 0
    for group in groups_with_data:
        snap = snapshots.get(group["layer_hash"], {})
        is_canonical = snap.get("drift", {}).get("is_canonical", None)
        if is_canonical is True:
            canonical_sessions += group["sessions"]
            canonical_users += group["users"]
        elif is_canonical is False:
            dirty_sessions += group["sessions"]
            dirty_users += group["users"]

    # Sort by success_proxy (higher = better)
    sorted_by_success = sorted(groups_with_data, key=lambda g: g["success_proxy"], reverse=True)
    best_group = sorted_by_success[0]
    worst_group = sorted_by_success[-1]

    # Diff the best vs worst configs
    best_snap = snapshots.get(best_group["layer_hash"], {})
    worst_snap = snapshots.get(worst_group["layer_hash"], {})
    config_diff = diff_snapshots(best_snap, worst_snap)

    # Extract anonymized content summaries
    best_summary = extract_content_summary(best_snap)
    worst_summary = extract_content_summary(worst_snap)

    # Extract positive dirty/canonical outlier inspiration candidates and
    # negative isolated regressions. These are kept separate from baseline means.
    inspiration_candidates = []
    isolated_regressions = []
    for group in groups_with_data:
        label = outlier_labels.get(group["layer_hash"], "normal")
        if group.get("sessions", 0) < 3:
            continue
        snap = snapshots.get(group["layer_hash"], {})
        candidate = {
            "layer_hash": group["layer_hash"],
            "agent_version": group.get("agent_version", ""),
            "sessions": group["sessions"],
            "users": group["users"],
            "success_proxy": group["success_proxy"],
            "tool_error_rate": group["tool_error_rate"],
            "is_canonical": snap.get("drift", {}).get("is_canonical", None),
            "content_summary": extract_content_summary(snap),
            "confidence": confidence,
        }
        if label == "positive_outlier":
            inspiration_candidates.append(
                {
                    **candidate,
                    "diff_vs_baseline": diff_snapshots(worst_snap, snap) if worst_snap else {},
                }
            )
        elif label == "negative_outlier":
            isolated_regressions.append(
                {
                    **candidate,
                    "diff_vs_best": diff_snapshots(best_snap, snap) if best_snap else {},
                    "baseline_policy": "excluded_from_canonical_mean",
                }
            )

    # Extract version pins if available
    best_versions = best_snap.get("pinned_versions", {})
    worst_versions = worst_snap.get("pinned_versions", {})

    return {
        "group_count": len(groups),
        "total_sessions": sum(g["sessions"] for g in groups),
        "total_users": sum(g["users"] for g in groups),
        "significant": True,
        "confidence": confidence,
        "success_gap_pct": round(success_gap * 100, 1),
        "error_gap_pct": round(error_gap * 100, 1),
        "canonical_dirty_summary": {
            "canonical_sessions": canonical_sessions,
            "dirty_sessions": dirty_sessions,
            "canonical_users": canonical_users,
            "dirty_users": dirty_users,
        },
        "groups": [
            {
                "layer_hash": g["layer_hash"],
                "sessions": g["sessions"],
                "users": g["users"],
                "success_proxy": g["success_proxy"],
                "tool_error_rate": g["tool_error_rate"],
                "avg_cost": g["avg_cost"],
                "avg_duration_seconds": g["avg_duration_seconds"],
                "outlier_label": outlier_labels.get(g["layer_hash"], "normal"),
                "is_canonical": snapshots.get(g["layer_hash"], {}).get("drift", {}).get("is_canonical", None),
            }
            for g in groups_with_data
        ],
        "best_config": {
            "layer_hash": best_group["layer_hash"],
            "metrics": best_group,
            "content_summary": best_summary,
            "versions": best_versions,
        },
        "worst_config": {
            "layer_hash": worst_group["layer_hash"],
            "metrics": worst_group,
            "content_summary": worst_summary,
            "versions": worst_versions,
        },
        "config_diff_best_vs_worst": config_diff,
        "inspiration_candidates": inspiration_candidates,
        "isolated_regressions": isolated_regressions,
    }
