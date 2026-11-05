# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""OBS-1443: edits reach the review, the CLI says so, and publish refusals explain themselves."""

import json

import pytest
from typer.testing import CliRunner

from observal_cli import cmd_review
from observal_cli.main import app
from observal_cli.review_display import print_draft_save
from observal_shared.mcp_analysis import docker_image_from_args

runner = CliRunner()


def _plain(text: str) -> str:
    """Output without colour codes or line wrapping, so assertions hold in any terminal."""
    import re

    return " ".join(re.sub(r"\x1b\[[0-9;]*m", "", text).split())


@pytest.mark.parametrize(
    ("args", "image"),
    [
        # The reported case: trailing container args must not be taken as the image.
        (
            [
                "run",
                "-i",
                "--rm",
                "-e",
                "GITHUB_PERSONAL_ACCESS_TOKEN",
                "ghcr.io/github/github-mcp-server",
                "stdio",
                "--read-only",
            ],
            "ghcr.io/github/github-mcp-server",
        ),
        (["run", "-it", "--rm", "mcp/fetch"], "mcp/fetch"),
        (["run", "--env=A=1", "-v", "/a:/b", "--name", "x", "img:1.2", "serve", "--port", "1"], "img:1.2"),
        (["run", "-eA=1", "img"], "img"),
        (["run", "-ie", "A", "img"], "img"),
        (["container", "run", "--rm", "img"], "img"),
        (["run", "--rm", "--", "img", "arg"], "img"),
        (["run", "--rm"], None),
        (["build", "."], None),
        (["--rm", "-i", "-env", "TOKEN", "img:latest"], "img:latest"),  # run omitted, -env typo
    ],
)
def test_docker_image_is_first_positional_after_run_options(args, image):
    assert docker_image_from_args(args) == image


def test_draft_save_message_names_the_new_revision(capsys):
    print_draft_save({"review_number": 7, "review_url": "/review/7", "review_revision": 3}, "unused")
    out = _plain(capsys.readouterr().out)
    assert "Review #7 now shows this change as revision 3" in out and "/review/7" in out


def test_draft_save_message_points_unreviewed_drafts_at_submit(capsys):
    print_draft_save({"status": "draft"}, "observal registry mcp submit --submit abc")
    assert "observal registry mcp submit --submit abc" in _plain(capsys.readouterr().out)


def _option_names(path):
    from typer.main import get_command

    command = get_command(app)
    for name in path:
        command = command.commands[name]
    return {opt for param in command.params for opt in getattr(param, "opts", [])}


@pytest.mark.parametrize("kind", ["mcp", "hook", "skill", "prompt", "sandbox"])
def test_every_component_submit_accepts_submit_draft(kind):
    # Inspect the parsed options, not help text: CI renders help with different width and colour.
    assert {"--submit", "--submit-draft"} <= _option_names(["registry", kind, "submit"])


def test_agent_publish_accepts_submit_draft():
    assert {"--submit", "--submit-draft"} <= _option_names(["agent", "publish"])


def test_review_publish_explains_missing_approvals_without_posting(monkeypatch):
    gate = {"ready": False, "approvals": 0, "required": 1, "requirements": ["required_approvals"]}
    posted = []
    monkeypatch.setattr(cmd_review.client, "get", lambda url, params=None: gate)
    monkeypatch.setattr(cmd_review.client, "post", lambda *a, **k: posted.append(a) or {})
    result = runner.invoke(app, ["review", "publish", "#2", "--output", "json"])
    assert result.exit_code != 0
    assert posted == []
    assert "1 more approval" in _plain(result.output) and "--override-reason" in _plain(result.output)


def test_review_publish_override_skips_the_approval_check(monkeypatch):
    gate = {"ready": False, "approvals": 0, "required": 1, "requirements": ["required_approvals", "edit_lock"]}
    posted = []
    monkeypatch.setattr(cmd_review.client, "get", lambda url, params=None: gate)
    monkeypatch.setattr(
        cmd_review.client, "post", lambda url, body=None, **kwargs: posted.append((url, body)) or {"state": "published"}
    )
    result = runner.invoke(app, ["review", "publish", "#2", "--override-reason", "urgent", "--output", "json"])
    assert result.exit_code == 0, result.output
    assert posted == [("/api/v1/reviews/%232/publish", {"category": None, "override_reason": "urgent"})]
    assert json.loads(result.stdout)["state"] == "published"


