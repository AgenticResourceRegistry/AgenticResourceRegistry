# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Harness-neutral skill evidence facts, dispatched by verified harness support.

A harness opts in through ``HARNESS_REGISTRY[...]["skill_evidence_extractor"]``.
An unsupported harness is not a supported session with no skill evidence.

Evidence kinds, none of which shows that a skill achieved anything:

* ``available``: the harness advertised the installed skill to the model.
* ``load``: the model tried to read the installed skill's instructions. It is a
  *confirmed* load only when ``result_state`` is ``success`` (the linked read
  succeeded); ``error`` and ``unknown`` are attempts, not loads.
* ``invoked``: a harness-native, distinguishable invocation or selection origin.
  Codex's tagged selected-skill instructions establish a selection, **not** a
  separately confirmed load; report it as selection for Codex. Pi does not
  record distinguishable origin: pasted text can reproduce its expansion.

Each extractor declares the kinds its harness can observe at all
(``observed_kinds``). A kind outside that set is *not recorded* for the
harness: its counts are unknown, never a measured zero.

Each fact's ``source_block_key`` is unique within its source line and
namespaced (``skill-...``), so facts never replace each other or an MCP call
in the shared activity table.

Facts identify a skill by its install ``scope`` and ``alias`` (the directory
Observal installed it into) plus ``location_sha256``, the SHA-256 of the
SKILL.md path the harness recorded, never by the model-visible name. They
carry no absolute paths, transcript text, or skill content.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from observal_shared.harness_registry import HARNESS_REGISTRY

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

EvidenceKind = Literal["available", "load", "invoked"]


@dataclass(frozen=True)
class SkillEvidence:
    kind: EvidenceKind
    scope: Literal["user", "project"]
    alias: str
    source_line_offset: int
    source_block_key: str
    event_time: datetime | None
    # Only for ``load``: whether the linked read succeeded (a confirmed load).
    result_state: Literal["unknown", "success", "error"] = "unknown"
    # Only for ``load``: the read's tool-call id, when it is a unique link key.
    tool_use_id: str = ""
    # SHA-256 of the recorded SKILL.md location; matched against the verified install.
    location_sha256: str = ""


@dataclass(frozen=True)
class SkillEvidenceExtraction:
    status: Literal["supported", "unsupported"]
    evidence: tuple[SkillEvidence, ...]
    malformed_source_records: int = 0


class SkillEvidenceExtractor(Protocol):
    # The evidence kinds/scopes this harness's transcripts can show, tied to a file.
    observed_kinds: frozenset[EvidenceKind]
    observed_scopes: frozenset[str]

    def extract(self, rows: Sequence[Mapping[str, object]]) -> SkillEvidenceExtraction: ...


def _extractors() -> dict[str, SkillEvidenceExtractor]:
    from .claude_code_skill_evidence import ClaudeCodeSkillEvidenceExtractor
    from .codex_skill_evidence import CodexSkillEvidenceExtractor
    from .pi_skill_evidence import PiSkillEvidenceExtractor

    return {
        "pi": PiSkillEvidenceExtractor(),
        "claude-code": ClaudeCodeSkillEvidenceExtractor(),
        "codex": CodexSkillEvidenceExtractor(),
    }


def kind_recorded(harness: str, kind: EvidenceKind, scope: str | None = None) -> bool:
    """True only for a supported evidence kind in an observed install scope."""
    extractor_id = HARNESS_REGISTRY.get(harness, {}).get("skill_evidence_extractor")
    if not extractor_id:
        return False
    extractor = _extractors()[extractor_id]
    return kind in extractor.observed_kinds and (scope is None or scope in extractor.observed_scopes)


def requires_verified_skill_scope(harness: str) -> bool:
    """Whether a harness's use denominator needs a unique verified install scope."""
    extractor_id = HARNESS_REGISTRY.get(harness, {}).get("skill_evidence_extractor")
    return bool(extractor_id and getattr(_extractors()[extractor_id], "requires_verified_scope", False))


def extract_skill_evidence(harness: str, rows: Sequence[Mapping[str, object]]) -> SkillEvidenceExtraction:
    """Resolve a registered harness's opt-in extractor; never guess a transcript format."""
    extractor_id = HARNESS_REGISTRY[harness].get("skill_evidence_extractor")  # unknown harness: KeyError
    if extractor_id is None:
        return SkillEvidenceExtraction(status="unsupported", evidence=())
    return _extractors()[extractor_id].extract(rows)  # unknown extractor: KeyError
