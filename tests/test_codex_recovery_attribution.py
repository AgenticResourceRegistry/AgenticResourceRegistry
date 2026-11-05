# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A Codex session is credited only to the agent its own rollout proves."""

from __future__ import annotations

import json

import pytest

from observal_cli.sessions import base

PROJECT = "/work/project"


def lockfile(codex_agents, cursor_agents=()):
    def agent(name, scope="project", directory=PROJECT):
        return {"name": name, "id": f"{name}-id", "version": "1.0.0", "scope": scope, "directory": directory}

    return (
        {},
        {
            "harnesses": {
                "codex": {"agents": [agent(n, *a) for n, *a in codex_agents]},
                "cursor": {"agents": [agent(n) for n in cursor_agents]},
            }
        },
    )


def rollout(tmp_path, cwd=PROJECT, **meta):
    path = tmp_path / "rollout-1.jsonl"
    path.write_text(json.dumps({"type": "session_meta", "payload": {"id": "1", "cwd": cwd, **meta}}) + "\n")
    return path


def resolve(path, cwd="/hook/cwd"):
    return base._resolve_agent(cwd, [], path, harness="codex", session_ids=("1", None))


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("OBSERVAL_AGENT_ID", raising=False)
    monkeypatch.delenv("OBSERVAL_AGENT_NAME", raising=False)


def test_ambient_env_never_credits_a_codex_session(tmp_path, monkeypatch):
    monkeypatch.setattr("observal_cli.lockfile.read_registry_lockfile", lambda: lockfile([("active",)]))
    monkeypatch.setenv("OBSERVAL_AGENT_ID", "active-id")
    monkeypatch.setenv("OBSERVAL_AGENT_NAME", "active")
    assert resolve(rollout(tmp_path, thread_source="user")) == (None, None)


def test_hook_cwd_never_credits_a_codex_session(tmp_path, monkeypatch):
    monkeypatch.setattr("observal_cli.lockfile.read_registry_lockfile", lambda: lockfile([("active",)]))
    assert resolve(rollout(tmp_path, cwd="/elsewhere"), cwd=PROJECT) == (None, None)


def test_subagent_role_credits_the_pulled_codex_agent(tmp_path, monkeypatch):
    monkeypatch.setattr("observal_cli.lockfile.read_registry_lockfile", lambda: lockfile([("fixer",)]))
    path = rollout(tmp_path, thread_source="subagent", agent_role="fixer")
    assert resolve(path) == ("fixer-id", "1.0.0")


def test_subagent_role_ignores_agents_pulled_for_other_harnesses(tmp_path, monkeypatch):
    monkeypatch.setattr("observal_cli.lockfile.read_registry_lockfile", lambda: lockfile([], cursor_agents=["fixer"]))
    path = rollout(tmp_path, thread_source="subagent", agent_role="fixer")
    assert resolve(path) == (None, None)


def test_other_harnesses_keep_ambient_identity(monkeypatch):
    monkeypatch.setattr("observal_cli.lockfile.read_registry_lockfile", lambda: lockfile([], cursor_agents=["active"]))
    monkeypatch.setenv("OBSERVAL_AGENT_ID", "active-id")
    assert base._resolve_agent(PROJECT, [], None, harness="cursor", session_ids=("1", None)) == ("active-id", "1.0.0")
