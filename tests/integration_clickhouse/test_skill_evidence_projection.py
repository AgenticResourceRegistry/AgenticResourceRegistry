# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Opt-in proof: Pi skill evidence through real ClickHouse, kept apart from MCP calls.

Runs against the same isolated databases as ``test_phase3_activity_api.py``
(see docs/testing/database-integration-tests.md): ClickHouse
``observal_phase22_ci`` on :18123 at migration 008, and PostgreSQL
``observal_phase14_ci`` on :15432 for the projection generation sequence.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import pytest
import pytest_asyncio
import xxhash

import services.clickhouse.client as clickhouse
from database import async_session, engine
from models.skill import SkillListing
from models.user import User
from services.component_activity import project_session_activity, project_session_skill_evidence, queries, skill_queries
from services.layer_components import CURRENT_EXTRACTOR_VERSION
from services.layer_components.extractor import ensure_layer_components
from services.projection_generation import next_projection_generation

_URL = os.getenv("OBSERVAL_CH_PHASE25_URL")
pytestmark = pytest.mark.skipif(not _URL, reason="opt-in isolated skill evidence proof")
_FIXTURE = Path(__file__).parents[1] / "fixtures" / "component_insights" / "pi" / "skill_session_model_read.jsonl"
_SLASH = _FIXTURE.with_name("skill_session_slash_command.jsonl")
_HASH = "v2_" + "c" * 60
_PROBE = "/home/fixture/.pi/agent/skills/observal-probe/SKILL.md"
_SECOND = "/home/fixture/.pi/agent/skills/second-probe/SKILL.md"


@pytest.fixture(scope="module", autouse=True)
def _isolated_databases_only():
    if not _URL:
        pytest.skip("isolated skill proof not configured")
    target = urlparse(_URL)
    pg = urlparse(os.environ.get("DATABASE_URL", ""))
    assert target.hostname == "127.0.0.1" and target.port == 18123 and target.path == "/observal_phase22_ci"
    assert clickhouse.CLICKHOUSE_DB == "observal_phase22_ci"
    assert pg.hostname == "127.0.0.1" and pg.port == 15432 and pg.path == "/observal_phase14_ci"


@pytest.fixture(autouse=True)
def _fresh_http_client(monkeypatch):
    monkeypatch.setattr(clickhouse, "_client", None)


@pytest_asyncio.fixture(autouse=True)
async def _close_pg_pool():
    yield
    await engine.dispose()


def _ts(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


async def _insert(table: str, rows: list[dict]) -> None:
    assert table in {
        "session_events",
        "session_stats_agg",
        "layer_components",
        "layer_component_extractions",
        "layer_snapshots",
    }
    response = await clickhouse._query(
        f"INSERT INTO {table} FORMAT JSONEachRow", data="\n".join(json.dumps(row) for row in rows)
    )
    response.raise_for_status()


def _location(alias: str, harness: str = "pi") -> str:
    """Digest of the active SKILL.md path the verifier fingerprinted (user scope, /home/fixture)."""
    root = "/home/fixture/.claude/skills" if harness == "claude-code" else "/home/fixture/.pi/agent/skills"
    return hashlib.sha256(f"{root}/{alias}/SKILL.md".encode()).hexdigest()


def _session_lines(fixture: Path, *, failed_read: bool = False, foreign: bool = False) -> list[str]:
    """The recorded Pi session, advertising a second installed skill on the same line."""
    lines = fixture.read_text(encoding="utf-8").splitlines()
    system = json.loads(lines[3])
    second = (
        f"\n  <skill>\n    <name>second-probe</name>\n    <description>A second synthetic skill.</description>"
        f"\n    <location>{_SECOND}</location>\n  </skill>"
    )
    sections = system["message"]["sections"]
    sections["skills"] = sections["skills"].replace("</available_skills>", second + "\n</available_skills>")
    lines[3] = json.dumps(system)
    if failed_read:
        result = json.loads(lines[6])
        result["message"]["isError"] = True
        lines[6] = json.dumps(result)
    if foreign:
        # Same layout, scope and alias, but not this installation's skill directory.
        lines = [line.replace("/home/fixture/.pi/", "/tmp/another-user/.pi/") for line in lines]
    return lines


async def _seed(
    project: str,
    user: str,
    session: str,
    skills: dict[str, str],
    *,
    failed_read: bool = False,
    foreign: bool = False,
    fixture: Path = _FIXTURE,
    index: bool = True,
    harness: str = "pi",
    lines: list[str] | None = None,
) -> None:
    now = datetime.now(UTC) - timedelta(minutes=10)
    await _insert(
        "session_events",
        [
            {
                "project_id": project,
                "user_id": user,
                "harness": harness,
                "session_id": session,
                "line_offset": offset,
                "line_hash": xxhash.xxh128(raw.encode()).hexdigest(),
                "source_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                "is_source_record": 1,
                "rendered": 1,
                "event_type": "system",
                "layer_hash": _HASH,
                "raw_line": raw,
                "content_length": len(raw),
                "timestamp": _ts(now + timedelta(seconds=offset)),
                "ingested_at": _ts(now + timedelta(days=1)),
            }
            for offset, raw in enumerate(
                lines if lines is not None else _session_lines(fixture, failed_read=failed_read, foreign=foreign)
            )
        ],
    )
    await _insert(
        "session_stats_agg",
        [
            {
                "project_id": project,
                "user_id": user,
                "harness": harness,
                "session_id": session,
                "layer_hash": _HASH,
                "first_event_time": _ts(now),
                "last_event_time": _ts(now + timedelta(seconds=8)),
            }
        ],
    )
    if not index:
        return  # the caller uploads a real snapshot and runs the real extractor
    await _insert(
        "layer_snapshots",
        [
            {
                "project_id": project,
                "user_id": user,
                "hash": _HASH,
                "harness": harness,
                "content": json.dumps({"pinned_versions": {"schema_version": 2, "agents": [], "standalone": []}}),
                "uploaded_at": _ts(now),
                "file_count": 0,
                "total_size": 0,
            }
        ],
    )
    generation = await next_projection_generation()
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
                "occurrence_key": f"skill-{alias}",
                "component_type": "skill",
                "source": "agent",
                "harness": harness,
                "scope": "user",
                "parent_agent_id": "",
                "parent_agent_version": "",
                "raw_listing_id": component_id,
                "raw_name": alias,
                "raw_version": "1.0.0",
                "qualified_name": f"team/{alias}",
                "local_name": alias,
                "component_id": component_id,
                "component_version_id": "",
                "identity_status": "resolved",
                "verification_status": "verified",
                "location_sha256": _location(alias, harness),
            }
            for alias, component_id in skills.items()
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
                "occurrence_count": len(skills),
            }
        ],
    )


