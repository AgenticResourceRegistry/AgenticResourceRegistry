# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Hook registry CLI commands."""

from __future__ import annotations

import json as _json
import os
import tempfile
from contextlib import nullcontext, redirect_stdout
from io import StringIO
from pathlib import Path

import typer
from packaging.version import InvalidVersion, Version
from rich import print as rprint
from rich.table import Table

from observal_cli import client, config
from observal_cli.cmd_fork import fork_detail_rows
from observal_cli.constants import (
    HARNESS_CAPABILITIES,
    VALID_HARNESSES,
    VALID_HOOK_EVENTS,
    VALID_HOOK_EXECUTION_MODES,
    VALID_HOOK_HANDLER_TYPES,
    VALID_HOOK_SCOPES,
)
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

hook_app = typer.Typer(
    help=(
        "Hook registry commands\n\n"
        "Examples:\n"
        "  observal registry hook list\n"
        "  observal registry hook show alice/my-hook\n"
        "  observal registry hook install alice/my-hook --harness claude-code"
    )
)


def register_hook(app: typer.Typer):
    app.add_typer(hook_app, name="hook")


# ── Timeout caps (client-side fail-fast) ──────────────────────────────────

HOOK_TIMEOUT_CAPS: dict[str, int] = {
    "blocking": 30,
    "sync": 10,
    "async": 60,
}


def _validate_timeout(execution_mode: str, handler_config: dict) -> None:
    """Fail-fast timeout validation before sending to server."""
    timeout = handler_config.get("timeout")
    if timeout is None:
        return
    cap = HOOK_TIMEOUT_CAPS.get(execution_mode)
    try:
        seconds = int(timeout)
    except (TypeError, ValueError) as error:
        fail(
            ErrorCategory.VALIDATION,
            "The hook timeout must be an integer.",
            operation="Validate hook",
            resource="timeout",
            remediation="Provide an integer timeout and retry.",
            detail=repr(error),
        )
    if cap and seconds > cap:
        fail(
            ErrorCategory.VALIDATION,
            f"Timeout {seconds}s exceeds the {cap}s maximum for {execution_mode} hooks.",
            operation="Validate hook",
            resource="timeout",
            remediation="Reduce the timeout or choose a compatible execution mode.",
        )


