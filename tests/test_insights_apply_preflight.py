# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Preview, validation and submission share one builder and never waive review."""

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from models.agent import AgentVersion
from models.inbox import InboxItem
from models.insight_report import InsightReport, InsightReportStatus
from models.review import Review, ReviewRevision, ReviewState
from services.insights.apply_preview import PreflightError
from services.insights.self_learn import apply_insight_suggestions, preview_insight_suggestions
from tests.test_insights_self_learn_reuse import (
    _agent,
    _user,
)
from tests.test_insights_self_learn_reuse import (
    db as _database_fixture,
)

db = _database_fixture

SELECT = {"config_indices": [0], "feature_indices": [], "pattern_indices": []}


async def setup_report(db, addition="Be concise."):
    """Persist a minimal owned report with one independent prompt addition."""
    owner = await _user(db, "owner@example.com")
    agent = await _agent(db, owner)
    now = datetime.now(UTC)
    report = InsightReport(
        agent_id=agent.id,
        status=InsightReportStatus.completed,
        period_start=now,
        period_end=now,
        narrative={
            "suggestions": {
                "config_additions": [
                    {
                        "action_type": "modify_prompt",
                        "where": "system_prompt",
                        "addition": addition,
                        "requires_feature_indices": [],
                    }
                ],
                "features_to_try": [],
                "usage_patterns": [],
            }
        },
    )
    db.add(report)
    await db.commit()
    return owner.id, report.id


async def counts(db):
    """Count proposal artifacts so rollback and failure paths can be checked for writes."""
    return tuple(
        [await db.scalar(select(func.count()).select_from(model)) for model in [AgentVersion, Review, InboxItem]]
    )


@pytest.mark.asyncio
async def test_preview_rolls_back_all_artifacts_and_digest_matches_submission(db):
    """Verify preview rolls back all artifacts and digest matches submission."""
    owner, rid = await setup_report(db)
    before = await counts(db)
    preview = await preview_insight_suggestions(rid, db, owner, SELECT)
    assert preview["ready"] and len(preview["digest"]) == 64
    assert "Be concise." in preview["artifacts"][-1]["files"]["prompt.md"]["content"]
    assert await counts(db) == before
    assert (await db.get(InsightReport, rid)).applied_at is None
    applied = await apply_insight_suggestions(rid, db, owner, SELECT, preview_digest=preview["digest"])
    assert applied["state"] == "submitted_for_review; not_publicly_installable"
    assert applied["preview_digest"] == preview["digest"]
    assert len(applied["reviews"]) == 1
    version = await db.get(AgentVersion, uuid.UUID(applied["agent_version"]["id"]))
    assert version.description == "Base"  # preserve useful identity, not a generated changelog
    assert (await db.get(InsightReport, rid)).applied_items["reviews"] == applied["reviews"]


@pytest.mark.asyncio
async def test_stale_preview_leaves_report_unapplied(db):
    """Verify stale preview leaves report unapplied."""
    owner, rid = await setup_report(db)
    preview = await preview_insight_suggestions(rid, db, owner, SELECT)
    report = await db.get(InsightReport, rid)
    report.narrative = {
        "suggestions": {
            "config_additions": [{"addition": "Changed instruction", "requires_feature_indices": []}],
            "features_to_try": [],
            "usage_patterns": [],
        }
    }
    await db.commit()
    before = await counts(db)
    from sqlalchemy import inspect

    from models.user import User

    authenticated = await db.get(User, owner)
    with pytest.raises(ValueError, match="Preview is stale"):
        await apply_insight_suggestions(rid, db, owner, SELECT, preview_digest=preview["digest"])
    assert not inspect(authenticated).expired
    assert authenticated.id == owner
    assert await counts(db) == before
    assert (await db.get(InsightReport, rid)).applied_at is None


