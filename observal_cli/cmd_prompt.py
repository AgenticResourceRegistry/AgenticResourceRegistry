# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Prompt registry CLI commands."""

from __future__ import annotations

import json as _json
from contextlib import nullcontext
from pathlib import Path

import typer
from packaging.version import InvalidVersion, Version
from rich import print as rprint
from rich.table import Table

from observal_cli import client, config
from observal_cli.cmd_fork import fork_detail_rows
from observal_cli.constants import VALID_PROMPT_CATEGORIES
from observal_cli.errors import ErrorCategory, fail, load_json_object
from observal_cli.prompts import select_one, text_input
from observal_cli.render import (
    OutputMode,
    console,
    display_name,
    esc,
    handle,
    kv_panel,
    listing_status,
    output_json,
    relative_time,
    spinner,
    status_badge,
)

prompt_app = typer.Typer(
    help=(
        "Prompt registry commands\n\n"
        "Examples:\n"
        "  observal registry prompt list\n"
        "  observal registry prompt show alice/my-prompt\n"
        "  observal registry prompt render alice/my-prompt --var lang=python"
    )
)


def register_prompt(app: typer.Typer):
    app.add_typer(prompt_app, name="prompt")


@prompt_app.command(name="submit")
def prompt_submit(
    from_file: str | None = typer.Option(
        None, "--from-file", "-f", help="Create from JSON file or read template from file"
    ),
    name: str | None = typer.Option(None, "--name", "-n", help="Prompt name"),
    version: str | None = typer.Option(None, "--version", "-v", help="Version (default: 1.0.0)"),
    description: str | None = typer.Option(None, "--description", "-d", help="Short description"),
    category: str | None = typer.Option(None, "--category", "-c", help="Prompt category"),
    template: str | None = typer.Option(None, "--template", "-t", help="Template body"),
    draft: bool = typer.Option(False, "--draft", help="Save as draft instead of submitting for review"),
    submit_draft: str | None = typer.Option(None, "--submit", help="Submit a draft for review (prompt ID)"),
    team: str | None = typer.Option(None, "--team", help="Teamspace UUID or handle"),
    visibility: str | None = typer.Option(None, "--visibility", help="Visibility: public or team"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Submit a new prompt template for review.

    Prompts are reusable templates with variable placeholders that agents can
    render at runtime. You can submit interactively, from a JSON file, or save
    as a draft first and submit later with --submit.

    Only submit prompts you created or are the point-of-contact for.

    Examples:
        observal registry prompt submit --from-file prompt.json
        observal registry prompt submit --draft
        observal registry prompt submit --submit abc123 --output json
    """
    human_output = output != "json"
    if human_output:
        rprint("[dim]Note: Only submit components you created or represent.[/dim]")
    if draft and submit_draft:
        fail(
            ErrorCategory.VALIDATION,
            "Draft creation and draft submission cannot be requested together.",
            operation="Submit prompt",
            resource="submit options",
            remediation="Choose either draft creation or draft submission and retry.",
        )
    if submit_draft:
        resolved = client.resolve_registry_reference("prompt", submit_draft)
        submit_context = nullcontext() if output == "json" else spinner("Submitting draft for review...")
        with submit_context:
            result = client.post(f"/api/v1/prompts/{resolved}/submit")
        if output == "json":
            output_json(result)
        else:
            rprint(f"[green]✓ Draft submitted for review![/green] ID: [bold]{esc(result['id'])}[/bold]")
        return

    flag_mode = any(x is not None for x in (name, version, description, category, template))
    if from_file:
        try:
            content = Path(from_file).read_text()
        except FileNotFoundError as error:
            fail(
                ErrorCategory.NOT_FOUND,
                "The prompt submission file was not found.",
                operation="Submit prompt",
                resource=from_file,
                remediation="Provide an existing file and retry.",
                detail=repr(error),
            )
        try:
            payload = _json.loads(content)
            if not payload.get("owner"):
                payload["owner"] = config.load().get("username", "")
        except _json.JSONDecodeError:
            if output == "json" and not flag_mode:
                fail(
                    ErrorCategory.VALIDATION,
                    "JSON mode requires prompt metadata for a template file.",
                    operation="Submit prompt",
                    resource=from_file,
                    remediation="Provide name, description, and template metadata options.",
                )
            if flag_mode:
                payload = {
                    "name": name,
                    "version": version or "1.0.0",
                    "description": description,
                    "owner": config.load().get("username", ""),
                    "category": category or "general",
                    "template": template or content,
                }
            else:
                payload = {
                    "name": text_input("Prompt name"),
                    "version": text_input("Version", default="1.0.0"),
                    "description": text_input("Description"),
                    "owner": config.load().get("username", ""),
                    "category": select_one("Category", VALID_PROMPT_CATEGORIES),
                    "template": content,
                }
    elif flag_mode:
        payload = {
            "name": name,
            "version": version or "1.0.0",
            "description": description,
            "owner": config.load().get("username", ""),
            "category": category or "general",
            "template": template,
        }
    else:
        if output == "json":
            fail(
                ErrorCategory.VALIDATION,
                "JSON mode requires explicit prompt fields.",
                operation="Submit prompt",
                resource="submit options",
                remediation="Provide name, description, category, and template options.",
            )
        payload = {
            "name": text_input("Prompt name"),
            "version": text_input("Version", default="1.0.0"),
            "description": text_input("Description"),
            "owner": config.load().get("username", ""),
            "category": select_one("Category", VALID_PROMPT_CATEGORIES),
            "template": text_input("Template"),
        }
    if flag_mode and not (payload.get("name") and payload.get("description") and payload.get("template")):
        fail(
            ErrorCategory.VALIDATION,
            "Prompt name, description, and template are required without prompts.",
            operation="Submit prompt",
            resource="prompt payload",
            remediation="Provide the required fields and retry.",
        )
    if payload.get("category") not in VALID_PROMPT_CATEGORIES:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown prompt category: {payload.get('category')}.",
            operation="Submit prompt",
            resource="category",
            remediation=f"Choose one of: {', '.join(VALID_PROMPT_CATEGORIES)}.",
        )
    try:
        Version(str(payload.get("version") or ""))
    except InvalidVersion as error:
        fail(
            ErrorCategory.VALIDATION,
            "The prompt version is invalid.",
            operation="Submit prompt",
            resource=str(payload.get("version") or ""),
            remediation="Provide a valid version and retry.",
            detail=repr(error),
        )

    client.add_publish_target(payload, team, visibility)
    submit_context = nullcontext() if output == "json" else spinner("Saving prompt...")
    with submit_context:
        endpoint = "/api/v1/prompts/draft" if draft else "/api/v1/prompts/submit"
        result = client.post(endpoint, payload)
    if output == "json":
        output_json(result)
        return
    message = "Draft saved" if draft else "Prompt submitted"
    rprint(f"[green]✓ {message}![/green] ID: [bold]{esc(result['id'])}[/bold]")
    rprint(f"  Render: [cyan]observal registry prompt render {esc(client.canonical_name(result))}[/cyan]")


@prompt_app.command(name="list")
def prompt_list(
    category: str | None = typer.Option(None, "--category", "-c"),
    search: str | None = typer.Option(None, "--search", "-s"),
    namespace: str | None = typer.Option(None, "--namespace", help="Filter by user or team namespace"),
    team: str | None = typer.Option(None, "--team", help="Only items owned by this teamspace"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """List approved prompts in the registry.

    Shows only prompts with approved status. Use --category or --search to
    filter results. Row numbers from the output can be used as references
    in subsequent commands.

    Examples:
        observal registry prompt list
        observal registry prompt list --category coding
        observal registry prompt list --search "refactor" --output json
    """
    if category and category not in VALID_PROMPT_CATEGORIES:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown prompt category: {category}.",
            operation="List prompts",
            resource="category filter",
            remediation=f"Choose one of: {', '.join(VALID_PROMPT_CATEGORIES)}.",
        )
    params = {}
    if category:
        params["category"] = category
    if search:
        params["search"] = search
    if namespace:
        params["namespace"] = namespace.lstrip("@").lower()
    if team:
        params["team_id"] = client.resolve_team_id(team)
    fetch_ctx = nullcontext() if output == "json" else spinner("Fetching prompts...")
    with fetch_ctx:
        data = client.get("/api/v1/prompts", params=params)
    if not data:
        config.save_last_results([], "prompt")
        if output == "json":
            output_json([])
        else:
            rprint("[dim]No prompts found.[/dim]")
        return
    config.save_last_results(data, "prompt")
    if output == "json":
        output_json(data)
        return
    table = Table(title=f"Prompts ({len(data)})", show_lines=False, padding=(0, 1))
    table.add_column("#", style="dim", width=3)
    table.add_column("Name", style="bold cyan", no_wrap=True)
    table.add_column("Version", style="green")
    table.add_column("Namespace", style="dim")
    table.add_column("Status")
    table.add_column("ID", style="dim", max_width=12)
    for i, item in enumerate(data, 1):
        table.add_row(
            str(i),
            esc(display_name(item)),
            esc(item.get("version", "")),
            esc(handle(item)),
            status_badge(item.get("status", "")),
            esc(str(item["id"])[:8] + "…"),
        )
    console.print(table)


@prompt_app.command(name="my")
def prompt_my(
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """List your own prompts across all statuses.

    Shows drafts, pending, approved, and rejected prompts you submitted.
    Useful for tracking the review status of your submissions.

    Examples:
        observal registry prompt my
        observal registry prompt my --output json
    """
    fetch_ctx = nullcontext() if output == "json" else spinner("Fetching your prompts...")
    with fetch_ctx:
        data = client.get("/api/v1/prompts/my")
    if not data:
        config.save_last_results([], "prompt")
        if output == "json":
            output_json([])
        else:
            rprint("[dim]You have no prompts.[/dim]")
        return
    config.save_last_results(data, "prompt")
    if output == "json":
        output_json(data)
        return
    table = Table(title=f"My Prompts ({len(data)})", show_lines=False, padding=(0, 1))
    table.add_column("#", style="dim", width=3)
    table.add_column("Name", style="bold cyan", no_wrap=True)
    table.add_column("Version", style="green")
    table.add_column("Namespace", style="dim")
    table.add_column("Status")
    table.add_column("ID", style="dim", max_width=12)
    for i, item in enumerate(data, 1):
        table.add_row(
            str(i),
            esc(display_name(item)),
            esc(item.get("version", "")),
            esc(handle(item)),
            status_badge(item.get("status", "")),
            esc(str(item["id"])[:8] + "…"),
        )
    console.print(table)


@prompt_app.command(name="show")
def prompt_show(
    prompt_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Show detailed information about a prompt.

    Displays metadata, status, category, template content, and timestamps.
    Accepts a UUID, name, row number from a previous list, or @alias.

    Examples:
        observal registry prompt show my-prompt
        observal registry prompt show @refactor-prompt
        observal registry prompt show abc123 --output json
    """
    resolved = client.resolve_registry_reference("prompt", prompt_id)
    fetch_ctx = nullcontext() if output == "json" else spinner()
    with fetch_ctx:
        item = client.get(f"/api/v1/prompts/{resolved}")
    if output == "json":
        output_json(item)
        return
    console.print(
        kv_panel(
            f"{esc(display_name(item))} v{esc(item.get('version', '?'))}",
            [
                ("Status", listing_status(item)),
                ("Category", esc(item.get("category", "N/A"))),
                ("Namespace", esc(handle(item) or "N/A")),
                ("Description", esc(item.get("description", ""))),
                ("Created", esc(relative_time(item.get("created_at")))),
                *fork_detail_rows(item),
                ("ID", f"[dim]{esc(item['id'])}[/dim]"),
            ],
            border_style="cyan",
        )
    )
    if item.get("template"):
        rprint(f"\n[bold]Template:[/bold]\n[dim]{esc(item['template'])}[/dim]")


@prompt_app.command(name="render")
def prompt_render(
    prompt_id: str = typer.Argument(..., help="Prompt ID, name, row number, or @alias"),
    var: list[str] = typer.Option([], "--var", "-v", help="Variable as key=value"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Render a prompt template with variable substitution.

    Sends variable key=value pairs to the server, which substitutes them
    into the prompt template and returns the rendered output. Also emits
    a prompt_render telemetry span.

    Examples:
        observal registry prompt render my-prompt --var lang=python
        observal registry prompt render @tpl --var file=main.py --var task=refactor --output json
    """
    resolved = client.resolve_registry_reference("prompt", prompt_id)
    variables = {}
    for value in var:
        key, separator, raw = value.partition("=")
        if not separator or not key.strip():
            fail(
                ErrorCategory.VALIDATION,
                "Prompt variables must use key=value syntax.",
                operation="Render prompt",
                resource=value,
                remediation="Provide each variable as a non-empty key and value.",
            )
        variables[key.strip()] = raw.strip("\"'")
    render_context = nullcontext() if output == "json" else spinner("Rendering prompt...")
    with render_context:
        result = client.post_public(f"/api/v1/prompts/{resolved}/render", {"variables": variables})
    if output == "json":
        output_json(result)
    else:
        rprint(esc(result.get("rendered", result)))


@prompt_app.command(name="edit")
def prompt_edit(
    prompt_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    from_file: str | None = typer.Option(None, "--from-file", "-f", help="Load updates from JSON file"),
    name: str | None = typer.Option(None, "--name", "-n", help="New listing name"),
    description: str | None = typer.Option(None, "--description", "-d", help="New description"),
    version: str | None = typer.Option(None, "--version", "-v", help="New version string"),
    category: str | None = typer.Option(None, "--category", "-c", help="New category"),
    template: str | None = typer.Option(None, "--template", "-t", help="New template text"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Edit a draft, rejected, or pending prompt submission.

    Updates fields on a prompt that has not yet been approved. You can
    provide individual field options or load all updates from a JSON file.
    Acquires an edit lock to prevent concurrent modifications.

    Examples:
        observal registry prompt edit my-prompt --description "Updated desc"
        observal registry prompt edit abc123 --from-file updates.json
        observal registry prompt edit @tpl --template "New template: {{var}}" --output json
    """
    resolved = client.resolve_registry_reference("prompt", prompt_id)
    if from_file:
        updates = load_json_object(from_file, operation="Edit prompt", noun="prompt update file")
    else:
        updates = {}
        if name is not None:
            updates["name"] = name
        if description is not None:
            updates["description"] = description
        if version is not None:
            updates["version"] = version
        if category is not None:
            updates["category"] = category
        if template is not None:
            updates["template"] = template

    if not updates:
        fail(
            ErrorCategory.VALIDATION,
            "No prompt changes were provided.",
            operation="Edit prompt",
            resource=prompt_id,
            remediation="Provide an update file or one or more field options.",
        )
    updated_category = updates.get("category")
    if updated_category is not None and updated_category not in VALID_PROMPT_CATEGORIES:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown prompt category: {updated_category}.",
            operation="Edit prompt",
            resource="category",
            remediation=f"Choose one of: {', '.join(VALID_PROMPT_CATEGORIES)}.",
        )
    updated_version = updates.get("version")
    if updated_version is not None:
        try:
            Version(str(updated_version))
        except InvalidVersion as error:
            fail(
                ErrorCategory.VALIDATION,
                "The prompt version is invalid.",
                operation="Edit prompt",
                resource=str(updated_version),
                remediation="Provide a valid version and retry.",
                detail=repr(error),
            )

    client.post(f"/api/v1/prompts/{resolved}/start-edit")
    save_context = nullcontext() if output == "json" else spinner("Saving changes...")
    with save_context:
        result = client.put(f"/api/v1/prompts/{resolved}/draft", updates)
    if output == "json":
        output_json(result)
    else:
        rprint(f"[green]✓ Updated {esc(result['name'])}[/green] (status: {esc(result.get('status', 'unknown'))})")
