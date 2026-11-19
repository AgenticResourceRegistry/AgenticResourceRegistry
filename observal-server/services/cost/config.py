# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Read and validate the cost.* settings."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger as optic

import services.dynamic_settings as ds
from services.cost.pricing import PRICE_FIELDS

if TYPE_CHECKING:
    from collections.abc import Callable

_CURRENCY = re.compile(r"^[A-Z]{3}$")
_EXAMPLE_RATES = '{"as_of": "2026-11-01", "rates": {"EUR": 0.92}}'


@dataclass(frozen=True, slots=True)
class CostSettings:
    currency: str = "USD"
    exchange_rates: dict[str, float] = field(default_factory=dict)
    rates_as_of: str | None = None
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo("UTC"))
    price_overrides: dict[str, dict[str, float]] = field(default_factory=dict)


def _currency(value: str) -> str:
    code = value.strip().upper() or "USD"
    if not _CURRENCY.match(code):
        raise ValueError("Currency must be a three-letter ISO 4217 code, for example EUR")
    return code


def _exchange_rates(value: str) -> tuple[dict[str, float], str | None]:
    if not value.strip():
        return {}, None
    data = json.loads(value)
    if not isinstance(data, dict) or not isinstance(data.get("rates"), dict):
        raise ValueError(f"Exchange rates must look like {_EXAMPLE_RATES}")
    as_of = data.get("as_of")
    if as_of is not None:
        if not isinstance(as_of, str):
            raise ValueError("as_of must be a date such as 2026-11-01")
        date.fromisoformat(as_of)
    rates: dict[str, float] = {}
    for raw_code, rate in data["rates"].items():
        code = _currency(raw_code)
        if code == "USD":
            raise ValueError("Don't list USD: amounts are stored in USD, so its rate is always 1")
        if isinstance(rate, bool) or not isinstance(rate, int | float) or rate <= 0:
            raise ValueError(f"Rate for {code} must be a positive number of {code} per 1 USD")
        rates[code] = float(rate)
    return rates, as_of


def _overrides(value: str) -> dict[str, dict[str, float]]:
    if not value.strip():
        return {}
    data = json.loads(value)
    if not isinstance(data, dict):
        raise ValueError("Price overrides must be a JSON object keyed by model name or ^regex")
    result: dict[str, dict[str, float]] = {}
    for name, prices in data.items():
        if name.startswith("^"):
            try:
                re.compile(name)
            except re.error as exc:
                raise ValueError(f"Invalid pattern {name}: {exc}") from exc
        if not isinstance(prices, dict) or not prices or set(prices) - set(PRICE_FIELDS):
            raise ValueError(f"Override for {name} may only set {', '.join(PRICE_FIELDS)}")
        for amount in prices.values():
            if isinstance(amount, bool) or not isinstance(amount, int | float) or amount < 0:
                raise ValueError(f"Prices for {name} must be non-negative numbers (USD per 1M tokens)")
        result[name] = {field_name: float(amount) for field_name, amount in prices.items()}
    return result


def _timezone(value: str) -> ZoneInfo:
    name = value.strip() or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown timezone {name!r}; use an IANA name such as Europe/London") from exc


def validate(key: str, value: str) -> str:
    """Validate a cost.* value before it is saved. Raises ValueError with a message for the admin."""
    try:
        if key == "cost.currency":
            return _currency(value)
        if key == "cost.exchange_rates":
            _exchange_rates(value)
        elif key == "cost.price_overrides":
            _overrides(value)
        elif key == "cost.timezone":
            _timezone(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {exc.msg}") from exc
    return value


async def _read(key: str, parse: Callable, fallback):
    raw = await ds.get(key, "")
    try:
        return parse(raw)
    except (ValueError, json.JSONDecodeError):
        optic.warning("invalid cost setting ignored: key={}", key)
        return fallback


async def load() -> CostSettings:
    """Read the cost settings. A bad stored value falls back to its default instead of breaking the dashboard."""
    defaults = CostSettings()
    currency = await _read("cost.currency", _currency, defaults.currency)
    exchange_rates, rates_as_of = await _read("cost.exchange_rates", _exchange_rates, ({}, None))
    timezone = await _read("cost.timezone", _timezone, defaults.timezone)
    overrides = await _read("cost.price_overrides", _overrides, {})
    if currency != "USD" and currency not in exchange_rates:
        currency = "USD"  # a default currency without a rate can't be displayed
    return CostSettings(currency, exchange_rates, rates_as_of, timezone, overrides)
