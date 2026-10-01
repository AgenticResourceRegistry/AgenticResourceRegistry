# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Harness-neutral hook evidence facts, dispatched by verified harness support.

A harness opts in through ``HARNESS_REGISTRY[...]["hook_evidence_extractor"]``.
An unsupported harness is not a supported session with no hook evidence.

Evidence kinds, none of which shows what a hook achieved:

* ``ran_with_output``: the hook ran, exited 0 and printed output.
* ``failed``: the hook ran and exited non-zero without blocking.
* ``blocked``: the hook ran and blocked the action it guarded.

Harnesses can record only some runs. Claude Code records nothing for a hook
that succeeds silently, so recorded runs are a lower bound and "no recorded
runs" never means the hook did not run. Each extractor declares its
``observed_kinds`` and whether silent successes are recorded.

Facts identify a hook by ``binding_sha256``, the SHA-256 of ``event NUL
command`` exactly as the harness records it. That must equal the digest of
the verified install's recorded binding. Commands, output and paths never
leave the extractor.

The extraction also reports the session context that decides whether an
installed hook could run at all: whether the session was headless, which
agents were active, and whether this harness runs agent-scoped hooks in a
headless session (Claude Code 2.1.286 does not, per the recorded fixtures).

This contract is not harness-agnostic. A harness opts in only with its own
verified binding (``observal_cli`` adapter ``verify_hook_binding`` and a
recorded event/command identity), proof of which runs its transcripts record,
and proof of when an installed hook could run. Setting the registry key alone
establishes nothing; the extractor id must also resolve to an implementation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from observal_shared.harness_registry import HARNESS_REGISTRY

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

HookEvidenceKind = Literal["ran_with_output", "failed", "blocked"]


def hook_binding_sha256(event: str, command: str) -> str:
    """Digest of a hook binding; must equal ``observal_cli.layer.hook_binding_sha256``."""
    return hashlib.sha256(f"{event}\0{command}".encode()).hexdigest()


@dataclass(frozen=True)
class HookEvidence:
    kind: HookEvidenceKind
    binding_sha256: str
    source_line_offset: int
    source_block_key: str
    event_time: datetime | None
    tool_use_id: str = ""


@dataclass(frozen=True)
class HookSession:
    # True for a headless run, False for interactive, None when not recorded.
    headless: bool | None = None
    agents: frozenset[str] = field(default_factory=frozenset)
    # Harness fact, declared by the extractor from recorded sessions: whether
    # agent-scoped hooks run in a headless session.
    agent_hooks_run_headless: bool = False


@dataclass(frozen=True)
class HookEvidenceExtraction:
    status: Literal["supported", "unsupported"]
    evidence: tuple[HookEvidence, ...]
    session: HookSession = field(default_factory=HookSession)
    malformed_source_records: int = 0


class HookEvidenceExtractor(Protocol):
    observed_kinds: frozenset[HookEvidenceKind]
    # Whether a hook that succeeds silently leaves a record (if not, runs are a lower bound).
    records_silent_success: bool

    def extract(self, rows: Sequence[Mapping[str, object]]) -> HookEvidenceExtraction: ...


def _extractors() -> dict[str, HookEvidenceExtractor]:
    from .claude_code_hook_evidence import ClaudeCodeHookEvidenceExtractor

    return {"claude-code": ClaudeCodeHookEvidenceExtractor()}


def hook_extractor(harness: str) -> HookEvidenceExtractor | None:
    extractor_id = HARNESS_REGISTRY.get(harness, {}).get("hook_evidence_extractor")
    return _extractors()[extractor_id] if extractor_id else None


def extract_hook_evidence(harness: str, rows: Sequence[Mapping[str, object]]) -> HookEvidenceExtraction:
    """Resolve a registered harness's opt-in extractor; never guess a transcript format."""
    extractor_id = HARNESS_REGISTRY[harness].get("hook_evidence_extractor")  # unknown harness: KeyError
    if extractor_id is None:
        return HookEvidenceExtraction(status="unsupported", evidence=())
    return _extractors()[extractor_id].extract(rows)  # unknown extractor: KeyError
