# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Kiro session file helpers.

Handles JSONL file discovery, session ID resolution, and credit reading
for Kiro sessions.

Kiro stores transcripts in two different layouts and Observal has to read both:

* **CLI** - ``~/.kiro/sessions/cli/<session_id>.jsonl`` with a companion
  ``<session_id>.json`` holding session state, credits and the active agent.
* **IDE** - ``~/.kiro/sessions/<workspaceHash>/<session_id>/messages.jsonl``
  with a sibling ``session.json``. Records are enveloped as
  ``{id, timestamp, payload}`` and credits live on ``usage_summary`` payloads.

Every public helper here takes a transcript path and dispatches on its layout,
so callers never need to know which surface produced the session.
"""

from __future__ import annotations

import json
from pathlib import Path

# Basename of the transcript the Kiro IDE writes inside each session directory.
_IDE_TRANSCRIPT_NAME = "messages.jsonl"

# Basename of the IDE's session-state file, a sibling of the transcript.
_IDE_SESSION_NAME = "session.json"


def find_sessions_dir(home: Path | None = None) -> Path:
    """Return ~/.kiro/sessions/cli/ (the root of all Kiro CLI session JSONL files)."""
    if home is None:
        home = Path.home()
    return home / ".kiro" / "sessions" / "cli"


def sessions_root(home: Path | None = None) -> Path:
    """Return ~/.kiro/sessions/, the parent of both the CLI and IDE layouts."""
    if home is None:
        home = Path.home()
    return home / ".kiro" / "sessions"


def is_ide_transcript(path: Path | None) -> bool:
    """Return True when a transcript path is in the IDE layout."""
    return path is not None and path.name == _IDE_TRANSCRIPT_NAME


def find_kiro_ide_jsonl(session_id: str, home: Path | None = None) -> Path | None:
    """Return the IDE transcript for a session id, or None if absent.

    The IDE buckets sessions by workspace hash, which is not derivable from the
    session id, so every bucket is checked. Buckets are few (one per workspace
    ever opened) and the probe is a single ``exists()`` each.
    """
    if not session_id:
        return None
    root = sessions_root(home)
    try:
        buckets = [d for d in root.iterdir() if d.is_dir() and d.name != "cli"]
    except OSError:
        return None
    for bucket in buckets:
        candidate = bucket / session_id / _IDE_TRANSCRIPT_NAME
        if candidate.exists():
            return candidate
    return None


def find_kiro_jsonl(session_id: str, home: Path | None = None) -> Path | None:
    """Return the Path to a Kiro session transcript, or None if not found.

    Checks the CLI layout first, then the IDE layout. Session ids do not
    collide between the two: the IDE prefixes its own with ``sess_``.
    """
    if not session_id:
        return None
    if home is None:
        home = Path.home()
    path = home / ".kiro" / "sessions" / "cli" / f"{session_id}.jsonl"
    if path.exists():
        return path
    return find_kiro_ide_jsonl(session_id, home=home)


def _companion_path(session_jsonl: Path) -> Path:
    """Return the session-state file that sits alongside a transcript."""
    if is_ide_transcript(session_jsonl):
        return session_jsonl.with_name(_IDE_SESSION_NAME)
    return session_jsonl.with_suffix(".json")


def _read_kiro_session(session_jsonl: Path | None) -> dict | None:
    """Read a Kiro companion session object, returning None on any invalid shape."""
    if session_jsonl is None:
        return None
    try:
        session = json.loads(_companion_path(session_jsonl).read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return session if isinstance(session, dict) else None


def _iter_ide_records(transcript: Path) -> list[dict]:
    """Return the records of an IDE transcript, skipping unreadable lines.

    A transcript being appended to while it is read can end in a partial line,
    so malformed lines are skipped rather than failing the whole read.
    """
    records: list[dict] = []
    try:
        with transcript.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and isinstance(record.get("payload"), dict):
                    records.append(record)
    except OSError:
        return []
    return records


def _iter_ide_payloads(transcript: Path) -> list[dict]:
    """Return the payload objects of an IDE transcript."""
    return [record["payload"] for record in _iter_ide_records(transcript)]


def read_kiro_ide_agent_name(transcript: Path) -> str | None:
    """Return the agent an IDE session delegated to, or None if it ran bare.

    The IDE session itself always runs as Kiro; a registry agent takes part only
    when Kiro delegates a turn to it, which the transcript records as a
    ``sub_agent_start`` payload carrying ``subAgentName``. Nothing else in the
    IDE's on-disk state names an agent - ``session.json`` has no equivalent of
    the CLI's ``session_state.agent_name``.

    A session can delegate to several different agents, and attribution is
    session-level, so the most recent one wins. That mirrors the CLI's
    latest-turn fallback rather than inventing a second rule.
    """
    latest: str | None = None
    for payload in _iter_ide_payloads(transcript):
        if payload.get("type") != "sub_agent_start":
            continue
        name = payload.get("subAgentName")
        if isinstance(name, str) and name.strip():
            latest = name.strip()
    return latest


def read_kiro_ide_session_agent(transcript: Path) -> str | None:
    """Return the agent an IDE session was started as, or None for a plain chat.

    Selecting an agent from the IDE's dropdown starts the whole conversation as
    that agent, which is recorded two ways:

    - ``session_start.agentType`` holds the agent name instead of "vibe"
    - the agent profile's own hooks fire, and their ``hookId`` is prefixed with
      the agent name rather than a path to a standalone hooks file

    Both are checked because ``session_start`` is not always present - a
    transcript can begin mid-session - while the hooks fire on every turn. A
    "vibe" session is ordinary Kiro and returns None.
    """
    declared: str | None = None
    hook_name: str | None = None
    delegated = False
    for record in _iter_ide_records(transcript):
        payload = record["payload"]
        ptype = payload.get("type")
        if ptype == "session_start":
            agent_type = payload.get("agentType")
            if isinstance(agent_type, str) and agent_type.strip():
                if agent_type.strip() == "vibe":
                    return None  # A delegated agent's hook does not identify the parent chat.
                declared = agent_type.strip()
        elif ptype == "sub_agent_start":
            delegated = True
        elif ptype == "ContextualHookInvoked":
            hook_id = payload.get("hookId")
            # A path means a standalone hooks file, which is not agent-scoped.
            if isinstance(hook_id, str) and hook_id and not hook_id.startswith("/"):
                name = hook_id.split("#", 1)[0].strip()
                if name:
                    hook_name = name
    return declared or (None if delegated else hook_name)


def is_ide_subexecution(path: Path) -> bool:
    """Return whether a path is an IDE sub-execution transcript."""
    return path.parent.name == "sub-executions"


def ide_subexecution_parent(path: Path) -> Path:
    """Return the parent conversation transcript for a sub-execution."""
    return path.parent.parent / "messages.jsonl"


def read_kiro_ide_subexecution_origin(path: Path) -> tuple[str | None, str | None, str | None]:
    """Return the (agent name, prompt, timestamp) a sub-execution started with.

    A sub-execution transcript holds only the agent's own output; nothing in it
    names the agent or records what it was asked. Both live in the parent
    conversation's ``sub_agent_start``, keyed by ``subSessionId``, which is the
    sub-execution's filename.

    The timestamp is the moment the agent was asked, which is what a synthetic
    prompt record must carry: without it the record inherits its upload time
    and sorts after the reply it prompted, producing a negative duration.

    Returns all-None for an orphan whose parent has no matching record, so
    callers fail closed rather than capturing work they cannot attribute.
    """
    parent = ide_subexecution_parent(path)
    if not parent.exists():
        return None, None, None
    target = path.stem
    for record in _iter_ide_records(parent):
        payload = record.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "sub_agent_start":
            continue
        if str(payload.get("subSessionId") or "") != target:
            continue
        name = payload.get("subAgentName")
        prompt = payload.get("prompt")
        started_at = record.get("timestamp")
        return (
            name.strip() if isinstance(name, str) and name.strip() else None,
            prompt if isinstance(prompt, str) else None,
            started_at if isinstance(started_at, str) and started_at else None,
        )
    return None, None, None


def read_kiro_agent_name(session_jsonl: Path | None) -> str | None:
    """Return the active Kiro agent for a session, or None when unattributed.

    For CLI sessions Kiro stores metadata next to the transcript as
    ``<session_id>.json``. Current versions expose the active agent directly as
    ``session_state.agent_name``. Older-compatible metadata also records the
    agent on each user turn, so the latest turn is a safe fallback when the
    direct field is absent. Missing or malformed metadata is left unattributed.

    IDE sessions carry no such field and are resolved from the transcript's
    delegation records instead.
    """
    if is_ide_transcript(session_jsonl):
        return read_kiro_ide_agent_name(session_jsonl)

    session = _read_kiro_session(session_jsonl)
    if session is None:
        return None

    state = session.get("session_state")
    if not isinstance(state, dict):
        return None
    agent_name = state.get("agent_name")
    if isinstance(agent_name, str) and agent_name.strip():
        return agent_name.strip()

    conversation = state.get("conversation_metadata")
    if not isinstance(conversation, dict):
        return None
    turns = conversation.get("user_turn_metadatas")
    if not isinstance(turns, list):
        return None
    for turn in reversed(turns):
        if not isinstance(turn, dict):
            continue
        loop_id = turn.get("loop_id")
        agent_id = loop_id.get("agent_id") if isinstance(loop_id, dict) else None
        name = agent_id.get("name") if isinstance(agent_id, dict) else None
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def read_kiro_session_cwd(session_jsonl: Path | None) -> str:
    """Return the working directory persisted in a Kiro companion session.

    The CLI records a single ``cwd``; the IDE records ``workspacePaths``, whose
    first entry is the folder the session was opened against.
    """
    session = _read_kiro_session(session_jsonl)
    if session is None:
        return ""
    cwd = session.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        return cwd.strip()
    workspaces = session.get("workspacePaths")
    if isinstance(workspaces, list):
        for entry in workspaces:
            if isinstance(entry, str) and entry.strip():
                return entry.strip()
    return ""


def resolve_session_id(event: dict) -> str:
    """Return a non-empty session ID supplied explicitly by a Kiro hook event.

    Identity-less events are intentionally left unresolved because a shared
    fallback cannot safely correlate concurrent Kiro sessions.
    """
    session_id = event.get("session_id")
    return session_id.strip() if isinstance(session_id, str) else ""


def _read_ide_credits(transcript: Path) -> float | None:
    """Sum credit usage across an IDE transcript's ``usage_summary`` payloads."""
    total = 0.0
    for payload in _iter_ide_payloads(transcript):
        if payload.get("type") != "usage_summary":
            continue
        summaries = payload.get("promptTurnSummaries")
        if not isinstance(summaries, list):
            continue
        for summary in summaries:
            if not isinstance(summary, dict) or summary.get("unit") != "credit":
                continue
            try:
                total += float(summary.get("usage") or 0.0)
            except (TypeError, ValueError):
                continue
    return total if total > 0 else None


def read_kiro_credits(session_id: str, home: Path | None = None) -> float | None:
    """Read total credit usage for a Kiro session.

    Sums all turns so the sessions page shows lifetime credit spend. The CLI
    keeps them in the companion ``.json``; the IDE emits one ``usage_summary``
    payload per turn in the transcript itself. Returns None when neither
    layout yields a positive total.
    """
    if not session_id:
        return None
    if home is None:
        home = Path.home()
    json_path = home / ".kiro" / "sessions" / "cli" / f"{session_id}.json"
    if not json_path.exists():
        ide_transcript = find_kiro_ide_jsonl(session_id, home=home)
        return _read_ide_credits(ide_transcript) if ide_transcript else None
    try:
        session = json.loads(json_path.read_text())
        turns = session.get("session_state", {}).get("conversation_metadata", {}).get("user_turn_metadatas", [])
        total = sum(
            u.get("value", 0.0) for turn in turns for u in turn.get("metering_usage", []) if u.get("unit") == "credit"
        )
        return total if total > 0 else None
    except Exception:
        return None
