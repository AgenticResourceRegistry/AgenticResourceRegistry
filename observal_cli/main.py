# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Swathi Saravanan <ss4522@cornell.edu>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Observal CLI: MCP Server & Agent Registry."""

import atexit
import logging
import os
import sys

if sys.platform == "win32" and not os.environ.get("PYTHONIOENCODING"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_shared = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "packages", "observal-shared"))
if os.path.isdir(_shared) and _shared not in sys.path:
    sys.path.insert(0, _shared)

import typer

from observal_cli.cmd_auth import version_callback
from observal_cli.errors import ErrorHandlingGroup


def _check_package_conflict() -> None:
    """Reject the legacy 'observal' package through the shared error contract."""
    from importlib.metadata import PackageNotFoundError, metadata

    try:
        meta = metadata("observal")
    except PackageNotFoundError:
        return

    if meta.get("Name", "").lower() == "observal-cli":
        return

    from observal_cli.errors import ErrorCategory, fail

    fail(
        ErrorCategory.CONFLICT,
        "The legacy observal package conflicts with observal-cli.",
        operation="Start Observal CLI",
        resource="installed Python packages",
        remediation="Run `uv pip uninstall observal` or `pip uninstall observal`, then retry.",
    )


# ── Version callback for --version flag ───────────────────


def _version_option(value: bool):
    if value:
        version_callback()
        raise typer.Exit()


