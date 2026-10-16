# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""observal delegate: hand a task to another approved agent and follow it.

The same operations pulled agents reach through the observal-agents MCP
server, for people and scripts. See ``observal_cli.delegation``.
"""

from __future__ import annotations

from contextlib import nullcontext

import typer
from rich import print as rprint
from rich.table import Table

from observal_cli.constants import VALID_HARNESSES
from observal_cli.delegation import service, tasks
from observal_cli.errors import ErrorCategory, fail
from observal_cli.render import OutputMode, console, esc, output_json, spinner

delegate_app = typer.Typer(
    name="delegate",
    help=(
        "Hand a task to another approved agent (registry or remote A2A)\n\n"
        "Examples:\n"
        '  observal delegate find "review this diff for auth bugs" --output json\n'
        '  observal delegate run urn:air:observal.acme.com:agent:5f2c... "Review src/auth for token leaks"\n'
        "  observal delegate status <task-id> --wait 120"
    ),
    no_args_is_help=True,
)

_STATE_STYLE = {
    tasks.STATE_COMPLETED: "green",
    tasks.STATE_WORKING: "yellow",
    tasks.STATE_SUBMITTED: "yellow",
    tasks.STATE_INPUT_REQUIRED: "cyan",
    tasks.STATE_AUTH_REQUIRED: "cyan",
    tasks.STATE_FAILED: "red",
    tasks.STATE_REJECTED: "red",
    tasks.STATE_CANCELED: "dim",
}


def _guard(operation: str, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except service.DelegationError as exc:
        fail(ErrorCategory.VALIDATION, exc.message, operation=operation, resource="delegation")


def _state(task: dict) -> str:
    state = (task.get("status") or {}).get("state", "")
    short = state.removeprefix("TASK_STATE_").lower().replace("_", "-")
    style = _STATE_STYLE.get(state, "white")
    return f"[{style}]{esc(short)}[/{style}]"


def _show(task: dict, output: OutputMode) -> None:
    if output == "json":
        output_json(task)
        return
    m = tasks.meta(task)
    rprint(f"[bold]{esc(m.get('targetName') or m.get('target'))}[/bold]  {_state(task)}  [dim]{esc(task['id'])}[/dim]")
    if m.get("harness"):
        rprint(f"  [dim]ran in[/dim] {esc(m['harness'])}")
    note = tasks.message_text((task.get("status") or {}).get("message"))
    if note:
        rprint(f"  {esc(note)}")
    for artifact in task.get("artifacts") or []:
        name = artifact.get("name") or "artifact"
        if name == "changes.patch":
            rprint(f"\n[bold]Proposed changes[/bold] [dim](not applied)[/dim]: {esc(m.get('patchPath'))}")
            rprint(f"  Apply with: [cyan]git apply {esc(m.get('patchPath'))}[/cyan]")
            continue
        rprint(f"\n[bold]{esc(name)}[/bold]")
        console.print(tasks.message_text(artifact), markup=False, highlight=False)


@delegate_app.command("find")
def delegate_find(
    words: list[str] = typer.Argument(..., help="The task you want to hand off"),
    limit: int = typer.Option(5, "--limit", "-n", min=1, max=10, help="How many agents"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Find approved agents (registry and remote A2A) that can take a task.

    Examples:
      observal delegate find "generate integration tests for the billing API" --output json
    """
    text = " ".join(w for w in words if w.strip())
    with nullcontext() if output == "json" else spinner("Searching agents..."):
        agents = _guard("Find agents to delegate to", service.find_agents, text, limit=limit)
    if output == "json":
        output_json({"query": text, "agents": agents, "count": len(agents)})
        return
    if not agents:
        rprint("[dim]No approved agent matches that task.[/dim]")
        return
    table = Table(title=f"Agents for: {esc(text)}", padding=(0, 1))
    table.add_column("Agent", style="bold", overflow="fold")
    table.add_column("Kind")
    table.add_column("Match", justify="right")
    table.add_column("Identifier", overflow="fold", style="dim")
    for agent in agents:
        table.add_row(esc(agent["name"]), esc(agent["kind"]), str(agent.get("score") or ""), esc(agent["identifier"]))
    console.print(table)


