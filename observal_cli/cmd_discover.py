# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""observal discover: find approved registry resources for a task and use them now.

This is the universal discovery surface. It reaches every harness through the
bundled skill, which already ships to all ten on login, so a plain assistant
can search Observal, inspect a result and load a skill or prompt into the
current session without a prebuilt Agent.

Three verbs, deliberately subcommands so a query that happens to start with a
verb never collides:

    observal discover search <words...>   ranked candidates with a reason each
    observal discover inspect <urn>       the complete entry
    observal discover use <urn>           activate: text resources load now,
                                          everything else points at the exact
                                          install command that already asks first

Every activation appends to the capability lock so session upload can say
which resources were actually used.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from contextlib import nullcontext
from pathlib import Path

import typer
from rich import print as rprint
from rich.table import Table

from observal_cli import capability_lock, client
from observal_cli.constants import VALID_HARNESSES
from observal_cli.errors import ErrorCategory, fail
from observal_cli.render import OutputMode, console, esc, output_json, spinner

discover_app = typer.Typer(
    name="discover",
    help=(
        "Find approved resources for a task and use them in this session\n\n"
        "Examples:\n"
        '  observal discover search "review a pull request for auth bugs" --output json\n'
        "  observal discover inspect urn:air:observal.acme.com:skill:5f2c...\n"
        "  observal discover use urn:air:observal.acme.com:skill:5f2c..."
    ),
    no_args_is_help=True,
)

KINDS = ("agent", "mcp", "skill", "hook", "prompt", "sandbox")
DEFAULT_LIMIT = 5
MAX_LIMIT = 25
DEFAULT_MAX_CHARS = 32_000

_KIND_MEDIA = {
    "skill": "application/ai-skill+md",
    "mcp": "application/mcp-server-card+json",
    "prompt": "application/vnd.observal.prompt+json",
    "sandbox": "application/vnd.observal.sandbox+json",
    "hook": "application/vnd.observal.hook+json",
    "agent": "application/vnd.observal.agent+json",
}

# Kinds whose artifact is text the assistant can read right now.
_CONTEXT_KINDS = {"skill", "prompt"}

# Terminal control sequences (CSI, OSC, and other ESC-prefixed codes). An
# approved artifact is still third-party text; it must not be able to drive
# the terminal when printed in table mode. JSON output is left untouched.
_TERMINAL_CONTROL_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"  # CSI ... final byte
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC ... BEL or ST
    r"|\x1b[@-Z\\-_]"  # two-byte escapes (except CSI/OSC handled above)
    r"|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"  # other C0 controls, keep \t \n \r
)


def sanitize_for_terminal(text: str) -> str:
    """Strip escape and control sequences so printed artifact text is inert."""
    return _TERMINAL_CONTROL_RE.sub("", text)


_AVAILABILITY_LABELS = {
    "now": "[green]now[/green]",
    "next-session": "[yellow]next session[/yellow]",
    "explicit-install": "[yellow]explicit install[/yellow]",
    "not-approved": "[red]not approved[/red]",
    "archived": "[dim]archived[/dim]",
    "unsupported-in-harness": "[red]unsupported here[/red]",
}


# ── Helpers ──────────────────────────────────────────────────────────────


def _kind_filter(kind: str | None, operation: str) -> str | None:
    if kind is None:
        return None
    normalized = kind.strip().lower().rstrip("s") if kind.strip().lower() != "sandboxes" else "sandbox"
    if normalized not in KINDS:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown resource type: {kind}.",
            operation=operation,
            resource="resource type",
            remediation=f"Choose one of: {', '.join(KINDS)}.",
        )
    return normalized


def _resolve_harness(harness: str | None, operation: str) -> str | None:
    """Explicit flag, then the OBSERVAL_HARNESS environment, then the only installed harness."""
    candidate = (harness or os.environ.get("OBSERVAL_HARNESS") or "").strip()
    if candidate:
        if candidate not in VALID_HARNESSES:
            fail(
                ErrorCategory.VALIDATION,
                f"Unknown harness: {candidate}.",
                operation=operation,
                resource="harness",
                remediation=f"Choose one of: {', '.join(VALID_HARNESSES)}.",
            )
        return candidate
    try:
        from observal_cli.harness import ensure_loaded, get_all_adapters

        ensure_loaded()
        installed = [name for name, adapter in get_all_adapters().items() if adapter.is_installed()]
    except Exception:
        return None
    return installed[0] if len(installed) == 1 else None


