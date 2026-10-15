# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Billing modes and per-response token de-duplication."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from services import usage_dedup
from services.cost import billing

# ── Billing ──────────────────────────────────────────────────────────────


def test_cloud_endpoints_are_detected_as_metered():
    mode, source, _ = billing.classify(
        "pi", {"eu.anthropic.claude-opus-4-6-v1", "global.anthropic.claude-sonnet-4-6"}, 0, {}
    )
    assert (mode, source) == (billing.MODE_METERED, billing.SOURCE_DETECTED)


def test_first_party_models_need_configuration():
    mode, source, evidence = billing.classify("claude-code", {"claude-sonnet-4-6"}, 0, {})
    assert (mode, source) == (billing.MODE_UNCONFIGURED, billing.SOURCE_NONE)
    assert "Settings" in evidence


def test_configuration_wins_over_detection():
    cfg = billing.parse('{"pi": {"mode": "subscription", "seats": 1, "seat_usd_month": 20}}')
    assert billing.classify("pi", {"eu.anthropic.claude-opus-4-6-v1"}, 0, cfg)[:2] == ("subscription", "configured")


def test_kiro_overage_is_billed_per_user_beyond_the_plan():
    cfg = billing.parse(
        '{"kiro": {"mode": "subscription", "seats": 2, "seat_usd_month": 20, "plan_credits": 1000, "overage_usd_per_credit": 0.04}}'
    )
    summary = billing.summarize({}, {}, {("kiro", "a"): 1250.0, ("kiro", "b"): 400.0}, cfg)
    [kiro] = summary.harnesses
    assert kiro.subscription_usd == pytest.approx(40)
    assert kiro.overage_usd == pytest.approx(250 * 0.04)
    assert summary.billed_usd == pytest.approx(50)


def test_unconfigured_usage_is_counted_and_flagged():
    summary = billing.summarize({"claude-code": 80.0}, {"claude-code": {"claude-sonnet-4-6"}}, {}, {})
    assert summary.billed_usd == pytest.approx(80)
    assert summary.unclassified_usd == pytest.approx(80)
    assert summary.complete is False


def test_subscription_usage_value_is_not_billed():
    cfg = billing.parse('{"claude-code": {"mode": "subscription", "seats": 3, "seat_usd_month": 100}}')
    summary = billing.summarize({"claude-code": 500.0}, {"claude-code": {"claude-opus-4-7"}}, {}, cfg)
    assert summary.billed_usd == pytest.approx(300)
    assert summary.usage_value_usd == pytest.approx(500)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ('{"pi": {"mode": "free"}}', "metered"),
        ('{"pi": {"mode": "metered", "seats": -1}}', "non-negative"),
        ('{"pi": {"mode": "metered", "price": 1}}', "Unknown billing field"),
    ],
)
def test_billing_validation(raw, message):
    with pytest.raises(ValueError, match=message):
        billing.validate(raw)


# ── Usage de-duplication ─────────────────────────────────────────────────


def _cc_line(message_id: str, request_id: str, output: int, *, cache_read=1000, one_hour=0) -> dict:
    return {
        "type": "assistant",
        "requestId": request_id,
        "message": {
            "id": message_id,
            "usage": {
                "input_tokens": 5,
                "output_tokens": output,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": 200,
                "cache_creation": {"ephemeral_1h_input_tokens": one_hour, "ephemeral_5m_input_tokens": 200 - one_hour},
            },
        },
    }


def _row(offset: int, parsed: dict, harness: str = "claude-code") -> dict:
    usage = parsed["message"]["usage"]
    return {
        "line_offset": offset,
        "usage_key": usage_dedup.usage_key(parsed, harness),
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "cache_read_tokens": usage["cache_read_input_tokens"],
        "cache_write_tokens": usage["cache_creation_input_tokens"],
        "cache_write_1h_tokens": usage_dedup.cache_write_1h(parsed, harness),
        "raw_line": json.dumps(parsed),
    }


def test_usage_key_and_one_hour_extraction():
    parsed = _cc_line("msg_1", "req_1", 10, one_hour=150)
    assert usage_dedup.usage_key(parsed, "claude-code") == "msg_1:req_1"
    assert usage_dedup.cache_write_1h(parsed, "claude-code") == 150
    assert usage_dedup.usage_key(parsed, "pi") == ""
    assert usage_dedup.usage_key({"message": {}}, "claude-code") == usage_dedup.NO_KEY
    assert usage_dedup.cache_write_1h({"message": {"usage": {"cacheWrite1h": 70}}}, "pi") == 70


