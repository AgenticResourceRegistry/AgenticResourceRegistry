# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Disposable-only: real ingest/CH, qualified pipeline/cache and authenticated PG/API.

Model stubs affect this verification process only. No hosted configuration,
telemetry repair, paid provider or personal profile writes.
"""

import asyncio
import json
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
    if os.environ.get("INSIGHTS_DISPOSABLE_VERIFY") != "1":
        raise RuntimeError("Explicit disposable verification required")
    suffix = uuid.uuid4().hex[:8]
    password = secrets.token_urlsafe(30)
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
                    "lines": [json.dumps(e) for e in records],
                },
            )
            response.raise_for_status()
        configure_insights()
        seen = []

        async def model(prompt, **kwargs):
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
            assert "_instruction_contract" not in kwargs["data_block"]
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
    credentials = Path("/tmp/insights-cohorts-ui.json")
    credentials.write_text(
        json.dumps(
            {
                "email": f"cohort-{suffix}@example.com",
                "password": password,
                "report_id": report_id,
                "agent_id": agent_id,
            }
        )
    )
    credentials.chmod(0o600)
    print(
        json.dumps(
            {
                "report_id": report_id,
                "bound": 4,
                "verified_top_level": 1,
                "child": 1,
                "classification": "passed",
                "pipeline": "passed",
                "cache": "passed",
                "authenticated_read": "passed",
            }
        ),
        flush=True,
    )


asyncio.run(main())