def _parse_urn(identifier: str, operation: str) -> tuple[str, str, str]:
    """Return (urn, kind, entity_id) for an Observal-issued identifier, or a registered remote A2A agent."""
    urn = identifier.strip()
    if urn.startswith("urn:ai:"):
        urn = "urn:air:" + urn[len("urn:ai:") :]
    parts = urn.split(":")
    if len(parts) != 5 or parts[0] != "urn" or parts[1] != "air" or parts[3] not in (*KINDS, "a2a"):
        fail(
            ErrorCategory.VALIDATION,
            f"Not an Observal resource identifier: {identifier}.",
            operation=operation,
            resource="identifier",
            remediation="Use the identifier from `observal discover search`, e.g. urn:air:<domain>:skill:<uuid>.",
        )
    return urn, parts[3], parts[4]


def _search(text: str, *, kind: str | None, harness: str | None, limit: int, unapproved: bool) -> dict:
    filters: dict = {}
    if kind:
        filters["type"] = [_KIND_MEDIA[kind]]
    if harness:
        filters["obs:supportedHarnesses"] = [harness]
    if not unapproved:
        filters["obs:lifecycle"] = ["approved"]
    body: dict = {"query": {"text": text, "filter": filters}, "federation": "none", "pageSize": limit}
    return client.post(
        "/api/v1/ard/search",
        body,
        operation="Search discoverable resources",
        resource="discovery search",
    )


def _use_hint(item: dict) -> str:
    # A remote A2A agent is called, never installed. Search results say so in obs:availability; a full
    # entry (discover inspect) only carries obs:protocol.
    if item.get("obs:availability") == "delegate" or item.get("obs:protocol") == "a2a":
        return f"observal delegate run {item.get('identifier', '')} '<complete task brief>'"
    return f"observal discover use {item.get('identifier', '')}"


def _next_step(item: dict, harness: str | None, entity_id: str | None = None) -> str | None:
    """The existing command that installs a next-session resource; it already confirms.

    The command pins the version the discovery entry describes, so what gets
    installed is what was searched and inspected.
    """
    kind = item.get("obs:kind")
    ref, _, version = (item.get("obs:nativeRef") or "").partition("@")
    if kind == "sandbox":
        # No install command exists: a sandbox only reaches a machine inside an Agent. `agent add` takes the UUID,
        # not the native ref, so a missing ref is fine; the entry's own version still pins the release shown.
        if not entity_id:
            return None
        version = version or item.get("version") or ""
        pin = f" --version {shlex.quote(version)}" if version else ""
        return f"observal agent add sandbox {shlex.quote(entity_id)}{pin}"
    if not ref or not kind:
        return None
    # The command is meant to be run as printed, and the reference comes from a
    # registry response, so quote anything a shell would interpret.
    ref = shlex.quote(ref)
    flag = f" --harness {harness}" if harness else " --harness <harness>"
    pin = f" --version {shlex.quote(version)}" if version else ""
    if kind == "agent":
        return f"observal agent pull {ref}{flag}{pin}"
    if kind in ("mcp", "skill", "hook"):
        return f"observal registry {kind} install {ref}{flag}{pin}"
    return None


# ── search ───────────────────────────────────────────────────────────────


