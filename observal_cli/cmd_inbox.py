# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""observal inbox: the signed-in user's work and event feed."""

from __future__ import annotations

from contextlib import nullcontext
from urllib.parse import urlencode
from uuid import UUID

import typer
from rich import print as rprint
from rich.table import Table
from typer.models import OptionInfo

from observal_cli import client
from observal_cli.errors import ErrorCategory, fail
from observal_cli.render import OutputMode, console, esc, output_json, spinner

inbox_app = typer.Typer(
    name="inbox",
    help=(
        "Your work and event feed: reviews, decisions, and update notices\n\n"
        "Examples:\n"
        "  observal inbox\n"
        "  observal inbox --action-required\n"
        "  observal inbox list --state open --output json"
    ),
    no_args_is_help=False,
    invoke_without_command=True,
)

_STATES = ("open", "done", "dismissed")
_FACET_STATES = (*_STATES, "all")
_SORTS = ("newest", "oldest")
_KINDS = (
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


def _option_value(value):
    return value.default if isinstance(value, OptionInfo) else value


def _validate(
    value: str | None,
    allowed: tuple[str, ...],
    label: str,
    *,
    operation: str = "List inbox items",
) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized not in allowed:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown inbox {label}: {value}.",
            operation=operation,
            resource=f"inbox {label}",
            remediation=f"Choose one of: {', '.join(allowed)}.",
        )
    return normalized


def _text_filter(
    value: str | None,
    label: str,
    max_length: int,
    *,
    operation: str = "List inbox items",
) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        fail(
            ErrorCategory.VALIDATION,
            f"Inbox {label} must contain 1 to {max_length} characters.",
            operation=operation,
            resource=f"inbox {label}",
            remediation=f"Provide a non-empty {label} within the server limit.",
        )
    return normalized


