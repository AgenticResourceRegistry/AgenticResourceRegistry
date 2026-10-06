# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ARD advice uses the selected listing and aggregate session evidence only."""

import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from services.insights import discovery_optimization as optimizer
from tests import discovery_support as fx


@pytest_asyncio.fixture()
async def sessions():
    engine = fx.make_engine()
    try:
        yield await fx.create_schema(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_agent_suggestions_use_versioned_ard_and_aggregate_signals(sessions, monkeypatch):
    async def setting(*_args):
        return "test/model"

    monkeypatch.setattr("services.dynamic_settings.get", setting)
    prompts = []

    async def model(prompt, **_kwargs):
        prompts.append(prompt)
        return {
            "suggestions": [
                {
                    "illustrative_query": "security vulnerability pull request review",
                    "suggested_description": "Reviews pull requests for security vulnerabilities using GitHub and a review skill.",
                    "rationale": "Clarify the review focus already supported by the linked skill.",
                    "evidence_keys": ["total_sessions"],
                }
            ]
        }

    monkeypatch.setattr(optimizer, "get_call_model", lambda: model)
    async with sessions() as db:
        owner = await fx.user(db)
        skill = await fx.skill(db, owner)
        agent = await fx.agent(db, owner, components=[("skill", skill.id, "Security Review")])
        from models.agent import AgentVersion

        version = await db.get(AgentVersion, agent.latest_version_id)
        version.description = "Helps with development tasks."
        await db.flush()
        report = SimpleNamespace(subject_type="agent", agent_id=agent.id, agent_version_id=agent.latest_version_id)
        result = await optimizer.generate_discovery_optimization(
            db,
            report,
            {
                "metrics": {"rich": {"total_sessions": 5, "tool_errors": 2, "top_tools": [("secret-command", 99)]}},
                "facets_summary": {
                    "goal_categories": [("write_tests", 3), ("PRIVATE CONSUMER PROMPT", 7)],
                    "repeated_instructions": [{"instruction": "PRIVATE CONSUMER PROMPT", "frequency": 7}],
                },
            },
        )
    assert result["version"] == 1
    assert result["state"] == "assessed"
    assert result["session_signals"] == {"total_sessions": 5, "tool_errors": 2, "goal:write_tests": 3}
    assert result["suggestions"][0]["field"] == "description"
    assert result["suggestions"][0]["relevance_after"] >= result["suggestions"][0]["relevance_before"]
    assert "secret-command" not in prompts[0]
    assert "PRIVATE CONSUMER PROMPT" not in prompts[0]
    assert "5" in prompts[0]
    assert "You are a meticulous reviewer" not in prompts[0]


@pytest.mark.asyncio
async def test_component_suggestions_never_send_consumer_text_or_unsupported_metrics(sessions, monkeypatch):
    async def setting(*_args):
        return "test/model"

    monkeypatch.setattr("services.dynamic_settings.get", setting)
    prompts = []

    async def model(prompt, **_kwargs):
        prompts.append(prompt)
        return {
            "suggestions": [
                {
                    "illustrative_query": "review code for authentication bugs",
                    "suggested_description": "Reviews code changes for authentication and authorization vulnerabilities.",
                    "rationale": "Specify the existing skill's security-review task more clearly.",
                    "evidence_keys": ["observed_calls"],  # not a skill evidence field
                }
            ]
        }

    monkeypatch.setattr(optimizer, "get_call_model", lambda: model)
    async with sessions() as db:
        owner = await fx.user(db)
        skill = await fx.skill(db, owner)
        report = SimpleNamespace(
            subject_type="component",
            component_type="skill",
            component_id=skill.id,
            component_version_id=skill.latest_version_id,
        )
        result = await optimizer.generate_discovery_optimization(
            db,
            report,
            {
                "metrics": {"present_sessions": 4, "loaded_sessions": 2, "observed_calls": 999},
                "narrative": {"summary": "PRIVATE CONSUMER PROMPT"},
            },
        )
    # It can cite only signals this extractor observed, so every reply broke a rule, even after repair.
    assert result == {"version": 1, "state": "unknown", "reason": "replies_failed_checks"}
    assert len(prompts) == optimizer.MAX_ATTEMPTS
    assert "evidence_keys may name only" in prompts[-1]
    assert "observed_calls" not in prompts[-1].split("Your reply was rejected", 1)[1]
    data = json.loads(prompts[0].split("UNTRUSTED LISTING DATA:\n", 1)[1])
    assert data["session_signals"] == {"present_sessions": 4, "loaded_sessions": 2}
    assert all("PRIVATE CONSUMER PROMPT" not in p and "999" not in p for p in prompts)


@pytest.mark.asyncio
async def test_unsupported_claims_are_not_published(sessions, monkeypatch):
    async def setting(*_args):
        return "test/model"

    async def model(*_args, **_kwargs):
        return {
            "suggestions": [
                {
                    "illustrative_query": "automated security review",
                    "suggested_description": "Automatically guarantees compliant and efficient security reviews.",
                    "rationale": "This will increase traffic for the listing.",
                    "evidence_keys": [],
                }
            ]
        }

    monkeypatch.setattr("services.dynamic_settings.get", setting)
    monkeypatch.setattr(optimizer, "get_call_model", lambda: model)
    async with sessions() as db:
        owner = await fx.user(db)
        agent = await fx.agent(db, owner)
        report = SimpleNamespace(subject_type="agent", agent_id=agent.id, agent_version_id=agent.latest_version_id)
        result = await optimizer.generate_discovery_optimization(db, report, {"metrics": {"rich": {}}})
    assert result["state"] == "unknown"
    assert "suggestions" not in result


def test_request_flag_is_opt_in():
    from schemas.insights import GenerateComponentInsightRequest, GenerateInsightRequest

    assert not GenerateInsightRequest().include_discovery_optimization
    assert not GenerateComponentInsightRequest().include_discovery_optimization
    assert GenerateInsightRequest(include_discovery_optimization=True).include_discovery_optimization
    assert GenerateComponentInsightRequest(include_discovery_optimization=True).include_discovery_optimization


@pytest.mark.asyncio
async def test_a_reply_that_breaks_a_rule_is_repaired_once_it_is_told_which(sessions, monkeypatch):
    async def setting(key, *_args):
        return "test/sections" if key == "insights.model_sections" else pytest.fail(f"unexpected setting {key}")

    monkeypatch.setattr("services.dynamic_settings.get", setting)
    prompts, overrides = [], []
    good = {
        "illustrative_query": "security vulnerability pull request review",
        "suggested_description": "Reviews pull requests for security vulnerabilities using GitHub and a review skill.",
        "rationale": "Clarify the review focus already supported by the linked skill.",
        "evidence_keys": [],
    }
    replies = [{"suggestions": [{**good, "evidence_keys": ["made_up_signal"]}]}, {"suggestions": [good]}]

    async def model(prompt, **kwargs):
        prompts.append(prompt)
        overrides.append(kwargs.get("model_override"))
        return replies.pop(0)

    monkeypatch.setattr(optimizer, "get_call_model", lambda: model)
    async with sessions() as db:
        owner = await fx.user(db)
        skill = await fx.skill(db, owner)
        agent = await fx.agent(db, owner, components=[("skill", skill.id, "Security Review")])
        from models.agent import AgentVersion

        version = await db.get(AgentVersion, agent.latest_version_id)
        version.description = "Helps with development tasks."
        await db.flush()
        report = SimpleNamespace(subject_type="agent", agent_id=agent.id, agent_version_id=agent.latest_version_id)
        result = await optimizer.generate_discovery_optimization(db, report, {"metrics": {"rich": {}}})
    assert result["state"] == "assessed" and len(result["suggestions"]) == 1
    assert "Your reply was rejected: evidence_keys may name only" in prompts[1]
    assert "made_up_signal" not in prompts[1], "model text never goes back into a repair prompt"
    assert overrides == ["test/sections", "test/sections"]


@pytest.mark.asyncio
async def test_no_accurate_edit_is_an_assessed_empty_result_not_a_failure(sessions, monkeypatch):
    async def setting(*_args):
        return None

    calls = []

    async def model(*_args, **_kwargs):
        calls.append(1)
        return {"suggestions": []}

    monkeypatch.setattr("services.dynamic_settings.get", setting)
    monkeypatch.setattr(optimizer, "get_call_model", lambda: model)
    async with sessions() as db:
        owner = await fx.user(db)
        agent = await fx.agent(db, owner)
        report = SimpleNamespace(subject_type="agent", agent_id=agent.id, agent_version_id=agent.latest_version_id)
        result = await optimizer.generate_discovery_optimization(db, report, {"metrics": {"rich": {}}})
    assert result["state"] == "assessed" and result["suggestions"] == []
    assert len(calls) == 1, "a valid empty answer is not retried"


@pytest.mark.asyncio
async def test_model_that_returns_nothing_is_unavailable(sessions, monkeypatch):
    async def setting(*_args):
        return None

    calls = []

    async def model(*_args, **_kwargs):
        calls.append(1)
        return {}  # what the model layer returns for an unconfigured model or a failed call

    monkeypatch.setattr("services.dynamic_settings.get", setting)
    monkeypatch.setattr(optimizer, "get_call_model", lambda: model)
    async with sessions() as db:
        owner = await fx.user(db)
        agent = await fx.agent(db, owner)
        report = SimpleNamespace(subject_type="agent", agent_id=agent.id, agent_version_id=agent.latest_version_id)
        result = await optimizer.generate_discovery_optimization(db, report, {"metrics": {"rich": {}}})
    assert result == {"version": 1, "state": "unavailable", "reason": "model_unavailable"}
    assert len(calls) == optimizer.MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_optional_enrichment_failure_does_not_fail_report(monkeypatch):
    from services.insights import batch

    async def broken(*_args):
        raise RuntimeError("secret prompt from provider")

    monkeypatch.setattr(optimizer, "generate_discovery_optimization", broken)
    report = SimpleNamespace(discovery_optimization_requested=True)
    content = {"narrative": {"summary": "usage"}}
    await batch._add_discovery_optimization(None, report, content)
    assert content["narrative"]["discovery"] == {"version": 1, "state": "unavailable", "reason": "generation_failed"}
    assert content["narrative"]["summary"] == "usage"
    assert "secret" not in json.dumps(content)

    report.discovery_optimization_requested = False
    content = {"narrative": {}}
    await batch._add_discovery_optimization(None, report, content)
    assert "discovery" not in content["narrative"]


def test_component_reports_serve_only_a_reviewed_discovery_version():
    from api.routes.insights import _safe_component_narrative

    def served(discovery):
        report = SimpleNamespace(narrative={"summary": "s", "discovery": discovery})
        return _safe_component_narrative(report)

    v1 = {"version": 1, "state": "assessed", "suggestions": []}
    assert served(v1)["discovery"] == v1
    for unreviewed in ({"version": 2, "state": "assessed"}, {"state": "assessed"}, {"version": True}, "text"):
        assert "discovery" not in served(unreviewed)
    assert served({"version": 2})["summary"] == "s"


def test_an_example_query_that_repeats_the_listings_name_is_circular():
    listing = SimpleNamespace(name="auth-review", slug="auth-review")
    assert optimizer._repeats_name("auth-review code changes", listing)
    assert optimizer._repeats_name("Review AUTH changes", listing), "word order and case do not matter"
    assert not optimizer._repeats_name("security review of login code", listing)
    assert not optimizer._repeats_name("check authentication", SimpleNamespace(name="", slug=""))


def test_a_schema_failure_names_the_fields_but_never_the_text():
    from pydantic import ValidationError

    secret = "x" * 700  # over the description limit; stands in for model text that must not be echoed
    with pytest.raises(ValidationError) as caught:
        optimizer.Proposals.model_validate(
            {
                "suggestions": [
                    {"illustrative_query": "find login bugs", "suggested_description": secret, "rationale": "r" * 20}
                ]
            }
        )
    where = optimizer._where(caught.value)
    assert "suggestions.0.suggested_description: string_too_long" in where
    assert secret not in where


@pytest.mark.asyncio
async def test_edits_the_ranker_shows_do_not_help_mean_no_change_is_needed(sessions, monkeypatch):
    async def setting(*_args):
        return None

    async def model(*_args, **_kwargs):
        return {
            "suggestions": [
                {
                    "illustrative_query": "kubernetes cluster upgrades",
                    "suggested_description": "Helps with everyday development tasks and related engineering work.",
                    "rationale": "A slightly longer description of the same scope.",
                    "evidence_keys": [],
                }
            ]
        }

    monkeypatch.setattr("services.dynamic_settings.get", setting)
    monkeypatch.setattr(optimizer, "get_call_model", lambda: model)
    async with sessions() as db:
        owner = await fx.user(db)
        agent = await fx.agent(db, owner)
        report = SimpleNamespace(subject_type="agent", agent_id=agent.id, agent_version_id=agent.latest_version_id)
        result = await optimizer.generate_discovery_optimization(db, report, {"metrics": {"rich": {}}})
    assert result["state"] == "assessed" and result["suggestions"] == []


@pytest.mark.parametrize(
    "text",
    [
        "Agent for development tasks. The author should name the specific languages or workflows it covers.",
        "Reviews pull requests for [language] projects.",
        "Reviews code. TODO: describe the checks.",
        "Reviews code; the owner needs to be specified.",
    ],
)
def test_advice_to_the_author_is_never_a_description(text):
    assert optimizer._META_TEXT.search(text)


def test_an_ordinary_description_is_not_mistaken_for_advice():
    ok = "Reviews login, session and JWT handling code for missing checks, and reports each finding with file and line."
    assert not optimizer._META_TEXT.search(ok)


@pytest.mark.asyncio
async def test_a_description_that_instructs_its_author_is_rejected_not_published(sessions, monkeypatch):
    async def setting(*_args):
        return None

    async def model(*_args, **_kwargs):
        return {
            "suggestions": [
                {
                    "illustrative_query": "software development helper with skills",
                    "suggested_description": "Agent for development tasks using its skills. The author should name the specific tasks it covers.",
                    "rationale": "The metadata supports only a development agent.",
                    "evidence_keys": [],
                }
            ]
        }

    monkeypatch.setattr("services.dynamic_settings.get", setting)
    monkeypatch.setattr(optimizer, "get_call_model", lambda: model)
    async with sessions() as db:
        owner = await fx.user(db)
        agent = await fx.agent(db, owner)
        report = SimpleNamespace(subject_type="agent", agent_id=agent.id, agent_version_id=agent.latest_version_id)
        result = await optimizer.generate_discovery_optimization(db, report, {"metrics": {"rich": {}}})
    assert result["state"] == "unknown"
    assert "suggestions" not in result
