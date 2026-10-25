# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Webhook auto-sync commands for MCP servers hosted on GitHub or GitLab."""

from __future__ import annotations

from enum import Enum

import typer
from loguru import logger as optic
from rich import print as rprint

from observal_cli import client
from observal_cli.render import OutputMode, console, esc, kv_panel, output_json, relative_time

mcp_sync_app = typer.Typer(
    help=(
        "Publish a new MCP version automatically when its GitHub or GitLab repository changes\n\n"
        "Examples:\n"
        "  observal registry mcp sync enable alice/my-server\n"
        "  observal registry mcp sync enable alice/my-server --release --no-push\n"
        "  observal registry mcp sync enable alice/my-server --provider gitlab\n"
        "  observal registry mcp sync status alice/my-server"
    )
)


class Provider(str, Enum):
    """Where the MCP's repository is hosted."""

    github = "github"
    gitlab = "gitlab"


_LABELS = {"github": "GitHub", "gitlab": "GitLab"}


def _label(state: dict) -> str:
    return _LABELS.get(state.get("provider") or "github", "GitHub")


def _path(mcp_id: str) -> str:
    return f"/api/v1/mcps/{client.resolve_registry_reference('mcp', mcp_id)}/webhook-sync"


def _triggers(state: dict) -> str:
    on = [name for name, key in (("push", "sync_on_push"), ("release", "sync_on_release")) if state.get(key)]
    return ", ".join(on) or "none"


def _render(state: dict, title: str) -> None:
    if not state.get("enabled"):
        rprint("[dim]Webhook sync is not enabled for this MCP server.[/dim]")
        return
    status = state.get("last_sync_status") or "never synced"
    fields = [
        ("Provider", _label(state)),
        ("Webhook URL", esc(state.get("webhook_url") or "")),
        ("Triggers", _triggers(state)),
        ("Branch", esc(state.get("branch") or "repository default")),
        (
            "Last delivery",
            esc(relative_time(state.get("last_delivery_at")) if state.get("last_delivery_at") else "none"),
        ),
        ("Last sync", esc(status)),
    ]
    if state.get("last_version"):
        fields.append(("Last version", esc(state["last_version"])))
    if state.get("last_sync_error"):
        fields.append(("Detail", esc(state["last_sync_error"])))
    console.print(kv_panel(title, fields, border_style="cyan"))


def _print_setup(state: dict) -> None:
    secret = state.get("secret")
    if not secret:
        return
    rprint("\n[bold]Webhook secret[/bold] (shown once, store it now):")
    rprint(f"  [yellow]{esc(secret)}[/yellow]")
    url = esc(state.get("webhook_url") or "")
    if state.get("provider") == "gitlab":
        rprint("\n[bold]Add the webhook in GitLab[/bold]: project Settings > Webhooks > Add new webhook")
        rprint(f"  URL:           {url}")
        rprint("  Secret token:  the secret above")
        rprint(f"  Trigger:       {_gitlab_events_hint(state)}")
        return
    rprint("\n[bold]Add the webhook in GitHub[/bold]: repository Settings > Webhooks > Add webhook")
    rprint(f"  Payload URL:   {url}")
    rprint("  Content type:  application/json")
    rprint("  Secret:        the secret above")
    rprint(f"  Events:        {_events_hint(state)}")


def _events_hint(state: dict) -> str:
    if state.get("sync_on_push") and state.get("sync_on_release"):
        return "Let me select individual events > Pushes and Releases"
    if state.get("sync_on_release"):
        return "Let me select individual events > Releases"
    return "Just the push event"


def _gitlab_events_hint(state: dict) -> str:
    events = (("Push events", "sync_on_push"), ("Releases events", "sync_on_release"))
    return " and ".join(name for name, key in events if state.get(key))


