# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Gate transitions must observe the state protected by the mutation lock."""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.routes.reviews import submissions
from schemas.review import Reason, VerdictCreate


@pytest.mark.asyncio
async def test_withdraw_then_request_clears_actionable_ready_notice(monkeypatch):
    review = SimpleNamespace(id=uuid.uuid4(), number=1, state=SimpleNamespace(value="open"))
    reviewer = SimpleNamespace(id=uuid.uuid4())
    row = SimpleNamespace(id=uuid.uuid4(), review_id=review.id, reviewer_id=reviewer.id, verdict="request_changes")
    ready = False  # The original request blocks the gate.
    actionable_ready = False
    transitions = []
    lock = asyncio.Lock()
    withdrawing = asyncio.Event()
    finish_withdraw = asyncio.Event()
    request_waiting = asyncio.Event()

    class DB:
        async def get(self, model, key):
            return row

        async def flush(self):
            pass

        async def commit(self):
            lock.release()

    async def lock_review(db, current):
        if lock.locked():
            request_waiting.set()
        await lock.acquire()

    async def gate(db, current):
        return SimpleNamespace(ready=ready)

    async def dismiss(db, current, submission, user, reason, *, withdraw_request, review_locked):
        nonlocal ready
        assert review_locked and withdraw_request and lock.locked()
        withdrawing.set()
        await finish_withdraw.wait()
        ready = True

    async def submit_verdict(db, current, user, verdict, body, *, review_locked):
        nonlocal ready
        assert review_locked and lock.locked()
        ready = False
        return SimpleNamespace(id=uuid.uuid4(), verdict=verdict)

    async def deliver_gate_change(db, current, was_ready, actor_id):
        nonlocal actionable_ready
        if was_ready != ready:
            transitions.append((was_ready, ready))
            actionable_ready = ready

    monkeypatch.setattr(submissions, "_lock_review", lock_review)
    monkeypatch.setattr(submissions, "gate", gate)
    monkeypatch.setattr(submissions, "dismiss", dismiss)
    monkeypatch.setattr(submissions, "submit_verdict", submit_verdict)
    monkeypatch.setattr(submissions.notifications, "resolve_review_work", AsyncMock())
    monkeypatch.setattr(submissions.notifications, "deliver_event", AsyncMock())
    monkeypatch.setattr(submissions.notifications, "deliver_gate_change", deliver_gate_change)
    monkeypatch.setattr(submissions, "audit_detail", lambda *args, **kwargs: None)
    monkeypatch.setattr(submissions, "notify_update", AsyncMock())

    withdraw = asyncio.create_task(
        submissions.withdraw_request(row.id, Reason(reason="Answered"), object(), review, DB(), reviewer)
    )
    await asyncio.wait_for(withdrawing.wait(), 1)
    request = asyncio.create_task(
        submissions.submit(
            VerdictCreate(verdict="request_changes", body="Another issue"), object(), review, DB(), reviewer
        )
    )
    try:
        await asyncio.wait_for(request_waiting.wait(), 1)
    finally:
        finish_withdraw.set()
    assert (await asyncio.wait_for(withdraw, 1))["withdrawn"]
    assert (await asyncio.wait_for(request, 1))["verdict"] == "request_changes"
    assert transitions == [(False, True), (True, False)]
    assert not actionable_ready
