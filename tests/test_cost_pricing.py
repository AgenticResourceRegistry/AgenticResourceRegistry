# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import pytest

from services.cost.pricing import Prices, Usage, build_price_book, list_prices, normalize_model


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("us.anthropic.claude-sonnet-4-5-v1:0", "claude-sonnet-4-5"),
        ("anthropic.claude-sonnet-4-5-20250929", "claude-sonnet-4-5"),
        ("claude-sonnet-4-5@20250929", "claude-sonnet-4-5"),
        ("anthropic/claude-sonnet-4.5", "claude-sonnet-4-5"),
        ("claude-opus-4-6[1m]", "claude-opus-4-6"),
        ("gpt-5.4-mini-2026-03-17", "gpt-5.4-mini"),
        ("models/gemini-2.5-pro", "gemini-2.5-pro"),
        ("vertex_ai/gemini-2.5-flash", "gemini-2.5-flash"),
        ("  Claude-Sonnet-5-5  ", "claude-sonnet-5-5"),
        ("", ""),
    ],
)
def test_normalize_model(raw, expected):
    assert normalize_model(raw) == expected


def test_every_listed_model_has_four_prices_and_a_provider():
    prices = list_prices()
    assert prices
    for name, entry in prices.items():
        assert entry.provider in {"anthropic", "openai", "google"}, name
        assert min(entry.input, entry.output, entry.cache_read, entry.cache_write) >= 0, name
        if entry.route_to:
            assert entry.route_to in prices, name


def test_price_for_matches_exact_names_and_longest_prefix_at_a_dash():
    book = build_price_book({})
    assert book.price_for("claude-sonnet-5-5").label == "Sonnet 5.5"
    assert book.price_for("gemini-3.1-pro-preview-05-06").label == "Gemini 3.1 Pro"
    assert book.price_for("gpt-5.4-mini").label == "GPT-5.4 mini"
    assert book.price_for("gpt-5.9") is None  # no dash boundary after "gpt-5"
    assert book.price_for("some-unknown-model") is None


def test_exact_override_replaces_only_the_given_prices():
    book = build_price_book({"claude-opus-5-5": {"input": 3.6, "output": 18}})
    prices = book.price_for("claude-opus-5-5@20260101")
    assert (prices.input, prices.output, prices.cache_read, prices.cache_write) == (3.6, 18, 0.2, 5.0)
    assert prices.label == "Opus 5.5"


def test_regex_override_matches_the_raw_model_name():
    override = {"input": 4.4, "output": 22, "cache_read": 0.22, "cache_write": 5.5}
    book = build_price_book({r"^eu\.anthropic\.": override})
    assert book.price_for("eu.anthropic.claude-sonnet-5-5").input == 4.4
    assert book.price_for("claude-sonnet-5-5").input == 2.0


def test_override_for_an_unlisted_model_needs_all_four_prices():
    partial = build_price_book({"in-house-model": {"input": 1.0}})
    assert partial.price_for("in-house-model") is None

    full = build_price_book({"in-house-model": {"input": 1, "output": 2, "cache_read": 0.1, "cache_write": 1}})
    assert full.price_for("in-house-model").output == 2


def test_cost_is_priced_per_token_type():
    sonnet = build_price_book({}).price_for("claude-sonnet-5-5")
    assert sonnet.cost(Usage(output=1_000_000)) == pytest.approx(10.0)
    usage = Usage(input=410_000_000, output=96_000_000, cache_read=6_100_000_000, cache_write=380_000_000)
    assert sonnet.cost_by_type(usage) == pytest.approx(
        {"input": 820.0, "output": 960.0, "cache_read": 1220.0, "cache_write": 950.0}
    )
    assert sonnet.cost(usage) == pytest.approx(3950.0)


def test_usage_cache_hit_is_share_of_input_side_served_from_cache():
    assert Usage(input=10, cache_read=80, cache_write=10).cache_hit == pytest.approx(0.8)
    assert Usage(output=5).cache_hit is None
    assert (Usage(input=1, output=2) + Usage(cache_read=3, cache_write=4)).total == 10


def test_label_falls_back_to_the_normalized_name():
    book = build_price_book({})
    assert book.label_for("models/some-new-model") == "some-new-model"
    assert book.label_for("") == "Unknown model"
    assert isinstance(book.price_for("gpt-5"), Prices)