# OBS-1473: a pending first agent release is amended in place, never superseded by a new version.


def _agent(status, approved=None):
    return {
        "id": "a1",
        "name": "helper",
        "namespace": "user",
        "slug": "helper",
        "version": "1.0.0",
        "status": status,
        "latest_approved_version": approved,
    }


def test_agent_edit_amends_pending_release_and_reports_the_revision(monkeypatch):
    import observal_cli.cmd_agent as agent

    puts, posts = [], []
    monkeypatch.setattr(agent.client, "resolve_registry_reference", lambda kind, ref: "a1")
    monkeypatch.setattr(agent.client, "get", lambda url, **kwargs: _agent("pending"))
    monkeypatch.setattr(agent.client, "post", lambda url, *a, **k: posts.append(url) or {})
    monkeypatch.setattr(
        agent.client,
        "put",
        lambda url, body, **k: (
            puts.append((url, body))
            or {"id": "a1", "name": "helper", "version": "1.0.0", "review_number": 21, "review_revision": 2}
        ),
    )
    result = runner.invoke(app, ["agent", "edit", "helper", "--description", "Fixed"], terminal_width=200)
    assert result.exit_code == 0, result.output
    # Under review, the edit is also submitted, which answers requested changes (like the web Edit tab).
    assert posts == ["/api/v1/agents/a1/start-edit", "/api/v1/agents/a1/submit"]
    assert puts == [("/api/v1/agents/a1/draft", {"description": "Fixed"})]
    assert "Review #21 now shows this change as revision 2" in _plain(result.output)


def test_agent_edit_refuses_an_approved_version_and_points_at_release(monkeypatch):
    import observal_cli.cmd_agent as agent

    monkeypatch.setattr(agent.client, "resolve_registry_reference", lambda kind, ref: "a1")
    monkeypatch.setattr(agent.client, "get", lambda url, **kwargs: _agent("approved", "1.0.0"))
    result = runner.invoke(app, ["agent", "edit", "helper", "--description", "x"], terminal_width=200)
    assert result.exit_code != 0
    assert "agent release" in _plain(result.output)


def test_release_over_pending_first_release_points_at_amend(monkeypatch):
    import observal_cli.cmd_agent as agent

    monkeypatch.setattr(agent, "_validate_agent_definition", lambda data, operation: data)
    monkeypatch.setattr(agent, "_load_agent_yaml", lambda path, operation: {"name": "helper"})
    monkeypatch.setattr(agent.client, "resolve_registry_reference", lambda kind, ref: "a1")
    monkeypatch.setattr(agent.client, "get", lambda url, **kwargs: _agent("pending"))
    posted = []
    monkeypatch.setattr(agent.client, "post", lambda *a, **k: posted.append(a) or {})
    result = runner.invoke(app, ["agent", "release", "helper", "--bump", "patch"], terminal_width=200)
    assert result.exit_code != 0
    assert posted == []
    assert "not published yet" in _plain(result.output) and "agent edit" in _plain(result.output)


def test_cli_shows_component_reasons_from_list_errors():
    import httpx

    from observal_cli.client import _safe_detail

    response = httpx.Response(
        400,
        json={"detail": [{"component_type": "mcp", "component_id": "x", "reason": "mcp 'gh' is not approved"}]},
    )
    assert _safe_detail(response) == "mcp 'gh' is not approved"


