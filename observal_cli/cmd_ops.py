# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Swathi Saravanan <ss4522@cornell.edu>
# SPDX-License-Identifier: Apache-2.0

"""Review, telemetry, dashboard, feedback, admin, and trace CLI commands."""

from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote
from uuid import UUID

import typer
from rich import print as rprint
from rich.table import Table
from typer.models import OptionInfo

from observal_cli import client, config
from observal_cli.constants import VALID_HARNESSES
from observal_cli.errors import CliError, ErrorCategory, fail
from observal_cli.prompts import password_input
from observal_cli.render import (
    OutputMode,
    console,
    esc,
    output_json,
    relative_time,
    spinner,
    star_rating,
)

# ═══════════════════════════════════════════════════════════
# ops_app: Observability / operational commands group
# ═══════════════════════════════════════════════════════════

_RANKING_TYPES = ("mcp", "agent")
_FEEDBACK_TYPES = ("mcp", "agent", "skill", "hook", "prompt", "sandbox")
_ADMIN_ROLES = ("super_admin", "admin", "reviewer", "user")
_SECURITY_SEVERITIES = ("info", "warning", "critical")
_AUDIT_SOURCES = ("server", "cli")


def _command_value(value):
    return value.default if isinstance(value, OptionInfo) else value


def _command_progress(output: OutputMode | str, message: str | None = None):
    return nullcontext() if _command_value(output) == "json" else spinner(message)


def _command_choice(value: str, allowed: tuple[str, ...], label: str, operation: str) -> str:
    normalized = value.strip().lower()
    if normalized not in allowed:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown {label}: {value}.",
            operation=operation,
            resource=label,
            remediation=f"Choose from: {', '.join(allowed)}.",
        )
    return normalized


def _admin_user(email: str, output: OutputMode | str, operation: str) -> dict:
    normalized = email.strip().lower()
    with _command_progress(output, "Looking up user..."):
        users = client.get("/api/v1/admin/users")
    match = next((user for user in users if str(user.get("email", "")).lower() == normalized), None)
    if match is None:
        fail(
            ErrorCategory.NOT_FOUND,
            f"User not found: {email}.",
            operation=operation,
            resource="user",
            remediation="Run `observal admin users --output json` and retry with an existing email.",
        )
    return match


