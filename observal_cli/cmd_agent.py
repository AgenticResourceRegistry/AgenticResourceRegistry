# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Agent CLI commands."""

from __future__ import annotations

import json as _json
import re
from contextlib import nullcontext
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

import typer
import yaml
from loguru import logger as optic
from packaging.version import InvalidVersion, Version
from rich import print as rprint
from rich.panel import Panel
from rich.table import Table

from observal_cli import client, config
from observal_cli.cmd_fork import fork_detail_rows
from observal_cli.constants import AGENT_NAME_REGEX, VALID_HARNESSES
from observal_cli.errors import CliError, ErrorCategory, exit_partial, fail
from observal_cli.prompts import fuzzy_select, select_many, select_one, text_input
from observal_cli.render import (
    OutputMode,
    console,
    display_name,
    esc,
    handle,
    ide_tags,
    kv_panel,
    listing_status,
    name_inline,
    output_json,
    relative_time,
    spinner,
    status_badge,
)

# ── Agent authoring constants ──────────────────────────────
YAML_FILE = "observal-agent.yaml"
VALID_COMPONENT_TYPES = {"mcp", "skill", "hook", "prompt", "sandbox"}
_RESERVED_AGENT_SLUGS = {
    "archive",
    "draft",
    "fork",
    "forks",
    "install",
    "resolve",
    "restore",
    "submit",
    "unarchive",
    "versions",
}

# Common model choices for the interactive wizard
_MODEL_CHOICES = [
    "claude-sonnet-4",
    "claude-opus-4",
    "claude-haiku-4-5",
    "gemini-2.5-pro",
    "gpt-4o",
    "gpt-4.1",
]


def _slugify(raw: str) -> str:
    """Convert a raw name to a valid agent slug."""
    s = raw.strip().lower()
    s = re.sub(r"[^a-z0-9_-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")


