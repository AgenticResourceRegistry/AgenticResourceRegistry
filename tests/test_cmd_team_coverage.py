# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Behavioral coverage for the teamspace CLI boundary."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
import typer
from click import Group
from typer.main import get_command
from typer.testing import CliRunner

import observal_cli.cmd_team as team
from observal_cli.main import app as cli_app

TEAM_ID = "11111111-1111-1111-1111-111111111111"
OTHER_TEAM_ID = "22222222-2222-2222-2222-222222222222"
INVITE_ID = "33333333-3333-3333-3333-333333333333"
REQUEST_ID = "44444444-4444-4444-4444-444444444444"

runner = CliRunner()


class FakeTable:
    """Capture table structure without depending on terminal width or color."""

    def __init__(self, *, title: str):
        self.title = title
        self.columns: list[tuple[str, dict]] = []
        self.rows: list[tuple[str, ...]] = []

    def add_column(self, name: str, **options) -> None:
        self.columns.append((name, options))

    def add_row(self, *values: str) -> None:
        self.rows.append(values)


def _blocked(name: str) -> Mock:
    def fail(*args, **kwargs):
        raise AssertionError(f"unmocked boundary: {name}, args={args}, kwargs={kwargs}")

    return Mock(side_effect=fail)


def _returns(boundary: Mock, value) -> Mock:
    boundary.side_effect = None
    boundary.return_value = value
    return boundary


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch):
    """Mock client, config, prompt, and rendering boundaries."""
    rendered: list[object] = []
    json_values: list[object] = []
    tables: list[FakeTable] = []

    client_boundaries = {
        name: _blocked(f"client.{name}") for name in ("get", "post", "patch", "delete", "resolve_team_id")
    }
    for name, boundary in client_boundaries.items():
        monkeypatch.setattr(team.client, name, boundary)

    config_boundaries = {name: _blocked(f"config.{name}") for name in ("load", "get_or_exit", "resolve_alias")}
    for name, boundary in config_boundaries.items():
        monkeypatch.setattr(team.client.config, name, boundary)

    confirm = _blocked("typer.confirm")
    monkeypatch.setattr(team.typer, "confirm", confirm)
    monkeypatch.setattr(team, "rprint", lambda value: rendered.append(value))
    monkeypatch.setattr(team, "output_json", json_values.append)

    def make_table(*, title: str) -> FakeTable:
        table = FakeTable(title=title)
        tables.append(table)
        return table

    monkeypatch.setattr(team, "Table", make_table)

    context = SimpleNamespace(
        client=SimpleNamespace(**client_boundaries),
        config=SimpleNamespace(**config_boundaries),
        confirm=confirm,
        rendered=rendered,
        json=json_values,
        tables=tables,
    )
    yield context

    for boundary in config_boundaries.values():
        boundary.assert_not_called()


def _resolve(cli, team_id: str = TEAM_ID) -> Mock:
    return _returns(cli.client.resolve_team_id, team_id)


def test_list_table_renders_personal_and_public_teamspaces_exactly(cli):
    rows = [
        {
            "id": TEAM_ID,
            "name": "Alice's Teamspace",
            "handle": "alice-team",
            "is_personal": True,
            "role": "owner",
            "member_count": None,
        },
        {
            "id": OTHER_TEAM_ID,
            "name": "Platform Tools",
            "handle": "platform-tools",
            "is_personal": False,
            "role": None,
            "member_count": 0,
        },
    ]
    get = _returns(cli.client.get, rows)

    team.list_teams(output="table", all_teams=False)

    get.assert_called_once_with("/api/v1/teams")
    assert cli.json == []
    assert len(cli.tables) == 1
    table = cli.tables[0]
    assert table.title == "Teamspaces"
    assert table.columns == [
        ("name", {"style": "cyan"}),
        ("handle", {"style": "green"}),
        ("role", {"style": "dim"}),
        ("members", {"style": "dim"}),
    ]
    assert table.rows == [
        ("Alice's Teamspace", "alice-team", "owner", "-"),
        ("Platform Tools", "platform-tools", "-", "0"),
    ]
    assert cli.rendered == [table]


def test_list_all_json_and_empty_output_use_exact_boundaries(cli):
    rows = [{"id": TEAM_ID, "name": "Platform", "handle": "platform"}]
    get = _returns(cli.client.get, rows)

    team.list_teams(output="json", all_teams=True)

    get.assert_called_once_with("/api/v1/teams/all")
    assert cli.json == [rows]
    assert cli.rendered == []
    assert cli.tables == []

    get.reset_mock()
    get.return_value = []
    cli.json.clear()
    team.list_teams(output="table", all_teams=False)

    get.assert_called_once_with("/api/v1/teams")
    assert cli.json == []
    assert cli.rendered == ["[dim]No teamspaces.[/dim]"]
    assert cli.tables == []


def test_show_handle_renders_detail_description_and_member_fallbacks(cli):
    detail = {
        "id": TEAM_ID,
        "name": "Platform Tools",
        "handle": "platform-tools",
        "description": "Internal tooling",
        "role": "owner",
    }
    members = [
        {"id": "user-1", "username": "alice", "email": "alice@example.test", "role": "owner"},
        {"id": "user-2", "username": None, "email": "bob@example.test", "role": "reviewer"},
    ]
    resolve = _resolve(cli)
    get = cli.client.get
    get.side_effect = [detail, members]

    team.show_team("@Platform-Tools", output="table")

    resolve.assert_called_once_with("@Platform-Tools")
    assert get.call_args_list == [
        call(f"/api/v1/teams/{TEAM_ID}"),
        call(f"/api/v1/teams/{TEAM_ID}/members"),
    ]
    table = cli.tables[0]
    assert table.title == "Members"
    assert table.columns == [("user", {"style": "cyan"}), ("role", {"style": "green"})]
    assert table.rows == [("@alice", "owner"), ("bob@example.test", "reviewer")]
    assert cli.rendered == [
        "[cyan]Platform Tools[/cyan]  [dim]platform-tools[/dim]",
        "[dim]Internal tooling[/dim]",
        "your role: [green]owner[/green]",
        table,
    ]