@delegate_app.command("run")
def delegate_run(
    agent: str = typer.Argument(..., help="Agent identifier (urn:air:...) or registry reference (namespace/slug)"),
    message: str = typer.Argument(..., help="The complete task brief (quote it as one argument)"),
    harness: str | None = typer.Option(
        None, "--harness", "-i", help="Harness to run a registry agent in (default: the harness you call from)"
    ),
    wait: int = typer.Option(600, "--wait", "-w", min=0, max=1800, help="Seconds to wait before returning"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Delegate a task and wait for the result.

    A registry agent runs headless in a throwaway copy of this repository; its
    changes come back as a patch that is not applied. It runs in the harness you
    call from; when that harness cannot run it, pass --harness to choose another. A remote A2A agent is
    called directly with its approved Agent Card.

    Examples:
      observal delegate run acme/security-reviewer "Review the diff on this branch for auth bugs"
      observal delegate run urn:air:agents.acme.com:a2a:triage "Summarize incident INC-42" --wait 0 --output json
    """
    if harness and harness not in VALID_HARNESSES:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown harness: {harness}.",
            operation="Delegate to agent",
            resource="harness",
            remediation=f"Choose one of: {', '.join(VALID_HARNESSES)}.",
        )
    with nullcontext() if output == "json" else spinner("Delegating..."):
        task = _guard(
            "Delegate to agent",
            service.start,
            agent,
            message,
            harness=harness,
            parent_harness=service.caller_harness(),
            wait_seconds=wait,
        )
    _show(task, output)


@delegate_app.command("status")
def delegate_status(
    task_id: str = typer.Argument(..., help="Task id from `delegate run`"),
    wait: int = typer.Option(0, "--wait", "-w", min=0, max=1800, help="Seconds to wait for it to finish"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Show a delegated task, optionally waiting for it to finish.

    Examples:
      observal delegate status 0b6f... --wait 120 --output json
    """
    task = _guard("Show delegated task", service.wait, task_id, wait)
    _show(task, output)


@delegate_app.command("reply")
def delegate_reply(
    task_id: str = typer.Argument(..., help="Task id of a remote task waiting for input"),
    message: str = typer.Argument(..., help="Your answer"),
    wait: int = typer.Option(600, "--wait", "-w", min=0, max=1800, help="Seconds to wait before returning"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Answer a remote agent that asked for more input.

    Examples:
      observal delegate reply 0b6f... "Use the production region"
    """
    task = _guard("Reply to delegated task", service.reply, task_id, message, wait_seconds=wait)
    _show(task, output)


@delegate_app.command("list")
def delegate_list(
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=200, help="How many recent tasks"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """List recent delegated tasks on this machine.

    Examples:
      observal delegate list --output json
    """
    items = tasks.list_tasks(limit)
    if output == "json":
        output_json({"items": items, "total": len(items)})
        return
    if not items:
        rprint("[dim]No delegated tasks yet.[/dim]")
        return
    table = Table(title="Delegated tasks", padding=(0, 1))
    table.add_column("Task", style="dim", no_wrap=True)  # the full id: status, reply and cancel take it
    table.add_column("Agent", style="bold")
    table.add_column("State")
    table.add_column("Started")
    for task in items:
        m = tasks.meta(task)
        table.add_row(task["id"], esc(m.get("targetName") or m.get("target")), _state(task), esc(m.get("createdAt")))
    console.print(table)


@delegate_app.command("cancel")
def delegate_cancel(
    task_id: str = typer.Argument(..., help="Task id to stop"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Stop a delegated task.

    Examples:
      observal delegate cancel 0b6f...
    """
    task = _guard("Cancel delegated task", service.cancel, task_id)
    _show(task, output)


@delegate_app.command("mcp")
def delegate_mcp(
    harness: str | None = typer.Option(None, "--harness", "-i", help="Harness this server runs inside"),
    parent_id: str | None = typer.Option(None, "--parent-id", help="UUID of the agent that owns this server"),
    output: OutputMode = typer.Option(
        "table", "--output", "-o", help="Error format: table or json. MCP traffic always uses stdio."
    ),
):
    """Run the observal-agents MCP server on stdio.

    Pulled agents get it automatically. To add it to a harness by hand,
    register this command as a stdio MCP server, for example with
    ``claude mcp add observal-agents -- <command>``.

    Examples:
      observal delegate mcp --harness claude-code
    """
    from observal_cli.delegation.mcp_server import main as mcp_main

    args = [*(["--harness", harness] if harness else []), *(["--parent-id", parent_id] if parent_id else [])]
    raise typer.Exit(mcp_main(args))
