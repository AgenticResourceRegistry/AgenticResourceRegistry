# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regenerate the cost price book from Langfuse's audited model price list.

Langfuse (MIT, https://github.com/langfuse/langfuse) maintains
``worker/src/constants/default-model-prices.json`` and audits it daily against
provider pricing pages. We keep a compact derivative: one entry per model with
the default-tier USD price per token for the four buckets Observal records.

Usage:
    python scripts/update_model_prices.py            # fetch latest main
    python scripts/update_model_prices.py --ref <sha> # pin a commit

The output is deterministic (sorted, no timestamps of our own) so a diff shows
exactly which prices changed.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

SOURCE = "https://raw.githubusercontent.com/langfuse/langfuse/{ref}/worker/src/constants/default-model-prices.json"
OUT = Path(__file__).resolve().parents[1] / "observal-server" / "services" / "cost" / "data" / "model_prices.json"

# Langfuse uses several key spellings for the same bucket depending on provider.
_BUCKET_KEYS = {
    "input": ("input", "input_tokens", "prompt_tokens"),
    "output": ("output", "output_tokens", "completion_tokens"),
    "cache_read": ("cache_read_input_tokens", "input_cache_read", "input_cached_tokens", "cached_tokens"),
    "cache_write_1h": ("input_cache_creation_1h",),
    "cache_write": (
        "cache_creation_input_tokens",
        "input_cache_creation",
        "input_cache_creation_5m",
        "cache_write_tokens",
        "input_cache_write_tokens",
    ),
}


def _bucket(prices: dict, bucket: str) -> float | None:
    for key in _BUCKET_KEYS[bucket]:
        value = prices.get(key)
        if isinstance(value, int | float):
            return float(value)
    return None


def compact(raw: list[dict], ref: str) -> dict:
    models = []
    for entry in raw:
        tiers = entry.get("pricingTiers") or []
        default = next((t for t in tiers if t.get("isDefault")), None)
        prices = (default or {}).get("prices") or entry.get("prices") or {}
        input_price = _bucket(prices, "input")
        output_price = _bucket(prices, "output")
        if input_price is None and output_price is None:
            continue
        cache_read = _bucket(prices, "cache_read")
        cache_write = _bucket(prices, "cache_write")
        cache_write_1h = _bucket(prices, "cache_write_1h")
        # Missing cache prices fall back to the plain input price: that is what a
        # provider charges when it does not discount cached input. Providers with
        # no separate one-hour rate bill one-hour writes at the normal write price.
        write = cache_write if cache_write is not None else input_price or 0.0
        models.append(
            {
                "model": entry["modelName"],
                "pattern": entry["matchPattern"],
                "input": input_price or 0.0,
                "output": output_price or 0.0,
                "cache_read": cache_read if cache_read is not None else input_price or 0.0,
                "cache_write": write,
                "cache_write_1h": cache_write_1h if cache_write_1h is not None else write,
                "updated_at": entry.get("updatedAt", ""),
            }
        )
    models.sort(key=lambda m: m["model"])
    return {
        "source": "langfuse/langfuse worker/src/constants/default-model-prices.json",
        "source_ref": ref,
        "license": "MIT",
        "unit": "USD per token, default pricing tier",
        "models": models,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="main")
    args = parser.parse_args()
    with urllib.request.urlopen(SOURCE.format(ref=args.ref), timeout=30) as resp:
        raw = json.load(resp)
    data = compact(raw, args.ref)
    OUT.write_text(json.dumps(data, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    print(f"wrote {len(data['models'])} models to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