def test_show_uuid_json_preserves_personal_teamspace_and_members(cli):
    detail = {
        "id": TEAM_ID,
        "name": "Alice's Teamspace",
        "handle": "alice-team",
        "description": None,
        "visibility": "private",
        "is_personal": True,
        "role": None,
    }
    members = [{"id": "user-1", "email": "alice@example.test", "username": "alice", "role": "owner"}]
    resolve = _resolve(cli)
    get = cli.client.get
    get.side_effect = [detail, members]

    team.show_team(TEAM_ID, output="json")

    resolve.assert_called_once_with(TEAM_ID)
    assert get.call_args_list == [
        call(f"/api/v1/teams/{TEAM_ID}"),
        call(f"/api/v1/teams/{TEAM_ID}/members"),
    ]
    assert cli.json == [{"team": detail, "members": members}]
    assert cli.rendered == []
    assert cli.tables == []


def test_show_without_description_uses_role_fallback(cli):
    detail = {"name": "Platform", "handle": "platform", "description": None, "role": None}
    _resolve(cli)
    cli.client.get.side_effect = [detail, []]

    team.show_team("platform", output="table")

    table = cli.tables[0]
    assert table.rows == []
    assert cli.rendered == [
        "[cyan]Platform[/cyan]  [dim]platform[/dim]",
        "your role: [green]-[/green]",
        table,
    ]


def test_create_sends_full_and_minimal_payloads_with_exact_plain_output(cli):
    post = _returns(
        cli.client.post,
        {
            "id": TEAM_ID,
            "name": "Platform Tools",
            "handle": "platform-tools",
            "visibility": "private",
        },
    )

    team.create_team("Platform Tools", "platform-tools", "Internal tooling", "private")

    post.assert_called_once_with(
        "/api/v1/teams",
        json_data={
            "name": "Platform Tools",
            "visibility": "private",
            "handle": "platform-tools",
            "description": "Internal tooling",
        },
    )
    assert cli.rendered == [
        f"[green]Created teamspace:[/green] Platform Tools ([dim]platform-tools[/dim]) private id={TEAM_ID}"
    ]

    post.reset_mock()
    post.return_value = {"id": OTHER_TEAM_ID, "name": "SRE", "handle": "sre"}
    cli.rendered.clear()
    team.create_team("SRE", None, None, "public")

    post.assert_called_once_with("/api/v1/teams", json_data={"name": "SRE", "visibility": "public"})
    assert cli.rendered == [f"[green]Created teamspace:[/green] SRE ([dim]sre[/dim]) public id={OTHER_TEAM_ID}"]


@pytest.mark.parametrize("command", ["create", "visibility"])
def test_visibility_validation_is_local_and_exact(cli, command):
    if command == "create":
        result = runner.invoke(team.team_app, ["create", "Platform", "--visibility", "secret"])
    else:
        result = runner.invoke(team.team_app, ["visibility", "set", "platform", "secret"])

    assert result.exit_code == 7
    assert cli.rendered == ["[red]Unknown teamspace visibility: secret.[/red]"]
    cli.client.resolve_team_id.assert_not_called()
    cli.client.post.assert_not_called()
    cli.client.patch.assert_not_called()


def test_visibility_update_resolves_handle_and_patches_exact_payload(cli):
    resolve = _resolve(cli)
    patch = _returns(cli.client.patch, {"visibility": "private"})

    team.set_visibility("platform-tools", "private")

    resolve.assert_called_once_with("platform-tools")
    patch.assert_called_once_with(
        f"/api/v1/teams/{TEAM_ID}/visibility",
        json_data={"visibility": "private"},
    )
    table = cli.tables[0]
    assert table.title == "Teamspace visibility"
    assert table.rows == [("", "", "private", "-")]
    assert cli.rendered == [table]


def test_delete_cancellation_stops_before_http_mutation(cli):
    resolve = _resolve(cli)
    cli.confirm.side_effect = None
    cli.confirm.return_value = False

    with pytest.raises(typer.Abort):
        team.delete_team("platform-tools", yes=False)

    resolve.assert_called_once_with("platform-tools")
    cli.confirm.assert_called_once_with("Delete teamspace 'platform-tools'? This cannot be undone.")
    cli.client.delete.assert_not_called()
    assert cli.rendered == []


def test_delete_confirmation_and_yes_bypass_have_exact_behavior(cli):
    resolve = _resolve(cli)
    delete = _returns(cli.client.delete, {})
    cli.confirm.side_effect = None
    cli.confirm.return_value = True

    team.delete_team("platform-tools", yes=False)

    cli.confirm.assert_called_once_with("Delete teamspace 'platform-tools'? This cannot be undone.")
    delete.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}")
    assert cli.rendered == ["[green]Teamspace deleted.[/green]"]

    resolve.reset_mock()
    delete.reset_mock()
    cli.confirm.reset_mock()
    cli.rendered.clear()
    team.delete_team(TEAM_ID, yes=True)

    resolve.assert_called_once_with(TEAM_ID)
    cli.confirm.assert_not_called()
    delete.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}")
    assert cli.rendered == ["[green]Teamspace deleted.[/green]"]


