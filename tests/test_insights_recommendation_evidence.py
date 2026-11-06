# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.insights.evidence import mcp_target, qualified, role_hash, source_ref, sources, validate_narrative
from services.insights.scope import SessionKey
from services.insights.session_meta_extractor import extract_session_meta


def meta(user="alice", sid="s", state="verified", parent=""):
    return {
        "session_key": SessionKey("p", user, "pi", sid),
        "instruction_evidence": {"state": state},
        "parent_metadata_known": True,
        "parent_session_id": parent,
    }


def facet(state="in_scope"):
    return {
        "_relevance_schema": 1,
        "_scope_hash": role_hash("role"),
        "_scope_complete": True,
        "agent_task_relevance": {"state": state, "reason": "model assessment"},
    }


def test_source_refs_are_scoped_and_require_recorded_instruction_contract():
    alice, bob = meta(), meta("bob")
    assert source_ref(alice["session_key"]) != source_ref(bob["session_key"])
    assert not qualified(meta(state="unknown")) and not qualified(meta(parent="parent"))
    index = sources(
        [alice, bob, meta(sid="unverified", state="unknown")],
        {alice["session_key"]: facet(), bob["session_key"]: facet("out_of_scope")},
        role_hash("role"),
    )
    assert len(index) == 2
    assert index[source_ref(alice["session_key"])]["eligible_for_changes"]
    assert not index[source_ref(bob["session_key"])]["eligible_for_changes"]


def test_missing_invented_off_task_and_unknown_refs_cannot_justify_changes():
    a, b = meta(), meta(sid="off")
    index = sources([a, b], {a["session_key"]: facet(), b["session_key"]: facet("out_of_scope")}, role_hash("role"))
    good, off = source_ref(a["session_key"]), source_ref(b["session_key"])
    records = [
        {"addition": "good", "evidence_refs": [good]},
        {"addition": "missing"},
        {"addition": "invented", "evidence_refs": ["bogus"]},
        {"addition": "off", "evidence_refs": [off]},
    ]
    result = validate_narrative({"suggestions": {"config_additions": records}}, index)
    assert [x["addition"] for x in result["suggestions"]["config_additions"]] == ["good"]
    assert len(result["evidence_validation"]["rejected_records"]) == 3


def test_unknown_scope_and_unversioned_facet_do_not_become_in_scope():
    a = meta()
    for data in [{}, {"agent_task_relevance": {"state": "in_scope"}}, {**facet(), "_scope_complete": False}]:
        index = sources([a], {a["session_key"]: data}, role_hash("role"))
        assert not index[source_ref(a["session_key"])]["eligible_for_changes"]


def test_unreferenced_synthesis_cannot_reintroduce_dropped_changes():
    result = validate_narrative({"at_a_glance": {"quick_win": "Add unsafe unrelated hook"}}, {})
    assert "unsafe" not in json.dumps(result)


@pytest.mark.parametrize(
    "name,args,expected",
    [
        ("mcp", {"server": "linear", "tool": "linear_get_issue"}, ("linear", True)),
        ("mcp", {"server": "github", "search": "tools"}, (None, True)),
        ("mcp", {"tool": "linear_get_issue"}, (None, True)),
        ("mcp__linear", {"tool": "linear_get_issue"}, ("linear", True)),
        ("mcp__my_server__read", {}, ("my_server", True)),
        ("bash", {}, (None, False)),
    ],
)
def test_mcp_targets_are_explicit_attempts_not_guessed_registry_components(name, args, expected):
    assert mcp_target(name, args) == expected


def test_meta_observes_actual_target_not_the_configured_mcp():
    rows = [
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "a",
                        "name": "mcp",
                        "arguments": {"server": "linear", "tool": "linear_get_issue"},
                    },
                    {"type": "toolCall", "id": "b", "name": "mcp", "arguments": {"tool": "unknown"}},
                ],
            },
        }
    ]
    data = extract_session_meta("s", [json.dumps(r) for r in rows], "pi")
    assert data["observed_mcp_target_attempts"] == {"linear": 1}
    assert data["unidentified_mcp_attempts"] == 1
    assert "github" not in data["observed_mcp_target_attempts"]


def test_configured_or_mentioned_skills_are_not_proven_successful_loads():
    from services.insights.generator import _analyze_component_utilization

    result = _analyze_component_utilization({"configured_skills": ["test-skill"]}, [{"first_prompt": "test-skill"}], [])
    assert result[0]["status"] == "unknown"


@pytest.mark.asyncio
async def test_standalone_main_without_cohort_contract_fails_closed(monkeypatch):
    from services.insights import generator

    monkeypatch.setattr(
        generator,
        "extract_all_session_metas",
        AsyncMock(return_value=[{"session_key": SessionKey("p", "u", "pi", "s")}]),
    )
    model = AsyncMock()
    monkeypatch.setattr(generator, "generate_sections", model)
    result = await generator.generate_report_content("agent", "id", db=SimpleNamespace())
    assert (
        result["metrics"].get("recommendation_evidence", {}).get("state") == "blocked"
        or result["metrics"].get("cohort", {}).get("verified_top_level_sessions") == 0
    )
    assert not result["narrative"]["suggestions"]["config_additions"]
    model.assert_not_awaited()


