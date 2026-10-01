# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Opt-in proof: Claude Code hook evidence through real ClickHouse, apart from MCP and skills.

Runs against the same isolated databases as ``test_skill_evidence_projection.py``
(see docs/testing/database-integration-tests.md).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_skill_evidence_projection import (  # noqa: F401  (fixtures are module-scoped autouse)
    _HASH,
    _URL,
    _close_pg_pool,
    _fresh_http_client,
    _insert,
    _isolated_databases_only,
    _period,
    _seed,
    _ts,
)

import services.clickhouse.client as clickhouse
from database import async_session
from models.hook import HookListing
from models.user import User
from services.component_activity import hook_queries, project_session_hook_evidence, project_session_skill_evidence
from services.layer_components import CURRENT_EXTRACTOR_VERSION
from services.layer_components.extractor import ensure_layer_components
from services.projection_generation import next_projection_generation
from services.session_parsers.hook_evidence import hook_binding_sha256

pytestmark = pytest.mark.skipif(not _URL, reason="opt-in isolated hook evidence proof")
_CLAUDE = Path(__file__).parents[1] / "fixtures" / "component_insights" / "claude_code"
PROMPT = ("UserPromptSubmit", ".claude/hooks/probe-prompt.sh")
FAIL = ("PostToolUse", ".claude/hooks/probe-fail.sh")
BLOCK = ("PreToolUse", ".claude/hooks/probe-block.sh")
PRE = ("PreToolUse", ".claude/hooks/probe-pre.sh")


def _lines(name: str) -> list[str]:
    return (_CLAUDE / name).read_text(encoding="utf-8").splitlines()


async def _seed_hooks(project: str, user: str, session: str, fixture: str, hooks: dict[str, tuple]) -> None:
    """Session source rows plus a published mapping of verified hooks: id -> ((event, command), agent)."""
    await _seed(project, user, session, {}, harness="claude-code", lines=_lines(fixture), index=False)
    await _insert(
        "layer_snapshots",
        [
            {
                "project_id": project,
                "user_id": user,
                "hash": _HASH,
                "harness": "claude-code",
                "content": json.dumps({"pinned_versions": {"schema_version": 2, "agents": [], "standalone": []}}),
                "uploaded_at": _ts(datetime.now(UTC)),
                "file_count": 0,
                "total_size": 0,
            }
        ],
    )
    generation = await next_projection_generation()
    if hooks:
        await _insert(
            "layer_components",
            [
                {
                    "project_id": project,
                    "user_id": user,
                    "layer_hash": _HASH,
                    "hash_schema_version": 2,
                    "extractor_version": CURRENT_EXTRACTOR_VERSION,
                    "extraction_generation": generation,
                    "occurrence_key": f"hook-{component_id}",
                    "component_type": "hook",
                    "source": "agent",
                    "harness": "claude-code",
                    "scope": "project",
                    "parent_agent_id": "",
                    "parent_agent_version": "",
                    "raw_listing_id": component_id,
                    "raw_name": "probe",
                    "raw_version": "1.0.0",
                    "qualified_name": "team/probe",
                    "local_name": "probe",
                    "component_id": component_id,
                    "component_version_id": "",
                    "identity_status": "resolved",
                    "verification_status": "verified",
                    "location_sha256": hook_binding_sha256(*binding),
                    "binding_agent": agent,
                }
                for component_id, (binding, agent) in hooks.items()
            ],
        )
    await _insert(
        "layer_component_extractions",
        [
            {
                "project_id": project,
                "user_id": user,
                "layer_hash": _HASH,
                "extractor_version": CURRENT_EXTRACTOR_VERSION,
                "extraction_generation": generation,
                "status": "complete",
                "occurrence_count": len(hooks),
            }
        ],
    )


@pytest.mark.asyncio
async def test_settings_hooks_record_runs_failures_and_blocks_per_hook():
    project, user = "hooks-" + uuid.uuid4().hex, "hook-owner"
    prompt, fail, block = (str(uuid.uuid4()) for _ in range(3))
    hooks = {prompt: (PROMPT, ""), fail: (FAIL, ""), block: (BLOCK, "")}
    await _seed_hooks(project, user, "settings", "hook_session_settings_outcomes.jsonl", hooks)
    published = await project_session_hook_evidence(project, user, "claude-code", "settings")
    assert published["status"] == "complete"
    assert (published["candidate_count"], published["attributed_count"]) == (3, 3)
    period = _period()
    by_hook = {hook: await hook_queries.hook_activity_summary(project, hook, None, period) for hook in hooks}
    assert (by_hook[prompt]["runs_with_output"], by_hook[prompt]["failures"], by_hook[prompt]["blocks"]) == (1, 0, 0)
    assert (by_hook[fail]["runs_with_output"], by_hook[fail]["failures"], by_hook[fail]["blocks"]) == (0, 1, 0)
    assert (by_hook[block]["runs_with_output"], by_hook[block]["failures"], by_hook[block]["blocks"]) == (0, 0, 1)
    for summary in by_hook.values():
        assert summary["eligible_sessions"] == 1 and summary["coverage"].attribution_state == "observed"
        assert any(item.startswith("silent_success_unrecorded") for item in summary["coverage"].limitations)
    # Hook rows never leak into skill evidence for the same component, and re-projection is idempotent.
    assert (await project_session_skill_evidence(project, user, "claude-code", "settings"))["status"] == "complete"
    assert (await project_session_hook_evidence(project, user, "claude-code", "settings"))["status"] == (
        "already_complete"
    )


