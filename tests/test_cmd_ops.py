# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Behavioral tests for the operations, admin, trace, and self CLI commands."""

from __future__ import annotations

import json
from contextlib import nullcontext
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from click import Group
from rich.console import Console
from typer.main import get_command
from typer.testing import CliRunner

from observal_cli import cmd_ops as ops
from observal_cli.install_detector import InstallInfo, InstallMethod
from observal_cli.main import app as cli_app
from observal_cli.upgrade_lock import UpgradeLockError

runner = CliRunner()


class FakeConsole:
    def __init__(self) -> None:
        self.renderables: list[object] = []
        self.clear_count = 0

    def print(self, renderable: object) -> None:
        self.renderables.append(renderable)

    def clear(self) -> None:
        self.clear_count += 1


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Isolate command rendering and every external client boundary."""
    lines: list[str] = []
    json_values: list[object] = []
    saved_results: list[object] = []
    fake_console = FakeConsole()

    def blocked(name: str):
        def fail(*args, **kwargs):
            raise AssertionError(f"unmocked boundary: {name}, args={args}, kwargs={kwargs}")

        return fail

    monkeypatch.setattr(ops, "rprint", lambda *values, **kwargs: lines.append(" ".join(map(str, values))))
    monkeypatch.setattr(ops, "output_json", json_values.append)
    monkeypatch.setattr(ops, "console", fake_console)
    monkeypatch.setattr(ops, "spinner", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(ops, "relative_time", lambda value: f"relative:{value}")
    monkeypatch.setattr(ops.config, "resolve_alias", lambda value, expected_type=None: value)
    monkeypatch.setattr(
        ops.config,
        "save_last_results",
        lambda items, item_type=None: saved_results.append((items, item_type)),
    )
    monkeypatch.setattr(ops, "password_input", blocked("password_input"))
    monkeypatch.setattr(ops.typer, "confirm", blocked("confirm"))
    for method in ("get", "get_text", "post", "put", "delete"):
        monkeypatch.setattr(ops.client, method, blocked(f"client.{method}"))

    return SimpleNamespace(
        lines=lines,
        json=json_values,
        saved=saved_results,
        console=fake_console,
        text=lambda: "\n".join(lines),
    )


def render(renderable: object) -> str:
    stream = StringIO()
    Console(file=stream, color_system=None, width=180).print(renderable)
    return stream.getvalue()


def raises(error: BaseException):
    def fail(*_args, **_kwargs):
        raise error

    return fail


def test_review_list_filters_caches_and_renders_rows(cli, monkeypatch):
    calls = []
    reviews = [
        {
            "id": "component-123456789",
            "type": "mcp",
            "name": "search",
            "version": "1.2.3",
            "submitted_by": "alice",
            "created_at": "created",
        },
        {
            "id": "agent-987654321",
            "listing_type": "agent",
            "name": "builder",
            "submitted_at": "submitted",
        },
    ]

    def fake_get(path, params=None):
        calls.append((path, params))
        return reviews

    monkeypatch.setattr(ops.client, "get", fake_get)

    ops.review_list("mcp", "components", "table")

    assert calls == [("/api/v1/review", {"type": "mcp", "tab": "components"})]
    assert cli.saved == [(reviews, "review")]
    table = cli.console.renderables[0]
    assert table.title == "Pending Reviews (2)"
    assert table.columns[1]._cells == ["mcp", "agent"]
    assert table.columns[2]._cells == ["search", "builder"]
    assert table.columns[5]._cells == ["relative:created", "relative:submitted"]
    assert table.columns[6]._cells == ["component-12", "agent-987654"]


def test_review_list_supports_json_and_empty_results(cli, monkeypatch):
    responses = iter([[{"id": "one"}], []])
    calls = []

    def fake_get(path, params=None):
        calls.append((path, params))
        return next(responses)

    monkeypatch.setattr(ops.client, "get", fake_get)

    ops.review_list(None, None, "json")
    ops.review_list(None, None, "table")

    assert calls == [("/api/v1/review", None), ("/api/v1/review", None)]
    assert cli.json == [[{"id": "one"}]]
    assert cli.saved == [([{"id": "one"}], "review"), ([], "review")]
    assert "No pending reviews" in cli.text()


def test_review_show_resolves_and_renders_validation_details(cli, monkeypatch):
    item = {
        "id": "review-id",
        "name": "filesystem",
        "type": "mcp",
        "status": "rejected",
        "version": "2.0",
        "owner": "team",
        "submitted_by": "alice",
        "created_at": "created",
        "git_url": "https://example.test/repo",
        "description": "",
        "rejection_reason": "unsafe",
        "mcp_validated": False,
        "validation_results": [
            {"stage": "clone", "passed": True},
            {"stage": "scan", "passed": False},
        ],
    }
    calls = []
    monkeypatch.setattr(ops.config, "resolve_alias", lambda value, expected_type=None: f"resolved-{value}")
    monkeypatch.setattr(ops.client, "get", lambda path: calls.append(path) or item)

    ops.review_show("1", "table")

    assert calls == ["/api/v1/review/resolved-1"]
    output = render(cli.console.renderables[0])
    assert "filesystem" in output
    assert "Rejection Reason" in output
    assert "Not validated" in output
    assert "clone" in output and "pass" in output
    assert "scan" in output and "fail" in output


def test_review_show_supports_json(cli, monkeypatch):
    item = {"id": "review-id"}
    monkeypatch.setattr(ops.client, "get", lambda path: item)

    ops.review_show("review-id", "json")

    assert cli.json == [item]
    assert cli.console.renderables == []


@pytest.mark.parametrize(
    ("agent", "bundle", "path", "result", "message"),
    [
        (False, False, "/api/v1/review/item/approve", {"name": "component"}, "Approved: component"),
        (True, False, "/api/v1/review/agents/item/approve", {}, "Approved: item"),
        (
            False,
            True,
            "/api/v1/review/bundles/item/approve",
            {"name": "bundle", "approved_count": 3},
            "Bundle approved: bundle (3 components)",
        ),
    ],
)
def test_review_approve_selects_the_expected_endpoint(cli, monkeypatch, agent, bundle, path, result, message):
    calls = []
    monkeypatch.setattr(ops.client, "get", lambda path: {"type": "mcp"})  # not a skill: listing endpoint applies
    monkeypatch.setattr(ops.client, "post", lambda actual: calls.append(actual) or result)

    ops.review_approve("item", agent, bundle)

    assert calls == [path]
    assert message in cli.text()


def test_review_reject_rejects_blank_reasons(cli):
    with pytest.raises(typer.Exit) as exc_info:
        ops.review_reject("item", "   ", False, False)

    assert exc_info.value.exit_code == 7
    assert "1 to 5,000 characters" in cli.text()


@pytest.mark.parametrize(
    ("agent", "bundle", "path", "result", "message"),
    [
        (False, False, "/api/v1/review/item/reject", {"name": "component"}, "Rejected: component"),
        (True, False, "/api/v1/review/agents/item/reject", {}, "Rejected: item"),
        (
            False,
            True,
            "/api/v1/review/bundles/item/reject",
            {"name": "bundle", "rejected_count": 2},
            "Bundle rejected: bundle (2 components)",
        ),
    ],
)
def test_review_reject_posts_the_reason(cli, monkeypatch, agent, bundle, path, result, message):
    calls = []
    monkeypatch.setattr(ops.client, "get", lambda path: {"type": "mcp"})  # not a skill: listing endpoint applies
    monkeypatch.setattr(ops.client, "post", lambda actual, body: calls.append((actual, body)) or result)

    ops.review_reject("item", "policy violation", agent, bundle)

    assert calls == [(path, {"reason": "policy violation"})]
    assert message in cli.text()


def test_telemetry_status_reports_server_and_outbox_state(cli, monkeypatch):
    from observal_cli import telemetry_buffer

    monkeypatch.setattr(
        ops.client,
        "get",
        lambda path: {"status": "ok", "tool_call_events": 8, "agent_interaction_events": 5},
    )
    monkeypatch.setattr(
        telemetry_buffer,
        "stats",
        lambda: {
            "pending": 2,
            "bytes": 1536,
            "oldest_pending": "2026-01-01",
            "last_sync": "2026-01-02",
            "total": 0,
        },
    )

    ops.telemetry_status()

    output = cli.text()
    assert "Status:       [green]ok" in output
    assert "Tool calls:   8" in output
    assert "Pending:      2 batches" in output
    assert "Disk:         1.5 KiB" in output
    assert "Oldest:       2026-01-01 UTC" in output
    assert "Last sync:    2026-01-02 UTC" in output
    assert "Outbox is empty" in output


def test_telemetry_status_tolerates_unavailable_local_stats(cli, monkeypatch):
    from observal_cli import telemetry_buffer

    monkeypatch.setattr(ops.client, "get", lambda path: {})
    monkeypatch.setattr(telemetry_buffer, "stats", raises(OSError("unavailable")))

    ops.telemetry_status()

    assert "unknown" in cli.text()
    assert "Durable Session Outbox" in cli.text()
    assert "Unavailable" in cli.text()


def test_top_renders_tables_json_and_empty_states(cli, monkeypatch):
    responses = {
        "/api/v1/overview/top-mcps": [{"id": "abcdefghijk", "name": "search", "value": 4.9}],
        "/api/v1/overview/top-agents": [{"id": "agent", "name": "builder", "value": 3}],
    }
    monkeypatch.setattr(ops.client, "get", lambda path: responses[path])

    ops._top("mcp", "table")
    ops._top_impl("agent", "json")
    responses["/api/v1/overview/top-agents"] = []
    ops._top_impl("agent", "table")

    table = cli.console.renderables[0]
    assert table.title == "Top MCP Servers"
    assert table.columns[1]._cells == ["search"]
    assert table.columns[2]._cells == ["4"]
    assert cli.json == [[{"id": "agent", "name": "builder", "value": 3}]]
    assert "No agent data yet" in cli.text()


def test_rate_accepts_uuid_without_lookup(cli, monkeypatch):
    listing_id = "11111111-1111-1111-1111-111111111111"
    calls = []
    monkeypatch.setattr(ops.client, "post", lambda path, body: calls.append((path, body)) or {})

    ops._rate(listing_id, 5, "mcp", "excellent", True)

    assert calls == [
        (
            "/api/v1/feedback",
            {
                "listing_id": listing_id,
                "listing_type": "mcp",
                "rating": 5,
                "comment": "excellent",
                "anonymous": True,
            },
        )
    ]
    assert "Rated" in cli.text()


@pytest.mark.parametrize("listing_type", ["agent", "skill"])
def test_resolve_listing_id_looks_up_non_uuid_names(cli, monkeypatch, listing_type):
    calls = []
    monkeypatch.setattr(ops.client, "resolve_registry_reference", lambda item_type, value: "builder")

    def get(path, params=None):
        calls.append((path, params))
        return {"id": "resolved-id"}

    monkeypatch.setattr(ops.client, "get", get)

    assert ops._resolve_listing_id("builder", listing_type) == "resolved-id"
    assert calls == [
        ("/api/v1/registry/resolve", {"type": listing_type, "identifier": "builder"}),
    ]


def test_resolve_listing_id_propagates_lookup_failures(cli, monkeypatch):
    monkeypatch.setattr(ops.client, "resolve_registry_reference", lambda item_type, value: "missing")
    monkeypatch.setattr(ops.client, "get", raises(RuntimeError("missing")))

    with pytest.raises(RuntimeError, match="missing"):
        ops._resolve_listing_id("missing", "prompt")


def test_rate_update_fetches_the_review_and_only_sends_supplied_fields(cli, monkeypatch):
    listing_id = "11111111-1111-1111-1111-111111111111"
    calls = []
    monkeypatch.setattr(ops.client, "get", lambda path: calls.append(("get", path)) or {"id": "review-id"})
    monkeypatch.setattr(ops.client, "put", lambda path, body: calls.append(("put", path, body)) or {})

    ops._rate_update(listing_id, "mcp", 4, "revised", False)

    assert calls == [
        ("get", f"/api/v1/feedback/mine/mcp/{listing_id}"),
        ("put", "/api/v1/feedback/review-id", {"rating": 4, "comment": "revised", "anonymous": False}),
    ]
    assert "Review updated" in cli.text()


def test_rate_update_requires_at_least_one_change(cli, monkeypatch):
    listing_id = "11111111-1111-1111-1111-111111111111"
    monkeypatch.setattr(ops.client, "get", lambda path: {"id": "review-id"})

    with pytest.raises(typer.Exit) as exc_info:
        ops._rate_update(listing_id, "mcp", None, None, None)

    assert exc_info.value.exit_code == 7
    assert "No feedback changes were provided" in cli.text()


def test_rate_delete_fetches_then_deletes_the_review(cli, monkeypatch):
    listing_id = "11111111-1111-1111-1111-111111111111"
    calls = []
    monkeypatch.setattr(ops.client, "get", lambda path: calls.append(("get", path)) or {"id": "review-id"})
    monkeypatch.setattr(ops.client, "delete", lambda path: calls.append(("delete", path)))

    ops._rate_delete(listing_id, "mcp", yes=True)

    assert calls == [
        ("get", f"/api/v1/feedback/mine/mcp/{listing_id}"),
        ("delete", "/api/v1/feedback/review-id"),
    ]
    assert "Review deleted" in cli.text()


def test_feedback_renders_summary_and_review_comments(cli, monkeypatch):
    calls = []

    def fake_get(path):
        calls.append(path)
        if path.startswith("/api/v1/feedback/summary"):
            return {"average_rating": 4.4, "total_reviews": 2}
        return [{"rating": 5, "comment": "great"}, {"rating": 3, "comment": None}]

    monkeypatch.setattr(ops.client, "get", fake_get)

    listing_id = "11111111-1111-1111-1111-111111111111"
    ops._feedback(listing_id, "mcp", "table")

    assert calls == [f"/api/v1/feedback/mcp/{listing_id}", f"/api/v1/feedback/summary/{listing_id}"]
    assert "[bold]4.4[/bold]/5 (2 reviews)" in cli.text()
    assert "great" in cli.text()


def test_feedback_supports_json_and_empty_results(cli, monkeypatch):
    responses = iter(
        [
            [{"rating": 4}],
            {"average_rating": 4, "total_reviews": 1},
            [],
            {"average_rating": 0, "total_reviews": 0},
        ]
    )
    monkeypatch.setattr(ops.client, "get", lambda path: next(responses))

    listing_id = "11111111-1111-1111-1111-111111111111"
    ops._feedback_impl(listing_id, "agent", "json")
    ops._feedback_impl(listing_id, "agent", "table")

    assert cli.json == [{"summary": {"average_rating": 4, "total_reviews": 1}, "reviews": [{"rating": 4}]}]
    assert "No feedback yet" in cli.text()


def test_admin_settings_handles_table_json_and_empty_states(cli, monkeypatch):
    responses = iter(
        [
            [{"key": "retention", "value": "30"}],
            [{"key": "mode", "value": "strict"}],
            [],
        ]
    )
    monkeypatch.setattr(ops.client, "get", lambda path: next(responses))

    ops.admin_settings("table")
    ops.admin_settings("json")
    ops.admin_settings("table")

    table = cli.console.renderables[0]
    assert table.title == "Admin Settings"
    assert table.columns[0]._cells == ["retention"]
    assert cli.json == [[{"key": "mode", "value": "strict"}]]
    assert "No settings configured" in cli.text()


def test_admin_set_updates_the_named_setting(cli, monkeypatch):
    calls = []
    monkeypatch.setattr(
        ops.client,
        "put",
        lambda path, body: calls.append((path, body)) or {"key": "retention", "value": "90"},
    )

    ops.admin_set("retention", "90")

    assert calls == [("/api/v1/admin/settings/retention", {"value": "90"})]
    assert "Updated retention" in cli.text()
    assert "90" not in cli.text()


def test_admin_users_renders_all_roles_and_supports_json(cli, monkeypatch):
    users = [
        {"id": "super-id", "email": "s@example.test", "name": "S", "role": "super_admin"},
        {"id": "admin-id", "email": "a@example.test", "name": "A", "role": "admin"},
        {"id": "reviewer-id", "email": "r@example.test", "name": "R", "role": "reviewer"},
        {"id": "user-id", "email": "u@example.test", "name": "U", "role": "user"},
    ]
    monkeypatch.setattr(ops.client, "get", lambda path: users)

    ops.admin_users("table")
    ops.admin_users("json")

    table = cli.console.renderables[0]
    assert table.title == "Users (4)"
    assert table.columns[1]._cells == [
        "s@example.test",
        "a@example.test",
        "r@example.test",
        "u@example.test",
    ]
    assert table.columns[3]._cells == [
        "[magenta]super_admin[/magenta]",
        "[green]admin[/green]",
        "[cyan]reviewer[/cyan]",
        "[white]user[/white]",
    ]
    assert cli.json == [users]


def test_admin_create_user_posts_optional_fields_and_prints_one_time_password(cli, monkeypatch):
    calls = []
    response = {
        "id": "user-id",
        "email": "alice@example.test",
        "name": "Alice",
        "username": "alice",
        "role": "admin",
        "password": "generated-secret",
    }
    monkeypatch.setattr(ops.client, "post", lambda path, body: calls.append((path, body)) or response)

    ops.admin_create_user("alice@example.test", "Alice", "alice", "admin", "chosen-secret", "table")

    assert calls == [
        (
            "/api/v1/admin/users",
            {
                "email": "alice@example.test",
                "name": "Alice",
                "role": "admin",
                "username": "alice",
                "password": "chosen-secret",
            },
        )
    ]
    assert "Username:" in cli.text()
    assert "generated-secret" in cli.text()


def test_admin_create_user_json_omits_unsupplied_optional_fields(cli, monkeypatch):
    calls = []
    response = {"id": "user-id"}
    monkeypatch.setattr(ops.client, "post", lambda path, body: calls.append((path, body)) or response)

    ops.admin_create_user("alice@example.test", "Alice", None, "reviewer", None, "json")

    assert calls == [("/api/v1/admin/users", {"email": "alice@example.test", "name": "Alice", "role": "reviewer"})]
    assert cli.json == [response]


def test_admin_reset_password_reports_missing_users(cli, monkeypatch):
    monkeypatch.setattr(ops.client, "get", lambda path: [])

    with pytest.raises(typer.Exit) as exc_info:
        ops.admin_reset_password("missing@example.test", True)

    assert exc_info.value.exit_code == 5
    assert "User not found" in cli.text()


def test_admin_reset_password_can_generate_a_password(cli, monkeypatch):
    calls = []
    monkeypatch.setattr(
        ops.client,
        "get",
        lambda path: [{"id": "user-id", "email": "alice@example.test"}],
    )
    monkeypatch.setattr(
        ops.client,
        "put",
        lambda path, body: (
            calls.append((path, body)) or {"message": "Password reset", "generated_password": "new-secret"}
        ),
    )

    ops.admin_reset_password(" ALICE@example.test ", True)

    assert calls == [("/api/v1/admin/users/user-id/password", {"generate": True})]
    assert "Password reset" in cli.text()
    assert "new-secret" in cli.text()


def test_admin_reset_password_validates_interactive_confirmation(cli, monkeypatch):
    monkeypatch.setattr(
        ops.client,
        "get",
        lambda path: [{"id": "user-id", "email": "alice@example.test"}],
    )
    passwords = iter(["first", "second"])
    monkeypatch.setattr(ops, "password_input", lambda prompt: next(passwords))

    with pytest.raises(typer.Exit) as exc_info:
        ops.admin_reset_password("alice@example.test", False)

    assert exc_info.value.exit_code == 7
    assert "Passwords do not match" in cli.text()


def test_admin_reset_password_submits_matching_interactive_password(cli, monkeypatch):
    calls = []
    monkeypatch.setattr(
        ops.client,
        "get",
        lambda path: [{"id": "user-id", "email": "alice@example.test"}],
    )
    monkeypatch.setattr(ops, "password_input", lambda prompt: "same-secret")
    monkeypatch.setattr(
        ops.client,
        "put",
        lambda path, body: calls.append((path, body)) or {"message": "Password reset"},
    )

    ops.admin_reset_password("alice@example.test", False)

    assert calls == [("/api/v1/admin/users/user-id/password", {"new_password": "same-secret"})]


def test_admin_delete_user_reports_missing_users(cli, monkeypatch):
    monkeypatch.setattr(ops.client, "get", lambda path: [])

    with pytest.raises(typer.Exit) as exc_info:
        ops.admin_delete_user("missing@example.test", True)

    assert exc_info.value.exit_code == 5
    assert "User not found" in cli.text()


def test_admin_delete_user_confirms_and_deletes_the_matching_user(cli, monkeypatch):
    calls = []
    confirmations = []
    user = {"id": "user-id", "email": "alice@example.test", "name": "Alice", "role": "reviewer"}
    monkeypatch.setattr(ops.client, "get", lambda path: [user])
    monkeypatch.setattr(ops.client, "delete", lambda path: calls.append(path))
    monkeypatch.setattr(ops.typer, "confirm", lambda prompt, abort: confirmations.append((prompt, abort)))

    ops.admin_delete_user(" ALICE@example.test ", False)

    assert confirmations == [("\nPermanently delete this user?", True)]
    assert calls == ["/api/v1/admin/users/user-id"]
    assert "Deleted user alice@example.test" in cli.text()


def test_admin_diagnostics_renders_health_checks_and_configuration_issues(cli, monkeypatch):
    data = {
        "status": "degraded",
        "checks": {
            "database": {"status": "ok", "users": 7},
            "jwt_keys": {"status": "error", "algorithm": "ES256"},
            "runtime_config": {"issues": ["missing key", "weak setting"]},
        },
    }
    monkeypatch.setattr(ops.client, "get", lambda path: data)

    ops.admin_diagnostics("table")

    output = cli.text()
    assert "Overall: [yellow]degraded" in output
    assert "Database: [green]ok" in output
    assert "Users: 7" in output
    assert "JWT:     [red]error" in output
    assert "Algorithm: ES256" in output
    assert "missing key" in output and "weak setting" in output


def test_admin_diagnostics_supports_json_and_healthy_runtime_config(cli, monkeypatch):
    responses = iter(
        [
            {"status": "ok", "checks": {"runtime_config": {"issues": []}}},
            {"status": "unhealthy", "checks": {}},
        ]
    )
    monkeypatch.setattr(ops.client, "get", lambda path: next(responses))

    ops.admin_diagnostics("table")
    ops.admin_diagnostics("json")

    assert "Configuration: [green]ok" in cli.text()
    assert cli.json == [{"status": "unhealthy", "checks": {}}]


def test_admin_saml_config_handles_unconfigured_json_and_configured_values(cli, monkeypatch):
    configured = {
        "configured": True,
        "idp_entity_id": "idp",
        "idp_sso_url": "https://idp.test/sso",
        "idp_slo_url": None,
        "sp_entity_id": "sp",
        "active": True,
        "jit_provisioning": False,
    }
    responses = iter([{}, configured, configured])
    monkeypatch.setattr(ops.client, "get", lambda path: next(responses))

    ops.admin_saml_config("table")
    ops.admin_saml_config("json")
    ops.admin_saml_config("table")

    output = cli.text()
    assert "SAML SSO is not configured" in output
    assert "idp_entity_id: idp" in output
    assert "active: [green]Yes" in output
    assert "jit_provisioning: [red]No" in output
    assert cli.json == [configured]


def test_admin_saml_config_set_sends_all_supplied_values(cli, monkeypatch):
    calls = []
    result = {
        "sp_entity_id": "sp-result",
        "sp_acs_url": "https://app.test/acs",
        "sp_metadata_url": "https://app.test/metadata",
    }
    monkeypatch.setattr(ops.client, "put", lambda path, body: calls.append((path, body)) or result)

    ops.admin_saml_config_set("idp", "https://idp.test/sso", "https://idp.test/slo", "cert", "sp", False, True)

    assert calls == [
        (
            "/api/v1/admin/saml-config",
            {
                "active": True,
                "jit_provisioning": False,
                "idp_entity_id": "idp",
                "idp_sso_url": "https://idp.test/sso",
                "idp_slo_url": "https://idp.test/slo",
                "idp_x509_cert": "cert",
                "sp_entity_id": "sp",
            },
        )
    ]
    output = cli.text()
    assert "sp-result" in output
    assert "https://app.test/acs" in output
    assert "https://app.test/metadata" in output


def test_admin_saml_config_delete_confirms_and_deletes(cli, monkeypatch):
    confirmations = []
    calls = []
    monkeypatch.setattr(ops.typer, "confirm", lambda prompt, abort: confirmations.append((prompt, abort)))
    monkeypatch.setattr(ops.client, "delete", calls.append)

    ops.admin_saml_config_delete(False)

    assert confirmations == [("This will disable SAML SSO for all users. Continue?", True)]
    assert calls == ["/api/v1/admin/saml-config"]
    assert "configuration deleted" in cli.text()


def test_admin_scim_tokens_handles_table_json_and_empty_states(cli, monkeypatch):
    tokens = [
        {
            "id": "abcdefghijk",
            "token_prefix": "obs_a",
            "description": "Okta",
            "active": True,
            "created_at": "2026-06-01T12:00:00Z",
        },
        {"id": "second", "token_prefix": "obs_b", "active": False},
    ]
    responses = iter([tokens, tokens, []])
    monkeypatch.setattr(ops.client, "get", lambda path: next(responses))

    ops.admin_scim_tokens("table")
    ops.admin_scim_tokens("json")
    ops.admin_scim_tokens("table")

    table = cli.console.renderables[0]
    assert table.title == "SCIM Tokens"
    assert table.columns[1]._cells == ["obs_a", "obs_b"]
    assert table.columns[3]._cells == ["[green]Yes[/green]", "[red]No[/red]"]
    assert table.columns[4]._cells == ["2026-06-01", "-"]
    assert cli.json == [tokens]
    assert "No SCIM tokens configured" in cli.text()


def test_admin_scim_token_create_posts_description_and_displays_token(cli, monkeypatch):
    calls = []
    monkeypatch.setattr(
        ops.client,
        "post",
        lambda path, body: calls.append((path, body)) or {"token": "secret-token", "description": "Okta"},
    )

    ops.admin_scim_token_create("Okta")

    assert calls == [("/api/v1/admin/scim-tokens", {"description": "Okta"})]
    assert "secret-token" in cli.text()
    assert "Description: Okta" in cli.text()


def test_admin_scim_token_revoke_confirms_and_deletes(cli, monkeypatch):
    confirmations = []
    calls = []
    monkeypatch.setattr(ops.typer, "confirm", lambda prompt, abort: confirmations.append((prompt, abort)))
    monkeypatch.setattr(ops.client, "delete", calls.append)

    token_id = "11111111-1111-1111-1111-111111111111"
    ops.admin_scim_token_revoke(token_id, False)

    assert confirmations == [("Revoke SCIM token 11111111...?", True)]
    assert calls == [f"/api/v1/admin/scim-tokens/{token_id}"]
    assert "11111111... revoked" in cli.text()


def test_admin_security_events_encodes_filters_and_renders_event_styles(cli, monkeypatch):
    calls = []
    events = [
        {
            "timestamp": "2026-06-01T12:00:00Z",
            "event_type": "login",
            "severity": "critical",
            "actor_email": "alice@example.test",
            "outcome": "failure",
            "detail": "x" * 50,
        },
        {
            "created_at": "2026-06-02T12:00:00Z",
            "event_type": "role.change",
            "severity": "warning",
            "outcome": "success",
        },
        {"event_type": "view", "severity": "info", "outcome": "unknown", "detail": None},
        {"event_type": "other", "severity": "custom", "outcome": "other"},
    ]
    monkeypatch.setattr(ops.client, "get", lambda path, params=None: calls.append((path, params)) or {"events": events})

    ops.admin_security_events("login", "critical", "alice@example.test", 25, "table")

    assert calls == [
        (
            "/api/v1/admin/security-events",
            {
                "limit": 25,
                "offset": 0,
                "event_type": "login",
                "severity": "critical",
                "actor_email": "alice@example.test",
            },
        )
    ]
    table = cli.console.renderables[0]
    assert table.title == "Security Events (4)"
    assert table.columns[2]._cells == [
        "[red]critical[/red]",
        "[yellow]warning[/yellow]",
        "[dim]info[/dim]",
        "[white]custom[/white]",
    ]
    assert table.columns[4]._cells == [
        "[red]failure[/red]",
        "[green]success[/green]",
        "[white]unknown[/white]",
        "[white]other[/white]",
    ]
    assert table.columns[5]._cells[0] == "x" * 40


def test_admin_security_events_supports_json_and_empty_results(cli, monkeypatch):
    responses = iter([{"events": []}, []])
    monkeypatch.setattr(ops.client, "get", lambda path, params=None: next(responses))

    ops.admin_security_events(None, None, None, 50, "json")
    ops.admin_security_events(None, None, None, 50, "table")

    assert cli.json == [{"events": []}]
    assert "No security events found" in cli.text()


def test_admin_audit_log_encodes_filters_and_renders_resources(cli, monkeypatch):
    calls = []
    entries = [
        {
            "timestamp": "2026-06-01T12:00:00Z",
            "actor_email": "alice@example.test",
            "action": "agent.update",
            "resource_type": "agent",
            "resource_name": "builder",
            "ip_address": "127.0.0.1",
            "detail": "d" * 40,
        },
        {"created_at": "2026-06-02T12:00:00Z", "action": "login", "resource_type": "user"},
    ]
    monkeypatch.setattr(ops.client, "get", lambda path, params=None: calls.append((path, params)) or entries)

    ops.admin_audit_log("agent.update", "alice@example.test", "agent", 10, "table")

    assert calls == [
        (
            "/api/v1/admin/audit-log",
            {
                "limit": 10,
                "offset": 0,
                "action": "agent.update",
                "actor": "alice@example.test",
                "resource_type": "agent",
            },
        )
    ]
    table = cli.console.renderables[0]
    assert table.title == "Audit Log (2 entries)"
    assert table.columns[3]._cells == ["agent/builder", "user"]
    assert table.columns[5]._cells[0] == "d" * 30


def test_admin_audit_log_supports_json_and_empty_results(cli, monkeypatch):
    responses = iter([[{"action": "login"}], []])
    monkeypatch.setattr(ops.client, "get", lambda path, params=None: next(responses))

    ops.admin_audit_log(None, None, None, 50, "json")
    ops.admin_audit_log(None, None, None, 50, "table")

    assert cli.json == [[{"action": "login"}]]
    assert "No audit log entries found" in cli.text()


def test_admin_audit_log_export_prints_or_writes_csv(cli, monkeypatch, tmp_path):
    responses = iter(["a,b\n1,2\n", "a,b\n3,4\n"])
    calls = []
    echoes = []

    def get_text(path, params=None, *, content_type):
        calls.append((path, params, content_type))
        return next(responses)

    monkeypatch.setattr(ops.client, "get_text", get_text)
    monkeypatch.setattr(ops.typer, "echo", lambda value, nl=False: echoes.append((value, nl)))
    destination = tmp_path / "audit.csv"

    ops.admin_audit_log_export("login", "alice@example.test", None)
    ops.admin_audit_log_export(None, None, str(destination))

    assert calls == [
        (
            "/api/v1/admin/audit-log/export",
            {"action": "login", "actor": "alice@example.test"},
            "text/csv",
        ),
        ("/api/v1/admin/audit-log/export", None, "text/csv"),
    ]
    assert echoes == [("a,b\n1,2\n", False)]
    assert destination.read_text() == "a,b\n3,4\n"
    assert f"Audit log exported to {destination}" in cli.text()


@pytest.mark.parametrize(("enabled", "label"), [(True, "enabled"), (False, "disabled")])
def test_admin_trace_privacy_reports_current_state(cli, monkeypatch, enabled, label):
    monkeypatch.setattr(ops.client, "get", lambda path: {"trace_privacy": enabled})

    ops.admin_trace_privacy()

    assert label in cli.text()


@pytest.mark.parametrize(("enabled", "returned", "label"), [(True, True, "enabled"), (True, False, "disabled")])
def test_admin_trace_privacy_set_uses_the_server_result(cli, monkeypatch, enabled, returned, label):
    calls = []
    monkeypatch.setattr(
        ops.client,
        "put",
        lambda path, body: calls.append((path, body)) or {"trace_privacy": returned},
    )

    ops.admin_trace_privacy_set(enabled)

    assert calls == [("/api/v1/admin/trace-privacy", {"trace_privacy": enabled})]
    assert label in cli.text()


def test_admin_cache_clear_posts_to_the_clear_endpoint(cli, monkeypatch):
    calls = []
    monkeypatch.setattr(ops.client, "post", lambda path: calls.append(path) or {"cleared": 4})

    ops.admin_cache_clear()

    assert calls == ["/api/v1/admin/cache/clear"]
    assert "Cleared 4 cached entries" in cli.text()


def test_admin_set_role_reports_missing_users(cli, monkeypatch):
    monkeypatch.setattr(ops.client, "get", lambda path: [])

    with pytest.raises(typer.Exit) as exc_info:
        ops.admin_set_role("missing@example.test", "admin")

    assert exc_info.value.exit_code == 5
    assert "User not found" in cli.text()


def test_admin_set_role_updates_the_matching_user(cli, monkeypatch):
    calls = []
    monkeypatch.setattr(
        ops.client,
        "get",
        lambda path: [{"id": "user-id", "email": "alice@example.test"}],
    )
    monkeypatch.setattr(
        ops.client,
        "put",
        lambda path, body: calls.append((path, body)) or {"email": "alice@example.test", "role": "admin"},
    )

    ops.admin_set_role(" ALICE@example.test ", "admin")

    assert calls == [("/api/v1/admin/users/user-id/role", {"role": "admin"})]
    assert "alice@example.test is now admin" in cli.text()


def test_traces_passes_filters_and_supports_json(cli, monkeypatch):
    sessions = [{"session_id": "one"}]
    calls = []
    monkeypatch.setattr(ops.client, "get", lambda path, params=None: calls.append((path, params)) or sessions)

    ops._traces("kiro", 7, 5, False, False, "json")

    assert calls == [("/api/v1/sessions", {"limit": 5, "platform": "kiro", "days": 7})]
    assert cli.json == [sessions]


def test_traces_handles_empty_results_and_routes_detail_view(cli, monkeypatch):
    responses = iter([[], [{"session_id": "one"}]])
    details = []
    monkeypatch.setattr(ops.client, "get", lambda path, params=None: next(responses))
    monkeypatch.setattr(ops, "_render_sessions_detail", lambda sessions, full: details.append((sessions, full)))

    ops._traces_impl(None, None, 20, False, False, "table")
    ops._traces_impl(None, None, 20, True, True, "table")

    assert "No traces found" in cli.text()
    assert details == [([{"session_id": "one"}], True)]


def test_render_sessions_summary_formats_counts_and_tokens(cli, monkeypatch):
    sessions = [
        {
            "prompt_count": 1,
            "user_name": "Alice",
            "platform": "kiro",
            "tool_result_count": 2,
            "total_input_tokens": 13800,
            "total_output_tokens": 137,
            "first_event_time": "first",
        },
        {"prompt_count": 2, "last_event_time": "last"},
    ]

    calls = []
    monkeypatch.setattr(ops.client, "get", lambda path, params=None: calls.append((path, params)) or sessions)

    ops._traces_impl(None, None, 20, False, False, "table")

    assert calls == [("/api/v1/sessions", {"limit": 20})]
    table = cli.console.renderables[0]
    assert table.title == "Sessions (2)"
    assert table.columns[1]._cells == ["1 prompt", "2 prompts"]
    assert table.columns[4]._cells == ["1", "2"]
    assert table.columns[6]._cells == ["13.8k / 137", "0 / 0"]
    assert table.columns[7]._cells == ["relative:first", "relative:last"]


def test_render_sessions_detail_handles_failures_empty_sessions_events_and_subagents(cli, monkeypatch):
    sessions = [
        {
            "session_id": "empty-session",
            "prompt_count": 0,
            "tool_result_count": 0,
            "platform": "cursor",
            "first_event_time": "second",
        },
        {
            "session_id": "full-session",
            "prompt_count": 1,
            "tool_result_count": 2,
            "platform": "codex",
            "first_event_time": "third",
        },
    ]
    details = {
        "empty-session": {"events": []},
        "full-session": {
            "events": [
                {"event_name": "user_prompt", "body": "p" * 101},
                {"event_name": "assistant_response", "body": "a" * 151},
                {"event_name": "tool_call", "body": "ignored", "attributes": {"tool_name": "search"}},
                {"event_name": "hook_pretooluse", "body": "fallback-tool", "attributes": {}},
                {"event_name": "tool_result", "body": "r" * 101},
            ],
            "subagent_sessions": [
                {
                    "events": [
                        {"event_name": "human_turn", "body": "sub prompt"},
                        {"event_name": "tool_call", "body": "sub fallback", "attributes": {}},
                    ]
                }
            ],
        },
    }

    def fake_get(path):
        session_id = path.rsplit("/", 1)[1]
        return details[session_id]

    monkeypatch.setattr(ops.client, "get", fake_get)

    ops._render_sessions_detail(sessions, full=True)

    output = render(cli.console.renderables[0])
    assert "prompts: 0, tools: 0" in output
    assert "search" in output
    assert "fallback-tool" in output
    assert "subagent (2 events)" in output
    assert "sub prompt" in output
    assert "sub fallback" in output
    assert "…" in output


@pytest.mark.parametrize(
    ("input_tokens", "output_tokens", "expected"),
    [(1_200_000, 1_500, "1.2M / 1.5k"), (999, 0, "999 / 0")],
)
def test_format_tokens_compacts_large_counts(input_tokens, output_tokens, expected):
    assert ops._format_tokens(input_tokens, output_tokens) == expected


def test_do_install_delegates_to_upgrade_executor(cli, monkeypatch):
    from observal_cli import upgrade_executor

    calls = []
    install = object()
    monkeypatch.setattr(
        upgrade_executor,
        "execute",
        lambda info, target, direction, progress, interactive: calls.append(
            (info, target, direction, progress, interactive)
        ),
    )

    ops._do_install(install, "2.0.0", "upgrade")

    assert calls == [(install, "2.0.0", "upgrade", ops.spinner, True)]


def install_info(
    method: InstallMethod = InstallMethod.UV_TOOL,
    managed_by: str | None = "uv",
    path: Path | None = None,
) -> InstallInfo:
    return InstallInfo(method=method, path=path or Path("/mock/observal"), writable=True, managed_by=managed_by)


def test_upgrade_rejects_invalid_versions(cli, monkeypatch):
    from observal_cli import install_detector, version_check

    monkeypatch.setattr(version_check, "get_current_version", lambda: "1.0.0")
    monkeypatch.setattr(install_detector, "detect", lambda: install_info())

    with pytest.raises(typer.Exit) as raised:
        ops.upgrade("not-a-version", False, True)

    assert raised.value.exit_code == 7
    assert "Invalid target version" in cli.text()


def test_upgrade_reports_release_lookup_failures(cli, monkeypatch):
    from observal_cli import install_detector, version_check

    monkeypatch.setattr(version_check, "get_current_version", lambda: "1.0.0")
    monkeypatch.setattr(install_detector, "detect", lambda: install_info())
    monkeypatch.setattr(version_check, "_fetch_from_github", lambda include_pre: None)

    with pytest.raises(typer.Exit) as raised:
        ops.upgrade(None, True, True)

    assert raised.value.exit_code == 9
    assert "Could not fetch" in cli.text()


def test_upgrade_honors_declined_confirmation(cli, monkeypatch):
    from observal_cli import install_detector, version_check

    confirmations = []
    monkeypatch.setattr(version_check, "get_current_version", lambda: "1.0.0")
    monkeypatch.setattr(install_detector, "detect", lambda: install_info())
    monkeypatch.setattr(ops.typer, "confirm", lambda prompt: confirmations.append(prompt) or False)

    with pytest.raises(typer.Abort):
        ops.upgrade("1.1.0", False, False)

    assert confirmations == ["\nProceed with upgrade?"]
    assert "Current:" in cli.text() and "Target:" in cli.text()


def test_upgrade_reports_lock_contention(cli, monkeypatch):
    from observal_cli import install_detector, upgrade_lock, version_check

    monkeypatch.setattr(version_check, "get_current_version", lambda: "1.0.0")
    monkeypatch.setattr(install_detector, "detect", lambda: install_info())
    monkeypatch.setattr(upgrade_lock, "acquire_lock", raises(UpgradeLockError("busy")))

    with pytest.raises(typer.Exit) as raised:
        ops.upgrade("1.1.0", False, True)

    assert raised.value.exit_code == 6
    assert "already running" in cli.text()


def test_upgrade_installs_releases_lock_and_returns_json(cli, monkeypatch):
    from observal_cli import install_detector, upgrade_lock, version_check

    calls = []
    info = install_info()
    monkeypatch.setattr(version_check, "get_current_version", lambda: "development")
    monkeypatch.setattr(install_detector, "detect", lambda: info)
    monkeypatch.setattr(upgrade_lock, "acquire_lock", lambda scope: calls.append(("acquire", scope)) or "lock")
    monkeypatch.setattr(upgrade_lock, "release_lock", lambda lock: calls.append(("release", lock)))
    monkeypatch.setattr(
        ops,
        "_do_install",
        lambda actual, target, direction, output: calls.append((actual, target, direction, output)),
    )

    ops.upgrade("1.2.0", False, True, "json")

    assert calls == [
        ("acquire", "cli"),
        (info, "1.2.0", "upgrade", "json"),
        ("release", "lock"),
    ]
    assert cli.json == [
        {
            "action": "upgrade",
            "status": "completed",
            "from_version": "development",
            "to_version": "1.2.0",
            "install_method": "uv_tool",
            "path": "/mock/observal",
        }
    ]
    assert cli.lines == []


def test_downgrade_reports_empty_release_lists(cli, monkeypatch):
    from observal_cli import version_check

    monkeypatch.setattr(version_check, "get_current_version", lambda: "2.0.0")
    monkeypatch.setattr(version_check, "fetch_all_releases", lambda: [])

    with pytest.raises(typer.Exit) as raised:
        ops.downgrade(None, True, True)

    assert raised.value.exit_code == 9
    assert "Could not fetch" in cli.text()


def test_downgrade_list_supports_table_and_json(cli, monkeypatch):
    from observal_cli import version_check

    releases = [
        {"version": "2.0.0", "published_at": "2026-06-02"},
        {"version": "1.9.0", "published_at": "2026-06-01"},
    ]
    monkeypatch.setattr(version_check, "get_current_version", lambda: "2.0.0")
    monkeypatch.setattr(version_check, "fetch_all_releases", lambda: releases)

    ops.downgrade(None, True, True, "table")
    ops.downgrade(None, True, True, "json")

    table = cli.console.renderables[0]
    assert table.columns[0]._cells == ["2.0.0", "1.9.0"]
    assert table.columns[2]._cells == ["← current", ""]
    assert cli.json == [
        {
            "current_version": "2.0.0",
            "items": [
                {**releases[0], "current": True},
                {**releases[1], "current": False},
            ],
        }
    ]


@pytest.mark.parametrize(
    ("version", "message"),
    [("invalid", "Invalid target version"), ("0.9.0", "Cannot downgrade below")],
)
def test_downgrade_validates_target_versions(cli, monkeypatch, version, message):
    from observal_cli import version_check

    monkeypatch.setattr(version_check, "get_current_version", lambda: "2.0.0")

    with pytest.raises(typer.Exit) as raised:
        ops.downgrade(version, False, True)

    assert raised.value.exit_code == 7
    assert message in cli.text()


def test_downgrade_blocks_managed_installations(cli, monkeypatch):
    from observal_cli import install_detector, version_check

    monkeypatch.setattr(version_check, "get_current_version", lambda: "2.0.0")
    monkeypatch.setattr(install_detector, "detect", lambda: install_info(InstallMethod.SYSTEM_PACKAGE, "apt"))

    with pytest.raises(typer.Exit) as raised:
        ops.downgrade("1.10.4", False, True)

    assert raised.value.exit_code == 6
    assert "managed by apt" in cli.text()


def test_downgrade_honors_declined_confirmation(cli, monkeypatch):
    from observal_cli import install_detector, version_check

    monkeypatch.setattr(version_check, "get_current_version", lambda: "2.0.0")
    monkeypatch.setattr(install_detector, "detect", lambda: install_info())
    monkeypatch.setattr(ops.typer, "confirm", lambda prompt: False)

    with pytest.raises(typer.Abort):
        ops.downgrade("1.10.4", False, False)


def test_downgrade_reports_lock_contention(cli, monkeypatch):
    from observal_cli import install_detector, upgrade_lock, version_check

    monkeypatch.setattr(version_check, "get_current_version", lambda: "2.0.0")
    monkeypatch.setattr(install_detector, "detect", lambda: install_info())
    monkeypatch.setattr(upgrade_lock, "acquire_lock", raises(UpgradeLockError("busy")))

    with pytest.raises(typer.Exit) as raised:
        ops.downgrade("1.10.4", False, True)

    assert raised.value.exit_code == 6
    assert "already running" in cli.text()


def test_downgrade_installs_when_current_version_is_nonstandard(cli, monkeypatch):
    from observal_cli import install_detector, upgrade_lock, version_check

    calls = []
    info = install_info()
    monkeypatch.setattr(version_check, "get_current_version", lambda: "development")
    monkeypatch.setattr(install_detector, "detect", lambda: info)
    monkeypatch.setattr(upgrade_lock, "acquire_lock", lambda scope: "lock")
    monkeypatch.setattr(upgrade_lock, "release_lock", lambda lock: calls.append(("release", lock)))
    monkeypatch.setattr(
        ops,
        "_do_install",
        lambda actual, target, direction, output: calls.append((actual, target, direction, output)),
    )

    ops.downgrade("1.10.4", False, True)

    assert calls == [(info, "1.10.4", "downgrade", "table"), ("release", "lock")]


def backup_path(monkeypatch, tmp_path: Path, exists: bool) -> Path:
    monkeypatch.setattr(ops.config, "CONFIG_DIR", tmp_path)
    backup = tmp_path / "bin" / "observal.prev"
    if exists:
        backup.parent.mkdir(parents=True)
        backup.write_bytes(b"previous binary")
    return backup


def test_rollback_reports_missing_backups(cli, monkeypatch, tmp_path):
    from observal_cli import install_detector

    backup_path(monkeypatch, tmp_path, False)
    monkeypatch.setattr(install_detector, "detect", lambda: install_info(InstallMethod.BINARY, "curl"))

    with pytest.raises(typer.Exit) as raised:
        ops.rollback()

    assert raised.value.exit_code == 5
    assert "No CLI rollback backup" in cli.text()


def test_rollback_rejects_non_binary_installs(cli, monkeypatch, tmp_path):
    from observal_cli import install_detector

    backup_path(monkeypatch, tmp_path, True)
    monkeypatch.setattr(install_detector, "detect", lambda: install_info())

    with pytest.raises(typer.Exit) as raised:
        ops.rollback()

    assert raised.value.exit_code == 6
    assert "only supported for standalone binary" in cli.text()


def test_rollback_honors_declined_confirmation(cli, monkeypatch, tmp_path):
    from observal_cli import install_detector

    backup_path(monkeypatch, tmp_path, True)
    monkeypatch.setattr(
        install_detector,
        "detect",
        lambda: install_info(InstallMethod.BINARY, "curl", tmp_path / "observal"),
    )
    monkeypatch.setattr(ops.typer, "confirm", lambda prompt: False)

    with pytest.raises(typer.Abort):
        ops.rollback()


def test_rollback_atomically_restores_binary_and_returns_json(cli, monkeypatch, tmp_path):
    from observal_cli import install_detector, upgrade_lock

    backup = backup_path(monkeypatch, tmp_path, True)
    target = tmp_path / "observal"
    target.write_bytes(b"current binary")
    monkeypatch.setattr(
        install_detector,
        "detect",
        lambda: install_info(InstallMethod.BINARY, "curl", target),
    )
    calls = []
    monkeypatch.setattr(upgrade_lock, "acquire_lock", lambda scope: calls.append(("acquire", scope)) or "lock")
    monkeypatch.setattr(upgrade_lock, "release_lock", lambda lock: calls.append(("release", lock)))

    ops.rollback(True, "json")

    assert target.read_bytes() == backup.read_bytes()
    assert target.stat().st_mode & 0o777 == 0o755
    assert calls == [("acquire", "cli"), ("release", "lock")]
    assert cli.json == [{"action": "rollback", "status": "completed", "backup": str(backup), "path": str(target)}]
    assert cli.lines == []


@pytest.mark.parametrize(
    ("release", "newer", "message"),
    [
        ({"latest_version": "2.0.0"}, True, "update available"),
        ({"latest_version": "1.0.0"}, False, "up to date"),
        (None, False, "could not reach GitHub"),
    ],
)
def test_status_reports_update_availability(cli, monkeypatch, release, newer, message):
    from observal_cli import install_detector, version_check

    monkeypatch.setattr(version_check, "get_current_version", lambda: "1.0.0")
    monkeypatch.setattr(version_check, "_fetch_from_github", lambda: release)
    monkeypatch.setattr(version_check, "_is_newer", lambda latest, current: newer)
    monkeypatch.setattr(install_detector, "detect", lambda: install_info())

    ops.status()

    output = cli.text()
    assert "Version:  [bold]v1.0.0" in output
    assert "uv_tool" in output
    assert message in output


def test_self_status_json_and_command_inventory(cli, monkeypatch):
    from observal_cli import install_detector, version_check

    monkeypatch.setattr(version_check, "get_current_version", lambda: "1.0.0")
    monkeypatch.setattr(version_check, "_fetch_from_github", lambda: {"latest_version": "2.0.0"})
    monkeypatch.setattr(version_check, "_is_newer", lambda latest, current: True)
    monkeypatch.setattr(install_detector, "detect", lambda: install_info())

    ops.status("json")

    assert cli.json == [
        {
            "current_version": "1.0.0",
            "install_method": "uv_tool",
            "path": "/mock/observal",
            "writable": True,
            "managed_by": "uv",
            "github_available": True,
            "latest_version": "2.0.0",
            "update_available": True,
        }
    ]
    assert cli.lines == []

    command = get_command(cli_app).commands["self"]
    assert set(command.commands) == {"upgrade", "downgrade", "rollback", "status"}
    assert all(any(parameter.name == "output" for parameter in child.params) for child in command.commands.values())


def test_self_json_mutations_require_force(cli, monkeypatch, tmp_path):
    from observal_cli import install_detector, version_check

    monkeypatch.setattr(version_check, "get_current_version", lambda: "2.0.0")
    monkeypatch.setattr(install_detector, "detect", lambda: install_info())

    with pytest.raises(typer.Exit) as upgrade_error:
        ops.upgrade("3.0.0", False, False, "json")
    with pytest.raises(typer.Exit) as downgrade_error:
        ops.downgrade("1.10.4", False, False, "json")

    backup_path(monkeypatch, tmp_path, True)
    monkeypatch.setattr(
        install_detector,
        "detect",
        lambda: install_info(InstallMethod.BINARY, "curl", tmp_path / "observal"),
    )
    with pytest.raises(typer.Exit) as rollback_error:
        ops.rollback(False, "json")

    assert [upgrade_error.value.exit_code, downgrade_error.value.exit_code, rollback_error.value.exit_code] == [7, 7, 7]
    assert cli.lines == [
        "[red]JSON mode cannot prompt before upgrading the CLI.[/red]",
        "[red]JSON mode cannot prompt before downgrading the CLI.[/red]",
        "[red]JSON mode cannot prompt before rolling back the CLI.[/red]",
    ]


def test_self_json_install_failure_suppresses_executor_output(cli, monkeypatch):
    from observal_cli import upgrade_executor

    def fail_install(*args, **kwargs):
        print("sensitive installer detail")
        raise typer.Exit(1)

    monkeypatch.setattr(upgrade_executor, "execute", fail_install)

    with pytest.raises(typer.Exit) as raised:
        ops._do_install(object(), "2.0.0", "upgrade", "json")

    assert raised.value.exit_code == 9
    assert "sensitive installer detail" not in cli.text()
    assert cli.lines == ["[red]CLI upgrade failed.[/red]"]


def test_every_remaining_ops_workflow_has_output_and_dead_commands_are_removed():
    command = get_command(cli_app).commands["ops"]

    def leaves(group):
        for name, child in group.commands.items():
            if isinstance(child, Group) and child.commands:
                yield from leaves(child)
            else:
                yield name, child

    rows = list(leaves(command))
    assert len(rows) == 11
    assert all(any(parameter.name == "output" for parameter in leaf.params) for _name, leaf in rows)
    assert "metrics" not in command.commands
    assert "spans" not in command.commands
    assert "test" not in command.commands["telemetry"].commands


def test_telemetry_status_json_combines_server_and_outbox(cli, monkeypatch):
    from observal_cli import telemetry_buffer

    server = {"status": "ok", "tool_call_events": 2, "agent_interaction_events": 3}
    monkeypatch.setattr(ops.client, "get", lambda path: server)
    monkeypatch.setattr(
        telemetry_buffer,
        "stats",
        lambda: {
            "pending": 1,
            "failed": 0,
            "sent": 0,
            "total": 1,
            "oldest_pending": None,
            "last_sync": None,
            "bytes": 64,
        },
    )

    ops.telemetry_status("json")

    assert cli.json == [{"server": server, "outbox": {"available": True, **telemetry_buffer.stats()}}]
    assert cli.lines == []


def test_feedback_mutations_return_direct_json(cli, monkeypatch):
    listing_id = "11111111-1111-1111-1111-111111111111"
    post = []
    put = []
    delete = []
    monkeypatch.setattr(
        ops.client,
        "post",
        lambda path, body: post.append((path, body)) or {"id": "feedback-1", "rating": 5},
    )
    monkeypatch.setattr(ops.client, "get", lambda path: {"id": "feedback-1"})
    monkeypatch.setattr(
        ops.client,
        "put",
        lambda path, body: put.append((path, body)) or {"id": "feedback-1", "rating": 4},
    )
    monkeypatch.setattr(
        ops.client,
        "delete",
        lambda path: delete.append(path) or {},
    )

    ops._rate_impl(listing_id, 5, "mcp", "great", False, "json")
    ops._rate_update(listing_id, "mcp", 4, None, None, "json")
    ops._rate_delete(listing_id, "mcp", yes=True, output="json")

    assert cli.json == [
        {"id": "feedback-1", "rating": 5},
        {"id": "feedback-1", "rating": 4},
        {},
    ]
    assert post[0][0] == "/api/v1/feedback"
    assert put == [("/api/v1/feedback/feedback-1", {"rating": 4})]
    assert delete == ["/api/v1/feedback/feedback-1"]
    assert cli.lines == []


def test_traces_detail_json_uses_current_session_endpoints(cli, monkeypatch):
    sessions = [{"session_id": "session/one", "platform": "kiro"}]
    detail = {"session_id": "session/one", "events": [{"event_name": "tool_call"}]}
    calls = []

    def get(path, params=None):
        calls.append((path, params))
        return sessions if path == "/api/v1/sessions" else detail

    monkeypatch.setattr(ops.client, "get", get)

    ops._traces_impl("kiro", 7, 5, False, True, "json")

    assert calls == [
        ("/api/v1/sessions", {"limit": 5, "platform": "kiro", "days": 7}),
        ("/api/v1/sessions/session%2Fone", None),
    ]
    assert cli.json == [{"view": "span", "items": [{"summary": sessions[0], "detail": detail}]}]


def test_trace_detail_failure_propagates(cli, monkeypatch):
    monkeypatch.setattr(ops.client, "get", raises(RuntimeError("detail unavailable")))

    with pytest.raises(RuntimeError, match="detail unavailable"):
        ops._render_sessions_detail([{"session_id": "session"}], full=True)


def test_ops_type_validation_is_categorized(cli):
    with pytest.raises(typer.Exit) as raised:
        ops._top_impl("unknown", "json")

    assert raised.value.exit_code == 7
    assert "Unknown ranking type" in cli.text()


@pytest.mark.parametrize(
    "arguments",
    [
        [
            "ops",
            "rate-update",
            "11111111-1111-1111-1111-111111111111",
            "--output",
            "json",
        ],
        [
            "ops",
            "rate-delete",
            "11111111-1111-1111-1111-111111111111",
            "--output",
            "json",
        ],
        ["ops", "traces", "--platform", "unknown", "--output", "json"],
        ["ops", "insights", "generate", "agent", "--version", "bad", "--output", "json"],
        ["ops", "logs", "--level", "verbose", "--output", "json"],
    ],
)
def test_ops_json_validation_uses_shared_error_boundary(arguments):
    result = runner.invoke(cli_app, arguments)

    assert result.exit_code == 7
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "validation"


def test_every_admin_workflow_has_output_contract():
    command = get_command(cli_app).commands["admin"]

    def leaves(group):
        for name, child in group.commands.items():
            if isinstance(child, Group) and child.commands:
                yield from leaves(child)
            else:
                yield name, child

    rows = list(leaves(command))
    assert len(rows) == 25
    assert all(any(parameter.name == "output" for parameter in leaf.params) for _name, leaf in rows)


def test_admin_recommend_resolves_the_reference_and_sends_the_flag(cli, monkeypatch):
    sent = []
    ids = {"mcp": "11111111-1111-4111-8111-111111111111", "agent": "22222222-2222-4222-8222-222222222222"}
    monkeypatch.setattr(ops.client, "resolve_registry_reference", lambda kind, ref: ids[kind])
    monkeypatch.setattr(
        ops.client,
        "patch",
        lambda path, body: sent.append((path, body)) or {**body, "is_recommended": body["recommended"]},
    )

    ops.admin_recommend("MCP", "acme/github", False, "json")
    ops.admin_recommend("agent", "acme/reviewer", True, "table")

    assert sent == [
        ("/api/v1/admin/recommended", {"entity_type": "mcp", "entity_id": ids["mcp"], "recommended": True}),
        ("/api/v1/admin/recommended", {"entity_type": "agent", "entity_id": ids["agent"], "recommended": False}),
    ]
    assert cli.json[0]["is_recommended"] is True
    assert "acme/reviewer is no longer recommended" in cli.lines[-1]


def test_admin_recommend_resolves_a_bare_name_through_the_item_route(cli, monkeypatch):
    skill_id = "33333333-3333-4333-8333-333333333333"
    fetched, sent = [], []
    monkeypatch.setattr(ops.client, "resolve_registry_reference", lambda kind, ref: ref)
    monkeypatch.setattr(ops.client, "get", lambda path: fetched.append(path) or {"id": skill_id})
    monkeypatch.setattr(
        ops.client,
        "patch",
        lambda path, body: sent.append(body) or {**body, "is_recommended": body["recommended"]},
    )

    ops.admin_recommend("skill", "security review", False, "json")

    assert fetched == ["/api/v1/skills/security%20review"]
    assert sent == [{"entity_type": "skill", "entity_id": skill_id, "recommended": True}]


def test_admin_mutations_return_json_without_human_output(cli, monkeypatch):
    user = {"id": "user-id", "email": "alice@example.test", "name": "Alice", "role": "user"}
    put_results = {
        "/api/v1/admin/settings/secret": {"key": "secret", "value": "<redacted>", "is_sensitive": True},
        "/api/v1/admin/users/user-id/password": {"message": "Password reset", "generated_password": "secret"},
        "/api/v1/admin/trace-privacy": {"trace_privacy": True},
        "/api/v1/admin/users/user-id/role": {"id": "user-id", "email": "alice@example.test", "role": "admin"},
    }
    monkeypatch.setattr(
        ops.client, "get", lambda path: {"type": "mcp"} if path.startswith("/api/v1/review/") else [user]
    )
    monkeypatch.setattr(ops.client, "put", lambda path, body: put_results[path])
    monkeypatch.setattr(ops.client, "delete", lambda path: {})
    monkeypatch.setattr(
        ops.client,
        "post",
        lambda path, body=None: (
            {"cleared": 2}
            if path == "/api/v1/admin/cache/clear"
            else {"id": "review-id", "name": "component", "status": "approved"}
        ),
    )

    ops.admin_set("secret", "never-echo", "json")
    ops.admin_reset_password("alice@example.test", True, "json")
    ops.admin_delete_user("alice@example.test", True, "json")
    ops.admin_trace_privacy_set(True, "json")
    ops.admin_cache_clear("json")
    ops.admin_set_role("alice@example.test", "admin", "json")
    ops.review_approve("review-id", False, False, "json")

    assert cli.json == [
        {"key": "secret", "value": "<redacted>", "is_sensitive": True},
        {"message": "Password reset", "generated_password": "secret"},
        {"deleted": True, "id": "user-id", "email": "alice@example.test"},
        {"trace_privacy": True},
        {"cleared": 2},
        {"id": "user-id", "email": "alice@example.test", "role": "admin"},
        {"id": "review-id", "name": "component", "status": "approved"},
    ]
    assert "never-echo" not in cli.text()
    assert cli.lines == []


def test_admin_secret_creation_mutations_return_direct_json(cli, monkeypatch):
    responses = iter(
        [
            {"id": "saml-id", "active": False},
            {"id": "token-id", "token": "one-time-token", "description": "Okta"},
        ]
    )
    monkeypatch.setattr(ops.client, "put", lambda path, body: next(responses))
    monkeypatch.setattr(ops.client, "post", lambda path, body: next(responses))

    ops.admin_saml_config_set("idp", "https://idp.test/sso", None, "certificate", None, True, False, "json")
    ops.admin_scim_token_create("Okta", "json")

    assert cli.json == [
        {"id": "saml-id", "active": False},
        {"id": "token-id", "token": "one-time-token", "description": "Okta"},
    ]
    assert cli.lines == []


def test_admin_audit_json_export_stdout_and_atomic_file(cli, monkeypatch, tmp_path):
    export = {"audit_trail": [{"event_id": "event-1"}], "record_count": 1}
    calls = []
    monkeypatch.setattr(ops.client, "get", lambda path, params=None: calls.append((path, params)) or export)
    destination = tmp_path / "audit.json"

    ops.admin_audit_log_export("login", "alice@example.test", None, "json")
    ops.admin_audit_log_export(None, None, str(destination), "json")

    assert calls == [
        (
            "/api/v1/admin/audit-log/export",
            {"action": "login", "actor": "alice@example.test", "format": "json"},
        ),
        ("/api/v1/admin/audit-log/export", {"format": "json"}),
    ]
    assert cli.json == [
        export,
        {"path": str(destination), "format": "json", "record_count": 1},
    ]
    assert json.loads(destination.read_text()) == export
    assert cli.lines == []


@pytest.mark.parametrize(
    "arguments",
    [
        ["admin", "delete-user", "alice@example.test", "--output", "json"],
        ["admin", "reset-password", "alice@example.test", "--output", "json"],
        ["admin", "saml-config-delete", "--output", "json"],
        [
            "admin",
            "scim-token-revoke",
            "11111111-1111-1111-1111-111111111111",
            "--output",
            "json",
        ],
        ["admin", "create-user", "a@example.test", "Alice", "--role", "unknown", "--output", "json"],
        ["admin", "review", "approve", "item", "--agent", "--bundle", "--output", "json"],
        ["admin", "review", "list", "--type", "unknown", "--output", "json"],
        ["admin", "saml-config-set", "--output", "json"],
        ["admin", "audit-log", "--source", "unknown", "--output", "json"],
    ],
)
def test_admin_json_validation_uses_shared_error_boundary(arguments):
    result = runner.invoke(cli_app, arguments)

    assert result.exit_code == 7
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "validation"


def _folder_version(**overrides):
    files = [
        {"path": "SKILL.md", "size": 10, "sha256": "a" * 64, "mode": "0644"},
        {"path": "scripts/run.sh", "size": 5, "sha256": "b" * 64, "mode": "0755"},
    ]
    return {
        "name": "pdf-kit",
        "version_id": "ver-2",
        "revision": "r" * 64,
        "files": files,
        "base_version_id": "ver-1",
        "base_version": "1.0.0",
        "base_delivery_mode": "registry_direct",
        **overrides,
    }


def _folder_review_api(monkeypatch, version, *, base_files=None, detail=None):
    calls = SimpleNamespace(gets=[], posts=[])
    base_files = (
        base_files if base_files is not None else [{"path": "SKILL.md", "size": 9, "sha256": "c" * 64, "mode": "0644"}]
    )

    def get(path):
        calls.gets.append(path)
        if path.endswith("/manifest"):
            return {"files": base_files}
        if "/versions/" in path:
            return version
        return detail if detail is not None else {"type": "skill", "id": "listing-1", "version_id": "ver-2"}

    monkeypatch.setattr(ops.client, "get", get)
    monkeypatch.setattr(
        ops.client,
        "post",
        lambda path, body=None: calls.posts.append((path, body)) or {"version": "1.1.0", "new_status": "x"},
    )
    return calls


def test_folder_skill_approval_is_bound_to_the_observed_revision(cli, monkeypatch):
    calls = _folder_review_api(monkeypatch, _folder_version())

    ops.review_approve("pdf-kit", False, False, "table", None, "r" * 64, False, False)

    assert calls.posts == [
        (
            "/api/v1/review/skills/listing-1/versions/ver-2/decision",
            {"action": "approve", "observed_revision": "r" * 64},
        )
    ]
    assert "/api/v1/skills/listing-1/versions/ver-1/manifest" in calls.gets  # reviewer sees what changed
    assert "Approved: pdf-kit" in cli.text()


def test_folder_skill_approval_refuses_to_rubber_stamp_without_revision_or_yes(cli, monkeypatch):
    calls = _folder_review_api(monkeypatch, _folder_version())
    seen, real_fail = [], ops.fail
    monkeypatch.setattr(ops, "fail", lambda *args, **kwargs: seen.append(kwargs) or real_fail(*args, **kwargs))

    with pytest.raises(typer.Exit) as exc_info:
        ops.review_approve("pdf-kit", False, False, "table", None, None, False, False)

    assert exc_info.value.exit_code == 7
    assert "--revision " + "r" * 64 in seen[0]["remediation"]  # tells the reviewer exactly how to proceed
    assert calls.posts == []


def test_folder_skill_yes_accepts_the_listed_files(cli, monkeypatch):
    calls = _folder_review_api(monkeypatch, _folder_version())

    ops.review_approve("pdf-kit", False, False, "table", None, None, True, False)

    assert calls.posts[0][1] == {"action": "approve", "observed_revision": "r" * 64}


def test_folder_skill_refuses_a_stale_revision_before_posting(cli, monkeypatch):
    calls = _folder_review_api(monkeypatch, _folder_version())

    with pytest.raises(typer.Exit):
        ops.review_approve("pdf-kit", False, False, "table", None, "z" * 64, False, False)

    assert "changed" in cli.text() and calls.posts == []


def test_git_to_folder_conversion_needs_explicit_acknowledgement(cli, monkeypatch):
    version = _folder_version(base_delivery_mode="git_fetch", base_git_url="https://git.example/x", base_git_ref="main")
    calls = _folder_review_api(monkeypatch, version)

    with pytest.raises(typer.Exit) as exc_info:
        ops.review_approve("pdf-kit", False, False, "table", None, "r" * 64, False, False)
    assert exc_info.value.exit_code == 7 and calls.posts == []

    ops.review_approve("pdf-kit", False, False, "table", None, "r" * 64, False, True)
    assert calls.posts[0][1] == {"action": "approve", "observed_revision": "r" * 64, "git_base_acknowledged": True}
    assert not any(path.endswith("/manifest") for path in calls.gets)  # no stored Git files to compare


def test_folder_skill_rejection_sends_reason_and_revision(cli, monkeypatch):
    calls = _folder_review_api(monkeypatch, _folder_version())

    ops.review_reject("pdf-kit", "unsafe script", False, False, "table", None)

    assert calls.posts == [
        (
            "/api/v1/review/skills/listing-1/versions/ver-2/decision",
            {"action": "reject", "observed_revision": "r" * 64, "reason": "unsafe script"},
        )
    ]


def test_explicit_version_id_reviews_that_version_without_listing_lookup(cli, monkeypatch):
    calls = _folder_review_api(monkeypatch, _folder_version())

    ops.review_approve("pdf-kit", False, False, "table", "ver-2", "r" * 64, False, False)

    assert calls.gets[0] == "/api/v1/review/skills/pdf-kit/versions/ver-2"
    assert calls.posts[0][0] == "/api/v1/review/skills/pdf-kit/versions/ver-2/decision"


def test_legacy_skill_without_a_pending_version_keeps_the_listing_endpoint(cli, monkeypatch):
    calls = _folder_review_api(
        monkeypatch, _folder_version(), detail={"type": "skill", "id": "listing-1", "name": "git-skill"}
    )
    monkeypatch.setattr(ops.client, "post", lambda path, body=None: calls.posts.append(path) or {"name": "git-skill"})

    ops.review_approve("git-skill", False, False)

    assert calls.posts == ["/api/v1/review/git-skill/approve"]  # no version row to bind a revision to


def test_resubmitted_git_skill_is_reviewed_by_revision_even_without_files(cli, monkeypatch):
    # The server refuses the listing endpoint once review_epoch > 0, so a file-less Git version still needs this.
    version = {"name": "git-skill", "version_id": "ver-3", "revision": "r" * 64, "files": []}
    calls = _folder_review_api(monkeypatch, version)

    ops.review_approve("git-skill", False, False)  # no --revision/--yes: nothing to inspect, so no gate

    assert calls.posts == [
        (
            "/api/v1/review/skills/listing-1/versions/ver-2/decision",
            {"action": "approve", "observed_revision": "r" * 64},
        )
    ]
    assert not any(path.endswith("/manifest") for path in calls.gets)
    ops.review_reject("git-skill", "still broken", False, False, "table", "ver-3")
    assert calls.posts[-1][0].endswith("/versions/ver-3/decision") and calls.posts[-1][1]["action"] == "reject"


def test_review_show_lists_folder_revision_and_files(cli, monkeypatch):
    _folder_review_api(
        monkeypatch,
        _folder_version(),
        detail={"type": "skill", "id": "listing-1", "name": "pdf-kit", "status": "pending", "version_id": "ver-2"},
    )

    ops.review_show("pdf-kit", "json")

    shown = cli.json[0]
    assert shown["revision"] == "r" * 64 and [f["path"] for f in shown["files"]] == ["SKILL.md", "scripts/run.sh"]


def test_folder_changes_reports_every_kind_of_difference():
    base = [
        {"path": "a", "sha256": "1", "mode": "0644"},
        {"path": "b", "sha256": "2", "mode": "0644"},
        {"path": "c", "sha256": "3", "mode": "0644"},
    ]
    new = [
        {"path": "a", "sha256": "9", "mode": "0644"},
        {"path": "c", "sha256": "3", "mode": "0755"},
        {"path": "d", "sha256": "4", "mode": "0644"},
    ]
    assert ops._folder_changes(new, base) == {
        "added": ["d"],
        "removed": ["b"],
        "modified": ["a"],
        "mode_changed": ["c"],
    }