def test_leave_posts_exact_endpoint_and_plain_output(cli):
    resolve = _resolve(cli)
    post = _returns(cli.client.post, {})

    team.leave_team("platform-tools", yes=True)

    resolve.assert_called_once_with("platform-tools")
    post.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}/leave")
    assert cli.rendered == ["[green]Left teamspace.[/green]"]


def test_member_list_table_and_json_outputs_are_exact(cli):
    rows = [
        {"id": "user-1", "username": "alice", "email": "alice@example.test", "role": "owner"},
        {"id": "user-2", "username": None, "email": "bob@example.test", "role": "member"},
    ]
    resolve = _resolve(cli)
    get = _returns(cli.client.get, rows)

    team.list_members("platform-tools", output="table")

    resolve.assert_called_once_with("platform-tools")
    get.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}/members")
    table = cli.tables[0]
    assert table.title == "Members"
    assert table.columns == [
        ("user", {"style": "cyan"}),
        ("email", {"style": "dim"}),
        ("role", {"style": "green"}),
    ]
    assert table.rows == [
        ("@alice", "alice@example.test", "owner"),
        ("-", "bob@example.test", "member"),
    ]
    assert cli.rendered == [table]

    resolve.reset_mock()
    get.reset_mock()
    cli.rendered.clear()
    cli.tables.clear()
    team.list_members(TEAM_ID, output="json")

    resolve.assert_called_once_with(TEAM_ID)
    get.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}/members")
    assert cli.json == [rows]
    assert cli.rendered == []
    assert cli.tables == []


def test_invite_create_sends_optional_fields_only_when_supplied(cli):
    resolve = _resolve(cli)
    post = _returns(cli.client.post, {"url": "https://example.test/team-invites/token-one"})

    team.create_invite("platform-tools", "Hiring", 30, 5)

    resolve.assert_called_once_with("platform-tools")
    post.assert_called_once_with(
        f"/api/v1/teams/{TEAM_ID}/invites",
        json_data={"expires_in_days": 30, "name": "Hiring", "max_uses": 5},
    )
    assert cli.rendered == ["https://example.test/team-invites/token-one"]

    resolve.reset_mock()
    post.reset_mock()
    post.return_value = {"url": "https://example.test/team-invites/token-two"}
    cli.rendered.clear()
    team.create_invite(TEAM_ID, None, 7, None)

    resolve.assert_called_once_with(TEAM_ID)
    post.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}/invites", json_data={"expires_in_days": 7})
    assert cli.rendered == ["https://example.test/team-invites/token-two"]


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (["--expires-days", "0"], "0 is not in the range 1<=x<=365"),
        (["--expires-days", "366"], "366 is not in the range 1<=x<=365"),
        (["--max-uses", "0"], "0 is not in the range 1<=x<=10000"),
    ],
)
def test_invite_numeric_validation_cancels_before_resolution(cli, options, message):
    result = runner.invoke(team.team_app, ["invite", "create", "platform", *options])

    assert result.exit_code == 2
    assert message in result.output
    cli.client.resolve_team_id.assert_not_called()
    cli.client.post.assert_not_called()


def test_invite_list_table_formats_bounded_and_unbounded_usage(cli):
    rows = [
        {
            "id": INVITE_ID,
            "name": "Hiring",
            "state": "active",
            "use_count": 2,
            "max_uses": 5,
            "expires_at": "2026-12-31T00:00:00Z",
            "invited_by_username": "alice",
        },
        {
            "id": "invite-two",
            "name": "",
            "state": "revoked",
            "max_uses": None,
            "expires_at": "2026-11-30T00:00:00Z",
            "invited_by_username": None,
        },
    ]
    resolve = _resolve(cli)
    get = _returns(cli.client.get, rows)

    team.list_invites("platform-tools", output="table")

    resolve.assert_called_once_with("platform-tools")
    get.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}/invites")
    table = cli.tables[0]
    assert table.title == "Private-team invite links"
    assert table.columns == [
        ("id", {"style": "dim"}),
        ("name", {}),
        ("state", {"style": "green"}),
        ("uses", {}),
        ("expires", {}),
        ("created by", {}),
    ]
    assert table.rows == [
        (INVITE_ID, "Hiring", "active", "2 / 5", "2026-12-31T00:00:00Z", "alice"),
        ("invite-two", "", "revoked", "0", "2026-11-30T00:00:00Z", "-"),
    ]
    assert cli.rendered == [table]


def test_invite_list_json_and_empty_output_are_exact(cli):
    rows = [{"id": INVITE_ID, "state": "active"}]
    _resolve(cli)
    get = _returns(cli.client.get, rows)

    team.list_invites(TEAM_ID, output="json")

    assert cli.json == [rows]
    assert cli.rendered == []
    assert cli.tables == []

    get.return_value = []
    cli.json.clear()
    team.list_invites(TEAM_ID, output="table")

    assert cli.json == []
    assert cli.rendered == ["[dim]No invite links.[/dim]"]
    assert cli.tables == []


def test_invite_revoke_posts_exact_endpoint_and_state(cli):
    resolve = _resolve(cli)
    post = _returns(cli.client.post, {"state": "revoked"})

    team.revoke_invite("platform-tools", INVITE_ID, yes=True)

    resolve.assert_called_once_with("platform-tools")
    post.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}/invites/{INVITE_ID}/revoke")
    assert cli.rendered == ["[green]Invite revoked.[/green]"]