def _bulk_agent_slug(raw: str) -> str:
    """Mirror the server's canonical registry slug for duplicate preflight."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", raw.strip().lower()).strip("-_")
    return slug[:64].rstrip("-_")


def _validate_name(name: str) -> str | None:
    """Return error message if name is invalid, else None."""
    if not name:
        return "Agent name is required."
    if len(name) > 64:
        return "Agent name must be at most 64 characters."
    if not AGENT_NAME_REGEX.match(name):
        return "Must start with a letter/digit and contain only lowercase letters, digits, hyphens, underscores."
    return None


def _fetch_registry_items(component_type: str) -> list[dict]:
    """Fetch approved items from a registry endpoint."""
    plural = {"mcp": "mcps", "skill": "skills", "hook": "hooks", "prompt": "prompts", "sandbox": "sandboxes"}
    return client.get(f"/api/v1/{plural[component_type]}")


def _progress(output: OutputMode | str, message: str | None = None):
    return nullcontext() if output == "json" else spinner(message)


def _validate_version(value: str, *, operation: str) -> str:
    try:
        return str(Version(value))
    except InvalidVersion:
        fail(
            ErrorCategory.VALIDATION,
            f"Invalid semantic version: {value}.",
            operation=operation,
            resource="agent version",
            remediation="Use a semantic version such as 1.2.3.",
        )


_EXACT_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?$")


def _validate_exact_version(value: str, *, operation: str, resource: str) -> str:
    """Pinned and looked-up versions are matched exactly, so keep them exactly as written.

    Unlike ``_validate_version``, this never normalizes: PEP 440 would turn
    ``1.2.0-beta.1`` into ``1.2.0b1``, which names no registry version.
    """
    if not _EXACT_VERSION_RE.match(value):
        fail(
            ErrorCategory.VALIDATION,
            f"Invalid {resource}: {value}.",
            operation=operation,
            resource=resource,
            remediation="Use an exact version such as 1.2.0.",
        )
    return value


def _validate_harnesses(values: list[str], *, operation: str) -> list[str]:
    invalid = [value for value in values if value not in VALID_HARNESSES]
    if invalid:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown harness: {invalid[0]}.",
            operation=operation,
            resource="agent harnesses",
            remediation=f"Choose from: {', '.join(VALID_HARNESSES)}.",
        )
    return values


def _validate_component_id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        fail(
            ErrorCategory.VALIDATION,
            "Component ID must be a UUID.",
            operation="Add agent component",
            resource="agent component",
            remediation="Copy the component ID from a Registry list JSON result.",
        )


def _dump_agent_yaml(data: dict) -> str:
    return yaml.safe_dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)


def _load_json_file(path_value: str, *, operation: str):
    path = Path(path_value)
    if not path.is_file():
        fail(
            ErrorCategory.NOT_FOUND,
            f"JSON file not found: {path}.",
            operation=operation,
            resource=str(path),
            remediation="Check the path and retry.",
        )
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError as error:
        fail(
            ErrorCategory.VALIDATION,
            f"Could not read JSON file: {path}.",
            operation=operation,
            resource=str(path),
            remediation="Provide a valid UTF-8 JSON file.",
            detail=repr(error),
        )
    except OSError as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            f"Could not read JSON file: {path}.",
            operation=operation,
            resource=str(path),
            remediation="Check file permissions and retry.",
            detail=repr(error),
        )
    try:
        return _json.loads(text)
    except (UnicodeError, _json.JSONDecodeError) as error:
        fail(
            ErrorCategory.VALIDATION,
            f"Could not read JSON file: {path}.",
            operation=operation,
            resource=str(path),
            remediation="Provide a valid UTF-8 JSON file.",
            detail=repr(error),
        )


# ── Agent authoring helpers ────────────────────────────────
def _load_agent_yaml(directory: Path, *, operation: str = "Read agent definition") -> dict:
    path = directory / YAML_FILE
    if not path.is_file():
        fail(
            ErrorCategory.NOT_FOUND,
            f"Agent definition not found: {path}.",
            operation=operation,
            resource=str(path),
            remediation="Run `observal agent init` or pass the directory containing observal-agent.yaml.",
        )
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError as error:
        fail(
            ErrorCategory.VALIDATION,
            f"Could not read agent definition: {path}.",
            operation=operation,
            resource=str(path),
            remediation="Provide a valid UTF-8 YAML file.",
            detail=repr(error),
        )
    except OSError as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            f"Could not read agent definition: {path}.",
            operation=operation,
            resource=str(path),
            remediation="Check file permissions and retry.",
            detail=repr(error),
        )
    try:
        data = yaml.safe_load(text)
    except (UnicodeError, yaml.YAMLError) as error:
        fail(
            ErrorCategory.VALIDATION,
            f"Could not read agent definition: {path}.",
            operation=operation,
            resource=str(path),
            remediation="Fix the YAML file and retry.",
            detail=repr(error),
        )
    if not isinstance(data, dict):
        fail(
            ErrorCategory.VALIDATION,
            "Agent definition must be a YAML mapping.",
            operation=operation,
            resource=str(path),
            remediation="Replace the file with a valid observal-agent.yaml mapping.",
        )
    return data


def _validate_agent_definition(data: dict, *, operation: str) -> dict:
    name = data.get("name")
    error = _validate_name(name) if isinstance(name, str) else "Agent name is required."
    if error:
        fail(
            ErrorCategory.VALIDATION,
            error,
            operation=operation,
            resource="agent name",
            remediation="Use lowercase letters, digits, hyphens, or underscores.",
        )
    data["version"] = _validate_version(str(data.get("version") or "1.0.0"), operation=operation)
    harnesses = data.get("supported_harnesses", [])
    if not isinstance(harnesses, list) or not all(isinstance(value, str) for value in harnesses):
        fail(
            ErrorCategory.VALIDATION,
            "Agent supported_harnesses must be a list of names.",
            operation=operation,
            resource="agent harnesses",
            remediation="Use a YAML list of registered harness names.",
        )
    data["supported_harnesses"] = _validate_harnesses(harnesses, operation=operation)
    if not isinstance(data.get("components", []), list):
        fail(
            ErrorCategory.VALIDATION,
            "Agent components must be a list.",
            operation=operation,
            resource="agent components",
            remediation="Use a YAML list of component reference objects.",
        )
    return data


def _save_agent_yaml(
    directory: Path, data: dict, *, operation: str = "Write agent definition", overwrite: bool = True
) -> Path:
    path = directory / YAML_FILE
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as file:
            temporary = Path(file.name)
            file.write(_dump_agent_yaml(data))
        if overwrite:
            temporary.replace(path)
        else:
            # Exclusive, atomic creation: an intervening writer cannot lose
            # its file between fork's preflight and this local scaffold write.
            path.hardlink_to(temporary)
            temporary.unlink()
    except OSError as error:
        if temporary:
            temporary.unlink(missing_ok=True)
        fail(
            ErrorCategory.UNAVAILABLE,
            f"Could not write agent definition: {path}.",
            operation=operation,
            resource=str(path),
            remediation="Check directory permissions and available disk space.",
            detail=repr(error),
        )
    return path


agent_app = typer.Typer(
    help=(
        "Agent registry commands\n\n"
        "Examples:\n"
        "  observal agent list\n"
        "  observal agent show alice/my-agent\n"
        "  observal agent pull alice/my-agent --harness claude-code"
    )
)


@agent_app.command(name="create")
def agent_create(
    from_file: str | None = typer.Option(None, "--from-file", "-f", help="Create from JSON file"),
    name: str | None = typer.Option(None, "--name", "-n", help="Agent name (lowercase, hyphens, underscores)"),
    version: str | None = typer.Option(None, "--version", "-v", help="Version (semver, e.g. 1.0.0)"),
    description: str | None = typer.Option(None, "--description", "-d", help="Short description"),
    prompt: str | None = typer.Option(None, "--prompt", "-p", help="System prompt text"),
    prompt_file: str | None = typer.Option(None, "--prompt-file", help="Read system prompt from a file"),
    model_name: str | None = typer.Option(None, "--model", "-m", help="Model name (e.g. claude-sonnet-4)"),
    supported_harnesses: list[str] | None = typer.Option(
        None, "--harness", help="Supported harnesses (repeat for multiple)"
    ),
    team: str | None = typer.Option(None, "--team", help="Teamspace UUID or handle"),
    visibility: str | None = typer.Option(None, "--visibility", help="Visibility: public or team"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Create a new agent (interactive wizard, from file, or via flags).

    Three modes:
      1. --from-file: load a complete JSON definition
      2. --name + --prompt (or --prompt-file): non-interactive flag-based creation
      3. No flags: interactive wizard

    Examples:
      observal agent create --from-file agent.json
      observal agent create --name my-agent --prompt "You are..." --model claude-sonnet-4
      observal agent create --name my-agent --prompt-file ./PROMPT.md --model claude-sonnet-4 --harness kiro --harness claude-code
    """
    optic.trace("from_file={}", from_file)
    if output == "json" and not (from_file or name or prompt or prompt_file):
        fail(
            ErrorCategory.VALIDATION,
            "JSON mode cannot run the interactive agent builder.",
            operation="Create agent",
            resource="agent definition",
            remediation="Provide --from-file, or provide --name with --prompt or --prompt-file.",
        )

    # ── Path A: From JSON file ───────────────────────────────
    if from_file:
        payload = _load_json_file(from_file, operation="Create agent")
        if not isinstance(payload, dict):
            fail(
                ErrorCategory.VALIDATION,
                "Agent JSON must be an object.",
                operation="Create agent",
                resource=from_file,
                remediation="Provide one complete agent definition object.",
            )
        if payload.get("version"):
            payload["version"] = _validate_version(str(payload["version"]), operation="Create agent")
        if payload.get("supported_harnesses"):
            payload["supported_harnesses"] = _validate_harnesses(
                list(payload["supported_harnesses"]), operation="Create agent"
            )
        if team or visibility:
            client.add_publish_target(payload, team, visibility)
        with _progress(output, "Creating agent..."):
            result = client.post("/api/v1/agents", payload)
        if output == "json":
            output_json(result)
            return
        status = result.get("status", "pending")
        rprint(f"[green]✓ Agent submitted for review![/green] ID: [bold]{esc(result['id'])}[/bold]")
        rprint(f"[yellow]Status: {esc(status)} - an admin must approve it before it becomes visible.[/yellow]")
        return

    # ── Path B: From flags (non-interactive) ─────────────────
    if name or prompt or prompt_file:
        # Resolve prompt from file if provided
        _prompt = prompt or ""
        if prompt_file:
            from pathlib import Path as _Path

            pf = _Path(prompt_file)
            if not pf.is_file():
                fail(
                    ErrorCategory.NOT_FOUND,
                    f"Prompt file not found: {prompt_file}.",
                    operation="Create agent",
                    resource=str(pf),
                    remediation="Check --prompt-file or provide --prompt directly.",
                )
            try:
                _prompt = pf.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                fail(
                    ErrorCategory.UNAVAILABLE,
                    f"Could not read prompt file: {prompt_file}.",
                    operation="Create agent",
                    resource=str(pf),
                    remediation="Provide a readable UTF-8 prompt file.",
                    detail=repr(error),
                )

        # Validate required fields
        if not name:
            fail(
                ErrorCategory.VALIDATION,
                "--name is required with --prompt or --prompt-file.",
                operation="Create agent",
                resource="agent name",
                remediation="Add --name with a lowercase agent name.",
            )
        _name = _slugify(name)
        err = _validate_name(_name)
        if err:
            fail(
                ErrorCategory.VALIDATION,
                err,
                operation="Create agent",
                resource="agent name",
                remediation="Use lowercase letters, digits, hyphens, or underscores.",
            )
        if not _prompt:
            fail(
                ErrorCategory.VALIDATION,
                "--prompt or --prompt-file is required.",
                operation="Create agent",
                resource="agent prompt",
                remediation="Provide non-empty prompt content.",
            )

        normalized_version = _validate_version(version or "1.0.0", operation="Create agent")
        normalized_harnesses = _validate_harnesses(supported_harnesses or [], operation="Create agent")

        # Default model
        _model = model_name or "claude-sonnet-4"

        # Resolve owner from whoami
        whoami = client.get("/api/v1/auth/whoami")
        _owner = whoami.get("username") or whoami.get("email", "unknown")

        payload = {
            "name": _name,
            "version": normalized_version,
            "description": description or "",
            "owner": _owner,
            "prompt": _prompt,
            "model_name": _model,
            "supported_harnesses": normalized_harnesses,
            "components": [],
        }

        client.add_publish_target(payload, team, visibility)
        with _progress(output, "Creating agent..."):
            result = client.post("/api/v1/agents", payload)
        if output == "json":
            output_json(result)
            return
        status = result.get("status", "pending")
        rprint(f"[green]✓ Agent created![/green] ID: [bold]{esc(result['id'])}[/bold]")
        rprint(f"[dim]Status: {esc(status)}[/dim]")
        if _name != name:
            rprint(f"[dim]Name slugified: {name} → {_name}[/dim]")
        return

    # ── Path C: Interactive wizard ───────────────────────────

    rprint("\n[bold cyan]Agent Builder[/bold cyan]\n")

    # ── Phase 1: Basics ─────────────────────────────────────
    rprint("[bold]1. Basics[/bold]")
    raw_name = text_input("  Agent name")
    name = _slugify(raw_name)
    if name != raw_name:
        rprint(f"  [dim]→ Slugified to:[/dim] [bold]{name}[/bold]")
    err = _validate_name(name)
    if err:
        fail(
            ErrorCategory.VALIDATION,
            err,
            operation="Create agent",
            resource="agent name",
            remediation="Use lowercase letters, digits, hyphens, or underscores.",
        )

    description = text_input("  Description")
    version = _validate_version(text_input("  Version", default="1.0.0"), operation="Create agent")
    model_name = select_one("  Model", _MODEL_CHOICES, default="claude-sonnet-4")

    # ── Phase 2: Components ──────────────────────────────────
    rprint("\n[bold]2. Components[/bold]")
    components: list[dict] = []

    with spinner("Fetching registry..."):
        registry_data: dict[str, list[dict]] = {}
        for ctype in ("mcp", "skill", "hook", "prompt", "sandbox"):
            registry_data[ctype] = _fetch_registry_items(ctype)

    for ctype in ("mcp", "skill", "hook", "prompt", "sandbox"):
        items = registry_data[ctype]
        if not items:
            rprint(f"  [dim]No {ctype}s available - skipping.[/dim]")
            continue

        choices = [f"{item['name']}  [dim]({str(item['id'])[:8]})[/dim]" for item in items]
        selected = select_many(f"  Select {ctype}s", choices, defaults=[])

        for sel in selected:
            # Match back to item by prefix (name part before the dim ID)
            sel_name = sel.split("  [dim]")[0].strip()
            match = next((item for item in items if item["name"] == sel_name), None)
            if match:
                components.append({"component_type": ctype, "component_id": str(match["id"])})

    # ── Phase 3: harnesses ────────────────────────────────────────
    rprint("\n[bold]3. Supported harnesses[/bold]")
    supported_harnesses = select_many("  harnesses", list(VALID_HARNESSES), defaults=list(VALID_HARNESSES))

    # ── Phase 4: Goal Template ───────────────────────────────
    rprint("\n[bold]4. Goal Template[/bold]")
    goal_desc = text_input("  Goal description", default=description)
    sections = []
    while True:
        sec_name = text_input("  Section name (or 'done' to finish)")
        if sec_name.lower() == "done":
            break
        sec_desc = text_input(f"    Description for '{sec_name}'", default="")
        sections.append({"name": sec_name, "description": sec_desc})

    if not sections:
        sections = [{"name": "default", "description": goal_desc}]
        rprint("  [dim]Using default section.[/dim]")

    # ── Phase 5: Optional Details ────────────────────────────
    rprint("\n[bold]5. Optional Details[/bold]")
    prompt_text = text_input("  System prompt (optional)", default="")
    max_tokens = text_input("  Max tokens", default="4096")
    temperature = text_input("  Temperature", default="0.2")
    model_cfg = {"max_tokens": int(max_tokens), "temperature": float(temperature)}

    # ── Phase 6: Review & Confirm ────────────────────────────
    component_summary = (
        ", ".join(
            f"{sum(1 for c in components if c['component_type'] == t)} {t}s"
            for t in ("mcp", "skill", "hook", "prompt", "sandbox")
            if any(c["component_type"] == t for c in components)
        )
        or "none"
    )

    review = (
        f"[bold]{name}[/bold] v{version}  |  Model: [cyan]{model_name}[/cyan]\n"
        f"Components: {component_summary}\n"
        f"harnesses: {', '.join(supported_harnesses)}\n"
        f"Goal: {len(sections)} section(s)"
    )
    console.print(Panel(review, title="Review", border_style="green"))

    if not typer.confirm("\nSubmit this agent for review?", default=True):
        rprint("[yellow]Aborted.[/yellow]")
        raise typer.Exit(0)

    whoami = client.get("/api/v1/auth/whoami")
    owner = whoami.get("username") or whoami.get("email", "unknown")
    payload = {
        "name": name,
        "version": version,
        "description": description,
        "owner": owner,
        "prompt": prompt_text,
        "model_name": model_name,
        "model_config_json": model_cfg,
        "supported_harnesses": supported_harnesses,
        "components": components,
    }
    client.add_publish_target(payload, team, visibility)
    with spinner("Creating agent..."):
        result = client.post("/api/v1/agents", payload)
    status = result.get("status", "pending")
    rprint(f"\n[green]✓ Agent submitted for review![/green] ID: [bold]{esc(result['id'])}[/bold]")
    rprint(f"[yellow]Status: {esc(status)} - an admin must approve it before it becomes visible.[/yellow]")


