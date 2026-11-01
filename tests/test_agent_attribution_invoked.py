# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Claude Code and Codex sessions are credited to an agent only when the agent ran."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from observal_cli.harness import ensure_loaded, get_adapter
from observal_cli.sessions.base import _resolve_agent

if TYPE_CHECKING:
    from pathlib import Path


def _lockfile(directory: str) -> dict:
    entry = {"name": "quick-fixer", "id": "agent-uuid", "version": "1.0.0", "scope": "project", "directory": directory}
    return {"harnesses": {"claude-code": {"agents": [entry]}, "codex": {"agents": [dict(entry)]}}}


def _resolve(
    tmp_path: Path, harness: str, transcript: Path | None, lines: list[str] | None = None, cwd: str | None = None
):
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("observal_cli.lockfile.read_registry_lockfile", return_value=({}, _lockfile(str(tmp_path)))),
        patch("observal_cli.delegation.tasks.delegated_agent", return_value=None),
    ):
        return _resolve_agent(str(tmp_path) if cwd is None else cwd, lines or [], transcript, harness=harness)


def _codex_rollout(path: Path, **meta) -> Path:
    path.write_text(json.dumps({"type": "session_meta", "payload": {"id": "thread", **meta}}) + "\n")
    return path


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
def test_pulled_agent_in_the_folder_does_not_claim_other_sessions(tmp_path: Path, harness: str):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"type":"user"}\n')

    assert _resolve(tmp_path, harness, transcript) == (None, None)


def test_claude_code_session_started_with_the_agent_is_credited(tmp_path: Path):
    lines = [json.dumps({"type": "agent-setting", "agentSetting": "quick-fixer"})]

    assert _resolve(tmp_path, "claude-code", None, lines) == ("agent-uuid", "1.0.0")


def test_claude_code_subagent_of_a_pulled_agent_is_credited(tmp_path: Path):
    subagents = tmp_path / "session-id" / "subagents"
    subagents.mkdir(parents=True)
    transcript = subagents / "agent-a1.jsonl"
    transcript.write_text('{"type":"user","isSidechain":true}\n')
    transcript.with_suffix(".meta.json").write_text(json.dumps({"agentType": "quick-fixer"}))

    # Discovered subagent sources carry no cwd, so the folder cannot be what credits them.
    assert _resolve(tmp_path, "claude-code", transcript, cwd="") == ("agent-uuid", "1.0.0")


def test_built_in_subagent_types_stay_unattributed(tmp_path: Path):
    subagents = tmp_path / "session-id" / "subagents"
    subagents.mkdir(parents=True)
    transcript = subagents / "agent-a2.jsonl"
    transcript.write_text('{"type":"user","isSidechain":true}\n')
    transcript.with_suffix(".meta.json").write_text(json.dumps({"agentType": "general-purpose"}))

    assert _resolve(tmp_path, "claude-code", transcript) == (None, None)


def test_codex_subagent_rollout_is_credited_to_its_agent(tmp_path: Path):
    rollout = _codex_rollout(
        tmp_path / "rollout-child.jsonl",
        thread_source="subagent",
        agent_role="quick-fixer",
        parent_thread_id="parent-thread",
    )

    assert _resolve(tmp_path, "codex", rollout, cwd="") == ("agent-uuid", "1.0.0")


def test_codex_sources_link_subagent_rollouts_to_their_parent(tmp_path: Path):
    day = tmp_path / ".codex" / "sessions" / "2026" / "10" / "18"
    day.mkdir(parents=True)
    parent_id = "01a14e32-7e4c-7632-883f-3bb99a309c53"
    child_id = "01a14e32-ce99-7131-ad08-efcdf76af180"
    _codex_rollout(day / f"rollout-2026-10-18T12-48-14-{parent_id}.jsonl", thread_source="user")
    _codex_rollout(
        day / f"rollout-2026-10-18T12-48-35-{child_id}.jsonl",
        thread_source="subagent",
        agent_role="quick-fixer",
        parent_thread_id=parent_id,
    )
    ensure_loaded()
    adapter = get_adapter("codex")

    sources = {source.session_id: source for source in adapter.discover_session_sources(home=tmp_path)}
    hooked = adapter.resolve_session_source({"session_id": child_id}, home=tmp_path)

    assert sources[parent_id].parent_session_id is None
    assert sources[child_id].parent_session_id == parent_id
    assert hooked is not None and hooked.parent_session_id == parent_id