def test_join_request_creation_with_and_without_message_is_exact(cli):
    resolve = _resolve(cli)
    post = _returns(cli.client.post, {"id": REQUEST_ID, "status": "pending"})

    team.request_join("platform-tools", "I maintain pager duty")

    resolve.assert_called_once_with("platform-tools")
    post.assert_called_once_with(
        f"/api/v1/teams/{TEAM_ID}/join-requests",
        json_data={"message": "I maintain pager duty"},
    )
    table = cli.tables[0]
    assert table.title == "Join request"
    assert table.rows == [("-", "pending", "-", "-", "-")]
    assert cli.rendered == [table]

    resolve.reset_mock()
    post.reset_mock()
    cli.rendered.clear()
    cli.tables.clear()
    team.request_join(TEAM_ID, None)

    resolve.assert_called_once_with(TEAM_ID)
    post.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}/join-requests", json_data={})
    assert cli.rendered == [cli.tables[0]]


def test_join_request_table_formats_requesters_decisions_and_fallbacks(cli):
    rows = [
        {
            "id": REQUEST_ID,
            "username": "bob",
            "email": "bob@example.test",
            "status": "rejected",
            "message": "Please add me",
            "decided_by_username": "alice",
            "decision_reason": "Use SRE",
        },
        {
            "id": "request-two",
            "username": None,
            "email": "carol@example.test",
            "status": "pending",
            "message": None,
            "decided_by_username": None,
            "decision_reason": None,
        },
    ]
    resolve = _resolve(cli)
    get = _returns(cli.client.get, rows)

    team.list_join_requests("platform-tools", status="pending", output="table")

    resolve.assert_called_once_with("platform-tools")
    get.assert_called_once_with(
        f"/api/v1/teams/{TEAM_ID}/join-requests",
        params={"status": "pending"},
    )
    table = cli.tables[0]
    assert table.title == "Join requests"
    assert table.columns == [
        ("user", {"style": "cyan"}),
        ("status", {"style": "green"}),
        ("message", {"style": "dim"}),
        ("decided by", {"style": "dim"}),
        ("reason", {"style": "dim"}),
    ]
    assert table.rows == [
        ("@bob", "rejected", "Please add me", "@alice", "Use SRE"),
        ("carol@example.test", "pending", "-", "-", "-"),
    ]
    assert cli.rendered == [table]


def test_join_request_json_and_empty_outputs_forward_unfiltered_params(cli):
    rows = [{"id": REQUEST_ID, "status": "approved"}]
    _resolve(cli)
    get = _returns(cli.client.get, rows)

    team.list_join_requests(TEAM_ID, status=None, output="json")

    get.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}/join-requests", params=None)
    assert cli.json == [rows]
    assert cli.rendered == []

    get.reset_mock()
    get.return_value = []
    cli.json.clear()
    team.list_join_requests(TEAM_ID, status=None, output="table")

    get.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}/join-requests", params=None)
    assert cli.json == []
    assert cli.rendered == ["[dim]No join requests.[/dim]"]
    assert cli.tables == []


def test_approve_resolves_pending_username_case_insensitively(cli):
    resolve = _resolve(cli)
    get = _returns(
        cli.client.get,
        [
            {"id": REQUEST_ID, "username": "Alice", "email": "alice@example.test", "status": "pending"},
            {"id": "other", "username": "bob", "email": "bob@example.test", "status": "pending"},
        ],
    )
    post = _returns(cli.client.post, {"status": "approved"})

    team.approve_join_request("platform-tools", "@ALICE")

    resolve.assert_called_once_with("platform-tools")
    get.assert_called_once_with(
        f"/api/v1/teams/{TEAM_ID}/join-requests",
        params={"status": "pending"},
    )
    post.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}/join-requests/{REQUEST_ID}/approve")
    table = cli.tables[0]
    assert table.title == "Approved join request"
    assert table.rows == [("-", "approved", "-", "-", "-")]
    assert cli.rendered == [table]


@pytest.mark.parametrize(("reason", "payload"), [("Not yet", {"reason": "Not yet"}), (None, {})])
def test_reject_matches_email_and_sends_optional_reason(cli, reason, payload):
    _resolve(cli)
    _returns(
        cli.client.get,
        [{"id": REQUEST_ID, "username": "alice", "email": "User@Example.Test", "status": "pending"}],
    )
    post = _returns(cli.client.post, {"status": "rejected"})

    team.reject_join_request("platform-tools", "user@example.test", reason)

    post.assert_called_once_with(
        f"/api/v1/teams/{TEAM_ID}/join-requests/{REQUEST_ID}/reject",
        json_data=payload,
    )
    table = cli.tables[0]
    assert table.title == "Rejected join request"
    assert table.rows == [("-", "rejected", "-", "-", "-")]
    assert cli.rendered == [table]


def test_missing_pending_request_is_a_parameter_error_and_never_decides(cli):
    _resolve(cli)
    _returns(
        cli.client.get,
        [{"id": "other", "username": None, "email": "other@example.test", "status": "pending"}],
    )

    with pytest.raises(typer.Exit) as raised:
        team.approve_join_request("platform-tools", "@missing")

    assert raised.value.exit_code == 5
    cli.client.post.assert_not_called()
    assert cli.rendered == ["[red]No pending join request from @missing.[/red]"]


