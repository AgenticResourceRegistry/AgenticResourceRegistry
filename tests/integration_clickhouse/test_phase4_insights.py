# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Isolated 007/030 report proof; never runs against the development sample DB.

Run only with OBSERVAL_CH_PHASE4_URL=http://127.0.0.1:18124/observal_phase4_ci
and the matching CLICKHOUSE_URL and isolated PostgreSQL DATABASE_URL at :15433.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlparse

import pytest
from tests.integration_clickhouse.test_phase3_activity_api import _insert, _stats
from tests.integration_clickhouse.test_phase25_activity import _ALIAS_A, _call, _mapping, _record, _source

import services.clickhouse.client as clickhouse
from api.routes import component_activity as route
from database import engine
from models.insight_report import InsightReport
from schemas.component_activity import ComponentRef
from services.component_activity import projector
from services.insights import component_evidence, configure_insights, generator, render_report_html
from services.insights.component_report import generate_component_content
from services.insights.registry_match import CatalogOffer
from services.insights.scope import SessionKey

_URL = os.getenv("OBSERVAL_CH_PHASE4_URL")
pytestmark = pytest.mark.skipif(not _URL, reason="opt-in isolated Phase 4 report proof")


@pytest.fixture(scope="module", autouse=True)
def _isolated_only():
    if not _URL:
        pytest.skip("isolated Phase 4 proof not configured")
    ch = urlparse(_URL)
    pg = urlparse(os.environ.get("DATABASE_URL", ""))
    assert (ch.hostname, ch.port, ch.path) == ("127.0.0.1", 18124, "/observal_phase4_ci")
    assert (clickhouse.CLICKHOUSE_DB, clickhouse.CLICKHOUSE_USER) == ("observal_phase4_ci", "proof")
    assert (pg.hostname, pg.port, pg.path) == ("127.0.0.1", 15433, "/observal_phase4_ci")


