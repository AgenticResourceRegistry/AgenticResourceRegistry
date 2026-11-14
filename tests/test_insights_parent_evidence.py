# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Parent declarations must survive transport/replay without inventing roots."""

import ast
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from api.routes.ingest import SessionIngestRequest
from services.insights.cohort import select_cohort
from services.insights.scope import SessionKey
from services.session_parent_evidence import load_parent_evidence, parent_decision, record_parent_evidence


@pytest.mark.parametrize("body", [{}, {"parent_session_id": None}, {"parent_session_id": ""}])
def test_legacy_null_default_is_not_an_explicit_root(body):
    """Old clients always serialized null; field presence does not establish ancestry."""
    req = SessionIngestRequest(session_id="s", lines=[], **body)
    assert req.parent_metadata_known is False
    assert not parent_decision(req.parent_session_id or "", [])["parent_metadata_known"]


@pytest.mark.parametrize("parent", [None, "parent"])
def test_explicit_known_parent_contract(parent):
    """Only a strict marker plus an explicit parent value declares root/child status."""
    req = SessionIngestRequest(session_id="s", lines=[], parent_session_id=parent, parent_metadata_known=True)
    assert req.parent_metadata_known is True


@pytest.mark.parametrize(
    "fields",
    [
        {"parent_metadata_known": True},
        {"parent_metadata_known": "true", "parent_session_id": None},
        {"parent_metadata_known": 1, "parent_session_id": None},
        {"parent_metadata_known": True, "parent_session_id": ""},
        {"parent_metadata_known": True, "parent_session_id": "   "},
        {"parent_session_id": "s"},
    ],
)
def test_malformed_parent_claims_are_rejected(fields):
    """Invalid declarations cannot become metadata for an accepted source stream."""
    with pytest.raises(ValidationError):
        SessionIngestRequest(session_id="s", lines=[], **fields)


@pytest.mark.parametrize(
    "parent,claims,known,expected_parent",
    [
        ("", [], False, ""),
        ("", [["top_level", ""]], True, ""),
        ("", [["top_level", ""], ["top_level", ""]], True, ""),
        ("parent", [], True, "parent"),
        ("", [["child", "parent"]], True, "parent"),
        ("parent", [["top_level", ""]], False, "parent"),
        ("", [["top_level", ""], ["child", "parent"]], False, ""),
        ("parent", [["child", "other"]], False, "parent"),
        ("", [["child", ""]], False, ""),
        ("", [["other", ""], ["top_level", ""]], False, ""),
    ],
)
def test_parent_reconciliation_is_monotonic_and_conservative(parent, claims, known, expected_parent):
    """Replays agree, conflicts never silently replace earlier evidence, and unknown stays unknown."""
    result = parent_decision(parent, claims)
    assert result["parent_metadata_known"] is known and result["parent_session_id"] == expected_parent
    assert result["parent_evidence_key"] == parent_decision(parent, list(reversed(claims)))["parent_evidence_key"]


@pytest.mark.asyncio
async def test_record_unknowns_without_writes_and_explicit_declarations_with_scope(monkeypatch):
    """Only accepted known-root/positive-child claims create durable metadata."""
    import services.clickhouse as ch

    query = AsyncMock(return_value=SimpleNamespace(raise_for_status=lambda: None))
    monkeypatch.setattr(ch, "_query", query)
    await record_parent_evidence("s", "p", "u", "pi", None)
    query.assert_not_awaited()
    await record_parent_evidence("s", "p", "u", "pi", None, True)
    root = json.loads(query.await_args.kwargs["data"])
    assert root["relation"] == "top_level" and root["parent_session_id"] == ""
    assert (root["project_id"], root["user_id"], root["harness"], root["session_id"]) == ("p", "u", "pi", "s")
    await record_parent_evidence("s", "p", "u", "pi", "parent")
    assert json.loads(query.await_args.kwargs["data"])["relation"] == "child"