def _uuid(value: str, label: str, operation: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        fail(
            ErrorCategory.VALIDATION,
            f"Invalid {label}: {value}.",
            operation=operation,
            resource=label,
            remediation="Use a complete UUID.",
        )


def _atomic_write(path: Path, content: str, operation: str) -> None:
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as file:
            temporary = Path(file.name)
            file.write(content)
        temporary.replace(path)
    except OSError as error:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        fail(
            ErrorCategory.UNAVAILABLE,
            f"Could not write audit export: {path}.",
            operation=operation,
            resource=str(path),
            remediation="Check the destination path and permissions, then retry.",
            detail=repr(error),
        )


ops_app = typer.Typer(
    name="ops",
    help=(
        "Observability and operational commands (sessions, telemetry, rankings, feedback, insights)\n\n"
        "Examples:\n"
        "  observal ops traces\n"
        "  observal ops top --type agent\n"
        "  observal ops telemetry status"
    ),
    no_args_is_help=True,
)


# ── Telemetry ────────────────────────────────────────────

telemetry_app = typer.Typer(
    help=(
        "Telemetry health commands\n\nExamples:\n  observal ops telemetry status\n  observal ops telemetry status --output json"
    )
)


@telemetry_app.command(name="status")
def telemetry_status(
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Check telemetry data flow status.

    Shows server-side event counts (tool calls, interactions) for the
    last hour and durable local session outbox statistics.
    Useful for verifying that session telemetry is reaching the server.

    Examples:

        observal ops telemetry status
    """
    with _command_progress(output, "Checking telemetry..."):
        server = client.get("/api/v1/telemetry/status")

    from observal_cli.telemetry_buffer import stats as buffer_stats

    try:
        outbox = {"available": True, **buffer_stats()}
    except (OSError, RuntimeError, sqlite3.Error) as error:
        outbox = {"available": False, "error": type(error).__name__}

    result = {"server": server, "outbox": outbox}
    if output == "json":
        output_json(result)
        return

    rprint(f"  Status:       [green]{esc(server.get('status', 'unknown'))}[/green]")
    rprint(f"  Tool calls:   {server.get('tool_call_events', 0)} (last hour)")
    rprint(f"  Interactions: {server.get('agent_interaction_events', 0)} (last hour)")
    rprint()
    rprint("  [bold]Durable Session Outbox[/bold]")
    if not outbox["available"]:
        rprint(f"  [yellow]Unavailable:[/yellow] {esc(outbox['error'])}")
        return
    rprint(f"  Pending:      {outbox['pending']} batches")
    rprint(f"  Disk:         {outbox['bytes'] / 1024:.1f} KiB")
    if outbox["oldest_pending"]:
        rprint(f"  Oldest:       {esc(outbox['oldest_pending'])} UTC")
    if outbox["last_sync"]:
        rprint(f"  Last sync:    {esc(outbox['last_sync'])} UTC")
    if outbox["total"] == 0:
        rprint("  [dim]Outbox is empty (all observed records acknowledged)[/dim]")


@ops_app.command(name="top")
def _top(
    item_type: str = typer.Option("mcp", "--type", "-t", help="mcp or agent"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Show top MCP servers or agents by usage.

    Lists the highest-download items in descending order. Defaults to
    MCP servers; use --type agent to see top agents instead.

    Examples:

        observal ops top

        observal ops top --type agent

        observal ops top --output json
    """
    _top_impl(item_type, output)


def _top_impl(item_type, output):
    item_type = _command_choice(item_type, _RANKING_TYPES, "ranking type", "List top registry items")
    endpoint = "/api/v1/overview/top-mcps" if item_type == "mcp" else "/api/v1/overview/top-agents"
    with _command_progress(output):
        data = client.get(endpoint)
    if output == "json":
        output_json(data)
        return
    if not data:
        rprint(f"[dim]No {item_type} data yet.[/dim]")
        return
    label = "MCP Servers" if item_type == "mcp" else "Agents"
    table = Table(title=f"Top {label}", show_lines=False, padding=(0, 1))
    table.add_column("#", style="dim", width=3)
    table.add_column("Name", style="bold")
    table.add_column("Downloads", justify="right")
    table.add_column("ID", style="dim", max_width=12)
    for i, item in enumerate(data, 1):
        table.add_row(
            str(i), esc(item.get("name", "")), str(int(item.get("value", 0))), esc(str(item.get("id", ""))[:8] + "…")
        )
    console.print(table)


# ── Feedback (on ops_app) ────────────────────────────────


@ops_app.command(name="rate")
def _rate(
    listing_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    stars: int = typer.Option(..., "--stars", "-s", min=1, max=5, help="Rating 1-5"),
    listing_type: str = typer.Option("mcp", "--type", "-t", help="mcp, agent, skill, hook, prompt, or sandbox"),
    comment: str | None = typer.Option(None, "--comment", "-c"),
    anonymous: bool = typer.Option(False, "--anonymous", "-a", help="Submit anonymously"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Rate an MCP server, agent, or component.

    Submits a 1-5 star review. Each user can only submit one review per
    item. Use `observal ops rate-update` to change it later.

    Examples:

        observal ops rate my-mcp --stars 5

        observal ops rate my-agent --type agent -s 4 -c "Great tool usage"

        observal ops rate my-mcp --stars 5 --anonymous
    """
    _rate_impl(listing_id, stars, listing_type, comment, anonymous, output)


def _rate_impl(listing_id, stars, listing_type, comment, anonymous=False, output="table"):
    listing_type = _command_choice(listing_type, _FEEDBACK_TYPES, "feedback type", "Rate registry item")
    if comment is not None and len(comment) > 5000:
        fail(
            ErrorCategory.VALIDATION,
            "Feedback comment must be at most 5000 characters.",
            operation="Rate registry item",
            resource="feedback comment",
            remediation="Shorten the comment and retry.",
        )
    resolved = _resolve_listing_id(listing_id, listing_type)
    with _command_progress(output, "Submitting rating..."):
        result = client.post(
            "/api/v1/feedback",
            {
                "listing_id": resolved,
                "listing_type": listing_type,
                "rating": stars,
                "comment": comment,
                "anonymous": anonymous,
            },
        )
    if output == "json":
        output_json(result)
        return
    rprint(f"[green]\u2713 Rated {star_rating(stars)}[/green]")


@ops_app.command(name="rate-update")
def _rate_update(
    listing_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    listing_type: str = typer.Option("mcp", "--type", "-t", help="mcp, agent, skill, hook, prompt, or sandbox"),
    stars: int | None = typer.Option(None, "--stars", "-s", min=1, max=5, help="New rating 1-5"),
    comment: str | None = typer.Option(None, "--comment", "-c", help="New comment"),
    anonymous: bool | None = typer.Option(None, "--anonymous/--no-anonymous", help="Set or unset anonymous flag"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Update your existing review for an item.

    Only the fields you provide will be changed.

    Examples:

        observal ops rate-update my-mcp --stars 4

        observal ops rate-update my-mcp --comment "Updated opinion" --anonymous
    """
    listing_type = _command_choice(listing_type, _FEEDBACK_TYPES, "feedback type", "Update registry feedback")
    body = {}
    if stars is not None:
        body["rating"] = stars
    if comment is not None:
        body["comment"] = comment
    if anonymous is not None:
        body["anonymous"] = anonymous
    if not body:
        fail(
            ErrorCategory.VALIDATION,
            "No feedback changes were provided.",
            operation="Update registry feedback",
            resource="feedback update",
            remediation="Provide --stars, --comment, --anonymous, or --no-anonymous.",
        )
    if comment is not None and len(comment) > 5000:
        fail(
            ErrorCategory.VALIDATION,
            "Feedback comment must be at most 5000 characters.",
            operation="Update registry feedback",
            resource="feedback comment",
            remediation="Shorten the comment and retry.",
        )
    resolved = _resolve_listing_id(listing_id, listing_type)
    with _command_progress(output, "Fetching your review..."):
        review = client.get(f"/api/v1/feedback/mine/{listing_type}/{resolved}")
    with _command_progress(output, "Updating review..."):
        result = client.put(f"/api/v1/feedback/{review['id']}", body)
    if output == "json":
        output_json(result)
        return
    rprint("[green]\u2713 Review updated[/green]")


@ops_app.command(name="rate-delete")
def _rate_delete(
    listing_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    listing_type: str = typer.Option("mcp", "--type", "-t", help="mcp, agent, skill, hook, prompt, or sandbox"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Delete your review for an item.

    Permanently removes your review. You can submit a new one afterwards.

    Examples:

        observal ops rate-delete my-mcp

        observal ops rate-delete my-agent --type agent
    """
    listing_type = _command_choice(listing_type, _FEEDBACK_TYPES, "feedback type", "Delete registry feedback")
    yes = _command_value(yes)
    output = _command_value(output)
    if output == "json" and not yes:
        fail(
            ErrorCategory.VALIDATION,
            "JSON mode cannot prompt before deleting feedback.",
            operation="Delete registry feedback",
            resource="feedback",
            remediation="Add --yes to confirm deletion.",
        )
    resolved = _resolve_listing_id(listing_id, listing_type)
    with _command_progress(output, "Fetching your review..."):
        review = client.get(f"/api/v1/feedback/mine/{listing_type}/{resolved}")
    if not yes and not typer.confirm("Delete your review permanently?"):
        raise typer.Abort()
    with _command_progress(output, "Deleting review..."):
        result = client.delete(f"/api/v1/feedback/{review['id']}")
    if output == "json":
        output_json(result)
        return
    rprint("[green]\u2713 Review deleted[/green]")


def _resolve_listing_id(listing_id: str, listing_type: str) -> str:
    """Resolve a UUID, canonical name, row, alias, or unambiguous bare name."""
    import uuid

    try:
        return str(uuid.UUID(listing_id))
    except ValueError:
        pass
    resolved = client.resolve_registry_reference(listing_type, listing_id)
    try:
        return str(uuid.UUID(resolved))
    except ValueError:
        result = client.get(
            "/api/v1/registry/resolve",
            params={"type": listing_type, "identifier": resolved},
        )
        return str(result["id"])


@ops_app.command(name="feedback")
def _feedback(
    listing_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    listing_type: str = typer.Option("mcp", "--type", "-t"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Show feedback for an MCP server or agent.

    Displays the average rating, total review count, and individual
    reviews (stars + comments) for the given item.

    Examples:

        observal ops feedback my-mcp

        observal ops feedback my-agent --type agent

        observal ops feedback my-mcp --output json
    """
    _feedback_impl(listing_id, listing_type, output)


def _feedback_impl(listing_id, listing_type, output):
    listing_type = _command_choice(listing_type, _FEEDBACK_TYPES, "feedback type", "Show registry feedback")
    resolved = _resolve_listing_id(listing_id, listing_type)
    with _command_progress(output):
        data = client.get(f"/api/v1/feedback/{listing_type}/{resolved}")
        summary = client.get(f"/api/v1/feedback/summary/{resolved}")

    if output == "json":
        output_json({"summary": summary, "reviews": data})
        return

    if not data:
        rprint("[dim]No feedback yet.[/dim]")
        return

    avg = summary.get("average_rating", 0)
    total = summary.get("total_reviews", 0)
    rprint(f"\n  {star_rating(round(avg))} [bold]{avg:.1f}[/bold]/5 ({total} reviews)\n")
    for fb in data:
        stars_str = star_rating(fb.get("rating", 0))
        comment = f"  {esc(fb['comment'])}" if fb.get("comment") else ""
        rprint(f"  {stars_str}{comment}")
    rprint()


# ── Admin ────────────────────────────────────────────────

admin_app = typer.Typer(
    help=(
        "Core administration commands (use 'observal review' for submission reviews)\n\n"
        "Examples:\n"
        "  observal admin diagnostics\n"
        "  observal admin users\n"
        "  observal admin settings"
    )
)


@admin_app.command(name="settings")
def admin_settings(output: OutputMode = typer.Option("table", "--output", "-o")):
    """List server settings.

    Displays all configured key-value server settings.

    Examples:

        observal admin settings

        observal admin settings --output json
    """
    with _command_progress(output):
        data = client.get("/api/v1/admin/settings")
    if output == "json":
        output_json(data)
        return
    if not data:
        rprint("[dim]No settings configured.[/dim]")
        return
    table = Table(title="Admin Settings", show_lines=False, padding=(0, 1))
    table.add_column("Key", style="bold")
    table.add_column("Value")
    for item in data:
        table.add_row(esc(item.get("key", "")), esc(item.get("value", "")))
    console.print(table)


@admin_app.command(name="set")
def admin_set(
    key: str = typer.Argument(...),
    value: str = typer.Argument(...),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Set a server setting.

    Creates or updates a key-value server configuration entry
    on the server. Requires admin privileges.

    Examples:

        observal admin set max_agents_per_user 10

        observal admin set telemetry_retention_days 90
    """
    with _command_progress(output):
        result = client.put(f"/api/v1/admin/settings/{quote(key, safe='')}", {"value": value})
    if output == "json":
        output_json(result)
        return
    rprint(f"[green]✓ Updated {esc(result.get('key', key))}[/green]")


@admin_app.command(name="users")
def admin_users(output: OutputMode = typer.Option("table", "--output", "-o")):
    """List all users.

    Displays all registered users with their email, name, role, and ID.
    Requires admin privileges.

    Examples:

        observal admin users

        observal admin users --output json
    """
    with _command_progress(output):
        data = client.get("/api/v1/admin/users")
    if output == "json":
        output_json(data)
        return
    table = Table(title=f"Users ({len(data)})", show_lines=False, padding=(0, 1))
    table.add_column("#", style="dim", width=3)
    table.add_column("Email")
    table.add_column("Name", style="bold")
    table.add_column("Role")
    table.add_column("ID", style="dim", max_width=12)
    role_colors = {"super_admin": "magenta", "admin": "green", "reviewer": "cyan", "user": "white"}
    for i, user in enumerate(data, 1):
        role = str(user.get("role", ""))
        color = role_colors.get(role, "white")
        table.add_row(
            str(i),
            esc(user.get("email", "")),
            esc(user.get("name", "")),
            f"[{color}]{esc(role)}[/{color}]",
            esc(str(user.get("id", ""))[:8] + "…"),
        )
    console.print(table)


@admin_app.command(name="create-user")
def admin_create_user(
    email: str = typer.Argument(..., help="Email address for the new user"),
    name: str = typer.Argument(..., help="Full name of the user"),
    username: str = typer.Option(None, "--username", "-u", help="Username (optional)"),
    role: str = typer.Option("reviewer", "--role", "-r", help="Role: super_admin, admin, reviewer, or user"),
    password: str = typer.Option(None, "--password", "-p", help="Password (auto-generated if omitted)"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Create a new user account. Requires admin privileges.

    If no password is provided, a secure random password will be generated.

    Examples:

        observal admin create-user alice@example.com "Alice Smith"

        observal admin create-user bob@example.com "Bob Jones" --role admin

        observal admin create-user carol@example.com "Carol Lee" -u carol -r reviewer -p s3cret
    """
    role = _command_choice(role, _ADMIN_ROLES, "user role", "Create administrator-managed user")
    body: dict = {"email": email.strip().lower(), "name": name.strip(), "role": role}
    if username:
        body["username"] = username
    if password:
        body["password"] = password

    with _command_progress(output, "Creating user..."):
        data = client.post("/api/v1/admin/users", body)

    if output == "json":
        output_json(data)
        return

    rprint("\n[green]User created successfully.[/green]\n")
    rprint(f"  [bold]Name:[/bold]     {esc(data.get('name', ''))}")
    rprint(f"  [bold]Email:[/bold]    {esc(data.get('email', ''))}")
    if data.get("username"):
        rprint(f"  [bold]Username:[/bold] {esc(data['username'])}")
    rprint(f"  [bold]Role:[/bold]     {esc(data.get('role', ''))}")
    rprint(f"  [bold]ID:[/bold]       {esc(data.get('id', ''))}")
    rprint(f"\n[yellow]Password:[/yellow] {esc(data.get('password', ''))}")
    rprint("[dim]Save this, it will not be shown again.[/dim]")


@admin_app.command(name="reset-password")
def admin_reset_password(
    email: str = typer.Argument(..., help="Email of the user to reset"),
    generate: bool = typer.Option(False, "--generate", "-g", help="Generate a secure random password"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Reset a user's password. Requires admin privileges.

    JSON mode requires --generate and never prompts.

    Examples:

        observal admin reset-password alice@example.com

        observal admin reset-password alice@example.com --generate --output json
    """
    output = _command_value(output)
    if output == "json" and not generate:
        fail(
            ErrorCategory.VALIDATION,
            "JSON mode cannot prompt for a new password.",
            operation="Reset user password",
            resource="new password",
            remediation="Add --generate.",
        )
    match = _admin_user(email, output, "Reset user password")
    if generate:
        body: dict = {"generate": True}
    else:
        new_password = password_input("New password")
        confirmation = password_input("Confirm password")
        if new_password != confirmation:
            fail(
                ErrorCategory.VALIDATION,
                "Passwords do not match.",
                operation="Reset user password",
                resource="new password",
                remediation="Enter the same password twice.",
            )
        body = {"new_password": new_password}

    with _command_progress(output, "Resetting password..."):
        result = client.put(f"/api/v1/admin/users/{match['id']}/password", body)
    if output == "json":
        output_json(result)
        return
    rprint(f"[green]{esc(result.get('message', 'Password reset.'))}[/green]")
    if "generated_password" in result:
        rprint(f"\n[yellow]Generated password:[/yellow] {esc(result['generated_password'])}")
        rprint("[dim]Save this, it will not be shown again.[/dim]")


@admin_app.command(name="delete-user")
def admin_delete_user(
    email: str = typer.Argument(..., help="Email of the user to delete"),
    force: bool = typer.Option(False, "--force", "--yes", "-f", help="Skip confirmation prompt"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Delete a user account. Requires admin privileges.

    JSON mode requires --force and never prompts.

    Examples:

        observal admin delete-user alice@example.com

        observal admin delete-user alice@example.com --force --output json
    """
    force = _command_value(force)
    output = _command_value(output)
    if output == "json" and not force:
        fail(
            ErrorCategory.VALIDATION,
            "JSON mode cannot prompt before deleting a user.",
            operation="Delete administrator-managed user",
            resource="user",
            remediation="Add --force to confirm deletion.",
        )
    match = _admin_user(email, output, "Delete administrator-managed user")
    if not force:
        rprint(
            f"\n  [bold]{esc(match.get('name', ''))}[/bold] "
            f"({esc(match.get('email', ''))}), {esc(match.get('role', ''))}"
        )
        typer.confirm("\nPermanently delete this user?", abort=True)

    with _command_progress(output, "Deleting user..."):
        client.delete(f"/api/v1/admin/users/{match['id']}")
    result = {"deleted": True, "id": match.get("id"), "email": match.get("email")}
    if output == "json":
        output_json(result)
        return
    rprint(f"[green]Deleted user {esc(match.get('email', ''))}[/green]")


# ── Diagnostics ─────────────────────────────────────────


@admin_app.command(name="diagnostics")
def admin_diagnostics(output: OutputMode = typer.Option("table", "--output", "-o")):
    """Show system diagnostics and health status.

    Reports overall system health, database connectivity, JWT key status,
    and runtime configuration issues. Useful for troubleshooting
    deployment problems.

    Examples:

        observal admin diagnostics

        observal admin diagnostics --output json
    """
    with _command_progress(output):
        data = client.get("/api/v1/admin/diagnostics")
    if output == "json":
        output_json(data)
        return

    overall = data.get("status", "unknown")
    color = {"ok": "green", "degraded": "yellow", "unhealthy": "red"}.get(overall, "white")
    rprint(f"\n  Overall: [{color}]{esc(overall)}[/{color}]")

    checks = data.get("checks", {})

    db = checks.get("database", {})
    if db:
        db_color = "green" if db.get("status") == "ok" else "red"
        rprint(f"\n  Database: [{db_color}]{esc(db.get('status', 'unknown'))}[/{db_color}]")
        rprint(f"    Users: {db.get('users', '?')}")

    jwt_info = checks.get("jwt_keys", {})
    if jwt_info:
        jwt_color = "green" if jwt_info.get("status") == "ok" else "red"
        rprint(f"\n  JWT:     [{jwt_color}]{esc(jwt_info.get('status', 'unknown'))}[/{jwt_color}]")
        rprint(f"    Algorithm: {esc(jwt_info.get('algorithm', '?'))}")

    runtime_cfg = checks.get("runtime_config", {})
    if runtime_cfg:
        issues = runtime_cfg.get("issues", [])
        if issues:
            rprint("\n  [yellow]Configuration issues:[/yellow]")
            for issue in issues:
                rprint(f"    • {esc(issue)}")
        else:
            rprint("\n  Configuration: [green]ok[/green]")
    rprint()


# ── SAML Config ─────────────────────────────────────────


@admin_app.command(name="saml-config")
def admin_saml_config(output: OutputMode = typer.Option("table", "--output", "-o")):
    """View current SAML SSO configuration.

    Displays the IdP entity ID, SSO/SLO URLs, SP entity ID, and whether
    SAML and JIT provisioning are active.

    Examples:

        observal admin saml-config

        observal admin saml-config --output json
    """
    with _command_progress(output):
        data = client.get("/api/v1/admin/saml-config")
    if output == "json":
        output_json(data)
        return
    if not data or not data.get("configured"):
        rprint("[dim]SAML SSO is not configured.[/dim]")
        rprint("Use [bold]observal admin saml-config-set[/bold] to configure.")
        return

    rprint("\n[bold]SAML SSO Configuration[/bold]\n")
    for key in ("source", "idp_entity_id", "idp_sso_url", "idp_slo_url", "sp_entity_id", "active", "jit_provisioning"):
        value = data.get(key)
        if value is not None:
            display = "[green]Yes[/green]" if value is True else "[red]No[/red]" if value is False else esc(value)
            rprint(f"  {key}: {display}")
    rprint()


@admin_app.command(name="saml-config-set")
def admin_saml_config_set(
    idp_entity_id: str = typer.Option(None, "--idp-entity-id", help="IdP Entity ID"),
    idp_sso_url: str = typer.Option(None, "--idp-sso-url", help="IdP SSO URL"),
    idp_slo_url: str = typer.Option(None, "--idp-slo-url", help="IdP SLO URL (optional)"),
    idp_x509_cert: str = typer.Option(None, "--idp-x509-cert", help="IdP X.509 certificate (PEM)"),
    sp_entity_id: str = typer.Option(None, "--sp-entity-id", help="SP Entity ID"),
    jit: bool = typer.Option(True, "--jit/--no-jit", help="Enable JIT user provisioning"),
    active: bool = typer.Option(True, "--active/--inactive", help="Enable SAML SSO"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Create or update SAML SSO configuration.

    Examples:

        observal admin saml-config-set --idp-entity-id https://idp.example.com \\
            --idp-sso-url https://idp.example.com/sso \\
            --idp-x509-cert "$(cat idp-cert.pem)"
    """
    required = {
        "IdP entity ID": idp_entity_id,
        "IdP SSO URL": idp_sso_url,
        "IdP X.509 certificate": idp_x509_cert,
    }
    missing = [label for label, value in required.items() if not value or not value.strip()]
    if missing:
        fail(
            ErrorCategory.VALIDATION,
            f"Missing required SAML values: {', '.join(missing)}.",
            operation="Update SAML configuration",
            resource="SAML configuration",
            remediation="Provide --idp-entity-id, --idp-sso-url, and --idp-x509-cert.",
        )
    body: dict = {
        "idp_entity_id": idp_entity_id.strip(),
        "idp_sso_url": idp_sso_url.strip(),
        "idp_x509_cert": idp_x509_cert.strip(),
        "active": active,
        "jit_provisioning": jit,
    }
    if idp_slo_url:
        body["idp_slo_url"] = idp_slo_url.strip()
    if sp_entity_id:
        body["sp_entity_id"] = sp_entity_id.strip()

    with _command_progress(output, "Updating SAML config..."):
        result = client.put("/api/v1/admin/saml-config", body)
    if output == "json":
        output_json(result)
        return
    rprint("[green]SAML SSO configuration updated.[/green]")
    if result.get("sp_entity_id"):
        rprint(f"  SP Entity ID:  {esc(result['sp_entity_id'])}")
    if result.get("sp_acs_url"):
        rprint(f"  SP ACS URL:    {esc(result['sp_acs_url'])}")
    if result.get("sp_metadata_url"):
        rprint(f"  SP Metadata:   {esc(result['sp_metadata_url'])}")


@admin_app.command(name="saml-config-delete")
def admin_saml_config_delete(
    force: bool = typer.Option(False, "--force", "--yes", "-f", help="Skip confirmation prompt"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Delete SAML SSO configuration. Disables SAML SSO.

    Removes the entire SAML configuration, disabling SSO for all users.
    Prompts for confirmation unless --force is passed.

    Examples:

        observal admin saml-config-delete

        observal admin saml-config-delete --force
    """
    force = _command_value(force)
    output = _command_value(output)
    if output == "json" and not force:
        fail(
            ErrorCategory.VALIDATION,
            "JSON mode cannot prompt before deleting SAML configuration.",
            operation="Delete SAML configuration",
            resource="SAML configuration",
            remediation="Add --force to confirm deletion.",
        )
    if not force:
        typer.confirm("This will disable SAML SSO for all users. Continue?", abort=True)
    with _command_progress(output, "Deleting SAML config..."):
        result = client.delete("/api/v1/admin/saml-config")
    if output == "json":
        output_json(result)
        return
    rprint("[green]SAML SSO configuration deleted.[/green]")


# ── SCIM Tokens ─────────────────────────────────────────


@admin_app.command(name="scim-tokens")
def admin_scim_tokens(output: OutputMode = typer.Option("table", "--output", "-o")):
    """List SCIM provisioning tokens.

    Shows all SCIM bearer tokens with their prefix, description,
    active status, and creation date.

    Examples:

        observal admin scim-tokens

        observal admin scim-tokens --output json
    """
    with _command_progress(output):
        data = client.get("/api/v1/admin/scim-tokens")
    if output == "json":
        output_json(data)
        return
    if not data:
        rprint("[dim]No SCIM tokens configured.[/dim]")
        rprint("Use [bold]observal admin scim-token-create[/bold] to create one.")
        return
    table = Table(title="SCIM Tokens", show_lines=False, padding=(0, 1))
    table.add_column("ID", style="dim", max_width=12)
    table.add_column("Prefix")
    table.add_column("Description")
    table.add_column("Active")
    table.add_column("Created")
    for t in data:
        active = "[green]Yes[/green]" if t.get("active") else "[red]No[/red]"
        created = t.get("created_at", "")[:10] if t.get("created_at") else "-"
        table.add_row(
            esc(str(t.get("id", ""))[:8] + "..."),
            esc(t.get("token_prefix", "")),
            esc(t.get("description", "-")),
            active,
            esc(created),
        )
    console.print(table)


@admin_app.command(name="scim-token-create")
def admin_scim_token_create(
    description: str = typer.Option("", "--description", "-d", help="Token description"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Create a new SCIM provisioning token.

    The token is shown once on creation. Save it securely.

    Examples:
        observal admin scim-token-create
        observal admin scim-token-create --description "Okta SCIM sync"
    """
    body: dict = {}
    if description:
        body["description"] = description
    with _command_progress(output, "Creating SCIM token..."):
        result = client.post("/api/v1/admin/scim-tokens", body)
    if output == "json":
        output_json(result)
        return
    rprint("[green]SCIM token created.[/green]")
    rprint(f"\n[yellow]Token:[/yellow] {esc(result.get('token', ''))}")
    rprint("[dim]Save this -- it will not be shown again.[/dim]")
    if result.get("description"):
        rprint(f"  Description: {esc(result['description'])}")


@admin_app.command(name="scim-token-revoke")
def admin_scim_token_revoke(
    token_id: str = typer.Argument(..., help="Token ID to revoke"),
    force: bool = typer.Option(False, "--force", "--yes", "-f", help="Skip confirmation prompt"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Revoke a SCIM provisioning token.

    Permanently disables the specified SCIM token so it can no longer
    be used for provisioning. Prompts for confirmation unless --force.

    Examples:

        observal admin scim-token-revoke abc12345-uuid

        observal admin scim-token-revoke abc12345-uuid --force
    """
    token_id = _uuid(token_id, "SCIM token ID", "Revoke SCIM token")
    force = _command_value(force)
    output = _command_value(output)
    if output == "json" and not force:
        fail(
            ErrorCategory.VALIDATION,
            "JSON mode cannot prompt before revoking a SCIM token.",
            operation="Revoke SCIM token",
            resource="SCIM token",
            remediation="Add --force to confirm revocation.",
        )
    if not force:
        typer.confirm(f"Revoke SCIM token {token_id[:8]}...?", abort=True)
    with _command_progress(output, "Revoking SCIM token..."):
        result = client.delete(f"/api/v1/admin/scim-tokens/{token_id}")
    if output == "json":
        output_json(result)
        return
    rprint(f"[green]SCIM token {esc(token_id[:8])}... revoked.[/green]")


# ── Security Events ─────────────────────────────────────


@admin_app.command(name="security-events")
def admin_security_events(
    event_type: str | None = typer.Option(None, "--type", "-t", help="Filter by event type"),
    severity: str | None = typer.Option(None, "--severity", "-s", help="Filter: info, warning, critical"),
    actor: str | None = typer.Option(None, "--actor", "-a", help="Filter by actor email"),
    limit: int = typer.Option(50, "--limit", "-n", min=1, max=1000),
    output: OutputMode = typer.Option("table", "--output", "-o"),
    offset: int = typer.Option(0, "--offset", min=0, help="Skip the first N events"),
):
    """View security events log.

    Lists security-relevant events (login attempts, permission changes,
    etc.) with optional filters on type, severity, and actor.

    Examples:

        observal admin security-events

        observal admin security-events --type auth.login --severity critical

        observal admin security-events --actor alice@example.com -n 100
    """
    offset = _command_value(offset)
    params: dict = {"limit": limit, "offset": offset}
    if event_type:
        params["event_type"] = event_type.strip()
    if severity:
        params["severity"] = _command_choice(
            severity, _SECURITY_SEVERITIES, "security severity", "List security events"
        )
    if actor:
        params["actor_email"] = actor.strip()

    with _command_progress(output):
        data = client.get("/api/v1/admin/security-events", params=params)
    events = data.get("events", data) if isinstance(data, dict) else data
    if output == "json":
        output_json(data)
        return
    if not events:
        rprint("[dim]No security events found.[/dim]")
        return
    table = Table(title=f"Security Events ({len(events)})", show_lines=False, padding=(0, 1))
    table.add_column("Time", style="dim", max_width=19)
    table.add_column("Type")
    table.add_column("Severity")
    table.add_column("Actor")
    table.add_column("Outcome")
    table.add_column("Detail", max_width=40)
    for ev in events:
        sev = ev.get("severity", "")
        sev_color = {"critical": "red", "warning": "yellow", "info": "dim"}.get(sev, "white")
        outcome = ev.get("outcome", "")
        outcome_color = "green" if outcome == "success" else "red" if outcome == "failure" else "white"
        timestamp = str(ev.get("timestamp") or ev.get("created_at") or "")[:19]
        table.add_row(
            esc(timestamp),
            esc(ev.get("event_type", "")),
            f"[{sev_color}]{esc(sev)}[/{sev_color}]",
            esc(ev.get("actor_email", "-")),
            f"[{outcome_color}]{esc(outcome)}[/{outcome_color}]",
            esc(str(ev.get("detail") or "")[:40]),
        )
    console.print(table)


# ── Audit Log ───────────────────────────────────────────


@admin_app.command(name="audit-log")
def admin_audit_log(
    action: str | None = typer.Option(None, "--action", "-a", help="Filter by action"),
    actor: str | None = typer.Option(None, "--actor", help="Filter by actor name, username, or email"),
    resource_type: str | None = typer.Option(None, "--resource-type", "-r", help="Filter by resource type"),
    limit: int = typer.Option(50, "--limit", "-n", min=1, max=500),
    output: OutputMode = typer.Option("table", "--output", "-o"),
    sensitivity: str | None = typer.Option(None, "--sensitivity", help="Filter by sensitivity"),
    outcome: str | None = typer.Option(None, "--outcome", help="Filter by outcome"),
    source: str | None = typer.Option(None, "--source", help="Filter by source: server or cli"),
    start_date: str | None = typer.Option(None, "--start-date", help="Filter from an ISO 8601 date or timestamp"),
    end_date: str | None = typer.Option(None, "--end-date", help="Filter through an ISO 8601 date or timestamp"),
    offset: int = typer.Option(0, "--offset", min=0, help="Skip the first N entries"),
):
    """Query the compliance audit log.

    Examples:

        observal admin audit-log

        observal admin audit-log --action auth.login --limit 100 --output json
    """
    sensitivity = _command_value(sensitivity)
    outcome = _command_value(outcome)
    source = _command_value(source)
    start_date = _command_value(start_date)
    end_date = _command_value(end_date)
    offset = _command_value(offset)
    params: dict = {"limit": limit, "offset": offset}
    for key, value in (
        ("action", action),
        ("actor", actor),
        ("resource_type", resource_type),
        ("sensitivity", sensitivity),
        ("outcome", outcome),
        ("start_date", start_date),
        ("end_date", end_date),
    ):
        if value:
            params[key] = value.strip()
    if source:
        params["source"] = _command_choice(source, _AUDIT_SOURCES, "audit source", "Query audit log")

    with _command_progress(output):
        data = client.get("/api/v1/admin/audit-log", params=params)
    if output == "json":
        output_json(data)
        return
    if not data:
        rprint("[dim]No audit log entries found.[/dim]")
        return
    table = Table(title=f"Audit Log ({len(data)} entries)", show_lines=False, padding=(0, 1))
    table.add_column("Time", style="dim", max_width=19)
    table.add_column("Actor")
    table.add_column("Action", style="bold")
    table.add_column("Resource")
    table.add_column("IP", style="dim")
    table.add_column("Detail", max_width=30)
    for entry in data:
        timestamp = str(entry.get("timestamp") or entry.get("created_at") or "")[:19]
        resource = str(entry.get("resource_type") or "")
        if entry.get("resource_name"):
            resource += f"/{entry['resource_name']}"
        table.add_row(
            esc(timestamp),
            esc(entry.get("actor_email", "-")),
            esc(entry.get("action", "")),
            esc(resource),
            esc(entry.get("ip_address", "-")),
            esc(str(entry.get("detail") or "")[:30]),
        )
    console.print(table)


@admin_app.command(name="audit-log-export")
def admin_audit_log_export(
    action: str | None = typer.Option(None, "--action", "-a", help="Filter by action"),
    actor: str | None = typer.Option(None, "--actor", help="Filter by actor name, username, or email"),
    file: str | None = typer.Option(None, "--file", "-f", help="Write output to file"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="CSV in table mode, JSON in JSON mode"),
    resource_type: str | None = typer.Option(None, "--resource-type", "-r", help="Filter by resource type"),
    sensitivity: str | None = typer.Option(None, "--sensitivity", help="Filter by sensitivity"),
    outcome: str | None = typer.Option(None, "--outcome", help="Filter by outcome"),
    source: str | None = typer.Option(None, "--source", help="Filter by source: server or cli"),
    start_date: str | None = typer.Option(None, "--start-date", help="Filter from an ISO 8601 date or timestamp"),
    end_date: str | None = typer.Option(None, "--end-date", help="Filter through an ISO 8601 date or timestamp"),
    force: bool = typer.Option(False, "--force", "--yes", help="Overwrite an existing export file"),
):
    """Export the compliance audit log as CSV or JSON.

    Examples:

        observal admin audit-log-export

        observal admin audit-log-export --file audit.csv

        observal admin audit-log-export --output json --file audit.json
    """
    output = _command_value(output)
    force = _command_value(force)
    resource_type = _command_value(resource_type)
    sensitivity = _command_value(sensitivity)
    outcome = _command_value(outcome)
    source = _command_value(source)
    start_date = _command_value(start_date)
    end_date = _command_value(end_date)
    params: dict = {}
    for key, value in (
        ("action", action),
        ("actor", actor),
        ("resource_type", resource_type),
        ("sensitivity", sensitivity),
        ("outcome", outcome),
        ("start_date", start_date),
        ("end_date", end_date),
    ):
        if value:
            params[key] = value.strip()
    if source:
        params["source"] = _command_choice(source, _AUDIT_SOURCES, "audit source", "Export audit log")

    destination = Path(file).expanduser() if file else None
    if destination is not None and destination.exists() and not force:
        if output == "json":
            fail(
                ErrorCategory.CONFLICT,
                f"Audit export already exists: {destination}.",
                operation="Export audit log",
                resource=str(destination),
                remediation="Choose another path or add --force.",
            )
        typer.confirm(f"Overwrite {destination}?", abort=True)

    if output == "json":
        params["format"] = "json"
        with _command_progress(output, "Exporting audit log..."):
            data = client.get("/api/v1/admin/audit-log/export", params=params)
        content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    else:
        with _command_progress("json" if destination is None else output, "Exporting audit log..."):
            content = client.get_text("/api/v1/admin/audit-log/export", params=params or None, content_type="text/csv")
        data = None

    if destination is not None:
        _atomic_write(destination, content, "Export audit log")
        if output == "json":
            output_json(
                {
                    "path": str(destination),
                    "format": "json",
                    "record_count": data.get("record_count") if isinstance(data, dict) else None,
                }
            )
        else:
            rprint(f"[green]Audit log exported to {esc(destination)}[/green]")
        return
    if output == "json":
        output_json(data)
    else:
        typer.echo(content, nl=False)


# ── Trace Privacy ───────────────────────────────────────


@admin_app.command(name="trace-privacy")
def admin_trace_privacy(
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """View trace privacy setting.

    Shows whether trace privacy (sensitive data redaction) is currently
    enabled or disabled for the deployment.

    Examples:

        observal admin trace-privacy
    """
    with _command_progress(output):
        data = client.get("/api/v1/admin/trace-privacy")
    if output == "json":
        output_json(data)
        return
    enabled = data.get("trace_privacy", False)
    status = "[green]enabled[/green]" if enabled else "[red]disabled[/red]"
    rprint(f"  Trace privacy: {status}")


@admin_app.command(name="trace-privacy-set")
def admin_trace_privacy_set(
    enabled: bool = typer.Argument(..., help="true or false"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Enable or disable trace privacy (redacts sensitive trace data).

    When enabled, the server scrubs PII and secrets from stored traces.
    When disabled, traces are stored verbatim.

    Examples:

        observal admin trace-privacy-set true

        observal admin trace-privacy-set false
    """
    with _command_progress(output, "Updating trace privacy..."):
        result = client.put("/api/v1/admin/trace-privacy", {"trace_privacy": enabled})
    if output == "json":
        output_json(result)
        return
    status = "[green]enabled[/green]" if result.get("trace_privacy") else "[red]disabled[/red]"
    rprint(f"  Trace privacy: {status}")


# ── Cache ───────────────────────────────────────────────


@admin_app.command(name="cache-clear")
def admin_cache_clear(
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Clear all server caches.

    Flushes all in-memory and Redis caches on the server. Useful after
    bulk data changes or when stale data is suspected.

    Examples:

        observal admin cache-clear
    """
    with _command_progress(output, "Clearing caches..."):
        result = client.post("/api/v1/admin/cache/clear")
    if output == "json":
        output_json(result)
        return
    rprint(f"[green]Cleared {result.get('cleared', 0)} cached entries.[/green]")


# ── Role Update ─────────────────────────────────────────


@admin_app.command(name="set-role")
def admin_set_role(
    email: str = typer.Argument(..., help="Email of the user"),
    role: str = typer.Argument(..., help="New role: super_admin, admin, reviewer, or user"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Change a user's role.

    Updates the role for the user identified by email. Valid roles are:
    super_admin, admin, reviewer, user. Requires admin privileges.

    Examples:

        observal admin set-role alice@example.com admin

        observal admin set-role bob@example.com reviewer
    """
    role = _command_choice(role, _ADMIN_ROLES, "user role", "Update user role")
    match = _admin_user(email, output, "Update user role")
    with _command_progress(output, "Updating role..."):
        result = client.put(f"/api/v1/admin/users/{match['id']}/role", {"role": role})
    if output == "json":
        output_json(result)
        return
    rprint(f"[green]{esc(result.get('email', email))} is now {esc(result.get('role', role))}[/green]")


_RECOMMENDABLE = ("agent", "mcp", "skill", "hook", "prompt", "sandbox")
_RECOMMENDABLE_ROUTES = {
    "agent": "agents",
    "mcp": "mcps",
    "skill": "skills",
    "hook": "hooks",
    "prompt": "prompts",
    "sandbox": "sandboxes",
}


@admin_app.command(name="recommend")
def admin_recommend(
    item_type: str = typer.Argument(..., help="agent, mcp, skill, hook, prompt, or sandbox"),
    reference: str = typer.Argument(..., help="namespace/slug, UUID, row number, or @alias"),
    unset: bool = typer.Option(False, "--unset", help="Remove the recommendation instead"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Mark or unmark an agent or component as recommended.

    Recommended items get a badge in the registry and `obs:recommended: true`
    in discovery results. It never changes the relevance score. Requires admin
    privileges.

    Examples:

        observal admin recommend agent acme/security-reviewer

        observal admin recommend mcp acme/github --unset --output json
    """
    operation = "Update recommendation"
    item_type = _command_choice(item_type, _RECOMMENDABLE, "item type", operation)
    with _command_progress(output, "Updating recommendation..."):
        entity_id = client.resolve_registry_reference(item_type, reference)
        try:
            entity_id = str(UUID(entity_id))
        except ValueError:
            # A bare name: let the item's own route resolve it, as `show` does,
            # so ambiguity and missing items get that route's error.
            route = _RECOMMENDABLE_ROUTES[item_type]
            entity_id = str(client.get(f"/api/v1/{route}/{quote(entity_id, safe='')}")["id"])
        result = client.patch(
            "/api/v1/admin/recommended",
            {"entity_type": item_type, "entity_id": entity_id, "recommended": not unset},
        )
    if output == "json":
        output_json(result)
        return
    state = "recommended" if result.get("is_recommended") else "no longer recommended"
    rprint(f"[green]{esc(reference)} is {state}[/green]")


# ── Traces / Spans (on ops_app) ─────────────────────────


@ops_app.command(name="traces")
def _traces(
    platform: str | None = typer.Option(None, "--platform", "-p", help="Filter by harness platform"),
    days: int | None = typer.Option(None, "--days", "-d", min=1, max=365, help="Limit to last N days"),
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=200),
    turn: bool = typer.Option(False, "--turn", help="Unfold sessions to show turns (prompts)"),
    span: bool = typer.Option(False, "--span", help="Show full detail including tool calls"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """List recent traces (sessions).

    By default shows sessions as a summary table (user prompts).
    Use --turn to unfold each session and show its turns/spans.
    Use --span for full detail including tool call inputs/outputs.

    Examples:

        observal ops traces

        observal ops traces --turn

        observal ops traces --platform kiro --days 7
    """
    _traces_impl(platform, days, limit, turn, span, output)


def _traces_impl(platform, days, limit, turn, span, output):
    # Fetch sessions from the REST endpoint (same data the web UI shows)
    params: dict = {"limit": limit}
    if platform:
        platform = _command_choice(platform, tuple(VALID_HARNESSES), "harness platform", "List sessions")
        params["platform"] = platform
    if days:
        params["days"] = days

    with _command_progress(output, "Querying sessions..."):
        sessions = client.get("/api/v1/sessions", params=params)

    if output == "json":
        if turn or span:
            items = []
            for session in sessions:
                session_id = str(session.get("session_id", ""))
                detail = client.get(f"/api/v1/sessions/{quote(session_id, safe='')}")
                items.append({"summary": session, "detail": detail})
            output_json({"view": "span" if span else "turn", "items": items})
        else:
            output_json(sessions)
        return

    if not sessions:
        rprint("[dim]No traces found.[/dim]")
        return

    if span or turn:
        _render_sessions_detail(sessions, full=span)
    else:
        _render_sessions_summary(sessions)


def _render_sessions_summary(sessions: list[dict]):
    """Default view: flat table of sessions (user prompts)."""
    table = Table(title=f"Sessions ({len(sessions)})", show_lines=False, padding=(0, 1))
    table.add_column("#", style="dim", width=3)
    table.add_column("Session", no_wrap=True, max_width=30)
    table.add_column("User")
    table.add_column("Platform")
    table.add_column("Prompts", justify="right")
    table.add_column("Tools", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("When")
    for i, s in enumerate(sessions, 1):
        # Build a session name from prompt count
        prompt_count = int(s.get("prompt_count", 0))
        name = f"{prompt_count} prompt{'s' if prompt_count != 1 else ''}"
        tokens_in = int(s.get("total_input_tokens", 0))
        tokens_out = int(s.get("total_output_tokens", 0))
        tokens_display = _format_tokens(tokens_in, tokens_out)
        table.add_row(
            str(i),
            name,
            esc(s.get("user_name", "--")),
            esc(s.get("platform", "--")),
            str(prompt_count),
            str(int(s.get("tool_result_count", 0))),
            tokens_display,
            relative_time(s.get("first_event_time") or s.get("last_event_time")),
        )
    console.print(table)


def _render_sessions_detail(sessions: list[dict], full: bool = False):
    """--turn / --span view: fetch session detail and show turns."""
    from rich.tree import Tree

    tree = Tree(f"[bold]Sessions ({len(sessions)})[/bold]")
    for s in sessions:
        session_id = s.get("session_id", "")
        prompt_count = int(s.get("prompt_count", 0))
        tool_count = int(s.get("tool_result_count", 0))
        tokens_in = int(s.get("total_input_tokens", 0))
        tokens_out = int(s.get("total_output_tokens", 0))
        name = f"{prompt_count} prompt{'s' if prompt_count != 1 else ''}"
        session_label = (
            f"[bold]{name}[/bold] "
            f"[dim]{esc(session_id[:12])}…[/dim] "
            f"[cyan]{esc(s.get('platform', ''))}[/cyan] "
            f"[dim]{esc(s.get('user_name', ''))}[/dim] "
            f"[dim]{relative_time(s.get('first_event_time'))}[/dim]"
        )
        session_node = tree.add(session_label)

        # Fetch session detail for turns
        detail = client.get(f"/api/v1/sessions/{quote(session_id, safe='')}")

        events = detail.get("events", [])
        if not events:
            session_node.add(f"[dim]prompts: {prompt_count}, tools: {tool_count}[/dim]")
            session_node.add(f"[dim]tokens: {_format_tokens(tokens_in, tokens_out)}[/dim]")
            continue

        for evt in events:
            etype = evt.get("event_name", "")
            body = evt.get("body", "") or ""
            attrs = evt.get("attributes", {})

            if etype in ("user_prompt", "human_turn", "hook_userpromptsubmit"):
                prompt_text = body[:100] + ("…" if len(body) > 100 else "")
                session_node.add(f"[bold green]▶[/bold green] {esc(prompt_text)}")
            elif etype in ("assistant_response", "assistant_turn", "hook_assistant_response"):
                if full:
                    resp_text = body[:150] + ("…" if len(body) > 150 else "")
                    session_node.add(f"  [dim]{esc(resp_text)}[/dim]")
            elif etype in ("tool_call", "hook_pretooluse"):
                tool_name = attrs.get("tool_name") or body[:50]
                session_node.add(f"  [cyan]⚡ {esc(tool_name)}[/cyan]")
            elif etype in ("tool_result", "hook_posttooluse") and full:
                result_text = body[:100] + ("…" if len(body) > 100 else "")
                session_node.add(f"    [dim]→ {esc(result_text)}[/dim]")

        # Show subagent sessions if available
        for sub in detail.get("subagent_sessions", []):
            sub_events = sub.get("events", [])
            sub_node = session_node.add(f"[yellow]↳ subagent ({len(sub_events)} events)[/yellow]")
            if full:
                for evt in sub_events[:10]:
                    etype = evt.get("event_name", "")
                    body = evt.get("body", "") or ""
                    if etype in ("user_prompt", "human_turn"):
                        sub_node.add(f"[green]▶[/green] {esc(body[:80])}")
                    elif etype == "tool_call":
                        tool_name = evt.get("attributes", {}).get("tool_name") or body[:40]
                        sub_node.add(f"  [cyan]⚡ {esc(tool_name)}[/cyan]")

    console.print(tree)


def _format_tokens(input_tokens: int, output_tokens: int) -> str:
    """Format token counts compactly (e.g. '13.8k / 137')."""

    def _fmt(n: int) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        elif n >= 1_000:
            return f"{n / 1_000:.1f}k"
        return str(n)

    return f"{_fmt(input_tokens)} / {_fmt(output_tokens)}"


# ═══════════════════════════════════════════════════════════
# self_app: CLI self-management commands
# ═══════════════════════════════════════════════════════════

self_app = typer.Typer(
    name="self",
    help=(
        "CLI self-management commands (upgrade, downgrade, rollback, status)\n\n"
        "Examples:\n"
        "  observal self status\n"
        "  observal self upgrade\n"
        "  observal self rollback"
    ),
    no_args_is_help=True,
)


def _do_install(install_info, target_version: str, direction: str, output: OutputMode | str = "table") -> None:
    """Execute and verify one CLI version change."""
    from observal_cli.upgrade_executor import execute

    output = _command_value(output)
    progress = spinner if output != "json" else lambda _message=None: nullcontext()
    capture = redirect_stdout(StringIO()) if output == "json" else nullcontext()
    try:
        with capture:
            execute(install_info, target_version, direction, progress, interactive=output != "json")
    except CliError:
        raise
    except typer.Abort:
        raise
    except typer.Exit as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            f"CLI {direction} failed.",
            operation=f"{direction.title()} Observal CLI",
            resource="CLI installation",
            remediation="Review the release, install method, and filesystem permissions, then retry.",
            detail=repr(error),
        )
    except Exception as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            f"CLI {direction} failed.",
            operation=f"{direction.title()} Observal CLI",
            resource="CLI installation",
            remediation="Check network access, the install method, and filesystem permissions, then retry.",
            detail=repr(error),
        )


def _managed_install(install, operation: str) -> None:
    from observal_cli.install_detector import InstallMethod

    if install.method != InstallMethod.SYSTEM_PACKAGE:
        return
    manager = install.managed_by or "the system package manager"
    fail(
        ErrorCategory.CONFLICT,
        f"Observal is managed by {manager}.",
        operation=operation,
        resource="CLI installation",
        remediation=f"Use `{manager} upgrade observal` or the equivalent package-manager command.",
    )


@self_app.command()
def upgrade(
    version: str | None = typer.Option(
        None, "--version", "-v", help="Target version to upgrade to. Defaults to the latest stable release."
    ),
    pre: bool = typer.Option(False, "--pre", help="Include prerelease versions when resolving latest"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip interactive confirmation prompt"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Upgrade the Observal CLI to the latest or specified version.

    Examples:
        observal self upgrade --force
        observal self upgrade --version 2.5.0 --force --output json
        observal self upgrade --pre --force
    """
    from packaging.version import InvalidVersion, Version

    from observal_cli import install_detector, version_check
    from observal_cli.upgrade_lock import UpgradeLockError, acquire_lock, release_lock

    force = _command_value(force)
    output = _command_value(output)
    current = version_check.get_current_version()
    install = install_detector.detect()
    _managed_install(install, "Upgrade Observal CLI")

    if version:
        try:
            target = str(Version(version))
        except InvalidVersion:
            fail(
                ErrorCategory.VALIDATION,
                f"Invalid target version: {version}.",
                operation="Upgrade Observal CLI",
                resource="target version",
                remediation="Use a release version such as 2.5.0.",
            )
    else:
        with _command_progress(output, "Checking for updates..."):
            release = version_check._fetch_from_github(include_pre=pre)
        if not release or not release.get("latest_version"):
            fail(
                ErrorCategory.UNAVAILABLE,
                "Could not fetch the latest CLI release from GitHub.",
                operation="Upgrade Observal CLI",
                resource="GitHub releases",
                remediation="Check network access and retry, or provide --version.",
            )
        try:
            target = str(Version(release["latest_version"]))
        except InvalidVersion:
            fail(
                ErrorCategory.UNAVAILABLE,
                "GitHub returned an invalid CLI release version.",
                operation="Upgrade Observal CLI",
                resource="GitHub releases",
                remediation="Retry later or provide a known release with --version.",
            )

    if target == current:
        result = {
            "action": "upgrade",
            "status": "up_to_date",
            "current_version": current,
            "target_version": target,
            "install_method": install.method.value,
            "path": str(install.path),
        }
        if output == "json":
            output_json(result)
        else:
            rprint(f"[green]Already on v{esc(current)} (latest).[/green]")
        return

    try:
        if Version(target) < Version(current):
            fail(
                ErrorCategory.VALIDATION,
                f"Upgrade target v{target} is older than current v{current}.",
                operation="Upgrade Observal CLI",
                resource="target version",
                remediation=f"Use `observal self downgrade --version {target}` instead.",
            )
    except InvalidVersion:
        pass

    if output == "json" and not force:
        fail(
            ErrorCategory.VALIDATION,
            "JSON mode cannot prompt before upgrading the CLI.",
            operation="Upgrade Observal CLI",
            resource="CLI installation",
            remediation="Add --force to confirm the upgrade.",
        )
    if not force:
        rprint(f"  Current: [dim]v{esc(current)}[/dim]")
        rprint(f"  Target:  [green]v{esc(target)}[/green]")
        rprint(f"  Method:  [dim]{esc(install.method.value)} ({esc(install.path)})[/dim]")
        if not typer.confirm("\nProceed with upgrade?"):
            raise typer.Abort()

    try:
        lock = acquire_lock("cli")
    except UpgradeLockError as error:
        fail(
            ErrorCategory.CONFLICT,
            "Another CLI version change is already running.",
            operation="Upgrade Observal CLI",
            resource="CLI upgrade lock",
            remediation="Wait for it to finish, then retry.",
            detail=repr(error),
        )
    try:
        _do_install(install, target, direction="upgrade", output=output)
    finally:
        release_lock(lock)

    if output == "json":
        output_json(
            {
                "action": "upgrade",
                "status": "completed",
                "from_version": current,
                "to_version": target,
                "install_method": install.method.value,
                "path": str(install.path),
            }
        )


@self_app.command()
def downgrade(
    version: str | None = typer.Option(None, "--version", "-v", help="Target version to downgrade to"),
    list_versions: bool = typer.Option(False, "--list", "-l", help="List available releases"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Downgrade the Observal CLI to a previous version.

    Examples:
        observal self downgrade --list --output json
        observal self downgrade --version 2.4.0 --force --output json
    """
    from packaging.version import InvalidVersion, Version

    from observal_cli import install_detector, version_check
    from observal_cli.upgrade_lock import UpgradeLockError, acquire_lock, release_lock

    force = _command_value(force)
    output = _command_value(output)
    current = version_check.get_current_version()

    if list_versions and version:
        fail(
            ErrorCategory.VALIDATION,
            "Choose either --list or --version, not both.",
            operation="Downgrade Observal CLI",
            resource="downgrade mode",
            remediation="Remove one of the conflicting options.",
        )
    if list_versions:
        with _command_progress(output, "Fetching releases..."):
            releases = version_check.fetch_all_releases()
        if not releases:
            fail(
                ErrorCategory.UNAVAILABLE,
                "Could not fetch CLI releases from GitHub.",
                operation="List Observal CLI releases",
                resource="GitHub releases",
                remediation="Check network access and retry.",
            )
        items = [
            {
                **release,
                "current": str(release.get("version", "")) == current,
            }
            for release in releases
        ]
        if output == "json":
            output_json({"current_version": current, "items": items})
            return
        table = Table(title="Available Versions")
        table.add_column("Version", style="bold")
        table.add_column("Published")
        table.add_column("Status")
        for release in items:
            table.add_row(
                esc(release.get("version", "")),
                esc(str(release.get("published_at") or "")[:10]),
                "← current" if release["current"] else "",
            )
        console.print(table)
        return

    if not version:
        fail(
            ErrorCategory.VALIDATION,
            "A target version is required for downgrade.",
            operation="Downgrade Observal CLI",
            resource="target version",
            remediation="Provide --version or use --list.",
        )
    try:
        target = Version(version)
    except InvalidVersion:
        fail(
            ErrorCategory.VALIDATION,
            f"Invalid target version: {version}.",
            operation="Downgrade Observal CLI",
            resource="target version",
            remediation="Use a release version such as 2.4.0.",
        )

    if target < Version(version_check.VERSION_FLOOR):
        fail(
            ErrorCategory.VALIDATION,
            f"Cannot downgrade below v{version_check.VERSION_FLOOR}.",
            operation="Downgrade Observal CLI",
            resource="target version",
            remediation=f"Choose v{version_check.VERSION_FLOOR} or newer.",
        )
    try:
        if target >= Version(current):
            fail(
                ErrorCategory.VALIDATION,
                f"Downgrade target v{target} is not older than current v{current}.",
                operation="Downgrade Observal CLI",
                resource="target version",
                remediation="Choose an older release or use `observal self upgrade`.",
            )
    except InvalidVersion:
        pass

    install = install_detector.detect()
    _managed_install(install, "Downgrade Observal CLI")
    if output == "json" and not force:
        fail(
            ErrorCategory.VALIDATION,
            "JSON mode cannot prompt before downgrading the CLI.",
            operation="Downgrade Observal CLI",
            resource="CLI installation",
            remediation="Add --force to confirm the downgrade.",
        )
    if not force:
        rprint(f"  Current: [dim]v{esc(current)}[/dim]")
        rprint(f"  Target:  [yellow]v{esc(target)}[/yellow]")
        if not typer.confirm("\nProceed with downgrade?"):
            raise typer.Abort()

    try:
        lock = acquire_lock("cli")
    except UpgradeLockError as error:
        fail(
            ErrorCategory.CONFLICT,
            "Another CLI version change is already running.",
            operation="Downgrade Observal CLI",
            resource="CLI upgrade lock",
            remediation="Wait for it to finish, then retry.",
            detail=repr(error),
        )

    pin_legacy_auto_update = target < Version("1.10.4")
    previous_auto_update = None
    try:
        if pin_legacy_auto_update:
            previous_auto_update = config.load().get("auto_update", True)
            config.save({"auto_update": False})
        _do_install(install, str(target), direction="downgrade", output=output)
    except BaseException as install_error:
        if pin_legacy_auto_update:
            try:
                config.save({"auto_update": previous_auto_update})
            except (Exception, SystemExit) as restore_error:
                fail(
                    ErrorCategory.UNAVAILABLE,
                    "CLI downgrade failed and the automatic-update setting could not be restored.",
                    operation="Downgrade Observal CLI",
                    resource="automatic-update setting",
                    remediation="Restore auto_update to its previous value, then inspect the failed installation.",
                    detail=f"install={install_error!r}; restore={restore_error!r}",
                )
        raise
    finally:
        release_lock(lock)

    if output == "json":
        output_json(
            {
                "action": "downgrade",
                "status": "completed",
                "from_version": current,
                "to_version": str(target),
                "install_method": install.method.value,
                "path": str(install.path),
                "automatic_updates_disabled": pin_legacy_auto_update,
            }
        )
    elif pin_legacy_auto_update:
        rprint("[dim]Automatic updates disabled to keep this legacy version pinned.[/dim]")


@self_app.command()
def rollback(
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Restore the CLI binary saved before the last version change.

    Examples:
        observal self rollback
        observal self rollback --force --output json
    """
    import os
    import shutil

    from observal_cli import install_detector
    from observal_cli.install_detector import InstallMethod
    from observal_cli.upgrade_lock import UpgradeLockError, acquire_lock, release_lock

    force = _command_value(force)
    output = _command_value(output)
    install = install_detector.detect()
    backup = config.CONFIG_DIR / "bin" / "observal.prev"
    if not backup.is_file():
        fail(
            ErrorCategory.NOT_FOUND,
            "No CLI rollback backup was found.",
            operation="Rollback Observal CLI",
            resource=str(backup),
            remediation="Run a successful binary upgrade or downgrade before rollback.",
        )
    if install.method != InstallMethod.BINARY:
        fail(
            ErrorCategory.CONFLICT,
            "Rollback is only supported for standalone binary installations.",
            operation="Rollback Observal CLI",
            resource="CLI installation",
            remediation="Install the previous version with the current package manager.",
        )
    if output == "json" and not force:
        fail(
            ErrorCategory.VALIDATION,
            "JSON mode cannot prompt before rolling back the CLI.",
            operation="Rollback Observal CLI",
            resource="CLI installation",
            remediation="Add --force to confirm rollback.",
        )

    target = Path(install.path)
    if not force:
        rprint(f"  Restore: {esc(backup)} → {esc(target)}")
        if not typer.confirm("Proceed?"):
            raise typer.Abort()

    try:
        lock = acquire_lock("cli")
    except UpgradeLockError as error:
        fail(
            ErrorCategory.CONFLICT,
            "Another CLI version change is already running.",
            operation="Rollback Observal CLI",
            resource="CLI upgrade lock",
            remediation="Wait for it to finish, then retry.",
            detail=repr(error),
        )

    temporary: Path | None = None
    try:
        with NamedTemporaryFile(dir=target.parent, prefix=".observal-rollback-", delete=False) as file:
            temporary = Path(file.name)
        shutil.copy2(backup, temporary)
        os.chmod(temporary, 0o755)
        temporary.replace(target)
    except OSError as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            "Could not restore the previous CLI binary.",
            operation="Rollback Observal CLI",
            resource=str(target),
            remediation="Check filesystem permissions and retry.",
            detail=repr(error),
        )
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        release_lock(lock)

    result = {"action": "rollback", "status": "completed", "backup": str(backup), "path": str(target)}
    if output == "json":
        output_json(result)
    else:
        rprint("[green]✓ Rolled back to previous version.[/green]")


@self_app.command()
def status(
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Show the CLI version, install method, and update availability.

    Examples:
        observal self status
        observal self status --output json
    """
    from observal_cli import install_detector, version_check

    output = _command_value(output)
    current = version_check.get_current_version()
    install = install_detector.detect()
    with _command_progress(output, "Checking for updates..."):
        release = version_check._fetch_from_github()

    latest = release.get("latest_version") if release else None
    update_available = version_check._is_newer(latest, current) if latest else None
    result = {
        "current_version": current,
        "install_method": install.method.value,
        "path": str(install.path),
        "writable": install.writable,
        "managed_by": install.managed_by,
        "github_available": release is not None,
        "latest_version": latest,
        "update_available": update_available,
    }
    if output == "json":
        output_json(result)
        return

    rprint(f"  Version:  [bold]v{esc(current)}[/bold]")
    rprint(f"  Install:  [dim]{esc(install.method.value)} ({esc(install.path)})[/dim]")
    if latest:
        suffix = "update available" if update_available else "up to date"
        rprint(f"  Latest:   [green]v{esc(latest)}[/green] ({suffix})")
        if update_available:
            rprint("\n  Run: [bold]observal self upgrade[/bold]")
    else:
        rprint("  Latest:   [dim]could not reach GitHub[/dim]")


# ═══════════════════════════════════════════════════════════
# Wire sub-Typers into ops_app and admin_app
# ═══════════════════════════════════════════════════════════

# telemetry is a subgroup of ops
ops_app.add_typer(telemetry_app, name="telemetry")
