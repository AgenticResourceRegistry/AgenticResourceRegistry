# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Create and consume scoped, expiring Agent share manifests."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import typer
from rich import print as rprint
from rich.table import Table

from observal_cli import client, config
from observal_cli.constants import VALID_HARNESSES
from observal_cli.errors import ErrorCategory, fail
from observal_cli.prompts import select_many, select_one
from observal_cli.render import OutputMode, console, esc, output_json, spinner

_OPERATION = "Share repository agents"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")

share_app = typer.Typer(
    name="share",
    help="Share version-pinned Agents installed in the current repository.\n\nExamples:\n  observal share candidates",
    invoke_without_command=True,
    no_args_is_help=False,
)


def _repository_root(directory: str) -> Path:
    root = Path(directory).expanduser().resolve()
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise typer.BadParameter(f"could not run git in {root}: {exc}") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise typer.BadParameter(f"{root} is not inside a git repository")
    return Path(result.stdout.strip()).resolve()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def discover_repository_agents(directory: str = ".") -> tuple[Path, list[dict]]:
    """Return unique pinned Agent versions tracked within a repository."""
    from observal_cli.lockfile import get_all_entries

    root = _repository_root(directory)
    entries = get_all_entries()
    grouped: dict[tuple[str, str], dict] = {}
    for entry in entries:
        if entry.get("entry_type") != "agent" or entry.get("scope") != "project":
            continue
        installed_dir = entry.get("directory")
        agent_id = str(entry.get("id") or "").strip()
        version = str(entry.get("version") or "").strip()
        if not installed_dir or not agent_id or not version:
            continue
        if not _is_within(Path(installed_dir), root):
            continue
        key = (agent_id, version)
        current = grouped.setdefault(
            key,
            {
                "agent_id": agent_id,
                "version": version,
                "qualified_name": entry.get("qualified_name") or entry.get("name") or agent_id,
                "installed_in": [],
            },
        )
        harness = str(entry.get("harness") or "")
        if harness and harness not in current["installed_in"]:
            current["installed_in"].append(harness)

    items = sorted(grouped.values(), key=lambda item: (item["qualified_name"], item["version"]))
    return root, items


def _candidate_label(item: dict) -> str:
    harnesses = ", ".join(item["installed_in"])
    return f"{item['qualified_name']}  {item['version']}  [{harnesses}]"


@share_app.callback()
def share_default(
    ctx: typer.Context,
    directory: str = typer.Option(".", "--dir", help="Repository directory."),
    agent: list[str] | None = typer.Option(None, "--agent", help="Installed Agent UUID or namespace/slug; repeatable."),
    all_agents: bool = typer.Option(False, "--all", help="Share every tracked Agent version in the repository."),
    expires_days: int = typer.Option(7, "--expires-days", min=1, max=30, help="Link lifetime in days (maximum 30)."),
    title: str | None = typer.Option(None, "--title", help="Optional share title."),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
) -> None:
    """Select repository Agents when no explicit share subcommand is given."""
    if ctx.invoked_subcommand is None:
        create_share(directory, agent, all_agents, expires_days, title, output)


def _select_candidates(
    candidates: list[dict], requested: list[str], select_all: bool, output: OutputMode
) -> list[dict]:
    if requested:
        wanted = {value.strip() for value in requested if value.strip()}
        selected = [item for item in candidates if item["agent_id"] in wanted or item["qualified_name"] in wanted]
        missing = wanted - {value for item in selected for value in (item["agent_id"], item["qualified_name"])}
        if missing:
            fail(
                ErrorCategory.NOT_FOUND,
                "One or more requested Agents are not installed in this repository.",
                operation=_OPERATION,
                resource=", ".join(sorted(missing)),
                remediation="Run `observal share candidates` to inspect tracked repository Agents.",
            )
        return selected
    if select_all:
        return candidates
    if output == "json":
        fail(
            ErrorCategory.VALIDATION,
            "JSON mode requires --all or at least one --agent.",
            operation=_OPERATION,
            resource="Agent selection",
            remediation="Specify the intended Agent selection explicitly.",
        )
    labels = [_candidate_label(item) for item in candidates]
    chosen = set(select_many("Select Agents to share", labels, defaults=labels))
    return [item for item in candidates if _candidate_label(item) in chosen]


