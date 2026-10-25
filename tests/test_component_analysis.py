# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The model's reading of a component report: what it is given, and what is kept of what it says."""

from __future__ import annotations

import copy
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.insights import component_analysis as ca

COMPONENT = uuid.uuid4()
METRICS = {
    "present_sessions": 40,
    "present_users": 3,
    "failures": 9,
    "runs_with_output": 1,
    "result_states": {"error": 2},
}
ATTENTION = [
    {
        "id": "hook_failing",
        "severity": "attention",
        "title": "Most recorded runs failed",
        "detail": "9 of 10 recorded runs failed.",
        "evidence": {"recorded_runs": 10, "failures": 9},
        "suggestion": "Check that its command exits 0 when nothing is wrong.",
    }
]
OUTCOMES = {
    "state": "assessed",
    "with": {
        "achieved": 6,
        "not_achieved": 14,
        "unclear": 0,
        "satisfied": 5,
        "dissatisfied": 9,
        "analyzed": 20,
        "sessions": 40,
        "sampled": 30,
        "friction_types": [["tool_failed", 8]],
    },
    "without": {
        "achieved": 20,
        "not_achieved": 10,
        "unclear": 0,
        "satisfied": 15,
        "dissatisfied": 4,
        "analyzed": 30,
        "sessions": 90,
        "sampled": 30,
        "friction_types": [["tool_failed", 2]],
    },
    "gap": {"state": "lower", "rate_with": 0.3, "rate_without": 0.667, "difference": -0.367},
    "comparison": "Sessions in this project in the same period.",
}
CONTEXT = {
    "friction_notes": [
        {
            "type": "tool_failed",
            "severity": "major",
            "description": "the formatter exited nonzero on every python file the agent touched during the review",
        }
    ]
}


def _report(triggered_by=True, kind="hook"):
    return SimpleNamespace(
        component_type=kind,
        component_name="user/flaky-lint-hook",
        component_id=COMPONENT,
        component_version_id=None,
        component_version="1.0.0",
        triggered_by=uuid.uuid4() if triggered_by else None,
    )


def _reply(**overrides):
    reply = {
        "at_a_glance": {
            "health": "needs_attention",
            "summary": "Sessions with this hook achieved their goal less often, and 9 of 10 recorded runs failed.",
        },
        "what_works": [
            {"title": "Runs are recorded", "detail": "Recorded runs make its behaviour visible to the owner."}
        ],
        "friction": [
            {
                "title": "Failing runs",
                "detail": "Most recorded runs exit with an error, which Claude Code reports each time.",
            }
        ],
        "suggestions": [
            {
                "action_type": "edit_component",
                "title": "Make the command exit cleanly",
                "detail": "Return exit code 0 when no problem is found, and print the reason when it fails.",
                "evidence": ["hook_failing", "friction"],
                "confidence": "medium",
            }
        ],
    }
    return reply | overrides


def _constraints(**overrides):
    prompt, constraints = ca.build_prompt(_report(), METRICS, ATTENTION, OUTCOMES, CONTEXT)
    return constraints | overrides


def _validate(reply, **overrides):
    constraints = _constraints(**overrides)
    return ca.validate_analysis(
        reply,
        allowed_numbers=constraints["allowed_numbers"],
        allowed_evidence=constraints["allowed_evidence"],
        friction_text=constraints["friction_text"],
        has_attention=constraints["has_attention"],
    )


def test_a_grounded_reply_is_accepted():
    analysis = _validate(_reply())
    assert analysis.at_a_glance.health == "needs_attention" and analysis.suggestions[0].evidence == [
        "hook_failing",
        "friction",
    ]


