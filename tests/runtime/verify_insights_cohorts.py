# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Disposable-only: real ingest/CH, qualified pipeline/cache and authenticated PG/API.

Model stubs affect this verification process only. No hosted configuration,
telemetry repair, paid provider or personal profile writes.
"""

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx

from database import async_session
from models.agent import Agent, AgentStatus, AgentVersion
from models.insight_report import InsightReport, InsightReportStatus
from models.user import User, UserRole
from services.insights import _deps, configure_insights, generator
from services.insights.batch import _load_agent_config
from services.insights.session_meta_extractor import extract_all_session_metas
from version import get_server_version

PROMPT = "Write one focused candidate test. Never execute it."


async def main():
    """Verify only a disposable instance; keep fixture passwords in the caller environment."""
    if os.environ.get("INSIGHTS_DISPOSABLE_VERIFY") != "1":
        raise RuntimeError("Explicit disposable verification required")
    suffix = uuid.uuid4().hex[:8]
    password = os.environ.get("INSIGHTS_RUNTIME_PASSWORD", "")
    if len(password) < 24:
        raise RuntimeError(
            "Pass an ephemeral INSIGHTS_RUNTIME_PASSWORD (24+ chars) to this process and the browser runner"
        )
    async with async_session() as db:
        owner = User(
            email=f"cohort-{suffix}@example.com",
            username="cohort-" + suffix,
            name="Cohort fixture owner",
            role=UserRole.user,
        )
        owner.set_password(password)
        db.add(owner)
        await db.flush()
        agent = Agent(
            name="cohort-agent-" + suffix,
            namespace=owner.username,
            slug="cohort-agent-" + suffix,
            owner=owner.username,
            created_by=owner.id,
        )
        db.add(agent)
        await db.flush()
        version = AgentVersion(
            agent_id=agent.id,
            version="1.0.0",
            description="Instruction cohort fixture",
            prompt=PROMPT,
            model_name="auto",
            supported_harnesses=["pi"],
            status=AgentStatus.approved,
            released_by=owner.id,
            released_at=datetime.now(UTC),
        )
        db.add(version)
        await db.flush()
        agent.latest_version_id = version.id
        await db.commit()
        owner_id, agent_id, version_id = str(owner.id), str(agent.id), version.id
        config = await _load_agent_config(db, agent.id, version_id=version.id)
        config["_instruction_contract"]["names"] = [agent.name, f"{agent.namespace}/{agent.slug}"]
    now = datetime.now(UTC)
    start, end = (
        (now - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),
        (now + timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S"),
    )
    parent_id = "cohort-parent-" + suffix
    samples = [
        (parent_id, PROMPT, ""),
        ("cohort-default-" + suffix, "Base coding assistant", ""),
        ("cohort-missing-" + suffix, None, ""),
        ("cohort-child-" + suffix, PROMPT, parent_id),
    ]
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", timeout=60, headers={"X-Observal-CLI-Version": get_server_version()}
    ) as client:
        auth = await client.post(
            "/api/v1/auth/login", json={"email": f"cohort-{suffix}@example.com", "password": password}
        )
        auth.raise_for_status()
        headers = {"Authorization": "Bearer " + auth.json()["access_token"]}
        for sid, role, parent in samples:
            records = [{"type": "session", "id": sid, "cwd": "/disposable", "timestamp": now.isoformat()}]
            if role is not None:
                records.append(
                    {
                        "type": "message",
                        "timestamp": now.isoformat(),
                        "message": {
                            "role": "system",
                            "sections": {
                                "preamble": "Base harness",
                                "project_context": f'<project_instructions path="/home/fixture/.pi/agent/AGENTS.md">\n{role}\n</project_instructions>',
                            },
                        },
                    }
                )
            records.extend(
                [
                    {
                        "type": "message",
                        "timestamp": now.isoformat(),
                        "message": {
                            "role": "user",
                            "content": "Reproduce the parser issue with a candidate test and report unverified boundaries.",
                        },
                    },
                    {
                        "type": "message",
                        "timestamp": (now + timedelta(seconds=1)).isoformat(),
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Candidate test; not executed."}],
                        },
                    },
                ]
            )
            response = await client.post(
                "/api/v1/ingest/session",
                headers=headers,
                json={
                    "session_id": sid,
                    "harness": "pi",
                    "agent_id": agent_id,
                    "agent_version": "1.0.0",
                    "parent_session_id": parent or None,
                    "parent_metadata_known": True,
                    "lines": [json.dumps(e) for e in records],
                },
            )
            response.raise_for_status()
        configure_insights()
        seen = []

        async def model(prompt, **kwargs):
            """Return deterministic facets only in this verification process."""
            seen.append(prompt)
            return {
                "goal_categories": ["write_tests"],
                "outcome": "mostly_achieved",
                "brief_summary": "Candidate reported",
                "user_satisfaction": "unsure",
                "agent_helpfulness": "moderately_helpful",
                "friction_points": [],
            }

        _deps.call_model = model

        async def sections(**kwargs):
            """Assert private contracts/rosters never enter provider-bound data."""
            assert "_instruction_contract" not in kwargs["data_block"]
            assert "private-roster-fixture" not in json.dumps(kwargs.get("previous_report"))
            return {"suggestions": {"config_additions": [], "features_to_try": [], "usage_patterns": []}}

        generator.generate_sections = sections
        async with async_session() as db:
            metas = await extract_all_session_metas(
                agent_id, start, end, agent_version="1.0.0", expected_instruction=config["_instruction_contract"]
            )
            assert len(metas) == 4, len(metas)
            result = await generator.generate_report_content(
                "cohort-agent-" + suffix,
                agent_id,
                "1.0.0",
                period_start=start,
                period_end=end,
                agent_config=config,
                previous_metrics={
                    "metrics": {"cohort": {"bound_sessions": 1, "members": [{"user_id": "private-roster-fixture"}]}}
                },
                db=db,
            )
            coverage = result["metrics"]["cohort"]
            assert (
                result["sessions_analyzed"] == 1 and coverage["bound_sessions"] == 4 and coverage["child_sessions"] == 1
            ), coverage
            assert coverage["decisions"] == {"verified": 1, "mismatch": 1, "unknown": 1, "child_session": 1}, coverage
            assert result["metrics"]["rich"]["user_turns"] == 1
            assert len(seen) == 1, "Only the qualified top-level session should reach facet extraction"
            report = InsightReport(
                agent_id=uuid.UUID(agent_id),
                agent_version_id=version_id,
                triggered_by=uuid.UUID(owner_id),
                agent_version="1.0.0",
                status=InsightReportStatus.completed,
                period_start=now - timedelta(minutes=1),
                period_end=now,
                metrics=result["metrics"],
                narrative=result["narrative"],
                sessions_analyzed=1,
                completed_at=now,
            )
            db.add(report)
            await db.commit()
            report_id = str(report.id)
            await generator.generate_report_content(
                "cohort-agent-" + suffix,
                agent_id,
                "1.0.0",
                period_start=start,
                period_end=end,
                agent_config=config,
                db=db,
            )
            assert len(seen) == 1, "A source/contract-matched cache should be reused"
        response = await client.get("/api/v1/insights/reports/" + report_id, headers=headers)
        response.raise_for_status()
        assert response.json()["metrics"]["cohort"]["child_sessions"] == 1
        # New identities with the same loaded instructions isolate parent evidence.
        from services.clickhouse import _query
        from services.insights.cohort import select_cohort
        from services.insights.session_meta_extractor import fetch_session_stats

        cases = [
            ("declared", None, True),
            ("legacy-null", None, False),
            ("omitted", None, False),
            ("child", "cohort-parent-" + suffix, False),
            ("unresolved", "not-in-cohort", False),
            ("conflict", None, True),
            ("two-parents", "cohort-parent-" + suffix, False),
        ]
        ids = {}
        for label, parent, known in cases:
            sid = "parent-evidence-" + label + "-" + suffix
            ids[label] = sid
            rows = [
                {"type": "message", "timestamp": now.isoformat(), "message": {"role": "system", "content": PROMPT}},
                {
                    "type": "message",
                    "timestamp": now.isoformat(),
                    "message": {"role": "user", "content": "Report a candidate test"},
                },
                {
                    "type": "message",
                    "timestamp": now.isoformat(),
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "Candidate reported"}]},
                },
            ]
            body = {
                "session_id": sid,
                "harness": "pi",
                "agent_id": agent_id,
                "agent_version": "1.0.0",
                "lines": [json.dumps(row) for row in rows],
            }
            if label != "omitted":
                body["parent_session_id"] = parent
            if known:
                body["parent_metadata_known"] = True
            response = await client.post("/api/v1/ingest/session", headers=headers, json=body)
            response.raise_for_status()
            if label == "declared":
                # Exact source replay must be acknowledged without losing its marker.
                replay = await client.post("/api/v1/ingest/session", headers=headers, json=body)
                replay.raise_for_status()
                assert replay.json()["ingested"] == 0
        for label, parent in [("conflict", parent_id), ("two-parents", "other-parent")]:
            response = await client.post(
                "/api/v1/ingest/session",
                headers=headers,
                json={
                    "session_id": ids[label],
                    "harness": "pi",
                    "agent_id": agent_id,
                    "agent_version": "1.0.0",
                    "lines": [],
                    "parent_session_id": parent,
                    "parent_metadata_known": True,
                },
            )
            response.raise_for_status()
        stats = await fetch_session_stats(agent_id, start, end, agent_version="1.0.0")
        keyed = {key.session_id: value for key, value in stats.items()}
        assert keyed[ids["declared"]]["parent_metadata_known"]
        assert (
            not keyed[ids["legacy-null"]]["parent_metadata_known"]
            and not keyed[ids["omitted"]]["parent_metadata_known"]
        )
        assert (
            not keyed[ids["conflict"]]["parent_metadata_known"]
            and not keyed[ids["two-parents"]]["parent_metadata_known"]
        )
        assert keyed[ids["child"]]["parent_metadata_known"] and keyed[ids["unresolved"]]["parent_metadata_known"]
        # Real raw-event fallback must agree with the materialized summary path.
        import services.insights.session_meta_extractor as extractor

        original_query = extractor.get_query

        async def force_fallback(sql, params):
            """Exercise actual ClickHouse SQL without changing any persisted source."""
            if "FROM session_stats_agg" in sql:
                raise RuntimeError("Fixture primary unavailable")
            return await _query(sql, params)

        extractor.get_query = lambda: force_fallback
        try:
            fallback = await extractor.fetch_session_stats(agent_id, start, end, agent_version="1.0.0")
        finally:
            extractor.get_query = original_query
        assert {key: (v["parent_session_id"], v["parent_metadata_known"]) for key, v in fallback.items()} == {
            key: (v["parent_session_id"], v["parent_metadata_known"]) for key, v in stats.items()
        }
        all_metas = await extract_all_session_metas(
            agent_id, start, end, agent_version="1.0.0", expected_instruction=config["_instruction_contract"]
        )
        selected, matrix_coverage = select_cohort(all_metas)
        assert {meta["session_key"].session_id for meta in selected} == {parent_id, ids["declared"]}
        assert matrix_coverage["policy_version"] == 2
        # A marker-only push may follow an old producer's acknowledged raw lines.
        changed = await client.post(
            "/api/v1/ingest/session",
            headers=headers,
            json={
                "session_id": ids["legacy-null"],
                "harness": "pi",
                "agent_id": agent_id,
                "agent_version": "1.0.0",
                "lines": [],
                "parent_session_id": None,
                "parent_metadata_known": True,
            },
        )
        changed.raise_for_status()
        revised = await extract_all_session_metas(
            agent_id, start, end, agent_version="1.0.0", expected_instruction=config["_instruction_contract"]
        )
        before = next(m for m in all_metas if m["session_key"].session_id == ids["legacy-null"])
        after = next(m for m in revised if m["session_key"].session_id == ids["legacy-null"])
        assert before["instruction_evidence"]["cache_key"] != after["instruction_evidence"]["cache_key"]
        assert after["parent_metadata_known"]
        response = await client.post(
            "/api/v1/ingest/session",
            headers=headers,
            json={
                "session_id": ids["declared"],
                "harness": "pi",
                "agent_id": agent_id,
                "agent_version": "1.0.0",
                "lines": [json.dumps({"changed": "acknowledged source"})],
                "parent_session_id": "rejected-parent",
                "parent_metadata_known": True,
            },
        )
        assert response.status_code == 409
        final_stats = await fetch_session_stats(agent_id, start, end, agent_version="1.0.0")
        assert next(v for key, v in final_stats.items() if key.session_id == ids["declared"])["parent_metadata_known"]
        # Old pre-v2 reports are not recomputed/relabelled in storage.
        old = await client.get("/api/v1/insights/reports/" + report_id, headers=headers)
        old.raise_for_status()
        assert old.json()["metrics"]["cohort"]["bound_sessions"] == 4

    print(
        json.dumps(
            {
                "report_id": report_id,
                "fixture_email": f"cohort-{suffix}@example.com",
                "agent_id": agent_id,
                "bound": 4,
                "verified_top_level": 1,
                "child": 1,
                "classification": "passed",
                "parent_declarations": "passed",
                "unknown_and_conflicts": "passed",
                "primary_fallback_parity": "passed",
                "replay_and_metadata_only": "passed",
                "rejected_source_no_declaration": "passed",
                "pipeline": "passed",
                "cache": "passed",
                "authenticated_read": "passed",
            }
        ),
        flush=True,
    )


asyncio.run(main())