@share_app.command("candidates")
def candidates(
    directory: str = typer.Option(".", "--dir", help="Repository directory."),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
) -> None:
    """List shareable Agent versions tracked in the current repository.

    Examples:
      observal share candidates
    """
    try:
        root, items = discover_repository_agents(directory)
    except (OSError, RuntimeError, ValueError) as error:
        fail(
            ErrorCategory.VALIDATION,
            "The Observal installation lockfile could not be read.",
            operation=_OPERATION,
            resource="Observal lockfile",
            remediation="Run `observal doctor` and retry.",
            detail=repr(error),
        )
    payload = {"repository_root": str(root), "items": items}
    if output == "json":
        output_json(payload)
        return
    table = Table(title=f"Shareable Agents — {root}")
    table.add_column("Agent")
    table.add_column("Version")
    table.add_column("Installed in")
    for item in items:
        table.add_row(esc(item["qualified_name"]), esc(item["version"]), esc(", ".join(item["installed_in"])))
    console.print(table)


@share_app.command("create")
def create_share(
    directory: str = typer.Option(".", "--dir", help="Repository directory."),
    agent: list[str] | None = typer.Option(None, "--agent", help="Installed Agent UUID or namespace/slug; repeatable."),
    all_agents: bool = typer.Option(False, "--all", help="Share every tracked Agent version in the repository."),
    expires_days: int = typer.Option(7, "--expires-days", min=1, max=30, help="Link lifetime in days (maximum 30)."),
    title: str | None = typer.Option(None, "--title", help="Optional share title."),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
) -> None:
    """Select repository Agents and create an opaque, expiring share link.

    Examples:
      observal share create --agent namespace/slug
    """
    if all_agents and agent:
        fail(
            ErrorCategory.VALIDATION,
            "--all cannot be combined with --agent.",
            operation=_OPERATION,
            resource="Agent selection",
            remediation="Choose all Agents or provide an explicit selection.",
        )
    try:
        root, available = discover_repository_agents(directory)
    except (OSError, RuntimeError, ValueError) as error:
        fail(
            ErrorCategory.VALIDATION,
            "The Observal installation lockfile could not be read.",
            operation=_OPERATION,
            resource="Observal lockfile",
            remediation="Run `observal doctor` and retry.",
            detail=repr(error),
        )
    if not available:
        fail(
            ErrorCategory.NOT_FOUND,
            "No Observal-tracked project Agents were found in this repository.",
            operation=_OPERATION,
            resource=str(root),
            remediation="Pull an Agent with project scope, then retry.",
        )
    selected = _select_candidates(available, agent or [], all_agents, output)
    if not selected:
        fail(
            ErrorCategory.VALIDATION,
            "At least one Agent must be selected.",
            operation=_OPERATION,
            resource="Agent selection",
            remediation="Select one or more repository Agents.",
        )
    body = {
        "title": title,
        "expires_in_days": expires_days,
        "items": [{"agent_id": item["agent_id"], "version": item["version"]} for item in selected],
    }
    with spinner("Creating secure share link...") if output != "json" else _nullcontext():
        result = client.post("/api/v1/agent-shares", json_data=body, operation=_OPERATION, resource="Agent share")
    if output == "json":
        output_json(result)
        return
    rprint(f"\n[green]Created share for {len(selected)} Agent(s).[/green]")
    rprint(f"[bold cyan]{esc(result['url'])}[/bold cyan]")
    rprint(f"[dim]Expires: {esc(result['expires_at'])}[/dim]")
    rprint("[dim]The link contains no Agent IDs, repository paths, or credentials.[/dim]")


def _nullcontext():
    from contextlib import nullcontext

    return nullcontext()


def _allowed_origins() -> set[tuple[str, str, int | None]]:
    values = config.load()
    origins: set[tuple[str, str, int | None]] = set()
    for key in ("server_url", "web_url"):
        raw = str(values.get(key) or "").strip()
        if not raw:
            continue
        parsed = urlsplit(raw)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            origins.add((parsed.scheme, parsed.hostname.lower(), parsed.port))
    return origins


def parse_share_token(value: str) -> str:
    """Extract a token without ever sending credentials to a user-supplied URL."""
    value = value.strip()
    if _TOKEN_RE.fullmatch(value):
        return value
    if len(value) > 2048 or any(ord(char) < 32 for char in value):
        raise ValueError("invalid share link")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("invalid share link")
    if (
        parsed.query
        or parsed.fragment
        or (parsed.scheme, parsed.hostname.lower(), parsed.port) not in _allowed_origins()
    ):
        raise ValueError("share link is not from the configured Observal server")
    parts = [part for part in parsed.path.split("/") if part]
    token = parts[-1] if parts else ""
    frontend_path = len(parts) >= 3 and parts[-3:] == ["shares", "agents", token]
    api_path = len(parts) >= 2 and parts[-2:] == ["agent-shares", token]
    if not frontend_path and not api_path:
        raise ValueError("invalid share link path")
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError("invalid share token")
    return token