@discover_app.command("search")
def discover_search(
    words: list[str] = typer.Argument(..., help="What you are trying to do (quote it as one argument)"),
    kind: str | None = typer.Option(
        None, "--type", "-t", help="Restrict to one kind: agent, mcp, skill, hook, prompt, sandbox"
    ),
    harness: str | None = typer.Option(None, "--harness", "-i", help="Only resources that work in this harness"),
    limit: int = typer.Option(DEFAULT_LIMIT, "--limit", "-n", min=1, max=MAX_LIMIT, help="How many results"),
    unapproved: bool = typer.Option(False, "--include-unapproved", help="Also show your own pending/rejected items"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Search approved resources for a task.

    Results are ranked by relevance only. Approval, availability in the
    current session, and harness support are shown as separate columns so a
    strong match that is not usable yet is never mistaken for one that is.

    Examples:
      observal discover search "review a pull request for authentication bugs"
      observal discover search "query postgres" --type mcp --output json
      observal discover search "generate tests" --harness pi
    """
    operation = "Search discoverable resources"
    text = " ".join(w.strip() for w in words if w.strip())
    if not text:
        fail(
            ErrorCategory.VALIDATION,
            "A search needs some words to search for.",
            operation=operation,
            resource="search text",
            remediation="Describe the task, e.g. `observal discover search 'review a pull request for auth bugs'`.",
        )
    kind_value = _kind_filter(kind, operation)
    harness_value = _resolve_harness(harness, operation) if harness else None

    fetch = nullcontext() if output == "json" else spinner("Searching Observal...")
    with fetch:
        data = _search(text, kind=kind_value, harness=harness_value, limit=limit, unapproved=unapproved)

    results = data.get("results") or []
    if output == "json":
        output_json({"query": text, "harness": harness_value, "results": results, "count": len(results)})
        return

    if not results:
        rprint(f"[dim]Nothing in Observal matches[/dim] {esc(text)}[dim].[/dim]")
        rprint(
            "[dim]Try fewer or different words, or drop --type/--harness. If it truly does not exist, build it and consider publishing it.[/dim]"
        )
        rprint(
            "[dim]For most users, discovery only covers your own listings and the teamspaces you belong to, not other "
            "people's public listings. Browse those with `observal registry <type> list` or `observal agent list`, and "
            "install registry agents and components by reference. Remote A2A agents are not installed: run them "
            "with `observal delegate run`.[/dim]"
        )
        return

    table = Table(title=f"Resources for: {esc(text)}", show_lines=False, padding=(0, 1))
    table.add_column("#", style="dim", width=3)
    table.add_column("Kind")
    table.add_column("Resource", style="bold", overflow="fold")
    table.add_column("Ver")
    table.add_column("Match", justify="right")
    table.add_column("Status")
    table.add_column("Use")
    table.add_column("Why", overflow="fold")
    for i, item in enumerate(results, 1):
        table.add_row(
            str(i),
            esc(item.get("obs:kind", "")),
            f"{esc(item.get('displayName', ''))}\n[dim]{esc(item.get('obs:nativeRef', ''))}[/dim]",
            esc(item.get("version", "")),
            f"{item.get('score', 0)}",
            esc(item.get("obs:approval", "")) + ("\n[cyan]recommended[/cyan]" if item.get("obs:recommended") else ""),
            _AVAILABILITY_LABELS.get(item.get("obs:availability", ""), esc(item.get("obs:availability", ""))),
            esc(", ".join(item.get("matchedOn") or [])),
        )
    console.print(table)
    first = results[0]
    rprint(
        f"[dim]Inspect: [cyan]observal discover inspect {esc(first.get('identifier', ''))} --output json[/cyan][/dim]"
    )
    rprint(f"[dim]Use:     [cyan]{esc(_use_hint(first))}[/cyan][/dim]")


# ── inspect ──────────────────────────────────────────────────────────────


@discover_app.command("inspect")
def discover_inspect(
    identifier: str = typer.Argument(..., help="Resource identifier from `discover search` (urn:air:...)"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Show the complete entry for one resource.

    Examples:
      observal discover inspect urn:air:observal.acme.com:skill:5f2c... --output json
    """
    operation = "Inspect discoverable resource"
    urn, _kind, _entity = _parse_urn(identifier, operation)
    fetch = nullcontext() if output == "json" else spinner("Fetching entry...")
    with fetch:
        entry = client.get(f"/api/v1/ard/entries/{urn}", operation=operation, resource=urn)
    if output == "json":
        output_json(entry)
        return

    rprint(f"[bold]{esc(entry.get('displayName', ''))}[/bold]  [dim]{esc(entry.get('obs:nativeRef', ''))}[/dim]")
    rprint(f"  {esc(entry.get('description', ''))}")
    rprint()
    rows = [
        ("Identifier", entry.get("identifier")),
        ("Kind", entry.get("obs:kind")),
        ("Type", entry.get("type")),
        ("Version", entry.get("version")),
        ("Status", entry.get("obs:lifecycle")),
        ("Visibility", entry.get("obs:visibility")),
        ("Harnesses", ", ".join(entry.get("obs:supportedHarnesses") or []) or "any"),
        ("Usable now", "yes" if entry.get("obs:activatable") else "no"),
        ("Capabilities", ", ".join(entry.get("capabilities") or [])),
        ("Artifact", entry.get("url")),
        ("Digest", entry.get("obs:artifactDigest")),
    ]
    for label, value in rows:
        rprint(f"  [dim]{label:<13}[/dim] {esc(value or '-')}")
    queries = entry.get("representativeQueries") or []
    if queries:
        rprint("  [dim]Good for[/dim]")
        for q in queries:
            rprint(f"    • {esc(q)}")
    rprint()
    rprint(f"[dim]Use: [cyan]{esc(_use_hint(entry))}[/cyan][/dim]")


# ── use ──────────────────────────────────────────────────────────────────


@discover_app.command("use")
def discover_use(
    identifier: str = typer.Argument(..., help="Resource identifier from `discover search` (urn:air:...)"),
    harness: str | None = typer.Option(
        None, "--harness", "-i", help="Harness this session runs in (detected when omitted)"
    ),
    max_chars: int = typer.Option(DEFAULT_MAX_CHARS, "--max-chars", min=1_000, help="Cap on printed content"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Load even if the resource is not approved (your own drafts)"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Activate a resource for the current task.

    Skills and prompts are text: their exact approved version is printed for
    you to read now and the use is recorded in the capability lock. MCP
    servers, hooks and agents change your setup, so this prints the existing
    install command, which asks before writing anything. Sandboxes have no
    install command; this prints `observal agent add sandbox <uuid>`, which
    only edits a local observal-agent.yaml.

    Examples:
      observal discover use urn:air:observal.acme.com:skill:5f2c...
      observal discover use urn:air:observal.acme.com:mcp:9a1b... --harness kiro
    """
    operation = "Use discoverable resource"
    urn, kind, entity_id = _parse_urn(identifier, operation)
    if kind == "a2a":
        fail(
            ErrorCategory.VALIDATION,
            f"A remote A2A agent is not installed or loaded; hand it a task with "
            f"observal delegate run {urn} '<complete task brief>'.",
            operation=operation,
            resource=urn,
        )
    harness_value = _resolve_harness(harness, operation)

    fetch = nullcontext() if output == "json" else spinner("Fetching entry...")
    with fetch:
        entry = client.get(f"/api/v1/ard/entries/{urn}", operation=operation, resource=urn)

    lifecycle = entry.get("obs:lifecycle")
    if lifecycle != "approved" and not yes:
        fail(
            ErrorCategory.PERMISSION,
            f"{entry.get('displayName', urn)} is {lifecycle or 'not approved'}, not approved.",
            operation=operation,
            resource=urn,
            remediation="Only approved resources load automatically. Add --yes to load your own draft anyway.",
        )
    supported = entry.get("obs:supportedHarnesses") or []
    if harness_value and supported and harness_value not in supported:
        fail(
            ErrorCategory.VALIDATION,
            f"{entry.get('displayName', urn)} does not list {harness_value} as a supported harness.",
            operation=operation,
            resource=urn,
            remediation=f"Supported: {', '.join(supported)}. Pass --harness to override the detected harness.",
        )

    version = entry.get("version") or ""
    native_ref = entry.get("obs:nativeRef")
    cwd = str(Path.cwd())

    if kind not in _CONTEXT_KINDS:
        step = _next_step({"obs:kind": kind, "obs:nativeRef": native_ref, "version": version}, harness_value, entity_id)
        if kind == "sandbox":
            reason = (
                "Sandboxes have no install command; they are used through an Agent. "
                + (
                    "Run next_step in an Agent directory (create one with `observal agent init`) to add it to "
                    "observal-agent.yaml, then "
                    if step
                    else "Add it to an Agent's observal-agent.yaml, then "
                )
                + "`observal agent publish` and `observal agent pull <agent> --harness <harness>` to put it on your machine."
            )
        else:
            reason = "This kind changes your harness setup; run the install command, which confirms before writing."
        result = {
            "identifier": urn,
            "kind": kind,
            "version": version,
            "native_ref": native_ref,
            "mode": "next-session",
            "activated": False,
            "reason": reason,
            "next_step": step,
        }
        if output == "json":
            output_json(result)
            return
        if kind == "sandbox":
            rprint(f"[yellow]{esc(entry.get('displayName', ''))}[/yellow] is a sandbox. {reason}")
        else:
            rprint(
                f"[yellow]{esc(entry.get('displayName', ''))}[/yellow] is a {kind}; it takes effect after a restart."
            )
        if step:
            rprint(f"  Run: [cyan]{esc(step)}[/cyan]")
        return

    # Text resource: fetch the exact bytes the entry points at.
    artifact_path = f"/api/v1/artifacts/{kind}/{entity_id}/{version}"
    with nullcontext() if output == "json" else spinner("Fetching content..."):
        if kind == "skill":
            content = client.get_text(artifact_path, operation=operation, resource=urn)
        else:
            payload = client.get(artifact_path, operation=operation, resource=urn)
            content = json.dumps(payload, indent=2, ensure_ascii=False)

    truncated = len(content) > max_chars
    shown = content[:max_chars]
    use = capability_lock.record(
        kind=kind,
        mode=capability_lock.MODE_CONTEXT,
        source="discover-cli",
        harness=harness_value,
        cwd=cwd,
        identifier=urn,
        component_id=entity_id,
        native_ref=native_ref,
        version=version,
        digest=entry.get("obs:artifactDigest"),
    )

    if output == "json":
        output_json(
            {
                "identifier": urn,
                "kind": kind,
                "version": version,
                "native_ref": native_ref,
                "digest": use.digest,
                "mode": use.mode,
                "activated": True,
                "harness": harness_value,
                "truncated": truncated,
                "artifact_url": entry.get("url"),
                "content": shown,
            }
        )
        return

    rprint(
        f"[green]✓ Loaded[/green] [bold]{esc(entry.get('displayName', ''))}[/bold] {esc(version)} [dim]({esc(kind)})[/dim]"
    )
    rprint(f"[dim]Recorded in the capability lock for {esc(harness_value or 'unknown harness')} in {esc(cwd)}[/dim]")
    rprint()
    print(sanitize_for_terminal(shown))
    if truncated:
        rprint()
        rprint(
            f"[yellow]Content truncated at {max_chars} characters.[/yellow] Full artifact: {esc(entry.get('url', ''))}"
        )