@pytest.mark.asyncio
async def test_a_silent_hook_is_eligible_with_no_recorded_run_not_unused():
    project, user = "hookq-" + uuid.uuid4().hex, "hook-owner"
    pre = str(uuid.uuid4())
    await _seed_hooks(project, user, "silent", "hook_session_silent_success.jsonl", {pre: (PRE, "")})
    assert (await project_session_hook_evidence(project, user, "claude-code", "silent"))["status"] == "complete"
    summary = await hook_queries.hook_activity_summary(project, pre, None, _period())
    assert (summary["eligible_sessions"], summary["sessions_with_recorded_run"]) == (1, 0)
    assert summary["coverage"].attribution_state == "no_recorded_runs"


@pytest.mark.asyncio
async def test_agent_hooks_count_only_interactive_sessions_of_their_agent():
    project = "hooka-" + uuid.uuid4().hex
    fail = str(uuid.uuid4())
    hooks = {fail: (FAIL, "probe-agent")}
    await _seed_hooks(project, "u-int", "interactive", "hook_session_agent_interactive.jsonl", hooks)
    await _seed_hooks(project, "u-head", "headless", "hook_session_agent_headless.jsonl", hooks)
    await _seed_hooks(project, "u-other", "other", "hook_session_silent_success.jsonl", hooks)  # agent never ran
    for user, session in (("u-int", "interactive"), ("u-head", "headless"), ("u-other", "other")):
        assert (await project_session_hook_evidence(project, user, "claude-code", session))["status"] == "complete"
    summary = await hook_queries.hook_activity_summary(project, fail, None, _period())
    eligibility = summary["coverage"].eligibility
    assert summary["present_sessions"] == 3
    assert (eligibility.eligible_sessions, eligibility.headless_sessions, eligibility.agent_inactive_sessions) == (
        1,
        1,
        1,
    )
    assert (summary["sessions_with_recorded_run"], summary["failures"]) == (1, 1)
    assert summary["coverage"].usage_rate == 1.0 and "agent_hook_headless_sessions" in summary["coverage"].reasons
    sessions, _ = await hook_queries.hook_activity_sessions(
        project, fail, None, _period(), limit=10, cursor=None, cursor_scope=(project, "u", "hook", fail, "", "1")
    )
    assert {(s["session_id"], s["eligibility"], s["failures"]) for s in sessions} == {
        ("interactive", "eligible", 1),
        ("headless", "headless", 0),
        ("other", "agent_inactive", 0),
    }


@pytest.mark.asyncio
async def test_a_real_snapshot_carries_the_binding_and_agent_into_attribution():
    project, user, session = "hooke-" + uuid.uuid4().hex, "hook-owner", "end-to-end"
    listing_id = uuid.uuid4()
    async with async_session() as db:
        owner = User(
            email=f"hook-{uuid.uuid4().hex}@example.invalid",
            username=f"hk{uuid.uuid4().hex[:12]}",
            name="Isolated fixture owner",
        )
        db.add(owner)
        await db.flush()
        db.add(
            HookListing(
                id=listing_id,
                name="probe-fail",
                namespace="team",
                slug=f"h{uuid.uuid4().hex[:12]}",
                owner=owner.username,
                submitted_by=owner.id,
            )
        )
        await db.commit()
    await _seed(
        project,
        user,
        session,
        {},
        harness="claude-code",
        lines=_lines("hook_session_agent_interactive.jsonl"),
        index=False,
    )
    pin = {
        "type": "hook",
        "id": str(listing_id),
        "name": "probe-fail",
        "version": "1.0.0",
        "scope": "project",
        "local_name": "probe-fail",
        "harness": "claude-code",
    }
    verification = {
        "harness": "claude-code",
        "component_id": str(listing_id),
        "alias": "probe-fail",
        "scope": "project",
        "parent_agent_id": "",
        "status": "verified",
        "location_sha256": hook_binding_sha256(*FAIL),
        "hook_agent": "probe-agent",
    }
    content = {
        "pinned_versions": {"schema_version": 2, "agents": [], "standalone": [pin]},
        "drift": {"is_canonical": True, "hook_verifications": [verification]},
    }
    await _insert(
        "layer_snapshots",
        [
            {
                "project_id": project,
                "user_id": user,
                "hash": _HASH,
                "harness": "claude-code",
                "content": json.dumps(content),
                "uploaded_at": _ts(datetime.now(UTC)),
                "file_count": 0,
                "total_size": 0,
            }
        ],
    )
    assert (await ensure_layer_components(project, user, _HASH))["occurrences"] == 1
    (row,) = (
        await clickhouse._query(
            "SELECT verification_status, location_sha256, binding_agent FROM layer_components FINAL "
            "WHERE project_id = {p:String} FORMAT JSON",
            {"param_p": project},
        )
    ).json()["data"]
    assert row == {
        "verification_status": "verified",
        "location_sha256": hook_binding_sha256(*FAIL),
        "binding_agent": "probe-agent",
    }
    assert (await project_session_hook_evidence(project, user, "claude-code", session))["status"] == "complete"
    summary = await hook_queries.hook_activity_summary(project, str(listing_id), None, _period())
    assert (summary["eligible_sessions"], summary["failures"]) == (1, 1)
