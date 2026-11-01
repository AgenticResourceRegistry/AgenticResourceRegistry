# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Rule-based attention items: each fires on its threshold and says only what the counts show."""

from __future__ import annotations

import re

import pytest

from services.insights.component_attention import attention_items


def _coverage(processed=10, state="observed", **eligibility):
    return {
        "projection": {"projection_complete_sessions": processed},
        "attribution_state": state,
        "eligibility": {"headless_sessions": 0, **eligibility},
    }


def _without_disclaimer(text):
    """The items say a thing is not proven; only an assertion of disuse is a problem."""
    return re.sub(r"does not show it (?:was )?never (?:used|ran)", "", text)


def _ids(items):
    return [item["id"] for item in items]


def _mcp(calls=0, errors=0, sessions=0):
    return {"observed_calls": calls, "observed_sessions": sessions, "result_states": {"error": errors}}


def test_mcp_with_many_errors_needs_attention_and_says_how_many():
    (item,) = attention_items("mcp", _mcp(calls=10, errors=3, sessions=4), _coverage(), None)
    assert (item["id"], item["severity"], item["detail"]) == (
        "mcp_call_errors",
        "attention",
        "3 of 10 observed calls returned an error.",
    )
    assert item["evidence"] == {"calls": 10, "errors": 3} and item["suggestion"]


@pytest.mark.parametrize(("calls", "errors"), [(4, 4), (10, 1), (0, 0)])
def test_mcp_errors_stay_quiet_below_the_call_floor_or_rate(calls, errors):
    assert "mcp_call_errors" not in _ids(attention_items("mcp", _mcp(calls, errors, 1), _coverage(), None))


def test_mcp_present_but_never_called_says_this_is_not_proof_of_no_use():
    (item,) = attention_items("mcp", _mcp(), _coverage(8, "no_observed_calls"), None)
    assert item["id"] == "mcp_no_calls" and item["severity"] == "info"
    assert "does not show it was never used" in item["detail"]
    assert not re.search(r"\b(unused|never used)\b", _without_disclaimer(item["detail"]), re.I)


def test_mcp_no_calls_needs_a_processed_cohort_and_the_no_calls_state():
    assert attention_items("mcp", _mcp(), _coverage(4, "no_observed_calls"), None) == []
    assert attention_items("mcp", _mcp(), _coverage(8, "attribution_not_possible"), None) == []


def test_mcp_rarely_called():
    assert _ids(attention_items("mcp", _mcp(3, 0, 2), _coverage(20), None)) == ["mcp_rarely_called"]
    assert attention_items("mcp", _mcp(3, 0, 8), _coverage(20), None) == []


def _skill(loads=0, available=10, loaded=0):
    return {"confirmed_loads": loads, "available_sessions": available, "loaded_sessions": loaded}


def test_skill_present_but_never_loaded_needs_attention():
    (item,) = attention_items("skill", _skill(), _coverage(6, "no_observed_skill_use"), None)
    assert (item["id"], item["severity"]) == ("skill_never_loaded", "attention")
    assert "description say when to use it" in item["suggestion"]


def test_skill_rarely_loaded_and_unknown_availability():
    assert _ids(attention_items("skill", _skill(2, 20, 2), _coverage(20), None)) == ["skill_rarely_loaded"]
    assert attention_items("skill", _skill(2, None, 2), _coverage(20), None) == []
    assert attention_items("skill", _skill(5, 20, 5), _coverage(20), None) == []


def _hook(failures=0, output=0, blocks=0, silent=None, present=10, eligible=10, recorded_sessions=5):
    return {
        "failures": failures,
        "runs_with_output": output,
        "blocks": blocks,
        "silent_runs": silent,
        "present_sessions": present,
        "eligible_sessions": eligible,
        "sessions_with_recorded_run": recorded_sessions,
    }


def test_hook_failing_most_runs_needs_attention():
    (item,) = attention_items("hook", _hook(failures=9, output=1), _coverage(), None)
    assert (item["id"], item["detail"]) == ("hook_failing", "9 of 10 recorded runs failed.")


def test_hook_failures_count_silent_runs_in_the_denominator_and_need_three_failures():
    assert "hook_failing" not in _ids(attention_items("hook", _hook(failures=3, output=1, silent=6), _coverage(), None))
    assert "hook_failing" not in _ids(attention_items("hook", _hook(failures=2, output=0), _coverage(), None))


def test_hook_that_cannot_run_headless_points_at_the_settings_placement():
    items = attention_items("hook", _hook(present=8), _coverage(headless_sessions=3), None)
    (item,) = [i for i in items if i["id"] == "hook_headless"]
    assert item["severity"] == "attention" and "--hooks=settings" in item["suggestion"]
    assert attention_items("hook", _hook(present=20), _coverage(headless_sessions=3), None) == []


