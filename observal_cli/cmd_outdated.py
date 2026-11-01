# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Compare installed lockfile versions with the active registry."""

from __future__ import annotations

import shlex
from contextlib import nullcontext

import typer
from packaging.version import InvalidVersion, Version
from rich import print as rprint
from rich.table import Table

from observal_cli import client
from observal_cli.constants import VALID_HARNESSES
from observal_cli.errors import CliError, ErrorCategory, fail
from observal_cli.render import OutputMode, console, esc, output_json, spinner

_OPERATION = "Check installed versions"
_COMPONENT_TYPES = {"mcp", "skill", "hook"}


def register_outdated(app: typer.Typer):
    @app.command("outdated")
    def outdated(
        harness: str | None = typer.Option(
            None,
            "--harness",
            "-i",
            help=f"Filter by harness: {', '.join(VALID_HARNESSES)}",
        ),
        output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
        report: bool = typer.Option(
            True,
            "--report/--no-report",
            help="Send findings to your Observal inbox so they persist between runs",
        ),
    ):
        """Show installed agents and standalone components with their registry status.

        Reads ~/.observal/lockfile.json and checks the authenticated registry for
        each pinned agent and separately installed MCP, skill, or hook. Reporting
        findings to the Inbox is best-effort and can be disabled.

        Examples:
          observal outdated
          observal outdated --harness claude-code
          observal outdated --output json --no-report
        """
        from observal_cli.config import CONFIG_FILE
        from observal_cli.lockfile import LOCKFILE_PATH, get_all_entries

        if harness and harness not in VALID_HARNESSES:
            fail(
                ErrorCategory.VALIDATION,
                f"Unknown harness: {harness}.",
                operation=_OPERATION,
                resource="harness filter",
                remediation=f"Choose one of: {', '.join(VALID_HARNESSES)}.",
            )

        try:
            entries = get_all_entries(harness=harness)
        except PermissionError as error:
            fail(
                ErrorCategory.PERMISSION,
                "The installed-state lockfile cannot be read.",
                operation=_OPERATION,
                resource=str(LOCKFILE_PATH),
                remediation="Check the lockfile ownership and permissions, then retry.",
                detail=repr(error),
            )
        except ValueError as error:
            fail(
                ErrorCategory.AUTH,
                "No active Observal registry is configured.",
                operation=_OPERATION,
                resource=str(CONFIG_FILE),
                remediation="Run observal auth login and retry.",
                detail=repr(error),
            )
        except (RuntimeError, AttributeError, TypeError) as error:
            cause = error.__cause__
            if isinstance(cause, PermissionError):
                fail(
                    ErrorCategory.PERMISSION,
                    "The installed-state lockfile cannot be read.",
                    operation=_OPERATION,
                    resource=str(LOCKFILE_PATH),
                    remediation="Check the lockfile ownership and permissions, then retry.",
                    detail=repr(error),
                )
            fail(
                ErrorCategory.VALIDATION,
                "The installed-state lockfile is malformed or unsupported.",
                operation=_OPERATION,
                resource=str(LOCKFILE_PATH),
                remediation="Repair or remove the lockfile, then reinstall the affected items.",
                detail=repr(error),
            )

        report_status = _report_status(requested=report)
        if not entries:
            payload = _result_payload([], report_status)
            if output == "json":
                output_json(payload)
            else:
                rprint("[dim]No installed agents or standalone components found in the lockfile.[/dim]")
                rprint("[dim]Run `observal agent pull` or a registry install command first.[/dim]")
            return

        installed = [_prepare_entry(entry, str(LOCKFILE_PATH)) for entry in entries]

        if output != "json":
            rprint(f"\n[bold]Checking {len(installed)} installed item(s)...[/bold]\n")

        results: list[dict] = []
        fetch_context = nullcontext() if output == "json" else spinner("Fetching latest registry versions...")
        with fetch_context:
            for item in installed:
                try:
                    data = client.get(
                        _registry_path(item["type"], item["id"]),
                        operation=_OPERATION,
                        resource=f"{item['type']} {item['qualified_name']}",
                    )
                except CliError as error:
                    if error.category is not ErrorCategory.NOT_FOUND:
                        raise
                    results.append(
                        {
                            **item,
                            "latest_version": None,
                            "status": "missing",
                            "outdated": False,
                            "error": _error_payload(error),
                            "upgrade_command": None,
                        }
                    )
                    continue

                if not isinstance(data, dict):
                    fail(
                        ErrorCategory.UNAVAILABLE,
                        "The registry returned an invalid item response.",
                        operation=_OPERATION,
                        resource=f"{item['type']} {item['qualified_name']}",
                        remediation="Check server health and version compatibility, then retry.",
                    )

                latest = _latest_version(item["type"], data)
                if not isinstance(latest, str) or not latest.strip():
                    fail(
                        ErrorCategory.UNAVAILABLE,
                        "The registry response does not contain a valid latest version.",
                        operation=_OPERATION,
                        resource=f"{item['type']} {item['qualified_name']}",
                        remediation="Check server health and version compatibility, then retry.",
                    )

                try:
                    # Check the registry's version even when the installed one is
                    # unknown, so a malformed release is never reported as fine.
                    Version(latest)
                    is_outdated = item["current_version"] is not None and _version_newer(
                        latest, item["current_version"]
                    )
                except InvalidVersion as error:
                    fail(
                        ErrorCategory.UNAVAILABLE,
                        "The registry returned an invalid latest version.",
                        operation=_OPERATION,
                        resource=f"{item['type']} {item['qualified_name']}",
                        remediation="Correct the registry version and retry.",
                        detail=repr(error),
                    )

                namespace = _text(data.get("namespace")) or item["namespace"]
                slug = _text(data.get("slug")) or item["slug"]
                qualified_name = f"{namespace}/{slug}" if namespace and slug else item["qualified_name"]
                result = {
                    **item,
                    "qualified_name": qualified_name,
                    "namespace": namespace,
                    "slug": slug,
                    "latest_version": latest,
                    "status": _status(item["current_version"], is_outdated),
                    "outdated": is_outdated,
                    "error": None,
                    "upgrade_command": None,
                }
                if result["status"] != "current":
                    # Reinstalling also records the version of an "unknown" entry.
                    result["upgrade_command"] = _upgrade_command(result)
                results.append(result)

        outdated_items = [item for item in results if item["outdated"]]
        if report and outdated_items:
            report_status = _report_to_inbox(outdated_items)

        payload = _result_payload(results, report_status)
        if output == "json":
            output_json(payload)
            return

        _render_table(payload)