def _item_id(value: str, *, operation: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        fail(
            ErrorCategory.VALIDATION,
            "Inbox item ID must be a UUID.",
            operation=operation,
            resource="inbox item",
            remediation="Copy an item ID from `observal inbox list --output json`.",
        )


def _progress(output: OutputMode | str, message: str):
    return nullcontext() if _option_value(output) == "json" else spinner(message)


def _filters(
    state: str | None,
    kind: str | None,
    action_required: bool | None,
    unread: bool | None = None,
    subject_type: str | None = None,
    query: str | None = None,
    sort: str | None = None,
    *,
    operation: str = "List inbox items",
) -> dict[str, object]:
    state = _option_value(state)
    kind = _option_value(kind)
    action_required = _option_value(action_required)
    unread = _option_value(unread)
    subject_type = _option_value(subject_type)
    query = _option_value(query)
    sort = _option_value(sort)

    params: dict[str, object] = {}
    if state:
        params["state"] = _validate(state, _STATES, "state", operation=operation)
    if kind:
        params["kind"] = _validate(kind, _KINDS, "kind", operation=operation)
    if action_required is not None:
        params["action_required"] = action_required
    if unread is not None:
        params["unread"] = unread
    if subject_type is not None:
        params["subject_type"] = _text_filter(subject_type, "subject type", 32, operation=operation)
    if query is not None:
        params["q"] = _text_filter(query, "search query", 200, operation=operation)
    if sort is not None:
        params["sort"] = _validate(sort, _SORTS, "sort order", operation=operation)
    return params


def _filter_params(
    state: str | None,
    kind: str | None,
    action_required: bool | None,
    unread: bool | None,
    page: int = 1,
    page_size: int = 25,
    subject_type: str | None = None,
    query: str | None = None,
    sort: str | None = None,
) -> dict[str, object]:
    params = _filters(state, kind, action_required, unread, subject_type, query, sort)
    params.update({"page": page, "page_size": page_size})
    return params


def _emit_list(params: dict[str, object], output: OutputMode | str) -> None:
    with _progress(output, "Fetching inbox..."):
        data = client.get("/api/v1/inbox", params=params or None)

    if output == "json":
        output_json(data)
        return

    items = data.get("items") or []
    if not items:
        rprint("[dim]Nothing in your inbox for that filter.[/dim]")
        return

    table = Table(title=f"Inbox ({data.get('total', len(items))})", show_lines=False, padding=(0, 1))
    table.add_column("#", style="dim", width=3)
    table.add_column("", width=1)
    table.add_column("Kind")
    table.add_column("Title", style="bold", overflow="fold")
    table.add_column("State")
    table.add_column("ID", style="dim")

    for index, item in enumerate(items, 1):
        marker = "" if item.get("read") else "●"
        state = item.get("state", "")
        if item.get("action_required") and state == "open":
            state = f"{state} !"
        table.add_row(
            str(index),
            marker,
            esc(item.get("kind", "")),
            esc(item.get("title", "")),
            esc(state),
            esc(str(item.get("id", ""))[:8]),
        )
    console.print(table)

    total = int(data.get("total") or len(items))
    page = int(data.get("page") or 1)
    page_size = int(data.get("page_size") or len(items) or 1)
    first_row = (page - 1) * page_size + 1
    last_row = (page - 1) * page_size + len(items)
    if total > len(items):
        rprint(f"\n[dim]Showing {first_row}-{last_row} of {total}.[/dim]")
        if last_row < total:
            rprint(f"[dim]Next page: [cyan]observal inbox list --page {page + 1}[/cyan][/dim]")

    first_id = items[0].get("id", "")
    rprint(f"\n[dim]Detail: [cyan]observal inbox show {esc(first_id)}[/cyan][/dim]")


@inbox_app.callback(invoke_without_command=True)
def inbox(
    ctx: typer.Context,
    state: str | None = typer.Option(None, "--state", "-s", help=f"Filter by state: {', '.join(_STATES)}"),
    kind: str | None = typer.Option(None, "--kind", "-k", help="Filter by kind"),
    action_required: bool | None = typer.Option(
        None,
        "--action-required/--no-action-required",
        help="Filter by whether an item requires action",
    ),
    unread: bool | None = typer.Option(None, "--unread/--read", help="Filter by read state"),
    page: int = typer.Option(1, "--page", "-p", min=1, help="Page number"),
    page_size: int = typer.Option(25, "--page-size", min=1, max=100, help="Items per page"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
    subject_type: str | None = typer.Option(None, "--subject-type", help="Filter by subject type"),
    query: str | None = typer.Option(None, "--search", "-q", help="Search title, body, namespace, or slug"),
    sort: str | None = typer.Option(None, "--sort", help=f"Sort order: {', '.join(_SORTS)}"),
):
    """Show your inbox.

    Examples:

        observal inbox

        observal inbox --action-required

        observal inbox --search postgres --sort oldest --output json
    """
    if ctx.invoked_subcommand is None:
        _emit_list(
            _filter_params(state, kind, action_required, unread, page, page_size, subject_type, query, sort),
            output,
        )


@inbox_app.command(name="list")
def inbox_list(
    state: str | None = typer.Option(None, "--state", "-s", help=f"Filter by state: {', '.join(_STATES)}"),
    kind: str | None = typer.Option(None, "--kind", "-k", help="Filter by kind"),
    action_required: bool | None = typer.Option(
        None,
        "--action-required/--no-action-required",
        help="Filter by whether an item requires action",
    ),
    unread: bool | None = typer.Option(None, "--unread/--read", help="Filter by read state"),
    page: int = typer.Option(1, "--page", "-p", min=1, help="Page number"),
    page_size: int = typer.Option(25, "--page-size", min=1, max=100, help="Items per page"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
    subject_type: str | None = typer.Option(None, "--subject-type", help="Filter by subject type"),
    query: str | None = typer.Option(None, "--search", "-q", help="Search title, body, namespace, or slug"),
    sort: str | None = typer.Option(None, "--sort", help=f"Sort order: {', '.join(_SORTS)}"),
):
    """List your inbox items.

    Examples:

        observal inbox list --state open --subject-type mcp --output json
    """
    _emit_list(
        _filter_params(state, kind, action_required, unread, page, page_size, subject_type, query, sort),
        output,
    )


@inbox_app.command(name="count")
def inbox_count(
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
    facets: bool = typer.Option(False, "--facets", help="Include kind and subject-type counts"),
    facet_state: str | None = typer.Option(None, "--facet-state", help=f"Facet state: {', '.join(_FACET_STATES)}"),
):
    """Show unread and needs-action counts.

    Examples:

        observal inbox count --facets --facet-state open --output json
    """
    output = _option_value(output)
    facets = _option_value(facets)
    facet_state = _option_value(facet_state)

    params: dict[str, object] = {}
    if facets:
        params["facets"] = True
    if facet_state is not None:
        if not facets:
            fail(
                ErrorCategory.VALIDATION,
                "Inbox facet state requires --facets.",
                operation="Count inbox items",
                resource="inbox count filters",
                remediation="Add --facets or remove --facet-state.",
            )
        params["facet_state"] = _validate(
            facet_state,
            _FACET_STATES,
            "facet state",
            operation="Count inbox items",
        )

    with _progress(output, "Fetching counts..."):
        data = client.get("/api/v1/inbox/count", params=params) if params else client.get("/api/v1/inbox/count")
    if output == "json":
        output_json(data)
        return

    rprint(
        f"[bold]{data.get('unread', 0)}[/bold] unread, "
        f"[bold]{data.get('action_required', 0)}[/bold] needing action, "
        f"[bold]{data.get('open', 0)}[/bold] open"
    )
    facet_rows = [("kind", name, count) for name, count in (data.get("by_kind") or {}).items()] + [
        ("subject type", name, count) for name, count in (data.get("by_subject_type") or {}).items()
    ]
    if facet_rows:
        table = Table(title="Inbox facets")
        table.add_column("Facet")
        table.add_column("Value")
        table.add_column("Count", justify="right")
        for facet, name, count in facet_rows:
            table.add_row(facet, esc(name), str(count))
        console.print(table)


@inbox_app.command(name="show")
def inbox_show(
    item_id: str = typer.Argument(..., help="Inbox item ID"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Show one item with its full action history.

    Examples:

        observal inbox show 11111111-1111-1111-1111-111111111111 --output json
    """
    item_id = _item_id(item_id, operation="Show inbox item")
    with _progress(output, "Fetching item..."):
        data = client.get(f"/api/v1/inbox/{item_id}")
    if output == "json":
        output_json(data)
        return

    rprint(f"\n[bold]{esc(data.get('title', ''))}[/bold]")
    rprint(f"[dim]{esc(data.get('kind', ''))} · {esc(data.get('state', ''))}[/dim]")
    if data.get("body"):
        rprint(f"\n{esc(data['body'])}")
    if data.get("action_url"):
        rprint(f"\n[dim]Open:[/dim] {esc(data['action_url'])}")
    if data.get("action_command"):
        rprint(f"[dim]Run:[/dim]  [cyan]{esc(data['action_command'])}[/cyan]")

    history = data.get("history") or []
    if history:
        rprint("\n[bold]History[/bold]")
        for event in history:
            detail = f" : {esc(event['detail'])}" if event.get("detail") else ""
            rprint(f"  [dim]{esc(event.get('created_at', ''))}[/dim]  {esc(event.get('event', ''))}{detail}")


def _act(item_id: str, action: str, past_tense: str, output: OutputMode | str) -> None:
    output = _option_value(output)
    item_id = _item_id(item_id, operation="Update inbox item")
    with _progress(output, f"Marking {action}..."):
        data = client.post(f"/api/v1/inbox/{item_id}/{action}", {})
    if output == "json":
        output_json(data)
        return
    rprint(f"[green]✓ {past_tense}: {esc(data.get('title', item_id))}[/green]")


@inbox_app.command(name="read")
def inbox_read(
    item_id: str = typer.Argument(..., help="Inbox item ID"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Mark an item read without resolving it.

    Examples:

        observal inbox read 11111111-1111-1111-1111-111111111111 --output json
    """
    _act(item_id, "read", "Marked read", output)


@inbox_app.command(name="unread")
def inbox_unread(
    item_id: str = typer.Argument(..., help="Inbox item ID"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Mark an item unread again.

    Examples:

        observal inbox unread 11111111-1111-1111-1111-111111111111 --output json
    """
    _act(item_id, "unread", "Marked unread", output)


@inbox_app.command(name="done")
def inbox_done(
    item_id: str = typer.Argument(..., help="Inbox item ID"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Resolve an item.

    Examples:

        observal inbox done 11111111-1111-1111-1111-111111111111 --output json
    """
    _act(item_id, "done", "Resolved", output)


@inbox_app.command(name="dismiss")
def inbox_dismiss(
    item_id: str = typer.Argument(..., help="Inbox item ID"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Dismiss an item without acting on it.

    Examples:

        observal inbox dismiss 11111111-1111-1111-1111-111111111111 --output json
    """
    _act(item_id, "dismiss", "Dismissed", output)


@inbox_app.command(name="reopen")
def inbox_reopen(
    item_id: str = typer.Argument(..., help="Inbox item ID"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Reopen a resolved or dismissed item.

    Examples:

        observal inbox reopen 11111111-1111-1111-1111-111111111111 --output json
    """
    _act(item_id, "reopen", "Reopened", output)


@inbox_app.command(name="read-all")
def inbox_read_all(
    state: str | None = typer.Option(None, "--state", "-s", help=f"Filter by state: {', '.join(_STATES)}"),
    kind: str | None = typer.Option(None, "--kind", "-k", help="Filter by kind"),
    action_required: bool | None = typer.Option(
        None,
        "--action-required/--no-action-required",
        help="Filter by whether an item requires action",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
    subject_type: str | None = typer.Option(None, "--subject-type", help="Filter by subject type"),
    query: str | None = typer.Option(None, "--search", "-q", help="Search title, body, namespace, or slug"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Mark everything matching the filter as read.

    Examples:

        observal inbox read-all --kind update_available --yes --output json
    """
    output = _option_value(output)
    yes = _option_value(yes)
    params = _filters(
        state,
        kind,
        action_required,
        subject_type=subject_type,
        query=query,
        operation="Mark inbox items read",
    )
    if output == "json" and not yes:
        fail(
            ErrorCategory.VALIDATION,
            "JSON mode cannot prompt for bulk inbox changes.",
            operation="Mark inbox items read",
            resource="user inbox",
            remediation="Add --yes to confirm the bulk read operation.",
        )
    if not yes:
        scope = ", ".join(f"{key}={value}" for key, value in params.items()) or "ALL unread items"
        typer.confirm(f"Mark as read: {scope}?", abort=True)

    query_string = urlencode(
        {key: str(value).lower() if isinstance(value, bool) else value for key, value in params.items()}
    )
    path = "/api/v1/inbox/read-all" + (f"?{query_string}" if query_string else "")
    with _progress(output, "Marking read..."):
        data = client.post(path, {})
    if output == "json":
        output_json(data)
        return
    rprint(f"[green]✓ Marked {data.get('updated', 0)} item(s) read.[/green]")