@pytest.mark.asyncio
async def test_raw_secret_fails_without_leaking_or_committing(db):
    """Verify raw secret fails without leaking or committing."""
    token = "sk-proj-" + "a" * 32
    owner, rid = await setup_report(db, "Use " + token)
    before = await counts(db)
    preview = await preview_insight_suggestions(rid, db, owner, SELECT)
    assert not preview["ready"] and any(e["check"] == "secrets" for e in preview["errors"])
    assert token not in json.dumps(preview)
    with pytest.raises(PreflightError) as error:
        await apply_insight_suggestions(rid, db, owner, SELECT, preview_digest=preview["digest"])
    assert token not in str(error.value.errors)
    assert await counts(db) == before
    assert (await db.get(InsightReport, rid)).applied_at is None


@pytest.mark.asyncio
async def test_generated_review_never_auto_publishes_when_checks_clear(db):
    """Verify generated review never auto publishes when checks clear."""
    from services.review.decisions import gate, sync_state
    from services.review.policy import ApprovalPolicy

    owner, rid = await setup_report(db)
    applied = await apply_insight_suggestions(rid, db, owner, SELECT)
    review = await db.get(Review, uuid.UUID(applied["reviews"][0]["id"]))
    revision = await db.get(ReviewRevision, review.head_revision_id)
    revision.checks = [{**c, "status": "pass"} for c in revision.checks]
    revision.checks_pending = False
    await db.flush()
    policy = ApprovalPolicy(required_approvals=dict.fromkeys(ApprovalPolicy().required_approvals, 0), auto_publish=True)
    assert (await gate(db, review, policy=policy)).clear
    await sync_state(db, review, policy=policy)
    assert review.state != ReviewState.published


@pytest.mark.asyncio
async def test_required_feature_cannot_be_silently_unselected(db):
    """Verify required feature cannot be silently unselected."""
    owner, rid = await setup_report(db)
    report = await db.get(InsightReport, rid)
    report.narrative = {
        "suggestions": {
            "config_additions": [{"addition": "Use the skill", "requires_feature_indices": [0]}],
            "features_to_try": [
                {"action_type": "create_new_skill", "feature": "Skill", "name": "needed", "example": "Read the input"}
            ],
            "usage_patterns": [],
        }
    }
    await db.commit()
    before = await counts(db)
    with pytest.raises(ValueError, match="requires an unselected feature"):
        await preview_insight_suggestions(rid, db, owner, SELECT)
    assert await counts(db) == before


@pytest.mark.parametrize(
    "header,event,mode",
    [
        ("before tool use", "PreToolUse", "sync"),
        ("PreToolUse", "PreToolUse", "sync"),
        ("after tool use", "PostToolUse", "async"),
        ("PostToolUse", "PostToolUse", "async"),
        ("before prompt", "UserPromptSubmit", "sync"),
        ("session start", "SessionStart", "async"),
        ("session end", "Stop", "blocking"),
        ("Notification", "Notification", "async"),
        ("SubagentStop", "SubagentStop", "async"),
    ],
)
def test_explicit_hook_mapping(header, event, mode):
    """Verify explicit hook mapping."""
    from services.insights.self_learn import _parse_hook_example

    actual_event, actual_mode, body = _parse_hook_example(f"# Hook: {header}\n#!/usr/bin/env bash\nexit 0")
    assert (actual_event, actual_mode) == (event, mode) and body.startswith("#!/")


@pytest.mark.parametrize("header", ["before commit", "before push", "unknown", "session after tool", ""])
def test_ambiguous_hook_events_are_rejected(header):
    """Verify ambiguous hook events are rejected."""
    from services.insights.self_learn import _parse_hook_example

    with pytest.raises(ValueError):
        _parse_hook_example(f"# Hook: {header}\n#!/bin/bash\nexit 0")


def test_hook_validation_is_syntax_only_and_never_executes(tmp_path):
    """Verify hook validation is syntax only and never executes."""
    from services.insights.self_learn import _normalize_script

    sentinel = tmp_path / "must-not-exist"
    script = f"#!/bin/bash\ntouch '{sentinel}'\n"
    assert _normalize_script(script, "test") == script
    assert not sentinel.exists()
    with pytest.raises(ValueError):
        _normalize_script("def hook(): pass", "test")