def _status(current_version: str | None, is_outdated: bool) -> str:
    if current_version is None:
        return "unknown"
    return "outdated" if is_outdated else "current"


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _prepare_entry(entry: object, lockfile_path: str) -> dict:
    if not isinstance(entry, dict):
        fail(
            ErrorCategory.VALIDATION,
            "The installed-state lockfile contains an invalid item entry.",
            operation=_OPERATION,
            resource=lockfile_path,
            remediation="Reinstall the affected item to rebuild its lockfile entry.",
        )
    entry_type = _text(entry.get("entry_type"))
    component_type = _text(entry.get("type"))
    item_type = "agent" if entry_type == "agent" else component_type if entry_type == "standalone" else None
    if item_type != "agent" and item_type not in _COMPONENT_TYPES:
        fail(
            ErrorCategory.VALIDATION,
            "The installed-state lockfile contains an unsupported item type.",
            operation=_OPERATION,
            resource=lockfile_path,
            remediation="Reinstall the affected item to rebuild its lockfile entry.",
        )

    item_id = _text(entry.get("id"))
    # Older CLIs recorded no version for skills installed without --version.
    # Such entries are reported as "unknown" rather than failing the whole check.
    current_version = _text(entry.get("version"))
    item_harness = _text(entry.get("harness"))
    if not item_id or not item_harness:
        fail(
            ErrorCategory.VALIDATION,
            "An installed-state lockfile entry is missing its ID or harness.",
            operation=_OPERATION,
            resource=lockfile_path,
            remediation="Reinstall the affected item to rebuild its lockfile entry.",
        )
    if item_harness not in VALID_HARNESSES:
        fail(
            ErrorCategory.VALIDATION,
            "An installed-state lockfile entry uses an unsupported harness.",
            operation=_OPERATION,
            resource=lockfile_path,
            remediation="Reinstall the affected item for a currently supported harness.",
        )
    try:
        if current_version is not None:
            Version(current_version)
    except InvalidVersion as error:
        fail(
            ErrorCategory.VALIDATION,
            "An installed-state lockfile entry has an invalid version.",
            operation=_OPERATION,
            resource=lockfile_path,
            remediation="Reinstall the affected item to rebuild its lockfile entry.",
            detail=repr(error),
        )

    name = _text(entry.get("name")) or item_id[:8]
    namespace = _text(entry.get("namespace"))
    slug = _text(entry.get("slug"))
    qualified_name = _text(entry.get("qualified_name"))
    if not qualified_name:
        qualified_name = f"{namespace}/{slug}" if namespace and slug else item_id

    return {
        "id": item_id,
        "qualified_name": qualified_name,
        "name": name,
        "namespace": namespace,
        "slug": slug,
        "type": item_type,
        "harness": item_harness,
        "current_version": current_version,
    }


def _registry_path(item_type: str, item_id: str) -> str:
    return f"/api/v1/agents/{item_id}" if item_type == "agent" else f"/api/v1/{item_type}s/{item_id}"


def _latest_version(item_type: str, data: dict) -> object:
    if item_type == "agent":
        return data.get("latest_approved_version") or data.get("version")
    return data.get("version")


def _upgrade_command(item: dict) -> str:
    target = shlex.quote(item["qualified_name"])
    harness = shlex.quote(item["harness"])
    if item["type"] == "agent":
        # A plain pull keeps the locked version; moving forward is explicit.
        return f"observal agent pull {target} --harness {harness} --no-prompt --upgrade"
    prompt_flag = " --no-prompt" if item["type"] == "mcp" else ""
    return f"observal registry {item['type']} install {target} --harness {harness}{prompt_flag}"


