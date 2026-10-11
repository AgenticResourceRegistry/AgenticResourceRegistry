# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""The review CLI uses only the new review API and does not publish on approval."""

import json

from typer.testing import CliRunner

from observal_cli import cmd_review
from observal_cli.main import app

runner = CliRunner()


def test_review_list_caches_review_ids_and_renders_queue(monkeypatch):
    calls, cache = [], []
    row = {
        "id": "1f1c41e5-9dd4-409f-a356-000000000001",
        "number": 42,
        "title": "Test skill",
        "subject_type": "skill",
        "version": "1.0.0",
        "state": "open",
        "gate": {"approvals": 0, "required": 1},
        "threads": {"unresolved": 0},
    }
    monkeypatch.setattr(
        cmd_review.client, "get", lambda url, params=None: calls.append((url, params)) or {"items": [row]}
    )
    monkeypatch.setattr(cmd_review.config, "save_last_results", lambda items, kind: cache.append((items, kind)))
    result = runner.invoke(app, ["review", "list", "--type", "skill", "--output", "json"])
    assert result.exit_code == 0, result.output
    assert calls == [("/api/v1/reviews", {"state": "open", "limit": "100", "type": "skill"})]
    assert cache == [([row], "review")]
    assert json.loads(result.stdout)["items"][0]["number"] == 42


def test_review_verdict_does_not_call_publish_and_legacy_admin_group_is_absent(monkeypatch):
    posted = []
    monkeypatch.setattr(
        cmd_review.client, "post", lambda url, body=None: posted.append((url, body)) or {"state": "approved"}
    )
    approved = runner.invoke(app, ["review", "approve", "#42", "--output", "json"])
    assert approved.exit_code == 0, approved.output
    assert posted == [("/api/v1/reviews/%2342/submissions", {"verdict": "approve", "body": ""})]
    assert runner.invoke(app, ["admin", "review", "list"]).exit_code == 2