@pytest.mark.parametrize(
    ("user", "role", "payload", "response", "message"),
    [
        (
            "Alice@Example.Test",
            "reviewer",
            {"role": "reviewer", "email": "alice@example.test"},
            {"email": "alice@example.test", "role": "reviewer"},
            "[green]Member saved:[/green] alice@example.test as reviewer",
        ),
        (
            "@Bob",
            "owner",
            {"role": "owner", "username": "Bob"},
            {"email": "bob@example.test", "role": "owner"},
            "[green]Member saved:[/green] bob@example.test as owner",
        ),
        (
            "carol",
            "member",
            {"role": "member", "username": "carol"},
            {"role": "member"},
            "[green]Member saved:[/green] carol as member",
        ),
    ],
)
def test_member_add_and_role_updates_send_exact_identity_payload(cli, user, role, payload, response, message):
    resolve = _resolve(cli)
    post = _returns(cli.client.post, response)

    team.add_member("platform-tools", user, role)

    resolve.assert_called_once_with("platform-tools")
    post.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}/members", json_data=payload)
    assert cli.rendered == [message]


def test_member_remove_cancellation_is_side_effect_free(cli):
    resolve = _resolve(cli)
    get = _returns(
        cli.client.get,
        [{"id": "user-1", "username": "bob", "email": "bob@example.test", "role": "member"}],
    )
    cli.confirm.side_effect = None
    cli.confirm.return_value = False

    with pytest.raises(typer.Abort):
        team.remove_member("platform-tools", "@bob", yes=False)

    resolve.assert_called_once_with("platform-tools")
    get.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}/members")
    cli.confirm.assert_called_once_with("Remove @bob from this team?")
    cli.client.delete.assert_not_called()
    assert cli.rendered == []


def test_member_remove_matches_email_case_insensitively_and_deletes_exact_id(cli):
    _resolve(cli)
    _returns(
        cli.client.get,
        [
            {"id": "user-1", "username": "alice", "email": "alice@example.test", "role": "owner"},
            {"id": "user-2", "username": "bob", "email": "Bob@Example.Test", "role": "member"},
        ],
    )
    delete = _returns(cli.client.delete, {})

    team.remove_member("platform-tools", "bob@example.test", yes=True)

    cli.confirm.assert_not_called()
    delete.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}/members/user-2")
    assert cli.rendered == ["[green]Member removed.[/green]"]


def test_member_remove_rejects_unknown_user_before_prompt_or_delete(cli):
    _resolve(cli)
    _returns(
        cli.client.get,
        [{"id": "user-1", "username": None, "email": "alice@example.test", "role": "owner"}],
    )

    with pytest.raises(typer.Exit) as raised:
        team.remove_member("platform-tools", "@missing", yes=False)

    assert raised.value.exit_code == 5
    cli.confirm.assert_not_called()
    cli.client.delete.assert_not_called()
    assert cli.rendered == ["[red]Team member not found: @missing.[/red]"]


def test_list_http_failure_propagates_without_rendering(cli):
    failure = typer.Exit(1)
    cli.client.get.side_effect = failure

    with pytest.raises(typer.Exit) as raised:
        team.list_teams(output="table", all_teams=False)

    assert raised.value is failure
    cli.client.get.assert_called_once_with("/api/v1/teams")
    assert cli.rendered == []
    assert cli.json == []
    assert cli.tables == []


def test_show_second_http_failure_is_atomic(cli):
    failure = typer.Exit(1)
    _resolve(cli)
    cli.client.get.side_effect = [{"name": "Platform", "handle": "platform", "role": "owner"}, failure]

    with pytest.raises(typer.Exit) as raised:
        team.show_team("platform", output="table")

    assert raised.value is failure
    assert cli.client.get.call_args_list == [
        call(f"/api/v1/teams/{TEAM_ID}"),
        call(f"/api/v1/teams/{TEAM_ID}/members"),
    ]
    assert cli.rendered == []
    assert cli.tables == []


@pytest.mark.parametrize(
    ("method", "invoke", "expected"),
    [
        (
            "post",
            lambda: team.create_team("Platform", None, None, "public"),
            call("/api/v1/teams", json_data={"name": "Platform", "visibility": "public"}),
        ),
        (
            "patch",
            lambda: team.set_visibility("platform", "private"),
            call(f"/api/v1/teams/{TEAM_ID}/visibility", json_data={"visibility": "private"}),
        ),
        (
            "delete",
            lambda: team.delete_team("platform", yes=True),
            call(f"/api/v1/teams/{TEAM_ID}"),
        ),
        (
            "post",
            lambda: team.leave_team("platform", yes=True),
            call(f"/api/v1/teams/{TEAM_ID}/leave"),
        ),
        (
            "post",
            lambda: team.create_invite("platform", None, 7, None),
            call(f"/api/v1/teams/{TEAM_ID}/invites", json_data={"expires_in_days": 7}),
        ),
        (
            "post",
            lambda: team.revoke_invite("platform", INVITE_ID, yes=True),
            call(f"/api/v1/teams/{TEAM_ID}/invites/{INVITE_ID}/revoke"),
        ),
        (
            "post",
            lambda: team.request_join("platform", None),
            call(f"/api/v1/teams/{TEAM_ID}/join-requests", json_data={}),
        ),
        (
            "post",
            lambda: team.add_member("platform", "@alice", "member"),
            call(f"/api/v1/teams/{TEAM_ID}/members", json_data={"role": "member", "username": "alice"}),
        ),
    ],
    ids=("create", "visibility", "delete", "leave", "invite-create", "invite-revoke", "join", "member-add"),
)
def test_http_mutation_failures_never_render_success(cli, method, invoke, expected):
    _resolve(cli)
    failure = typer.Exit(1)
    boundary = getattr(cli.client, method)
    boundary.side_effect = failure

    with pytest.raises(typer.Exit) as raised:
        invoke()

    assert raised.value is failure
    assert boundary.call_args == expected
    assert cli.rendered == []
    assert cli.json == []
    assert cli.tables == []


