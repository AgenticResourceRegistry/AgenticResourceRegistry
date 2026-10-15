# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Seed the Cost Intelligence demo stack with synthetic data and stage a realistic month.

DESTRUCTIVE: wipes users, agents, opportunities and all session telemetry in the
target databases. It refuses to run unless DATABASE_URL and CLICKHOUSE_URL point
at the demo stack ports from compose.yml (15432 / 18123). Never point it at a
real deployment.

What it creates (all synthetic):
- an admin (admin@verify.local / costdemo-2026) and 5 developers
- 4 agents: ultra-pi (heavy, Opus + Sonnet on Bedrock EU via Pi), pr-review,
  linear-worklog, doc-writer (poor caching, huge context, on Claude Code)
- ~6 months of sessions; Claude Code sessions written the way real transcripts
  are (several lines per response repeating usage), Kiro credits, a spend spike
- billing: Claude Code 3 seats at $100, Kiro 2 seats at $20, Kiro credits $0.02
- the pipeline: detection, the ultra-pi routing opportunity approved, ultra-pi
  1.2.0 "shipped" with 14 cheaper sessions so savings are actioned and measured

Run from observal-server/ with the demo env sourced:

    source ../scripts/cost-demo/env.sh
    PYTHONPATH=. .venv/bin/python ../scripts/cost-demo/seed.py
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, text

random.seed(7)
NOW = datetime.now(UTC)
CH = "http://127.0.0.1:18123/"
CH_AUTH = ("default", "clickhouse")
PASSWORD = "costdemo-2026"

ADMIN_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
USERS = [uuid.UUID(f"aaaaaaaa-0000-0000-0000-00000000000{i}") for i in range(2, 7)]
ULTRA = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000001")
PRREV = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
WORKLOG = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000003")
DOCS = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000004")


def _guard() -> None:
    db, ch = os.environ.get("DATABASE_URL", ""), os.environ.get("CLICKHOUSE_URL", "")
    if ":15432/" not in db or ":18123/" not in ch:
        sys.exit("Refusing to run: DATABASE_URL / CLICKHOUSE_URL are not the demo stack (ports 15432 / 18123).")


def event(
    sid,
    user,
    agent,
    version,
    harness,
    offset,
    ts,
    etype,
    *,
    model="",
    tokens=(0, 0, 0, 0),
    credits=0.0,
    raw=None,
    tool=None,
):
    i, o, cr, cw = tokens
    return {
        "session_id": sid,
        "project_id": "default",
        "user_id": str(user),
        "agent_id": str(agent) if agent else None,
        "agent_version": version,
        "harness": harness,
        "line_offset": offset,
        "event_type": etype,
        "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S.000"),
        "content_preview": "",
        "content_length": 0,
        "raw_line": json.dumps(raw or {"type": etype}),
        "rendered": 1,
        "is_source_record": 1,
        "input_tokens": i,
        "output_tokens": o,
        "cache_read_tokens": cr,
        "cache_write_tokens": cw,
        "model": model,
        "credits": credits,
        "tool_name": tool,
    }


def session_events(
    sid, user, agent, version, harness, start, model, calls, *, context, out_per_call, hit=0.97, pr=False, errors=0
):
    rows, offset, ts = [], 0, start
    rows.append(event(sid, user, agent, version, harness, offset, ts, "user_prompt"))
    offset += 1
    carried = context
    for c in range(calls):
        ts += timedelta(seconds=random.randint(5, 40))
        prompt = carried + random.randint(500, 3000)
        cached = int(prompt * hit) if c > 0 else 0
        write = int(prompt * 0.03) if c > 0 else prompt
        fresh = max(prompt - cached - write, 1)
        out_tokens = int(out_per_call * random.uniform(0.6, 1.4))
        if harness == "claude-code":
            # Real Claude Code shape: one line per content block, each repeating
            # the response's usage; earlier blocks carry a partial output count.
            mid, rid = f"msg_{uuid.uuid4().hex[:12]}", f"req_{uuid.uuid4().hex[:12]}"
            blocks = random.choice([1, 2, 3])
            for b in range(blocks):
                o = out_tokens if b == blocks - 1 else random.randint(1, 20)
                raw = {
                    "type": "assistant",
                    "requestId": rid,
                    "message": {
                        "id": mid,
                        "model": model,
                        "usage": {
                            "input_tokens": fresh,
                            "output_tokens": o,
                            "cache_read_input_tokens": cached,
                            "cache_creation_input_tokens": write,
                            "cache_creation": {"ephemeral_1h_input_tokens": write, "ephemeral_5m_input_tokens": 0},
                        },
                    },
                }
                rows.append(
                    event(
                        sid,
                        user,
                        agent,
                        version,
                        harness,
                        offset,
                        ts,
                        "assistant_text",
                        model=model,
                        tokens=(fresh, o, cached, write),
                        raw=raw,
                    )
                )
                offset += 1
        else:
            rows.append(
                event(
                    sid,
                    user,
                    agent,
                    version,
                    harness,
                    offset,
                    ts,
                    "assistant_text",
                    model=model,
                    tokens=(fresh, out_tokens, cached, write),
                )
            )
            offset += 1
        carried += random.randint(800, 4000)
        if pr and c == calls - 2:
            rows.append(
                event(
                    sid,
                    user,
                    agent,
                    version,
                    harness,
                    offset,
                    ts,
                    "tool_call",
                    tool="bash",
                    raw={"type": "tool_call", "command": "gh pr create --fill"},
                )
            )
            offset += 1
    for _ in range(errors):
        rows.append(
            event(
                sid,
                user,
                agent,
                version,
                harness,
                offset,
                ts,
                "tool_result",
                raw={"type": "tool_result", "is_error": True},
            )
        )
        offset += 1
    return rows