def test_hook_with_unverified_sessions_and_no_recorded_runs_gets_info_items_only():
    items = attention_items("hook", _hook(recorded_sessions=0), _coverage(version_unverified_sessions=4), None)
    assert sorted(_ids(items)) == ["hook_no_recorded_runs", "hook_version_unverified"]
    assert {i["severity"] for i in items} == {"info"}
    assert "does not show it never ran" in next(i for i in items if i["id"] == "hook_no_recorded_runs")["detail"]


def _outcomes(with_side, without_side, gap_state):
    return {"state": "assessed", "with": with_side, "without": without_side, "gap": {"state": gap_state}}


def _side(achieved, not_achieved, analyzed=20, friction=()):
    return {
        "achieved": achieved,
        "not_achieved": not_achieved,
        "analyzed": analyzed,
        "friction_types": [list(item) for item in friction],
    }


def test_a_lower_achieved_rate_is_flagged_as_an_association_with_both_samples():
    outcomes = _outcomes(_side(6, 14), _side(20, 10, 30), "lower")
    (item,) = attention_items("skill", _skill(5, 20, 5), _coverage(20), outcomes)
    assert item["id"] == "outcomes_lower" and item["severity"] == "attention"
    assert "6 of 20 with a known outcome (30%), against 20 of 30 (67%)" in item["detail"]
    assert "association" in item["detail"] and item["evidence"]["known_without"] == 30


def test_a_higher_achieved_rate_is_only_informational():
    (item,) = attention_items(
        "skill", _skill(5, 20, 5), _coverage(20), _outcomes(_side(18, 2), _side(10, 10), "higher")
    )
    assert (item["id"], item["severity"]) == ("outcomes_higher", "info")


def test_no_difference_and_insufficient_gaps_raise_nothing():
    for state in ("no_difference", "insufficient"):
        assert (
            attention_items("skill", _skill(5, 20, 5), _coverage(20), _outcomes(_side(12, 8), _side(13, 7), state))
            == []
        )


def test_over_represented_friction_needs_count_ratio_and_gap():
    with_side = _side(12, 8, friction=[("tool_failed", 8), ("buggy_code", 3), ("user_unclear", 2)])
    without = _side(13, 7, friction=[("tool_failed", 2), ("buggy_code", 2)])
    items = attention_items("skill", _skill(5, 20, 5), _coverage(20), _outcomes(with_side, without, "no_difference"))
    assert _ids(items) == ["friction_tool_failed"]
    assert "8 of 20 analyzed sessions with this component, against 2 of 20 without it" in items[0]["detail"]


def test_friction_a_component_cannot_cause_is_never_flagged():
    with_side = _side(12, 8, friction=[("external_issue", 9), ("user_unclear", 8)])
    without = _side(13, 7, friction=[])
    assert (
        attention_items("skill", _skill(5, 20, 5), _coverage(20), _outcomes(with_side, without, "no_difference")) == []
    )


def test_friction_is_not_compared_when_a_side_is_too_small():
    items = attention_items(
        "skill",
        _skill(5, 20, 5),
        _coverage(20),
        _outcomes(_side(6, 4, 10, [("tool_failed", 9)]), _side(2, 1, 3), "insufficient"),
    )
    assert items == []


def test_outcomes_that_are_not_assessed_add_no_items():
    for outcomes in (None, {"state": "unavailable"}, {"state": "insufficient"}):
        assert attention_items("skill", _skill(5, 20, 5), _coverage(20), outcomes) == []


def test_attention_items_come_before_info_and_keep_rule_order():
    outcomes = _outcomes(_side(18, 2), _side(10, 10), "higher")
    items = attention_items("mcp", _mcp(10, 5, 1), _coverage(20), outcomes)
    assert _ids(items) == ["mcp_call_errors", "mcp_rarely_called", "outcomes_higher"]
    assert [i["severity"] for i in items] == ["attention", "info", "info"]


def test_no_item_claims_causation_or_disuse():
    forbidden = re.compile(r"\b(caus\w*|because|due to|led to|unused|never used)\b", re.I)
    outcomes = _outcomes(_side(6, 14), _side(20, 10, 30), "lower")
    cases = [
        attention_items("mcp", _mcp(10, 5, 1), _coverage(20, "no_observed_calls"), outcomes),
        attention_items("skill", _skill(), _coverage(6, "no_observed_skill_use"), outcomes),
        attention_items(
            "hook",
            _hook(failures=9, output=1, recorded_sessions=0),
            _coverage(headless_sessions=5, version_unverified_sessions=2),
            outcomes,
        ),
    ]
    for item in (i for items in cases for i in items):
        text = item["title"] + " " + item["detail"] + " " + (item["suggestion"] or "")
        assert not forbidden.search(_without_disclaimer(text)), item


def test_unknown_component_type_is_an_error():
    with pytest.raises(KeyError):
        attention_items("prompt", {}, _coverage(), None)