def test_resolution_failure_stops_before_http_and_rendering(cli):
    failure = typer.Exit(1)
    cli.client.resolve_team_id.side_effect = failure

    with pytest.raises(typer.Exit) as raised:
        team.leave_team("missing")

    assert raised.value is failure
    cli.client.resolve_team_id.assert_called_once_with("missing")
    cli.client.post.assert_not_called()
    assert cli.rendered == []


def test_every_team_leaf_has_shared_output_option():
    command = get_command(cli_app).commands["team"]

    def leaves(group):
        for child in group.commands.values():
            if isinstance(child, Group):
                yield from leaves(child)
            else:
                yield child

    assert len(list(leaves(command))) == 26
    assert all(any(parameter.name == "output" for parameter in leaf.params) for leaf in leaves(command))


def test_team_lifecycle_mutations_return_direct_json(cli):
    resolve = _resolve(cli)
    post = _returns(cli.client.post, {"id": TEAM_ID, "name": "Platform", "handle": "platform"})
    patch = _returns(cli.client.patch, {"id": TEAM_ID, "visibility": "private"})
    delete = _returns(cli.client.delete, {})

    team.create_team("Platform", "platform", None, "public", "json")
    team.set_visibility("platform", "private", "json")
    team.delete_team("platform", yes=True, output="json")
    post.return_value = {}
    team.leave_team("platform", yes=True, output="json")

    assert cli.json == [
        {"id": TEAM_ID, "name": "Platform", "handle": "platform"},
        {"id": TEAM_ID, "visibility": "private"},
        {},
        {},
    ]
    assert cli.rendered == []
    assert cli.confirm.call_count == 0
    assert resolve.call_count == 3
    delete.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}")


def test_member_mutations_return_direct_json(cli):
    _resolve(cli)
    post = _returns(
        cli.client.post,
        {"id": "user-1", "email": "alice@example.test", "username": "alice", "role": "reviewer"},
    )
    get = _returns(
        cli.client.get,
        [{"id": "user-1", "email": "alice@example.test", "username": "alice", "role": "reviewer"}],
    )
    delete = _returns(cli.client.delete, {})

    team.add_member("platform", "alice@example.test", "reviewer", "json")
    team.remove_member("platform", "@ALICE", yes=True, output="json")

    assert cli.json == [
        {"id": "user-1", "email": "alice@example.test", "username": "alice", "role": "reviewer"},
        {},
    ]
    post.assert_called_once_with(
        f"/api/v1/teams/{TEAM_ID}/members",
        json_data={"role": "reviewer", "email": "alice@example.test"},
    )
    get.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}/members")
    delete.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}/members/user-1")
    cli.confirm.assert_not_called()


def test_invite_mutations_return_direct_json(cli):
    _resolve(cli)
    created = {
        "id": INVITE_ID,
        "state": "active",
        "token": "secret-token",
        "url": "https://example.test/team-invites/secret-token",
    }
    post = cli.client.post
    post.side_effect = [created, {"id": INVITE_ID, "state": "revoked"}]

    team.create_invite("platform", "Onboarding", 30, 5, "json")
    team.revoke_invite("platform", INVITE_ID, yes=True, output="json")

    assert cli.json == [created, {"id": INVITE_ID, "state": "revoked"}]
    assert post.call_args_list == [
        call(
            f"/api/v1/teams/{TEAM_ID}/invites",
            json_data={"expires_in_days": 30, "name": "Onboarding", "max_uses": 5},
        ),
        call(f"/api/v1/teams/{TEAM_ID}/invites/{INVITE_ID}/revoke"),
    ]
    cli.confirm.assert_not_called()


def test_join_request_mutations_return_direct_json(cli):
    _resolve(cli)
    get = _returns(
        cli.client.get,
        [{"id": REQUEST_ID, "username": "alice", "email": "alice@example.test", "status": "pending"}],
    )
    post = cli.client.post
    post.side_effect = [
        {"id": REQUEST_ID, "status": "pending"},
        {"id": REQUEST_ID, "status": "approved"},
        {"id": REQUEST_ID, "status": "rejected"},
    ]

    team.request_join("platform", "Please add me", "json")
    team.approve_join_request("platform", "@alice", "json")
    team.reject_join_request("platform", "alice@example.test", "Not now", "json")

    assert cli.json == [
        {"id": REQUEST_ID, "status": "pending"},
        {"id": REQUEST_ID, "status": "approved"},
        {"id": REQUEST_ID, "status": "rejected"},
    ]
    assert get.call_count == 2
    assert post.call_args_list[-2:] == [
        call(f"/api/v1/teams/{TEAM_ID}/join-requests/{REQUEST_ID}/approve"),
        call(
            f"/api/v1/teams/{TEAM_ID}/join-requests/{REQUEST_ID}/reject",
            json_data={"reason": "Not now"},
        ),
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        ["team", "create", "Platform", "--visibility", "secret", "--output", "json"],
        ["team", "delete", "platform", "--output", "json"],
        ["team", "leave", "platform", "--output", "json"],
        ["team", "members", "add", "platform", "alice", "--role", "admin", "--output", "json"],
        ["team", "members", "remove", "platform", "alice", "--output", "json"],
        ["team", "request", "list", "platform", "--status", "unknown", "--output", "json"],
        ["team", "invite", "revoke", "platform", INVITE_ID, "--output", "json"],
        ["team", "invite", "revoke", "platform", "not-a-uuid", "--yes", "--output", "json"],
    ],
)
def test_team_json_validation_uses_shared_error_boundary(arguments):
    result = runner.invoke(cli_app, arguments)

    assert result.exit_code == 7
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "validation"