def _period() -> tuple[datetime, datetime]:
    end = datetime.now(UTC) + timedelta(minutes=1)
    return end - timedelta(days=1), end


@pytest.mark.asyncio
async def test_two_advertised_skills_both_survive_and_stay_out_of_mcp_totals():
    project, user, session = "skill-" + uuid.uuid4().hex, "skill-owner", "two-skills"
    probe, second = str(uuid.uuid4()), str(uuid.uuid4())
    await _seed(project, user, session, {"observal-probe": probe, "second-probe": second})

    published = await project_session_skill_evidence(project, user, "pi", session)
    assert published["status"] == "complete"
    # Two availability facts on one line plus one load: all attributed, none replaced.
    assert (published["candidate_count"], published["attributed_count"]) == (3, 3)
    rows = (
        await clickhouse._query(
            "SELECT source_line_offset, source_block_key, component_id, evidence_kind FROM component_activity FINAL "
            "WHERE project_id = {p:String} AND projection_generation = {g:UInt64} ORDER BY source_block_key FORMAT JSON",
            {"param_p": project, "param_g": published["generation"]},
        )
    ).json()["data"]
    assert [(r["source_line_offset"], r["source_block_key"], r["evidence_kind"]) for r in rows] == [
        (3, "skill-available:user:observal-probe", "skill_available"),
        (3, "skill-available:user:second-probe", "skill_available"),
        (5, "skill-load:0", "skill_load"),
    ]
    period = _period()
    probe_summary = await skill_queries.skill_activity_summary(project, probe, None, period)
    second_summary = await skill_queries.skill_activity_summary(project, second, None, period)
    assert (
        probe_summary["available_sessions"],
        probe_summary["loaded_sessions"],
        probe_summary["confirmed_loads"],
    ) == (1, 1, 1)
    assert (second_summary["available_sessions"], second_summary["loaded_sessions"]) == (1, 0)
    assert probe_summary["coverage"].attribution_state == "observed"
    assert second_summary["coverage"].attribution_state == "no_observed_skill_use", "available is not use"
    # Re-projecting the same source is idempotent.
    assert (await project_session_skill_evidence(project, user, "pi", session))["status"] == "already_complete"

    # MCP is an independent projection: it completes on its own, and even the MCP
    # read path asked about this very component counts none of its skill rows.
    assert (await project_session_activity(project, user, "pi", session))["status"] == "complete"
    mcp = await queries.activity_summary(project, "skill", probe, None, period)
    assert mcp["observed_calls"] == 0 and mcp["coverage"].calls.candidate_calls == 0