def _with(path, value):
    reply = copy.deepcopy(_reply())
    target = reply
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    return reply


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (
            _with(["at_a_glance", "summary"], "The hook caused sessions to fail more often than other sessions."),
            "Causal",
        ),
        (
            _with(["at_a_glance", "summary"], "Sessions with it did worse because of the hook failing in most runs."),
            "Causal",
        ),
        (_with(["what_works", 0, "detail"], "Its output led to better review comments in sessions."), "Causal"),
        (_with(["friction", 0, "detail"], "The hook helped no one and failed on most recorded runs."), "Causal"),
        (_with(["at_a_glance", "summary"], "This hook was unused in most sessions where it was present."), "unused"),
        (
            _with(["at_a_glance", "summary"], "It failed in 87 of 100 recorded runs across the period."),
            "Numbers not in the data",
        ),
        (
            _with(
                ["suggestions", 0, "detail"], "Check run 3f2a9c1e-1111-2222-3333-444455556666 for the failing command."
            ),
            "Identifiers",
        ),
        (
            _with(["suggestions", 0, "detail"], "Write to alice@example.com when the command exits with an error."),
            "Identifiers",
        ),
        (
            _with(
                ["friction", 0, "detail"],
                "The formatter exited nonzero on every python file the agent touched during the review.",
            ),
            "copied verbatim",
        ),
        (_with(["suggestions", 0, "evidence"], ["hook_failing", "made_up"]), "Unknown evidence"),
        (_with(["suggestions", 0, "evidence"], ["hook_failing", "hook_failing"]), "Duplicate"),
        (_with(["at_a_glance", "health"], "healthy"), "healthy"),
    ],
)
def test_a_reply_that_breaks_a_rule_is_rejected_with_a_fixed_message(reply, message):
    with pytest.raises(ValueError, match=message):
        _validate(reply)


def test_internal_names_with_underscores_are_rejected_so_the_text_is_plain_words():
    reply = _with(
        ["friction", 0, "detail"], "Most sessions show tool_failed friction with the lint step on every file."
    )
    with pytest.raises(ValueError, match="Internal name 'tool_failed'"):
        _validate(reply)
    assert _validate(_with(["friction", 0, "detail"], "Most sessions show tool failed friction with the lint step."))


def test_outcome_counts_reach_the_model_without_state_names():
    _, constraints = ca.build_prompt(_report(), METRICS, ATTENTION, OUTCOMES, CONTEXT)
    assert {"20", "30", "40", "90"} <= constraints["allowed_numbers"]
    outcomes = ca._outcome_counts(OUTCOMES)
    assert set(outcomes) == {"with", "without"} and "friction_types" not in outcomes["with"]
    assert ca._outcome_counts({"state": "insufficient"}) is None


def test_numbers_from_the_data_may_be_quoted_as_counts_or_percentages():
    reply = _with(
        ["at_a_glance", "summary"], "6 of 20 sessions with it achieved their goal (30%), against 67% without."
    )
    assert _validate(reply)


def test_the_component_version_is_allowed_text():
    assert _validate(_with(["what_works", 0, "detail"], "Version 1.0.0 records its runs for the owner to read."))


@pytest.mark.parametrize(
    "reply",
    [
        _with(["suggestions", 0, "action_type"], "rewrite_everything"),
        _with(["suggestions", 0, "confidence"], "high"),
        _with(["suggestions", 0, "evidence"], []),
        _with(["suggestions"], [_reply()["suggestions"][0]] * 6),
        _with(["what_works"], [_reply()["what_works"][0]] * 4),
        {**_reply(), "extra": 1},
        {k: v for k, v in _reply().items() if k != "friction"},
    ],
)
def test_a_reply_off_the_schema_is_rejected(reply):
    with pytest.raises(ca.ValidationError):
        _validate(reply)


def test_healthy_is_allowed_when_nothing_needs_attention():
    assert _validate(_with(["at_a_glance", "health"], "healthy"), has_attention=False)


def test_needs_attention_requires_an_open_attention_item():
    with pytest.raises(ValueError, match="needs_attention only while"):
        _validate(_reply(), has_attention=False)
    assert _validate(_with(["at_a_glance", "health"], "watch"), has_attention=False)