def test_secret_boundaries_and_structured_context():
    """Verify secret boundaries and structured context."""
    from services.review.secret_scanner import CREDENTIAL, secret_paths

    harmless = "bug-repro-ask-answers-not-investigations"
    assert not CREDENTIAL.search(harmless)
    assert not secret_paths({"qualified_name": "owner/" + harmless}, "lock_snapshot")
    assert CREDENTIAL.search("prefixsk-proj-" + "a" * 32)
    assert secret_paths({"api_key": "prefixsk-" + "a" * 32}, "config") == ["config.api_key"]


@pytest.mark.asyncio
@pytest.mark.parametrize("actor_kind", ["owner", "co_author", "admin", "reader"])
@pytest.mark.parametrize("operation", ["apply", "preview"])
@pytest.mark.parametrize("scoped", [False, True])
async def test_actual_route_dependencies_allow_editors_not_readers(monkeypatch, actor_kind, operation, scoped):
    """Verify actual route dependencies allow editors not readers."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    import api.deps as deps
    import api.routes.agent.insights as scoped_api
    import api.routes.insights as api
    import services.dynamic_settings as settings
    import services.insights.self_learn as pipeline
    from models.user import UserRole
    from tests.test_insights_access import _agent as access_agent
    from tests.test_insights_access import _report, _result
    from tests.test_insights_access import _user as access_user

    uid = uuid.uuid4()
    user = access_user(user_id=uid, role=UserRole.admin if actor_kind == "admin" else UserRole.user)
    agent = access_agent(
        created_by=uid if actor_kind == "owner" else uuid.uuid4(), co_authors=[uid] if actor_kind == "co_author" else []
    )
    report = _report(agent.id)
    report.subject_type = "agent"
    session = SimpleNamespace(
        execute=AsyncMock(side_effect=lambda stmt: _result(report if "insight_reports" in str(stmt) else agent)),
        get=AsyncMock(return_value=report),
        scalar=AsyncMock(return_value=None),
    )
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(scoped_api.router)
    app.dependency_overrides[deps.get_current_user] = lambda: user
    app.dependency_overrides[deps.get_db] = lambda: session
    monkeypatch.setattr(api, "_resolve_insights_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(settings, "get_bool", AsyncMock(return_value=True))
    backend = AsyncMock(return_value={"ready": True, "digest": "0" * 64})
    monkeypatch.setattr(
        pipeline, "apply_insight_suggestions" if operation == "apply" else "preview_insight_suggestions", backend
    )
    path = (
        f"/api/v1/agents/{agent.id}/insights/reports/{report.id}/{operation}"
        if scoped
        else f"/api/v1/insights/reports/{report.id}/{operation}"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(path, json={"config_indices": [0], "preview_digest": "0" * 64})
    assert response.status_code == (403 if actor_kind == "reader" else 200), response.text
    assert backend.await_count == (0 if actor_kind == "reader" else 1)


def test_embedded_generic_keys_in_credential_maps_still_fail():
    """Verify embedded generic keys in credential maps still fail."""
    from services.review.secret_scanner import CREDENTIAL, secret_paths

    token = "prefixsk-" + "a" * 32
    assert secret_paths({"accessToken": token}, "config") == ["config.accessToken"]
    assert secret_paths({"Custom": token}, "headers") == ["headers.Custom"]
    assert CREDENTIAL.search("variable_sk-" + "a" * 32)


def test_pi_delivery_contract_uses_existing_supported_matrix():
    """Verify pi delivery contract uses existing supported matrix."""
    from services.harness import ensure_loaded, get_adapter

    ensure_loaded()
    pi = get_adapter("pi")
    command = {
        "name": "command",
        "event": "PreToolUse",
        "handler_type": "command",
        "handler_config": {"command": "exit 2"},
    }
    assert pi.hook_delivery_errors(command, "user") == []
    assert pi.hook_delivery_errors(command, "project")
    assert pi.hook_delivery_errors(
        {**command, "script_filename": "guard.sh", "script_content": "#!/bin/bash\nexit 2"}, "user"
    )
    assert pi.hook_delivery_errors({**command, "event": "Stop"}, "user")


@pytest.mark.asyncio
async def test_assembled_reused_hook_is_checked_for_real_delivery(db):
    """Verify assembled reused hook is checked for real delivery."""
    from models.agent_component import AgentComponent
    from models.hook import HookListing, HookVersion
    from models.mcp import ListingStatus

    owner, rid = await setup_report(db)
    report = await db.get(InsightReport, rid)
    base = await db.scalar(select(AgentVersion).where(AgentVersion.agent_id == report.agent_id))
    base.supported_harnesses = ["pi"]
    listing = HookListing(
        name="unsupported-stop", namespace="test", slug="unsupported-stop", owner="owner", submitted_by=owner
    )
    db.add(listing)
    await db.flush()
    hook = HookVersion(
        listing_id=listing.id,
        version="1.0.0",
        description="Stop hook",
        event="Stop",
        handler_type="command",
        handler_config={"command": "exit 0"},
        supported_harnesses=["pi"],
        status=ListingStatus.approved,
        released_by=owner,
        released_at=datetime.now(UTC),
    )
    db.add(hook)
    await db.flush()
    listing.latest_version_id = hook.id
    db.add(
        AgentComponent(
            agent_version_id=base.id,
            component_type="hook",
            component_id=listing.id,
            component_name=listing.name,
            resolved_version="1.0.0",
            resolved_version_id=hook.id,
            order_index=0,
        )
    )
    await db.commit()
    before = await counts(db)
    preview = await preview_insight_suggestions(rid, db, owner, SELECT)
    assert not preview["ready"] and any(e["check"] == "delivery" for e in preview["errors"])
    assert await counts(db) == before


@pytest.mark.parametrize("action", ["No_Action", "Remove_Component"])
@pytest.mark.asyncio
async def test_mixed_case_skipped_features_cannot_satisfy_prompt_dependencies(db, action):
    """Match the feature loop's normalization before allowing dependent instructions."""
    owner, rid = await setup_report(db)
    report = await db.get(InsightReport, rid)
    report.narrative = {
        "suggestions": {
            "config_additions": [{"addition": "Use the required feature", "requires_feature_indices": [0]}],
            "features_to_try": [{"action_type": action, "feature": "Unused"}],
            "usage_patterns": [],
        }
    }
    await db.commit()
    before = await counts(db)
    with pytest.raises(ValueError, match="not removed or skipped"):
        await preview_insight_suggestions(rid, db, owner, {**SELECT, "feature_indices": [0]})
    assert await counts(db) == before


def test_generated_hook_prompt_requests_executable_requirements():
    """The model schema and format rules must expose hook preflight dependencies."""
    from services.insights.sections import SECTION_PROMPTS

    prompt = SECTION_PROMPTS["suggestions"]
    assert '"requirements": ["jq"]' in prompt
    assert "Every create_new_hook MUST include requirements" in prompt


@pytest.mark.asyncio
async def test_hook_syntax_validation_runs_outside_the_event_loop(monkeypatch):
    """The blocking parse-only subprocess must not stall concurrent API requests."""
    import threading
    from types import SimpleNamespace

    from services.insights import self_learn

    current = threading.get_ident()
    observed = []

    def normalize(script, name):
        """Record the worker identity without spawning or executing a shell."""
        observed.append(threading.get_ident())
        return script

    monkeypatch.setattr(self_learn, "_normalize_script", normalize)
    agent = SimpleNamespace(name="fixture", supported_harnesses=[])
    with pytest.raises(ValueError, match="Agent must declare harnesses"):
        await self_learn._create_hook_listing(
            agent, {"example": "# Hook: PreToolUse\n#!/bin/bash\nexit 0"}, uuid.uuid4(), None
        )
    assert len(observed) == 1 and observed[0] != current