def test_team_tables_escape_server_markup(cli):
    _returns(
        cli.client.get,
        [{"name": "Platform [prod]", "handle": "platform[/x]", "role": "owner", "member_count": 1}],
    )

    team.list_teams(output="table", all_teams=False)

    assert cli.tables[0].rows == [("Platform \\[prod]", "platform\\[/x]", "owner", "1")]


def test_claim_personal_teamspace_table_and_json(cli):
    response = {
        "id": TEAM_ID,
        "name": "Alice's Teamspace",
        "handle": "alice-team",
        "visibility": "private",
        "visibility_request_status": None,
    }
    post = _returns(cli.client.post, response)

    team.claim_personal_teamspace(output="table")

    post.assert_called_once_with("/api/v1/teams/claim-personal")
    table = cli.tables[0]
    assert table.title == "Personal teamspace"
    assert table.rows == [("Alice's Teamspace", "alice-team", "private", "-")]
    assert cli.rendered == [table]

    post.reset_mock()
    cli.rendered.clear()
    cli.tables.clear()
    team.claim_personal_teamspace(output="json")

    post.assert_called_once_with("/api/v1/teams/claim-personal")
    assert cli.json == [response]
    assert cli.rendered == []


def test_visibility_request_list_table_and_empty_json(cli):
    rows = [
        {
            "team_id": TEAM_ID,
            "name": "Platform Tools",
            "handle": "platform-tools",
            "requested_by_username": "alice",
            "requested_at": "2026-03-20T10:00:00Z",
        }
    ]
    get = _returns(cli.client.get, rows)

    team.list_visibility_requests(output="table")

    get.assert_called_once_with("/api/v1/teams/visibility-requests")
    table = cli.tables[0]
    assert table.title == "Pending public visibility requests"
    assert table.rows == [("Platform Tools", "platform-tools", "@alice", "2026-03-20T10:00:00Z")]
    assert cli.rendered == [table]

    get.reset_mock()
    get.return_value = []
    cli.rendered.clear()
    cli.tables.clear()
    team.list_visibility_requests(output="json")

    get.assert_called_once_with("/api/v1/teams/visibility-requests")
    assert cli.json == [[]]
    assert cli.rendered == []


def test_visibility_review_decisions_use_direct_json_and_tables(cli):
    resolve = _resolve(cli)
    approved = {
        "id": TEAM_ID,
        "name": "Platform Tools",
        "handle": "platform-tools",
        "visibility": "public",
        "visibility_request_status": "approved",
    }
    rejected = {
        **approved,
        "visibility": "private",
        "visibility_request_status": "rejected",
    }
    post = cli.client.post
    post.side_effect = [approved, rejected]

    team.approve_visibility_request("platform-tools", output="json")
    team.reject_visibility_request("platform-tools", reason="Needs context", output="table")

    assert resolve.call_count == 2
    assert post.call_args_list == [
        call(f"/api/v1/teams/{TEAM_ID}/visibility-request/approve"),
        call(
            f"/api/v1/teams/{TEAM_ID}/visibility-request/reject",
            json_data={"reason": "Needs context"},
        ),
    ]
    assert cli.json == [approved]
    table = cli.tables[0]
    assert table.title == "Visibility request rejected"
    assert table.rows == [("Platform Tools", "platform-tools", "private", "rejected")]
    assert cli.rendered == [table]


def test_invite_preview_table_json_and_secret_redaction(cli):
    token = "secret-invite-token"
    response = {
        "valid": True,
        "invite_state": "active",
        "is_member": False,
        "team_id": TEAM_ID,
        "team_name": "Platform Tools",
        "team_handle": "platform-tools",
        "invited_by": "Alice",
        "request": {"id": REQUEST_ID, "status": "pending"},
    }
    post = _returns(cli.client.post, response)

    team.preview_invite(token, output="table")

    post.assert_called_once_with(
        "/api/v1/teams/invites/preview",
        json_data={"token": token},
        operation="Preview teamspace invite",
        resource="teamspace invitation",
    )
    table = cli.tables[0]
    assert table.title == "Team invitation"
    assert table.rows == [("Platform Tools", "platform-tools", "active", "no", "pending", "Alice")]
    assert token not in repr(cli.rendered)

    post.reset_mock()
    cli.rendered.clear()
    cli.tables.clear()
    team.preview_invite(token, output="json")

    assert cli.json == [response]
    assert cli.rendered == []


def test_invite_request_previews_then_creates_pending_request(cli):
    token = "secret-invite-token"
    preview = {"valid": True, "team_id": TEAM_ID}
    response = {"id": REQUEST_ID, "status": "pending", "message": "Please add me"}
    post = cli.client.post
    post.side_effect = [preview, response]

    team.request_via_invite(token, message="Please add me", output="json")

    assert post.call_args_list == [
        call(
            "/api/v1/teams/invites/preview",
            json_data={"token": token},
            operation="Preview teamspace invite",
            resource="teamspace invitation",
        ),
        call(
            f"/api/v1/teams/{TEAM_ID}/join-requests",
            json_data={"invite_token": token, "message": "Please add me"},
            operation="Request teamspace access via invite",
            resource="teamspace invitation",
        ),
    ]
    assert cli.json == [response]
    assert cli.rendered == []


