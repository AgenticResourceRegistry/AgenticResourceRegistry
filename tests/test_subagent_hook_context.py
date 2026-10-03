# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""A subagent session's agent, named by its parent session's own record of the spawn."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from services.component_activity import evidence_projector, hook_projector, projector
from services.component_activity.hook_matcher import match_hook_evidence
from services.session_parsers.claude_code_hook_evidence import ClaudeCodeHookEvidenceExtractor as Extractor
from services.session_parsers.hook_evidence import (
    HookEvidenceExtraction,
    HookSession,
    extract_hook_evidence,
    hook_binding_sha256,
)

FIXTURES = Path(__file__).parent / "fixtures" / "component_insights" / "claude_code"
PARENT = "00000000-0000-4000-8000-000000000107"
SUBAGENT = "a3028a7249695872d"
SPAWN = "toolu_fixture_0125"
LAYER = "v2_" + "e" * 60
GATE = (
    "PYTHONPATH=/home/fixture/observal-src /home/fixture/.local/share/observal/bin/python3 "
    "-P -m observal_cli.hook_gate --agent probe-agent --command "
)


def _lines(name: str) -> list[str]:
    return (FIXTURES / name).read_text(encoding="utf-8").splitlines()


def _rows(lines: list[str]) -> list[dict]:
    return [
        {
            "line_offset": offset,
            "line_hash": f"line-{offset}",
            "source_sha256": hashlib.sha256(line.encode()).hexdigest(),
            "layer_hash": LAYER,
            "timestamp": "2026-10-01 16:35:00.000",
            "raw_line": line,
            "raw_line_truncated": 0,
            "is_source_record": 1,
        }
        for offset, line in enumerate(lines)
    ]


def _replace(lines: list[str], offset: int, change) -> list[str]:
    record = json.loads(lines[offset])
    change(record)
    return [*lines[:offset], json.dumps(record), *lines[offset + 1 :]]


def _mentioning(lines: list[str], needle: str) -> list[dict]:
    return [row for row in _rows(lines) if needle in row["raw_line"]]


def _resolve(parent_lines: list[str], subagent_id: str = SUBAGENT, parent: str = PARENT) -> str:
    results = Extractor.subagent_results(_mentioning(parent_lines, subagent_id), parent, subagent_id)
    if not results:
        return ""
    calls = Extractor.agent_tool_calls(
        [row for tool_id in results for row in _mentioning(parent_lines, tool_id)], parent, frozenset(results)
    )
    return Extractor.resolve_agent(results, calls) if calls is not None else ""


# -- the subagent's own link ---------------------------------------------------------------------------


def test_a_subagent_transcript_links_to_its_parent_session_and_a_main_transcript_does_not():
    subagent = extract_hook_evidence("claude-code", _rows(_lines("gate_session_headless_subagent.jsonl"))).session
    assert (subagent.subagent, subagent.parent_session_id, subagent.subagent_id) == (True, PARENT, SUBAGENT)
    assert subagent.subagent_agent == "", "the extractor never names the agent from the subagent's own transcript"
    main = extract_hook_evidence("claude-code", _rows(_lines("gate_session_headless_subagent_main.jsonl"))).session
    assert (main.subagent, main.parent_session_id, main.subagent_id) == (False, "", "")


@pytest.mark.parametrize(
    "change",
    [
        lambda record: record.update(agentId="a0000000000000000"),  # two subagent ids
        lambda record: record.update(sessionId="00000000-0000-4000-8000-000000000999"),  # two parents
        lambda record: record.pop("sessionId"),  # a sidechain record without the pair
        lambda record: record.update(agentId='a3028a"7249695872d'),  # not a safe link key
    ],
)
def test_an_inconsistent_subagent_transcript_has_no_link(change):
    lines = _replace(_lines("gate_session_headless_subagent.jsonl"), 9, change)
    session = extract_hook_evidence("claude-code", _rows(lines)).session
    assert session.subagent is True
    assert (session.parent_session_id, session.subagent_id) == ("", "")


