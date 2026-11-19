# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Model prices and cost arithmetic for Cost Intelligence."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path

_DATA = Path(__file__).parent / "data" / "model_prices.json"
_PER_MILLION = 1_000_000
PRICE_FIELDS = ("input", "output", "cache_read", "cache_write")

# Provider and region prefixes: "us.anthropic.", "bedrock/", "vertex_ai/", "openai/", "models/" ...
_PREFIX = re.compile(
    r"^(?:(?:us|eu|apac|global)\.)?(?:anthropic|openai|google|bedrock|vertex_ai|vertex|azure|models)[./]"
)
# Trailing variants: "[1m]", "@20250929", "-v1:0", "-20250929", "-2026-03-17", "-latest"
_SUFFIX = re.compile(r"(?:\[[^\]]*\]|@[\w.-]+|-v\d+(?::\d+)?|-\d{8}|-\d{4}-\d{2}-\d{2}|-latest)$")


@dataclass(frozen=True, slots=True)
class Usage:
    """Token counts by type."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input + other.input,
            self.output + other.output,
            self.cache_read + other.cache_read,
            self.cache_write + other.cache_write,
        )

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_write

    @property
    def input_side(self) -> int:
        return self.input + self.cache_read + self.cache_write

    @property
    def cache_hit(self) -> float | None:
        return self.cache_read / self.input_side if self.input_side else None


@dataclass(frozen=True, slots=True)
class Prices:
    """USD per 1M tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float
    label: str = ""
    provider: str = ""
    route_to: str | None = None

    def cost_by_type(self, usage: Usage) -> dict[str, float]:
        return {field: getattr(usage, field) * getattr(self, field) / _PER_MILLION for field in PRICE_FIELDS}

    def cost(self, usage: Usage) -> float:
        return sum(self.cost_by_type(usage).values())


def normalize_model(raw: str) -> str:
    """Map the many spellings harnesses report to one price-table name."""
    name = (raw or "").strip().lower()
    while (stripped := _PREFIX.sub("", name)) != name:
        name = stripped
    while (stripped := _SUFFIX.sub("", name)) != name:
        name = stripped
    if name.startswith("claude-"):
        name = name.replace(".", "-")  # "claude-sonnet-4.5" (OpenRouter) -> "claude-sonnet-4-5"
    return name


@cache
def list_prices() -> dict[str, Prices]:
    data = json.loads(_DATA.read_text())
    return {
        name: Prices(
            **{field: float(entry[field]) for field in PRICE_FIELDS},
            label=entry.get("label", name),
            provider=entry["provider"],
            route_to=entry.get("route_to"),
        )
        for name, entry in data["models"].items()
    }


@dataclass(frozen=True, slots=True)
class PriceBook:
    models: dict[str, Prices]
    exact_overrides: dict[str, dict[str, float]]
    regex_overrides: tuple[tuple[re.Pattern[str], dict[str, float]], ...]

    def price_for(self, raw_model: str) -> Prices | None:
        """Override, then exact list price, then longest list prefix. None means unpriced."""
        name = normalize_model(raw_model)
        base = self.models.get(name) or self._longest_prefix(name)
        override = self.exact_overrides.get(name)
        if override is None:
            override = next((values for pattern, values in self.regex_overrides if pattern.search(raw_model)), None)
        if override is None:
            return base
        if base is None:
            if not all(field in override for field in PRICE_FIELDS):
                return None
            return Prices(**{field: override[field] for field in PRICE_FIELDS}, label=name)
        return replace(base, **{field: override[field] for field in PRICE_FIELDS if field in override})

    def label_for(self, raw_model: str) -> str:
        prices = self.price_for(raw_model)
        if prices and prices.label:
            return prices.label
        return normalize_model(raw_model) or "Unknown model"

    def _longest_prefix(self, name: str) -> Prices | None:
        # Only match at a "-" boundary, so "gpt-5" never prices "gpt-5.9".
        matches = [entry for entry in self.models if name.startswith(entry + "-")]
        return self.models[max(matches, key=len)] if matches else None


def build_price_book(overrides: dict[str, dict[str, float]]) -> PriceBook:
    exact = {normalize_model(name): values for name, values in overrides.items() if not name.startswith("^")}
    regex = tuple(
        (re.compile(pattern, re.IGNORECASE), values) for pattern, values in overrides.items() if pattern.startswith("^")
    )
    return PriceBook(list_prices(), exact, regex)
