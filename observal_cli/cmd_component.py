# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Registry component version commands."""

from __future__ import annotations

import json as _json
from contextlib import nullcontext

import typer
from loguru import logger as optic
from packaging.version import InvalidVersion, Version
from rich import print as rprint
from rich.table import Table

from observal_cli import client
from observal_cli.constants import VALID_HARNESSES
from observal_cli.errors import ErrorCategory, fail
from observal_cli.prompts import text_input
from observal_cli.render import OutputMode, console, esc, output_json, relative_time, spinner, status_badge
from observal_cli.review_display import print_review_link

# ── Constants ──────────────────────────────────────────────────

_VALID_TYPES = {"mcp", "skill", "hook", "prompt", "sandbox"}

_PLURAL = {
    "mcp": "mcps",
    "skill": "skills",
    "hook": "hooks",
    "prompt": "prompts",
    "sandbox": "sandboxes",
}

# ── App hierarchy ──────────────────────────────────────────────

version_app = typer.Typer(
    help=(
        "Manage component versions\n\n"
        "Examples:\n"
        "  observal registry version list mcp alice/my-server\n"
        "  observal registry version publish mcp alice/my-server --version 2.0.0 --description 'Breaking change'"
    ),
    no_args_is_help=True,
)

# ── Helpers ────────────────────────────────────────────────────


def _require_valid_type(component_type: str) -> None:
    if component_type not in _VALID_TYPES:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown component type: {component_type}.",
            operation="Manage component version",
            resource="component type",
            remediation=f"Choose one of: {', '.join(sorted(_VALID_TYPES))}.",
        )


# ── version publish ────────────────────────────────────────────


