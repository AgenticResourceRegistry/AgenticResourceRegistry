# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Sandbox registry CLI commands."""

from __future__ import annotations

import json as _json
from contextlib import nullcontext

import typer
from packaging.version import InvalidVersion, Version
from rich import print as rprint
from rich.table import Table

from observal_cli import client, config
from observal_cli.cmd_fork import fork_detail_rows
from observal_cli.constants import VALID_HARNESSES, VALID_SANDBOX_NETWORK_POLICIES, VALID_SANDBOX_RUNTIME_TYPES
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

sandbox_app = typer.Typer(
    help=(
        "Sandbox registry commands\n\n"
        "Examples:\n"
        "  observal registry sandbox list\n"
        "  observal registry sandbox show alice/my-sandbox\n"
        "  observal registry sandbox submit --from-file sandbox.json"
    )
)


def register_sandbox(app: typer.Typer):
    app.add_typer(sandbox_app, name="sandbox")


def _json_object(value: str, label: str, operation: str) -> dict:
    try:
        parsed = _json.loads(value)
    except _json.JSONDecodeError as error:
        fail(
            ErrorCategory.VALIDATION,
            f"The {label} value is not valid JSON.",
            operation=operation,
            resource=label,
            remediation="Provide a JSON object and retry.",
            detail=repr(error),
        )
    if not isinstance(parsed, dict):
        fail(
            ErrorCategory.VALIDATION,
            f"The {label} value must be a JSON object.",
            operation=operation,
            resource=label,
            remediation="Provide a JSON object and retry.",
        )
    return parsed


