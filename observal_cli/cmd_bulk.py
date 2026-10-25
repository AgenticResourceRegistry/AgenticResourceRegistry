# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Mixed Registry component bulk submission."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich import print as rprint
from rich.table import Table

from observal_cli import client
from observal_cli.errors import CliError, ErrorCategory, exit_partial, fail
from observal_cli.render import OutputMode, esc, output_json

bulk_app = typer.Typer(
    name="bulk",
    help=(
        "Submit mixed Registry components from one JSON file.\n\n"
        "Examples:\n"
        "  observal registry bulk submit --from-file components.json --dry-run --output json\n"
        "  observal registry bulk submit --from-file components.json --yes --output json"
    ),
    no_args_is_help=True,
)

_ENDPOINTS = {
    "mcp": "/api/v1/mcps/submit",
    "skill": "/api/v1/skills/submit",
    "hook": "/api/v1/hooks/submit",
    "prompt": "/api/v1/prompts/submit",
    "sandbox": "/api/v1/sandboxes/submit",
}
_STOP_CATEGORIES = {
    ErrorCategory.AUTH,
    ErrorCategory.PERMISSION,
    ErrorCategory.RATE_LIMIT,
    ErrorCategory.UNAVAILABLE,
    ErrorCategory.VERSION,
}


def _load_components(path: str) -> list[dict]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        fail(
            ErrorCategory.NOT_FOUND,
            "Bulk component file was not found.",
            operation="Bulk submit components",
            resource=path,
            remediation="Provide an existing JSON file and retry.",
            detail=repr(error),
        )
    except json.JSONDecodeError as error:
        fail(
            ErrorCategory.VALIDATION,
            "Bulk component file is not valid JSON.",
            operation="Bulk submit components",
            resource=path,
            remediation="Correct the JSON and retry.",
            detail=repr(error),
        )
    entries = raw if isinstance(raw, list) else raw.get("components") if isinstance(raw, dict) else None
    if not isinstance(entries, list) or not entries:
        fail(
            ErrorCategory.VALIDATION,
            'Bulk component JSON must be a non-empty array or {"components": [...]}.',
            operation="Bulk submit components",
            resource=path,
            remediation="Add at least one typed component object.",
        )
    if len(entries) > 200:
        fail(
            ErrorCategory.VALIDATION,
            "Bulk component files may contain at most 200 entries.",
            operation="Bulk submit components",
            resource=path,
            remediation="Split the input into files of 200 entries or fewer.",
        )

    components: list[dict] = []
    identities: set[tuple[str, str]] = set()
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            fail(
                ErrorCategory.VALIDATION,
                f"Bulk component entry {index} must be an object.",
                operation="Bulk submit components",
                resource=path,
                remediation="Replace scalar and array entries with typed component objects.",
            )
        component_type = str(entry.get("type", "")).strip().lower()
        if component_type not in _ENDPOINTS:
            fail(
                ErrorCategory.VALIDATION,
                f"Bulk component entry {index} has an unsupported type.",
                operation="Bulk submit components",
                resource=path,
                remediation=f"Choose from: {', '.join(_ENDPOINTS)}.",
            )
        payload = {key: value for key, value in entry.items() if key != "type"}
        name = str(payload.get("name", "")).strip()
        if not name:
            fail(
                ErrorCategory.VALIDATION,
                f"Bulk component entry {index} requires a name.",
                operation="Bulk submit components",
                resource=path,
                remediation="Add a non-empty name to every entry.",
            )
        identity = (component_type, name.lower())
        if identity in identities:
            fail(
                ErrorCategory.VALIDATION,
                f"Bulk component file repeats {component_type} name: {name}.",
                operation="Bulk submit components",
                resource=path,
                remediation="Keep one entry for each component type and name.",
            )
        identities.add(identity)
        payload.setdefault("version", "1.0.0")
        components.append({"type": component_type, "name": name, "payload": payload})
    return components


def _owner() -> str:
    configured = client.config.load().get("username")
    if configured:
        return str(configured)
    identity = client.get("/api/v1/auth/whoami", operation="Resolve bulk component owner", resource="user account")
    return str(identity.get("username") or identity.get("email") or "")