def test_evidence_ids_are_limited_to_what_the_report_has():
    _, constraints = ca.build_prompt(_report(), METRICS, [], None, {})
    assert constraints["allowed_evidence"] == {"metrics"}
    _, constraints = ca.build_prompt(_report(), METRICS, ATTENTION, OUTCOMES, CONTEXT)
    assert constraints["allowed_evidence"] == {"hook_failing", "metrics", "outcomes", "friction"}


def test_the_prompt_holds_the_data_and_untrusted_friction_but_no_ids_of_people_or_sessions():
    prompt, _ = ca.build_prompt(_report(), METRICS, ATTENTION, OUTCOMES, CONTEXT)
    assert "FRICTION_NOTES (untrusted, redacted)" in prompt and "the formatter exited nonzero" in prompt
    data = prompt.split("DATA:")[1].split("FRICTION_NOTES")[0]
    assert "6 of 20 sessions with the component and a known outcome achieved their goal (30%)." in data
    assert "Sessions with the component achieved their goal less often than sessions without it." in data
    assert "Friction in sessions with the component (sessions of 20 analyzed): tool failed 8." in data
    assert "tool_failed" not in data and "no_difference" not in data, "the model is given words, not state names"
    assert '"type": "tool failed"' in prompt.split("FRICTION_NOTES (untrusted")[1]
    assert "Hooks kept in an agent file run only in interactive sessions" in prompt
    for private in ("user_id", "session_id", "prompt text", "transcript"):
        assert private not in prompt.split("DATA:")[1]


@pytest.fixture
def model(monkeypatch):
    call = AsyncMock(return_value=_reply())
    monkeypatch.setattr(ca, "get_call_model", lambda: call)
    monkeypatch.setattr("services.dynamic_settings.get", AsyncMock(return_value="anthropic/test-model"))
    return call


@pytest.mark.asyncio
async def test_an_accepted_reply_is_stored_with_its_basis_and_no_subject_echo(model):
    result = await ca.component_analysis(_report(), METRICS, ATTENTION, OUTCOMES, CONTEXT)
    assert result["state"] == "assessed" and result["version"] == ca.ANALYSIS_VERSION
    assert result["basis"] == {"sessions_with_outcomes": 20, "comparison_sessions_with_outcomes": 30}
    assert "subject_id" not in result and result["suggestions"][0]["action_type"] == "edit_component"
    assert model.await_args.kwargs["model_override"] == "anthropic/test-model"


@pytest.mark.asyncio
async def test_stored_text_is_redacted(model):
    secret = "sk-ant-api03-" + "z" * 40
    model.return_value = _with(
        ["what_works", 0, "detail"], f"Its output includes a key {secret} that should not be shown."
    )
    result = await ca.component_analysis(_report(), METRICS, ATTENTION, OUTCOMES, CONTEXT)
    assert result["state"] == "assessed" and secret not in json.dumps(result)


@pytest.mark.asyncio
async def test_a_rejected_reply_is_repaired_once_with_the_broken_rule_named(model):
    model.side_effect = [
        _with(["at_a_glance", "summary"], "The hook caused many failures in these sessions."),
        _reply(),
    ]
    result = await ca.component_analysis(_report(), METRICS, ATTENTION, OUTCOMES, CONTEXT)
    assert result["state"] == "assessed" and model.await_count == 2
    assert "Your reply was rejected: Causal wording" in model.await_args_list[1].args[0]