# -- the parent's record of the spawn ------------------------------------------------------------------


def test_the_recorded_parent_names_the_subagents_agent():
    main = _lines("gate_session_headless_subagent_main.jsonl")
    assert Extractor.subagent_results(_mentioning(main, SUBAGENT), PARENT, SUBAGENT) == {SPAWN: "probe-agent"}
    assert Extractor.agent_tool_calls(_mentioning(main, SPAWN), PARENT, frozenset({SPAWN})) == {SPAWN: "probe-agent"}
    assert _resolve(main) == "probe-agent"
    assert _resolve(main, parent="00000000-0000-4000-8000-000000000999") == "", "another session's records never count"
    assert _resolve(main, subagent_id="a0000000000000000") == ""


def test_a_spawn_result_counts_only_when_linked_to_a_named_agent_call_asking_for_that_type():
    main = _lines("gate_session_headless_subagent_main.jsonl")

    def rename_tool(record):
        record["message"]["content"][0]["name"] = "mcp__evil__spawn"

    def ask_other(record):
        record["message"]["content"][0]["input"]["subagent_type"] = "other-agent"

    def no_type(record):
        record["message"]["content"][0]["input"].pop("subagent_type")

    # An MCP tool's structured result cannot pose as a spawn result.
    assert _resolve(_replace(main, 18, rename_tool)) == ""
    assert _resolve(_replace(main, 18, ask_other)) == "", "result and call disagree"
    assert _resolve(_replace(main, 18, no_type)) == ""
    assert _resolve(_replace(main, 19, lambda r: r["toolUseResult"].update(agentType="other-agent"))) == ""
    # The parent naming another agent resolves to that agent, which is how a hook becomes inactive.
    other = _replace(_replace(main, 18, ask_other), 19, lambda r: r["toolUseResult"].update(agentType="other-agent"))
    assert _resolve(other) == "other-agent"


def test_unreadable_or_disagreeing_parent_records_leave_the_agent_unknown():
    main = _lines("gate_session_headless_subagent_main.jsonl")
    truncated = [row | {"raw_line_truncated": 1} for row in _mentioning(main, SUBAGENT)]
    assert Extractor.subagent_results(truncated, PARENT, SUBAGENT) is None
    garbled = [{"raw_line": '{"agentId": "' + SUBAGENT + '"', "raw_line_truncated": 0}]
    assert Extractor.subagent_results(_mentioning(main, SUBAGENT) + garbled, PARENT, SUBAGENT) is None
    # Two spawn records for the same call naming different agents.
    second = _replace(main, 19, lambda r: r["toolUseResult"].update(agentType="other-agent"))[19]
    assert _resolve([*main, second]) == ""
    # The call id also used by a sidechain record or by a second, different call.
    sidechain = _replace(main, 18, lambda r: r.update(isSidechain=True))[18]
    assert _resolve([*main, sidechain]) == ""
    renamed = _replace(main, 18, lambda r: r["message"]["content"][0].update(name="Bash"))[18]
    assert _resolve([*main, renamed]) == ""


def test_two_spawns_of_one_subagent_must_name_the_same_agent():
    main = _lines("gate_session_headless_subagent_main.jsonl")

    def respawn(offset: int, agent: str):
        def change(record):
            if offset == 18:
                record["message"]["content"][0]["id"] = "toolu_fixture_0999"
                record["message"]["content"][0]["input"]["subagent_type"] = agent
            else:
                record["message"]["content"][0]["tool_use_id"] = "toolu_fixture_0999"
                record["toolUseResult"]["agentType"] = agent

        return _replace(main, offset, change)[offset]

    assert _resolve([*main, respawn(18, "probe-agent"), respawn(19, "probe-agent")]) == "probe-agent"
    assert _resolve([*main, respawn(18, "other-agent"), respawn(19, "other-agent")]) == ""


# -- eligibility ---------------------------------------------------------------------------------------


def _candidate(binding: str, component_id: str) -> dict:
    return {
        "component_id": component_id,
        "component_version_id": "",
        "identity_status": "resolved",
        "verification_status": "verified",
        "location_sha256": binding,
        "binding_agent": "probe-agent",
        "binding_placement": "gated_settings",
    }