async def _insert_events(rows: list[dict]) -> None:
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(
            CH,
            params={"query": "INSERT INTO observal.session_events FORMAT JSONEachRow"},
            content="\n".join(json.dumps(r) for r in rows),
            auth=CH_AUTH,
        )
        r.raise_for_status()


async def _set(db, key: str, value: str) -> None:
    from models.enterprise_config import EnterpriseConfig

    row = (await db.execute(select(EnterpriseConfig).where(EnterpriseConfig.key == key))).scalar_one_or_none()
    if row is None:
        db.add(EnterpriseConfig(key=key, value=value))
    else:
        row.value = value


async def main() -> None:
    _guard()
    import services.dynamic_settings as ds
    from database import async_session
    from jobs.cost import backfill_usage_dedup
    from models.agent import Agent, AgentStatus, AgentVersion
    from models.cost_opportunity import CostOpportunity
    from models.insight_session_facets import InsightSessionFacets
    from models.user import User, UserRole
    from services.cost import service

    async with httpx.AsyncClient(timeout=60) as client:
        for table in ("session_events", "session_stats_agg", "session_capabilities"):
            (
                await client.post(CH, params={"query": f"TRUNCATE TABLE IF EXISTS observal.{table}"}, auth=CH_AUTH)
            ).raise_for_status()

    async with async_session() as db:
        await db.execute(
            text("TRUNCATE cost_opportunities, insight_session_facets, agent_versions, agents, users CASCADE")
        )
        await db.execute(text("DELETE FROM enterprise_config WHERE key LIKE 'cost.%'"))
        admin = User(
            id=ADMIN_ID, email="admin@verify.local", username="admin", name="Demo Admin", role=UserRole.super_admin
        )
        admin.set_password(PASSWORD)
        db.add(admin)
        for n, u in enumerate(USERS):
            db.add(
                User(id=u, email=f"dev{n}@verify.local", username=f"dev{n}", name=f"Developer {n}", role=UserRole.user)
            )
        await db.flush()
        for aid, name, versions in [
            (ULTRA, "ultra-pi", [("1.0.0", NOW - timedelta(days=170)), ("1.1.0", NOW - timedelta(days=12))]),
            (PRREV, "pr-review", [("1.0.0", NOW - timedelta(days=120))]),
            (WORKLOG, "linear-worklog", [("1.0.0", NOW - timedelta(days=90))]),
            (DOCS, "doc-writer", [("1.0.0", NOW - timedelta(days=60))]),
        ]:
            db.add(Agent(id=aid, name=name, namespace="demo", slug=name, owner="demo", created_by=ADMIN_ID))
            await db.flush()
            for v, released in versions:
                db.add(
                    AgentVersion(
                        agent_id=aid,
                        version=v,
                        model_name="claude-sonnet-4-6",
                        status=AgentStatus.approved,
                        released_by=ADMIN_ID,
                        released_at=released,
                    )
                )
        await db.commit()

    rows, facets = [], []
    for day in range(175, -1, -1):
        date = NOW - timedelta(days=day)
        heavy = 1.0 if day > 150 else 0.25
        for _ in range(random.randint(0, 3 if day > 150 else 2)):
            sid = f"ultra-{day}-{uuid.uuid4().hex[:6]}"
            version = "1.1.0" if day <= 12 else "1.0.0"
            on_opus = random.random() < (0.15 if version == "1.1.0" else 0.6)
            rows += session_events(
                sid,
                random.choice(USERS),
                ULTRA,
                version,
                "pi",
                date.replace(hour=10),
                "eu.anthropic.claude-opus-4-6-v1" if on_opus else "eu.anthropic.claude-sonnet-4-6",
                int(random.randint(20, 60) * (1 + heavy)),
                context=35_000,
                out_per_call=900,
                pr=random.random() < 0.3,
            )
            if random.random() < 0.5:
                facets.append(
                    (
                        ULTRA,
                        sid,
                        random.choice(["fully_achieved", "mostly_achieved", "mostly_achieved", "partially_achieved"]),
                    )
                )
        if random.random() < 0.5:
            sid = f"pr-{day}-{uuid.uuid4().hex[:6]}"
            rows += session_events(
                sid,
                random.choice(USERS),
                PRREV,
                "1.0.0",
                "claude-code",
                date.replace(hour=14),
                "claude-sonnet-5",
                random.randint(8, 20),
                context=22_000,
                out_per_call=700,
                pr=True,
            )
            facets.append((PRREV, sid, random.choice(["fully_achieved", "mostly_achieved", "not_achieved"])))
        if random.random() < 0.35:
            rows += session_events(
                f"wl-{day}-{uuid.uuid4().hex[:6]}",
                random.choice(USERS),
                WORKLOG,
                "1.0.0",
                "claude-code",
                date.replace(hour=16),
                "claude-sonnet-4-6",
                random.randint(5, 12),
                context=20_000,
                out_per_call=400,
            )
        if day <= 60 and random.random() < 0.4:
            rows += session_events(
                f"doc-{day}-{uuid.uuid4().hex[:6]}",
                random.choice(USERS),
                DOCS,
                "1.0.0",
                "claude-code",
                date.replace(hour=11),
                "claude-sonnet-4-6",
                random.randint(10, 25),
                context=95_000,
                out_per_call=1200,
                hit=0.45,
                errors=random.randint(0, 2),
            )
        if random.random() < 0.6:
            rows += session_events(
                f"na-{day}-{uuid.uuid4().hex[:6]}",
                random.choice(USERS),
                None,
                None,
                "claude-code",
                date.replace(hour=9),
                random.choice(["claude-opus-4-7", "claude-sonnet-5", "gemini-3.1-pro-preview"]),
                random.randint(5, 30),
                context=18_000,
                out_per_call=800,
            )
        if random.random() < 0.2:
            rows.append(
                event(
                    f"kiro-{day}-{uuid.uuid4().hex[:6]}",
                    random.choice(USERS),
                    None,
                    None,
                    "kiro",
                    0xFFFFFFF,
                    date.replace(hour=12),
                    "kiro_credits",
                    model="Kiro Auto",
                    credits=round(random.uniform(0.5, 3.0), 3),
                )
            )
    spike_day = max(NOW - timedelta(days=2), NOW.replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    for n in range(6):
        rows += session_events(
            f"spike-{n}-{uuid.uuid4().hex[:6]}",
            USERS[0],
            ULTRA,
            "1.1.0",
            "pi",
            spike_day.replace(hour=13),
            "eu.anthropic.claude-opus-4-6-v1",
            90,
            context=35_000,
            out_per_call=1500,
        )
    await _insert_events(rows)
    async with async_session() as db:
        for agent_id, sid, outcome in facets:
            db.add(InsightSessionFacets(agent_id=agent_id, session_id=sid, facets={"outcome": outcome}))
        await db.commit()
    print(f"seeded {len(rows)} events, {len(facets)} judged sessions")

    print("backfill:", await backfill_usage_dedup({}))

    async with async_session() as db:
        await _set(
            db,
            "cost.billing",
            json.dumps(
                {
                    "claude-code": {"mode": "subscription", "seats": 3, "seat_usd_month": 100},
                    "kiro": {
                        "mode": "subscription",
                        "seats": 2,
                        "seat_usd_month": 20,
                        "plan_credits": 1000,
                        "overage_usd_per_credit": 0.04,
                    },
                }
            ),
        )
        await _set(db, "cost.credit_usd", json.dumps({"kiro": 0.02}))
        await db.commit()
    for key in ("cost.billing", "cost.credit_usd"):
        await ds.invalidate(key)

    async with async_session() as db:
        print("refresh:", await service.refresh(db))
        routing = (
            (await db.execute(select(CostOpportunity).where(CostOpportunity.detector == "model_routing")))
            .scalars()
            .first()
        )
        if routing is None:
            print("no routing opportunity detected; pipeline staging skipped")
            return
        # Approve two hours ago so the simulated release and its sessions are in the past.
        routing = await service.approve(db, routing, ADMIN_ID, datetime.now(UTC) - timedelta(hours=2))
        released = routing.approved_at + timedelta(seconds=1)
        db.add(
            AgentVersion(
                agent_id=ULTRA,
                version="1.2.0",
                model_name="claude-sonnet-4-6",
                status=AgentStatus.approved,
                released_by=ADMIN_ID,
                released_at=released,
            )
        )
        await db.commit()

    shipped, sids = [], []
    for n in range(14):
        sid = f"v12-{n}-{uuid.uuid4().hex[:6]}"
        sids.append(sid)
        shipped += session_events(
            sid,
            random.choice(USERS),
            ULTRA,
            "1.2.0",
            "pi",
            released + timedelta(minutes=5 + 5 * n),
            "eu.anthropic.claude-sonnet-4-6",
            35,
            context=35_000,
            out_per_call=900,
        )
    await _insert_events(shipped)
    async with async_session() as db:
        for sid in sids[:10]:
            db.add(
                InsightSessionFacets(
                    agent_id=ULTRA,
                    session_id=sid,
                    facets={"outcome": random.choice(["fully_achieved", "mostly_achieved"])},
                )
            )
        await db.commit()
        print("refresh after release:", await service.refresh(db))
    print(f"done. Log in at http://localhost:13000/login as admin@verify.local / {PASSWORD}")


if __name__ == "__main__":
    asyncio.run(main())