@pytest.mark.asyncio
async def test_server_refuses_new_version_over_pending_first_release():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from api.routes.agent_versions import _refuse_release_over_unpublished_first_release
    from models.agent import AgentStatus

    agent = SimpleNamespace(
        id="a1", namespace="user", slug="helper", latest_version=SimpleNamespace(id="v1", version="1.0.0")
    )
    agent.latest_version.status = AgentStatus.pending
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[0, 21]))
    with pytest.raises(HTTPException) as refused:
        await _refuse_release_over_unpublished_first_release(db, agent)
    assert refused.value.status_code == 409 and "review #21" in refused.value.detail
    # With an approved release already published, releasing alongside a pending version stays allowed.
    db = SimpleNamespace(scalar=AsyncMock(return_value=1))
    await _refuse_release_over_unpublished_first_release(db, agent)


# PR review follow-ups: component edits answer change requests like agent edit.


@pytest.mark.parametrize(("kind", "plural"), [("mcp", "mcps"), ("hook", "hooks"), ("prompt", "prompts")])
def test_component_edit_under_review_submits_the_revision(monkeypatch, kind, plural):
    from observal_cli import draft_submit

    posts = []
    monkeypatch.setattr(
        draft_submit.client,
        "post",
        lambda url, *a, **k: posts.append(url) or {"status": "pending", "review_number": 4},
    )
    result = draft_submit.submit_edit_under_review(kind, plural, "c1", {"status": "changes_requested", "id": "c1"})
    assert posts == [f"/api/v1/{plural}/c1/submit"]
    assert result["status"] == "pending" and result["review_number"] == 4


def test_component_edit_outside_review_does_not_submit(monkeypatch):
    from observal_cli import draft_submit

    monkeypatch.setattr(draft_submit.client, "post", lambda *a, **k: pytest.fail("must not submit"))
    assert draft_submit.submit_edit_under_review("mcp", "mcps", "c1", {"status": "draft"}) == {"status": "draft"}


def test_skill_edit_under_review_submits_by_version(monkeypatch):
    from observal_cli import draft_submit

    def get(url, **kwargs):
        if url.endswith("/versions"):
            return {"items": [{"id": "v2", "version": "1.1.0", "status": "changes_requested"}]}
        return {"revision": "rev-9"}

    posts = []
    monkeypatch.setattr(draft_submit.client, "get", get)
    monkeypatch.setattr(draft_submit.client, "post", lambda url, body=None, **k: posts.append((url, body)) or {})
    result = draft_submit.submit_edit_under_review(
        "skill", "skills", "s1", {"status": "changes_requested", "version": "1.1.0"}
    )
    assert posts == [("/api/v1/skills/s1/versions/v2/submit", {"observed_revision": "rev-9"})]
    assert result["status"] == "pending"


# CodeRabbit follow-ups.


def test_unchanged_pending_save_is_not_resubmitted(monkeypatch):
    from observal_cli import draft_submit

    monkeypatch.setattr(draft_submit.client, "post", lambda *a, **k: pytest.fail("must not submit"))
    saved = {"status": "pending", "review_revision": None}
    assert draft_submit.submit_edit_under_review("mcp", "mcps", "c1", saved) == saved


def test_changed_pending_save_is_resubmitted(monkeypatch):
    from observal_cli import draft_submit

    posts = []
    monkeypatch.setattr(draft_submit.client, "post", lambda url, *a, **k: posts.append(url) or {"status": "pending"})
    draft_submit.submit_edit_under_review("mcp", "mcps", "c1", {"status": "pending", "review_revision": 3})
    assert posts == ["/api/v1/mcps/c1/submit"]


def test_agent_edit_releases_the_edit_lock_when_the_save_fails(monkeypatch):
    import observal_cli.cmd_agent as agent

    posts = []
    monkeypatch.setattr(agent.client, "resolve_registry_reference", lambda kind, ref: "a1")
    monkeypatch.setattr(agent.client, "get", lambda url, **kwargs: _agent("pending"))
    monkeypatch.setattr(agent.client, "post", lambda url, *a, **k: posts.append(url) or {})

    def failing_put(url, body, **kwargs):
        raise RuntimeError("server unavailable")

    monkeypatch.setattr(agent.client, "put", failing_put)
    result = runner.invoke(app, ["agent", "edit", "helper", "--description", "x"])
    assert result.exit_code != 0
    assert posts == ["/api/v1/agents/a1/start-edit", "/api/v1/agents/a1/cancel-edit"]