@pytest.mark.asyncio
async def test_a_failed_read_is_an_attempt_not_a_confirmed_load():
    project, user, session = "skillf-" + uuid.uuid4().hex, "skill-owner", "failed-read"
    probe = str(uuid.uuid4())
    await _seed(project, user, session, {"observal-probe": probe}, failed_read=True)
    assert (await project_session_skill_evidence(project, user, "pi", session))["status"] == "complete"
    summary = await skill_queries.skill_activity_summary(project, probe, None, _period())
    assert (summary["loaded_sessions"], summary["confirmed_loads"], summary["load_attempts"]) == (0, 0, 1)
    assert summary["coverage"].attribution_state == "no_observed_skill_use"
    sessions, _ = await skill_queries.skill_activity_sessions(
        project, probe, None, _period(), limit=10, cursor=None, cursor_scope=(project, "u", "skill", probe, "", "1")
    )
    assert [(s["available"], s["confirmed_loads"], s["load_attempts"]) for s in sessions] == [(True, 0, 1)]


@pytest.mark.asyncio
async def test_an_unverified_or_unsupported_skill_is_never_attributed():
    project, user, session = "skillu-" + uuid.uuid4().hex, "skill-owner", "unverified"
    await _seed(project, user, session, {})  # no verified installed skill in the layer
    published = await project_session_skill_evidence(project, user, "pi", session)
    assert published["status"] == "complete" and published["attributed_count"] == 0
    assert published["unmatched_count"] == published["candidate_count"] > 0
    from observal_shared.harness_registry import HARNESS_REGISTRY

    unsupported = next(h for h, entry in sorted(HARNESS_REGISTRY.items()) if not entry.get("skill_evidence_extractor"))
    assert (await project_session_skill_evidence(project, user, unsupported, session))["status"] == "unsupported"


@pytest.mark.asyncio
async def test_a_same_named_skill_at_a_foreign_path_is_never_attributed():
    project, user, session = "skillx-" + uuid.uuid4().hex, "skill-owner", "foreign"
    probe = str(uuid.uuid4())
    await _seed(project, user, session, {"observal-probe": probe}, foreign=True)
    published = await project_session_skill_evidence(project, user, "pi", session)
    assert published["status"] == "complete" and published["candidate_count"] > 0
    assert (published["attributed_count"], published["unmatched_count"]) == (0, published["candidate_count"])
    summary = await skill_queries.skill_activity_summary(project, probe, None, _period())
    assert (summary["available_sessions"], summary["loaded_sessions"], summary["confirmed_loads"]) == (0, 0, 0)


@pytest.mark.asyncio
async def test_slash_command_text_is_not_an_invocation_only_the_read_counts():
    project, user, session = "skills-" + uuid.uuid4().hex, "skill-owner", "slash"
    probe = str(uuid.uuid4())
    await _seed(project, user, session, {"observal-probe": probe}, fixture=_SLASH)
    assert (await project_session_skill_evidence(project, user, "pi", session))["status"] == "complete"
    summary = await skill_queries.skill_activity_summary(project, probe, None, _period())
    # Pi records no invocation origin: unknown, not a measured zero.
    assert (summary["invoked_sessions"], summary["invocations"]) == (None, None)
    assert any(item.startswith("invocations_not_recorded") for item in summary["coverage"].limitations)
    sessions, _ = await skill_queries.skill_activity_sessions(
        project, probe, None, _period(), limit=10, cursor=None, cursor_scope=(project, "u", "skill", probe, "", "1")
    )
    assert [(s["confirmed_loads"], s["invocations"]) for s in sessions] == [(1, None)]
    assert (summary["loaded_sessions"], summary["confirmed_loads"]) == (1, 1)


