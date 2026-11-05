# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Canonical-source invocation extraction, dispatched by verified harness support.

An unsupported harness is *not* a supported session with zero invocations.
Future harnesses opt in through ``HARNESS_REGISTRY[...]["invocation_extractor"]``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from observal_shared.harness_registry import HARNESS_REGISTRY

from .claude_code_invocations import ClaudeCodeInvocationExtractor
from .invocation_types import InvocationExtraction, InvocationExtractor
from .pi_invocations import PiInvocationExtractor

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_EXTRACTORS: dict[str, InvocationExtractor] = {
    "claude-code": ClaudeCodeInvocationExtractor(),
    "pi": PiInvocationExtractor(),
}


def extract_invocations(harness: str, rows: Sequence[Mapping[str, object]]) -> InvocationExtraction:
    """Resolve a registered harness's opt-in extractor; never guess a parser format."""
    extractor_id = HARNESS_REGISTRY[harness].get("invocation_extractor")  # unknown harness: KeyError
    if extractor_id is None:
        return InvocationExtraction(status="unsupported", invocations=())
    return _EXTRACTORS[extractor_id].extract(rows)  # unknown extractor: KeyError
