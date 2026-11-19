# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import pytest

from services.cost import savings
from services.cost.pricing import Usage, build_price_book
from services.cost.savings import SessionLine

BOOK = build_price_book({})
M = 1_000_000


def _line(session_id: str, model: str, usage: Usage, user_id: str = "u1") -> SessionLine:
    return SessionLine(session_id, user_id, "claude-code", model, usage, BOOK.price_for(model))


# Opus 5.5 costs $24 for 1M input + 1M output; Sonnet 5.5 costs $12, so each such session saves $12.
OPUS_SESSION = Usage(input=M, output=M)


def test_route_down_counts_only_sessions_under_the_prompt_limit():
    lines = [_line("short", "claude-opus-5-5", OPUS_SESSION), _line("long", "claude-opus-5-5", OPUS_SESSION)]
    prompts = {"short": savings.ROUTE_MAX_PROMPTS - 1, "long": savings.ROUTE_MAX_PROMPTS}

    [result] = savings.route_down(lines, prompts, BOOK, days=30)

    assert result.model == "claude-opus-5-5"
    assert result.route_to == "claude-sonnet-5-5"
    assert (result.sessions, result.model_sessions) == (1, 2)
    assert result.cost_usd == pytest.approx(24.0)
    assert result.monthly_usd == pytest.approx(12.0)
    assert result.counted is True


def test_route_down_scales_to_a_month_and_drops_small_estimates():
    lines = [_line("s1", "claude-opus-5-5", OPUS_SESSION)]
    [weekly] = savings.route_down(lines, {"s1": 1}, BOOK, days=7)
    assert weekly.monthly_usd == pytest.approx(12.0 * 30 / 7, abs=0.01)

    assert savings.route_down(lines, {"s1": 1}, BOOK, days=90) == []  # $4/month is below the floor


def test_route_down_ignores_models_without_a_cheaper_sibling():
    lines = [_line("s1", "claude-sonnet-5-5", Usage(input=50 * M, output=50 * M))]
    assert savings.route_down(lines, {"s1": 1}, BOOK, days=30) == []


def _org_with(candidate: Usage) -> dict[str, Usage]:
    # Sonnet dominates the org average at a 90% cache hit rate.
    return {"claude-sonnet-5-5": Usage(input=100 * M, cache_read=900 * M), "gemini-3.1-pro-preview": candidate}


def test_caching_needs_a_ten_point_gap_to_the_org_rate():
    near = savings.caching(_org_with(Usage(input=20 * M, cache_read=80 * M)), BOOK, days=30)
    assert near == []  # 80% vs about 89%: under ten points

    [far] = savings.caching(_org_with(Usage(input=23 * M, cache_read=77 * M)), BOOK, days=30)
    assert far.model == "gemini-3.1-pro-preview"
    assert far.cache_hit == pytest.approx(0.77)
    assert far.org_cache_hit == pytest.approx(0.888, abs=0.001)
    exact_org_hit = (900 + 77) / 1100  # the response rounds org_cache_hit; the estimate uses the exact rate
    assert far.extra_cached_input == round(exact_org_hit * 100 * M) - 77 * M
    assert far.monthly_usd == pytest.approx(far.extra_cached_input * (2.0 - 0.2) / M, rel=0.001)


def test_caching_ignores_models_with_little_spend():
    assert savings.caching(_org_with(Usage(input=10 * M)), BOOK, days=30) == []  # $20 of spend


def test_caching_never_moves_more_than_the_uncached_input():
    [result] = savings.caching(_org_with(Usage(input=10 * M, cache_write=90 * M)), BOOK, days=30)
    assert result.extra_cached_input == 10 * M


def test_department_medians_use_members_with_sessions():
    medians = savings.department_medians({"a": 1.0, "b": 3.0, "c": 2.0}, {"Eng": ["a", "b", "c", "idle"], "Ops": ["x"]})
    assert medians == {"Eng": 2.0}


def test_review_users_flags_high_cost_regular_users_and_is_never_counted():
    result = savings.review_users(
        sessions={"steady": 40, "new": 19, "heavy": 20, "both": 25},
        per_session={"steady": 2.0, "new": 6.0, "heavy": 3.0, "both": 2.0},
        user_departments={"steady": ["Eng"], "new": ["Eng"], "heavy": ["Eng"], "both": ["Eng", "Ops"]},
        medians={"Eng": 2.0, "Ops": 1.0},
        usernames={"heavy": "h.user", "both": "b.user"},
    )

    assert result.counted is False
    assert result.monthly_usd is None
    assert [(u.username, u.ratio_to_median) for u in result.users] == [("b.user", 2.0), ("h.user", 1.5)]


def test_review_users_returns_none_when_nobody_stands_out():
    assert savings.review_users({"a": 30}, {"a": 1.0}, {"a": ["Eng"]}, {"Eng": 1.0}, {}) is None