@share_app.command("open")
def open_share(
    share: str = typer.Argument(..., help="Share link or opaque token."),
    harness: str | None = typer.Option(None, "--harness", help="Harness to pull into."),
    directory: str = typer.Option(".", "--dir", help="Installation directory."),
    no_pull: bool = typer.Option(False, "--no-pull", help="Only inspect the share."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Pull all accessible Agents without confirmation."),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
) -> None:
    """Open a share and, after confirmation, pull its accessible Agents.

    Examples:
      observal share open YOUR_SHARE_TOKEN --no-pull
    """
    try:
        token = parse_share_token(share)
    except ValueError as error:
        fail(
            ErrorCategory.VALIDATION,
            str(error).capitalize() + ".",
            operation="Open Agent share",
            resource="share link",
            remediation="Use the exact link produced by the configured Observal server.",
        )
    result = client.get(f"/api/v1/agent-shares/{token}", operation="Open Agent share", resource="Agent share")
    items = result.get("items") or []
    if output == "json":
        output_json(result)
        return
    rprint(f"\n[bold]{esc(result.get('title') or 'Shared Agents')}[/bold]")
    rprint(f"Shared by [cyan]@{esc(result.get('created_by_username', 'unknown'))}[/cyan]")
    rprint(f"Expires: {esc(result.get('expires_at', ''))}")
    for item in items:
        rprint(f"  • [cyan]{esc(item['qualified_name'])}[/cyan] [dim]{esc(item['version'])}[/dim]")
    unavailable = int(result.get("unavailable_count") or 0)
    if unavailable:
        rprint(f"[yellow]{unavailable} item(s) are unavailable in your current scope.[/yellow]")
    if no_pull or not items:
        return

    selected = items
    if not yes:
        labels = [f"{item['qualified_name']}  {item['version']}" for item in items]
        chosen = set(select_many("Select Agents to pull", labels, defaults=labels))
        selected = [item for item in items if f"{item['qualified_name']}  {item['version']}" in chosen]
        if not selected or not typer.confirm(f"Pull {len(selected)} Agent(s)?"):
            rprint("[dim]No Agents pulled.[/dim]")
            return
    target_harness = harness or select_one("Target harness", list(VALID_HARNESSES))
    if target_harness not in VALID_HARNESSES:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown harness: {target_harness}.",
            operation="Pull shared Agents",
            resource="target harness",
            remediation=f"Choose one of: {', '.join(VALID_HARNESSES)}.",
        )

    failures: list[str] = []
    for item in selected:
        command = [
            sys.executable,
            "-m",
            "observal_cli",
            "agent",
            "pull",
            str(item["agent_id"]),
            "--version",
            str(item["version"]),
            "--harness",
            target_harness,
            "--dir",
            str(Path(directory).resolve()),
        ]
        if yes:
            command.append("--no-prompt")
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            failures.append(item["qualified_name"])
    if failures:
        fail(
            ErrorCategory.UNAVAILABLE,
            f"Failed to pull {len(failures)} shared Agent(s).",
            operation="Pull shared Agents",
            resource=", ".join(failures),
            remediation="Resolve the reported pull errors and retry those Agents.",
        )
    rprint(f"[green]Pulled {len(selected)} Agent(s) into {esc(target_harness)}.[/green]")


@share_app.command("revoke")
def revoke_share(
    share: str = typer.Argument(..., help="Share link or opaque token."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
) -> None:
    """Revoke a share link immediately.

    Examples:
      observal share revoke YOUR_SHARE_TOKEN
    """
    try:
        token = parse_share_token(share)
    except ValueError as error:
        fail(
            ErrorCategory.VALIDATION,
            str(error).capitalize() + ".",
            operation="Revoke Agent share",
            resource="share link",
            remediation="Use a share created by the configured Observal server.",
        )
    if output == "json" and not yes:
        fail(
            ErrorCategory.VALIDATION,
            "JSON mode requires --yes.",
            operation="Revoke Agent share",
            resource="confirmation",
            remediation="Add --yes after confirming the intended share.",
        )
    if not yes and not typer.confirm("Revoke this share link?"):
        return
    result = client.delete(f"/api/v1/agent-shares/{token}", operation="Revoke Agent share", resource="Agent share")
    if output == "json":
        output_json(result)
    else:
        rprint("[green]Share link revoked.[/green]")
