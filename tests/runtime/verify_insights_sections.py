# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Deterministic model fault injection plus real PostgreSQL/API verification.

Only run in a disposable container with INSIGHTS_DISPOSABLE_VERIFY=1.
The model is stubbed in this process, never in the running server or its settings.
"""

import asyncio
import json
import os
import re
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from database import async_session
from models.agent import Agent
from models.insight_report import InsightReport, InsightReportStatus
from models.user import User, UserRole
from services.insights import sections
from services.insights.self_learn import apply_insight_suggestions
from version import get_server_version


async def main():
    """Exercise the guarded disposable stack without changing hosted settings or model providers."""
    if os.environ.get("INSIGHTS_DISPOSABLE_VERIFY") != "1":
        raise RuntimeError("Requires explicit disposable verification")
    calls = {}

    async def model(prompt, **kwargs):
        """Return deterministic process-local fixture responses for the exercised pipeline."""
        match = re.search(r'"([a-z_]+)":\s*\{', prompt)
        if not match:
            return {}
        name = match.group(1)
        calls[name] = calls.get(name, 0) + 1
        if name == "what_works":
            return {
                name: {
                    "intro": "Synthetic answer delivered",
                    "strengths": [{"title": "Fixture retained", "description": "Available analysis remains visible."}],
                }
            }
        return {}  # What the provider layer returns on malformed JSON.

    sections.get_call_model = lambda: model

    async def no_override():
        """Use fixture model selection without modifying persisted configuration."""
        return None

    sections._get_section_model = no_override
    sections._get_synthesis_model = no_override
    narrative = await sections.generate_sections("Synthetic test data, no private session content")
    assert narrative["generation_state"] == "partial"
    assert narrative["what_works"]["intro"] == "Synthetic answer delivered"
    assert any(e["section"] == "suggestions" for e in narrative["section_errors"])
    assert calls["suggestions"] == 2
    print("section generation assertions passed", flush=True)
    async with async_session() as db:
        admin = await db.scalar(select(User).where(User.role == UserRole.super_admin))
        print("disposable admin resolved", flush=True)
        assert admin, "Disposable demo super-admin is required"
        suffix = uuid.uuid4().hex[:8]
        agent = Agent(
            name="section-trial-" + suffix,
            namespace=admin.username,
            slug="section-trial-" + suffix,
            owner=admin.username,
            created_by=admin.id,
        )
        db.add(agent)
        await db.flush()
        now = datetime.now(UTC)
        report = InsightReport(
            agent_id=agent.id,
            triggered_by=admin.id,
            period_start=now - timedelta(days=1),
            period_end=now,
            status=InsightReportStatus.completed,
            narrative=narrative,
            metrics={"overview": {"total_sessions": 1}},
            sessions_analyzed=1,
            completed_at=now,
        )
        db.add(report)
        await db.commit()
        report_id = str(report.id)
        print("synthetic report persisted: " + report_id, flush=True)
        try:
            await apply_insight_suggestions(report.id, db, admin.id)
        except ValueError as error:
            assert "Suggestions generation failed" in str(error)
        else:
            raise AssertionError("Failed suggestions were applied")
        await db.rollback()
        assert (await db.get(InsightReport, uuid.UUID(report_id))).applied_at is None
    print("direct Apply rejection passed", flush=True)
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", timeout=30, headers={"X-Observal-CLI-Version": get_server_version()}
    ) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": os.environ["DEMO_SUPER_ADMIN_EMAIL"], "password": os.environ["DEMO_SUPER_ADMIN_PASSWORD"]},
        )
        login.raise_for_status()
        tokens = login.json()
        # Keep bearer/refresh credentials out of output; screenshots use independent login.
        token = tokens.get("access_token")
        assert token, list(tokens)
        response = await client.get(
            "/api/v1/insights/reports/" + report_id, headers={"Authorization": "Bearer " + token}
        )
        response.raise_for_status()
        stored = response.json()
        assert stored["narrative"]["generation_state"] == "partial"
        assert stored["narrative"]["section_errors"] == narrative["section_errors"]
        response = await client.post(
            "/api/v1/insights/reports/" + report_id + "/apply", json={}, headers={"Authorization": "Bearer " + token}
        )
        assert response.status_code == 400 and "Suggestions generation failed" in response.text, response.text
    print(
        json.dumps(
            {
                "report_id": report_id,
                "generation": "partial",
                "retry": "passed",
                "persistence": "passed",
                "http_read": "passed",
                "apply_rejected": "passed",
            }
        )
    )


asyncio.run(main())