@pytest.mark.asyncio
async def test_same_id_isolation_and_api_report_count_parity(monkeypatch):
    monkeypatch.setattr(clickhouse, "_client", None)
    project, same_id = "phase4-" + uuid.uuid4().hex, "shared-session"
    alice, bob, agent_id, listing_id = "alice", "bob", str(uuid.uuid4()), str(uuid.uuid4())
    period_end = datetime.now(UTC) + timedelta(minutes=2)
    period = (period_end - timedelta(days=1), period_end)
    await _mapping(project, alice, [(_ALIAS_A, listing_id, "verified")])
    for user in (alice, bob):
        label = "ALICE ONLY" if user == alice else "BOB ONLY"
        prompt = {
            "type": "user",
            "timestamp": datetime.now(UTC).isoformat(),
            "message": {"role": "user", "content": f"Private prompt for {label} using {_ALIAS_A}"},
        }
        rows = [
            _source(project, user, same_id, 0, prompt),
            _source(project, user, same_id, 1, _record([_call(_ALIAS_A, user + "-call")])),
        ]
        rows[0]["event_type"] = "user_prompt"
        await _insert("session_events", rows)
        await _insert(
            "session_stats_agg", [{**_stats(project, user, same_id, datetime.now(UTC)), "agent_id": agent_id}]
        )
    assert (await projector.project_session_activity(project, alice, "claude-code", same_id))["status"] == "complete"

    # The API and report must consume the exact same scoped cohort and published
    # activity. Freeze the route window so it is byte-for-byte the report window.
    monkeypatch.setattr(route, "_period", lambda _days: period)
    monkeypatch.setattr(
        route,
        "_authorize",
        AsyncMock(
            return_value=(
                SimpleNamespace(id=uuid.UUID(listing_id)),
                ComponentRef(type="mcp", id=listing_id, qualified_name="test/probe"),
                None,
            )
        ),
    )
    report = InsightReport(
        subject_type="component",
        project_id=project,
        component_type="mcp",
        component_id=uuid.UUID(listing_id),
        component_name=f"test/{_ALIAS_A}",
        triggered_by=uuid.uuid4(),
        period_start=period[0],
        period_end=period[1],
    )
    # API normally uses DEFAULT_PROJECT_ID. For this isolated fixture, route the
    # same explicit project to both consumers instead of sharing test data.
    monkeypatch.setattr(route, "DEFAULT_PROJECT_ID", project)
    api = await route.component_activity_summary("mcp", listing_id, 1, None, SimpleNamespace(), SimpleNamespace())
    configure_insights()

    async def interpret(prompt, **_kwargs):
        assert "ALICE ONLY" not in prompt and "BOB ONLY" not in prompt
        assert f"mcp__{_ALIAS_A}__ping" in prompt
        return {
            "subject_id": listing_id,
            "subject_version_id": None,
            "findings": [
                {
                    "kind": "workflow",
                    "insight": "The published call invoked the probe ping tool.",
                    "confidence": "low",
                    "evidence_refs": ["s0-call0"],
                }
            ],
        }

    monkeypatch.setattr(component_evidence, "get_call_model", lambda: interpret)
    sample_rows, _ = await component_evidence.activity_evidence_sample(
        project, "mcp", listing_id, None, period, limit=8
    )
    call_ref = sample_rows[0]["source_references"][0]
    from_source = await component_evidence._source(
        SessionKey(project, alice, "claude-code", same_id),
        call_ref["source_line_offset"],
        call_ref["_source_line_hash"],
    )
    assert from_source, (call_ref, sample_rows)
    sampled, excerpt_map, _ = await component_evidence._sample(report)
    assert sampled and "s0-goal" not in excerpt_map and "s0-call0" in excerpt_map, (sample_rows, excerpt_map)
    content = await generate_component_content(report)
    assert content["narrative"]["component_analysis"]["state"] == "assessed"
    assert "ALICE ONLY" not in str(content["narrative"]["component_analysis"])
    assert (
        (content["metrics"]["present_sessions"], content["metrics"]["observed_calls"])
        == (
            api.present_sessions,
            api.observed_calls,
        )
        == (1, 1)
    )
    assert content["metrics"]["version_distribution"] == api.version_distribution == {"1.0.0": 1}
    assert content["metrics"]["cohort_collision_calls"] == api.coverage.calls.collision_calls
    assert content["coverage"] == api.coverage.model_dump(mode="json")

    # Both users ran the agent under the SAME session_id. Selection, source read,
    # transcript, cache key and facet input must all remain distinct.
    keys_seen = set()

    async def facet(session, transcript, meta, agent_id, db):
        assert isinstance(session, SessionKey)
        keys_seen.add(session)
        label = "ALICE ONLY" if session.user_id == alice else "BOB ONLY"
        other = "BOB ONLY" if session.user_id == alice else "ALICE ONLY"
        assert label in transcript and other not in transcript
        return {"goal_categories": [label], "brief_summary": label}

    monkeypatch.setattr(generator, "load_cached_facets_batch", AsyncMock(return_value={}))
    monkeypatch.setattr(generator, "extract_and_cache_facets", facet)
    monkeypatch.setattr(generator, "build_catalog", AsyncMock(return_value=CatalogOffer()))
    monkeypatch.setattr(
        generator,
        "generate_sections",
        AsyncMock(
            return_value={
                "at_a_glance": {"whats_working": "ALICE ONLY and BOB ONLY stayed in separate transcripts"},
            }
        ),
    )
    monkeypatch.setattr(generator, "validate_reuse_suggestions", AsyncMock(side_effect=lambda narrative, *_: narrative))
    import services.dynamic_settings as ds

    monkeypatch.setattr(ds, "get", AsyncMock(return_value=5))
    configure_insights()
    generated = await generator.generate_report_content(
        agent_name="fixture",
        agent_id=agent_id,
        period_start=period[0].strftime("%Y-%m-%d %H:%M:%S"),
        period_end=period[1].strftime("%Y-%m-%d %H:%M:%S"),
        db=SimpleNamespace(),
    )
    assert keys_seen == {SessionKey(project, user, "claude-code", same_id) for user in (alice, bob)}
    assert generated["sessions_analyzed"] == generated["facets_summary"]["sessions_with_facets"] == 2
    # Exercise the production HTML export renderer with the generated report,
    # not a fabricated bare-ID transcript lookup.
    exported = render_report_html(
        {
            "id": str(uuid.uuid4()),
            "agent_id": agent_id,
            "agent_version": None,
            "status": "completed",
            "period_start": period[0],
            "period_end": period[1],
            "metrics": generated["metrics"],
            "narrative": generated["narrative"],
            "sessions_analyzed": generated["sessions_analyzed"],
        }
    )
    assert "ALICE ONLY" in exported and "BOB ONLY" in exported
    await engine.dispose()
