# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Disposable integration ONLY: requires verified-cohort + recommendation code.

Runs real ingest/CH/pipeline/facet cache/PG/authenticated reads. Model responses
are deterministic process-local fixtures, not a hosted provider/settings change.
"""

import asyncio
import json
import os
import re
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
from services.insights.evidence import source_ref
from services.insights.scope import SessionKey
from version import get_server_version

PROMPT = "Write one focused candidate test. Never execute it."


async def main():
    if os.environ.get("INSIGHTS_DISPOSABLE_VERIFY") != "1":
        raise RuntimeError("Disposable integration verification only")
    suffix = uuid.uuid4().hex[:8]
    password = secrets.token_urlsafe(30)
    now = datetime.now(UTC)
    async with async_session() as db:
        owner = User(
            email=f"evidence-{suffix}@example.com",
            username="evidence-" + suffix,
            name="Evidence fixture owner",
            role=UserRole.user,
        )
        owner.set_password(password)
        db.add(owner)
        await db.flush()
        agent = Agent(
            name="evidence-agent-" + suffix,
            namespace=owner.username,
            slug="evidence-agent-" + suffix,
            owner=owner.username,
            created_by=owner.id,
        )
        db.add(agent)
        await db.flush()
        version = AgentVersion(
            agent_id=agent.id,
            version="1.0.0",
            description="Evidence fixture",
            prompt=PROMPT,
            model_name="auto",
            supported_harnesses=["pi"],
            external_mcps=[{"name": "github", "url": "https://example.com/github-mcp"}],
            status=AgentStatus.approved,
            released_by=owner.id,
            released_at=now,
        )
        db.add(version)
        await db.flush()
        agent.latest_version_id = version.id
        await db.commit()
        oid, aid = str(owner.id), str(agent.id)
        config = await _load_agent_config(db, agent.id, version_id=version.id)
        config["_instruction_contract"]["names"] = [agent.name]
    good_sid, off_sid = "evidence-good-" + suffix, "evidence-off-" + suffix
    good_ref = source_ref(SessionKey("default", oid, "pi", good_sid))
    off_ref = source_ref(SessionKey("default", oid, "pi", off_sid))
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", timeout=60, headers={"X-Observal-CLI-Version": get_server_version()}
    ) as client:
        auth = await client.post(
            "/api/v1/auth/login", json={"email": f"evidence-{suffix}@example.com", "password": password}
        )
        auth.raise_for_status()
        headers = {"Authorization": "Bearer " + auth.json()["access_token"]}
        for sid, task, role, parent in [
            (good_sid, "Write parser candidate test", PROMPT, None),
            (off_sid, "Remove disk caches", PROMPT, None),
            ("evidence-bound-" + suffix, "General work", None, None),
            ("evidence-child-" + suffix, "Write parser candidate test", PROMPT, good_sid),
        ]:
            rows = [{"type": "session", "id": sid, "cwd": "/disposable", "timestamp": now.isoformat()}]
            if role:
                rows.append(
                    {
                        "type": "message",
                        "timestamp": now.isoformat(),
                        "message": {
                            "role": "system",
                            "sections": {
                                "preamble": "Base",
                                "project_context": f'<project_instructions path="/home/test/.pi/agent/AGENTS.md">\n{role}\n</project_instructions>',
                            },
                        },
                    }
                )
            rows.extend(
                [
                    {
                        "type": "message",
                        "timestamp": now.isoformat(),
                        "message": {"role": "user", "content": task + ". Return evidence and unverified boundaries."},
                    },
                    {
                        "type": "message",
                        "timestamp": (now + timedelta(seconds=1)).isoformat(),
                        "message": {"role": "assistant", "content": [{"type": "text", "text": "Candidate reported"}]},
                    },
                ]
            )
            if sid == good_sid:
                rows[-1]["message"]["content"].extend(
                    [
                        {
                            "type": "toolCall",
                            "id": "t1",
                            "name": "mcp",
                            "arguments": {"server": "linear", "tool": "linear_get_issue"},
                        },
                        {"type": "toolCall", "id": "t2", "name": "mcp", "arguments": {"tool": "unknown"}},
                    ]
                )
            response = await client.post(
                "/api/v1/ingest/session",
                headers=headers,
                json={
                    "session_id": sid,
                    "harness": "pi",
                    "agent_id": aid,
                    "agent_version": "1.0.0",
                    "parent_session_id": parent,
                    "lines": [json.dumps(row) for row in rows],
                },
            )
            response.raise_for_status()
        configure_insights()
        facet_calls = []

        async def model(prompt, **kwargs):
            if "Analyze this session and extract structured facets" in prompt:
                facet_calls.append(prompt)
                state = "out_of_scope" if "Remove disk caches" in prompt else "in_scope"
                return {
                    "goal_categories": ["write_tests"],
                    "outcome": "mostly_achieved",
                    "brief_summary": "Candidate reported",
                    "friction_points": [],
                    "agent_task_relevance": {"state": state, "reason": "Synthetic task/role assessment"},
                }
            match = re.search(r'RESPOND WITH ONLY.*?\{\s*"([a-z_]+)"', prompt, re.S)
            name = match.group(1) if match else "unknown"
            if name == "suggestions":
                return {
                    name: {
                        "config_additions": [
                            {
                                "addition": "Scoped candidate rule",
                                "action_type": "modify_prompt",
                                "where": "system_prompt",
                                "requires_feature_indices": [],
                                "evidence_refs": [good_ref],
                            },
                            {"addition": "DELETECACHESRULE", "evidence_refs": [off_ref]},
                            {"addition": "invented rule", "evidence_refs": ["invented"]},
                            {"addition": "unreferenced rule"},
                        ],
                        "features_to_try": [],
                        "usage_patterns": [],
                    }
                }
            if name == "at_a_glance":
                assert "DELETECACHESRULE" not in prompt, "Rejected advice must not reach synthesis"
                return {
                    name: {
                        "health": "mixed",
                        "whats_working": "Candidate reported",
                        "whats_hindering": "Off-task use",
                        "quick_win": "DELETECACHESRULE",
                        "ambitious_workflows": "Off-task plan",
                        "evidence_refs": [off_ref],
                    }
                }
            return {name: {}}

        _deps.call_model = model
        start = (now - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        end = (now + timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S")
        async with async_session() as db:
            result = await generator.generate_report_content(
                agent.name, aid, "1.0.0", period_start=start, period_end=end, agent_config=config, db=db
            )
            assert result["sessions_analyzed"] == 2 and len(facet_calls) == 2, result
            narrative = result["narrative"]
            validation = narrative["evidence_validation"]
            assert validation["instruction_matched_sources"] == 2 and validation["change_eligible_sources"] == 1, (
                validation
            )
            assert [s["addition"] for s in narrative["suggestions"]["config_additions"]] == ["Scoped candidate rule"], (
                narrative
            )
            assert len(validation["rejected_records"]) >= 3
            assert (
                narrative["source_index"][good_ref]["session_id"] == good_sid
                and not narrative["source_index"][off_ref]["eligible_for_changes"]
            )
            assert "DELETECACHESRULE" not in json.dumps(narrative["at_a_glance"])
            assert result["metrics"]["rich"]["observed_mcp_target_attempts"] == {"linear": 1}, result["metrics"]
            assert result["metrics"]["rich"]["unidentified_mcp_attempts"] == 1
            report = InsightReport(
                agent_id=uuid.UUID(aid),
                triggered_by=uuid.UUID(oid),
                agent_version_id=version.id,
                agent_version="1.0.0",
                status=InsightReportStatus.completed,
                period_start=now - timedelta(minutes=1),
                period_end=now,
                completed_at=now,
                metrics=result["metrics"],
                narrative=narrative,
                sessions_analyzed=2,
            )
            db.add(report)
            await db.commit()
            rid = str(report.id)
        response = await client.get("/api/v1/insights/reports/" + rid, headers=headers)
        response.raise_for_status()
        assert response.json()["narrative"]["source_index"][good_ref]["session_id"] == good_sid
    path = Path("/tmp/insights-evidence-ui.json")
    path.write_text(json.dumps({"email": f"evidence-{suffix}@example.com", "password": password, "report_id": rid}))
    path.chmod(0o600)
    print(
        json.dumps(
            {
                "report_id": rid,
                "verified_sources": 2,
                "change_eligible_sources": 1,
                "invalid_off_task_withheld": "passed",
                "synthesis_gate": "passed",
                "actual_mcp_target": "passed",
                "authenticated_source_read": "passed",
            }
        ),
        flush=True,
    )


asyncio.run(main())