app = typer.Typer(
    name="observal",
    cls=ErrorHandlingGroup,
    help=(
        "Observal: MCP Server & Agent Registry CLI\n\n"
        "Examples:\n"
        "  observal scan\n"
        "  observal agent list\n"
        "  observal registry mcp list"
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
    pretty_exceptions_enable=False,
)


@app.callback()
def main(
    ctx: typer.Context,
    version: bool | None = typer.Option(
        None,
        "--version",
        "-V",
        help="Show CLI version and exit.",
        callback=_version_option,
        is_eager=True,
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    debug: bool = typer.Option(False, "--debug", help="Debug logging"),
):
    """Observal: MCP Server & Agent Registry CLI"""
    from observal_cli.optic import setup_optic

    # Scan must decide whether it is the strictly read-only inventory variant
    # before any startup migration, skill synchronization, or file logging.
    if ctx.invoked_subcommand == "scan":
        ctx.meta["observal.scan.startup"] = (debug, verbose)
        setup_optic(debug=False, verbose=debug or verbose)
        return

    setup_optic(debug=debug, verbose=verbose)
    _check_package_conflict()

    if debug:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
    elif verbose:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    _migrate_legacy_mcp_configs()

    # One-time local state migrations
    _try_lockfile_migration()


def _migrate_legacy_mcp_configs() -> None:
    """Rewrite legacy wrapped MCP entries before running a command."""
    from rich import print as rprint

    from observal_cli.config import migrate_shimmed_mcp_configs
    from observal_cli.errors import ErrorCategory, fail, machine_output_requested

    try:
        migrated = migrate_shimmed_mcp_configs()
    except RuntimeError as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            "Legacy MCP configuration migration failed.",
            operation="Migrate legacy MCP configuration",
            resource="local harness configuration",
            remediation="Run with --debug, repair the reported configuration, and retry.",
            detail=repr(error),
        )

    if migrated and not machine_output_requested():
        rprint(
            f"[green]Migrated {len(migrated)} legacy MCP config file(s) to direct commands.[/green]",
            file=sys.stderr,
        )
        for path in migrated:
            rprint(f"  [dim]{path}[/dim]", file=sys.stderr)


def _sync_bundled_skills() -> None:
    """Hash-check and synchronize bundled skills before every command."""
    try:
        from observal_cli.skill_installer import sync_observal_skills

        sync_observal_skills()
    except OSError as error:
        from observal_cli.errors import ErrorCategory, fail

        fail(
            ErrorCategory.PERMISSION if isinstance(error, PermissionError) else ErrorCategory.UNEXPECTED,
            "Bundled Observal skills could not be synchronized.",
            operation="Synchronize bundled skills",
            resource="installed Observal skills",
            remediation="Reinstall the CLI or check harness skill-directory permissions, then retry.",
            detail=repr(error),
        )


def _try_lockfile_migration() -> None:
    """Synchronize bundled skills, then migrate legacy agent markers when needed."""
    _sync_bundled_skills()
    try:
        from observal_cli.lockfile import CONFIG_DIR, LOCKFILE_PATH, migrate_agent_markers

        # Don't run if the config dir doesn't exist yet (auth login hasn't happened)
        if not CONFIG_DIR.exists():
            return
        if not LOCKFILE_PATH.exists():
            migrate_agent_markers()
    except Exception as error:
        from loguru import logger as optic

        from observal_cli.errors import emit_warning

        optic.warning("legacy agent-marker migration failed: error_type={}", type(error).__name__)
        emit_warning(
            "local_state_migration",
            "Legacy installation state could not be migrated.",
            operation="Migrate legacy installation state",
            remediation="Run `observal doctor` before relying on installed-state data.",
            detail=repr(error),
        )


# ── Register command groups ──────────────────────────────

from observal_cli.cmd_a2a import a2a_app
from observal_cli.cmd_agent import agent_app
from observal_cli.cmd_api import register_api
from observal_cli.cmd_archive import add_archive_commands
from observal_cli.cmd_auth import auth_app, register_config
from observal_cli.cmd_bulk import bulk_app
from observal_cli.cmd_co_authors import make_co_authors_typer
from observal_cli.cmd_component import version_app
from observal_cli.cmd_delegate import delegate_app
from observal_cli.cmd_discover import discover_app
from observal_cli.cmd_doctor import doctor_app
from observal_cli.cmd_fork import add_fork_command
from observal_cli.cmd_hook import hook_app
from observal_cli.cmd_inbox import inbox_app
from observal_cli.cmd_insights import insights_app
from observal_cli.cmd_logs import logs_app
from observal_cli.cmd_mcp import mcp_app
from observal_cli.cmd_mcp_sync import mcp_sync_app
from observal_cli.cmd_migrate import migrate_app
from observal_cli.cmd_models import models_app
from observal_cli.cmd_ops import (
    admin_app,
    ops_app,
    self_app,
)
from observal_cli.cmd_outdated import register_outdated
from observal_cli.cmd_prompt import prompt_app
from observal_cli.cmd_pull import register_pull
from observal_cli.cmd_recommend import recommend_app
from observal_cli.cmd_sandbox import sandbox_app
from observal_cli.cmd_scan import register_scan
from observal_cli.cmd_share import share_app
from observal_cli.cmd_skill import skill_app
from observal_cli.cmd_support import support_app
from observal_cli.cmd_team import team_app
from observal_cli.cmd_transfer import add_transfer_owner_command

# ═══════════════════════════════════════════════════════════
# registry_app: Component registry parent group
# ═══════════════════════════════════════════════════════════

registry_app = typer.Typer(
    name="registry",
    help=(
        "Component registry (MCPs, skills, hooks, prompts, sandboxes, remote A2A agents)\n\n"
        "Examples:\n"
        "  observal registry mcp list\n"
        "  observal registry skill list\n"
        "  observal registry recommend"
    ),
    no_args_is_help=True,
)

registry_app.add_typer(mcp_app, name="mcp")
mcp_app.add_typer(mcp_sync_app, name="sync")
registry_app.add_typer(skill_app, name="skill")
registry_app.add_typer(hook_app, name="hook")
registry_app.add_typer(prompt_app, name="prompt")
registry_app.add_typer(sandbox_app, name="sandbox")
registry_app.add_typer(models_app, name="models")
registry_app.add_typer(version_app, name="version")
registry_app.add_typer(recommend_app, name="recommend")
registry_app.add_typer(bulk_app, name="bulk")
registry_app.add_typer(a2a_app, name="a2a")

# ── Co-authors and ownership sub-commands ─────────────────
mcp_app.add_typer(make_co_authors_typer("mcps"), name="co-authors")
skill_app.add_typer(make_co_authors_typer("skills"), name="co-authors")
hook_app.add_typer(make_co_authors_typer("hooks"), name="co-authors")
prompt_app.add_typer(make_co_authors_typer("prompts"), name="co-authors")
sandbox_app.add_typer(make_co_authors_typer("sandboxes"), name="co-authors")
agent_app.add_typer(make_co_authors_typer("agents"), name="co-authors")
add_transfer_owner_command(mcp_app, "mcps")
add_transfer_owner_command(skill_app, "skills")
add_transfer_owner_command(hook_app, "hooks")
add_transfer_owner_command(prompt_app, "prompts")
add_transfer_owner_command(sandbox_app, "sandboxes")
add_transfer_owner_command(agent_app, "agents")
for _kind, _app in (
    ("mcp", mcp_app),
    ("skill", skill_app),
    ("hook", hook_app),
    ("prompt", prompt_app),
    ("sandbox", sandbox_app),
):
    add_fork_command(_app, _kind)
add_archive_commands(mcp_app, "mcps")
add_archive_commands(skill_app, "skills")
add_archive_commands(hook_app, "hooks")
add_archive_commands(prompt_app, "prompts")
add_archive_commands(sandbox_app, "sandboxes")

# ── Auth subgroup ────────────────────────────────────────
app.add_typer(auth_app, name="auth")

# ── Primary user workflows (root) ─────────────────────────
register_config(app)
register_api(app)
register_scan(app)
register_outdated(app)
app.add_typer(discover_app, name="discover")
app.add_typer(delegate_app, name="delegate")


# ── Agent pull (full-featured, lives under `observal agent pull`) ──
register_pull(agent_app)

# ── Subgroups ─────────────────────────────────────────────
app.add_typer(registry_app, name="registry")
app.add_typer(inbox_app, name="inbox")
app.add_typer(agent_app, name="agent")
app.add_typer(share_app, name="share")
app.add_typer(team_app, name="team")
app.add_typer(ops_app, name="ops")
app.add_typer(admin_app, name="admin")
app.add_typer(self_app, name="self")
app.add_typer(doctor_app, name="doctor")

# ── Nest under parent groups ──────────────────────────────
# logs → ops logs (dev log viewer, complements traces/telemetry)
ops_app.add_typer(logs_app, name="logs")
# insights → ops insights (agent insight reports)
ops_app.add_typer(insights_app, name="insights")
# support → doctor support (diagnostic bundles, related to doctor troubleshooting)
doctor_app.add_typer(support_app, name="support")
# migrate → server migrate (operator infra tooling)

# Reconcile (push local sessions to server)
from observal_cli.cmd_reconcile_cli import register_reconcile

register_reconcile(app)

# Server management (embedded + Docker)
from observal_cli.cmd_server import server_app

server_app.add_typer(migrate_app, name="migrate")
app.add_typer(server_app, name="server")


def _show_update_banner() -> None:
    """Post-command hook: notify when a different CLI version is recommended.

    Never mutates the installed binary. Surfaces both upgrades (community
    GitHub-latest) and downgrades (server recommends an older version) as a
    notice with the explicit command to run. Version mismatches that block
    operation are still enforced by the version enforcement gate.
    """
    import sys as _sys

    if not (_sys.stdout.isatty() and _sys.stderr.isatty()):
        return
    from observal_cli.errors import machine_output_requested

    if machine_output_requested(_sys.argv[1:]):
        return
    if len(_sys.argv) > 1 and _sys.argv[1] in ("self", "server"):
        return
    if os.environ.get("CI") or os.environ.get("OBSERVAL_NO_UPDATE_CHECK"):
        return

    try:
        from observal_cli.version_check import maybe_check

        update = maybe_check()
        if not update:
            return

        from rich import print as _rprint

        from observal_cli.install_detector import downgrade_command, upgrade_command

        if update.direction == "downgrade":
            _rprint(
                f"\n[yellow]CLI v{update.current} is ahead of server v{update.latest}.[/yellow]\n"
                f"  Downgrade to match: [bold cyan]{downgrade_command(update.latest)}[/bold cyan]",
                file=_sys.stderr,
            )
        else:
            _rprint(
                f"\n[green]Update available: v{update.current} \u2192 v{update.latest}[/green]\n"
                f"  Run: [bold cyan]{upgrade_command(update.latest)}[/bold cyan]",
                file=_sys.stderr,
            )
    except Exception as error:
        from loguru import logger as optic

        optic.debug("CLI update check failed: error_type={}", type(error).__name__)


# Register update banner as atexit handler so it runs via any entry point
atexit.register(_show_update_banner)

if __name__ == "__main__":
    app()
