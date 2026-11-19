# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import AsyncMock

import pytest

from services.cost import config as cost_config


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("cost.currency", "EURO", "three-letter ISO 4217 code"),
        ("cost.exchange_rates", "{bad", "Not valid JSON"),
        ("cost.exchange_rates", '{"rates": {"EUR": -1}}', "positive number"),
        ("cost.exchange_rates", '{"rates": {"EUR": true}}', "positive number"),
        ("cost.exchange_rates", '{"rates": {"USD": 1}}', "Don't list USD"),
        ("cost.exchange_rates", '{"as_of": "yesterday", "rates": {"EUR": 0.9}}', "Invalid isoformat"),
        ("cost.exchange_rates", '{"EUR": 0.92}', "Exchange rates must look like"),
        ("cost.price_overrides", '{"gpt-5": {"inputs": 1}}', "may only set"),
        ("cost.price_overrides", '{"gpt-5": {"input": -1}}', "non-negative"),
        ("cost.price_overrides", '{"^(": {"input": 1}}', "Invalid pattern"),
        ("cost.price_overrides", "[1, 2]", "JSON object"),
        ("cost.timezone", "Mars/Olympus", "Unknown timezone"),
    ],
)
def test_validate_rejects_bad_values(key, value, message):
    with pytest.raises(ValueError, match=message):
        cost_config.validate(key, value)


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("cost.currency", " inr ", "INR"),
        ("cost.currency", "", "USD"),
        ("cost.exchange_rates", '{"as_of": "2026-11-01", "rates": {"eur": 0.92}}', None),
        ("cost.exchange_rates", "", None),
        ("cost.price_overrides", '{"claude-opus-5-5": {"input": 3.6}, "^eu\\\\.": {"output": 1}}', None),
        ("cost.timezone", "Asia/Kolkata", None),
    ],
)
def test_validate_accepts_good_values(key, value, expected):
    assert cost_config.validate(key, value) == (expected if expected is not None else value)


def _settings(monkeypatch, values: dict[str, str]):
    async def fake_get(key, default=None):
        return values.get(key, default)

    monkeypatch.setattr(cost_config.ds, "get", AsyncMock(side_effect=fake_get))


@pytest.mark.asyncio
async def test_load_reads_every_setting(monkeypatch):
    _settings(
        monkeypatch,
        {
            "cost.currency": "eur",
            "cost.exchange_rates": json.dumps({"as_of": "2026-11-01", "rates": {"EUR": 0.92, "INR": 84.1}}),
            "cost.price_overrides": json.dumps({"gpt-5": {"input": 1.0}}),
            "cost.timezone": "Europe/London",
        },
    )

    loaded = await cost_config.load()

    assert loaded.currency == "EUR"
    assert loaded.exchange_rates == {"EUR": 0.92, "INR": 84.1}
    assert loaded.rates_as_of == "2026-11-01"
    assert loaded.timezone.key == "Europe/London"
    assert loaded.price_overrides == {"gpt-5": {"input": 1.0}}


@pytest.mark.asyncio
async def test_load_falls_back_on_bad_stored_values(monkeypatch):
    _settings(
        monkeypatch,
        {"cost.exchange_rates": "{broken", "cost.timezone": "Nowhere/City", "cost.price_overrides": "[]"},
    )

    loaded = await cost_config.load()

    assert loaded.exchange_rates == {}
    assert loaded.timezone.key == "UTC"
    assert loaded.price_overrides == {}


@pytest.mark.asyncio
async def test_default_currency_without_a_rate_falls_back_to_usd(monkeypatch):
    _settings(monkeypatch, {"cost.currency": "JPY", "cost.exchange_rates": json.dumps({"rates": {"EUR": 0.9}})})

    assert (await cost_config.load()).currency == "USD"