def _error_payload(error: CliError) -> dict:
    return {
        "category": error.category.value,
        "message": error.message,
        "operation": error.operation,
        "resource": error.resource,
        "remediation": error.remediation,
        "request_id": error.request_id,
        "http_status": error.http_status,
        "exit_code": error.contract_exit_code,
    }


def _report_status(*, requested: bool) -> dict:
    return {
        "requested": requested,
        "attempted": False,
        "succeeded": None,
        "created": 0,
        "superseded": 0,
        "error": None,
    }


def _report_to_inbox(outdated_items: list[dict]) -> dict:
    status = _report_status(requested=True)
    status["attempted"] = True
    payload = [
        {
            "type": item["type"],
            "component_id": item["id"],
            "name": item["name"],
            "namespace": item["namespace"],
            "slug": item["slug"],
            "current_version": item["current_version"],
            "latest_version": item["latest_version"],
            "harness": item["harness"] or None,
        }
        for item in outdated_items
    ]
    try:
        result = client.post(
            "/api/v1/inbox/outdated-report",
            {"items": payload},
            operation="Report outdated items",
            resource="user inbox",
        )
    except CliError as error:
        status["succeeded"] = False
        status["error"] = _error_payload(error)
        return status

    counters = None if not isinstance(result, dict) else (result.get("created", 0), result.get("superseded", 0))
    if counters is None or any(type(value) is not int or value < 0 for value in counters):
        error = CliError(
            ErrorCategory.UNAVAILABLE,
            "The inbox returned an invalid report response.",
            operation="Report outdated items",
            resource="user inbox",
            remediation="Check server health and version compatibility, then retry.",
        )
        status["succeeded"] = False
        status["error"] = _error_payload(error)
        return status

    status["succeeded"] = True
    status["created"], status["superseded"] = counters
    return status


def _result_payload(results: list[dict], report_status: dict) -> dict:
    return {
        "items": results,
        "summary": {
            "total": len(results),
            "outdated": sum(item["status"] == "outdated" for item in results),
            "current": sum(item["status"] == "current" for item in results),
            "missing": sum(item["status"] == "missing" for item in results),
            "unknown": sum(item["status"] == "unknown" for item in results),
        },
        "report": report_status,
    }


def _render_table(payload: dict) -> None:
    items = payload["items"]
    summary = payload["summary"]
    table = Table(title="Installed Versions", show_header=True, header_style="bold")
    table.add_column("Name", style="cyan")
    table.add_column("Type", style="dim")
    table.add_column("Harness", style="dim")
    table.add_column("Pinned", style="yellow")
    table.add_column("Latest", style="green")
    table.add_column("Status")

    status_labels = {
        "outdated": "[yellow]outdated[/yellow]",
        "current": "[green]current[/green]",
        "missing": "[red]missing[/red]",
        "unknown": "[yellow]unknown[/yellow]",
    }
    for item in items:
        table.add_row(
            esc(item["qualified_name"]),
            esc(item["type"]),
            esc(item["harness"]),
            esc(item["current_version"] or "unrecorded"),
            esc(item["latest_version"] or "not found"),
            status_labels[item["status"]],
        )
    console.print(table)

    if summary["outdated"] or summary["unknown"]:
        if summary["outdated"]:
            rprint(f"\n[yellow]{summary['outdated']} item(s) have newer versions available.[/yellow]")
        rprint("[bold]Upgrade commands:[/bold]")
        for item in items:
            if item["upgrade_command"]:
                rprint(f"  [cyan]{esc(item['upgrade_command'])}[/cyan]")
    elif summary["missing"]:
        rprint("\n[yellow]No updates found among the items that could be checked.[/yellow]")
    else:
        rprint("\n[green]✓ All installed items are up to date.[/green]")

    if summary["current"]:
        rprint(f"[dim]{summary['current']} item(s) up to date.[/dim]")
    if summary["missing"]:
        rprint(f"[yellow]{summary['missing']} item(s) no longer exist in the active registry.[/yellow]")
    if summary["unknown"]:
        rprint(
            f"[yellow]{summary['unknown']} item(s) have no recorded version; "
            "reinstall them with the commands above to record one.[/yellow]"
        )

    report = payload["report"]
    if report["attempted"] and report["succeeded"]:
        rprint(
            f"[dim]Inbox report accepted: {report['created']} added, {report['superseded']} superseded. "
            "View with `observal inbox --kind update_available`.[/dim]"
        )
    elif report["attempted"]:
        category = report["error"]["category"] if report["error"] else "unexpected"
        rprint(f"[yellow]Inbox reporting failed ({esc(category)}); the version comparison still completed.[/yellow]")


def _version_newer(latest: str, current: str) -> bool:
    """Return whether the registry version is newer using the installed version parser."""
    return Version(latest) > Version(current)