@agent_app.command(name="bulk-create")
def agent_bulk_create(
    file_path: str = typer.Option(..., "--from-file", help="JSON file with agent definitions"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without creating"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Bulk-create agents from a JSON file.

    Accepts a JSON file containing an array of agent definitions, or an
    object with an "agents" key. Shows a preview table before creating.
    Use --dry-run to validate without actually creating agents.

    Examples:
      observal agent bulk-create --from-file agents.json
      observal agent bulk-create --from-file agents.json --dry-run
      observal agent bulk-create --from-file agents.json --yes
    """
    raw = _load_json_file(file_path, operation="Bulk create agents")

    # Accept {"agents": [...]} or bare [...]
    if isinstance(raw, list):
        agents = raw
    elif isinstance(raw, dict) and "agents" in raw:
        agents = raw["agents"]
    else:
        fail(
            ErrorCategory.VALIDATION,
            'Agent JSON must be {"agents": [...]} or a bare array.',
            operation="Bulk create agents",
            resource=file_path,
            remediation="Provide an array of agent definition objects.",
        )

    if not agents:
        fail(
            ErrorCategory.VALIDATION,
            "Agent JSON contains no agents.",
            operation="Bulk create agents",
            resource=file_path,
            remediation="Add at least one agent definition.",
        )
    if not all(isinstance(item, dict) for item in agents):
        fail(
            ErrorCategory.VALIDATION,
            "Every bulk agent entry must be an object.",
            operation="Bulk create agents",
            resource=file_path,
            remediation="Replace scalar or array entries with agent definition objects.",
        )
    if len(agents) > 50:
        fail(
            ErrorCategory.VALIDATION,
            "Bulk agent files may contain at most 50 entries.",
            operation="Bulk create agents",
            resource=file_path,
            remediation="Split the input into files of 50 entries or fewer.",
        )

    identities: dict[str, int] = {}
    for index, item in enumerate(agents, 1):
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            fail(
                ErrorCategory.VALIDATION,
                f"Bulk agent entry {index} requires a non-empty string name.",
                operation="Bulk create agents",
                resource=file_path,
                remediation="Add a name to every agent definition.",
            )
        if len(name) > 255:
            fail(
                ErrorCategory.VALIDATION,
                f"Bulk agent entry {index} has a name longer than 255 characters.",
                operation="Bulk create agents",
                resource=file_path,
                remediation="Shorten the agent name and retry.",
            )
        identity = _bulk_agent_slug(name)
        if not identity:
            fail(
                ErrorCategory.VALIDATION,
                f"Bulk agent entry {index} has a name without letters or numbers.",
                operation="Bulk create agents",
                resource=file_path,
                remediation="Use a name containing at least one letter or number.",
            )
        if identity in _RESERVED_AGENT_SLUGS:
            fail(
                ErrorCategory.VALIDATION,
                f"Bulk agent entry {index} resolves to reserved name: {identity}.",
                operation="Bulk create agents",
                resource=file_path,
                remediation="Choose a non-reserved agent name.",
            )
        if identity in identities:
            fail(
                ErrorCategory.VALIDATION,
                f"Bulk agent entries {identities[identity]} and {index} resolve to the same name: {identity}.",
                operation="Bulk create agents",
                resource=file_path,
                remediation="Keep one entry for each canonical agent name.",
            )
        identities[identity] = index

        components = item.get("components", [])
        if not isinstance(components, list):
            fail(
                ErrorCategory.VALIDATION,
                f"Bulk agent entry {index} components must be a list.",
                operation="Bulk create agents",
                resource=file_path,
                remediation="Use a list of component reference objects.",
            )
        for component_index, component in enumerate(components, 1):
            if not isinstance(component, dict):
                fail(
                    ErrorCategory.VALIDATION,
                    f"Bulk agent entry {index} component {component_index} must be an object.",
                    operation="Bulk create agents",
                    resource=file_path,
                    remediation="Replace scalar component references with objects.",
                )
            component_type = component.get("component_type", "mcp")
            if component_type not in VALID_COMPONENT_TYPES or not str(component.get("component_id") or "").strip():
                fail(
                    ErrorCategory.VALIDATION,
                    f"Bulk agent entry {index} component {component_index} is incomplete.",
                    operation="Bulk create agents",
                    resource=file_path,
                    remediation="Provide a valid component_type and component_id.",
                )
        for field in ("version", "description", "owner", "prompt", "model_name"):
            if field in item and not isinstance(item[field], str):
                fail(
                    ErrorCategory.VALIDATION,
                    f"Bulk agent entry {index} field {field} has the wrong type.",
                    operation="Bulk create agents",
                    resource=file_path,
                    remediation=f"Use a JSON string for {field}.",
                )
        if "model_config_json" in item and not isinstance(item["model_config_json"], dict):
            fail(
                ErrorCategory.VALIDATION,
                f"Bulk agent entry {index} field model_config_json has the wrong type.",
                operation="Bulk create agents",
                resource=file_path,
                remediation="Use a JSON object for model_config_json.",
            )
        for field in ("supported_harnesses", "external_mcps"):
            if field in item and not isinstance(item[field], list):
                fail(
                    ErrorCategory.VALIDATION,
                    f"Bulk agent entry {index} field {field} has the wrong type.",
                    operation="Bulk create agents",
                    resource=file_path,
                    remediation=f"Use a JSON list for {field}.",
                )
        if "supported_harnesses" in item and not all(isinstance(value, str) for value in item["supported_harnesses"]):
            fail(
                ErrorCategory.VALIDATION,
                f"Bulk agent entry {index} supported_harnesses must contain strings.",
                operation="Bulk create agents",
                resource=file_path,
                remediation="Replace non-string harness values.",
            )
        if "external_mcps" in item and not all(isinstance(value, dict) for value in item["external_mcps"]):
            fail(
                ErrorCategory.VALIDATION,
                f"Bulk agent entry {index} external_mcps must contain objects.",
                operation="Bulk create agents",
                resource=file_path,
                remediation="Replace non-object external MCP values.",
            )

    if output == "json" and not (dry_run or yes):
        fail(
            ErrorCategory.VALIDATION,
            "JSON mode cannot prompt before bulk creation.",
            operation="Bulk create agents",
            resource=file_path,
            remediation="Add --yes or use --dry-run.",
        )

    # ── Preview table ────────────────────────────────────────
    preview = Table(title=f"Agents to create ({len(agents)})", show_lines=False, padding=(0, 1))
    preview.add_column("#", style="dim", width=3)
    preview.add_column("Name", style="bold cyan", no_wrap=True)
    preview.add_column("Version", style="green")
    preview.add_column("Components")
    preview.add_column("Model")
    for i, ag in enumerate(agents, 1):
        comp_count = str(len(ag.get("components", [])))
        preview.add_row(
            str(i),
            esc(ag.get("name", "unnamed")),
            esc(ag.get("version", "1.0.0")),
            comp_count,
            esc(ag.get("model_name", "claude-sonnet-4")),
        )
    if output != "json":
        console.print(preview)

    # ── Dry-run mode ─────────────────────────────────────────
    if dry_run:
        with _progress(output, "Running dry-run..."):
            result = client.post("/api/v1/bulk/agents", {"agents": agents, "dry_run": True})
        result = dict(result)
        result["partial"] = result.get("errors", 0) > 0
        if output == "json":
            output_json(result)
            if result["partial"]:
                exit_partial()
            return

        results_table = Table(title="Dry-run results", show_lines=False, padding=(0, 1))
        results_table.add_column("#", style="dim", width=3)
        results_table.add_column("Name", style="bold cyan", no_wrap=True)
        results_table.add_column("Status")
        results_table.add_column("Error", style="red")
        for i, item in enumerate(result.get("results", []), 1):
            status = item.get("status", "")
            badge = (
                "[green]created[/green]"
                if status == "created"
                else ("[yellow]skipped[/yellow]" if status == "skipped" else f"[red]{esc(status)}[/red]")
            )
            results_table.add_row(str(i), esc(item.get("name", "")), badge, esc(item.get("error", "") or ""))
        console.print(results_table)

        rprint(
            f"\n[bold]Summary:[/bold] {result.get('created', 0)} would be created, "
            f"{result.get('skipped', 0)} skipped, {result.get('errors', 0)} errors"
        )
        if result["partial"]:
            exit_partial()
        return

    # ── Confirmation ─────────────────────────────────────────
    if not yes and not typer.confirm(f"\nCreate {len(agents)} agents?", default=False):
        rprint("[yellow]Aborted.[/yellow]")
        raise typer.Exit(0)

    # ── Create ───────────────────────────────────────────────
    with _progress(output, "Creating agents..."):
        result = client.post("/api/v1/bulk/agents", {"agents": agents, "dry_run": False})
    result = dict(result)
    result["partial"] = result.get("errors", 0) > 0
    if output == "json":
        output_json(result)
        if result["partial"]:
            exit_partial()
        return

    results_table = Table(title="Bulk create results", show_lines=False, padding=(0, 1))
    results_table.add_column("#", style="dim", width=3)
    results_table.add_column("Name", style="bold cyan", no_wrap=True)
    results_table.add_column("Status")
    results_table.add_column("Agent ID", style="dim")
    results_table.add_column("Error", style="red")
    for i, item in enumerate(result.get("results", []), 1):
        status = item.get("status", "")
        badge = (
            "[green]created[/green]"
            if status == "created"
            else ("[yellow]skipped[/yellow]" if status == "skipped" else f"[red]{esc(status)}[/red]")
        )
        agent_id = f"{str(item['agent_id'])[:8]}…" if item.get("agent_id") else ""
        results_table.add_row(str(i), esc(item.get("name", "")), badge, agent_id, esc(item.get("error", "") or ""))
    console.print(results_table)

    rprint(
        f"\n[green]✓ Bulk create complete![/green] "
        f"{result.get('created', 0)} created, {result.get('skipped', 0)} skipped, "
        f"{result.get('errors', 0)} errors"
    )
    if result["partial"]:
        exit_partial()


@agent_app.command(name="list")
def agent_list(
    search: str | None = typer.Option(None, "--search", "-s"),
    namespace: str | None = typer.Option(None, "--namespace", help="Filter by user or team namespace"),
    team: str | None = typer.Option(None, "--team", help="Only items owned by this teamspace"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Interactive search mode"),
    limit: int = typer.Option(50, "--limit", "-n", min=1, max=200, help="Page size (1-200)"),
    page: int = typer.Option(1, "--page", "-p", min=1, help="Page number (1-indexed)"),
    show_id: bool = typer.Option(False, "--id", help="Include the agent ID column"),
    full_id: bool = typer.Option(False, "--full-id", help="Show full UUID (implies --id)"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """List active agents (paginated).

    Shows approved agents in the registry with pagination support.
    Use --interactive for fuzzy search with arrow-key selection.
    Results are cached locally for numeric shorthand in subsequent commands.

    Examples:
      observal agent list
      observal agent list --search my-agent
      observal agent list --output json
    """
    if interactive and output == "json":
        fail(
            ErrorCategory.VALIDATION,
            "Interactive selection cannot be combined with JSON output.",
            operation="List agents",
            resource="agent registry",
            remediation="Remove --interactive or use table output.",
        )

    params: dict = {"limit": limit, "offset": (page - 1) * limit}
    if search:
        params["search"] = search
    if namespace:
        params["namespace"] = namespace.lstrip("@").lower()
    if team:
        params["team_id"] = client.resolve_team_id(team)

    with _progress(output, "Fetching agents..."):
        data, headers = client.get_with_headers("/api/v1/agents", params=params)

    if interactive and data:

        def _display(item: dict) -> str:
            email = item.get("created_by_email", "")
            suffix = f"  by {email}" if email else ""
            return f"{name_inline(item)}  v{item.get('version', '?')}  {item.get('model_name', '')}{suffix}"

        selected = fuzzy_select(data, _display, label="Select agent")
        if selected:
            agent_show(selected["id"])
        return

    total = int(headers.get("x-total-count", str(len(data))))
    total_pages = max(1, (total + limit - 1) // limit)

    # Preserve only this agent page for numeric shorthand.
    config.save_last_results(data, "agent")

    if output == "json":
        output_json({"items": data, "total": total, "page": page, "page_size": limit})
        return

    if not data:
        if total == 0:
            rprint("[dim]No agents found.[/dim]")
        else:
            rprint(f"[yellow]Page {page} is empty. Total agents: {total} (last page: {total_pages})[/yellow]")
        return

    include_id = show_id or full_id
    table = Table(
        title=f"Agents (page {page} of {total_pages} · {len(data)} of {total})",
        show_lines=False,
        padding=(0, 1),
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("Name", style="bold cyan", no_wrap=True)
    table.add_column("Version", style="green")
    table.add_column("Model")
    table.add_column("Namespace", style="dim")
    if include_id:
        table.add_column("ID", style="dim", no_wrap=full_id)
    for i, item in enumerate(data, 1):
        row = [
            str(i),
            esc(display_name(item)),
            esc(item.get("version", "")),
            esc(item.get("model_name", "")),
            esc(handle(item)),
        ]
        if include_id:
            row.append(str(item["id"]) if full_id else f"{str(item['id'])[:8]}…")
        table.add_row(*row)
    console.print(table)

    # Pagination footer
    if total_pages > 1:
        if page < total_pages:
            rprint(
                f"[dim]Next:[/dim] [bold]observal agent list --page {page + 1}[/bold]"
                + (f" --limit {limit}" if limit != 50 else "")
            )
        else:
            rprint("[dim]End of results.[/dim]")


@agent_app.command(name="my")
def agent_my(
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """List your own agents (all statuses).

    Shows all agents you created, including pending, approved, rejected,
    and archived ones. Useful for checking the review status of your
    submissions.

    Examples:
      observal agent my
      observal agent my --output json
    """
    with _progress(output, "Fetching your agents..."):
        data = client.get("/api/v1/agents/my")
    config.save_last_results(data, "agent")
    if output == "json":
        output_json(data)
        return
    if not data:
        rprint("[dim]You have no agents.[/dim]")
        return
    table = Table(title=f"My Agents ({len(data)})", show_lines=False, padding=(0, 1))
    table.add_column("#", style="dim", width=3)
    table.add_column("Name", style="bold cyan", no_wrap=True)
    table.add_column("Version", style="green")
    table.add_column("Model")
    table.add_column("Namespace", style="dim")
    table.add_column("Status")
    table.add_column("ID", style="dim", max_width=12)
    for i, item in enumerate(data, 1):
        table.add_row(
            str(i),
            esc(display_name(item)),
            esc(item.get("version", "")),
            esc(item.get("model_name", "")),
            esc(handle(item)),
            status_badge(item.get("status", "")),
            str(item["id"])[:8] + "…",
        )
    console.print(table)


@agent_app.command(name="show")
def agent_show(
    agent_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Show full agent details.

    Displays the complete agent profile: name, version, model, owner,
    description, supported harnesses, linked MCP servers, and metadata.
    Accepts a UUID, agent name, numeric row number from the last list,
    or an @alias.

    Examples:
      observal agent show my-agent
      observal agent show @myalias
      observal agent show alice/my-agent --output json
    """
    resolved = client.resolve_registry_reference("agent", agent_id)
    with _progress(output):
        item = client.get(f"/api/v1/agents/{resolved}")

    if output == "json":
        output_json(item)
        return

    console.print(
        kv_panel(
            f"{display_name(item)} v{item.get('version', '?')}",
            [
                ("Status", listing_status(item)),
                ("Model", f"[bold]{esc(item.get('model_name', 'N/A'))}[/bold]"),
                ("Namespace", esc(handle(item) or "N/A")),
                ("Created By", esc(item.get("created_by_username") or item.get("created_by_email", ""))),
                ("Description", esc(item.get("description", ""))),
                ("harnesses", ide_tags([esc(value) for value in item.get("supported_harnesses", [])])),
                ("Created", relative_time(item.get("created_at"))),
                *fork_detail_rows(item),
                ("ID", f"[dim]{item['id']}[/dim]"),
            ],
            border_style="magenta",
        )
    )

    # MCP links
    if item.get("mcp_links"):
        rprint("\n[bold]Linked MCP Servers:[/bold]")
        for link in item["mcp_links"]:
            rprint(
                f"  [cyan]•[/cyan] {esc(link.get('mcp_name', ''))} [dim]({esc(link.get('mcp_listing_id', ''))})[/dim]"
            )

    # Success criteria
    sc = item.get("success_criteria")
    if sc and sc.get("intended_purpose"):
        rprint("\n[bold]Success Criteria:[/bold]")
        rprint(f"  [cyan]Purpose:[/cyan] {esc(sc['intended_purpose'])}")
        metrics = sc.get("success_metrics") or []
        if metrics:
            rprint("  [cyan]Metrics:[/cyan]")
            for m in metrics:
                rprint(f"    • {esc(m['name'])} : target {esc(m['target'])} (via {esc(m['measurement'])})")
        if sc.get("evaluation_notes"):
            rprint(f"  [cyan]Notes:[/cyan] {esc(sc['evaluation_notes'])}")


def _fork_agent_yaml(item: dict) -> dict:
    """Build editable authoring YAML from the fork response, not its install snapshot."""
    components = []
    for link in item.get("component_links") or []:
        ref = {"component_type": link["component_type"], "component_id": link["component_id"]}
        if link.get("version_ref"):
            ref["version"] = link["version_ref"]
        if link.get("config_override") is not None:
            ref["config_override"] = link["config_override"]
        components.append(ref)
    return {
        "agent_id": item["id"],
        "name": item["slug"],
        "version": item["version"],
        "description": item.get("description") or "",
        "owner": item.get("owner") or "",
        "category": item.get("category"),
        "model_name": item.get("model_name") or "claude-sonnet-4",
        "model_config_json": item.get("model_config_json") or {},
        "models_by_harness": item.get("models_by_harness") or {},
        "prompt": item.get("prompt") or "",
        "supported_harnesses": item.get("supported_harnesses") or [],
        "components": components,
        "external_mcps": item.get("external_mcps") or [],
        "success_criteria": item.get("success_criteria"),
    }


@agent_app.command(name="fork")
def agent_fork(
    source: str = typer.Argument(..., help="UUID, namespace/slug, list row, or @alias"),
    name: str | None = typer.Option(None, "--name", help="New draft name (defaults to the source name)"),
    version: str | None = typer.Option(None, "--version", help="Approved source version to copy"),
    new_version: str | None = typer.Option(None, "--new-version", help="Version of the new draft"),
    team: str | None = typer.Option(None, "--team", help="Target teamspace handle or UUID"),
    visibility: str | None = typer.Option(None, "--visibility", help="public or team"),
    directory: str | None = typer.Option(None, "--dir", "-d", help="Create an editable observal-agent.yaml here"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Fork an approved agent release into your own editable draft.

    Examples:
      observal agent fork acme/pr-reviewer
      observal agent fork acme/pr-reviewer --name my-reviewer --team payments --visibility team
      observal agent fork acme/pr-reviewer --dir ./my-reviewer --output json
    """
    target_path = Path(directory) / YAML_FILE if directory is not None else None
    if target_path is not None and (target_path.exists() or target_path.is_symlink()):
        fail(
            ErrorCategory.CONFLICT,
            f"Agent definition already exists: {target_path}.",
            operation="Fork agent",
            resource=str(target_path),
            remediation="Choose an empty directory with --dir; existing files are not overwritten.",
        )
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
    resolved = client.resolve_registry_reference("agent", source)
    with _progress(output, "Forking agent..."):
        result = client.post(f"/api/v1/agents/{resolved}/fork", json_data=payload)
    if directory is not None:
        try:
            saved = _save_agent_yaml(
                Path(directory), _fork_agent_yaml(result), operation="Scaffold forked agent", overwrite=False
            )
        except CliError as error:
            fail(
                error.category,
                "The fork was created, but its local YAML could not be written.",
                operation="Scaffold forked agent",
                resource=str(target_path),
                remediation=f"Inspect the existing draft with `observal agent show {result['id']}`; do not fork again.",
                detail=error.detail,
                result={"id": result["id"], "qualified_name": result.get("qualified_name"), "partial": True},
            )
        result = {**result, "yaml_path": str(saved)}
    if output == "json":
        output_json(result)
        return
    console.print(
        kv_panel(
            f"Forked agent: {esc(result.get('qualified_name') or result.get('name', ''))}",
            [("Status", esc(result.get("status", "draft"))), *fork_detail_rows(result), ("ID", esc(result["id"]))],
            border_style="magenta",
        )
    )
    if directory is not None:
        rprint(f"[dim]Next:[/dim] observal agent publish --update --dir {esc(directory)}")
    rprint(f"[dim]Submit:[/dim] observal agent publish --submit {esc(result['id'])}")
    for warning in result.get("warnings") or []:
        rprint(f"[yellow]⚠ {esc(warning)}[/yellow]")


@agent_app.command(name="install")
def agent_install(
    agent_id: str = typer.Argument(..., help="Agent ID, name, row number, or @alias"),
    harness: str = typer.Option(..., "--harness", "-i", help="Target harness"),
    raw: bool = typer.Option(False, "--raw", help="Output only the generated config snippet as JSON"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Get install config for an agent.

    Generates the harness-specific configuration needed to use the agent.
    Output includes rules files, MCP configs, skill files, and agent
    files depending on the target harness. Use --raw to pipe JSON directly
    to a file.

    Examples:
      observal agent install my-agent --harness claude-code
      observal agent install my-agent --harness cursor --raw > config.json
      observal agent install @myalias --harness opencode
    """
    harness = _validate_harnesses([harness], operation="Generate agent installation")[0]
    resolved = client.resolve_registry_reference("agent", agent_id)
    with _progress("json" if raw else output, f"Generating {harness} config..."):
        result = client.post_public(f"/api/v1/agents/{resolved}/install", {"harness": harness})

    snippet = result.get("config_snippet", {})
    if raw:
        output_json(snippet)
        return
    if output == "json":
        output_json(result)
        return

    rprint(f"\n[bold]Config for {esc(harness)}:[/bold]\n")

    # Kiro agent file: single JSON to drop in
    agent_profile = snippet.get("agent_profile")
    if agent_profile:
        rprint(f"[bold]Save to:[/bold] {esc(agent_profile['path'])}")
        rprint()
        console.print_json(_json.dumps(agent_profile["content"], indent=2))
        rprint(
            f"\n[dim]Or pipe:[/dim] observal agent install {esc(agent_id)} --harness {esc(harness)} "
            f"--raw | jq .agent_profile.content > {esc(agent_profile['path'])}"
        )
        return

    # Rules file
    rules = snippet.get("agent_profile")
    if rules:
        rprint(f"[bold]Rules file:[/bold] {esc(rules.get('path', ''))}")
        content = rules.get("content", "")
        rprint(f"[dim]{esc(content[:200])}{'...' if len(content) > 200 else ''}[/dim]\n")

    # Skill files
    skills = snippet.get("skills", [])
    if skills:
        rprint(f"[bold]Skill files ({len(skills)}):[/bold]")
        for sf in skills:
            rprint(f"  [green]{esc(sf['path'])}[/green]")
        rprint()

    # MCP config
    mcp_cfg = snippet.get("mcp_config")
    if mcp_cfg:
        path = mcp_cfg.get("path") if isinstance(mcp_cfg, dict) and "path" in mcp_cfg else None
        content = mcp_cfg.get("content", mcp_cfg) if isinstance(mcp_cfg, dict) and "content" in mcp_cfg else mcp_cfg
        if path:
            rprint(f"[bold]MCP config:[/bold] {esc(path)}")
        else:
            rprint("[bold]MCP config:[/bold]")
        console.print_json(_json.dumps(content, indent=2))
        return

    # Fallback
    console.print_json(_json.dumps(snippet, indent=2))


def _archive_agent(agent_id: str, yes: bool, output: OutputMode) -> None:
    if output == "json" and not yes:
        fail(
            ErrorCategory.VALIDATION,
            "JSON mode cannot prompt before archiving an agent.",
            operation="Archive agent",
            resource="agent registry",
            remediation="Add --yes to confirm the archive.",
        )
    resolved = client.resolve_registry_reference("agent", agent_id)
    if not yes:
        with spinner():
            item = client.get(f"/api/v1/agents/{resolved}")
        if not typer.confirm(f"Archive [bold]{esc(item['name'])}[/bold] ({esc(resolved)})?"):
            raise typer.Abort()
    with _progress(output, "Archiving..."):
        result = client.patch(f"/api/v1/agents/{resolved}/archive")
    if output == "json":
        output_json(result)
        return
    rprint("[green]✓ Agent archived[/green]")


@agent_app.command(name="archive")
def agent_archive(
    agent_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Archive an agent.

    Marks the agent as archived. It will no longer appear in public
    listings but can be restored with the unarchive command.

    Examples:
      observal agent archive alice/my-agent
      observal agent archive alice/my-agent --yes
    """
    _archive_agent(agent_id, yes, output)


@agent_app.command(name="delete")
def agent_delete(
    agent_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Archive an agent. Prefer the archive command.

    Examples:
      observal agent delete alice/my-agent
      observal agent delete alice/my-agent --yes
    """
    _archive_agent(agent_id, yes, output)


@agent_app.command(name="unarchive")
def agent_unarchive(
    agent_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Restore an archived agent back to active status.

    Reverses a previous archive (soft delete) operation, making the
    agent visible in public listings again. Prompts for confirmation
    unless --yes is provided.

    Examples:
      observal agent unarchive my-agent
      observal agent unarchive my-agent --yes
      observal agent unarchive a1b2c3d4-...
    """
    if output == "json" and not yes:
        fail(
            ErrorCategory.VALIDATION,
            "JSON mode cannot prompt before restoring an agent.",
            operation="Restore agent",
            resource="agent registry",
            remediation="Add --yes to confirm the restore.",
        )
    resolved = client.resolve_registry_reference("agent", agent_id)
    if not yes:
        with spinner():
            item = client.get(f"/api/v1/agents/{resolved}")
        if not typer.confirm(f"Unarchive [bold]{esc(item['name'])}[/bold] ({esc(resolved)})?"):
            raise typer.Abort()
    with _progress(output, "Restoring..."):
        result = client.patch(f"/api/v1/agents/{resolved}/unarchive")
    if output == "json":
        output_json(result)
        return
    rprint("[green]✓ Agent restored[/green]")


# ═══════════════════════════════════════════════════════════════
# Agent authoring commands (local YAML workflow)
# ═══════════════════════════════════════════════════════════════


_SINCE_UNITS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def _parse_since(value: str, *, operation: str) -> int:
    """Turn ``2h`` / ``3d`` / ``45m`` into seconds."""
    text = (value or "").strip().lower()
    match = re.fullmatch(r"(\d+)([mhdw])", text)
    if not match:
        fail(
            ErrorCategory.VALIDATION,
            f"Invalid --since value: {value}.",
            operation=operation,
            resource="--since",
            remediation="Use a number and unit, e.g. 45m, 2h, 3d, 1w.",
        )
    return int(match.group(1)) * _SINCE_UNITS[match.group(2)]


def _components_from_capabilities(since: str, *, operation: str) -> tuple[list[dict], list[str], list[str]]:
    """Read the capability lock for this directory and shape it into agent components.

    Returns (components, harnesses seen, skipped agent refs). Agents are
    skipped because an Agent cannot nest another Agent; the user is told.
    """
    from datetime import UTC, datetime, timedelta

    from observal_cli import capability_lock

    window = timedelta(seconds=_parse_since(since, operation=operation))
    uses = capability_lock.matching(harness=None, cwd=str(Path.cwd()), since=datetime.now(UTC) - window)
    components: list[dict] = []
    harnesses: list[str] = []
    skipped: list[str] = []
    for use in capability_lock.dedupe_latest(uses):
        if use.harness and use.harness in VALID_HARNESSES and use.harness not in harnesses:
            harnesses.append(use.harness)
        if use.kind == "agent":
            skipped.append(use.native_ref or use.component_id or use.identifier or "unknown")
            continue
        if use.kind not in VALID_COMPONENT_TYPES or not use.component_id:
            continue
        components.append(
            {
                "component_type": use.kind,
                "component_id": use.component_id,
                "native_ref": use.native_ref,
                "version": use.version,
                "identifier": use.identifier,
                "mode": use.mode,
                "used_at": use.ts,
            }
        )
    return components, sorted(harnesses), skipped


@agent_app.command(name="init")
def agent_init(
    directory: str = typer.Option(".", "--dir", "-d", help="Directory to scaffold in"),
    beta: bool = typer.Option(False, "--beta", help="Start at version 0.1.0 (beta)"),
    name: str | None = typer.Option(None, "--name", "-n", help="Agent name"),
    version: str | None = typer.Option(None, "--version", "-v", help="Version"),
    description: str | None = typer.Option(None, "--description", help="Description"),
    model_name: str | None = typer.Option(None, "--model", "-m", help="Model name"),
    prompt: str | None = typer.Option(None, "--prompt", "-p", help="System prompt text"),
    prompt_file: str | None = typer.Option(None, "--prompt-file", help="Read system prompt from a file"),
    supported_harnesses: list[str] | None = typer.Option(None, "--harness", help="Supported harness (repeatable)"),
    from_capabilities: bool = typer.Option(
        False,
        "--from-capabilities",
        help="Pre-fill components from resources used in this directory (the capability lock)",
    ),
    since: str = typer.Option("24h", "--since", help="With --from-capabilities: how far back to look (e.g. 2h, 3d)"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Scaffold an observal-agent.yaml definition file.

    Runs an interactive wizard to collect agent metadata (name, version,
    description, owner, model, system prompt) and writes the result as
    observal-agent.yaml in the target directory. Use --beta to start
    at version 0.1.0 instead of 1.0.0.

    With --from-capabilities, the components list is pre-filled from the
    resources `observal discover use` and the install commands recorded for
    this directory, so a session that worked can be saved as an Agent.

    Examples:
      observal agent init
      observal agent init --dir ./my-agent --beta
      observal agent init --from-capabilities --name pr-review-flow --output json
    """
    dir_path = Path(directory)
    yaml_path = dir_path / YAML_FILE

    flag_mode = any(
        value is not None
        for value in (name, version, description, model_name, prompt, prompt_file, supported_harnesses)
    )
    captured: list[dict] = []
    captured_harnesses: list[str] = []
    skipped_agents: list[str] = []
    if from_capabilities:
        captured, captured_harnesses, skipped_agents = _components_from_capabilities(
            since, operation="Initialize agent definition"
        )
        if not captured and not skipped_agents:
            fail(
                ErrorCategory.NOT_FOUND,
                f"No resources were used in {Path.cwd()} in the last {since}.",
                operation="Initialize agent definition",
                resource="capability lock",
                remediation="Use `observal discover use <identifier>` or install something first, or widen --since.",
            )
        if description is None and output == "json":
            description = f"Assembled from {len(captured)} resource(s) used in {Path.cwd().name}."
        if prompt is None and prompt_file is None and output == "json":
            prompt = "Use the attached components to complete the task."
        if not supported_harnesses and captured_harnesses:
            supported_harnesses = captured_harnesses

    if output == "json" and not flag_mode:
        fail(
            ErrorCategory.VALIDATION,
            "JSON mode cannot run the interactive agent initializer.",
            operation="Initialize agent definition",
            resource=str(yaml_path),
            remediation="Provide --name, --description, and --prompt or --prompt-file.",
        )
    if yaml_path.exists():
        if output == "json":
            fail(
                ErrorCategory.CONFLICT,
                f"Agent definition already exists: {yaml_path}.",
                operation="Initialize agent definition",
                resource=str(yaml_path),
                remediation="Choose an empty directory or remove the existing file deliberately.",
            )
        if not typer.confirm(f"{YAML_FILE} already exists in {dir_path}. Overwrite?"):
            rprint("[yellow]Aborted.[/yellow]")
            raise typer.Abort()

    default_version = "0.1.0" if beta else "1.0.0"
    if flag_mode:
        if not name or not description or not (prompt or prompt_file):
            fail(
                ErrorCategory.VALIDATION,
                "--name, --description, and --prompt or --prompt-file are required.",
                operation="Initialize agent definition",
                resource="agent definition",
                remediation="Provide every required non-interactive field.",
            )
        raw_name = name
        if prompt_file:
            prompt_path = Path(prompt_file)
            if not prompt_path.is_file():
                fail(
                    ErrorCategory.NOT_FOUND,
                    f"Prompt file not found: {prompt_file}.",
                    operation="Initialize agent definition",
                    resource=str(prompt_path),
                    remediation="Check --prompt-file or provide --prompt directly.",
                )
            try:
                prompt_text = prompt_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                fail(
                    ErrorCategory.UNAVAILABLE,
                    f"Could not read prompt file: {prompt_file}.",
                    operation="Initialize agent definition",
                    resource=str(prompt_path),
                    remediation="Provide a readable UTF-8 prompt file.",
                    detail=repr(error),
                )
        else:
            prompt_text = prompt or ""
        harnesses = _validate_harnesses(
            supported_harnesses or list(VALID_HARNESSES), operation="Initialize agent definition"
        )
    else:
        raw_name = text_input("Agent name")
        version = text_input("Version", default=default_version)
        description = text_input("Description")
        model_name = text_input("Model name", default="claude-sonnet-4")
        prompt_text = text_input("System prompt")
        harnesses = _validate_harnesses(
            supported_harnesses or list(VALID_HARNESSES), operation="Initialize agent definition"
        )

    name = _slugify(raw_name)
    if name != raw_name:
        rprint(f"  [dim]→ Slugified to:[/dim] [bold]{name}[/bold]")
    err = _validate_name(name)
    if err:
        fail(
            ErrorCategory.VALIDATION,
            err,
            operation="Initialize agent definition",
            resource="agent name",
            remediation="Use lowercase letters, digits, hyphens, or underscores.",
        )
    version = _validate_version(version or default_version, operation="Initialize agent definition")

    owner = config.load().get("username", "") or "unknown"

    data = {
        "name": name,
        "version": version,
        "description": description,
        "owner": owner,
        "model_name": model_name or "claude-sonnet-4",
        "model_config_json": {},
        # Optional per-harness model overrides, e.g. {"kiro": "claude-haiku-4-5"}.
        # Leave empty to use model_name everywhere that accepts a model choice.
        "models_by_harness": {},
        "prompt": prompt_text,
        "supported_harnesses": harnesses,
        "components": [],
        "external_mcps": [],
        "success_criteria": None,
    }

    if captured:
        data["components"] = [
            {"component_type": item["component_type"], "component_id": item["component_id"]} for item in captured
        ]

    saved_path = _save_agent_yaml(dir_path, data, operation="Initialize agent definition")
    if output == "json":
        result: dict = {"path": str(saved_path), "agent": data}
        if from_capabilities:
            result["from_capabilities"] = {"components": captured, "skipped_agents": skipped_agents, "since": since}
        output_json(result)
        return
    rprint(f"[green]✓ Created {esc(yaml_path)}[/green]")
    if captured:
        rprint(
            f"  [dim]Pre-filled {len(captured)} component(s) from resources used here in the last {esc(since)}:[/dim]"
        )
        for item in captured:
            rprint(f"    • {esc(item['component_type'])} {esc(item.get('native_ref') or item['component_id'])}")
    for ref in skipped_agents:
        rprint(
            f"  [yellow]Skipped agent {esc(ref)}: an Agent cannot contain another Agent; add its components instead.[/yellow]"
        )


@agent_app.command(name="add")
def agent_add(
    component_type: str = typer.Argument(..., help="Component type: mcp, skill, hook, prompt, sandbox"),
    component_id: str = typer.Argument(..., help="Component ID (UUID)"),
    version: str | None = typer.Option(
        None, "--version", "-V", help="Pin an exact component version (default: keep or use latest approved)"
    ),
    directory: str = typer.Option(".", "--dir", "-d", help="Directory containing observal-agent.yaml"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Add a component reference to observal-agent.yaml.

    Appends a component entry to the components list in your local
    observal-agent.yaml file. The component is referenced by type and
    UUID. Duplicates are rejected. With --version the agent pins that exact
    release; without it the agent keeps its current pin, or pins the latest
    approved release when it first gains the component.

    Examples:
      observal agent add mcp a1b2c3d4-e5f6-7890-abcd-ef1234567890
      observal agent add skill b2c3d4e5-f6a7-8901-bcde-f12345678901 --version 1.2.0
      observal agent add hook c3d4e5f6-... --dir ./my-agent
    """
    component_type = component_type.strip().lower()
    if component_type not in VALID_COMPONENT_TYPES:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown agent component type: {component_type}.",
            operation="Add agent component",
            resource="agent component type",
            remediation=f"Choose from: {', '.join(sorted(VALID_COMPONENT_TYPES))}.",
        )
    component_id = _validate_component_id(component_id)
    if version is not None:
        version = _validate_exact_version(version, operation="Add agent component", resource="component version")

    dir_path = Path(directory)
    data = _load_agent_yaml(dir_path, operation="Add agent component")

    components = data.get("components", [])
    if not isinstance(components, list):
        fail(
            ErrorCategory.VALIDATION,
            "Agent components must be a list.",
            operation="Add agent component",
            resource=str(dir_path / YAML_FILE),
            remediation="Fix the components field and retry.",
        )
    for comp in components:
        if comp.get("component_type") == component_type and comp.get("component_id") == component_id:
            fail(
                ErrorCategory.CONFLICT,
                f"Component already exists: {component_type}:{component_id}.",
                operation="Add agent component",
                resource=str(dir_path / YAML_FILE),
                remediation="Choose a different component or leave the definition unchanged.",
            )

    entry = {"component_type": component_type, "component_id": component_id}
    if version is not None:
        entry["version"] = version
    components.append(entry)
    data["components"] = components
    path = _save_agent_yaml(dir_path, data, operation="Add agent component")
    result = {"path": str(path), "component": components[-1]}
    if output == "json":
        output_json(result)
        return
    rprint(f"[green]✓ Added {esc(component_type)}:{esc(component_id)}[/green]")


@agent_app.command(name="build")
def agent_build(
    directory: str = typer.Option(".", "--dir", "-d", help="Directory containing observal-agent.yaml"),
    team: str | None = typer.Option(None, "--team", help="Validate private components for this teamspace"),
    visibility: str | None = typer.Option(None, "--visibility", help="Agent visibility: public or team"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Validate agent definition against the server (dry-run).

    Reads observal-agent.yaml and checks each referenced component
    against the registry API to confirm it exists and is accessible.
    Exits with code 1 if any component fails validation.

    Examples:
      observal agent build
      observal agent build --dir ./my-agent
    """
    dir_path = Path(directory)
    data = _validate_agent_definition(
        _load_agent_yaml(dir_path, operation="Validate agent"), operation="Validate agent"
    )

    if output != "json":
        rprint(f"[bold]Agent:[/bold] {esc(data.get('name', 'unnamed'))} v{esc(data.get('version', '?'))}")
        rprint(f"[bold]Model:[/bold] {esc(data.get('model_name', 'N/A'))}")
        rprint()

    components = data.get("components", [])
    if not isinstance(components, list):
        fail(
            ErrorCategory.VALIDATION,
            "Agent components must be a list.",
            operation="Validate agent",
            resource=str(dir_path / YAML_FILE),
            remediation="Fix the components field and retry.",
        )
    if not components:
        result = {"valid": True, "agent": data.get("name"), "components": [], "issues": []}
        if output == "json":
            output_json(result)
        else:
            rprint("[dim]No components to validate.[/dim]")
        return

    table = Table(title="Component Validation", show_lines=False)
    table.add_column("Type", style="bold")
    table.add_column("ID", style="dim")
    table.add_column("Status")

    errors: list[str] = []
    component_results: list[dict] = []
    for comp in components:
        ctype = comp.get("component_type")
        cid = str(comp.get("component_id", ""))
        if ctype not in VALID_COMPONENT_TYPES:
            errors.append(f"invalid component type: {ctype}")
            component_results.append({"type": ctype, "id": cid, "valid": False, "error": "invalid type"})
            if output != "json":
                table.add_row(esc(ctype), esc(cid), "[red]✗ invalid type[/red]")
            continue
        # API convention: plural resource name
        plural = {"mcp": "mcps", "skill": "skills", "hook": "hooks", "prompt": "prompts", "sandbox": "sandboxes"}
        endpoint = f"/api/v1/{plural[ctype]}/{cid}"
        if comp.get("version"):
            # A pinned version has to exist, not just the component.
            endpoint = f"{endpoint}/versions/{comp['version']}"
        try:
            with _progress(output, f"Checking {ctype} {cid[:8]}..."):
                client.get(endpoint)
            component_results.append({"type": ctype, "id": cid, "valid": True, "error": None})
            if output != "json":
                table.add_row(esc(ctype), esc(cid), "[green]✓ valid[/green]")
        except CliError as error:
            if error.category is not ErrorCategory.NOT_FOUND:
                raise
            component_results.append({"type": ctype, "id": cid, "valid": False, "error": "not found"})
            if output != "json":
                table.add_row(esc(ctype), esc(cid), "[red]✗ not found[/red]")
            errors.append(f"{ctype}:{cid}")

    if output != "json":
        console.print(table)

    scope_payload = {"components": components}
    client.add_publish_target(scope_payload, team, visibility)
    with _progress(output, "Checking agent composition scope..."):
        scope_result = client.post("/api/v1/agents/validate", scope_payload)
    issues = scope_result.get("issues", [])
    for issue in issues:
        errors.append(issue.get("message", "Component is not valid for this agent target"))

    result = {
        "valid": not errors,
        "agent": data.get("name"),
        "components": component_results,
        "issues": issues,
    }
    if errors:
        if output != "json":
            rprint(f"\n[red]{len(errors)} component issue(s):[/red]")
            for error in errors:
                rprint(f"  [red]•[/red] {esc(error)}")
        fail(
            ErrorCategory.VALIDATION,
            f"Agent validation failed with {len(errors)} issue(s).",
            operation="Validate agent",
            resource=str(dir_path / YAML_FILE),
            remediation="Fix the reported component references or target scope and retry.",
            result=result,
        )
    if output == "json":
        output_json(result)
    else:
        rprint("\n[green]✓ All components valid.[/green]")


@agent_app.command(name="publish")
def agent_publish(
    directory: str = typer.Option(".", "--dir", "-d", help="Directory containing observal-agent.yaml"),
    update: bool = typer.Option(False, "--update", "-u", help="Update existing agent instead of creating"),
    draft: bool = typer.Option(False, "--draft", help="Save as draft instead of submitting for review"),
    submit: str | None = typer.Option(None, "--submit", help="Submit a draft agent for review (agent ID)"),
    bump: str | None = typer.Option(None, "--bump", help="Version bump type: patch, minor, or major (skips prompt)"),
    team: str | None = typer.Option(None, "--team", help="Teamspace UUID or handle"),
    visibility: str | None = typer.Option(None, "--visibility", help="Visibility: public or team"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Publish the agent definition to the server.

    Reads observal-agent.yaml from the specified directory and submits it.
    Use --update to modify an existing agent (same name). Use --draft to
    save without submitting for review.

    Examples:
      observal agent publish
      observal agent publish --update
      observal agent publish --draft
    """
    if draft and submit:
        fail(
            ErrorCategory.VALIDATION,
            "--draft and --submit cannot be used together.",
            operation="Publish agent",
            resource="agent publication mode",
            remediation="Use --draft to save a new draft or --submit to submit an existing draft.",
        )
    if bump is not None and bump not in {"patch", "minor", "major"}:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown version bump: {bump}.",
            operation="Publish agent",
            resource="agent version bump",
            remediation="Choose patch, minor, or major.",
        )
    if submit:
        resolved = client.resolve_registry_reference("agent", submit)
        with _progress(output, "Submitting draft for review..."):
            result = client.post(f"/api/v1/agents/{resolved}/submit")
        if output == "json":
            output_json(result)
            return
        rprint(f"[green]✓ Draft submitted for review![/green] ID: [bold]{esc(result['id'])}[/bold]")
        return

    dir_path = Path(directory)
    data = _validate_agent_definition(_load_agent_yaml(dir_path, operation="Publish agent"), operation="Publish agent")

    payload = {
        "name": data["name"],
        "version": data.get("version", "1.0.0"),
        "description": data.get("description", ""),
        "owner": data.get("owner", ""),
        "model_name": data.get("model_name", "claude-sonnet-4"),
        "models_by_harness": data.get("models_by_harness", {}) or {},
        "prompt": data.get("prompt", ""),
        "supported_harnesses": data.get("supported_harnesses", []),
        "components": data.get("components", []),
        "success_criteria": data.get("success_criteria"),
    }
    # Preserve legacy YAML that omits these optional fields; a fork scaffold
    # includes them explicitly and therefore round-trips their exact values.
    for field in ("model_config_json", "external_mcps", "category"):
        if field in data:
            payload[field] = data[field]

    if update:
        # The update endpoint refuses both fields by design: visibility has its own
        # authorized endpoint and a teamspace move is a transfer, not an edit.
        # Accepting them here and dropping them would report success for something
        # that never happened.
        if visibility is not None:
            # There is no single server call that edits an agent and republishes it,
            # so doing both means one can succeed while the other fails and the agent
            # is left half-published. Reordering only chooses which half breaks.
            fail(
                ErrorCategory.VALIDATION,
                "--visibility cannot be combined with --update.",
                operation="Publish agent",
                resource="agent visibility",
                remediation="Run the update first, then change visibility separately.",
            )
        if team is not None:
            fail(
                ErrorCategory.VALIDATION,
                "--team cannot be used with --update.",
                operation="Publish agent",
                resource="agent ownership",
                remediation="Use agent transfer-owner or recreate the agent in the target teamspace.",
            )
    else:
        client.add_publish_target(payload, team, visibility)

    if draft:
        with _progress(output, "Saving draft..."):
            result = client.post("/api/v1/agents/draft", payload)
        if output == "json":
            output_json(result)
            return
        rprint(f"[green]✓ Draft saved![/green] ID: [bold]{esc(result['id'])}[/bold]")
        return

    if update:
        # Fork scaffolds carry the exact new draft ID: an unapproved draft does
        # not appear in the public search and must not update a namesake instead.
        draft_id = data.get("agent_id")
        if draft_id is not None:
            try:
                agent_id = str(UUID(str(draft_id)))
            except ValueError:
                fail(
                    ErrorCategory.VALIDATION,
                    "Agent YAML has an invalid agent_id.",
                    operation="Publish agent",
                    resource=str(dir_path / YAML_FILE),
                    remediation="Use the ID returned by `observal agent fork` or remove agent_id.",
                )
        else:
            with _progress(output, "Looking up existing agent..."):
                results = client.get("/api/v1/agents", params={"search": data["name"]})
            match = next((agent for agent in results if agent.get("name") == data["name"]), None)
            if not match:
                fail(
                    ErrorCategory.NOT_FOUND,
                    f"No existing agent has the name {data['name']}.",
                    operation="Publish agent",
                    resource="agent registry",
                    remediation="Check the name or publish without --update.",
                )
            agent_id = match["id"]

        # Version bump selection (interactive only when --bump not provided)
        import sys

        if bump and bump in ("patch", "minor", "major"):
            payload["version_bump_type"] = bump
            payload.pop("version", None)
        elif sys.stdin.isatty() and draft_id is None:
            current_version = match.get("version", "1.0.0")
            suggestions = client.get(f"/api/v1/agents/{agent_id}/version-suggestions")
            sug = suggestions.get("suggestions", {})
            bump_choices = [
                f"patch  {current_version} → {sug.get('patch', '?')}  (bug fix)",
                f"minor  {current_version} → {sug.get('minor', '?')}  (improvement)",
                f"major  {current_version} → {sug.get('major', '?')}  (revamp)",
                "keep   (use version from YAML)",
            ]
            choice = select_one("Version bump type", bump_choices, default=bump_choices[0])
            bump_type = choice.split()[0]
            if bump_type != "keep":
                payload["version_bump_type"] = bump_type
                payload.pop("version", None)

        with _progress(output, "Updating agent..."):
            result = client.put(f"/api/v1/agents/{agent_id}", payload)
        if output == "json":
            output_json(result)
            return
        rprint(
            f"[green]✓ Agent updated![/green] ID: [bold]{esc(result['id'])}[/bold]  v{esc(result.get('version', '?'))}"
        )
    else:
        with _progress(output, "Submitting agent for review..."):
            result = client.post("/api/v1/agents", payload)
        if output == "json":
            output_json(result)
            return
        status = result.get("status", "pending")
        rprint(f"[green]✓ Agent submitted![/green] ID: [bold]{esc(result['id'])}[/bold]")
        rprint(f"  Pull: [cyan]observal agent pull {esc(client.canonical_name(result))}[/cyan]")
        if status != "approved":
            rprint(f"[yellow]Status: {esc(status)} - an admin must approve it before it becomes visible.[/yellow]")


@agent_app.command(name="release")
def agent_release(
    name: str = typer.Argument(..., help="Agent name, ID, row number, or @alias"),
    bump: str = typer.Option(..., "--bump", help="Version bump type: patch, minor, or major"),
    directory: str = typer.Option(".", "--dir", "-d", help="Directory containing observal-agent.yaml"),
    refresh_components: bool = typer.Option(
        False,
        "--refresh-components",
        help="Move every component without a version in the YAML to its latest approved release",
    ),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Bump version and push a versioned release to the registry.

    Reads observal-agent.yaml, bumps the version, and submits a new version
    to the review queue. The YAML must contain all required fields including
    model_config_json: {} and external_mcps: [].

    A release keeps every component at the version the current release
    pinned, so components never change without the author asking. Pin one
    component by giving it a `version` in the YAML, or pass
    --refresh-components to move the rest to their latest approved release.
    `observal agent outdated` shows what that would change.

    Examples:
      observal agent release my-agent --bump patch
      observal agent release my-agent --bump minor --dir /tmp/my-agent
      observal agent release my-agent --bump patch --refresh-components
    """
    if bump not in ("patch", "minor", "major"):
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown version bump: {bump}.",
            operation="Release agent version",
            resource="agent version bump",
            remediation="Choose patch, minor, or major.",
        )

    dir_path = Path(directory)
    data = _validate_agent_definition(
        _load_agent_yaml(dir_path, operation="Release agent version"), operation="Release agent version"
    )

    resolved = client.resolve_registry_reference("agent", name)
    with _progress(output, "Looking up agent..."):
        agent = client.get(f"/api/v1/agents/{resolved}")
    agent_id = agent["id"]

    # Fetch version suggestions
    with _progress(output, "Fetching version suggestions..."):
        suggestions = client.get(f"/api/v1/agents/{agent_id}/version-suggestions")

    current = suggestions.get("current", data.get("version", "1.0.0"))
    new_version = suggestions.get("suggestions", {}).get(bump)
    if not new_version:
        fail(
            ErrorCategory.VALIDATION,
            f"The server did not provide a {bump} version suggestion.",
            operation="Release agent version",
            resource="agent version suggestions",
            remediation="Check the current version and retry.",
        )
    new_version = _validate_version(str(new_version), operation="Release agent version")

    if output != "json":
        rprint(f"[dim]→[/dim] Bumping version: [bold]{esc(current)}[/bold] → [bold cyan]{esc(new_version)}[/bold cyan]")

    data["version"] = new_version
    raw_yaml = _dump_agent_yaml(data)

    # Build release payload from YAML
    payload = {
        "version": new_version,
        "description": data.get("description", ""),
        "prompt": data.get("prompt", ""),
        "model_name": data.get("model_name", "claude-sonnet-4"),
        "model_config_json": data.get("model_config_json") or {},
        "models_by_harness": data.get("models_by_harness", {}) or {},
        "external_mcps": data.get("external_mcps") or [],
        "supported_harnesses": data.get("supported_harnesses", []),
        "components": data.get("components", []),
        "yaml_snapshot": raw_yaml,
        "success_criteria": data.get("success_criteria"),
        "refresh_components": refresh_components,
    }

    if output != "json":
        rprint("[dim]→[/dim] Pushing definition to registry...")
    with _progress(output, "Creating version..."):
        result = client.post(f"/api/v1/agents/{agent_id}/versions", payload)

    _save_agent_yaml(dir_path, data, operation="Record released agent version")
    result.setdefault("version", new_version)
    if output == "json":
        output_json(result)
        return

    rprint(f"[green]✓ Version {esc(new_version)} submitted for review[/green]")
    for warning in result.get("warnings", []):
        rprint(f"[yellow]⚠ {esc(warning)}[/yellow]")


@agent_app.command(name="versions")
def agent_versions(
    name: str = typer.Argument(..., help="Agent name, ID, row number, or @alias"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
    page: int = typer.Option(1, "--page", "-p", min=1, help="Page number"),
    page_size: int = typer.Option(50, "--page-size", min=1, max=100, help="Versions per page"),
):
    """List all versions for an agent.

    Shows the version history for a given agent, including version
    number, review status, release date, author, and component count.
    Accepts a UUID, agent name, row number, or @alias.

    Examples:
      observal agent versions my-agent
      observal agent versions my-agent --output json
      observal agent versions @myalias
    """
    resolved = client.resolve_registry_reference("agent", name)

    with _progress(output, "Fetching versions..."):
        data = client.get(f"/api/v1/agents/{resolved}/versions", params={"page": page, "page_size": page_size})

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
    table.add_column("COMPONENTS")

    for item in items:
        table.add_row(
            esc(item.get("version", "")),
            status_badge(item.get("status", "")),
            esc(relative_time(item.get("created_at"))),
            esc(item.get("created_by_email", "") or item.get("created_by_username", "")),
            str(item.get("component_count", "")),
        )

    console.print(table)


@agent_app.command(name="outdated")
def agent_outdated(
    name: str = typer.Argument(..., help="Agent name, ID, row number, or @alias"),
    version: str | None = typer.Option(
        None, "--version", "-V", help="Agent version to check (default: the latest version)"
    ),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Show which components an agent version pins behind their latest release.

    Every agent version pins exact component versions, and pulls always
    install those pins. Use this as the agent's author to see what a
    refreshed release would change, then publish one with
    `observal agent release <agent> --bump patch --refresh-components`.

    Examples:
      observal agent outdated alice/reviewer
      observal agent outdated alice/reviewer --version 1.2.0 --output json
    """
    if version:
        version = _validate_exact_version(version, operation="Check agent components", resource="agent version")
    resolved = client.resolve_registry_reference("agent", name)
    with _progress(output, "Checking pinned components..."):
        if not version:
            version = str(client.get(f"/api/v1/agents/{resolved}").get("version") or "")
        data = client.get(f"/api/v1/agents/{resolved}/versions/{version}/outdated")

    if output == "json":
        output_json(data)
        return

    components = data.get("components", [])
    if not components:
        rprint(f"[dim]{esc(data.get('qualified_name', name))} v{esc(version)} has no components.[/dim]")
        return

    table = Table(title=f"{data.get('qualified_name', name)} v{version}", show_lines=False, padding=(0, 1))
    table.add_column("TYPE", style="dim")
    table.add_column("COMPONENT", style="cyan")
    table.add_column("PINNED", style="yellow")
    table.add_column("LATEST", style="green")
    table.add_column("STATUS")
    for item in components:
        if not item.get("locked"):
            status = "[yellow]unlocked[/yellow]"
        elif item.get("outdated"):
            status = "[yellow]outdated[/yellow]"
        else:
            status = "[green]current[/green]"
        if item.get("archived"):
            status += " [dim](archived)[/dim]"
        table.add_row(
            esc(item.get("type", "")),
            esc(item.get("qualified_name") or item.get("name", "")),
            esc(item.get("pinned_version") or "?"),
            esc(item.get("latest_version") or "none"),
            status,
        )
    console.print(table)

    summary = data.get("summary", {})
    if summary.get("outdated") or summary.get("unlocked"):
        rprint(
            f"\n[yellow]{summary.get('outdated', 0)} outdated, {summary.get('unlocked', 0)} unlocked.[/yellow] "
            "Release a refreshed version to move pins forward:"
        )
        rprint(
            f"  [cyan]observal agent release {esc(data.get('qualified_name', name))} --bump patch "
            "--refresh-components[/cyan]"
        )
    else:
        rprint("\n[green]✓ Every pinned component is on its latest approved release.[/green]")