def test_one_row_per_response_keeps_the_largest_output():
    rows = [
        _row(10, _cc_line("msg_1", "req_1", 3)),  # thinking block, partial output
        _row(11, _cc_line("msg_1", "req_1", 412)),  # final block, full output
        _row(12, _cc_line("msg_1", "req_1", 412)),  # tool_use block, same usage
        _row(13, _cc_line("msg_2", "req_2", 50)),
    ]
    out, superseded = usage_dedup.dedupe_rows(rows)
    kept = {r["line_offset"]: r["output_tokens"] for r in out if r["output_tokens"] or r["cache_read_tokens"]}
    assert kept == {11: 412, 13: 50}
    assert sum(r["cache_read_tokens"] for r in out) == 2000  # once per response, not four times
    assert superseded == []


def test_a_later_batch_with_the_final_count_supersedes_a_stored_partial_row():
    stored = [_row(10, _cc_line("msg_1", "req_1", 3))]
    batch = [_row(11, _cc_line("msg_1", "req_1", 412))]
    out, superseded = usage_dedup.dedupe_rows(batch, stored)
    assert out[0]["output_tokens"] == 412
    assert [r["line_offset"] for r in superseded] == [10]
    assert superseded[0]["cache_read_tokens"] == 0 and superseded[0]["output_tokens"] == 0


def test_a_later_partial_line_does_not_replace_the_stored_final_row():
    stored = [_row(11, _cc_line("msg_1", "req_1", 412))]
    batch = [_row(12, _cc_line("msg_1", "req_1", 412))]
    out, superseded = usage_dedup.dedupe_rows(batch, stored)
    assert out[0]["output_tokens"] == 0  # stored row already counts this response
    assert superseded == []


def test_rows_without_a_response_id_are_never_merged():
    rows = [
        {"line_offset": 1, "usage_key": usage_dedup.NO_KEY, "output_tokens": 5, "cache_read_tokens": 10},
        {"line_offset": 2, "usage_key": usage_dedup.NO_KEY, "output_tokens": 5, "cache_read_tokens": 10},
    ]
    out, _ = usage_dedup.dedupe_rows(rows)
    assert [r["output_tokens"] for r in out] == [5, 5]


@pytest.mark.asyncio
async def test_backfill_keys_and_dedupes_one_session(monkeypatch):
    stored = [
        {**_row(10, _cc_line("msg_1", "req_1", 3, one_hour=200)), "usage_key": ""},
        {**_row(11, _cc_line("msg_1", "req_1", 412, one_hour=200)), "usage_key": "", "cache_write_1h_tokens": 0},
    ]
    monkeypatch.setattr(usage_dedup, "_rows", AsyncMock(return_value=stored))
    import services.clickhouse as ch

    inserted = AsyncMock()
    refreshed = AsyncMock()
    monkeypatch.setattr(ch, "insert_session_events", inserted)
    monkeypatch.setattr(ch, "refresh_session_summary", refreshed)

    rewritten = await usage_dedup.backfill_session("s1", "default", "u1", "claude-code")

    assert rewritten == 2
    rows = {r["line_offset"]: r for r in inserted.await_args.args[0]}
    assert rows[10]["output_tokens"] == 0 and rows[10]["usage_key"] == "msg_1:req_1"
    assert rows[11]["output_tokens"] == 412 and rows[11]["cache_write_1h_tokens"] == 200
    refreshed.assert_awaited_once()


@pytest.mark.asyncio
async def test_ingest_batch_dedupes_only_keyed_harnesses(monkeypatch):
    lookup = AsyncMock(return_value=[])
    monkeypatch.setattr(usage_dedup, "existing_keyed_rows", lookup)
    pi_rows = [{"line_offset": 1, "usage_key": "", "output_tokens": 5}]
    out, superseded = await usage_dedup.dedupe_batch(pi_rows, session_id="s", project_id="p", user_id="u", harness="pi")
    assert out == pi_rows and superseded == []
    lookup.assert_not_awaited()


def test_clickhouse_array_parameter_is_single_quoted_and_escaped():
    # ClickHouse rejects JSON (double-quoted) arrays for Array(String) parameters.
    assert usage_dedup.ch_string_array(["msg_A:r-1", "it's", "back\\slash"]) == "['msg_A:r-1','it\\'s','back\\\\slash']"
