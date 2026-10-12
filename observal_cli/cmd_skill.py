# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Skill registry CLI commands."""

from __future__ import annotations

import hashlib
import json as _json
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager, nullcontext, redirect_stdout
from io import StringIO
from pathlib import Path

import typer
from packaging.version import InvalidVersion, Version
from rich import print as rprint
from rich.table import Table

from observal_cli import client, config
from observal_cli.cmd_fork import fork_detail_rows
from observal_cli.constants import HARNESS_CAPABILITIES, VALID_HARNESSES, VALID_SKILL_TASK_TYPES
from observal_cli.errors import CliError, ErrorCategory, fail
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
from observal_cli.shared.utils import sanitize_name as _sanitize_name
from observal_cli.skill_folder import (
    SUPPORTED_FEATURE as SKILL_FOLDER_FEATURE,
)
from observal_cli.skill_folder import (
    BundleValidationError,
    DirectoryCaptureError,
    capture_directory,
    snapshot_to_extra_files,
    validate_bundle,
)

skill_app = typer.Typer(
    help=(
        "Skill registry commands\n\n"
        "Examples:\n"
        "  observal registry skill list\n"
        "  observal registry skill show alice/my-skill\n"
        "  observal registry skill install alice/my-skill --harness claude-code"
    )
)


def register_skill(app: typer.Typer):
    app.add_typer(skill_app, name="skill")


backups_app = typer.Typer(
    help=(
        "List, restore or explicitly prune verified skill folder backups.\n\n"
        "Examples:\n  observal registry skill backups list"
    )
)
skill_app.add_typer(backups_app, name="backups")


