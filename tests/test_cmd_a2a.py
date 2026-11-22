# SPDX-FileCopyrightText: 2026 Christian Sidak <christian@sentineltech.eu>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for observal_cli/cmd_a2a.py -- one happy path and one error path per command."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

import observal_cli.cmd_a2a as a2a

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry(identifier: str = "urn:air:agents.acme.com:a2a:triage") -> dict:
    return {
        "identifier": identifier,
        "displayName": "Triage",
        "version": "1.0.0",
        "description": "Triage agent",
        "obs:lifecycle": "pending",
        "obs:visibility": "team",
        "obs:artifactDigest": "sha256:abc123",
        "obs:a2aInterface": {"url": "https://agents.acme.com/triage", "protocolBinding": "a2a/1.0"},
        "capabilities": ["a2a-skill:triage"],
    }


# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------

class TestA2aSubmit:
    def test_submit_happy_path_table(self, monkeypatch: pytest.MonkeyPatch):
        post = Mock(return_value=_entry())
        monkeypatch.setattr(a2a.client, "post", post)

        result = runner.invoke(a2a.a2a_app, ["submit", "https://agents.acme.com/triage"])

        assert result.exit_code == 0, result.output
        assert "Registered" in result.output
        post.assert_called_once_with(
            "/api/v1/ard/imports/a2a",
            {"cardUrl": "https://agents.acme.com/triage", "visibility": "private"},
            operation="Register A2A agent",
            resource="https://agents.acme.com/triage",
        )

    def test_submit_happy_path_json(self, monkeypatch: pytest.MonkeyPatch):
        post = Mock(return_value=_entry())
        monkeypatch.setattr(a2a.client, "post", post)

        result = runner.invoke(
            a2a.a2a_app,
            ["submit", "https://agents.acme.com/triage", "--output", "json"],
        )

        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["identifier"] == "urn:air:agents.acme.com:a2a:triage"

    def test_submit_team_visibility_resolves_team_id(self, monkeypatch: pytest.MonkeyPatch):
        resolve = Mock(return_value="team-uuid-123")
        post = Mock(return_value=_entry())
        monkeypatch.setattr(a2a.client, "resolve_team_id", resolve)
        monkeypatch.setattr(a2a.client, "post", post)

        result = runner.invoke(
            a2a.a2a_app,
            ["submit", "https://agents.acme.com/triage", "--visibility", "team", "--team", "platform"],
        )

        assert result.exit_code == 0, result.output
        resolve.assert_called_once_with("platform")
        assert post.call_args.args[1]["teamId"] == "team-uuid-123"

    def test_submit_invalid_visibility_fails_before_api(self, monkeypatch: pytest.MonkeyPatch):
        post = Mock()
        monkeypatch.setattr(a2a.client, "post", post)

        result = runner.invoke(
            a2a.a2a_app,
            ["submit", "https://agents.acme.com/triage", "--visibility", "invalid"],
        )

        assert result.exit_code == 7
        post.assert_not_called()

    def test_submit_team_visibility_missing_team_fails_before_api(self, monkeypatch: pytest.MonkeyPatch):
        post = Mock()
        monkeypatch.setattr(a2a.client, "post", post)

        result = runner.invoke(
            a2a.a2a_app,
            ["submit", "https://agents.acme.com/triage", "--visibility", "team"],
        )

        assert result.exit_code == 7
        post.assert_not_called()


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