@version_app.command(name="publish")
def version_publish(
    component_type: str = typer.Argument(..., help="Component type: hook, skill, prompt, mcp, sandbox"),
    listing: str = typer.Argument(..., help="Listing name or ID"),
    version: str | None = typer.Option(None, "--version", "-v", help="Version to publish (e.g. 1.2.0)"),
    description: str = typer.Option(..., "--description", "-d", help="Short description of this version"),
    changelog: str | None = typer.Option(None, "--changelog", help="Changelog notes"),
    message: str | None = typer.Option(None, "--message", help="Review revision note"),
    supported_harnesses: list[str] | None = typer.Option(
        None, "--harness", help="Supported harnesses (repeat for multiple)"
    ),
    extra: str | None = typer.Option(None, "--extra", help="Extra JSON for type-specific fields"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Publish a new version for a registry component.

    Creates a versioned release for any component type (mcp, skill, hook,
    prompt, sandbox). If --version is omitted, fetches version suggestions
    from the server and prompts interactively.

    The --extra flag accepts a JSON string for type-specific metadata
    (e.g. supported transports for MCP servers).

    \b
    Examples:
      observal registry version publish mcp alice/my-server -v 2.0.0 -d "Breaking change"
      observal registry version publish hook alice/guard-hook -v 1.1.0 -d "Add timeout" --changelog "Fixed race"
      observal registry version publish skill alice/my-skill -v 1.0.0 -d "Initial" --harness claude-code --output json
    """
    optic.trace("component_type={}", component_type)
    _require_valid_type(component_type)

    # Validate --extra JSON early
    extra_data: dict | None = None
    if extra is not None:
        try:
            extra_data = _json.loads(extra)
        except _json.JSONDecodeError as error:
            fail(
                ErrorCategory.VALIDATION,
                "The extra version metadata is not valid JSON.",
                operation="Publish component version",
                resource="extra metadata",
                remediation="Correct the JSON and retry.",
                detail=repr(error),
            )
        if not isinstance(extra_data, dict):
            fail(
                ErrorCategory.VALIDATION,
                "The extra version metadata must be a JSON object.",
                operation="Publish component version",
                resource="extra metadata",
                remediation="Provide a JSON object and retry.",
            )

    bad_harnesses = [harness for harness in supported_harnesses or [] if harness not in VALID_HARNESSES]
    if bad_harnesses:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown harness: {bad_harnesses[0]}.",
            operation="Publish component version",
            resource="supported harnesses",
            remediation=f"Choose from: {', '.join(VALID_HARNESSES)}.",
        )
    resolved = client.resolve_registry_reference(component_type, listing)
    plural = _PLURAL[component_type]

    if output == "json" and version is None:
        fail(
            ErrorCategory.VALIDATION,
            "JSON mode requires an explicit component version.",
            operation="Publish component version",
            resource=listing,
            remediation="Provide a version and retry.",
        )
    if version is None:
        with spinner("Fetching version suggestions..."):
            suggestions = client.get(f"/api/v1/{plural}/{resolved}/version-suggestions")
        current = suggestions.get("current", "?")
        suggested = suggestions.get("suggestions", {})
        rprint(
            f"[dim]Current: {esc(current)}  patch→{esc(suggested.get('patch', '?'))}  "
            f"minor→{esc(suggested.get('minor', '?'))}  major→{esc(suggested.get('major', '?'))}[/dim]"
        )
        version = text_input("Version")
    try:
        Version(version)
    except InvalidVersion as error:
        fail(
            ErrorCategory.VALIDATION,
            "The component version is invalid.",
            operation="Publish component version",
            resource=version,
            remediation="Provide a valid version and retry.",
            detail=repr(error),
        )

    # Build payload (only include optional keys when provided)
    payload: dict = {
        "version": version,
        "description": description,
    }
    if changelog is not None:
        payload["changelog"] = changelog
    if message is not None:
        payload["message"] = message
    if supported_harnesses:
        payload["supported_harnesses"] = supported_harnesses
    if extra_data is not None:
        payload["extra"] = extra_data

    publish_context = (
        nullcontext() if output == "json" else spinner(f"Publishing {component_type} version {version}...")
    )
    with publish_context:
        result = client.post(f"/api/v1/{plural}/{resolved}/versions", payload)

    if output == "json":
        output_json(result)
        return
    status = result.get("status", "pending")
    rprint(
        f"[green]✓ Version [bold]{esc(result.get('version', version))}[/bold] submitted for review![/green]"
        f"  Status: {status_badge(status)}"
    )
    print_review_link(result)


# ── version list ───────────────────────────────────────────────


@version_app.command(name="list")
def version_list(
    component_type: str = typer.Argument(..., help="Component type: hook, skill, prompt, mcp, sandbox"),
    listing: str = typer.Argument(..., help="Listing name or ID"),
    page: int = typer.Option(1, "--page", min=1, help="Page number"),
    page_size: int = typer.Option(50, "--page-size", min=1, max=200, help="Items per page"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """List version history for a registry component.

    Shows all published versions for a component, including status,
    release date, and who published each version. Supports table and
    JSON output formats.

    \b
    Examples:
      observal registry version list mcp alice/my-server
      observal registry version list hook alice/guard-hook --output json
      observal registry version list skill @my-skill-alias
    """
    _require_valid_type(component_type)

    resolved = client.resolve_registry_reference(component_type, listing)
    plural = _PLURAL[component_type]

    fetch_context = nullcontext() if output == "json" else spinner("Fetching versions...")
    with fetch_context:
        data = client.get(
            f"/api/v1/{plural}/{resolved}/versions",
            params={"page": page, "page_size": page_size},
        )

    items = data.get("items", [])

    if output == "json":
        output_json(data)
        return

    if not items:
        rprint("[dim]No versions found.[/dim]")
        return

    table = Table(show_lines=False, padding=(0, 1))
    table.add_column("VERSION", style="bold cyan", no_wrap=True)
    table.add_column("STATUS")
    table.add_column("DATE")
    table.add_column("RELEASED BY", style="dim")

    for item in items:
        table.add_row(
            esc(item.get("version", "")),
            status_badge(item.get("status", "")),
            esc(relative_time(item.get("created_at"))),
            esc(item.get("created_by_email", "") or item.get("created_by_username", "")),
        )

    console.print(table)