def _preview_table(components: list[dict]) -> Table:
    table = Table(title=f"Components to submit ({len(components)})")
    table.add_column("#", style="dim")
    table.add_column("type", style="cyan")
    table.add_column("name", style="green")
    table.add_column("version")
    table.add_column("visibility")
    for index, component in enumerate(components, 1):
        payload = component["payload"]
        table.add_row(
            str(index),
            component["type"],
            esc(component["name"]),
            esc(payload.get("version", "1.0.0")),
            esc(payload.get("visibility", "public")),
        )
    return table


def _results_table(results: list[dict], *, title: str) -> Table:
    table = Table(title=title)
    table.add_column("#", style="dim")
    table.add_column("type", style="cyan")
    table.add_column("name", style="green")
    table.add_column("status")
    table.add_column("id", style="dim")
    table.add_column("error", style="red")
    for index, result in enumerate(results, 1):
        table.add_row(
            str(index),
            result["type"],
            esc(result["name"]),
            result["status"],
            esc(result.get("id") or ""),
            esc((result.get("error") or {}).get("message") or ""),
        )
    return table


@bulk_app.command("submit")
def bulk_submit_components(
    file_path: str = typer.Option(..., "--from-file", help="JSON file containing mixed typed components."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate file structure and preview without mutations."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json."),
):
    """Submit mixed MCP, skill, hook, prompt, and sandbox entries.

    Each entry contains a "type" plus the normal API submission fields for
    that component. Conflicts are reported as skipped; validation failures are
    reported per entry. Authentication and service failures stop the batch.

    Examples:
      observal registry bulk submit --from-file components.json --dry-run --output json
      observal registry bulk submit --from-file components.json --yes --output json
    """
    components = _load_components(file_path)
    if output == "json" and not (dry_run or yes):
        fail(
            ErrorCategory.VALIDATION,
            "JSON mode requires --yes before bulk component submission.",
            operation="Bulk submit components",
            resource=file_path,
            remediation="Add --yes or use --dry-run.",
        )
    if output != "json":
        rprint(_preview_table(components))
    if dry_run:
        results = [{"type": item["type"], "name": item["name"], "status": "planned"} for item in components]
        response = {
            "total": len(components),
            "submitted": 0,
            "skipped": 0,
            "errors": 0,
            "partial": False,
            "dry_run": True,
            "results": results,
        }
        if output == "json":
            output_json(response)
        else:
            rprint(_results_table(results, title="Bulk validation results"))
        return

    if not yes and not typer.confirm(f"Submit {len(components)} Registry components?", default=False):
        raise typer.Abort()

    owner = _owner() if any(not str(item["payload"].get("owner") or "").strip() for item in components) else ""
    results: list[dict] = []
    submitted = skipped = errors = 0
    for item in components:
        payload = item["payload"]
        if not str(payload.get("owner") or "").strip():
            payload["owner"] = owner
        try:
            created = client.post(
                _ENDPOINTS[item["type"]],
                json_data=payload,
                operation="Bulk submit components",
                resource=f"{item['type']} submission",
            )
        except CliError as error:
            if error.category in _STOP_CATEGORIES:
                raise
            status = "skipped" if error.category is ErrorCategory.CONFLICT else "error"
            skipped += status == "skipped"
            errors += status == "error"
            results.append(
                {
                    "type": item["type"],
                    "name": item["name"],
                    "status": status,
                    "error": {
                        "category": error.category.value,
                        "message": error.message,
                        "request_id": error.request_id,
                    },
                }
            )
            continue
        submitted += 1
        results.append(
            {
                "type": item["type"],
                "name": item["name"],
                "status": "submitted",
                "id": created.get("id"),
                "qualified_name": created.get("qualified_name"),
                "review_status": created.get("status"),
            }
        )

    response = {
        "total": len(components),
        "submitted": submitted,
        "skipped": skipped,
        "errors": errors,
        "partial": errors > 0,
        "dry_run": False,
        "results": results,
    }
    if output == "json":
        output_json(response)
    else:
        rprint(_results_table(results, title="Bulk submission results"))
        rprint(f"[green]{submitted} submitted[/green], [yellow]{skipped} skipped[/yellow], [red]{errors} errors[/red]")
    if errors:
        exit_partial()