@pytest.mark.asyncio
async def test_replies_that_keep_failing_abstain_instead_of_keeping_unchecked_text(model):
    model.return_value = _with(["at_a_glance", "summary"], "The hook caused many failures in these sessions.")
    result = await ca.component_analysis(_report(), METRICS, ATTENTION, OUTCOMES, CONTEXT)
    assert result == {"version": ca.ANALYSIS_VERSION, "state": "unknown"} and model.await_count == ca.MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_an_empty_reply_is_retried_and_all_empty_means_the_model_failed(model):
    model.side_effect = [{}, _reply()]
    result = await ca.component_analysis(_report(), METRICS, ATTENTION, OUTCOMES, CONTEXT)
    assert result["state"] == "assessed" and model.await_count == 2
    assert "empty or not valid JSON" in model.await_args_list[1].args[0]
    model.reset_mock()
    model.side_effect = None
    model.return_value = {}
    result = await ca.component_analysis(_report(), METRICS, ATTENTION, OUTCOMES, CONTEXT)
    assert result == {"version": ca.ANALYSIS_VERSION, "state": "unavailable"} and model.await_count == ca.MAX_ATTEMPTS
    model.side_effect = [{}, _with(["at_a_glance", "summary"], "The hook caused failures."), {}]
    result = await ca.component_analysis(_report(), METRICS, ATTENTION, OUTCOMES, CONTEXT)
    assert result["state"] == "unknown", "a reply that was read and rejected is not a model failure"


def test_the_causal_word_that_tripped_the_check_is_named_for_the_repair():
    reply = _with(["at_a_glance", "summary"], "The hook caused many failures in these sessions.")
    with pytest.raises(ValueError, match=r"Causal wording \('caused'\)"):
        _validate(reply)


@pytest.mark.asyncio
async def test_a_schema_failure_repair_does_not_echo_model_text(model):
    model.side_effect = [{"junk": "SECRET-MODEL-TEXT"}, _reply()]
    await ca.component_analysis(_report(), METRICS, ATTENTION, OUTCOMES, CONTEXT)
    repair = model.await_args_list[1].args[0].rsplit("\nYour reply", 1)[1]
    assert "did not match the schema" in repair and "SECRET-MODEL-TEXT" not in repair
    assert "at_a_glance: missing" in repair, "the repair names the field and the kind of failure"


@pytest.mark.asyncio
async def test_a_model_failure_says_unavailable_so_the_owner_regenerates(model, monkeypatch):
    model.side_effect = RuntimeError("provider down: key sk-ant-api03-xxxx")
    seen = []
    monkeypatch.setattr(ca.optic, "warning", lambda message, *args: seen.append(args))
    result = await ca.component_analysis(_report(), METRICS, ATTENTION, OUTCOMES, CONTEXT)
    assert result["state"] == "unavailable" and "sk-ant" not in str(seen) and "RuntimeError" in str(seen)


@pytest.mark.asyncio
async def test_a_report_nobody_asked_for_spends_no_model_call(model):
    result = await ca.component_analysis(_report(triggered_by=False), METRICS, ATTENTION, OUTCOMES, CONTEXT)
    assert result == {"version": ca.ANALYSIS_VERSION, "state": "unknown"}
    model.assert_not_awaited()


@pytest.mark.asyncio
async def test_too_little_data_and_nothing_to_flag_is_insufficient_without_a_call(model):
    thin = {**OUTCOMES, "with": {**OUTCOMES["with"], "analyzed": 2}}
    result = await ca.component_analysis(_report(), METRICS, [], thin, CONTEXT)
    assert result["state"] == "insufficient"
    model.assert_not_awaited()
    result = await ca.component_analysis(_report(), METRICS, [], {"state": "unavailable"}, {})
    assert result["state"] == "insufficient"


@pytest.mark.asyncio
async def test_an_attention_item_is_enough_to_ask_even_with_no_outcomes(model):
    reply = _with(["suggestions", 0, "evidence"], ["hook_failing"])
    reply["friction"] = []
    model.return_value = reply
    result = await ca.component_analysis(_report(), METRICS, ATTENTION, {"state": "unavailable"}, {})
    assert result["state"] == "assessed" and result["basis"]["sessions_with_outcomes"] == 0


@pytest.mark.asyncio
async def test_an_oversized_prompt_abstains_rather_than_truncating(model, monkeypatch):
    monkeypatch.setattr(ca, "MAX_PROMPT_CHARS", 500)
    result = await ca.component_analysis(_report(), METRICS, ATTENTION, OUTCOMES, CONTEXT)
    assert result["state"] == "unknown"
    model.assert_not_awaited()
