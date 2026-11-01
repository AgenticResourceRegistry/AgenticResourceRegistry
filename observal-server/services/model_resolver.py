# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Resolve the model value a harness config should emit."""

from __future__ import annotations

from observal_shared.agent_model import is_auto_model
from observal_shared.harness_registry import HARNESS_REGISTRY, has_model_selection
from services.harness import ensure_loaded, get_adapter


def _adapter_for(harness: str):
    ensure_loaded()
    adapter = get_adapter(harness)
    if adapter is None:
        raise KeyError(harness)
    return adapter


def _format_for_harness(model: str, provider: str, harness: str) -> str:
    return _adapter_for(harness).format_model(model, provider)


def _candidate_for_harness(harness: str, models_by_harness: dict | None, model_name: str | None) -> str | None:
    if models_by_harness and models_by_harness.get(harness):
        return models_by_harness[harness]
    if is_auto_model(model_name):
        return None  # "auto": the harness picks its own default model.
    return _adapter_for(harness).default_model_candidate(model_name)


def _provider_for(harness: str, model: str) -> str:
    for row_id in HARNESS_REGISTRY[harness].get("supported_models", []):
        if row_id == model:
            if model.startswith("opencode/"):
                return "opencode"
            if model.startswith("gemini"):
                return "google"
            if model.startswith("gpt"):
                return "openai"
            if model.startswith("claude") or model in {"sonnet", "opus", "haiku", "fable", "best", "default"}:
                return "anthropic"
    return "anthropic"


def _supported(harness: str, candidate: str) -> bool:
    rows = HARNESS_REGISTRY[harness].get("supported_models", [])
    if candidate in rows:
        return True
    return any("<" in row and candidate.startswith(row.split("<", 1)[0]) for row in rows)


def resolve_saved_value(harness: str, model_name: str, models_by_harness: dict | None) -> str | None:
    if not has_model_selection(harness):
        return None
    candidate = _candidate_for_harness(harness, models_by_harness, model_name)
    if not candidate:
        return None
    return _format_for_harness(candidate, _provider_for(harness, candidate), harness)


async def resolve_model_for_harness(
    harness: str,
    *,
    model_name: str = "",
    models_by_harness: dict | None = None,
    override: str | None = None,
) -> tuple[str | None, list[str]]:
    warnings: list[str] = []
    if not has_model_selection(harness):
        if override:
            warnings.append(f"{harness} does not accept a model choice; ignoring --model {override}.")
        return None, warnings
    candidate = override or _candidate_for_harness(harness, models_by_harness, model_name)
    if not candidate or candidate == "inherit":
        return None, warnings
    # Normalize the candidate to the harness-expected format before checking support
    formatted = _format_for_harness(candidate, _provider_for(harness, candidate), harness)
    if not _supported(harness, formatted):
        warnings.append(f"Model '{candidate}' is not in the {harness} harness registry. Falling back to auto/default.")
        return None, warnings
    return formatted, warnings