def test_invite_request_rejects_invalid_preview_without_leaking_token(cli):
    token = "secret-invite-token"
    post = _returns(cli.client.post, {"valid": False})

    with pytest.raises(typer.Exit) as raised:
        team.request_via_invite(token, message=None, output="table")

    assert raised.value.exit_code == 5
    assert post.call_count == 1
    assert token not in repr(cli.rendered)
    assert cli.rendered == ["[red]Invitation is invalid or unavailable.[/red]"]


def test_invite_delete_and_request_audit_outputs(cli):
    _resolve(cli)
    delete = _returns(cli.client.delete, {})

    team.delete_invite("platform-tools", INVITE_ID, yes=True, output="json")

    delete.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}/invites/{INVITE_ID}")
    assert cli.json == [{}]
    cli.confirm.assert_not_called()

    cli.json.clear()
    rows = [{"id": REQUEST_ID, "username": "alice", "status": "approved"}]
    get = _returns(cli.client.get, rows)
    team.list_invite_requests("platform-tools", INVITE_ID, output="table")

    get.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}/invites/{INVITE_ID}/requests")
    table = cli.tables[0]
    assert table.title == "Invite access requests"
    assert table.rows == [("@alice", "approved", "-", "-", "-")]

    get.reset_mock()
    get.return_value = []
    cli.rendered.clear()
    cli.tables.clear()
    team.list_invite_requests("platform-tools", INVITE_ID, output="json")
    assert cli.json == [[]]


def test_personal_request_status_and_withdrawal(cli):
    _resolve(cli)
    rows = [{"id": REQUEST_ID, "username": "alice", "status": "pending", "message": "Please add me"}]
    get = _returns(cli.client.get, rows)

    team.list_my_join_requests("platform-tools", output="table")

    get.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}/join-requests/mine")
    table = cli.tables[0]
    assert table.title == "Your join requests"
    assert table.rows == [("@alice", "pending", "Please add me", "-", "-")]

    get.reset_mock()
    get.return_value = []
    cli.rendered.clear()
    cli.tables.clear()
    team.list_my_join_requests("platform-tools", output="json")
    assert cli.json == [[]]

    get.reset_mock()
    get.return_value = rows
    cli.json.clear()
    delete = _returns(cli.client.delete, {})
    team.withdraw_join_request("platform-tools", yes=True, output="json")

    get.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}/join-requests/mine")
    delete.assert_called_once_with(f"/api/v1/teams/{TEAM_ID}/join-requests/{REQUEST_ID}")
    assert cli.json == [{}]
    cli.confirm.assert_not_called()


def test_withdrawal_without_pending_request_is_not_found(cli):
    _resolve(cli)
    _returns(cli.client.get, [{"id": REQUEST_ID, "status": "rejected"}])

    with pytest.raises(typer.Exit) as raised:
        team.withdraw_join_request("platform-tools", yes=True, output="table")

    assert raised.value.exit_code == 5
    cli.client.delete.assert_not_called()
    assert cli.rendered == ["[red]You have no pending join request for this teamspace.[/red]"]


def test_new_team_commands_emit_parseable_json(monkeypatch):
    resolve = Mock(return_value=TEAM_ID)
    get = Mock()
    post = Mock()
    patch = Mock()
    delete = Mock()
    monkeypatch.setattr(team.client, "resolve_team_id", resolve)
    monkeypatch.setattr(team.client, "get", get)
    monkeypatch.setattr(team.client, "post", post)
    monkeypatch.setattr(team.client, "patch", patch)
    monkeypatch.setattr(team.client, "delete", delete)

    def invoke(arguments, expected):
        result = runner.invoke(team.team_app, [*arguments, "--output", "json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == expected

    claimed = {"id": TEAM_ID, "handle": "alice-team", "visibility": "private"}
    post.return_value = claimed
    invoke(["claim-personal"], claimed)

    empty_list = {"items": [], "total": 0, "page": 1, "page_size": 0}
    get.return_value = []
    invoke(["visibility", "list-requests"], empty_list)

    approved = {"id": TEAM_ID, "visibility": "public", "visibility_request_status": "approved"}
    post.return_value = approved
    invoke(["visibility", "approve", "platform-tools"], approved)

    rejected = {"id": TEAM_ID, "visibility": "private", "visibility_request_status": "rejected"}
    post.return_value = rejected
    invoke(["visibility", "reject", "platform-tools"], rejected)

    preview = {"valid": True, "team_id": TEAM_ID, "team_handle": "platform-tools"}
    post.return_value = preview
    invoke(["invite", "preview", "secret-token"], preview)

    pending = {"id": REQUEST_ID, "team_id": TEAM_ID, "status": "pending"}
    post.side_effect = [preview, pending]
    invoke(["invite", "request", "secret-token"], pending)
    post.side_effect = None

    delete.return_value = {}
    invoke(["invite", "delete", "platform-tools", INVITE_ID, "--yes"], {})

    get.return_value = []
    invoke(["invite", "requests", "platform-tools", INVITE_ID], empty_list)
    invoke(["request", "mine", "platform-tools"], empty_list)

    get.return_value = [pending]
    invoke(["request", "withdraw", "platform-tools", "--yes"], {})


def test_refactored_team_paths_are_canonical_and_output_mode_is_strict(cli):
    command = get_command(cli_app).commands["team"]

    assert {"visibility", "request"}.issubset(command.commands)
    assert {"request-join", "requests", "approve", "reject"}.isdisjoint(command.commands)

    result = runner.invoke(cli_app, ["team", "claim-personal", "--output", "yaml"])

    assert result.exit_code == 2
    cli.client.post.assert_not_called()
