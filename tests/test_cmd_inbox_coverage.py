# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deterministic behavioral coverage for the inbox CLI boundary."""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
import typer
from typer.main import get_command
from typer.testing import CliRunner

import observal_cli.cmd_inbox as inbox
from observal_cli.main import app as cli_app

ITEM_ID = "11111111-1111-1111-1111-111111111111"
SECOND_ITEM_ID = "22222222-2222-2222-2222-222222222222"
runner = CliRunner()
LONG_OPTION = "-" * 2


class FakeTable:
    """Capture table structure without terminal width or color behavior."""

    def __init__(self, **options):
        self.options = options
        self.columns: list[tuple[str, dict]] = []
        self.rows: list[tuple[str, ...]] = []

    def add_column(self, name: str, **options) -> None:
        self.columns.append((name, options))

    def add_row(self, *values: str) -> None:
        self.rows.append(values)


class FakeConsole:
    def __init__(self) -> None:
        self.renderables: list[object] = []

    def print(self, renderable: object) -> None:
        self.renderables.append(renderable)


def _blocked(name: str) -> Mock:
    def fail(*args, **kwargs):
        raise AssertionError(f"unmocked boundary: {name}, args={args}, kwargs={kwargs}")

    return Mock(side_effect=fail)


def _returns(boundary: Mock, value) -> Mock:
    boundary.side_effect = None
    boundary.return_value = value
    return boundary


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Mock all HTTP, rendering, status, and prompt boundaries."""
    messages: list[str] = []
    json_values: list[object] = []
    spinner_messages: list[str] = []
    tables: list[FakeTable] = []
    console = FakeConsole()
    client_get = _blocked("client.get")
    client_post = _blocked("client.post")
    confirm = _blocked("typer.confirm")

    monkeypatch.setattr(inbox.client, "get", client_get)
    monkeypatch.setattr(inbox.client, "post", client_post)
    monkeypatch.setattr(inbox.typer, "confirm", confirm)
    monkeypatch.setattr(inbox, "rprint", lambda value: messages.append(str(value)))
    monkeypatch.setattr(inbox, "output_json", json_values.append)
    monkeypatch.setattr(inbox, "console", console)

    @contextmanager
    def fake_spinner(message: str):
        spinner_messages.append(message)
        yield

    def fake_table(**options) -> FakeTable:
        table = FakeTable(**options)
        tables.append(table)
        return table

    monkeypatch.setattr(inbox, "spinner", fake_spinner)
    monkeypatch.setattr(inbox, "Table", fake_table)

    return SimpleNamespace(
        confirm=confirm,
        console=console,
        get=client_get,
        json=json_values,
        messages=messages,
        post=client_post,
        spinners=spinner_messages,
        tables=tables,
    )


def test_registration_and_bare_invocation_use_default_list_contract(cli):
    command = get_command(inbox.inbox_app)
    assert list(command.commands) == [
        "list",
        "count",
        "show",
        "read",
        "unread",
        "done",
        "dismiss",
        "reopen",
        "read-all",
    ]
    _returns(cli.get, {"items": [], "total": 0, "page": 1, "page_size": 25})

    result = runner.invoke(inbox.inbox_app, [])

    assert result.exit_code == 0, result.output
    cli.get.assert_called_once_with("/api/v1/inbox", params={"page": 1, "page_size": 25})
    assert cli.spinners == ["Fetching inbox..."]
    assert cli.messages == ["[dim]Nothing in your inbox for that filter.[/dim]"]
    assert cli.json == []
    assert cli.tables == []
    assert cli.console.renderables == []


def test_explicit_list_normalizes_and_forwards_every_filter_as_raw_json(cli):
    payload = {"items": [], "total": 0, "page": 2, "page_size": 10}
    _returns(cli.get, payload)

    result = runner.invoke(
        inbox.inbox_app,
        [
            "list",
            f"{LONG_OPTION}state",
            " OPEN ",
            f"{LONG_OPTION}kind",
            " REVIEW_REQUESTED ",
            f"{LONG_OPTION}action-required",
            f"{LONG_OPTION}unread",
            f"{LONG_OPTION}page",
            "2",
            f"{LONG_OPTION}page-size",
            "10",
            f"{LONG_OPTION}output",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    cli.get.assert_called_once_with(
        "/api/v1/inbox",
        params={
            "state": "open",
            "kind": "review_requested",
            "action_required": True,
            "unread": True,
            "page": 2,
            "page_size": 10,
        },
    )
    assert cli.json == [payload]
    assert cli.spinners == []
    assert cli.messages == []
    assert cli.tables == []
    assert cli.console.renderables == []


def test_filter_helpers_match_server_enums_and_omit_unrequested_values():
    assert inbox._STATES == ("open", "done", "dismissed")
    assert inbox._KINDS == (
        "review_requested",
        "review_approved",
        "review_rejected",
        "review_comment",
        "change_requested",
        "team_join_requested",
        "team_join_decided",
        "team_created_pending",
        "ownership_transfer",
        "update_available",
        "insight_ready",
        "system_notice",
    )
    assert inbox._validate(None, inbox._STATES, "state") is None
    assert inbox._filters(None, None, False) == {"action_required": False}
    assert inbox._filters(" DONE ", " UPDATE_AVAILABLE ", True, unread=True) == {
        "state": "done",
        "kind": "update_available",
        "action_required": True,
        "unread": True,
    }
    assert inbox._filter_params(None, None, False, False, page=4, page_size=7) == {
        "action_required": False,
        "unread": False,
        "page": 4,
        "page_size": 7,
    }


@pytest.mark.parametrize(
    ("state", "kind", "message", "resource"),
    [
        ("lost", None, "Unknown inbox state: lost.", "inbox state"),
        (None, "[bad]", "Unknown inbox kind: [bad].", "inbox kind"),
    ],
)
def test_invalid_filters_fail_locally_with_exact_guidance(cli, state, kind, message, resource):
    with pytest.raises(typer.Exit) as raised:
        inbox.inbox_list(state, kind, False, False, 1, 25, "table")

    assert raised.value.exit_code == 7
    assert raised.value.message == message
    assert raised.value.resource == resource
    assert cli.messages == [f"[red]{message}[/red]"]
    cli.get.assert_not_called()
    cli.post.assert_not_called()
    cli.confirm.assert_not_called()
    assert cli.spinners == []


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["list", f"{LONG_OPTION}page", "0"], "0 is not in the range x>=1"),
        (["list", f"{LONG_OPTION}page-size", "0"], "0 is not in the range 1<=x<=100"),
        (["list", f"{LONG_OPTION}page-size", "101"], "101 is not in the range 1<=x<=100"),
        (["show"], "Missing argument 'ITEM_ID'"),
    ],
)
def test_typer_validation_stops_before_http(cli, arguments, message):
    result = runner.invoke(inbox.inbox_app, arguments)

    assert result.exit_code == 2
    assert message in result.output
    cli.get.assert_not_called()
    cli.post.assert_not_called()
    cli.confirm.assert_not_called()


def test_list_table_renders_unread_action_state_markup_and_pagination_exactly(cli):
    payload = {
        "items": [
            {
                "id": ITEM_ID,
                "kind": "review_requested",
                "title": "Deploy [prod]",
                "state": "open",
                "read": False,
                "action_required": True,
            },
            {
                "id": SECOND_ITEM_ID,
                "kind": "update_available",
                "title": "Clean [/tmp]",
                "state": "dismissed",
                "read": True,
                "action_required": True,
            },
        ],
        "total": 7,
        "page": 2,
        "page_size": 2,
    }
    _returns(cli.get, payload)

    inbox._emit_list({"page": 2, "page_size": 2}, "table")

    cli.get.assert_called_once_with("/api/v1/inbox", params={"page": 2, "page_size": 2})
    assert cli.spinners == ["Fetching inbox..."]
    assert len(cli.tables) == 1
    table = cli.tables[0]
    assert table.options == {"title": "Inbox (7)", "show_lines": False, "padding": (0, 1)}
    assert table.columns == [
        ("#", {"style": "dim", "width": 3}),
        ("", {"width": 1}),
        ("Kind", {}),
        ("Title", {"style": "bold", "overflow": "fold"}),
        ("State", {}),
        ("ID", {"style": "dim"}),
    ]
    assert table.rows == [
        ("1", "\N{BLACK CIRCLE}", "review_requested", "Deploy \\[prod]", "open !", "11111111"),
        ("2", "", "update_available", "Clean \\[/tmp]", "dismissed", "22222222"),
    ]
    assert cli.console.renderables == [table]
    assert cli.messages == [
        "\n[dim]Showing 3-4 of 7.[/dim]",
        f"[dim]Next page: [cyan]observal inbox list {LONG_OPTION}page 3[/cyan][/dim]",
        f"\n[dim]Detail: [cyan]observal inbox show {ITEM_ID}[/cyan][/dim]",
    ]
    assert cli.json == []


def test_list_final_page_has_range_without_a_nonexistent_next_page(cli):
    payload = {
        "items": [{"id": ITEM_ID, "kind": "system_notice", "title": "Done", "state": "done", "read": True}],
        "total": 5,
        "page": 3,
        "page_size": 2,
    }
    _returns(cli.get, payload)

    inbox._emit_list({"page": 3, "page_size": 2}, "table")

    assert cli.messages == [
        "\n[dim]Showing 5-5 of 5.[/dim]",
        f"\n[dim]Detail: [cyan]observal inbox show {ITEM_ID}[/cyan][/dim]",
    ]
    assert all("Next page" not in message for message in cli.messages)


def test_single_page_list_omits_pagination_copy(cli):
    payload = {
        "items": [{"id": ITEM_ID, "kind": "system_notice", "title": "Notice", "state": "open", "read": False}],
        "total": 1,
        "page": 1,
        "page_size": 25,
    }
    _returns(cli.get, payload)

    inbox._emit_list({"page": 1, "page_size": 25}, "table")

    assert cli.messages == [f"\n[dim]Detail: [cyan]observal inbox show {ITEM_ID}[/cyan][/dim]"]


def test_list_json_accepts_an_empty_param_mapping_without_inventing_filters(cli):
    payload = {"items": [{"id": ITEM_ID}], "total": 1, "page": 1, "page_size": 25}
    _returns(cli.get, payload)

    inbox._emit_list({}, "json")

    cli.get.assert_called_once_with("/api/v1/inbox", params=None)
    assert cli.json == [payload]
    assert cli.messages == []
    assert cli.tables == []
    assert cli.console.renderables == []


def test_count_table_and_json_outputs_are_exact(cli):
    counts = {"unread": 4, "action_required": 2, "open": 7, "done": 9}
    _returns(cli.get, counts)

    inbox.inbox_count("table")

    cli.get.assert_called_once_with("/api/v1/inbox/count")
    assert cli.spinners == ["Fetching counts..."]
    assert cli.messages == ["[bold]4[/bold] unread, [bold]2[/bold] needing action, [bold]7[/bold] open"]
    assert cli.json == []

    cli.get.reset_mock()
    cli.spinners.clear()
    cli.messages.clear()
    inbox.inbox_count("json")

    cli.get.assert_called_once_with("/api/v1/inbox/count")
    assert cli.spinners == []
    assert cli.json == [counts]
    assert cli.messages == []


def test_count_table_defaults_missing_counts_to_zero(cli):
    _returns(cli.get, {})

    inbox.inbox_count("table")

    assert cli.messages == ["[bold]0[/bold] unread, [bold]0[/bold] needing action, [bold]0[/bold] open"]


def test_show_renders_full_detail_actions_and_timestamped_history_exactly(cli):
    payload = {
        "id": ITEM_ID,
        "title": "Review [urgent]",
        "kind": "review_requested",
        "state": "open",
        "body": "Inspect [/tmp]",
        "action_url": "/review?tab=components",
        "action_command": f"observal registry mcp show acme/tool {LONG_OPTION}output json",
        "history": [
            {"created_at": "2026-06-01T01:02:03Z", "event": "created"},
            {
                "created_at": "2026-06-02T04:05:06+00:00",
                "event": "read [once]",
                "detail": "Moved [queue]",
            },
        ],
    }
    _returns(cli.get, payload)

    inbox.inbox_show(ITEM_ID, "table")

    cli.get.assert_called_once_with(f"/api/v1/inbox/{ITEM_ID}")
    cli.post.assert_not_called()
    assert cli.spinners == ["Fetching item..."]
    assert cli.messages == [
        "\n[bold]Review \\[urgent][/bold]",
        "[dim]review_requested · open[/dim]",
        "\nInspect \\[/tmp]",
        "\n[dim]Open:[/dim] /review?tab=components",
        f"[dim]Run:[/dim]  [cyan]observal registry mcp show acme/tool {LONG_OPTION}output json[/cyan]",
        "\n[bold]History[/bold]",
        "  [dim]2026-06-01T01:02:03Z[/dim]  created",
        "  [dim]2026-06-02T04:05:06+00:00[/dim]  read \\[once] : Moved \\[queue]",
    ]
    assert cli.json == []
    assert cli.tables == []
    assert cli.console.renderables == []


def test_show_minimal_table_omits_optional_sections(cli):
    _returns(cli.get, {"title": "Notice", "kind": "system_notice", "state": "open"})

    inbox.inbox_show(ITEM_ID, "table")

    assert cli.messages == ["\n[bold]Notice[/bold]", "[dim]system_notice · open[/dim]"]


def test_show_json_forwards_the_untouched_payload(cli):
    payload = {
        "id": ITEM_ID,
        "title": "Notice",
        "created_at": "2026-06-03T12:00:00Z",
        "history": [{"event": "created", "created_at": "2026-06-03T12:00:00Z"}],
    }
    _returns(cli.get, payload)

    inbox.inbox_show(ITEM_ID, "json")

    cli.get.assert_called_once_with(f"/api/v1/inbox/{ITEM_ID}")
    assert cli.json == [payload]
    assert cli.spinners == []
    assert cli.messages == []
    assert cli.tables == []
    assert cli.console.renderables == []


@pytest.mark.parametrize(
    ("invoke", "action", "past_tense"),
    [
        (inbox.inbox_read, "read", "Marked read"),
        (inbox.inbox_unread, "unread", "Marked unread"),
        (inbox.inbox_done, "done", "Resolved"),
        (inbox.inbox_dismiss, "dismiss", "Dismissed"),
        (inbox.inbox_reopen, "reopen", "Reopened"),
    ],
)
def test_item_actions_post_empty_payload_and_render_exact_success(cli, invoke, action, past_tense):
    _returns(cli.post, {"title": "Review [prod]"})

    invoke(ITEM_ID)

    cli.post.assert_called_once_with(f"/api/v1/inbox/{ITEM_ID}/{action}", {})
    cli.get.assert_not_called()
    assert cli.spinners == [f"Marking {action}..."]
    assert cli.messages == [f"[green]\N{CHECK MARK} {past_tense}: Review \\[prod][/green]"]
    assert cli.json == []
    assert cli.tables == []
    assert cli.console.renderables == []


def test_item_action_uses_id_when_response_has_no_title(cli):
    _returns(cli.post, {})

    inbox.inbox_read(ITEM_ID)

    assert cli.messages == [f"[green]\N{CHECK MARK} Marked read: {ITEM_ID}[/green]"]


def test_read_all_confirms_filter_scope_and_encodes_exact_query(cli):
    cli.confirm.side_effect = None
    cli.confirm.return_value = True
    _returns(cli.post, {"updated": 3})

    inbox.inbox_read_all(" OPEN ", " UPDATE_AVAILABLE ", True, False)

    cli.confirm.assert_called_once_with(
        "Mark as read: state=open, kind=update_available, action_required=True?",
        abort=True,
    )
    cli.post.assert_called_once_with(
        "/api/v1/inbox/read-all?state=open&kind=update_available&action_required=true",
        {},
    )
    assert cli.spinners == ["Marking read..."]
    assert cli.messages == ["[green]\N{CHECK MARK} Marked 3 item(s) read.[/green]"]


def test_read_all_yes_bypasses_prompt_and_uses_unfiltered_endpoint(cli):
    _returns(cli.post, {})

    inbox.inbox_read_all(None, None, None, True)

    cli.confirm.assert_not_called()
    cli.post.assert_called_once_with("/api/v1/inbox/read-all", {})
    assert cli.spinners == ["Marking read..."]
    assert cli.messages == ["[green]\N{CHECK MARK} Marked 0 item(s) read.[/green]"]


def test_read_all_cancellation_has_no_http_or_rendering_side_effects(cli):
    cli.confirm.side_effect = typer.Abort()

    with pytest.raises(typer.Abort):
        inbox.inbox_read_all(None, None, None, False)

    cli.confirm.assert_called_once_with("Mark as read: ALL unread items?", abort=True)
    cli.get.assert_not_called()
    cli.post.assert_not_called()
    assert cli.spinners == []
    assert cli.messages == []
    assert cli.json == []
    assert cli.tables == []
    assert cli.console.renderables == []


def test_list_authentication_failure_propagates_without_partial_output(cli):
    failure = typer.Exit(1)
    cli.get.side_effect = failure

    with pytest.raises(typer.Exit) as raised:
        inbox._emit_list({"page": 1, "page_size": 25}, "table")

    assert raised.value is failure
    cli.get.assert_called_once_with("/api/v1/inbox", params={"page": 1, "page_size": 25})
    assert cli.spinners == ["Fetching inbox..."]
    assert cli.messages == []
    assert cli.json == []
    assert cli.tables == []
    assert cli.console.renderables == []


@pytest.mark.parametrize(
    ("invoke", "expected_call", "spinner_message"),
    [
        (lambda: inbox.inbox_show(ITEM_ID, "table"), call(f"/api/v1/inbox/{ITEM_ID}"), "Fetching item..."),
        (lambda: inbox.inbox_count("table"), call("/api/v1/inbox/count"), "Fetching counts..."),
    ],
)
def test_get_http_failures_propagate_without_success_output(cli, invoke, expected_call, spinner_message):
    failure = typer.Exit(1)
    cli.get.side_effect = failure

    with pytest.raises(typer.Exit) as raised:
        invoke()

    assert raised.value is failure
    assert cli.get.call_args == expected_call
    assert cli.spinners == [spinner_message]
    assert cli.messages == []
    assert cli.json == []
    assert cli.tables == []
    assert cli.console.renderables == []


@pytest.mark.parametrize(
    ("invoke", "expected_call", "spinner_message"),
    [
        (
            lambda: inbox.inbox_done(ITEM_ID),
            call(f"/api/v1/inbox/{ITEM_ID}/done", {}),
            "Marking done...",
        ),
        (
            lambda: inbox.inbox_read_all(None, None, None, True),
            call("/api/v1/inbox/read-all", {}),
            "Marking read...",
        ),
    ],
)
def test_post_http_failures_propagate_without_success_output(cli, invoke, expected_call, spinner_message):
    failure = typer.Exit(1)
    cli.post.side_effect = failure

    with pytest.raises(typer.Exit) as raised:
        invoke()

    assert raised.value is failure
    assert cli.post.call_args == expected_call
    assert cli.spinners == [spinner_message]
    assert cli.messages == []
    assert cli.json == []
    assert cli.tables == []
    assert cli.console.renderables == []


@pytest.mark.parametrize(
    ("method", "invoke", "expected_call", "spinner_message"),
    [
        (
            "get",
            lambda: inbox._emit_list({"page": 1, "page_size": 25}, "table"),
            call("/api/v1/inbox", params={"page": 1, "page_size": 25}),
            "Fetching inbox...",
        ),
        ("get", lambda: inbox.inbox_count("table"), call("/api/v1/inbox/count"), "Fetching counts..."),
        ("get", lambda: inbox.inbox_show(ITEM_ID, "table"), call(f"/api/v1/inbox/{ITEM_ID}"), "Fetching item..."),
        (
            "post",
            lambda: inbox.inbox_read(ITEM_ID),
            call(f"/api/v1/inbox/{ITEM_ID}/read", {}),
            "Marking read...",
        ),
    ],
)
def test_malformed_non_mapping_responses_fail_loudly(cli, method, invoke, expected_call, spinner_message):
    boundary = _returns(getattr(cli, method), [])

    with pytest.raises(AttributeError, match="has no attribute 'get'"):
        invoke()

    assert boundary.call_args == expected_call
    assert cli.spinners == [spinner_message]
    assert cli.messages == []
    assert cli.json == []
    assert cli.console.renderables == []


def test_every_inbox_leaf_has_shared_output_option():
    command = get_command(inbox.inbox_app)

    assert all(any(parameter.name == "output" for parameter in leaf.params) for leaf in command.commands.values())


def test_list_exposes_all_server_filters_including_false_booleans(cli):
    payload = {"items": [], "total": 0, "page": 3, "page_size": 5}
    _returns(cli.get, payload)

    result = runner.invoke(
        inbox.inbox_app,
        [
            "list",
            "--state",
            "done",
            "--kind",
            "system_notice",
            "--no-action-required",
            "--read",
            "--subject-type",
            "mcp",
            "--search",
            "postgres",
            "--sort",
            "oldest",
            "--page",
            "3",
            "--page-size",
            "5",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    cli.get.assert_called_once_with(
        "/api/v1/inbox",
        params={
            "state": "done",
            "kind": "system_notice",
            "action_required": False,
            "unread": False,
            "subject_type": "mcp",
            "q": "postgres",
            "sort": "oldest",
            "page": 3,
            "page_size": 5,
        },
    )
    assert cli.json == [payload]
    assert cli.spinners == []


def test_count_facets_forwards_state_and_renders_breakdown(cli):
    counts = {
        "unread": 4,
        "action_required": 2,
        "open": 7,
        "by_kind": {"review_requested": 3},
        "by_subject_type": {"mcp": 2},
    }
    _returns(cli.get, counts)

    inbox.inbox_count("table", True, "done")

    cli.get.assert_called_once_with("/api/v1/inbox/count", params={"facets": True, "facet_state": "done"})
    assert cli.messages == ["[bold]4[/bold] unread, [bold]2[/bold] needing action, [bold]7[/bold] open"]
    assert cli.spinners == ["Fetching counts..."]
    assert len(cli.tables) == 1
    assert cli.tables[0].rows == [
        ("kind", "review_requested", "3"),
        ("subject type", "mcp", "2"),
    ]


def test_facet_state_without_facets_is_validation_error(cli):
    with pytest.raises(typer.Exit) as raised:
        inbox.inbox_count("table", False, "done")

    assert raised.value.exit_code == 7
    assert raised.value.operation == "Count inbox items"
    cli.get.assert_not_called()


def test_read_all_json_forwards_all_server_filters_without_prompt_or_spinner(cli):
    response = {"updated": 8}
    _returns(cli.post, response)

    inbox.inbox_read_all("open", "update_available", False, True, "mcp", "postgres", "json")

    cli.confirm.assert_not_called()
    cli.post.assert_called_once_with(
        "/api/v1/inbox/read-all?state=open&kind=update_available&action_required=false&subject_type=mcp&q=postgres",
        {},
    )
    assert cli.json == [response]
    assert cli.messages == []
    assert cli.spinners == []


def test_item_mutation_json_writes_only_the_server_result(monkeypatch):
    payload = {"id": ITEM_ID, "state": "done", "title": "Review [prod]"}
    post = Mock(return_value=payload)
    monkeypatch.setattr(inbox.client, "post", post)

    result = runner.invoke(cli_app, ["inbox", "done", ITEM_ID, "--output", "json"])

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout) == payload
    assert result.stderr == ""
    post.assert_called_once_with(f"/api/v1/inbox/{ITEM_ID}/done", {})


@pytest.mark.parametrize(
    "arguments",
    [
        ["inbox", "list", "--sort", "sideways", "--output", "json"],
        ["inbox", "list", "--search", " ", "--output", "json"],
        ["inbox", "show", "not-a-uuid", "--output", "json"],
        ["inbox", "read-all", "--output", "json"],
    ],
)
def test_json_validation_uses_shared_error_boundary(monkeypatch, arguments):
    get = _blocked("client.get")
    post = _blocked("client.post")
    monkeypatch.setattr(inbox.client, "get", get)
    monkeypatch.setattr(inbox.client, "post", post)

    result = runner.invoke(cli_app, arguments)

    assert result.exit_code == 7
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "validation"
    get.assert_not_called()
    post.assert_not_called()