@hook_app.command(name="submit")
def hook_submit(
    from_file: str | None = typer.Option(None, "--from-file", "-f", help="Create from JSON file"),
    draft: bool = typer.Option(False, "--draft", help="Save as draft instead of submitting for review"),
    submit_draft: str | None = typer.Option(None, "--submit", help="Submit a draft for review (hook ID)"),
    script: str | None = typer.Option(None, "--script", help="Path to hook script file (content stored in registry)"),
    source_url: str | None = typer.Option(None, "--source-url", help="Git repo containing hook scripts"),
    source_ref: str | None = typer.Option(None, "--source-ref", help="Branch/tag to track (default: main)"),
    source_path: str | None = typer.Option(None, "--source-path", help="Directory within repo containing hook files"),
    requires: list[str] | None = typer.Option(None, "--requires", help="Install prerequisites (repeatable)"),
    name: str | None = typer.Option(None, "--name", "-n", help="Hook name"),
    version: str | None = typer.Option(None, "--version", "-v", help="Version (default: 1.0.0)"),
    description: str | None = typer.Option(None, "--description", "-d", help="Short description"),
    event: str | None = typer.Option(None, "--event", "-e", help="Hook event"),
    handler_type: str | None = typer.Option(None, "--handler-type", help="command or http"),
    handler_command: str | None = typer.Option(None, "--handler-command", help="Command handler"),
    handler_url: str | None = typer.Option(None, "--handler-url", help="HTTP handler URL"),
    timeout: int | None = typer.Option(None, "--timeout", help="Timeout seconds"),
    execution_mode: str | None = typer.Option(None, "--execution-mode", help="async, sync, or blocking"),
    scope: str | None = typer.Option(None, "--scope", help="agent, session, or global"),
    supported_harnesses: list[str] | None = typer.Option(None, "--harness", help="Supported harness (repeatable)"),
    team: str | None = typer.Option(None, "--team", help="Teamspace UUID or handle"),
    visibility: str | None = typer.Option(None, "--visibility", help="Visibility: public or team"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Submit a new hook for review.

    Only submit hooks you created or are the point-of-contact for.

    Examples:
      observal registry hook submit --script ./protect-files.sh
      observal registry hook submit --source-url https://github.com/org/hooks --source-path hooks/guard/
      observal registry hook submit --from-file hook.json --output json
    """
    human_output = output != "json"
    if human_output:
        rprint("[dim]Note: Only submit components you created or represent.[/dim]")
    if draft and submit_draft:
        fail(
            ErrorCategory.VALIDATION,
            "Draft creation and draft submission cannot be requested together.",
            operation="Submit hook",
            resource="submit options",
            remediation="Choose either draft creation or draft submission and retry.",
        )
    if submit_draft:
        resolved = client.resolve_registry_reference("hook", submit_draft)
        submit_context = nullcontext() if output == "json" else spinner("Submitting draft for review...")
        with submit_context:
            result = client.post(f"/api/v1/hooks/{resolved}/submit")
        if output == "json":
            output_json(result)
        else:
            rprint(f"[green]✓ Draft submitted for review![/green] ID: [bold]{esc(result['id'])}[/bold]")
        return

    if from_file:
        payload = load_json_object(from_file, operation="Submit hook", noun="hook submission file")
        if not payload.get("owner"):
            payload["owner"] = config.load().get("username", "")
    else:
        # Read script content if --script provided
        script_content: str | None = None
        script_filename: str | None = None
        if script:
            script_path = Path(script)
            if not script_path.is_file():
                fail(
                    ErrorCategory.NOT_FOUND,
                    "The hook script file was not found.",
                    operation="Submit hook",
                    resource=script,
                    remediation="Provide an existing script file and retry.",
                )
            script_content = script_path.read_text()
            script_filename = script_path.name

        flag_mode = any(
            x is not None
            for x in (
                name,
                version,
                description,
                event,
                handler_type,
                handler_command,
                handler_url,
                timeout,
                execution_mode,
                scope,
                supported_harnesses,
            )
        )
        if output == "json" and not flag_mode:
            fail(
                ErrorCategory.VALIDATION,
                "JSON mode requires explicit hook fields.",
                operation="Submit hook",
                resource="submit options",
                remediation="Provide the hook name, description, event, handler, and execution settings.",
            )
        if flag_mode:
            _handler_type = handler_type or ("http" if handler_url else "command")
            _execution_mode = execution_mode or "async"
            _scope = scope or "agent"
            for value, choices, label in (
                (event, VALID_HOOK_EVENTS, "event"),
                (_handler_type, VALID_HOOK_HANDLER_TYPES, "handler type"),
                (_execution_mode, VALID_HOOK_EXECUTION_MODES, "execution mode"),
                (_scope, VALID_HOOK_SCOPES, "scope"),
            ):
                if value and value not in choices:
                    fail(
                        ErrorCategory.VALIDATION,
                        f"Unknown hook {label}: {value}.",
                        operation="Submit hook",
                        resource=label,
                        remediation=f"Choose one of: {', '.join(choices)}.",
                    )
            bad_harnesses = [h for h in supported_harnesses or [] if h not in VALID_HARNESSES]
            if bad_harnesses:
                fail(
                    ErrorCategory.VALIDATION,
                    f"Unknown harness: {bad_harnesses[0]}.",
                    operation="Submit hook",
                    resource="supported harnesses",
                    remediation=f"Choose from: {', '.join(VALID_HARNESSES)}.",
                )
            if not (name and description and event):
                fail(
                    ErrorCategory.VALIDATION,
                    "Hook name, description, and event are required without prompts.",
                    operation="Submit hook",
                    resource="hook payload",
                    remediation="Provide the required fields and retry.",
                )
            if _handler_type == "http":
                if not handler_url:
                    fail(
                        ErrorCategory.VALIDATION,
                        "An HTTP handler URL is required for an HTTP hook.",
                        operation="Submit hook",
                        resource="handler URL",
                        remediation="Provide a handler URL and retry.",
                    )
                handler_config = {"url": handler_url, "timeout": timeout or 10}
            else:
                command = handler_command or script_filename
                if not command:
                    fail(
                        ErrorCategory.VALIDATION,
                        "A handler command or script is required for a command hook.",
                        operation="Submit hook",
                        resource="handler command",
                        remediation="Provide a handler command or script and retry.",
                    )
                handler_config = {"command": command, "timeout": timeout or 10}
            _validate_timeout(_execution_mode, handler_config)
            payload: dict = {
                "name": name,
                "version": version or "1.0.0",
                "description": description,
                "owner": config.load().get("username", ""),
                "event": event,
                "handler_type": _handler_type,
                "handler_config": handler_config,
                "execution_mode": _execution_mode,
                "scope": _scope,
                "supported_harnesses": supported_harnesses or [],
            }
        else:
            # Prompt for essential fields
            name = text_input("Hook name")
            version = text_input("Version", default="1.0.0")
            description = text_input("Description")
            owner = config.load().get("username", "")
            event = select_one("Event", VALID_HOOK_EVENTS)
            handler_type = select_one("Handler type", VALID_HOOK_HANDLER_TYPES)

            # Build handler_config
            if script_filename and handler_type == "command":
                # Auto-populate command from script filename
                timeout = text_input("Timeout (seconds)", default="10")
                handler_config = {"command": script_filename, "timeout": timeout}
                rprint(f"[dim]Command auto-set to '{script_filename}' from --script[/dim]")
            elif handler_type == "command":
                command = text_input("Command")
                timeout = text_input("Timeout (seconds)", default="10")
                handler_config = {"command": command, "timeout": timeout}
            else:
                # HTTP handler
                url = text_input("Hook URL")
                timeout = text_input("Timeout (seconds)", default="10")
                handler_config = {"url": url, "timeout": timeout}

            execution_mode = select_one("Execution mode", VALID_HOOK_EXECUTION_MODES)

            # Validate timeout before sending
            _validate_timeout(execution_mode, handler_config)
            handler_config["timeout"] = int(handler_config["timeout"])

            payload = {
                "name": name,
                "version": version,
                "description": description,
                "owner": owner,
                "event": event,
                "handler_type": handler_type,
                "handler_config": handler_config,
                "execution_mode": execution_mode,
            }

        # Add optional script/source fields
        if script_content:
            payload["script_content"] = script_content
            payload["script_filename"] = script_filename
        if source_url:
            payload["source_url"] = source_url
            payload["source_ref"] = source_ref or "main"
        if source_path:
            payload["source_path"] = source_path
        if requires:
            payload["requirements"] = requires

    try:
        Version(str(payload.get("version") or ""))
    except InvalidVersion as error:
        fail(
            ErrorCategory.VALIDATION,
            "The hook version is invalid.",
            operation="Submit hook",
            resource=str(payload.get("version") or ""),
            remediation="Provide a valid version and retry.",
            detail=repr(error),
        )
    client.add_publish_target(payload, team, visibility)
    submit_context = nullcontext() if output == "json" else spinner("Saving hook...")
    with submit_context:
        endpoint = "/api/v1/hooks/draft" if draft else "/api/v1/hooks/submit"
        result = client.post(endpoint, payload)
    if output == "json":
        output_json(result)
        return
    message = "Draft saved" if draft else "Hook submitted"
    rprint(f"[green]✓ {message}![/green] ID: [bold]{esc(result['id'])}[/bold]")
    rprint(f"  Install: [cyan]observal registry hook install {esc(client.canonical_name(result))}[/cyan]")


@hook_app.command(name="list")
def hook_list(
    event: str | None = typer.Option(None, "--event", "-e", help="Filter by event type"),
    search: str | None = typer.Option(None, "--search", "-s"),
    namespace: str | None = typer.Option(None, "--namespace", help="Filter by user or team namespace"),
    team: str | None = typer.Option(None, "--team", help="Only items owned by this teamspace"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """List approved hooks from the registry.

    Returns all hooks visible to you (approved, plus your own pending/draft
    submissions). Supports filtering by event type and free-text search.

    \b
    Examples:
      observal registry hook list
      observal registry hook list --event Stop
      observal registry hook list --search guard --output json
    """
    if event and event not in VALID_HOOK_EVENTS:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown hook event: {event}.",
            operation="List hooks",
            resource="event filter",
            remediation=f"Choose one of: {', '.join(VALID_HOOK_EVENTS)}.",
        )
    params = {}
    if event:
        params["event"] = event
    if search:
        params["search"] = search
    if namespace:
        params["namespace"] = namespace.lstrip("@").lower()
    if team:
        params["team_id"] = client.resolve_team_id(team)
    fetch_ctx = nullcontext() if output == "json" else spinner("Fetching hooks...")
    with fetch_ctx:
        data = client.get("/api/v1/hooks", params=params)
    if not data:
        config.save_last_results([], "hook")
        if output == "json":
            output_json([])
        else:
            rprint("[dim]No hooks found.[/dim]")
        return
    config.save_last_results(data, "hook")
    if output == "json":
        output_json(data)
        return
    table = Table(title=f"Hooks ({len(data)})", show_lines=False, padding=(0, 1))
    table.add_column("#", style="dim", width=3)
    table.add_column("Name", style="bold cyan", no_wrap=True)
    table.add_column("Event", style="magenta")
    table.add_column("Mode", style="green")
    table.add_column("Namespace", style="dim")
    table.add_column("Status")
    table.add_column("ID", style="dim", max_width=12)
    for i, item in enumerate(data, 1):
        table.add_row(
            str(i),
            esc(display_name(item)),
            esc(item.get("event", "")),
            esc(item.get("execution_mode", "")),
            esc(handle(item)),
            status_badge(item.get("status", "")),
            esc(str(item["id"])[:8] + "…"),
        )
    console.print(table)


@hook_app.command(name="show")
def hook_show(
    hook_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Show detailed information for a single hook.

    Displays event type, handler config, execution mode, owner, status,
    and any associated script or source URL. Accepts ID, name, row number
    from a previous list, or an @alias.

    \b
    Examples:
      observal registry hook show my-hook
      observal registry hook show @guard
      observal registry hook show abc123 -o json
    """
    resolved = client.resolve_registry_reference("hook", hook_id)
    fetch_ctx = nullcontext() if output == "json" else spinner()
    with fetch_ctx:
        item = client.get(f"/api/v1/hooks/{resolved}")
    if output == "json":
        output_json(item)
        return
    rows = [
        ("Status", listing_status(item)),
        ("Event", esc(item.get("event", "N/A"))),
        ("Handler Type", esc(item.get("handler_type", "N/A"))),
        ("Handler Config", esc(_json.dumps(item.get("handler_config", {}), indent=2))),
        ("Execution Mode", esc(item.get("execution_mode", "N/A"))),
        ("Scope", esc(item.get("scope", "N/A"))),
        ("Namespace", esc(handle(item) or "N/A")),
        ("Description", esc(item.get("description", ""))),
        ("Created", esc(relative_time(item.get("created_at")))),
        *fork_detail_rows(item),
        ("ID", f"[dim]{esc(item['id'])}[/dim]"),
    ]
    if item.get("script_filename"):
        rows.insert(5, ("Script", esc(item["script_filename"])))
    if item.get("source_url"):
        rows.insert(5, ("Source", esc(f"{item['source_url']}@{item.get('source_ref', 'main')}")))
    if item.get("requirements"):
        rows.insert(5, ("Requires", esc(", ".join(item["requirements"]))))
    console.print(
        kv_panel(
            f"{esc(display_name(item))} v{esc(item.get('version', '?'))}",
            rows,
            border_style="magenta",
        )
    )


def _atomic_write_text(path: Path, content: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = path.stat().st_mode if path.exists() else None
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(content)
        if executable:
            os.chmod(temp_path, (existing_mode or 0o644) | 0o111)
        elif existing_mode is not None:
            os.chmod(temp_path, existing_mode)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


@hook_app.command(name="install")
def hook_install(
    hook_id: str = typer.Argument(..., help="Hook ID, name, row number, or @alias"),
    harness: str = typer.Option(..., "--harness", "-i", help="Target harness"),
    platform: str = typer.Option("", "--platform", "-p", help="Platform (win32, darwin, linux)"),
    raw: bool = typer.Option(False, "--raw", help="Output raw JSON only (no file writes)"),
    directory: str | None = typer.Option(None, "--dir", "-d", help="Project directory for file writes"),
    version: str | None = typer.Option(
        None, "--version", "-V", help="Install a specific version (e.g. '1.2.0'). Defaults to latest."
    ),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Install a hook for a specific harness.

    Writes script files and merges hook config into the harness's settings.
    Use --raw to just output JSON without writing anything. The hook
    config is merged into the harness's hooks file (existing hooks are preserved).

    \b
    Examples:
      observal registry hook install my-hook --harness claude-code
      observal registry hook install @guard --harness kiro --dir ./project --version 1.2.0
      observal registry hook install my-hook --harness cursor --raw
    """
    if raw and output == "json":
        fail(
            ErrorCategory.VALIDATION,
            "Raw config output and JSON operation output cannot be combined.",
            operation="Install hook",
            resource="output options",
            remediation="Choose either raw config output or JSON operation output.",
        )
    if harness not in VALID_HARNESSES or "hooks" not in HARNESS_CAPABILITIES.get(harness, set()):
        fail(
            ErrorCategory.VALIDATION,
            f"Harness {harness} does not support hooks.",
            operation="Install hook",
            resource="harness",
            remediation="Choose a harness with hook support.",
        )
    if platform and platform not in {"win32", "darwin", "linux"}:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown platform: {platform}.",
            operation="Install hook",
            resource="platform",
            remediation="Choose win32, darwin, or linux.",
        )
    if version:
        try:
            Version(version)
        except InvalidVersion as error:
            fail(
                ErrorCategory.VALIDATION,
                "The requested hook version is invalid.",
                operation="Install hook",
                resource=version,
                remediation="Provide a valid version and retry.",
                detail=repr(error),
            )
    machine_output = raw or output == "json"
    resolved = client.resolve_registry_reference("hook", hook_id)
    listing = client.get(f"/api/v1/hooks/{resolved}")
    project_dir = Path(directory) if directory else Path.cwd()
    from observal_cli.lockfile import local_registry_name

    local_name = local_registry_name(
        harness,
        "hook",
        listing["namespace"],
        listing["slug"],
        scope="project",
        directory=str(project_dir.resolve()),
    )
    install_context = nullcontext() if machine_output else spinner(f"Generating {harness} config...")
    with install_context:
        install_body = {"harness": harness, "platform": platform, "local_name": local_name}
        if version:
            install_body["version"] = version
        result = client.post_public(f"/api/v1/hooks/{resolved}/install", install_body)

    config_snippet = result.get("config_snippet", {})
    files = result.get("files", [])
    requirements = result.get("requirements", [])
    notes = result.get("notes", [])
    warnings = result.get("warnings", [])
    config_path = result.get("config_path", "")

    if raw:
        print(_json.dumps(result, indent=2))
        return

    project_root = project_dir.resolve()
    file_writes: list[tuple[Path, str, bool]] = []
    for file_entry in files:
        if not isinstance(file_entry, dict) or not isinstance(file_entry.get("path"), str):
            fail(
                ErrorCategory.UNAVAILABLE,
                "The registry returned an invalid hook file entry.",
                operation="Install hook",
                resource=hook_id,
                remediation="Check server health and version compatibility, then retry.",
            )
        file_path = (project_root / file_entry["path"]).resolve()
        if not file_path.is_relative_to(project_root):
            fail(
                ErrorCategory.VALIDATION,
                "The hook contains a file path outside the project directory.",
                operation="Install hook",
                resource=file_entry["path"],
                remediation="Correct the hook package path and retry.",
            )
        file_writes.append((file_path, str(file_entry.get("content") or ""), bool(file_entry.get("executable"))))

    cfg_file: Path | None = None
    config_content: str | None = None
    if config_path and config_snippet:
        if not isinstance(config_snippet, dict):
            fail(
                ErrorCategory.UNAVAILABLE,
                "The registry returned an invalid hook configuration.",
                operation="Install hook",
                resource=hook_id,
                remediation="Check server health and version compatibility, then retry.",
            )
        if config_path.startswith("~/"):
            cfg_file = Path(config_path).expanduser().resolve()
            if not cfg_file.is_relative_to(Path.home().resolve()):
                fail(
                    ErrorCategory.VALIDATION,
                    "The hook configuration path is outside the user home directory.",
                    operation="Install hook",
                    resource=config_path,
                    remediation="Correct the hook configuration path and retry.",
                )
        else:
            cfg_file = (project_root / config_path).resolve()
            if not cfg_file.is_relative_to(project_root):
                fail(
                    ErrorCategory.VALIDATION,
                    "The hook configuration path is outside the project directory.",
                    operation="Install hook",
                    resource=config_path,
                    remediation="Correct the hook configuration path and retry.",
                )
        if cfg_file.exists():
            try:
                existing = _json.loads(cfg_file.read_text())
            except _json.JSONDecodeError as error:
                fail(
                    ErrorCategory.VALIDATION,
                    "The existing hook configuration is not valid JSON.",
                    operation="Install hook",
                    resource=str(cfg_file),
                    remediation="Repair the existing configuration and retry.",
                    detail=repr(error),
                )
            if not isinstance(existing, dict):
                fail(
                    ErrorCategory.VALIDATION,
                    "The existing hook configuration must contain a JSON object.",
                    operation="Install hook",
                    resource=str(cfg_file),
                    remediation="Repair the existing configuration and retry.",
                )
        else:
            existing = {}
        incoming_hooks = config_snippet.get("hooks", {})
        existing_hooks = existing.setdefault("hooks", {})
        if not isinstance(incoming_hooks, dict) or not isinstance(existing_hooks, dict):
            fail(
                ErrorCategory.VALIDATION,
                "The hook configuration has an invalid hooks object.",
                operation="Install hook",
                resource=str(cfg_file),
                remediation="Repair the hook configuration and retry.",
            )
        for event_name, entries in incoming_hooks.items():
            current = existing_hooks.setdefault(event_name, [])
            if not isinstance(current, list) or not isinstance(entries, list):
                fail(
                    ErrorCategory.VALIDATION,
                    "The hook configuration event entries must be arrays.",
                    operation="Install hook",
                    resource=str(cfg_file),
                    remediation="Repair the hook configuration and retry.",
                )
            current.extend(entry for entry in entries if entry not in current)
        if "version" in config_snippet:
            existing["version"] = config_snippet["version"]
        config_content = _json.dumps(existing, indent=2) + "\n"

    write_context = redirect_stdout(StringIO()) if output == "json" else nullcontext()
    with write_context:
        for file_path, content, executable in file_writes:
            _atomic_write_text(file_path, content, executable=executable)
            rprint(f"  [green]✓[/green] Wrote {esc(file_path.relative_to(project_root))}")
        if cfg_file and config_content is not None:
            _atomic_write_text(cfg_file, config_content)
            rprint(f"  [green]✓[/green] Updated {esc(cfg_file)}")

    from observal_cli.lockfile import upsert_standalone

    try:
        upsert_standalone(
            harness,
            component_type="hook",
            name=listing.get("name", resolved),
            component_id=str(listing.get("id", resolved)),
            version=result.get("version") or listing.get("version"),
            scope="project",
            version_id=str(result["version_id"]) if result.get("version_id") else None,
            digest=result.get("digest"),
            requested_version=version,
            directory=str(project_root),
            namespace=listing.get("namespace"),
            slug=listing.get("slug"),
            local_name=local_name,
        )
    except PermissionError as error:
        fail(
            ErrorCategory.PERMISSION,
            "The hook was written but its installed state could not be recorded.",
            operation="Install hook",
            resource="installed-state lockfile",
            remediation="Check lockfile ownership and permissions, then retry.",
            detail=repr(error),
        )
    except (OSError, RuntimeError) as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            "The hook was written but its installed state could not be recorded.",
            operation="Install hook",
            resource="installed-state lockfile",
            remediation="Check local storage and retry.",
            detail=repr(error),
        )

    if output == "json":
        output_json(
            {
                **result,
                "files_written": [str(path) for path, _content, _executable in file_writes],
                "config_path": str(cfg_file) if cfg_file else None,
            }
        )
        return

    for warning in warnings:
        rprint(f"\n[yellow]Warning:[/yellow] {esc(warning)}")
    if requirements:
        rprint("\n[yellow]⚠ Prerequisites required:[/yellow]")
        for requirement in requirements:
            rprint(f"  [dim]$[/dim] {esc(requirement)}")
    for note in notes:
        rprint(f"[dim]i {esc(note)}[/dim]")
    rprint(f"\n[green]✓ Hook installed for {esc(harness)}![/green]")


@hook_app.command(name="edit")
def hook_edit(
    hook_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    from_file: str | None = typer.Option(None, "--from-file", "-f", help="Load updates from JSON file"),
    name: str | None = typer.Option(None, "--name", "-n", help="New listing name"),
    description: str | None = typer.Option(None, "--description", "-d", help="New description"),
    version: str | None = typer.Option(None, "--version", "-v", help="New version string"),
    event: str | None = typer.Option(None, "--event", "-e", help="New event type"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Edit a draft, rejected, or pending hook submission.

    Acquires an edit lock, applies changes, then releases the lock.
    You can update individual fields with flags or supply a full JSON
    file with --from-file. Only the hook owner can edit.

    \b
    Examples:
      observal registry hook edit my-hook --description "Updated guard hook"
      observal registry hook edit my-hook --event Stop --version 1.1.0
      observal registry hook edit @guard --from-file updated-hook.json --output json
    """
    resolved = client.resolve_registry_reference("hook", hook_id)
    if from_file:
        updates = load_json_object(from_file, operation="Edit hook", noun="hook update file")
    else:
        updates = {}
        if name is not None:
            updates["name"] = name
        if description is not None:
            updates["description"] = description
        if version is not None:
            updates["version"] = version
        if event is not None:
            updates["event"] = event

    if not updates:
        fail(
            ErrorCategory.VALIDATION,
            "No hook changes were provided.",
            operation="Edit hook",
            resource=hook_id,
            remediation="Provide an update file or one or more field options.",
        )
    updated_event = updates.get("event")
    if updated_event is not None and updated_event not in VALID_HOOK_EVENTS:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown hook event: {updated_event}.",
            operation="Edit hook",
            resource="event",
            remediation=f"Choose one of: {', '.join(VALID_HOOK_EVENTS)}.",
        )
    updated_version = updates.get("version")
    if updated_version is not None:
        try:
            Version(str(updated_version))
        except InvalidVersion as error:
            fail(
                ErrorCategory.VALIDATION,
                "The hook version is invalid.",
                operation="Edit hook",
                resource=str(updated_version),
                remediation="Provide a valid version and retry.",
                detail=repr(error),
            )

    client.post(f"/api/v1/hooks/{resolved}/start-edit")
    save_context = nullcontext() if output == "json" else spinner("Saving changes...")
    with save_context:
        result = client.put(f"/api/v1/hooks/{resolved}/draft", updates)
    if output == "json":
        output_json(result)
    else:
        rprint(f"[green]✓ Updated {esc(result['name'])}[/green] (status: {esc(result.get('status', 'unknown'))})")
