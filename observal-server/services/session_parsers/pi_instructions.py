# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Recorded Pi system instructions, never names in user/tool text or installed pins.

Evaluate each recorded response on its own tree ancestry. A session-wide claim
requires all responses (including abandoned branches) to match. Missing legacy
checkpoints are unknown; this is local transcript evidence, not attestation.
"""

import hashlib
import json
import re

_ENVELOPE = re.compile(
    r"^You are acting as the agent `([^`]+)`\. Follow its instructions\.\s*<agent-instructions>\s*(.*?)\s*</agent-instructions>\s*<task>\s*(.+?)\s*</task>\s*$",
    re.S,
)

_FILES = re.compile(r'<project_instructions\b[^>]*\bpath="([^"]+)"[^>]*>(.*?)</project_instructions>', re.S)


def _text(value):
    return value.replace("\r\n", "\n").strip() if isinstance(value, str) else ""


def _matches(sections, content, expected):
    if _text(content) == expected or _text(sections.get("preamble")) == expected:
        return True
    for path, body in _FILES.findall(sections.get("project_context", "")):
        normalized = path.replace("\\", "/").lower()
        if not normalized.endswith(("/.pi/agent/agents.md", "/.pi/agent/agents.override.md")):
            continue  # Contributor docs and inactive isolated profiles are not the active Pi file.
        body = _text(body)
        if body == expected:
            return True
        for match in re.finditer(r"(?:^|\n)" + re.escape(expected) + r"(?:\n|$)", body):
            if body[: match.start()].count("```") % 2 == 0:
                return True
    return False


def instruction_evidence(entries, expected):
    prompt = _text((expected or {}).get("prompt"))
    if not prompt:
        return {"state": "unknown", "reason": "registry_prompt_unavailable"}
    records = [e for e in entries if isinstance(e, dict) and e.get("type") != "session"]
    if any(
        e.get("type") == "message"
        and (not isinstance(e.get("message"), dict) or not isinstance(e["message"].get("role"), str))
        for e in records
    ):
        return {"state": "unknown", "reason": "malformed_message"}
    tree = any("parentId" in e for e in records)
    indexed = {}
    if tree:
        for entry in records:
            identity = entry.get("id")
            if not isinstance(identity, str) or not identity or identity in indexed:
                return {"state": "unknown", "reason": "ambiguous_tree"}
            indexed[identity] = entry
        if any(
            e.get("parentId") is not None and (not isinstance(e["parentId"], str) or e["parentId"] not in indexed)
            for e in records
        ):
            return {"state": "unknown", "reason": "incomplete_tree"}
    memo = {}
    visiting = set()

    def apply(entry, prior):
        sections, content, known = prior
        message = entry.get("message") or {}
        if entry.get("type") == "context_edit" and entry.get("targetId") == sections.get("_delegated_entry"):
            sections = {k: v for k, v in sections.items() if not k.startswith("_delegated")}
        if isinstance(message, dict) and message.get("role") == "user":
            value = message.get("content")
            if isinstance(value, list):
                value = "\n".join(b.get("text", "") for b in value if isinstance(b, dict) and b.get("type") == "text")
            match = _ENVELOPE.fullmatch(value) if isinstance(value, str) else None
            names = (expected or {}).get("names") or []
            if match and match.group(1) in names and _text(match.group(2)) == prompt:
                sections = {**sections, "_delegated": prompt, "_delegated_entry": entry.get("id")}
        checkpoint = entry.get("type") == "compaction"
        if checkpoint:
            message = entry.get("systemMessage") or {}
            sections, content, known = {}, "", False
        if isinstance(message, dict) and message.get("role") == "system":
            patch = message.get("sections")
            if patch is not None:
                if not isinstance(patch, dict) or any(v is not None and not isinstance(v, str) for v in patch.values()):
                    return {}, "", False
                sections = dict(sections)
                if "preamble" in patch or "project_context" in patch:
                    sections = {k: v for k, v in sections.items() if not k.startswith("_delegated")}
                for key, value in patch.items():
                    if value is None:
                        sections.pop(key, None)
                    else:
                        sections[key] = value
                known = known or "preamble" in patch or "project_context" in patch
            elif isinstance(message.get("content"), str) and message["content"].strip():
                sections, content, known = {}, message["content"], True
        return sections, content, known

    def state(identity):
        if identity in memo:
            return memo[identity]
        if identity in visiting:
            raise ValueError("cyclic_tree")
        visiting.add(identity)
        entry = indexed[identity]
        parent = entry.get("parentId")
        prior = state(parent) if parent is not None else ({}, "", False)
        memo[identity] = apply(entry, prior)
        visiting.remove(identity)
        return memo[identity]

    observations = []
    prior = ({}, "", False)
    try:
        for entry in records:
            current = state(entry["id"]) if tree else apply(entry, prior)
            prior = current
            message = entry.get("message") or {}
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            if message.get("stopReason") == "error" and not message.get("content"):
                continue  # A request that never reached the model says nothing about loaded instructions.
            sections, content, known = current
            loaded = sections.get("_delegated") == prompt or _matches(sections, content, prompt)
            observations.append("verified" if loaded else "unknown" if not known else "mismatch")
    except (ValueError, RecursionError):
        return {"state": "unknown", "reason": "invalid_or_deep_tree"}
    result = (
        "mismatch"
        if "mismatch" in observations
        else "unknown"
        if not observations or "unknown" in observations
        else "verified"
    )
    fingerprint = hashlib.sha256(
        json.dumps(
            {"version_id": (expected or {}).get("version_id"), "prompt": prompt, "source": entries, "policy": 1},
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    return {
        "state": result,
        "reason": {
            "verified": "recorded_instruction_match",
            "mismatch": "recorded_instruction_mismatch",
            "unknown": "system_checkpoint_unavailable",
        }[result],
        "responses_checked": len(observations),
        "cache_key": fingerprint,
        "version_evidence": "reported_binding_and_expected_prompt; not_unique_version_attestation",
    }
