# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Real API/Postgres review-flow proof. ONLY run in a disposable stack.

Creates synthetic accounts/reports and simulates a distinct reviewer/publisher.
No paid model, hosted data, historical repair, local profile install or override.
Set INSIGHTS_DISPOSABLE_VERIFY=1 explicitly in the disposable API container.
"""

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, select

from database import async_session
from models.agent import Agent, AgentStatus, AgentVersion
from models.inbox import InboxItem
from models.insight_report import InsightReport, InsightReportStatus
from models.prompt import PromptListing, PromptVersion
from models.review import Review, ReviewRevision, ReviewState
from models.user import User, UserRole
from services.review.decisions import gate, publish, submit_verdict
from version import get_server_version


async def counts():
    """Count proposal artifacts so rollback and failure paths can be checked for writes."""
    async with async_session() as db:
        return [
            await db.scalar(select(func.count()).select_from(model))
            for model in (AgentVersion, PromptListing, PromptVersion, Review, ReviewRevision, InboxItem)
        ]


async def main():
    """Exercise the guarded disposable stack without changing hosted settings or model providers."""
    if os.environ.get("INSIGHTS_DISPOSABLE_VERIFY") != "1":
        raise RuntimeError("Requires explicit disposable verification")
    import threading
    from types import SimpleNamespace

    from services.insights import self_learn

    caller_thread = threading.get_ident()
    worker_threads = []
    original_normalize = self_learn._normalize_script

    def normalize(script, name):
        """Observe the real parse-only subprocess without changing its validation."""
        worker_threads.append(threading.get_ident())
        return original_normalize(script, name)

    self_learn._normalize_script = normalize
    try:
        await self_learn._create_hook_listing(
            SimpleNamespace(name="runtime-hook", supported_harnesses=[]),
            {"example": "# Hook: PreToolUse\n#!/bin/bash\nexit 0"},
            uuid.uuid4(),
            None,
        )
    except ValueError as error:
        assert "Agent must declare harnesses" in str(error)
    finally:
        self_learn._normalize_script = original_normalize
    assert len(worker_threads) == 1 and worker_threads[0] != caller_thread
    suffix = uuid.uuid4().hex[:8]
    password = os.environ.get("INSIGHTS_RUNTIME_PASSWORD", "")
    if len(password) < 24:
        raise RuntimeError("Pass an ephemeral INSIGHTS_RUNTIME_PASSWORD (24+ chars) to this process and browser runner")
    async with async_session() as db:
        people = {}
        for name, role in (
            ("owner", UserRole.user),
            ("reader", UserRole.user),
            ("reviewer", UserRole.reviewer),
            ("publisher", UserRole.admin),
        ):
            user = User(
                email=f"{name}-{suffix}@example.com", username=f"apply-{name}-{suffix}", name=f"Suite {name}", role=role
            )
            user.set_password(password)
            db.add(user)
            await db.flush()
            people[name] = user.id
        owner = await db.get(User, people["owner"])
        agent = Agent(
            name="suite-agent-" + suffix,
            namespace=owner.username,
            slug="suite-agent-" + suffix,
            owner=owner.username,
            created_by=owner.id,
        )
        db.add(agent)
        await db.flush()
        base = AgentVersion(
            agent_id=agent.id,
            version="1.0.0",
            description="Preserved suite description",
            prompt="Base instructions.",
            model_name="auto",
            supported_harnesses=["claude-code"],
            status=AgentStatus.approved,
            released_by=owner.id,
            released_at=datetime.now(UTC),
        )
        db.add(base)
        await db.flush()
        agent.latest_version_id = base.id
        now = datetime.now(UTC)
        narrative = {
            "suggestions": {
                "config_additions": [
                    {
                        "action_type": "modify_prompt",
                        "addition": "Be concise.",
                        "where": "system_prompt",
                        "requires_feature_indices": [],
                    }
                ],
                "features_to_try": [],
                "usage_patterns": [
                    {
                        "title": "Ask answers not investigations",
                        "detail": "Synthetic verification prompt",
                        "copyable_prompt": "Suite verification pattern: report before acting.",
                    }
                ],
            }
        }
        report = InsightReport(
            agent_id=agent.id,
            triggered_by=owner.id,
            status=InsightReportStatus.completed,
            period_start=now - timedelta(days=1),
            period_end=now,
            narrative=narrative,
            metrics={"overview": {"total_sessions": 1}},
            sessions_analyzed=1,
            completed_at=now,
        )
        db.add(report)
        await db.flush()
        agent_id, report_id = str(agent.id), str(report.id)
        await db.commit()
    body = {"config_indices": [0], "feature_indices": [], "pattern_indices": [0]}
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", timeout=60, headers={"X-Observal-CLI-Version": get_server_version()}
    ) as client:

        async def login(name):
            """Authenticate the disposable fixture user without printing its credentials."""
            response = await client.post(
                "/api/v1/auth/login", json={"email": f"{name}-{suffix}@example.com", "password": password}
            )
            response.raise_for_status()
            return {"Authorization": "Bearer " + response.json()["access_token"]}

        owner_headers, reader_headers = await login("owner"), await login("reader")
        path = f"/api/v1/agents/{agent_id}/insights/reports/{report_id}"
        denied = await client.post(path + "/preview", json=body, headers=reader_headers)
        assert denied.status_code == 403, denied.text
        before = await counts()
        response = await client.post(path + "/preview", json=body, headers=owner_headers)
        response.raise_for_status()
        preview = response.json()
        assert preview["ready"] and [a["type"] for a in preview["artifacts"]] == ["prompt", "agent"], preview
        assert await counts() == before
        # A fake digest must not commit any part of the staged bundle.
        rejected = await client.post(path + "/apply", json={**body, "preview_digest": "0" * 64}, headers=owner_headers)
        assert rejected.status_code == 400 and "stale" in rejected.text.lower(), rejected.text
        assert await counts() == before
        response = await client.post(
            path + "/apply", json={**body, "preview_digest": preview["digest"]}, headers=owner_headers
        )
        response.raise_for_status()
        items = response.json()["items"]
        assert items["state"] == "submitted_for_review; not_publicly_installable"
        assert [r["type"] for r in items["reviews"]] == ["prompt", "agent"]
        target_version = items["agent_version"]["version"]
        rejected = await client.post(
            f"/api/v1/agents/{agent_id}/install",
            headers=reader_headers,
            json={"harness": "claude-code", "version": target_version, "strict": True},
        )
        assert rejected.status_code == 404, rejected.text
        applied_counts = await counts()
        repeated = await client.post(
            path + "/apply", json={**body, "preview_digest": preview["digest"]}, headers=owner_headers
        )
        assert repeated.status_code == 400 and await counts() == applied_counts

        async with async_session() as db:
            parent_review = await db.get(Review, uuid.UUID(items["reviews"][-1]["id"]))
            assert "pinned_components" in (await gate(db, parent_review)).requirements
            reviewer, publisher = await db.get(User, people["reviewer"]), await db.get(User, people["publisher"])
            for info in items["reviews"]:
                review = await db.get(Review, uuid.UUID(info["id"]))
                # Default local check policy may leave no queued CI; do not pretend it ran.
                revision = await db.get(ReviewRevision, review.head_revision_id)
                assert not revision.checks_pending, "Wait for the real worker checks before publishing"
                await submit_verdict(db, review, reviewer, "approve")
                assert review.state != ReviewState.published
                await publish(db, review, publisher)
                await db.commit()
            parent = await db.get(AgentVersion, uuid.UUID(items["agent_version"]["id"]))
            assert parent.description == "Preserved suite description"
        installed = await client.post(
            f"/api/v1/agents/{agent_id}/install",
            headers=reader_headers,
            json={"harness": "claude-code", "version": target_version, "strict": True},
        )
        installed.raise_for_status()
        assert installed.json()["version"] == target_version
        assert "Be concise." in json.dumps(installed.json()["config_snippet"])

        # Independent bad report: real token shape must fail without leaking or committing.
        token = "sk-proj-" + "a" * 32
        async with async_session() as db:
            bad = InsightReport(
                agent_id=uuid.UUID(agent_id),
                triggered_by=people["owner"],
                status=InsightReportStatus.completed,
                period_start=now - timedelta(days=1),
                period_end=now,
                narrative={
                    "suggestions": {
                        "config_additions": [{"addition": "Use " + token, "requires_feature_indices": []}],
                        "features_to_try": [],
                        "usage_patterns": [],
                    }
                },
            )
            db.add(bad)
            await db.commit()
            bad_id = str(bad.id)
        before_bad = await counts()
        failed = await client.post(
            f"/api/v1/insights/reports/{bad_id}/preview", headers=owner_headers, json={"config_indices": [0]}
        )
        failed.raise_for_status()
        assert not failed.json()["ready"] and token not in failed.text
        refused = await client.post(
            f"/api/v1/insights/reports/{bad_id}/apply",
            headers=owner_headers,
            json={"config_indices": [0], "preview_digest": failed.json()["digest"]},
        )
        assert refused.status_code == 400 and token not in refused.text
        assert await counts() == before_bad
    # Separate unapplied UI fixture: exercise preview/confirmation without
    # reusing an already-published report or colliding with its prompt name.
    async with async_session() as db:
        ui = InsightReport(
            agent_id=uuid.UUID(agent_id),
            triggered_by=people["owner"],
            status=InsightReportStatus.completed,
            period_start=now - timedelta(days=1),
            period_end=now,
            completed_at=now,
            metrics={"overview": {"total_sessions": 1}},
            sessions_analyzed=1,
            narrative={
                "suggestions": {
                    "config_additions": [
                        {
                            "action_type": "modify_prompt",
                            "addition": "Be precise.",
                            "where": "system_prompt",
                            "requires_feature_indices": [],
                        }
                    ],
                    "features_to_try": [],
                    "usage_patterns": [
                        {
                            "title": "Review evidence summary",
                            "detail": "Disposable UI test",
                            "copyable_prompt": "UI fixture: list verification boundaries.",
                        }
                    ],
                }
            },
        )
        db.add(ui)
        await db.commit()
        ui_id = str(ui.id)
    print(
        json.dumps(
            {
                "agent_id": agent_id,
                "report_id": report_id,
                "ui_report_id": ui_id,
                "fixture_email": f"owner-{suffix}@example.com",
                "bad_report_id": bad_id,
                "owner_permissions": "passed",
                "reader_denied": "passed",
                "preview_rollback": "passed",
                "stale_rollback": "passed",
                "dependency_publication": "passed",
                "strict_install": "passed",
                "secret_rejection": "passed",
                "version": target_version,
            }
        ),
        flush=True,
    )


asyncio.run(main())
