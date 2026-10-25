# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Non-interactive component fork commands and safe provenance presentation."""

import typer
from rich import print as rprint

from observal_cli import client
from observal_cli.render import OutputMode, console, esc, kv_panel, output_json

# `registry skill fork` already creates a same-listing successor version, so the
# independent-copy command for skills needs its own name.
FORK_COMMANDS = {"skill": "fork-listing"}
_PLURALS = {"mcp": "mcps", "skill": "skills", "hook": "hooks", "prompt": "prompts", "sandbox": "sandboxes"}


def fork_detail_rows(item: dict) -> list[tuple[str, str]]:
    """Show only live, permission-checked provenance; never use the saved source ref."""
    rows = []
    source = item.get("forked_from")
    if source:
        name = (
            f"{source.get('qualified_name')}@{source.get('version') or '?'}"
            if source.get("available") and source.get("qualified_name")
            else "Source unavailable"
        )
        rows.append(("Forked from", esc(name)))
    if "fork_count" in item:
        rows.append(("Forks", esc(str(item["fork_count"]))))
    return rows


def add_fork_command(app: typer.Typer, entity_type: str) -> None:
    """Register a type-specific fork command on one component Typer group."""
    plural = _PLURALS[entity_type]
    command = FORK_COMMANDS.get(entity_type, "fork")
    path = f"observal registry {entity_type}"

    def _fork_component(
        source: str = typer.Argument(..., help="UUID, namespace/slug, list row, or @alias"),
        name: str | None = typer.Option(None, "--name", help="Name of the new draft (defaults to source name)"),
        version: str | None = typer.Option(None, "--version", help="Approved source version to copy"),
        new_version: str | None = typer.Option(None, "--new-version", help="Version for the new draft"),
        team: str | None = typer.Option(None, "--team", help="Target teamspace handle or UUID"),
        visibility: str | None = typer.Option(None, "--visibility", help="public or team"),
        output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
    ):
        payload = {
            key: value
            for key, value in {
                "name": name,
                "version": version,
                "new_version": new_version,
            }.items()
            if value is not None
        }
        if team is not None or visibility is not None:
            client.add_publish_target(payload, team, visibility)
        resolved = client.resolve_registry_reference(entity_type, source)
        result = client.post(f"/api/v1/{plural}/{resolved}/fork", json_data=payload)
        if output == "json":
            output_json(result)
            return
        console.print(
            kv_panel(
                f"Forked {entity_type}: {esc(result.get('qualified_name') or result.get('name', ''))}",
                [("Status", esc(result.get("status", "draft"))), *fork_detail_rows(result), ("ID", esc(result["id"]))],
                border_style="cyan",
            )
        )
        rprint(f"[dim]Next:[/dim] {path} edit {esc(result.get('qualified_name') or result['id'])}")
        for warning in result.get("warnings") or []:
            rprint(f"[yellow]⚠ {esc(warning)}[/yellow]")

    _fork_component.__doc__ = f"""Fork an approved {entity_type} release into your own editable draft.

    Examples:
      {path} {command} acme/my-{entity_type}
      {path} {command} acme/my-{entity_type} --name my-copy --team payments --visibility team
      {path} {command} acme/my-{entity_type} --output json
    """
    app.command(name=command)(_fork_component)