@sandbox_app.command(name="submit")
def sandbox_submit(
    from_file: str | None = typer.Option(None, "--from-file", "-f", help="Create from JSON file"),
    name: str | None = typer.Option(None, "--name", "-n", help="Sandbox name"),
    version: str | None = typer.Option(None, "--version", "-v", help="Version (default: 1.0.0)"),
    description: str | None = typer.Option(None, "--description", "-d", help="Short description"),
    runtime_type: str | None = typer.Option(None, "--runtime-type", "-r", help="Runtime type"),
    image: str | None = typer.Option(None, "--image", "-i", help="Container image"),
    resource_limits: str | None = typer.Option(None, "--resource-limits", help="Resource limits JSON"),
    runtime_config: str | None = typer.Option(None, "--runtime-config", help="Runtime-specific config JSON"),
    network_policy: str | None = typer.Option(None, "--network-policy", help="Network policy"),
    entrypoint: str | None = typer.Option(None, "--entrypoint", help="Default entrypoint"),
    supported_harnesses: list[str] | None = typer.Option(None, "--harness", help="Supported harness (repeatable)"),
    source_url: str | None = typer.Option(None, "--source-url", help="Source repository URL"),
    source_ref: str | None = typer.Option(None, "--source-ref", help="Source branch/tag"),
    sandbox_path: str | None = typer.Option(None, "--sandbox-path", help="Path in source repo"),
    draft: bool = typer.Option(False, "--draft", help="Save as draft instead of submitting for review"),
    submit_draft: str | None = typer.Option(None, "--submit", help="Submit a draft for review (sandbox ID)"),
    team: str | None = typer.Option(None, "--team", help="Teamspace UUID or handle"),
    visibility: str | None = typer.Option(None, "--visibility", help="Visibility: public or team"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Submit a new sandbox environment for review.

    Sandboxes are containerized execution environments for agent tasks.
    You can submit interactively, from a JSON file, or save as a draft
    first and submit later with --submit.

    Only submit sandboxes you created or are the point-of-contact for.

    Examples:
        observal registry sandbox submit --from-file sandbox.json
        observal registry sandbox submit --draft
        observal registry sandbox submit --submit abc123 --output json
    """
    human_output = output != "json"
    if human_output:
        rprint("[dim]Note: Only submit components you created or represent.[/dim]")
    if draft and submit_draft:
        fail(
            ErrorCategory.VALIDATION,
            "Draft creation and draft submission cannot be requested together.",
            operation="Submit sandbox",
            resource="submit options",
            remediation="Choose either draft creation or draft submission and retry.",
        )
    if submit_draft:
        resolved = client.resolve_registry_reference("sandbox", submit_draft)
        submit_context = nullcontext() if output == "json" else spinner("Submitting draft for review...")
        with submit_context:
            result = client.post(f"/api/v1/sandboxes/{resolved}/submit")
        if output == "json":
            output_json(result)
        else:
            rprint(f"[green]✓ Draft submitted for review![/green] ID: [bold]{esc(result['id'])}[/bold]")
        return

    flag_mode = any(
        x is not None
        for x in (
            name,
            version,
            description,
            runtime_type,
            image,
            resource_limits,
            runtime_config,
            network_policy,
            entrypoint,
            supported_harnesses,
            source_url,
            source_ref,
            sandbox_path,
        )
    )
    if from_file:
        try:
            with open(from_file) as f:
                payload = _json.load(f)
        except _json.JSONDecodeError as error:
            fail(
                ErrorCategory.VALIDATION,
                "The sandbox submission file is not valid JSON.",
                operation="Submit sandbox",
                resource=from_file,
                remediation="Correct the JSON and retry.",
                detail=repr(error),
            )
        except FileNotFoundError as error:
            fail(
                ErrorCategory.NOT_FOUND,
                "The sandbox submission file was not found.",
                operation="Submit sandbox",
                resource=from_file,
                remediation="Provide an existing JSON file and retry.",
                detail=repr(error),
            )
        if not isinstance(payload, dict):
            fail(
                ErrorCategory.VALIDATION,
                "The sandbox submission file must contain a JSON object.",
                operation="Submit sandbox",
                resource=from_file,
                remediation="Replace the file contents with a JSON object and retry.",
            )
        if not payload.get("owner"):
            payload["owner"] = config.load().get("username", "")
    elif flag_mode:
        limits = _json_object(resource_limits or "{}", "resource limits", "Submit sandbox")
        runtime_cfg = _json_object(runtime_config or "{}", "runtime config", "Submit sandbox")
        payload = {
            "name": name,
            "version": version or "1.0.0",
            "description": description,
            "owner": config.load().get("username", ""),
            "runtime_type": runtime_type,
            "image": image,
            "resource_limits": limits,
            "runtime_config": runtime_cfg,
            "network_policy": network_policy or "none",
            "supported_harnesses": supported_harnesses or [],
        }
        if entrypoint:
            payload["entrypoint"] = entrypoint
        if source_url:
            payload["source_url"] = source_url
        if source_ref:
            payload["source_ref"] = source_ref
        if sandbox_path:
            payload["sandbox_path"] = sandbox_path
    else:
        if output == "json":
            fail(
                ErrorCategory.VALIDATION,
                "JSON mode requires explicit sandbox fields.",
                operation="Submit sandbox",
                resource="submit options",
                remediation="Provide name, description, runtime type, image, and JSON configuration options.",
            )
        interactive_name = text_input("Sandbox name")
        interactive_version = text_input("Version", default="1.0.0")
        interactive_description = text_input("Description")
        interactive_runtime = select_one("Runtime type", VALID_SANDBOX_RUNTIME_TYPES)
        interactive_image = text_input("Image")
        limits = _json_object(text_input("Resource limits (JSON)", default="{}"), "resource limits", "Submit sandbox")
        runtime_cfg = _json_object(
            text_input("Runtime config (JSON)", default="{}"), "runtime config", "Submit sandbox"
        )
        payload = {
            "name": interactive_name,
            "version": interactive_version,
            "description": interactive_description,
            "owner": config.load().get("username", ""),
            "runtime_type": interactive_runtime,
            "image": interactive_image,
            "resource_limits": limits,
            "runtime_config": runtime_cfg,
            "network_policy": "none",
            "supported_harnesses": [],
        }
    if not (
        payload.get("name") and payload.get("description") and payload.get("runtime_type") and payload.get("image")
    ):
        fail(
            ErrorCategory.VALIDATION,
            "Sandbox name, description, runtime type, and image are required.",
            operation="Submit sandbox",
            resource="sandbox payload",
            remediation="Provide the required fields and retry.",
        )
    if payload.get("runtime_type") not in VALID_SANDBOX_RUNTIME_TYPES:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown sandbox runtime type: {payload.get('runtime_type')}.",
            operation="Submit sandbox",
            resource="runtime type",
            remediation=f"Choose one of: {', '.join(VALID_SANDBOX_RUNTIME_TYPES)}.",
        )
    if payload.get("network_policy", "none") not in VALID_SANDBOX_NETWORK_POLICIES:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown sandbox network policy: {payload.get('network_policy')}.",
            operation="Submit sandbox",
            resource="network policy",
            remediation=f"Choose one of: {', '.join(VALID_SANDBOX_NETWORK_POLICIES)}.",
        )
    bad_harnesses = [h for h in payload.get("supported_harnesses", []) if h not in VALID_HARNESSES]
    if bad_harnesses:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown harness: {bad_harnesses[0]}.",
            operation="Submit sandbox",
            resource="supported harnesses",
            remediation=f"Choose from: {', '.join(VALID_HARNESSES)}.",
        )
    try:
        Version(str(payload.get("version") or ""))
    except InvalidVersion as error:
        fail(
            ErrorCategory.VALIDATION,
            "The sandbox version is invalid.",
            operation="Submit sandbox",
            resource=str(payload.get("version") or ""),
            remediation="Provide a valid version and retry.",
            detail=repr(error),
        )

    client.add_publish_target(payload, team, visibility)
    submit_context = nullcontext() if output == "json" else spinner("Saving sandbox...")
    with submit_context:
        endpoint = "/api/v1/sandboxes/draft" if draft else "/api/v1/sandboxes/submit"
        result = client.post(endpoint, payload)
    if output == "json":
        output_json(result)
        return
    message = "Draft saved" if draft else "Sandbox submitted"
    rprint(f"[green]✓ {message}![/green] ID: [bold]{esc(result['id'])}[/bold]")
    rprint(f"  Attach to an agent with ID: [cyan]{esc(result['id'])}[/cyan]")


@sandbox_app.command(name="list")
def sandbox_list(
    runtime: str | None = typer.Option(None, "--runtime", "-r"),
    search: str | None = typer.Option(None, "--search", "-s"),
    namespace: str | None = typer.Option(None, "--namespace", help="Filter by user or team namespace"),
    team: str | None = typer.Option(None, "--team", help="Only items owned by this teamspace"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """List approved sandboxes in the registry.

    Shows only sandboxes with approved status. Use --runtime or --search
    to filter results. Row numbers from the output can be used as references
    in subsequent commands.

    Examples:
        observal registry sandbox list
        observal registry sandbox list --runtime docker
        observal registry sandbox list --search "node" --output json
    """
    if runtime and runtime not in VALID_SANDBOX_RUNTIME_TYPES:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown sandbox runtime type: {runtime}.",
            operation="List sandboxes",
            resource="runtime filter",
            remediation=f"Choose one of: {', '.join(VALID_SANDBOX_RUNTIME_TYPES)}.",
        )
    params = {}
    if runtime:
        params["runtime"] = runtime
    if search:
        params["search"] = search
    if namespace:
        params["namespace"] = namespace.lstrip("@").lower()
    if team:
        params["team_id"] = client.resolve_team_id(team)
    fetch_ctx = nullcontext() if output == "json" else spinner("Fetching sandboxes...")
    with fetch_ctx:
        data = client.get("/api/v1/sandboxes", params=params)
    if not data:
        config.save_last_results([], "sandbox")
        if output == "json":
            output_json([])
        else:
            rprint("[dim]No sandboxes found.[/dim]")
        return
    config.save_last_results(data, "sandbox")
    if output == "json":
        output_json(data)
        return
    table = Table(title=f"Sandboxes ({len(data)})", show_lines=False, padding=(0, 1))
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


@sandbox_app.command(name="show")
def sandbox_show(
    sandbox_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Show detailed information about a sandbox.

    Displays metadata including runtime type, container image, resource
    limits, status, and timestamps. Accepts a UUID, name, row number
    from a previous list, or @alias.

    Examples:
        observal registry sandbox show my-sandbox
        observal registry sandbox show 1
        observal registry sandbox show @dev-env --output json
    """
    resolved = client.resolve_registry_reference("sandbox", sandbox_id)
    fetch_ctx = nullcontext() if output == "json" else spinner()
    with fetch_ctx:
        item = client.get(f"/api/v1/sandboxes/{resolved}")
    if output == "json":
        output_json(item)
        return
    console.print(
        kv_panel(
            f"{esc(display_name(item))} v{esc(item.get('version', '?'))}",
            [
                ("Status", listing_status(item)),
                ("Runtime", esc(item.get("runtime_type", "N/A"))),
                ("Image", esc(item.get("image", "N/A"))),
                ("Namespace", esc(handle(item) or "N/A")),
                ("Description", esc(item.get("description", ""))),
                ("Created", esc(relative_time(item.get("created_at")))),
                *fork_detail_rows(item),
                ("ID", f"[dim]{esc(item['id'])}[/dim]"),
            ],
            border_style="red",
        )
    )


@sandbox_app.command(name="edit")
def sandbox_edit(
    sandbox_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    from_file: str | None = typer.Option(None, "--from-file", "-f", help="Load updates from JSON file"),
    name: str | None = typer.Option(None, "--name", "-n", help="New listing name"),
    description: str | None = typer.Option(None, "--description", "-d", help="New description"),
    version: str | None = typer.Option(None, "--version", "-v", help="New version string"),
    runtime_type: str | None = typer.Option(None, "--runtime-type", "-r", help="New runtime type"),
    image: str | None = typer.Option(None, "--image", "-i", help="New container image"),
    resource_limits: str | None = typer.Option(None, "--resource-limits", help="Resource limits JSON"),
    runtime_config: str | None = typer.Option(None, "--runtime-config", help="Runtime config JSON"),
    network_policy: str | None = typer.Option(None, "--network-policy", help="New network policy"),
    entrypoint: str | None = typer.Option(None, "--entrypoint", help="New entrypoint"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Edit a draft, rejected, or pending sandbox submission.

    Updates fields on a sandbox that has not yet been approved. You can
    provide individual field options or load all updates from a JSON file.
    Acquires an edit lock to prevent concurrent modifications.

    Examples:
        observal registry sandbox edit my-sandbox --image node:20-alpine
        observal registry sandbox edit abc123 --from-file updates.json
        observal registry sandbox edit @env --runtime-type docker --version 2.0.0 --output json
    """
    resolved = client.resolve_registry_reference("sandbox", sandbox_id)
    if from_file:
        updates = load_json_object(from_file, operation="Edit sandbox", noun="sandbox update file")
    else:
        updates = {}
        if name is not None:
            updates["name"] = name
        if description is not None:
            updates["description"] = description
        if version is not None:
            updates["version"] = version
        if runtime_type is not None:
            updates["runtime_type"] = runtime_type
        if image is not None:
            updates["image"] = image
        if resource_limits is not None:
            updates["resource_limits"] = _json_object(resource_limits, "resource limits", "Edit sandbox")
        if runtime_config is not None:
            updates["runtime_config"] = _json_object(runtime_config, "runtime config", "Edit sandbox")
        if network_policy is not None:
            updates["network_policy"] = network_policy
        if entrypoint is not None:
            updates["entrypoint"] = entrypoint

    if not updates:
        fail(
            ErrorCategory.VALIDATION,
            "No sandbox changes were provided.",
            operation="Edit sandbox",
            resource=sandbox_id,
            remediation="Provide an update file or one or more field options.",
        )
    updated_runtime = updates.get("runtime_type")
    if updated_runtime is not None and updated_runtime not in VALID_SANDBOX_RUNTIME_TYPES:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown sandbox runtime type: {updated_runtime}.",
            operation="Edit sandbox",
            resource="runtime type",
            remediation=f"Choose one of: {', '.join(VALID_SANDBOX_RUNTIME_TYPES)}.",
        )
    updated_policy = updates.get("network_policy")
    if updated_policy is not None and updated_policy not in VALID_SANDBOX_NETWORK_POLICIES:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown sandbox network policy: {updated_policy}.",
            operation="Edit sandbox",
            resource="network policy",
            remediation=f"Choose one of: {', '.join(VALID_SANDBOX_NETWORK_POLICIES)}.",
        )
    updated_version = updates.get("version")
    if updated_version is not None:
        try:
            Version(str(updated_version))
        except InvalidVersion as error:
            fail(
                ErrorCategory.VALIDATION,
                "The sandbox version is invalid.",
                operation="Edit sandbox",
                resource=str(updated_version),
                remediation="Provide a valid version and retry.",
                detail=repr(error),
            )

    client.post(f"/api/v1/sandboxes/{resolved}/start-edit")
    save_context = nullcontext() if output == "json" else spinner("Saving changes...")
    with save_context:
        result = client.put(f"/api/v1/sandboxes/{resolved}/draft", updates)
    if output == "json":
        output_json(result)
    else:
        rprint(f"[green]✓ Updated {esc(result['name'])}[/green] (status: {esc(result.get('status', 'unknown'))})")