@pytest.mark.asyncio
async def test_truncated_role_forces_unknown_relevance(monkeypatch):
    import services.dynamic_settings as ds
    from services.insights import facets

    monkeypatch.setattr(ds, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(
        facets,
        "get_call_model",
        lambda: AsyncMock(return_value={"agent_task_relevance": {"state": "in_scope", "reason": "assumed"}}),
    )
    result = await facets.extract_facets(
        "s", "task with enough text " * 10, {"_agent_scope_excerpt": "role", "_agent_scope_complete": False}
    )
    assert result["agent_task_relevance"]["state"] == "unknown"


@pytest.mark.parametrize("bad", ["unreferenced advice", ["unreferenced advice"], 7, True])
def test_actionable_wrong_shapes_cannot_escape_through_legacy_rendering(bad):
    result = validate_narrative({"suggestions": bad, "on_the_horizon": bad, "at_a_glance": bad}, {})
    assert result["suggestions"] == {"config_additions": [], "features_to_try": [], "usage_patterns": []}
    assert result["on_the_horizon"]["opportunities"] == []
    assert "unreferenced advice" not in json.dumps(result)
    assert any(r["reason"] == "invalid_section_shape" for r in result["evidence_validation"]["rejected_records"])


def test_legacy_items_do_not_bypass_canonical_suggestion_gate():
    result = validate_narrative({"suggestions": {"items": [{"action": "unreferenced advice"}]}}, {})
    assert "items" not in result["suggestions"]


def test_every_actionable_prompt_schema_explicitly_requests_source_refs():
    from services.insights.sections import SECTION_PROMPTS

    for name, expected in [("what_works", 1), ("friction_analysis", 1), ("suggestions", 3), ("on_the_horizon", 1)]:
        assert SECTION_PROMPTS[name].count('"evidence_refs"') == expected


def test_redaction_expansion_keeps_original_scope_hash_and_marks_truncation():
    from services.insights.evidence import role_context
    from services.secrets_redactor import redact_secrets

    raw = "password=abcdefgh\n" + "x" * (2000 - len("password=abcdefgh\n"))
    assert len(redact_secrets(raw)) > 2000
    text, scope_hash, complete = role_context(raw, True)
    assert len(text) <= 2000 and scope_hash == role_hash(raw) and complete is False


@pytest.mark.asyncio
async def test_long_upstream_role_uses_same_identity_for_facet_and_generator(monkeypatch):
    import services.dynamic_settings as ds
    from services.insights import facets
    from services.insights.evidence import role_context

    raw = "role " * 450
    monkeypatch.setattr(ds, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(
        facets, "get_call_model", lambda: AsyncMock(return_value={"agent_task_relevance": {"state": "in_scope"}})
    )
    result = await facets.extract_facets(
        "s", "Task with enough input " * 10, {"_agent_scope_excerpt": raw, "_agent_scope_complete": True}
    )
    assert result["_scope_hash"] == role_context(raw, True)[1] == role_hash(raw)
    assert result["_scope_complete"] is False and result["agent_task_relevance"]["state"] == "unknown"


@pytest.mark.parametrize(
    "arguments,expected,unknown",
    [
        ('{"server":"linear","tool":"linear_get_issue"}', {"linear": 1}, 0),
        ('{"server":"linear","search":"tools"}', {}, 1),
        ("[1,2]", {}, 1),
        ("null", {}, 1),
        ("{bad", {}, 1),
    ],
)
def test_mcp_json_string_arguments_have_the_same_target_semantics(arguments, expected, unknown):
    row = {
        "type": "message",
        "message": {
            "role": "assistant",
            "content": [{"type": "toolCall", "id": "c", "name": "mcp", "arguments": arguments}],
        },
    }
    result = extract_session_meta("s", [json.dumps(row)], "pi")
    assert result["observed_mcp_target_attempts"] == expected and result["unidentified_mcp_attempts"] == unknown


def test_provider_source_index_is_bounded_and_does_not_mutate_audit_roster():
    import copy

    from services.insights.evidence import prompt_sources

    index = {
        f"s_{i}": {
            "project_id": "private-project",
            "user_id": "private-user",
            "session_id": f"private-{i}",
            "eligible_for_changes": True,
            "task_relevance": "in_scope",
        }
        for i in range(100)
    }
    before = copy.deepcopy(index)
    provider = prompt_sources(index)
    assert len(provider) == 50 and index == before
    assert "private-" not in json.dumps(provider) and "user_id" not in json.dumps(provider)
    assert list(prompt_sources(index, ["s_99", "s_1", "invalid"])) == ["s_99", "s_1"]