@mcp_sync_app.command(name="enable")
def mcp_sync_enable(
    mcp_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    push: bool | None = typer.Option(None, "--push/--no-push", help="Publish a version on each push to the branch"),
    release: bool | None = typer.Option(
        None, "--release/--no-release", help="Publish a version when a release is published"
    ),
    branch: str | None = typer.Option(None, "--branch", "-b", help="Branch to track (default: repository default)"),
    provider: Provider | None = typer.Option(
        None,
        "--provider",
        help="Where the repository is hosted. Detected from the repository URL; set it for self-hosted GitLab.",
        case_sensitive=False,
    ),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Turn on GitHub or GitLab webhook sync, or change which events trigger it.

    Synced versions are approved and published right away. Push sync bumps
    the patch version, or uses the version in pyproject.toml or package.json
    when the repository raised it. Release sync uses the release tag
    (v1.2.3 or 1.2.3) as the version.

    On first enable this prints the webhook URL and a secret to add in the
    repository's webhook settings. The secret is shown only once; use
    rotate-secret to issue a new one.

    Examples:
        observal registry mcp sync enable alice/my-server
        observal registry mcp sync enable alice/my-server --release
        observal registry mcp sync enable alice/my-server --release --no-push --branch stable
        observal registry mcp sync enable alice/my-server --provider gitlab
    """
    optic.trace("mcp_id={}, push={}, release={}, provider={}", mcp_id, push, release, provider)
    path = _path(mcp_id)
    current = client.get(path)
    enabled = current.get("enabled")
    body = {
        "sync_on_push": push if push is not None else (current.get("sync_on_push") if enabled else True),
        "sync_on_release": release if release is not None else (current.get("sync_on_release") if enabled else False),
        "branch": branch if branch is not None else (current.get("branch") if enabled else None),
        "provider": provider.value if provider else None,
    }
    state = client.put(path, body)
    if output == "json":
        output_json(state)
        return
    _render(state, "Webhook sync enabled" if not enabled else "Webhook sync updated")
    _print_setup(state)


@mcp_sync_app.command(name="status")
def mcp_sync_status(
    mcp_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Show webhook sync settings and the result of the last sync.

    Examples:
        observal registry mcp sync status alice/my-server
    """
    optic.trace("mcp_id={}", mcp_id)
    state = client.get(_path(mcp_id))
    if output == "json":
        output_json(state)
        return
    _render(state, "Webhook sync")


@mcp_sync_app.command(name="run")
def mcp_sync_run(
    mcp_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Sync the tracked branch now, without waiting for a push.

    Examples:
        observal registry mcp sync run alice/my-server
    """
    optic.trace("mcp_id={}", mcp_id)
    state = client.post(f"{_path(mcp_id)}/run")
    if output == "json":
        output_json(state)
        return
    rprint("[green]Sync queued.[/green] Check the result with: observal registry mcp sync status " + esc(mcp_id))


@mcp_sync_app.command(name="rotate-secret")
def mcp_sync_rotate_secret(
    mcp_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Issue a new webhook secret. Update it in GitHub or GitLab, or deliveries will be rejected.

    Examples:
        observal registry mcp sync rotate-secret alice/my-server
    """
    optic.trace("mcp_id={}", mcp_id)
    state = client.post(f"{_path(mcp_id)}/rotate-secret")
    if output == "json":
        output_json(state)
        return
    _print_setup(state)


@mcp_sync_app.command(name="disable")
def mcp_sync_disable(
    mcp_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Turn off webhook sync. Published versions are kept.

    Examples:
        observal registry mcp sync disable alice/my-server --yes
    """
    optic.trace("mcp_id={}", mcp_id)
    path = _path(mcp_id)
    if not yes and output != "json":
        typer.confirm("Turn off webhook sync? Webhook deliveries will be rejected afterwards.", abort=True)
    result = client.delete(path)
    if output == "json":
        output_json(result)
        return
    rprint("[green]Webhook sync disabled.[/green] Remove the webhook from the repository's settings too.")
