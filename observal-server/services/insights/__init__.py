# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Insights engine.

Generates agent insight reports: deterministic metadata extraction from raw
session JSONL, LLM-powered facet extraction, parallel narrative sections,
and self-contained HTML export.

Freely available in open-source Observal.
"""

import json
import logging  # stdlib logging (only for LiteLLM suppression)
import re

import litellm
from loguru import logger as optic

from config import settings

from .batch import discover_and_queue_reports as discover_and_queue_reports
from .batch import run_single_report as run_single_report
from .generator import generate_report_content
from .html_export import render_report_html as render_report_html

# ---------------------------------------------------------------------------
# Suppress LiteLLM's verbose logging (it logs every request at INFO level)
# ---------------------------------------------------------------------------
litellm.suppress_debug_info = True
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("LiteLLM Router").setLevel(logging.WARNING)
logging.getLogger("LiteLLM Proxy").setLevel(logging.WARNING)

INSIGHTS_AVAILABLE: bool = True


async def generate_report_content_wrapper(*args, **kwargs):
    return await generate_report_content(*args, **kwargs)


# ---------------------------------------------------------------------------
# Generic LLM caller via LiteLLM
# ---------------------------------------------------------------------------

# Regex to detect bare Bedrock-style Anthropic model IDs (e.g. us.anthropic.claude-opus-4-6-v1)
_BEDROCK_ANTHROPIC_RE = re.compile(r"^[a-z]{2}\.anthropic\.")

# Deprecation warning flags (fire once per process to avoid log spam during batch facet extraction)
_warned_model_format: set[str] = set()


def _normalize_model_id(model: str) -> str:
    """Normalize legacy model IDs to LiteLLM format (provider/model-name).

    Rules:
    1. Already contains '/' -> pass through (e.g. anthropic/..., bedrock/..., openai/...)
    2. Matches Bedrock pattern (e.g. us.anthropic.claude-*) -> prepend 'bedrock/'
    3. Starts with 'kimi' or 'moonshot' -> prepend 'openai/'
    4. Otherwise -> pass through as-is

    A deprecation warning is logged when normalization is applied.
    """
    if "/" in model:
        return model

    if _BEDROCK_ANTHROPIC_RE.match(model):
        if model not in _warned_model_format:
            _warned_model_format.add(model)
            optic.warning(
                "Deprecated model ID format '{}' -- use 'bedrock/{}' instead. "
                "Auto-prefixing 'bedrock/' for backwards compatibility.",
                model,
                model,
            )
        return f"bedrock/{model}"

    lower = model.lower()
    if lower.startswith("kimi") or lower.startswith("moonshot"):
        if model not in _warned_model_format:
            _warned_model_format.add(model)
            optic.warning(
                "Deprecated model ID format '{}' -- use 'openai/{}' instead. "
                "Auto-prefixing 'openai/' for backwards compatibility.",
                model,
                model,
            )
        return f"openai/{model}"

    return model


def _strip_json_fences(text: str) -> str:
    """Strip markdown JSON fences from LLM response if present."""
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    return text.strip()


async def _call_litellm(prompt: str, model: str, max_tokens: int = 16384) -> dict:
    """Call the configured LLM via LiteLLM.

    Reads api_key and api_base from dynamic_settings.
    LiteLLM handles provider-specific auth (including boto3 credential chain
    for Bedrock when no explicit api_key is passed).
    """
    import services.dynamic_settings as ds

    api_key = await ds.get("insights.api_key")
    api_base = await ds.get("insights.api_base")
    api_version = await ds.get("insights.api_version")

    aws_region = await ds.get("insights.aws_region")

    kwargs: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "num_retries": 2,
        "timeout": 120,
        "drop_params": True,
        "response_format": {"type": "json_object"},
    }

    if api_key:
        kwargs["api_key"] = api_key
    if api_base:
        kwargs["api_base"] = api_base
    if api_version and model.startswith("azure/"):
        kwargs["api_version"] = api_version
    if aws_region and "bedrock" in model:
        kwargs["aws_region_name"] = aws_region

    try:
        response = await litellm.acompletion(**kwargs)
        content = response.choices[0].message.content
        if not content:
            optic.warning("litellm: empty response, model={}", model)
            return {}

        content = _strip_json_fences(content)
        return json.loads(content)
    except json.JSONDecodeError as e:
        optic.error("litellm: json parse failed, model={}, error={}", model, str(e))
        return {}
    except Exception as e:
        optic.error("litellm: call failed, model={}, error={}", model, str(e))
        return {}


async def call_model(prompt: str, model_override: str | None = None, max_tokens: int = 16384) -> dict:
    """Call the configured LLM for insights generation.

    Model IDs use LiteLLM format: provider/model-name.
    Examples:
        - bedrock/us.anthropic.claude-opus-4-6-v1
        - anthropic/claude-sonnet-4-20250514
        - openai/gpt-4o
        - ollama/llama3

    Legacy bare model IDs (e.g. us.anthropic.claude-opus-4-6-v1) are
    auto-normalized with a deprecation warning.

    Args:
        prompt: The prompt to send to the model.
        model_override: Optional model ID to use instead of the default.
        max_tokens: Maximum output tokens (default 16384).
    """
    import services.dynamic_settings as ds

    # Use override, or fall back to sections model as the default
    model = model_override or await ds.get("insights.model_sections")

    if not model:
        return {}

    # Normalize legacy model IDs to LiteLLM format
    model = _normalize_model_id(model)

    result = await _call_litellm(prompt, model, max_tokens=max_tokens)
    if result:
        # Only a model that produced output is the report's provenance; failures return {}.
        from . import _deps

        _deps.note_model_used(model)
    return result


# ---------------------------------------------------------------------------
# Insights engine configuration
# ---------------------------------------------------------------------------


def enabled_features() -> list[str]:
    """Return enabled feature list for the public config endpoint."""
    return ["all"]


def configure_insights():
    """Wire up dependencies from the host app into the insights package.

    Called once at server startup.
    """
    from database import async_session
    from models.insight_meta_cache import InsightMetaCache
    from models.insight_session_facets import InsightSessionFacets
    from models.insight_session_meta import InsightSessionMeta
    from services.clickhouse import _query

    from . import _deps

    _deps.configure(
        settings=settings,
        query_fn=_query,
        call_model_fn=call_model,
        db_session_factory=async_session,
        meta_model=InsightSessionMeta,
        facets_model=InsightSessionFacets,
        meta_cache_model=InsightMetaCache,
    )