@backups_app.command("list")
def skill_backups_list(
    backup_root: Path | None = typer.Option(None, "--backup-root"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """List retained verified backups and their ages.

    Examples:
        observal registry skill backups list
    """
    from observal_cli.managed_skill import backups_list

    items = backups_list(backup_root=backup_root)
    if output == "json":
        output_json(items)
    else:
        for item in items:
            rprint(f"{esc(item['id'])}  {esc(item['target'])}  {item['size']:,} bytes  {item['age_seconds']}s old")
        if sum(item["size"] for item in items) > 100 * 1024 * 1024:
            rprint("[yellow]Retained backups exceed 100 MiB; review before explicit pruning.[/yellow]")


@backups_app.command("restore")
def skill_backups_restore(
    backup_id: str,
    backup_root: Path | None = typer.Option(None, "--backup-root"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Restore only a byte-verified unmodified active installation.

    Examples:
        observal registry skill backups restore BACKUP_ID
    """
    from observal_cli.managed_skill import ManagedSkillError, restore_backup

    try:
        result = restore_backup(backup_id, backup_root=backup_root)
    except (ManagedSkillError, OSError) as exc:
        fail(
            ErrorCategory.CONFLICT,
            str(exc),
            operation="Restore skill backup",
            resource=backup_id,
            remediation=getattr(exc, "remediation", None)
            or "Inspect the active folder and retained backup before retrying.",
        )
    if output == "json":
        output_json(result)
    else:
        rprint(f"[green]Restored backup {esc(backup_id)}[/green]")


@backups_app.command("prune")
def skill_backups_prune(
    backup_id: str,
    backup_root: Path | None = typer.Option(None, "--backup-root"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Explicitly remove one old backup after verifying its active successor.

    Examples:
        observal registry skill backups prune BACKUP_ID
    """
    from observal_cli.managed_skill import ManagedSkillError, restore_backup

    try:
        result = restore_backup(backup_id, backup_root=backup_root, prune=True)
    except (ManagedSkillError, OSError) as exc:
        fail(
            ErrorCategory.CONFLICT,
            str(exc),
            operation="Prune skill backup",
            resource=backup_id,
            remediation="Verify the active folder and receipt before pruning.",
        )
    if output == "json":
        output_json(result)
    else:
        rprint(f"[green]Pruned backup {esc(backup_id)}[/green]")


# ── Security helpers (port of vercel-labs installer.ts) ─────────────────────


def _is_path_safe(path: Path, base: Path) -> bool:
    """Return True only if resolved *path* is inside *base* (no traversal)."""
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


# ── Frontmatter parser (mirrors vercel-labs parseFrontmatter) ───────────────

_FM_RE = re.compile(r"^---\r?\n(.*?)\r?\n---", re.DOTALL)


def _parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from markdown.  Uses yaml.safe_load (no eval)."""
    try:
        import yaml  # only needed locally; server-side already does this
    except ImportError:
        return {}
    m = _FM_RE.match(content)
    if not m:
        return {}
    try:
        result = yaml.safe_load(m.group(1))
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def _validate_skill_fields(payload: dict, operation: str) -> None:
    task_type = payload.get("task_type")
    if task_type is not None and task_type not in VALID_SKILL_TASK_TYPES:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown skill task type: {task_type}.",
            operation=operation,
            resource="task type",
            remediation=f"Choose one of: {', '.join(VALID_SKILL_TASK_TYPES)}.",
        )
    harnesses = payload.get("supported_harnesses")
    if harnesses is not None:
        bad_harnesses = (
            [item for item in harnesses if item not in VALID_HARNESSES]
            if isinstance(harnesses, list)
            else [str(harnesses)]
        )
        if bad_harnesses:
            fail(
                ErrorCategory.VALIDATION,
                f"Unknown harness: {bad_harnesses[0]}.",
                operation=operation,
                resource="supported harnesses",
                remediation=f"Choose from: {', '.join(VALID_HARNESSES)}.",
            )
    version = payload.get("version")
    if version is not None:
        try:
            Version(str(version))
        except InvalidVersion as error:
            fail(
                ErrorCategory.VALIDATION,
                "The skill version is invalid.",
                operation=operation,
                resource=str(version),
                remediation="Provide a valid version and retry.",
                detail=repr(error),
            )


# ── Submit ────────────────────────────────────────────────────────────────────


@skill_app.command(name="submit")
def skill_submit(
    from_file: str | None = typer.Option(None, "--from-file", "-f", help="Create from JSON file"),
    from_dir: str | None = typer.Option(None, "--from-dir", help="Create from directory (must contain SKILL.md)"),
    skill_md: str | None = typer.Option(None, "--skill-md", help="Path to SKILL.md to paste (auto-fills fields)"),
    git_url: str | None = typer.Option(None, "--git-url", help="Git repository URL"),
    git_ref: str | None = typer.Option(None, "--git-ref", help="Branch or tag (default: main)"),
    script: str | None = typer.Option(None, "--script", help="Path to script file (registry_direct mode)"),
    exclude: list[str] | None = typer.Option(None, "--exclude", help="Paths to exclude from --from-dir (repeatable)"),
    allow_excluded: bool = typer.Option(False, "--allow-excluded", help="Acknowledge paths excluded from --from-dir"),
    allow_sensitive: bool = typer.Option(
        False, "--allow-sensitive", help="Acknowledge likely-sensitive files in --from-dir"
    ),
    delivery_mode: str | None = typer.Option(None, "--delivery-mode", help="Delivery: git_fetch or registry_direct"),
    name: str | None = typer.Option(None, "--name", "-n", help="Skill name"),
    version: str | None = typer.Option(None, "--version", "-v", help="Version (default: 1.0.0)"),
    description: str | None = typer.Option(None, "--description", "-d", help="Short description"),
    task_type: str | None = typer.Option(None, "--task-type", "-t", help="Task type"),
    target_agent: list[str] | None = typer.Option(None, "--target-agent", help="Target agent (repeatable)"),
    skill_path: str | None = typer.Option(None, "--skill-path", help="Skill path in repo"),
    slash_command: str | None = typer.Option(None, "--slash-command", help="Slash command name"),
    supported_harnesses: list[str] | None = typer.Option(None, "--harness", help="Supported harness (repeatable)"),
    draft: bool = typer.Option(False, "--draft", help="Save as draft instead of submitting for review"),
    submit_draft: str | None = typer.Option(None, "--submit", help="Submit a draft for review (skill ID)"),
    version_id: str | None = typer.Option(None, "--version-id", help="Exact saved draft UUID for --submit"),
    team: str | None = typer.Option(None, "--team", help="Teamspace UUID or handle"),
    visibility: str | None = typer.Option(None, "--visibility", help="Visibility: public or team"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Submit a new skill for review.

    Skills provide agents with task-specific SKILL.md instructions. For a
    Git-backed skill, provide --git-url (with optional --git-ref) so the server
    fetches SKILL.md automatically.

    Shortcut: provide --skill-md PATH to paste the SKILL.md content directly
    (fields are auto-filled from frontmatter; --git-url is still required
    for install unless using --delivery-mode registry_direct).

    Registry direct: use --delivery-mode registry_direct with --skill-md and
    optionally --script for historical single-file delivery (no git repo
    needed). For a complete versioned folder, use --from-dir instead.

    Complete folders: use --from-dir PATH to save a versioned folder draft
    containing SKILL.md and additional files (scripts, templates, assets).
    It is not submitted for review until --submit ID --version-id UUID succeeds
    with the current observed revision and delivery setting enabled.
    Use --exclude to skip specific paths (e.g., --exclude .venv).

    Only submit skills you created or are the point-of-contact for.

    Examples:
        observal registry skill submit --git-url https://github.com/org/repo
        observal registry skill submit --skill-md ./SKILL.md --script ./run.sh \
          --delivery-mode registry_direct --name my-skill --description "My skill"
        observal registry skill submit --from-dir ./my-skill --name my-skill --description "My skill"
    """
    human_output = output != "json"
    if human_output:
        rprint("[dim]Note: Only submit components you created or represent.[/dim]")
    if draft and submit_draft:
        fail(
            ErrorCategory.VALIDATION,
            "Draft creation and draft submission cannot be requested together.",
            operation="Submit skill",
            resource="submit options",
            remediation="Choose either draft creation or draft submission and retry.",
        )

    # Validate conflicting options
    if from_dir and (skill_md or script or git_url):
        fail(
            ErrorCategory.VALIDATION,
            "--from-dir cannot be combined with --skill-md, --script, or --git-url.",
            operation="Submit skill",
            resource="submit options",
            remediation="Use --from-dir alone for complete folder submissions.",
        )

    if version_id and not submit_draft:
        fail(
            ErrorCategory.VALIDATION,
            "--version-id requires --submit LISTING.",
            operation="Submit skill",
            resource="submit options",
            remediation="Select an exact saved draft to submit for review.",
        )
    if submit_draft:
        resolved = client.resolve_registry_reference("skill", submit_draft)
        submit_context = nullcontext() if output == "json" else spinner("Submitting draft for review...")
        with submit_context:
            if version_id:
                manifest = client.get(f"/api/v1/skills/{resolved}/versions/{version_id}/manifest")
                if str(manifest.get("version_id")) != version_id or not manifest.get("revision"):
                    fail(
                        ErrorCategory.CONFLICT,
                        "The selected draft manifest changed.",
                        operation="Submit skill",
                        resource=submit_draft,
                        remediation="Refresh the selected version and retry.",
                    )
                result = client.post(
                    f"/api/v1/skills/{resolved}/versions/{version_id}/submit",
                    {"observed_revision": manifest["revision"]},
                )
            else:
                result = client.post(f"/api/v1/skills/{resolved}/submit")
        if output == "json":
            output_json(result)
        else:
            rprint(
                f"[green]✓ Draft submitted for review![/green] ID: [bold]{esc(str(result.get('listing_id') or result.get('id')))}[/bold]"
            )
        return

    if from_file:
        try:
            with open(from_file) as f:
                payload = _json.load(f)
        except _json.JSONDecodeError as error:
            fail(
                ErrorCategory.VALIDATION,
                "The skill submission file is not valid JSON.",
                operation="Submit skill",
                resource=from_file,
                remediation="Correct the JSON and retry.",
                detail=repr(error),
            )
        except FileNotFoundError as error:
            fail(
                ErrorCategory.NOT_FOUND,
                "The skill submission file was not found.",
                operation="Submit skill",
                resource=from_file,
                remediation="Provide an existing JSON file and retry.",
                detail=repr(error),
            )
        if not isinstance(payload, dict):
            fail(
                ErrorCategory.VALIDATION,
                "The skill submission file must contain a JSON object.",
                operation="Submit skill",
                resource=from_file,
                remediation="Replace the file contents with a JSON object and retry.",
            )
        _validate_skill_fields(payload, "Submit skill")
    elif from_dir:
        # --- Complete folder submission ---
        _submit_folder_draft(
            from_dir=from_dir,
            exclude=exclude,
            allow_excluded=allow_excluded,
            allow_sensitive=allow_sensitive,
            name=name,
            version=version,
            description=description,
            task_type=task_type,
            target_agent=target_agent,
            slash_command=slash_command,
            supported_harnesses=supported_harnesses,
            team=team,
            visibility=visibility,
            draft=draft,
            output=output,
        )
        return
    else:
        # --- Paste-first: parse SKILL.md locally if provided ---
        prefill: dict = {}
        skill_md_content: str | None = None
        script_content: str | None = None
        script_filename: str | None = None

        if skill_md:
            try:
                raw = Path(skill_md).read_text(encoding="utf-8")
            except FileNotFoundError as error:
                fail(
                    ErrorCategory.NOT_FOUND,
                    "The SKILL.md file was not found.",
                    operation="Submit skill",
                    resource=skill_md,
                    remediation="Provide an existing SKILL.md file and retry.",
                    detail=repr(error),
                )
            fm = _parse_frontmatter(raw)
            skill_md_content = raw
            prefill["name"] = fm.get("name", "")
            prefill["description"] = fm.get("description", "")
            cmd_field = fm.get("command", "")
            if isinstance(cmd_field, str) and cmd_field.strip():
                prefill["slash_command"] = cmd_field.strip().lstrip("/")
            if fm and human_output:
                rprint(
                    f"[green]✓ Parsed SKILL.md:[/green] name={esc(repr(prefill.get('name')))}  "
                    f"description={esc(repr(str(prefill.get('description', ''))[:60]))}"
                )

        if script:
            script_path_obj = Path(script)
            if not script_path_obj.is_file():
                fail(
                    ErrorCategory.NOT_FOUND,
                    "The skill script file was not found.",
                    operation="Submit skill",
                    resource=script,
                    remediation="Provide an existing script file and retry.",
                )
            script_content = script_path_obj.read_text(encoding="utf-8")
            script_filename = script_path_obj.name
            if human_output:
                rprint(f"[green]✓ Read script:[/green] {esc(script_filename)}")

        # Auto-detect delivery mode
        effective_delivery_mode = delivery_mode or (
            "registry_direct" if (skill_md_content and not git_url) else "git_fetch"
        )

        flag_mode = any(
            x is not None
            for x in (name, version, description, task_type, skill_path, slash_command, supported_harnesses)
        ) or bool(target_agent)
        if output == "json" and not flag_mode:
            fail(
                ErrorCategory.VALIDATION,
                "JSON mode requires explicit skill fields.",
                operation="Submit skill",
                resource="submit options",
                remediation="Provide name, description, task type, and the selected delivery source.",
            )
        if flag_mode:
            _name = name or prefill.get("name", "")
            _description = description or prefill.get("description", "")
            if not _name or not _description:
                fail(
                    ErrorCategory.VALIDATION,
                    "Skill name and description are required without prompts.",
                    operation="Submit skill",
                    resource="skill payload",
                    remediation="Provide both name and description and retry.",
                )
            payload = {
                "name": _name,
                "version": version or "1.0.0",
                "description": _description,
                "owner": config.load().get("username", ""),
                "task_type": task_type or "general",
                "target_agents": target_agent or [],
                "delivery_mode": effective_delivery_mode,
                "supported_harnesses": supported_harnesses or [],
            }
        else:
            agents_input = text_input("Target agents (comma-separated)", default="")
            payload = {
                "name": text_input("Skill name", default=prefill.get("name", "")),
                "version": text_input("Version", default="1.0.0"),
                "description": text_input("Description", default=prefill.get("description", "")),
                "owner": config.load().get("username", ""),
                "task_type": select_one("Task type", VALID_SKILL_TASK_TYPES),
                "target_agents": [a.strip() for a in agents_input.split(",") if a.strip()],
                "delivery_mode": effective_delivery_mode,
            }
        _validate_skill_fields(payload, "Submit skill")
        if effective_delivery_mode == "git_fetch":
            if flag_mode and not git_url:
                fail(
                    ErrorCategory.VALIDATION,
                    "A Git URL is required for git-fetch skills.",
                    operation="Submit skill",
                    resource="Git URL",
                    remediation="Provide a Git URL or choose registry-direct delivery.",
                )
            payload["git_url"] = git_url or text_input("Git URL")
            payload["skill_path"] = skill_path or ("/" if flag_mode else text_input("Skill path in repo", default="/"))
            payload["git_ref"] = git_ref or (
                "main" if flag_mode else text_input("Git ref (branch/tag)", default="main")
            )
        if slash_command or prefill.get("slash_command"):
            payload["slash_command"] = slash_command or prefill["slash_command"]
        if skill_md_content:
            payload["skill_md_content"] = skill_md_content
        if script_content:
            payload["script_content"] = script_content
            payload["script_filename"] = script_filename

    client.add_publish_target(payload, team, visibility)
    endpoint = "/api/v1/skills/draft" if draft else "/api/v1/skills/submit"
    label = "draft" if draft else "skill"
    submit_context = nullcontext() if output == "json" else spinner(f"Saving {label}...")
    with submit_context:
        result = client.post(endpoint, payload)
    if output == "json":
        output_json(result)
        return
    validated = result.get("validated", False)
    validated_tag = "[green]✓ validated[/green]" if validated else "[yellow]unvalidated[/yellow]"
    rprint(f"[green]✓ {label.capitalize()} submitted![/green] ID: [bold]{esc(result['id'])}[/bold]  {validated_tag}")
    rprint(f"  Install: [cyan]observal registry skill install {esc(client.canonical_name(result))}[/cyan]")


# ── List / My ─────────────────────────────────────────────────────────────────


@skill_app.command(name="list")
def skill_list(
    task_type: str | None = typer.Option(None, "--task-type", "-t"),
    target_agent: str | None = typer.Option(None, "--target-agent"),
    harness: str | None = typer.Option(None, "--harness", help="Only skills supporting this harness"),
    search: str | None = typer.Option(None, "--search", "-s"),
    namespace: str | None = typer.Option(None, "--namespace", help="Filter by user or team namespace"),
    team: str | None = typer.Option(None, "--team", help="Only items owned by this teamspace"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """List approved skills in the registry.

    Shows only skills with approved status. Use --task-type, --target-agent,
    or --search to filter results. Row numbers from the output can be used
    as references in subsequent commands.

    Examples:
        observal registry skill list
        observal registry skill list --task-type code-generation
        observal registry skill list --target-agent claude-code --output json
    """
    if task_type and task_type not in VALID_SKILL_TASK_TYPES:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown skill task type: {task_type}.",
            operation="List skills",
            resource="task type filter",
            remediation=f"Choose one of: {', '.join(VALID_SKILL_TASK_TYPES)}.",
        )
    if harness and (harness not in VALID_HARNESSES or "skills" not in HARNESS_CAPABILITIES.get(harness, set())):
        fail(
            ErrorCategory.VALIDATION,
            f"Harness {harness} does not support skills.",
            operation="List skills",
            resource="harness filter",
            remediation="Choose a harness with skill support.",
        )
    params = {}
    if task_type:
        params["task_type"] = task_type
    if target_agent:
        params["target_agent"] = target_agent
    if harness:
        params["harness"] = harness
    if search:
        params["search"] = search
    if namespace:
        params["namespace"] = namespace.lstrip("@").lower()
    if team:
        params["team_id"] = client.resolve_team_id(team)
    fetch_ctx = nullcontext() if output == "json" else spinner("Fetching skills...")
    with fetch_ctx:
        data = client.get("/api/v1/skills", params=params)
    if not data:
        config.save_last_results([], "skill")
        if output == "json":
            output_json([])
        else:
            rprint("[dim]No skills found.[/dim]")
        return
    config.save_last_results(data, "skill")
    if output == "json":
        output_json(data)
        return
    table = Table(title=f"Skills ({len(data)})", show_lines=False, padding=(0, 1))
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


@skill_app.command(name="my")
def skill_my(
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """List your own skills across all statuses.

    Shows drafts, pending, approved, and rejected skills you submitted.
    Useful for tracking the review status of your submissions.

    Examples:
        observal registry skill my
        observal registry skill my --output json
    """
    fetch_ctx = nullcontext() if output == "json" else spinner("Fetching your skills...")
    with fetch_ctx:
        data = client.get("/api/v1/skills/my")
    if not data:
        config.save_last_results([], "skill")
        if output == "json":
            output_json([])
        else:
            rprint("[dim]You have no skills.[/dim]")
        return
    config.save_last_results(data, "skill")
    if output == "json":
        output_json(data)
        return
    table = Table(title=f"My Skills ({len(data)})", show_lines=False, padding=(0, 1))
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


# ── Show ──────────────────────────────────────────────────────────────────────


@skill_app.command(name="show")
def skill_show(
    skill_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    version: str | None = typer.Option(None, "--version", "-V", help="Inspect an exact approved release"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Show detailed information about a skill.

    Displays metadata including validation status, task type, git source,
    slash command, target agents, and timestamps. Accepts a UUID, name,
    row number from a previous list, or @alias. Complete direct releases
    list reviewed paths, sizes and modes, not file contents. Use export for
    complete local inspection.

    Examples:
        observal registry skill show my-skill
        observal registry skill show 1
        observal registry skill show @refactor-skill --version 1.0.0 --output json
    """
    resolved = client.resolve_registry_reference("skill", skill_id)
    fetch_ctx = nullcontext() if output == "json" else spinner()
    with fetch_ctx:
        item = client.get(f"/api/v1/skills/{resolved}")
        manifest = None
        selected = None
        if item.get("delivery_mode") == "registry_direct" and item.get("status") in ("approved", "archived"):
            requested = version or item.get("version")
            if requested:
                page = 1
                while page <= 10:
                    versions = client.get(f"/api/v1/skills/{resolved}/versions", {"page": page, "page_size": 50})
                    selected = next(
                        (entry for entry in versions.get("items", []) if entry.get("version") == requested), None
                    )
                    if selected or page * 50 >= versions.get("total", 0):
                        break
                    page += 1
                unavailable = (
                    not selected or selected.get("status") != "approved" or selected.get("requires_global_review")
                )
                if unavailable and version:
                    fail(
                        ErrorCategory.CONFLICT,
                        "The selected approved skill version is unavailable.",
                        operation="Show skill",
                        resource=skill_id,
                        remediation="Select a visible reviewed release and retry.",
                    )
                if unavailable:
                    selected = None
                else:
                    manifest = client.get(f"/api/v1/skills/{resolved}/versions/{selected['id']}/manifest")
                    if manifest.get("version_id") != selected["id"]:
                        fail(
                            ErrorCategory.CONFLICT,
                            "The returned file manifest belongs to another release.",
                            operation="Show skill",
                            resource=skill_id,
                            remediation="Refresh the exact approved version before inspecting files.",
                        )
    if output == "json":
        output_json({**item, "selected_version": selected, "files": manifest.get("files") if manifest else None})
        return
    console.print(
        kv_panel(
            f"{esc(display_name(item))} v{esc(item.get('version', '?'))}",
            [
                ("Status", listing_status(item)),
                ("Validated", "✓" if item.get("validated") else "✗"),
                ("Task Type", esc(item.get("task_type", "N/A"))),
                ("Delivery Mode", esc(item.get("delivery_mode", "git_fetch"))),
                ("Namespace", esc(handle(item) or "N/A")),
                ("Git URL", esc(item.get("git_url", "N/A"))),
                ("Git Ref", esc(item.get("git_ref") or "N/A")),
                ("Skill Path", esc(item.get("skill_path", "/"))),
                ("Script", esc(item.get("script_filename") or "N/A")),
                ("Slash Command", esc(f"/{item['slash_command']}" if item.get("slash_command") else "N/A")),
                ("Description", esc(item.get("description", ""))),
                ("Target Agents", esc(", ".join(item.get("target_agents", [])) or "N/A")),
                ("Created", esc(relative_time(item.get("created_at")))),
                *fork_detail_rows(item),
                ("ID", f"[dim]{esc(item['id'])}[/dim]"),
            ],
            border_style="green",
        )
    )
    if manifest and selected:
        table = Table(title=f"Reviewed files in v{esc(selected['version'])}")
        table.add_column("Path")
        table.add_column("Bytes", justify="right")
        table.add_column("Mode")
        for file in manifest["files"]:
            table.add_row(esc(file["path"]), str(file["size"]), esc(file["mode"]))
        console.print(table)
        rprint(
            f"[dim]Inspect all bytes: observal registry skill export {esc(str(item['id']))} "
            f"./skill-export --version-id {esc(selected['id'])}[/dim]"
        )


# ── Install ────────────────────────────────────────────────────────────────────


def _normalize_skill_path(skill_path: str | None) -> str:
    clean_path = (skill_path or "/").strip("/")
    if clean_path.casefold() == "skill.md":
        return ""
    if clean_path.casefold().endswith("/skill.md"):
        return clean_path.rsplit("/", 1)[0]
    return clean_path


def _sparse_clone_skill_dir(git_url: str, skill_path: str, git_ref: str, dest: Path) -> bool:
    """Sparse-clone only the skill subdirectory from a remote repo.

    Returns True on success, False if git is unavailable or the clone fails.
    Writes the full skill directory tree to *dest*.
    """
    import shutil

    git_ref = git_ref or "main"

    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False

    clean_path = _normalize_skill_path(skill_path)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _run = lambda cmd, **kw: subprocess.run(  # noqa: E731
                cmd, cwd=tmp_path, check=True, capture_output=True, timeout=30, **kw
            )
            _run(["git", "init"])
            _run(["git", "remote", "add", "origin", git_url])
            _run(["git", "config", "core.sparseCheckout", "true"])
            _run(["git", "fetch", "--filter=blob:none", "--depth=1", "origin", git_ref])
            # Set sparse checkout path
            sparse_file = tmp_path / ".git" / "info" / "sparse-checkout"
            sparse_file.parent.mkdir(parents=True, exist_ok=True)
            sparse_file.write_text(f"{clean_path}/\n" if clean_path else "/\n")
            _run(["git", "checkout", "FETCH_HEAD"])
            # Copy skill directory to dest
            src = tmp_path / clean_path if clean_path else tmp_path
            if not src.exists():
                return False
            shutil.copytree(src, dest, dirs_exist_ok=True)
        return True
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


@skill_app.command(name="install")
def skill_install(
    skill_id: str = typer.Argument(..., help="Skill ID, name, row number, or @alias"),
    harness: str = typer.Option(..., "--harness", "-i", help="Target harness"),
    scope: str = typer.Option("user", "--scope", "-s", help="Install scope: user (global, default) or project"),
    raw: bool = typer.Option(False, "--raw", help="Output raw JSON only"),
    no_write: bool = typer.Option(False, "--no-write", help="Print config without writing files"),
    version: str | None = typer.Option(
        None, "--version", "-V", help="Install a specific version (e.g. '1.0.0'). Defaults to latest."
    ),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
    upgrade: bool = typer.Option(False, "--upgrade", help="Update only a verified managed folder"),
    check_upgrade: bool = typer.Option(
        False, "--check-upgrade", help="Preview a verified folder update without writing"
    ),
    backup_root: Path | None = typer.Option(None, "--backup-root", help="Same-filesystem private ignored backup root"),
):
    """Install an approved skill from Git or a complete reviewed registry folder.

    Git-backed skills use sparse checkout; direct skills with resources use a
    verified versioned folder bundle. Neither path overwrites an existing
    skill directory. Select a skill-capable harness from server configuration.

    Scopes:
      --scope user (default): install for this user's harness.
      --scope project: install relative to the current project directory.

    Examples:
        observal registry skill install alice/review --harness claude-code --version 1.1.0
        observal registry skill install alice/review --harness pi --scope project
        observal registry skill install @sk --harness claude-code --no-write
    """
    if (upgrade or check_upgrade or backup_root) and (raw or no_write or (upgrade and check_upgrade)):
        fail(
            ErrorCategory.VALIDATION,
            "Upgrade flags cannot be combined with raw/no-write or each other.",
            operation="Install skill",
            resource=skill_id,
            remediation="Choose one upgrade action.",
        )
    if raw and output == "json":
        fail(
            ErrorCategory.VALIDATION,
            "Raw config output and JSON operation output cannot be combined.",
            operation="Install skill",
            resource="output options",
            remediation="Choose either raw config output or JSON operation output.",
        )
    if scope not in {"user", "project"}:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown skill scope: {scope}.",
            operation="Install skill",
            resource="scope",
            remediation="Choose user or project.",
        )
    if harness not in VALID_HARNESSES or "skills" not in HARNESS_CAPABILITIES.get(harness, set()):
        fail(
            ErrorCategory.VALIDATION,
            f"Harness {harness} does not support skills.",
            operation="Install skill",
            resource="harness",
            remediation="Choose a harness with skill support.",
        )
    if version:
        try:
            Version(version)
        except InvalidVersion as error:
            fail(
                ErrorCategory.VALIDATION,
                "The requested skill version is invalid.",
                operation="Install skill",
                resource=version,
                remediation="Provide a valid version and retry.",
                detail=repr(error),
            )
    machine_output = raw or output == "json"
    resolved = client.resolve_registry_reference("skill", skill_id)
    listing = client.get(f"/api/v1/skills/{resolved}")
    from observal_cli.lockfile import local_registry_name

    directory = str(Path.cwd()) if scope == "project" else None
    local_name = local_registry_name(
        harness,
        "skill",
        listing["namespace"],
        listing["slug"],
        scope=scope,
        directory=directory,
    )
    # Resolve the selected version before sending a name. Older resource-less
    # skills retain their established aliases; a folder must use SKILL.md name.
    requested_version = version
    selected_version_id = None
    selected_version_name = version or listing.get("version")
    if selected_version_name and (listing.get("delivery_mode") == "registry_direct" or version):
        selected = client.get(f"/api/v1/skills/{resolved}/versions/{selected_version_name}")
        if selected.get("extra_files"):
            declared_name = _parse_frontmatter(selected.get("skill_md_content") or "").get("name")
            if not isinstance(declared_name, str) or not declared_name:
                fail(
                    ErrorCategory.VALIDATION,
                    "Selected complete folder has no declared name.",
                    operation="Install skill",
                    resource=skill_id,
                    remediation="Correct the selected reviewed skill version before installing.",
                )
            local_name = declared_name
            selected_version_id = str(selected["id"])
            version = str(selected["version"])
    install_context = nullcontext() if machine_output else spinner(f"Generating {harness} config...")
    with install_context:
        install_body = {
            "harness": harness,
            "scope": scope,
            "local_name": local_name,
            # Advertise folder bundle support for complete skill folders
            "supported_features": [SKILL_FOLDER_FEATURE],
        }
        if check_upgrade or no_write or raw:
            install_body["preview"] = True
        if version:
            install_body["version"] = version
        result = client.post_public(f"/api/v1/skills/{resolved}/install", install_body)
    snippet = result.get("config_snippet", result)
    if selected_version_id and str(result.get("version_id")) != selected_version_id:
        fail(
            ErrorCategory.CONFLICT,
            "The selected skill version changed during installation.",
            operation="Install skill",
            resource=skill_id,
            remediation="Refresh the exact selected version and retry without changing its folder name.",
        )

    if raw:
        print(_json.dumps(snippet, indent=2))
        return

    skill_info = snippet.get("skill", {})
    if not isinstance(skill_info, dict):
        fail(
            ErrorCategory.UNAVAILABLE,
            "The registry returned an invalid skill installation response.",
            operation="Install skill",
            resource=skill_id,
            remediation="Check server health and version compatibility, then retry.",
        )

    installed_path: Path | None = None
    managed_result: dict | None = None
    bundle_response = result.get("bundle")
    if (upgrade or check_upgrade or backup_root) and not (
        isinstance(bundle_response, dict) and bundle_response.get("files")
    ):
        fail(
            ErrorCategory.VALIDATION,
            "Managed upgrades require a complete reviewed folder bundle.",
            operation="Install skill",
            resource=skill_id,
            remediation="Git and resource-less installs retain their existing behavior.",
        )
    if skill_info.get("bundle_version_id") and not (isinstance(bundle_response, dict) and bundle_response.get("files")):
        fail(
            ErrorCategory.CONFLICT,
            "The selected complete skill folder was not returned by the server.",
            operation="Install skill",
            resource=skill_id,
            remediation="Retry with a compatible server; never install only SKILL.md from a selected folder.",
        )

    if not no_write:
        write_context = redirect_stdout(StringIO()) if output == "json" else nullcontext()
        with write_context:
            delivery_mode = skill_info.get("delivery_mode", "git_fetch")

            # Complete folder bundle from server (registry_direct with extra files)
            if bundle_response and isinstance(bundle_response, dict) and bundle_response.get("files"):
                installed_path, managed_result = _install_managed_folder(
                    bundle_response,
                    result,
                    listing,
                    harness,
                    scope,
                    directory,
                    skill_id,
                    requested_version,
                    upgrade=upgrade,
                    check=check_upgrade,
                    backup_root=backup_root,
                )
            elif delivery_mode == "registry_direct":
                # Preserve historical single-file installs, but never overwrite
                # an active verified folder or orphan its ownership receipt.
                try:
                    with _protect_legacy_skill_install(
                        listing_id=str(skill_info.get("id", resolved)),
                        name=skill_info.get("name", "skill"),
                        harness=harness,
                        scope=scope,
                        directory=directory,
                    ):
                        installed_path = install_skill_registry_direct(
                            name=skill_info.get("name", "skill"),
                            skill_md_content=skill_info.get("skill_md_content"),
                            script_content=skill_info.get("script_content"),
                            script_filename=skill_info.get("script_filename"),
                            harness=harness,
                            scope=scope,
                        )
                except (OSError, RuntimeError) as exc:
                    fail(
                        ErrorCategory.CONFLICT,
                        str(exc),
                        operation="Install skill",
                        resource=skill_id,
                        remediation="Inspect managed folder ownership; choose an explicit safe migration.",
                    )
            else:
                try:
                    with _protect_legacy_skill_install(
                        listing_id=str(skill_info.get("id", resolved)),
                        name=skill_info.get("name", "skill"),
                        harness=harness,
                        scope=scope,
                        directory=directory,
                    ):
                        installed_path = install_skill_from_git(
                            name=skill_info.get("name", "skill"),
                            git_url=skill_info.get("git_url"),
                            skill_path=skill_info.get("skill_path", "/"),
                            git_ref=skill_info.get("git_ref", "main"),
                            harness=harness,
                            scope=scope,
                            skill_md_content=skill_info.get("skill_md_content"),
                        )
                except (OSError, RuntimeError) as exc:
                    fail(
                        ErrorCategory.CONFLICT,
                        str(exc),
                        operation="Install skill",
                        resource=skill_id,
                        remediation="Inspect managed folder ownership; choose an explicit safe migration.",
                    )
        if check_upgrade:
            if output == "json":
                output_json(managed_result)
            else:
                rprint(f"[cyan]Verified upgrade preview:[/cyan] {esc(str(managed_result))}")
            return
        if installed_path is None:
            fail(
                ErrorCategory.UNAVAILABLE,
                "The skill content could not be installed.",
                operation="Install skill",
                resource=skill_id,
                remediation="Check the skill source and local filesystem, then retry.",
            )

        from observal_cli.lockfile import upsert_standalone

        try:
            if managed_result is None:
                upsert_standalone(
                    harness,
                    component_type="skill",
                    name=skill_info.get("name", resolved),
                    component_id=str(skill_info.get("id", resolved)),
                    version=result.get("version") or version or skill_info.get("version") or listing.get("version"),
                    scope=scope,
                    directory=directory,
                    namespace=listing.get("namespace"),
                    slug=listing.get("slug"),
                    local_name=local_name,
                    version_id=str(result["version_id"]) if result.get("version_id") else None,
                    digest=result.get("digest"),
                    requested_version=requested_version,
                )
        except PermissionError as error:
            fail(
                ErrorCategory.PERMISSION,
                "The skill was written but its installed state could not be recorded.",
                operation="Install skill",
                resource="installed-state lockfile",
                remediation="Check lockfile ownership and permissions, then retry.",
                detail=repr(error),
            )
        except (OSError, RuntimeError) as error:
            fail(
                ErrorCategory.UNAVAILABLE,
                "The skill was written but its installed state could not be recorded.",
                operation="Install skill",
                resource="installed-state lockfile",
                remediation="Check local storage and retry.",
                detail=repr(error),
            )
    elif output != "json":
        rprint("[dim]Skill install skipped (no-write mode).[/dim]")

    if output == "json":
        output_json(
            {
                **result,
                "write_performed": not no_write,
                "installed_path": str(installed_path) if installed_path else None,
                "managed_folder": managed_result,
            }
        )
        return

    for warning in result.get("warnings") or []:
        rprint(f"\n[yellow]Warning:[/yellow] {esc(warning)}")

    rprint(f"\n[bold]Config for {esc(harness)}:[/bold]\n")
    console.print_json(_json.dumps(snippet, indent=2))


def _submit_folder_draft(
    *,
    from_dir: str,
    exclude: list[str] | None,
    allow_excluded: bool,
    allow_sensitive: bool,
    name: str | None,
    version: str | None,
    description: str | None,
    task_type: str | None,
    target_agent: list[str] | None,
    slash_command: str | None,
    supported_harnesses: list[str] | None,
    team: str | None,
    visibility: str | None,
    draft: bool,
    output: OutputMode,
) -> None:
    """Submit a complete skill folder as a registry-direct draft.

    Captures the directory, validates content, and submits to the
    folder-drafts endpoint.
    """
    human_output = output != "json"
    source_dir = Path(from_dir).absolute()

    # Capture directory snapshot
    capture_context = nullcontext() if output == "json" else spinner("Capturing directory...")
    with capture_context:
        try:
            snapshot = capture_directory(source_dir, exclude=exclude or [])
        except DirectoryCaptureError as e:
            fail(
                ErrorCategory.VALIDATION,
                f"Cannot capture skill directory: {e}",
                operation="Submit skill",
                resource=str(source_dir),
                remediation="Fix the reported issue and retry.",
            )

    # Parse frontmatter for default values
    fm = _parse_frontmatter(snapshot.skill_md_content)

    # Show capture summary
    if human_output:
        rprint(f"[green]✓ Captured directory:[/green] {esc(str(source_dir))}")
        rprint(f"  Files: {len(snapshot.extra_files) + 1} ({snapshot.total_size:,} bytes)")
        if snapshot.excluded_paths:
            rprint(f"  Excluded: {len(snapshot.excluded_paths)} paths")
            for excluded in snapshot.excluded_paths[:15]:
                rprint(f"    • {esc(excluded)}")
            if len(snapshot.excluded_paths) > 15:
                rprint(f"    • ... and {len(snapshot.excluded_paths) - 15} more")
        for warning in snapshot.warnings:
            rprint(f"  [yellow]Warning:[/yellow] {esc(warning)}")
        if snapshot.excluded_paths and not allow_excluded:
            from observal_cli.prompts import confirm

            if not confirm("Continue without the excluded paths?", default=False):
                rprint("[yellow]Aborted.[/yellow]")
                return
        if snapshot.warnings and not allow_sensitive:
            from observal_cli.prompts import confirm

            if not confirm("Continue with potentially sensitive files?", default=False):
                rprint("[yellow]Aborted.[/yellow]")
                return
    if snapshot.excluded_paths and not human_output and not allow_excluded:
        fail(
            ErrorCategory.VALIDATION,
            "Folder capture excluded local paths.",
            operation="Save skill folder draft",
            resource="local skill folder",
            remediation="Inspect excluded paths, then use --allow-excluded explicitly if intended.",
        )
    if snapshot.warnings and not human_output and not allow_sensitive:
        fail(
            ErrorCategory.VALIDATION,
            "Folder contains likely-sensitive paths.",
            operation="Save skill folder draft",
            resource="local skill folder",
            remediation="Inspect excluded/sensitive paths, then use --allow-sensitive explicitly if intended.",
        )

    # Build payload
    _name = name or fm.get("name", "")
    _description = description or fm.get("description", "")

    if not _name or not _description:
        if output == "json":
            fail(
                ErrorCategory.VALIDATION,
                "Skill name and description are required.",
                operation="Submit skill",
                resource="skill payload",
                remediation="Provide --name and --description, or add them to SKILL.md frontmatter.",
            )
        # Interactive mode
        _name = _name or text_input("Skill name", default=fm.get("name", ""))
        _description = _description or text_input("Description", default=fm.get("description", ""))

    payload: dict = {
        "name": _name,
        "version": version or "1.0.0",
        "description": _description,
        "owner": config.load().get("username", ""),
        "task_type": task_type or "general",
        "skill_md_content": snapshot.skill_md_content,
        "extra_files": snapshot_to_extra_files(snapshot),
    }

    if slash_command or target_agent:
        fail(
            ErrorCategory.VALIDATION,
            "Folder drafts do not accept slash-command or target-agent metadata.",
            operation="Save skill folder draft",
            resource="skill folder metadata",
            remediation="Put a command in SKILL.md frontmatter or edit supported metadata after saving the folder.",
        )
    if supported_harnesses:
        payload["supported_harnesses"] = supported_harnesses

    _validate_skill_fields(payload, "Submit skill")
    client.add_publish_target(payload, team, visibility)

    # Submit to folder-drafts endpoint
    endpoint = "/api/v1/skills/folder-drafts"
    label = "folder draft" if draft else "skill folder"
    submit_context = nullcontext() if output == "json" else spinner(f"Saving {label}...")
    with submit_context:
        result = client.post(endpoint, payload)

    if output == "json":
        output_json(result)
        return

    # The folder endpoint returns a version manifest, not a listing response.
    listing_id = result.get("listing_id")
    rprint("\n[green]✓ Skill folder draft saved.[/green] Review and delivery may still be disabled.")
    rprint(f"  Listing ID: [bold]{esc(str(listing_id))}[/bold]")
    rprint(f"  Version: {esc(payload['version'])}")
    rprint(f"  Version ID: {esc(str(result.get('version_id')))}")
    if listing_id:
        rprint(f"\n[dim]Show draft: observal registry skill show {esc(str(listing_id))}[/dim]")
        rprint(
            "[dim]When review is enabled: observal registry skill submit "
            f"--submit {esc(str(listing_id))} --version-id {esc(str(result.get('version_id')))}[/dim]"
        )


@contextmanager
def _protect_legacy_skill_install(
    *,
    listing_id: str,
    name: str,
    harness: str,
    scope: str,
    directory: str | None,
):
    """Do not let a historical installer overwrite or orphan a managed receipt.

    Keep the destination lock through the legacy write. Upsert performs its own
    atomic lockfile check so a concurrent new managed install also fails closed.
    """
    from observal_cli import lockfile

    target = (
        _user_skill_dest(harness, _sanitize_name(name))
        if scope == "user"
        else Path.cwd() / ".agents" / "skills" / _sanitize_name(name)
    ).absolute()
    lockfile.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(str(target).encode()).hexdigest()
    with lockfile._exclusive_lock(lockfile.CONFIG_DIR / f"skill-{key}.lock"):
        data = lockfile.read_lockfile()
        registry_url = lockfile.current_registry_url()
        for url, registry in data.get("registries", {}).items():
            for section_name, section in registry.get("harnesses", {}).items():
                for owner in [*section.get("standalone", []), *section.get("agents", [])]:
                    owned = [owner, *owner.get("components", [])] if "components" in owner else [owner]
                    for entry in owned:
                        proof = entry.get("folder_receipt")
                        if not isinstance(proof, dict):
                            continue
                        recorded = Path(proof.get("target", ""))
                        same_path = recorded == target or (target.exists() and target.resolve() == recorded.resolve())
                        same_install = (
                            url == registry_url
                            and section_name == harness
                            and proof.get("listing_id") == listing_id
                            and proof.get("source") == "standalone"
                            and proof.get("scope") == scope
                            and (scope == "user" or owner.get("directory") == directory)
                        )
                        if same_path or same_install:
                            raise RuntimeError(
                                "A verified folder already owns this skill or destination; legacy delivery cannot overwrite it"
                            )
        yield


def _install_managed_folder(
    bundle_response: dict,
    result: dict,
    listing: dict,
    harness: str,
    scope: str,
    directory: str | None,
    skill_id: str,
    requested_version: str | None,
    *,
    upgrade: bool,
    check: bool,
    backup_root: Path | None,
) -> tuple[Path | None, dict]:
    """Install with machine receipt; legacy adoption needs reviewed exact bytes."""
    from observal_cli import lockfile, managed_skill
    from observal_shared.harness_registry import HARNESS_REGISTRY

    try:
        bundle = validate_bundle(
            bundle_response, expected_version_id=result.get("version_id"), expected_digest=result.get("digest")
        )
        template = HARNESS_REGISTRY.get(harness.replace("_", "-"), {}).get("skills", {}).get(scope)
        if not template or template.format(name=bundle.folder_name) != bundle.skill_file_path:
            raise managed_skill.ManagedSkillError("Bundle destination differs from harness template")
        target = (
            Path(bundle.skill_file_path).expanduser() if scope == "user" else Path.cwd() / bundle.skill_file_path
        ).parent.absolute()
        proof = managed_skill.receipt(bundle, target, harness, scope, registry_url=lockfile.current_registry_url())
        # Fetch an old reviewed version only when an existing, unreceipted
        # lock entry names it; never infer ownership from the directory name.
        old_bundle = None
        if target.exists() and not target.is_symlink():
            data = lockfile.read_lockfile()
            entries = (
                data.get("registries", {})
                .get(proof["registry_url"], {})
                .get("harnesses", {})
                .get(harness, {})
                .get("standalone", [])
            )
            legacy = [
                e
                for e in entries
                if e.get("type") == "skill"
                and e.get("id") == bundle.listing_id
                and e.get("scope") == scope
                and (e.get("directory") or None) == directory
                and not e.get("folder_receipt")
                and e.get("version_id")
                and e.get("digest")
            ]
            if len(legacy) == 1 and legacy[0].get("version"):
                old_response = client.post_public(
                    f"/api/v1/skills/{bundle.listing_id}/install",
                    {
                        "harness": harness,
                        "scope": scope,
                        "local_name": bundle.folder_name,
                        "supported_features": [SKILL_FOLDER_FEATURE],
                        "version": legacy[0]["version"],
                    },
                )
                old = old_response.get("bundle")
                if (
                    isinstance(old, dict)
                    and old.get("files")
                    and old_response.get("version_id") == legacy[0]["version_id"]
                    and old_response.get("digest") == legacy[0]["digest"]
                ):
                    old_bundle = validate_bundle(
                        old, expected_version_id=legacy[0]["version_id"], expected_digest=legacy[0]["digest"]
                    )
                    if old_bundle.folder_name != bundle.folder_name:
                        raise managed_skill.ManagedSkillError("Folder name changed; explicit migration required")
        if target.exists() and not requested_version:
            existing = managed_skill._records(lockfile.read_lockfile(), target)
            if len(existing) == 1 and existing[0][1].get("version") and result.get("version"):
                try:
                    if Version(str(result["version"])) < Version(str(existing[0][1]["version"])):
                        raise managed_skill.ManagedSkillError(
                            "Downgrading requires an explicit --version pin",
                            remediation="Re-run with --version VERSION --upgrade to downgrade deliberately.",
                        )
                except InvalidVersion as exc:
                    raise managed_skill.ManagedSkillError(
                        "Cannot compare installed and selected release versions"
                    ) from exc
        if target.exists() and not (upgrade or check):
            # Same-version no-op is safe; all other replacements require intent.
            existing = managed_skill._records(lockfile.read_lockfile(), target)
            if len(existing) != 1 or existing[0][1].get("folder_receipt", {}).get("version_id") != bundle.version_id:
                raise managed_skill.ManagedSkillError(
                    "Folder exists; use --upgrade after verifying the selected version",
                    remediation="Preview with --check-upgrade, then re-run with --upgrade to replace it.",
                )

        def record(data: dict) -> None:
            registry = data.setdefault("registries", {}).setdefault(
                proof["registry_url"], {"server_url": proof["registry_url"], "harnesses": {}}
            )
            section = registry["harnesses"].setdefault(harness, {"agents": [], "standalone": []})
            entries = section.setdefault("standalone", [])
            matches = [
                e
                for e in entries
                if e.get("type") == "skill"
                and e.get("id") == bundle.listing_id
                and e.get("scope") == scope
                and (e.get("directory") or None) == directory
            ]
            if len(matches) > 1:
                raise managed_skill.ManagedSkillError("Ambiguous standalone lock entries")
            # The destination preflight and this lockfile update are separate
            # locks. A concurrent first install of this listing may have won
            # under a different folder name while our folder was staged.
            for existing in matches:
                receipt = existing.get("folder_receipt")
                if (
                    receipt
                    and receipt.get("target") != str(target)
                    and Path(receipt.get("target", "")).parent == target.parent
                ):
                    raise managed_skill.ManagedSkillError(
                        "This listing already owns a different folder name in this skill root"
                    )
            entry = matches[0] if matches else {}
            entry.update(
                {
                    "type": "skill",
                    "name": result.get("config_snippet", {}).get("skill", {}).get("name", skill_id),
                    "id": bundle.listing_id,
                    "version": result.get("version"),
                    "scope": scope,
                    "namespace": listing.get("namespace"),
                    "slug": listing.get("slug"),
                    "local_name": bundle.folder_name,
                    "version_id": bundle.version_id,
                    "digest": bundle.digest,
                    "folder_receipt": proof,
                }
            )
            if directory:
                entry["directory"] = directory
            if requested_version:
                entry["requested_version"] = requested_version
            if not matches:
                entries.append(entry)

        outcome = managed_skill.transact(
            bundle, target, proof, record=record, old_bundle=old_bundle, backup_root=backup_root, check=check
        )
        return (None if check else target / "SKILL.md"), outcome
    except (BundleValidationError, managed_skill.ManagedSkillError, OSError, RuntimeError) as exc:
        fail(
            ErrorCategory.CONFLICT,
            str(exc),
            operation="Install skill",
            resource=skill_id,
            remediation=getattr(exc, "remediation", None)
            or "No files were overwritten. Inspect the reported path; see registry skill backups list for retained backups.",
        )
        raise AssertionError("unreachable") from exc


# Harness config dirs to check for symlinking (canonical name → dir name)
_HARNESS_SKILL_DIRS: list[tuple[str, str]] = [
    ("claude-code", ".claude"),
    ("cursor", ".cursor"),
    ("kiro", ".kiro"),
    ("opencode", ".opencode"),
]

# User-scope skill directories per harness (global install locations)
_USER_SKILL_DIRS: dict[str, str] = {
    "claude-code": "~/.claude/skills",
    "kiro": "~/.kiro/skills",
    "opencode": "~/.config/opencode/skills",
    "cursor": "~/.cursor/rules",
    "copilot": "~/.copilot/skills",
    "pi": "~/.pi/agent/skills",
}


def _user_skill_dest(harness: str, skill_name: str) -> Path:
    """Resolve the user-scope (global) install path for a skill."""
    harness_key = harness.replace("_", "-")
    base = _USER_SKILL_DIRS.get(harness_key, "~/.agents/skills")
    expanded = Path(base.replace("~", str(Path.home())))
    return expanded / skill_name


def install_skill_registry_direct(
    *,
    name: str,
    skill_md_content: str | None,
    script_content: str | None = None,
    script_filename: str | None = None,
    harness: str = "claude-code",
    scope: str = "user",
    ide: str | None = None,
    cwd: Path | None = None,
    dest: Path | None = None,
) -> Path | None:
    """Install a registry_direct skill: write SKILL.md and optional script.

    Writes to <dest>/<name>/SKILL.md and <dest>/<name>/scripts/<script_filename>.
    Returns the destination Path on success, None on failure.
    """
    skill_name = _sanitize_name(name)
    custom_dest = dest is not None
    target_harness = ide or harness

    if dest is None:
        if scope == "user":
            dest = _user_skill_dest(target_harness, skill_name)
        else:
            base = (cwd or Path.cwd()) / ".agents" / "skills"
            dest = base / skill_name
            if not _is_path_safe(dest, base):
                rprint(f"[red]✗ Unsafe skill name (path traversal detected):[/red] {esc(repr(skill_name))}")
                return None

    if not skill_md_content:
        rprint("[yellow]\u26a0 No SKILL.md content available to write.[/yellow]")
        return None

    dest.mkdir(parents=True, exist_ok=True)
    (dest / "SKILL.md").write_text(skill_md_content, encoding="utf-8")
    rprint(f"[green]\u2713 Wrote skill file:[/green] {esc(dest / 'SKILL.md')}")

    if script_content and script_filename:
        scripts_dir = dest / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        script_path = scripts_dir / script_filename
        if not _is_path_safe(script_path, scripts_dir):
            rprint(f"[red]\u2717 Unsafe script filename (path traversal):[/red] {esc(repr(script_filename))}")
        else:
            script_path.write_text(script_content, encoding="utf-8")
            # Make executable if it looks like a script
            if script_filename.endswith((".sh", ".bash", ".py", ".rb")):
                import os

                os.chmod(script_path, 0o755)
            rprint(f"[green]\u2713 Wrote script:[/green] {esc(script_path)}")

    if scope == "project" and not custom_dest:
        _symlink_for_harnesses(cwd or Path.cwd(), dest, skill_name)

    return dest


def install_skill_from_git(
    *,
    name: str,
    git_url: str | None,
    skill_path: str = "/",
    git_ref: str = "main",
    harness: str = "claude-code",
    scope: str = "user",
    ide: str | None = None,
    skill_md_content: str | None = None,
    cwd: Path | None = None,
    dest: Path | None = None,
) -> Path | None:
    """Core skill install logic - clone full directory from git.

    Used by both `observal skill install` and `observal pull` (for agent skills).

    Returns the destination Path on success, None on failure.
    """
    skill_name = _sanitize_name(name)
    custom_dest = dest is not None
    target_harness = ide or harness

    if dest is None:
        if scope == "user":
            dest = _user_skill_dest(target_harness, skill_name)
        else:
            base = (cwd or Path.cwd()) / ".agents" / "skills"
            dest = base / skill_name
            if not _is_path_safe(dest, base):
                rprint(f"[red]✗ Unsafe skill name (path traversal detected):[/red] {esc(repr(skill_name))}")
                return None

    if not git_url:
        rprint("[red]\u2717 Git URL is required for git-fetch skill installation.[/red]")
        return None

    dest.mkdir(parents=True, exist_ok=True)
    wrote_full_dir = _sparse_clone_skill_dir(git_url, skill_path, git_ref, dest)
    if not wrote_full_dir:
        rprint("[red]\u2717 Git skill clone failed.[/red]")
        return None
    rprint(f"[green]\u2713 Skill directory written:[/green] {esc(dest)}")
    if scope == "project" and not custom_dest:
        _symlink_for_harnesses(cwd or Path.cwd(), dest, skill_name)
    return dest


def _symlink_for_harnesses(cwd: Path, canonical: Path, skill_name: str) -> None:
    """Create .<agent>/skills/<name>/ symlinks for every harness config dir that exists."""
    for _harness, agent_dir in _HARNESS_SKILL_DIRS:
        agent_root = cwd / agent_dir
        if not agent_root.exists():
            continue
        skills_dir = agent_root / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        link = skills_dir / skill_name
        if link.exists() or link.is_symlink():
            continue
        link.symlink_to(canonical.resolve())
        rprint(f"[dim]  → symlinked {esc(link)} → {esc(canonical)}[/dim]")


# ── Edit ─────────────────────────────────────────────────────────────────────


@skill_app.command(name="edit")
def skill_edit(
    skill_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    from_file: str | None = typer.Option(None, "--from-file", "-f", help="Load updates from JSON file"),
    name: str | None = typer.Option(None, "--name", "-n", help="New listing name"),
    description: str | None = typer.Option(None, "--description", "-d", help="New description"),
    version: str | None = typer.Option(None, "--version", "-v", help="New version string"),
    task_type: str | None = typer.Option(None, "--task-type", "-t", help="New task type"),
    git_url: str | None = typer.Option(None, "--git-url", help="New git URL"),
    git_ref: str | None = typer.Option(None, "--git-ref", help="New git ref"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Edit a draft, rejected, or pending skill submission.

    Updates fields on a skill that has not yet been approved. You can
    provide individual field options or load all updates from a JSON file.
    Acquires an edit lock to prevent concurrent modifications.

    Examples:
        observal registry skill edit my-skill --description "Better desc"
        observal registry skill edit abc123 --from-file updates.json
        observal registry skill edit @sk --git-url https://github.com/org/new-repo --output json
    """
    if from_file:
        try:
            with open(from_file) as f:
                updates = _json.load(f)
        except _json.JSONDecodeError as error:
            fail(
                ErrorCategory.VALIDATION,
                "The skill update file is not valid JSON.",
                operation="Edit skill",
                resource=from_file,
                remediation="Correct the JSON and retry.",
                detail=repr(error),
            )
        except FileNotFoundError as error:
            fail(
                ErrorCategory.NOT_FOUND,
                "The skill update file was not found.",
                operation="Edit skill",
                resource=from_file,
                remediation="Provide an existing update file and retry.",
                detail=repr(error),
            )
        if not isinstance(updates, dict):
            fail(
                ErrorCategory.VALIDATION,
                "The skill update file must contain a JSON object.",
                operation="Edit skill",
                resource=from_file,
                remediation="Replace the file contents with a JSON object and retry.",
            )
    else:
        updates = {}
        if name is not None:
            updates["name"] = name
        if description is not None:
            updates["description"] = description
        if version is not None:
            updates["version"] = version
        if task_type is not None:
            updates["task_type"] = task_type
        if git_url is not None:
            updates["git_url"] = git_url
        if git_ref is not None:
            updates["git_ref"] = git_ref

    if not updates:
        fail(
            ErrorCategory.VALIDATION,
            "No skill changes were provided.",
            operation="Edit skill",
            resource=skill_id,
            remediation="Provide an update file or one or more field options.",
        )
    _validate_skill_fields(updates, "Edit skill")

    resolved = client.resolve_registry_reference("skill", skill_id)
    client.post(f"/api/v1/skills/{resolved}/start-edit")
    save_context = nullcontext() if output == "json" else spinner("Saving changes...")
    with save_context:
        result = client.put(f"/api/v1/skills/{resolved}/draft", updates)
    if output == "json":
        output_json(result)
    else:
        rprint(f"[green]✓ Updated {esc(result['name'])}[/green] (status: {esc(result.get('status', 'unknown'))})")


# ── Version lifecycle commands ──────────────────────────────────────────────


def _uncertain_skill_draft(resolved: str, version: str, skill_id: str, operation: str) -> None:
    """A timed-out create is not safe to retry without a lookup by version."""
    try:
        found = client.get(f"/api/v1/skills/{resolved}/versions/{version}", operation=operation, resource=skill_id)
    except CliError:
        found = None
    if found and found.get("status") in {"draft", "rejected"}:
        detail = f"The draft exists: version UUID {found['id']}. Resume it instead of creating a duplicate."
    else:
        detail = f"The create outcome is uncertain. Check {skill_id} v{version} before retrying."
    fail(ErrorCategory.UNAVAILABLE, detail, operation=operation, resource=skill_id)


@skill_app.command(name="fork")
def skill_fork(
    skill_id: str = typer.Argument(..., help="Existing reviewed direct skill ID or namespace/slug"),
    version: str = typer.Option(..., "--version", "-v", help="New stable version (X.Y.Z)"),
    description: str = typer.Option(..., "--description", "-d", help="Description of this successor"),
    changelog: str | None = typer.Option(None, "--changelog", help="What changed since the reviewed release"),
    from_dir: str | None = typer.Option(None, "--from-dir", help="Optionally replace cloned files from this folder"),
    exclude: list[str] | None = typer.Option(None, "--exclude", help="Paths to exclude (repeatable)"),
    allow_excluded: bool = typer.Option(False, "--allow-excluded", help="Acknowledge excluded source paths"),
    allow_sensitive: bool = typer.Option(False, "--allow-sensitive", help="Acknowledge sensitive source paths"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Create an editable successor to the currently reviewed direct release.

    If a file replacement fails, the new draft remains saved; use its printed
    version UUID to resume editing instead of creating another release.

    Examples:
        observal registry skill fork alice/review --version 1.1.0 --description 'New templates' --from-dir ./review
    """
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version):
        fail(ErrorCategory.VALIDATION, "Version must be X.Y.Z.", operation="Fork skill", resource=version)
    snapshot_body = None
    if from_dir:
        snapshot_body = _capture_skill_snapshot(
            Path(from_dir).resolve(), exclude or [], allow_excluded, allow_sensitive, output, "Fork skill"
        )
    elif exclude or allow_excluded or allow_sensitive:
        fail(ErrorCategory.USAGE, "Upload options require --from-dir.", operation="Fork skill", resource=skill_id)
    resolved = client.resolve_registry_reference("skill", skill_id)
    base = client.get(f"/api/v1/skills/{resolved}/approved-base", operation="Fork skill", resource=skill_id)
    if base.get("delivery_mode") != "registry_direct":
        fail(
            ErrorCategory.VALIDATION,
            "The reviewed release is Git-backed; use import-folder instead.",
            operation="Fork skill",
            resource=skill_id,
        )
    body = {
        "base_version_id": base["version_id"],
        "observed_base_revision": base["revision"],
        "version": version,
        "description": description,
        "changelog": changelog,
    }
    try:
        draft = client.post(f"/api/v1/skills/{resolved}/drafts", body, operation="Fork skill", resource=skill_id)
    except CliError as error:
        if error.category != ErrorCategory.UNAVAILABLE:
            raise
        _uncertain_skill_draft(resolved, version, skill_id, "Fork skill")
    if snapshot_body is not None:
        try:
            saved = client.put(
                f"/api/v1/skills/{resolved}/versions/{draft['version_id']}/files",
                {"observed_revision": draft["revision"], **snapshot_body},
                operation="Replace fork files",
                resource=skill_id,
            )
        except CliError:
            rprint(
                f"[yellow]Draft {esc(draft['version_id'])} was created but its file replacement failed. "
                "Resume that same draft; do not fork again.[/yellow]",
                file=sys.stderr,
            )
            raise
        draft = saved
    result = {
        "listing_id": str(resolved),
        "version_id": draft["version_id"],
        "base_version_id": base["version_id"],
        "revision": draft["revision"],
        "version": version,
    }
    if output == "json":
        output_json(result)
    else:
        rprint(f"[green]✓ Saved editable v{esc(version)} folder draft[/green]")
        rprint(f"  Version ID: {esc(result['version_id'])}")
        rprint(f"  Revision: {esc(result['revision'])}")
        rprint(
            f"  Next: observal registry skill replace-files {esc(skill_id)} --version-id {esc(result['version_id'])} --from-dir DIR --revision {esc(result['revision'])}"
        )
        rprint("  Submit this exact version for review when ready; the approved release is unchanged.")


@skill_app.command(name="import-folder")
def skill_import_folder(
    skill_id: str = typer.Argument(..., help="Existing approved Git or legacy direct skill ID or namespace/slug"),
    from_dir: str = typer.Option(..., "--from-dir", help="Complete locally supplied skill folder"),
    version: str = typer.Option(..., "--version", "-v", help="New stable version (X.Y.Z)"),
    description: str = typer.Option(..., "--description", "-d", help="Description of this successor"),
    changelog: str | None = typer.Option(None, "--changelog", help="What changed since the reviewed release"),
    exclude: list[str] | None = typer.Option(None, "--exclude", help="Paths to exclude (repeatable)"),
    allow_excluded: bool = typer.Option(False, "--allow-excluded", help="Acknowledge excluded source paths"),
    allow_sensitive: bool = typer.Option(False, "--allow-sensitive", help="Acknowledge sensitive source paths"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Create one complete direct-folder draft under a reviewed Git or legacy listing.

    This does not download Git bytes or alter the approved release. Review must
    explicitly acknowledge that the old Git file tree cannot be compared.

    Examples:
        observal registry skill import-folder alice/review --from-dir ./review --version 1.1.0 --description 'Direct folder'
    """
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version):
        fail(ErrorCategory.VALIDATION, "Version must be X.Y.Z.", operation="Import skill folder", resource=version)
    snapshot = _capture_skill_snapshot(
        Path(from_dir).resolve(), exclude or [], allow_excluded, allow_sensitive, output, "Import skill folder"
    )
    resolved = client.resolve_registry_reference("skill", skill_id)
    base = client.get(f"/api/v1/skills/{resolved}/approved-base", operation="Import skill folder", resource=skill_id)
    body = {
        "base_version_id": base["version_id"],
        "observed_base_revision": base["revision"],
        "version": version,
        "description": description,
        "changelog": changelog,
        **snapshot,
    }
    try:
        draft = client.post(
            f"/api/v1/skills/{resolved}/folder-import-drafts",
            body,
            operation="Import skill folder",
            resource=skill_id,
        )
    except CliError as error:
        if error.category != ErrorCategory.UNAVAILABLE:
            raise
        _uncertain_skill_draft(resolved, version, skill_id, "Import skill folder")
    result = {
        "listing_id": str(resolved),
        "version_id": draft["version_id"],
        "base_version_id": base["version_id"],
        "revision": draft["revision"],
        "version": version,
    }
    if output == "json":
        output_json(result)
    else:
        rprint(f"[green]✓ Saved imported folder as editable v{esc(version)} draft[/green]")
        rprint(f"  Version ID: {esc(result['version_id'])}")
        rprint(f"  Revision: {esc(result['revision'])}")
        rprint("  Review the complete candidate folder before submitting; the old release is unchanged.")


@skill_app.command(name="withdraw")
def skill_withdraw(
    skill_id: str = typer.Argument(..., help="Skill ID, name, or @alias"),
    version_id: str = typer.Option(..., "--version-id", help="Version UUID to withdraw"),
    revision: str = typer.Option(..., "--revision", help="Observed revision (prevents stale withdrawals)"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Withdraw a pending skill version from review.

    Returns the version to draft status so you can make further edits.
    Requires the observed revision to prevent withdrawing a version that
    has changed since you last viewed it.

    Examples:
        observal registry skill withdraw my-skill --version-id abc123 --revision def456
    """
    resolved = client.resolve_registry_reference("skill", skill_id)
    body = {"observed_revision": revision}
    withdraw_context = nullcontext() if output == "json" else spinner("Withdrawing version...")
    with withdraw_context:
        result = client.post(f"/api/v1/skills/{resolved}/versions/{version_id}/withdraw", body)
    if output == "json":
        output_json(result)
    else:
        rprint("[green]✓ Version withdrawn[/green] - now in draft status")
        rprint(f"  Version ID: {esc(version_id)}")


@skill_app.command(name="rebase")
def skill_rebase(
    skill_id: str = typer.Argument(..., help="Skill ID, name, or @alias"),
    version_id: str = typer.Option(..., "--version-id", help="Draft version UUID to rebase"),
    revision: str = typer.Option(..., "--revision", help="Observed revision of your draft"),
    current_version_id: str = typer.Option(..., "--current-version-id", help="Current approved version UUID"),
    current_revision: str = typer.Option(..., "--current-revision", help="Observed revision of current approved"),
    new_version: str = typer.Option(..., "--new-version", "-v", help="New version string after rebase"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Rebase a draft skill version on the current approved version.

    Use this when the approved version has changed since you created your draft.
    The server reports any conflicts between your changes and the approved changes.

    Examples:
        observal registry skill rebase my-skill --version-id draft123 --revision abc \\
            --current-version-id approved456 --current-revision def --new-version 1.3.0
    """
    resolved = client.resolve_registry_reference("skill", skill_id)
    body = {
        "observed_revision": revision,
        "current_version_id": current_version_id,
        "observed_current_revision": current_revision,
        "new_version": new_version,
    }
    rebase_context = nullcontext() if output == "json" else spinner("Rebasing version...")
    with rebase_context:
        result = client.post(f"/api/v1/skills/{resolved}/versions/{version_id}/rebase", body)
    if output == "json":
        output_json(result)
    else:
        conflicts = result.get("conflicts", [])
        if conflicts:
            rprint(f"[yellow]⚠ Rebase completed with {len(conflicts)} conflict(s)[/yellow]")
            for conflict in conflicts:
                rprint(f"  • {esc(conflict)}")
        else:
            rprint("[green]✓ Rebased successfully[/green]")
        rprint(f"  New version: {esc(new_version)}")
        rprint(f"  New revision: {esc(result.get('revision', 'unknown'))}")


@skill_app.command(name="export")
def skill_export(
    skill_id: str = typer.Argument(..., help="Skill ID, name, or @alias"),
    dest: str = typer.Argument(..., help="Destination directory (must not exist)"),
    version_id: str | None = typer.Option(
        None, "--version-id", help="Specific version UUID (default: latest approved)"
    ),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Export a skill version to a local directory.

    Downloads the complete skill folder (SKILL.md and all extra files) and
    writes them atomically to a new directory. Existing paths are never overwritten.

    Examples:
        observal registry skill export my-skill ./my-skill-local
        observal registry skill export my-skill ./v2 --version-id abc123
    """
    dest_path = Path(dest).expanduser().absolute()
    if dest_path.is_symlink() or dest_path.exists():
        fail(
            ErrorCategory.VALIDATION,
            "Export destination already exists; refusing to overwrite it.",
            operation="Export skill",
            resource=str(dest_path),
            remediation="Choose a new destination directory.",
        )

    resolved = client.resolve_registry_reference("skill", skill_id)

    # Get manifest
    manifest_context = nullcontext() if output == "json" else spinner("Fetching manifest...")
    with manifest_context:
        if version_id:
            manifest = client.get(f"/api/v1/skills/{resolved}/versions/{version_id}/manifest")
        else:
            # Listing responses intentionally do not expose latest_version_id.
            # Select only a cleared approved release, not a pending draft or an
            # archived historical release, from the paginated version history.
            for page in range(1, 11):
                history = client.get(f"/api/v1/skills/{resolved}/versions", {"page": page, "page_size": 50})
                release = next(
                    (
                        v
                        for v in history.get("items", [])
                        if v.get("status") == "approved" and not v.get("requires_global_review")
                    ),
                    None,
                )
                if release:
                    version_id = release["id"]
                    break
                if page * 50 >= history.get("total", 0):
                    break
            if version_id is None:
                fail(
                    ErrorCategory.NOT_FOUND,
                    "No approved version found in the first 500 releases.",
                    operation="Export skill",
                    resource=skill_id,
                    remediation="Specify an authorized --version-id, or wait for approval.",
                )
            manifest = client.get(f"/api/v1/skills/{resolved}/versions/{version_id}/manifest")

    files = manifest.get("files", [])
    if not files:
        fail(
            ErrorCategory.UNAVAILABLE,
            "No files found in skill version manifest.",
            operation="Export skill",
            resource=skill_id,
            remediation="The skill version may be empty or inaccessible.",
        )

    # Download the entire version before touching the destination. Both media
    # types must match the exact version/revision and each manifest hash. A
    # corrupted response cannot leave a partially exported directory behind.
    import base64
    import json
    from urllib.parse import quote

    from observal_cli.skill_folder import (
        MAX_BUNDLE_FILES,
        BundleInstallError,
        BundleValidationError,
        _normalize_path,
        install_folder_bundle,
        validate_bundle,
    )

    if not isinstance(files, list) or len(files) > MAX_BUNDLE_FILES:
        fail(
            ErrorCategory.UNAVAILABLE,
            "Invalid skill file manifest.",
            operation="Export skill",
            resource=skill_id,
            remediation="Retry after checking the server version.",
        )
    if str(manifest.get("version_id")) != str(version_id) or str(manifest.get("listing_id")) != str(resolved):
        fail(
            ErrorCategory.UNAVAILABLE,
            "Skill manifest identity does not match the selected version.",
            operation="Export skill",
            resource=skill_id,
            remediation="Retry or inspect the server response.",
        )
    download_context = nullcontext() if output == "json" else spinner(f"Downloading {len(files)} files...")
    try:
        downloaded = []
        with download_context:
            for declaration in files:
                file_path = _normalize_path(declaration["path"])
                raw, headers = client.get_bytes_with_headers(
                    f"/api/v1/skills/{resolved}/versions/{version_id}/files/{quote(file_path, safe='/')}"
                )
                media = headers.get("content-type", "").split(";", 1)[0].lower()
                if media == "application/json":
                    preview = json.loads(raw)
                    if (
                        str(preview.get("version_id")) != str(version_id)
                        or preview.get("revision") != manifest.get("revision")
                        or preview.get("file") != declaration
                        or preview.get("encoding") != "utf-8"
                        or not isinstance(preview.get("content"), str)
                    ):
                        raise BundleValidationError(f"Preview identity mismatch for {file_path}")
                    content = preview["content"].encode("utf-8")
                elif media == "application/octet-stream":
                    content = raw
                else:
                    raise BundleValidationError(f"Unexpected file response type for {file_path}")
                downloaded.append(
                    {
                        **declaration,
                        "version_id": str(version_id),
                        "content": base64.b64encode(content).decode("ascii"),
                        "encoding": "base64",
                    }
                )
        bundle = validate_bundle(
            {
                "listing_id": str(resolved),
                "version_id": str(version_id),
                "digest": manifest["revision"],
                "skill_file_path": "export/skill/SKILL.md",
                "files": downloaded,
            },
            expected_version_id=str(version_id),
        )
        install_folder_bundle(bundle, dest_path)
    except (BundleValidationError, BundleInstallError, ValueError, KeyError, TypeError) as exc:
        fail(
            ErrorCategory.UNAVAILABLE,
            "Cannot export the complete verified skill folder.",
            operation="Export skill",
            resource=skill_id,
            remediation="Inspect the version and destination; no existing directory was overwritten.",
            detail=str(exc),
        )

    written_files = [file.path for file in bundle.files]

    if output == "json":
        output_json(
            {
                "skill_id": resolved,
                "version_id": version_id,
                "destination": str(dest_path),
                "files": written_files,
            }
        )
    else:
        rprint(f"[green]✓ Exported {len(written_files)} files to {esc(str(dest_path))}[/green]")
        for f in written_files:
            rprint(f"  • {esc(f)}")


def _capture_skill_snapshot(
    source_dir: Path,
    exclude: list[str],
    allow_excluded: bool,
    allow_sensitive: bool,
    output: OutputMode,
    operation: str,
) -> dict:
    capture_context = nullcontext() if output == "json" else spinner("Capturing directory...")
    with capture_context:
        try:
            snapshot = capture_directory(source_dir, exclude=exclude)
        except DirectoryCaptureError as error:
            fail(
                ErrorCategory.VALIDATION,
                f"Cannot capture directory: {error}",
                operation=operation,
                resource=str(source_dir),
                remediation="Fix the reported issue and retry.",
            )
    if output != "json":
        for excluded in snapshot.excluded_paths[:15]:
            rprint(f"[yellow]Excluded:[/yellow] {esc(excluded)}")
        if len(snapshot.excluded_paths) > 15:
            rprint(f"  ... and {len(snapshot.excluded_paths) - 15} more")
        for warning in snapshot.warnings:
            rprint(f"[yellow]Warning:[/yellow] {esc(warning)}")
    if snapshot.excluded_paths and not allow_excluded:
        fail(
            ErrorCategory.VALIDATION,
            "Source folder contains excluded paths; complete replacement may delete files.",
            operation=operation,
            resource=str(source_dir),
            remediation="Inspect excluded paths and explicitly use --allow-excluded if deletion is intended.",
        )
    if snapshot.warnings and not allow_sensitive:
        if output == "json":
            fail(
                ErrorCategory.VALIDATION,
                "Source folder contains likely-sensitive files.",
                operation=operation,
                resource=str(source_dir),
                remediation="Inspect the files, then use --allow-sensitive explicitly if intended.",
            )
        from observal_cli.prompts import confirm

        if not confirm("Continue uploading potentially sensitive files?", default=False):
            fail(ErrorCategory.VALIDATION, "Upload cancelled.", operation=operation, resource=str(source_dir))
    return {
        "skill_md_content": snapshot.skill_md_content,
        "extra_files": snapshot_to_extra_files(snapshot),
    }


@skill_app.command(name="replace-files")
def skill_replace_files(
    skill_id: str = typer.Argument(..., help="Skill ID, name, or @alias"),
    version_id: str = typer.Option(..., "--version-id", help="Version UUID to update"),
    from_dir: str = typer.Option(..., "--from-dir", help="Directory containing new files"),
    revision: str = typer.Option(..., "--revision", help="Observed revision (prevents stale updates)"),
    exclude: list[str] | None = typer.Option(None, "--exclude", help="Paths to exclude (repeatable)"),
    allow_excluded: bool = typer.Option(
        False, "--allow-excluded", help="Acknowledge excluded paths before replacing all files"
    ),
    allow_sensitive: bool = typer.Option(
        False, "--allow-sensitive", help="Acknowledge likely-sensitive paths before upload"
    ),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Replace all files in a skill version with contents from a directory.

    This is a complete replacement - files not in the source directory will
    be deleted from the version. Use for updating a draft after local edits.

    Examples:
        observal registry skill replace-files my-skill --version-id abc123 \\
            --from-dir ./my-skill --revision def456
    """
    snapshot_body = _capture_skill_snapshot(
        Path(from_dir).resolve(), exclude or [], allow_excluded, allow_sensitive, output, "Replace skill files"
    )
    resolved = client.resolve_registry_reference("skill", skill_id)
    body = {"observed_revision": revision, **snapshot_body}

    replace_context = nullcontext() if output == "json" else spinner("Replacing files...")
    with replace_context:
        result = client.put(f"/api/v1/skills/{resolved}/versions/{version_id}/files", body)

    if output == "json":
        output_json(result)
    else:
        rprint("[green]✓ Files replaced[/green]")
        rprint(f"  Files: {len(snapshot_body['extra_files']) + 1}")
        rprint(f"  New revision: {esc(result.get('revision', 'unknown'))}")