@pytest.mark.asyncio
async def test_a_real_snapshot_carries_the_verified_location_into_attribution():
    """Snapshot drift → real extractor → layer_components.location_sha256 → skill attribution."""
    project, user, session = "skille-" + uuid.uuid4().hex, "skill-owner", "end-to-end"
    listing_id = uuid.uuid4()
    async with async_session() as db:
        owner = User(
            email=f"skill-{uuid.uuid4().hex}@example.invalid",
            username=f"sk{uuid.uuid4().hex[:12]}",
            name="Isolated fixture owner",
        )
        db.add(owner)
        await db.flush()
        db.add(
            SkillListing(
                id=listing_id,
                name="observal-probe",
                namespace="team",
                slug=f"p{uuid.uuid4().hex[:12]}",
                owner=owner.username,
                submitted_by=owner.id,
            )
        )
        await db.commit()
    await _seed(project, user, session, {}, index=False)
    pin = {
        "type": "skill",
        "id": str(listing_id),
        "name": "observal-probe",
        "version": "1.0.0",
        "scope": "user",
        "local_name": "observal-probe",
        "harness": "pi",
    }
    verification = {
        "harness": "pi",
        "component_id": str(listing_id),
        "alias": "observal-probe",
        "scope": "user",
        "parent_agent_id": "",
        "status": "verified",
        "location_sha256": _location("observal-probe"),
    }
    content = {
        "pinned_versions": {"schema_version": 2, "agents": [], "standalone": [pin]},
        "drift": {"is_canonical": True, "skill_verifications": [verification]},
    }
    await _insert(
        "layer_snapshots",
        [
            {
                "project_id": project,
                "user_id": user,
                "hash": _HASH,
                "harness": "pi",
                "content": json.dumps(content),
                "uploaded_at": _ts(datetime.now(UTC)),
                "file_count": 0,
                "total_size": 0,
            }
        ],
    )
    indexed = await ensure_layer_components(project, user, _HASH)
    assert indexed["status"] == "complete" and indexed["occurrences"] == 1
    (row,) = (
        await clickhouse._query(
            "SELECT verification_status, location_sha256 FROM layer_components FINAL "
            "WHERE project_id = {p:String} FORMAT JSON",
            {"param_p": project},
        )
    ).json()["data"]
    assert row == {"verification_status": "verified", "location_sha256": _location("observal-probe")}
    published = await project_session_skill_evidence(project, user, "pi", session)
    assert published["status"] == "complete"
    summary = await skill_queries.skill_activity_summary(project, str(listing_id), None, _period())
    assert (summary["available_sessions"], summary["confirmed_loads"]) == (1, 1)


_CLAUDE = Path(__file__).parents[1] / "fixtures" / "component_insights" / "claude_code"


def _claude(name: str) -> list[str]:
    return (_CLAUDE / name).read_text(encoding="utf-8").splitlines()


@pytest.mark.asyncio
async def test_claude_code_loads_and_invocations_are_attributed_and_availability_is_unknown():
    project, user = "skillc-" + uuid.uuid4().hex, "skill-owner"
    probe = str(uuid.uuid4())
    for session, name in (
        ("model-read", "skill_session_model_read.jsonl"),
        ("slash", "skill_session_slash_command.jsonl"),
        ("literal", "skill_session_literal_text.jsonl"),
        ("unknown", "skill_session_unknown_skill.jsonl"),
    ):
        await _seed(project, user, session, {"observal-probe": probe}, harness="claude-code", lines=_claude(name))
        assert (await project_session_skill_evidence(project, user, "claude-code", session))["status"] == "complete"
    summary = await skill_queries.skill_activity_summary(project, probe, None, _period())
    assert summary["present_sessions"] == 4
    assert (summary["loaded_sessions"], summary["confirmed_loads"]) == (1, 1)
    assert (summary["invoked_sessions"], summary["invocations"]) == (1, 1), "only the real /observal-probe"
    assert summary["available_sessions"] is None, "the skill listing names no file"
    coverage = summary["coverage"]
    assert coverage.observed_sessions == 2 and coverage.usage_rate_denominator_sessions == 4
    assert any(item.startswith("availability_not_recorded") for item in coverage.limitations)
    assert not any(item.startswith("invocations_not_recorded") for item in coverage.limitations)
    sessions, _ = await skill_queries.skill_activity_sessions(
        project, probe, None, _period(), limit=10, cursor=None, cursor_scope=(project, "u", "skill", probe, "", "1")
    )
    assert {(s["session_id"], s["available"], s["confirmed_loads"], s["invocations"]) for s in sessions} == {
        ("literal", None, 0, 0),
        ("model-read", None, 1, 0),
        ("slash", None, 0, 1),
        ("unknown", None, 0, 0),
    }


@pytest.mark.asyncio
async def test_a_mixed_pi_and_claude_cohort_reports_partial_kinds():
    project = "skillm-" + uuid.uuid4().hex
    probe = str(uuid.uuid4())
    # One layer per user: each extraction replaces that user's mapping for the hash.
    await _seed(project, "pi-user", "pi-session", {"observal-probe": probe})
    claude = _claude("skill_session_slash_command.jsonl")
    await _seed(project, "cc-user", "cc-session", {"observal-probe": probe}, harness="claude-code", lines=claude)
    for user, harness, session in (("pi-user", "pi", "pi-session"), ("cc-user", "claude-code", "cc-session")):
        assert (await project_session_skill_evidence(project, user, harness, session))["status"] == "complete"
    summary = await skill_queries.skill_activity_summary(project, probe, None, _period())
    assert (summary["available_sessions"], summary["invocations"], summary["confirmed_loads"]) == (1, 1, 1)
    reasons = summary["coverage"].reasons
    assert {"available_not_recorded_on_some_harnesses", "invoked_not_recorded_on_some_harnesses"} <= set(reasons)
