# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Opt-in, local-only control of registry resource auto-updating."""

from __future__ import annotations

import typer
from rich import print as rprint

from observal_cli import auto_update_policy as policy
from observal_cli.errors import ErrorCategory, fail
from observal_cli.render import OutputMode, esc, output_json


def register_freeze(app: typer.Typer) -> None:
    def change(
        *,
        enabled: bool,
        project: bool,
        directory: str | None,
        output: OutputMode,
    ) -> None:
        operation = "Enable automatic registry updates" if enabled else "Freeze automatic registry updates"
        if directory and not project:
            fail(
                ErrorCategory.VALIDATION,
                "--dir requires --project.",
                operation=operation,
                resource="update scope",
                remediation="Add --project or omit --dir.",
            )
        try:
            registry = policy.active_registry()
        except ValueError as error:
            fail(
                ErrorCategory.AUTH,
                "No active Observal registry is configured.",
                operation=operation,
                resource="active registry",
                remediation="Run observal auth login or configure a server URL, then retry.",
                detail=repr(error),
            )
        try:
            root = policy.project_root(directory or ".") if project else None
        except (OSError, ValueError) as error:
            fail(
                ErrorCategory.VALIDATION,
                "The project directory does not exist or is not a directory.",
                operation=operation,
                resource=directory or ".",
                remediation="Choose an existing project root with --dir.",
                detail=repr(error),
            )
        try:
            result = policy.set_policy(registry, enabled=enabled, root=root)
        except policy.AccountUnavailableError as error:
            fail(
                ErrorCategory.AUTH,
                "No locally authenticated Observal account is available for update consent.",
                operation=operation,
                resource="active account",
                remediation="Run `observal auth login` without an overriding token, then retry.",
                detail=repr(error),
            )
        except policy.GateBusyError as error:
            fail(
                ErrorCategory.CONFLICT,
                "An automatic installation or policy change is still in progress; preference was not changed.",
                operation=operation,
                resource="auto-update policy gate",
                remediation="Wait for the current installation to finish, then retry.",
                detail=repr(error),
            )
        except policy.PolicyError as error:
            fail(
                ErrorCategory.VALIDATION,
                "The local auto-update policy is malformed; automatic installs remain disabled.",
                operation=operation,
                resource=str(policy.POLICY_PATH),
                remediation="Repair or move the policy file aside, then retry.",
                detail=repr(error),
            )
        except OSError as error:
            fail(
                ErrorCategory.PERMISSION if isinstance(error, PermissionError) else ErrorCategory.UNAVAILABLE,
                "The local auto-update policy could not be saved; preference was not changed.",
                operation=operation,
                resource=str(policy.POLICY_PATH),
                remediation="Check permissions and disk space, then retry.",
                detail=repr(error),
            )
        if output == "json":
            output_json(result)
            return
        rprint(f"[bold]Registry:[/bold] {esc(result['registry'])}")
        if project:
            rprint(f"[bold]Project:[/bold] {esc(root)}")
            if result["auto_update"] and not result["effective"]:
                rprint(
                    "[yellow]Project opt-in saved, but global updates are frozen. Run `observal unfreeze` to enable them.[/yellow]"
                )
            elif result["effective"]:
                rprint("[green]Project consent saved. No automatic project installers are active yet.[/green]")
            else:
                rprint("[yellow]Automatic project updates frozen here. Manual upgrades remain available.[/yellow]")
        elif enabled:
            rprint("[green]Consent saved for future updates of eligible user-scoped installations.[/green]")
            rprint(
                "[dim]Eligible, unedited user-scope installs that Observal owns (Pi and Claude Code agents, skills and "
                "managed MCPs) can now be updated at interactive startup; anything else stays manual and "
                "says why. Run `observal freeze` to return to notices only. Project installs are not "
                "updated automatically; that would need a separate `observal unfreeze --project` opt-in.[/dim]"
            )
        else:
            rprint("[yellow]Automatic updates frozen. Manual upgrades and update checks remain available.[/yellow]")

    @app.command("freeze")
    def freeze(
        project: bool = typer.Option(False, "--project", help="Freeze automatic updates for this project only"),
        directory: str | None = typer.Option(
            None, "--dir", help="Project root (requires --project; default: current directory)"
        ),
        output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
    ) -> None:
        """Turn off automatic registry updates; does not affect manual upgrades.

        Examples:
          observal freeze
          observal freeze --project --dir ./my-project
        """
        change(enabled=False, project=project, directory=directory, output=output)

    @app.command("unfreeze")
    def unfreeze(
        project: bool = typer.Option(False, "--project", help="Opt this project into automatic updates separately"),
        directory: str | None = typer.Option(
            None, "--dir", help="Project root (requires --project; default: current directory)"
        ),
        output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
    ) -> None:
        """Opt in to automatic updates of eligible managed installations.

        Default scope is user installations. Project opt-in also requires
        global unfreeze; no project installs are updated automatically yet.

        Examples:
          observal unfreeze
          observal unfreeze --project --dir ./my-project
        """
        change(enabled=True, project=project, directory=directory, output=output)