FAIL_HOOK = _candidate(hook_binding_sha256("PostToolUse", f"{GATE}.claude/hooks/probe-fail.sh"), "hook-1")
PRE_HOOK = _candidate(hook_binding_sha256("PreToolUse", f"{GATE}.claude/hooks/probe-pre.sh"), "hook-2")


def _contexts(rows) -> dict[str, str]:
    return {
        row["component_id"]: row["evidence_kind"].removeprefix("hook_context_")
        for row in rows
        if row["evidence_kind"].startswith("hook_context_")
    }


@pytest.mark.parametrize(
    ("subagent_agent", "expected"),
    [("probe-agent", "eligible"), ("other-agent", "agent_inactive"), ("", "agent_unknown")],
)
def test_a_gated_hook_in_a_subagent_session_follows_the_resolved_agent(subagent_agent, expected):
    session = HookSession(True, frozenset(), subagent=True, subagent_agent=subagent_agent)
    extraction = HookEvidenceExtraction("supported", (), session)
    assert _contexts(match_hook_evidence(extraction, {LAYER: [PRE_HOOK]}, {0: LAYER}).rows) == {"hook-2": expected}
    frontmatter = PRE_HOOK | {"binding_placement": "frontmatter"}
    assert _contexts(match_hook_evidence(extraction, {LAYER: [frontmatter]}, {0: LAYER}).rows) == {
        "hook-2": "agent_inactive"
    }, "agent-file hooks in a subagent transcript are unchanged"


# -- the projector's scoped parent lookup --------------------------------------------------------------


class _Store:
    """In-memory ``session_events`` for the parent lookup: scoped, substring-filtered, bounded."""

    def __init__(self, sessions: dict[tuple[str, str, str, str], list[str]]):
        self.sessions = sessions
        self.queries: list[dict] = []

    async def query(self, sql: str, params: dict | None = None, *, data: str | None = None) -> list[dict]:
        assert data is None and params is not None
        self.queries.append(params)
        assert "position(raw_line, {needle:String})" in sql and "is_source_record = 1" in sql
        key = tuple(params[f"param_{name}"] for name in ("project_id", "user_id", "harness", "session_id"))
        rows = [row for row in _rows(self.sessions.get(key, [])) if params["param_needle"] in row["raw_line"]]
        return rows[: params["param_limit"]]


def _subagent_extraction() -> HookEvidenceExtraction:
    return extract_hook_evidence("claude-code", _rows(_lines("gate_session_headless_subagent.jsonl")))


@pytest.mark.asyncio
async def test_the_projector_resolves_only_from_the_same_users_parent_session(monkeypatch):
    main = _lines("gate_session_headless_subagent_main.jsonl")
    store = _Store({("p", "u", "claude-code", PARENT): main})
    monkeypatch.setattr(hook_projector, "_query", store.query)
    resolved = await hook_projector.resolve_subagent(_subagent_extraction(), "p", "u", "claude-code", SUBAGENT)
    assert resolved.session.subagent_agent == "probe-agent"
    assert resolved.evidence == _subagent_extraction().evidence, "context only, never facts"
    assert [q["param_needle"] for q in store.queries] == [SUBAGENT, SPAWN]
    assert all(q["param_session_id"] == PARENT and q["param_user_id"] == "u" for q in store.queries)
    for scope in (("p", "other-user", "claude-code"), ("other-project", "u", "claude-code"), ("p", "u", "pi")):
        unresolved = await hook_projector.resolve_subagent(_subagent_extraction(), *scope, SUBAGENT)
        assert unresolved.session.subagent_agent == "", scope