@pytest.mark.asyncio
async def test_bounded_parent_reads_do_not_share_colliding_identities():
    """The complete source identity is bound in each lookup, and unsolicited rows are ignored."""
    keys = [SessionKey("p", str(i), "pi", "same") for i in range(1001)]
    calls = []

    async def query(sql, params):
        calls.append((sql, params))
        requested = zip(
            *(
                ast.literal_eval(params[f"param_parent_{name}"])
                for name in ("projects", "users", "harnesses", "sessions")
            ),
            strict=True,
        )
        data = [
            {"project_id": p, "user_id": u, "harness": h, "session_id": s, "declarations": [["top_level", ""]]}
            for p, u, h, s in requested
        ]
        data.append({"project_id": "other", "user_id": "0", "harness": "pi", "session_id": "same", "declarations": []})
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": data})

    result = await load_parent_evidence(keys, query)
    assert len(calls) == 2 and set(result) == set(keys)
    assert all("project_id, user_id, harness, session_id" in sql and "groupUniqArray(2)" in sql for sql, _ in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_primary_and_fallback_stats_require_parent_evidence(monkeypatch, fallback):
    """Both stats paths overlay declarations, not column membership or SQL null coalescing."""
    from services.insights import session_meta_extractor as extractor

    async def query(sql, params):
        if "FROM session_stats_agg" in sql and fallback:
            raise RuntimeError("Fixture primary failure")
        if "FROM session_parent_evidence" in sql:
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {
                    "data": [
                        {
                            "project_id": "p",
                            "user_id": "u",
                            "harness": "pi",
                            "session_id": "declared",
                            "declarations": [["top_level", ""]],
                        }
                    ]
                },
            )
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "data": [
                    {
                        "project_id": "p",
                        "user_id": "u",
                        "harness": "pi",
                        "session_id": s,
                        "parent_session_id": "",
                        "total_credits": 1,
                        "reported_agent_version": "1",
                    }
                    for s in ["declared", "legacy"]
                ]
            },
        )

    monkeypatch.setattr(extractor, "get_query", lambda: query)
    result = await extractor.fetch_session_stats("a", "start", "end")
    assert result[SessionKey("p", "u", "pi", "declared")]["parent_metadata_known"] is True
    assert result[SessionKey("p", "u", "pi", "legacy")]["parent_metadata_known"] is False


@pytest.mark.asyncio
async def test_missing_parent_projection_never_promotes_a_root(monkeypatch):
    """Missing migration/outage fails closed while an explicit legacy child remains excluded."""
    from services.insights import session_meta_extractor as extractor

    async def query(sql, params):
        if "FROM session_parent_evidence" in sql:
            raise RuntimeError("Fixture metadata table unavailable")
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "data": [
                    {"project_id": "p", "user_id": "u", "harness": "pi", "session_id": s, "parent_session_id": parent}
                    for s, parent in [("root", ""), ("child", "root")]
                ]
            },
        )

    monkeypatch.setattr(extractor, "get_query", lambda: query)
    result = await extractor.fetch_session_stats("a", "start", "end")
    assert result[SessionKey("p", "u", "pi", "root")]["parent_metadata_known"] is False
    assert result[SessionKey("p", "u", "pi", "child")]["parent_metadata_known"] is True


def test_cohort_preserves_declared_roots_without_promoting_unknowns():
    """This is not the unsafe workaround of excluding every parentless record."""
    metas = []
    for sid, claims in [
        ("root", [["top_level", ""]]),
        ("unknown", []),
        ("conflict", [["top_level", ""], ["child", "root"]]),
    ]:
        metas.append(
            {
                "session_key": SessionKey("p", "u", "pi", sid),
                **parent_decision("", claims),
                "instruction_evidence": {"state": "verified", "reason": "fixture"},
            }
        )
    selected, coverage = select_cohort(metas)
    assert [m["session_key"].session_id for m in selected] == ["root"]
    assert coverage["policy_version"] == 2 and coverage["verified_top_level_sessions"] == 1
    assert coverage["decisions"]["parent_evidence_unavailable"] == 1
    assert coverage["decisions"]["conflicting_parent_evidence"] == 1


def test_parent_migration_never_backfills_or_rewrites_history():
    """Only schema creation is allowed; old parentless rows have no fabricated marker."""
    from pathlib import Path

    from services.clickhouse.migrations import _split_sql

    file = Path(__file__).parents[1] / "observal-server/clickhouse/migrations/012_session_parent_evidence.sql"
    statements = _split_sql(file.read_text())
    assert len(statements) == 1 and statements[0].startswith("CREATE TABLE IF NOT EXISTS session_parent_evidence")
    assert "relation, parent_session_id" in statements[0]
    assert "INSERT" not in statements[0] and "ALTER TABLE session_events" not in statements[0]


def test_bound_parent_arrays_escape_quotes_and_backslashes():
    """Scoped identifiers stay parameter data even when they contain SQL-like text."""
    from services.session_parent_evidence import _clickhouse_strings

    values = ["user'quote", "path\\name", "x'); DROP TABLE session_events; --"]
    assert ast.literal_eval(_clickhouse_strings(values)) == values