class TestA2aList:
    def test_list_happy_path_table(self, monkeypatch: pytest.MonkeyPatch):
        get = Mock(return_value={"items": [_entry()]})
        monkeypatch.setattr(a2a.client, "get", get)

        result = runner.invoke(a2a.a2a_app, ["list"])

        assert result.exit_code == 0, result.output
        get.assert_called_once_with(
            "/api/v1/ard/imports",
            operation="List A2A agents",
            resource="A2A agents",
        )

    def test_list_happy_path_json(self, monkeypatch: pytest.MonkeyPatch):
        payload = {"items": [_entry()]}
        get = Mock(return_value=payload)
        monkeypatch.setattr(a2a.client, "get", get)

        result = runner.invoke(a2a.a2a_app, ["list", "--output", "json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["items"][0]["identifier"] == "urn:air:agents.acme.com:a2a:triage"

    def test_list_empty_shows_message(self, monkeypatch: pytest.MonkeyPatch):
        get = Mock(return_value={"items": []})
        monkeypatch.setattr(a2a.client, "get", get)

        result = runner.invoke(a2a.a2a_app, ["list"])

        assert result.exit_code == 0
        assert "No remote A2A agents" in result.output

    def test_list_api_failure_propagates(self, monkeypatch: pytest.MonkeyPatch):
        import httpx

        request = httpx.Request("GET", "https://registry.example/api/v1/ard/imports")
        response = httpx.Response(401, request=request, json={"detail": "Unauthorized"})
        monkeypatch.setattr(
            a2a.client,
            "get",
            Mock(
                side_effect=httpx.HTTPStatusError("unauthorized", request=request, response=response)
            ),
        )

        result = runner.invoke(a2a.a2a_app, ["list"])

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------

class TestA2aReview:
    _ID = "urn:air:agents.acme.com:a2a:triage"
    _DIGEST = "sha256:9f2cabc"

    def test_review_approve_happy_path_table(self, monkeypatch: pytest.MonkeyPatch):
        post = Mock(return_value=_entry())
        monkeypatch.setattr(a2a.client, "post", post)

        result = runner.invoke(
            a2a.a2a_app,
            ["review", self._ID, "--approve", "--digest", self._DIGEST],
        )

        assert result.exit_code == 0, result.output
        post.assert_called_once_with(
            f"/api/v1/ard/imports/{self._ID}/review",
            {"action": "approve", "reason": None, "digest": self._DIGEST},
            operation="Review A2A agent",
            resource=self._ID,
        )

    def test_review_approve_happy_path_json(self, monkeypatch: pytest.MonkeyPatch):
        post = Mock(return_value=_entry())
        monkeypatch.setattr(a2a.client, "post", post)

        result = runner.invoke(
            a2a.a2a_app,
            ["review", self._ID, "--approve", "--digest", self._DIGEST, "--output", "json"],
        )

        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["identifier"] == self._ID

    def test_review_reject_with_reason(self, monkeypatch: pytest.MonkeyPatch):
        post = Mock(return_value=_entry())
        monkeypatch.setattr(a2a.client, "post", post)

        result = runner.invoke(
            a2a.a2a_app,
            ["review", self._ID, "--reject", "--reason", "No auth"],
        )

        assert result.exit_code == 0, result.output
        assert post.call_args.args[1]["action"] == "reject"
        assert post.call_args.args[1]["reason"] == "No auth"

    def test_review_neither_approve_nor_reject_fails(self, monkeypatch: pytest.MonkeyPatch):
        post = Mock()
        monkeypatch.setattr(a2a.client, "post", post)

        result = runner.invoke(a2a.a2a_app, ["review", self._ID])

        assert result.exit_code == 7
        post.assert_not_called()

    def test_review_both_approve_and_reject_fails(self, monkeypatch: pytest.MonkeyPatch):
        post = Mock()
        monkeypatch.setattr(a2a.client, "post", post)

        result = runner.invoke(
            a2a.a2a_app,
            ["review", self._ID, "--approve", "--reject", "--digest", self._DIGEST],
        )

        assert result.exit_code == 7
        post.assert_not_called()

    def test_review_approve_without_digest_fails(self, monkeypatch: pytest.MonkeyPatch):
        post = Mock()
        monkeypatch.setattr(a2a.client, "post", post)

        result = runner.invoke(a2a.a2a_app, ["review", self._ID, "--approve"])

        assert result.exit_code == 7
        post.assert_not_called()


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------

class TestA2aRemove:
    _ID = "urn:air:agents.acme.com:a2a:triage"

    def test_remove_happy_path_table(self, monkeypatch: pytest.MonkeyPatch):
        delete = Mock(return_value={})
        monkeypatch.setattr(a2a.client, "delete", delete)

        result = runner.invoke(a2a.a2a_app, ["remove", self._ID, "--yes"])

        assert result.exit_code == 0, result.output
        assert self._ID in result.output
        delete.assert_called_once_with(
            f"/api/v1/ard/imports/{self._ID}",
            operation="Remove A2A agent",
            resource=self._ID,
        )

    def test_remove_happy_path_json(self, monkeypatch: pytest.MonkeyPatch):
        delete = Mock(return_value={"removed": True})
        monkeypatch.setattr(a2a.client, "delete", delete)

        result = runner.invoke(a2a.a2a_app, ["remove", self._ID, "--yes", "--output", "json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data == {"removed": True}

    def test_remove_without_yes_flag_fails_before_api(self, monkeypatch: pytest.MonkeyPatch):
        delete = Mock()
        monkeypatch.setattr(a2a.client, "delete", delete)

        result = runner.invoke(a2a.a2a_app, ["remove", self._ID])

        assert result.exit_code == 7
        delete.assert_not_called()
