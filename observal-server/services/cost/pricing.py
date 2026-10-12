# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Price book: turn recorded token buckets into US dollars.

Observal records four token buckets per model call (input, output, cache read,
cache write) and, for Kiro, provider credits. This module prices them:

1. Admin overrides (``cost.price_overrides``) win, for negotiated rates or
   models the public list does not know yet.
2. Otherwise the vendored list price (``data/model_prices.json``, derived from
   Langfuse's audited price list) applies.
3. ``cost.price_multipliers`` then scales a raw model name, e.g. a regional
   Bedrock premium.
4. Credits are priced only with an explicit ``cost.credit_usd`` rate per
   harness. Without one they stay unpriced: credits are not currency.

Anything that cannot be priced is reported as unpriced, never as zero cost.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from loguru import logger as optic

_DATA = Path(__file__).parent / "data" / "model_prices.json"

PRICE_SOURCE_LIST = "list_price"
PRICE_SOURCE_OVERRIDE = "override"
PRICE_SOURCE_CREDIT = "credit_rate"


@dataclass(frozen=True, slots=True)
class TokenPrices:
    """USD per token for each bucket."""

    model: str
    input: float
    output: float
    cache_read: float
    cache_write: float
    source: str

    def scaled(self, factor: float) -> TokenPrices:
        return TokenPrices(
            model=self.model,
            input=self.input * factor,
            output=self.output * factor,
            cache_read=self.cache_read * factor,
            cache_write=self.cache_write * factor,
            source=self.source,
        )


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0

    @property
    def total(self) -> float:
        return self.input + self.output + self.cache_read + self.cache_write

    def cost(self, prices: TokenPrices) -> float:
        return (
            self.input * prices.input
            + self.output * prices.output
            + self.cache_read * prices.cache_read
            + self.cache_write * prices.cache_write
        )

    def cache_savings(self, prices: TokenPrices) -> float:
        """What cached reads saved compared with paying the full input price."""
        return self.cache_read * max(prices.input - prices.cache_read, 0.0)


@dataclass(frozen=True, slots=True)
class _ListEntry:
    model: str
    pattern: re.Pattern[str]
    prices: TokenPrices


@lru_cache(maxsize=1)
def _list_entries() -> tuple[_ListEntry, ...]:
    data = json.loads(_DATA.read_text(encoding="utf-8"))
    entries = []
    for item in data["models"]:
        try:
            pattern = re.compile(item["pattern"])
        except re.error:
            optic.warning("price book pattern does not compile model={}", item["model"])
            continue
        entries.append(
            _ListEntry(
                model=item["model"],
                pattern=pattern,
                prices=TokenPrices(
                    model=item["model"],
                    input=float(item["input"]),
                    output=float(item["output"]),
                    cache_read=float(item["cache_read"]),
                    cache_write=float(item["cache_write"]),
                    source=PRICE_SOURCE_LIST,
                ),
            )
        )
    return tuple(entries)


def price_book_ref() -> str:
    return json.loads(_DATA.read_text(encoding="utf-8")).get("source_ref", "")


_DICT_MODEL_RE = re.compile(r"""['"]modelID['"]\s*:\s*['"]([^'"]+)['"]""")
_PROVIDER_PREFIX_RE = re.compile(r"^(amazon-bedrock|bedrock|vertex_ai|vertex|anthropic|openai|azure)/", re.I)
_BEDROCK_RE = re.compile(r"^(?:(?:eu|us|apac|au|jp|global)\.)?anthropic\.(.+?)(?:-v\d+(?::\d+)?)?$", re.I)


def normalize_model(raw: str | None) -> str:
    """Canonical model name for display and grouping.

    Harnesses report the same model several ways: Bedrock inference profiles
    (``eu.anthropic.claude-opus-4-6-v1``), provider prefixes
    (``amazon-bedrock/anthropic.claude-sonnet-4-6``), Vertex suffixes
    (``claude-sonnet-4-5@20250929``) and OpenCode's serialized dict.
    """
    model = (raw or "").strip()
    if not model:
        return ""
    match = _DICT_MODEL_RE.search(model)
    if match:
        model = match.group(1)
    model = _PROVIDER_PREFIX_RE.sub("", model)
    bedrock = _BEDROCK_RE.match(model)
    if bedrock:
        model = bedrock.group(1)
    model = model.split("@", 1)[0]
    return model


_FAMILY_RE = re.compile(r"^claude-(opus|sonnet|haiku|fable)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$", re.I)


def display_model(raw: str | None) -> str:
    """Human label for a model id: ``eu.anthropic.claude-opus-4-6-v1`` -> ``Claude Opus 4.6``."""
    model = normalize_model(raw)
    match = _FAMILY_RE.match(model)
    if match:
        family, major, minor = match.groups()
        return f"Claude {family.capitalize()} {major}{'.' + minor if minor else ''}"
    if model.lower().startswith("gpt-"):
        return "GPT-" + model[4:]
    return model or "Unknown model"


@dataclass(frozen=True, slots=True)
class PricingConfig:
    """Admin-controlled pricing inputs, parsed from dynamic settings."""

    overrides: tuple[tuple[re.Pattern[str], TokenPrices], ...] = ()
    multipliers: tuple[tuple[re.Pattern[str], float], ...] = ()
    credit_usd: dict[str, float] | None = None

    @classmethod
    def parse(cls, overrides_json: str, multipliers_json: str, credit_json: str) -> PricingConfig:
        return cls(
            overrides=tuple(_parse_overrides(overrides_json)),
            multipliers=tuple(_parse_multipliers(multipliers_json)),
            credit_usd=_parse_credit_rates(credit_json),
        )

    @property
    def is_customized(self) -> bool:
        return bool(self.overrides or self.multipliers)


def _load_json(raw: str, key: str):
    if not raw or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        optic.warning("cost setting is not valid JSON key={}", key)
        return None


def _parse_overrides(raw: str) -> list[tuple[re.Pattern[str], TokenPrices]]:
    """``{"model or regex": {"input": 3, "output": 15, ...}}`` in USD per 1M tokens."""
    data = _load_json(raw, "cost.price_overrides")
    if not isinstance(data, dict):
        return []
    out = []
    for key, prices in data.items():
        if not isinstance(prices, dict):
            continue
        try:
            pattern = re.compile(key if key.startswith("^") else f"^{re.escape(key)}$", re.I)
            per_m = {
                b: float(prices.get(b, prices.get("input", 0)) or 0) for b in ("input", "cache_read", "cache_write")
            }
            per_m["output"] = float(prices.get("output", 0) or 0)
        except (re.error, TypeError, ValueError):
            optic.warning("ignoring invalid price override key={}", key)
            continue
        out.append(
            (
                pattern,
                TokenPrices(
                    model=key,
                    input=per_m["input"] / 1e6,
                    output=per_m["output"] / 1e6,
                    cache_read=per_m["cache_read"] / 1e6,
                    cache_write=per_m["cache_write"] / 1e6,
                    source=PRICE_SOURCE_OVERRIDE,
                ),
            )
        )
    return out


def _parse_multipliers(raw: str) -> list[tuple[re.Pattern[str], float]]:
    data = _load_json(raw, "cost.price_multipliers")
    if not isinstance(data, dict):
        return []
    out = []
    for key, factor in data.items():
        try:
            value = float(factor)
            if value <= 0:
                raise ValueError("multiplier must be positive")
            out.append((re.compile(key, re.I), value))
        except (re.error, TypeError, ValueError):
            optic.warning("ignoring invalid price multiplier key={}", key)
    return out


def _parse_credit_rates(raw: str) -> dict[str, float] | None:
    data = _load_json(raw, "cost.credit_usd")
    if not isinstance(data, dict):
        return None
    rates = {}
    for harness, rate in data.items():
        try:
            value = float(rate)
        except (TypeError, ValueError):
            continue
        if value > 0:
            rates[str(harness)] = value
    return rates or None


class PriceBook:
    def __init__(self, config: PricingConfig | None = None) -> None:
        self.config = config or PricingConfig()
        self._cache: dict[str, TokenPrices | None] = {}

    def prices_for(self, raw_model: str | None) -> TokenPrices | None:
        raw = (raw_model or "").strip()
        if raw in self._cache:
            return self._cache[raw]
        prices = self._resolve(raw)
        self._cache[raw] = prices
        return prices

    def _resolve(self, raw: str) -> TokenPrices | None:
        if not raw or raw.startswith("<"):  # "<synthetic>" and similar placeholders carry no billable usage
            return None
        normalized = normalize_model(raw)
        candidates = [c for c in (raw, normalized) if c]
        prices: TokenPrices | None = None
        for pattern, override in self.config.overrides:
            if any(pattern.search(c) for c in candidates):
                prices = override
                break
        if prices is None:
            for entry in _list_entries():
                if any(entry.pattern.search(c) for c in candidates):
                    prices = entry.prices
                    break
        if prices is None:
            return None
        for pattern, factor in self.config.multipliers:
            if pattern.search(raw):
                prices = prices.scaled(factor)
        return prices

    def credit_rate(self, harness: str) -> float | None:
        if not self.config.credit_usd:
            return None
        return self.config.credit_usd.get(harness)


async def load_price_book() -> PriceBook:
    import services.dynamic_settings as ds

    config = PricingConfig.parse(
        await ds.get("cost.price_overrides", ""),
        await ds.get("cost.price_multipliers", ""),
        await ds.get("cost.credit_usd", ""),
    )
    return PriceBook(config)


def validate_setting(key: str, value: str) -> str:
    """Validate a ``cost.*`` setting on write. Raises ValueError with a user-facing message."""
    if not value:
        return value
    if key == "cost.opportunity_min_monthly_usd":
        try:
            amount = float(value)
        except ValueError:
            raise ValueError("Minimum monthly savings must be a number") from None
        if amount < 0:
            raise ValueError("Minimum monthly savings cannot be negative")
        return value
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        raise ValueError(f"{key} must be a JSON object") from None
    if not isinstance(data, dict):
        raise ValueError(f"{key} must be a JSON object")
    if key == "cost.price_overrides":
        for model, prices in data.items():
            if not isinstance(prices, dict) or not prices:
                raise ValueError(f"Override for {model} must be an object of per-1M-token prices")
            for bucket, price in prices.items():
                if bucket not in ("input", "output", "cache_read", "cache_write"):
                    raise ValueError(f"Unknown price bucket {bucket!r}; use input, output, cache_read, cache_write")
                if not isinstance(price, int | float) or price < 0:
                    raise ValueError(f"Price for {model}.{bucket} must be a non-negative number")
            if model.startswith("^"):
                try:
                    re.compile(model)
                except re.error as exc:
                    raise ValueError(f"Invalid regex {model!r}: {exc}") from None
    elif key == "cost.price_multipliers":
        for pattern, factor in data.items():
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"Invalid regex {pattern!r}: {exc}") from None
            if not isinstance(factor, int | float) or factor <= 0:
                raise ValueError(f"Multiplier for {pattern!r} must be a positive number")
    elif key == "cost.credit_usd":
        for harness, rate in data.items():
            if not isinstance(rate, int | float) or rate <= 0:
                raise ValueError(f"Credit rate for {harness} must be a positive number")
    return value