@pytest.mark.asyncio
async def test_the_projector_leaves_the_agent_unknown_without_a_safe_parent_record(monkeypatch):
    main = _lines("gate_session_headless_subagent_main.jsonl")
    store = _Store({})
    monkeypatch.setattr(hook_projector, "_query", store.query)
    missing = await hook_projector.resolve_subagent(_subagent_extraction(), "p", "u", "claude-code", SUBAGENT)
    assert missing.session.subagent_agent == "", "parent not ingested yet"
    noisy = main + [json.dumps({"type": "system", "sessionId": PARENT, "content": SUBAGENT})] * 64
    store.sessions[("p", "u", "claude-code", PARENT)] = noisy
    resolved = await hook_projector.resolve_subagent(_subagent_extraction(), "p", "u", "claude-code", SUBAGENT)
    assert resolved.session.subagent_agent == "", "too many matching records to read safely"
    # Not a subagent transcript, or one linked to itself: no lookup at all.
    store.queries.clear()
    main_extraction = extract_hook_evidence("claude-code", _rows(main))
    assert await hook_projector.resolve_subagent(main_extraction, "p", "u", "claude-code", PARENT) is main_extraction
    assert await hook_projector.resolve_subagent(_subagent_extraction(), "p", "u", "claude-code", PARENT) == (
        _subagent_extraction()
    )
    assert store.queries == []


@pytest.mark.asyncio
async def test_a_subagent_session_republishes_once_its_parent_arrives(monkeypatch):
    """Projected first without its parent (unknown), then again after the parent is ingested (eligible)."""
    store = _Store({})
    writes: list[tuple[str, str | None]] = []

    async def write(sql: str, params: dict | None = None, *, data: str | None = None) -> list[dict]:
        writes.append((sql, data))
        return []

    published: dict[int, list[dict]] = {}
    previous: list[dict] = []

    async def latest(_params, _version, _kind):
        return previous[-1] if previous else None

    async def rows_of(_params, _version, generation):
        return published[generation]

    monkeypatch.setattr(hook_projector, "_query", store.query)
    monkeypatch.setattr(evidence_projector, "_query", write)
    monkeypatch.setattr(projector, "_query", write)  # publication markers
    monkeypatch.setattr(
        evidence_projector,
        "_source_rows",
        AsyncMock(return_value=_rows(_lines("gate_session_headless_subagent.jsonl"))),
    )
    monkeypatch.setattr(evidence_projector, "_mapping", AsyncMock(return_value=("complete", 7, [FAIL_HOOK, PRE_HOOK])))
    monkeypatch.setattr(evidence_projector, "_inputs_unchanged", AsyncMock(return_value=True))
    monkeypatch.setattr(evidence_projector, "_latest_complete", latest)
    monkeypatch.setattr(evidence_projector, "_published_rows", rows_of)
    monkeypatch.setattr(evidence_projector, "next_projection_generation", AsyncMock(side_effect=[21, 22]))

    async def project() -> tuple[dict, dict[str, str]]:
        writes.clear()
        result = await hook_projector.project_session_hook_evidence("p", "u", "claude-code", SUBAGENT)
        rows = [json.loads(line) for line in writes[0][1].splitlines()] if result["status"] == "complete" else []
        if rows:
            published[result["generation"]] = rows
            previous.append({"projection_generation": result["generation"], "source_revision": revision, **result})
        return result, _contexts(rows)

    revision = evidence_projector._source_revision(_rows(_lines("gate_session_headless_subagent.jsonl")))
    first, contexts = await project()
    assert first["status"] == "complete" and contexts == {"hook-1": "eligible", "hook-2": "agent_unknown"}
    assert (await project())[0]["status"] == "already_complete", "unchanged without the parent"
    store.sessions[("p", "u", "claude-code", PARENT)] = _lines("gate_session_headless_subagent_main.jsonl")
    second, contexts = await project()
    assert second["status"] == "complete" and second["generation"] == 22
    assert contexts == {"hook-1": "eligible", "hook-2": "eligible"}
    assert all(row["matcher_version"] == 3 for row in published[22])
    markers = [json.loads(data) for sql, data in writes if "component_activity_publications" in sql]
    assert [(m["status"], m["projection_generation"]) for m in markers] == [("complete", 22)]
    assert "probe-agent" not in "".join(data or "" for _, data in writes), "the agent name is never stored"
