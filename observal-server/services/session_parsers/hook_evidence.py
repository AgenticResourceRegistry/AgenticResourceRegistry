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
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from observal_shared.harness_registry import HARNESS_REGISTRY

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

HookEvidenceKind = Literal["ran_with_output", "failed", "blocked"]


# Observal's own telemetry hooks are never a registry component, so a recorded run of
# one is not a candidate for attribution. Only the complete launcher Observal writes is
# recognised (server configs, ``agent pull`` rewrites and ``doctor`` specs):
#
#     [OBSERVAL_AGENT_ID=<id>] [PYTHONPATH=<root>] <python> [-I|-P] -m <session-push module>
#         [--harness <name>] [--json-response]
#
# A compound or wrapped command (``echo hi && python3 -m ...``, a ``hook_gate`` whose
# argument mentions the module, anything with shell substitution) is a user's hook and
# stays a candidate. Windows ``set "...=..." && ...`` forms are deliberately not matched.
_TELEMETRY_MODULES = frozenset(
    {
        "observal_cli.hooks.session_push",
        "observal_cli.hooks.codex_session_push",
        "observal_cli.hooks.antigravity_session_push",
    }
)
# The value is already unquoted by shlex, so a quoted package root may contain spaces.
_TELEMETRY_ENV = re.compile(r"(OBSERVAL_AGENT_ID|PYTHONPATH)=.+\Z")
_PYTHON = re.compile(r"python(?:3(?:\.\d{1,2})?)?\Z")  # interpreter basename; the path may contain spaces
_HARNESS_NAME = re.compile(r"[a-z][a-z0-9-]{0,40}\Z")
# Substitution, line breaks and control operators anywhere: never a single direct launcher.
_SHELL_ACTIVE = re.compile(r"[$`\n\r;&|<>()]")


def is_observal_telemetry_hook(command: str) -> bool:
    """Whether a recorded hook command is exactly Observal's own telemetry launcher."""
    import posixpath
    import shlex

    if _SHELL_ACTIVE.search(command):
        return False
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return False
    index, seen_env = 0, set()
    while index < len(tokens) and (match := _TELEMETRY_ENV.match(tokens[index])):
        if match.group(1) in seen_env:
            return False
        seen_env.add(match.group(1))
        index += 1
    if index >= len(tokens) or "=" in tokens[index] or not _PYTHON.match(posixpath.basename(tokens[index])):
        return False
    index += 1
    if index < len(tokens) and tokens[index] in ("-I", "-P"):
        index += 1
    if tokens[index : index + 1] != ["-m"] or index + 1 >= len(tokens) or tokens[index + 1] not in _TELEMETRY_MODULES:
        return False
    rest, seen_args = tokens[index + 2 :], set()
    while rest:
        option = rest.pop(0)
        if option in seen_args:
            return False
        seen_args.add(option)
        if option == "--harness":
            if not rest or not _HARNESS_NAME.match(rest.pop(0)):
                return False
        elif option != "--json-response":
            return False
    return True


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
