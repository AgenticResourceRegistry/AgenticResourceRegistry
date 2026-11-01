# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""observal registry a2a: register remote A2A agents so agents can discover and delegate to them."""

from __future__ import annotations

import typer
from rich import print as rprint
from rich.table import Table

from observal_cli import client
from observal_cli.errors import ErrorCategory, fail
from observal_cli.render import OutputMode, console, esc, output_json

a2a_app = typer.Typer(
    name="a2a",
    help=(
        "Remote A2A agents (register an Agent Card, review, remove)\n\n"
        "Examples:\n"
        "  observal registry a2a submit https://agents.acme.com/triage --visibility team --team platform\n"
        "  observal registry a2a list --output json\n"
        "  observal registry a2a review urn:air:agents.acme.com:a2a:triage --approve --digest sha256:9f2c..."
    ),
    no_args_is_help=True,
)

_BASE = "/api/v1/ard/imports"


def _print_entry(entry: dict) -> None:
    iface = entry.get("obs:a2aInterface") or {}
    rprint(
        f"[bold]{esc(entry.get('displayName'))}[/bold] {esc(entry.get('version'))}  [dim]{esc(entry.get('identifier'))}[/dim]"
    )
    if entry.get("description"):
        rprint(f"  {esc(entry['description'])}")
    rprint(f"  [dim]Status[/dim]     {esc(entry.get('obs:lifecycle'))}")
    rprint(f"  [dim]Visibility[/dim] {esc(entry.get('obs:visibility'))}")
    rprint(f"  [dim]Endpoint[/dim]   {esc(iface.get('url') or '-')} {esc(iface.get('protocolBinding') or '')}")
    rprint(f"  [dim]Digest[/dim]     {esc(entry.get('obs:artifactDigest') or '-')}")
    skills = [c.removeprefix("a2a-skill:") for c in entry.get("capabilities") or [] if c.startswith("a2a-skill:")]
    if skills:
        rprint(f"  [dim]Skills[/dim]     {esc(', '.join(skills))}")


@a2a_app.command("submit")
def a2a_submit(
    card_url: str = typer.Argument(..., help="Agent Card URL, or the agent's origin (/.well-known/agent-card.json)"),
    visibility: str = typer.Option("private", "--visibility", help="private, team, or public"),
    team: str | None = typer.Option(None, "--team", help="Team handle or UUID (for --visibility team)"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Register (or refresh) a remote A2A agent for review.

    The server fetches the card, and a reviewer approves it before any agent
    can delegate to it. Resubmitting a card whose content changed sends it
    back to review.

    Examples:
      observal registry a2a submit https://agents.acme.com/triage --visibility team --team platform --output json
    """
    if visibility not in ("private", "team", "public"):
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown visibility: {visibility}.",
            operation="Register A2A agent",
            resource="visibility",
            remediation="Choose private, team, or public.",
        )
    body: dict = {"cardUrl": card_url, "visibility": visibility}
    if visibility == "team":
        if not team:
            fail(
                ErrorCategory.VALIDATION,
                "Team visibility needs --team.",
                operation="Register A2A agent",
                resource="team",
                remediation="Pass --team with a team handle or UUID.",
            )
        body["teamId"] = client.resolve_team_id(team)
    entry = client.post(f"{_BASE}/a2a", body, operation="Register A2A agent", resource=card_url)
    if output == "json":
        output_json(entry)
        return
    rprint("[green]Registered.[/green] A reviewer must approve it before agents can delegate to it.\n")
    _print_entry(entry)


@a2a_app.command("list")
def a2a_list(output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json")):
    """List remote A2A agents you can see, including pending ones you own or review.

    Examples:
      observal registry a2a list --output json
    """
    data = client.get(_BASE, operation="List A2A agents", resource="A2A agents")
    if output == "json":
        output_json(data)
        return
    items = data.get("items") or []
    if not items:
        rprint("[dim]No remote A2A agents registered.[/dim]")
        return
    table = Table(title="Remote A2A agents", padding=(0, 1))
    table.add_column("Agent", style="bold")
    table.add_column("Version")
    table.add_column("Status")
    table.add_column("Visibility")
    table.add_column("Identifier", style="dim", overflow="fold")
    for item in items:
        table.add_row(
            esc(item.get("displayName")),
            esc(item.get("version")),
            esc(item.get("obs:lifecycle")),
            esc(item.get("obs:visibility")),
            esc(item.get("identifier")),
        )
    console.print(table)


@a2a_app.command("review")
def a2a_review(
    identifier: str = typer.Argument(..., help="Identifier (urn:air:...) of the registered agent"),
    approve: bool = typer.Option(False, "--approve", help="Approve the agent"),
    reject: bool = typer.Option(False, "--reject", help="Reject the agent"),
    reason: str | None = typer.Option(None, "--reason", help="Reason (required to reject)"),
    digest: str | None = typer.Option(
        None, "--digest", help="Digest of the card you reviewed (from discover inspect); required to approve"
    ),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Approve or reject a registered agent (reviewers and admins).

    Inspect the card first (observal discover inspect <identifier>) and pass
    its digest to approve, so an owner refreshing the card in between cannot
    get a card you never saw approved.

    Examples:
      observal registry a2a review urn:air:agents.acme.com:a2a:triage --approve --digest sha256:9f2c...
      observal registry a2a review urn:air:agents.acme.com:a2a:triage --reject --reason "No auth on endpoint"
    """
    if approve == reject:
        fail(
            ErrorCategory.VALIDATION,
            "Pass exactly one of --approve or --reject.",
            operation="Review A2A agent",
            resource=identifier,
        )
    if approve and not digest:
        fail(
            ErrorCategory.VALIDATION,
            "Approving needs --digest with the digest of the card you reviewed.",
            operation="Review A2A agent",
            resource=identifier,
            remediation=f"Run observal discover inspect {identifier} and pass its Digest.",
        )
    body = {"action": "approve" if approve else "reject", "reason": reason, "digest": digest}
    entry = client.post(f"{_BASE}/{identifier}/review", body, operation="Review A2A agent", resource=identifier)
    if output == "json":
        output_json(entry)
        return
    _print_entry(entry)


@a2a_app.command("remove")
def a2a_remove(
    identifier: str = typer.Argument(..., help="Identifier (urn:air:...) of the registered agent"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm removal"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Remove a registered agent from discovery (owner or admin).

    Examples:
      observal registry a2a remove urn:air:agents.acme.com:a2a:triage --yes
    """
    if not yes:
        fail(
            ErrorCategory.VALIDATION,
            "Removing an agent needs --yes.",
            operation="Remove A2A agent",
            resource=identifier,
            remediation="Re-run with --yes to confirm.",
        )
    result = client.delete(f"{_BASE}/{identifier}", operation="Remove A2A agent", resource=identifier)
    if output == "json":
        output_json(result)
        return
    rprint(f"[green]Removed[/green] {esc(identifier)}")
