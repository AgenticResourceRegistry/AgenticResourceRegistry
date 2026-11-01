# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""CLI commands for managing the embedded Observal server.

Provides `observal server start|stop|status|logs|reset|install|config` commands
for running a fully self-contained Observal instance with embedded PostgreSQL,
ClickHouse, and Redis.
"""

from __future__ import annotations

import os
import subprocess
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated

import typer
from loguru import logger as optic
from packaging.version import InvalidVersion, Version
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from observal_cli.errors import ErrorCategory, fail
from observal_cli.render import OutputMode, output_json, output_json_line
from observal_cli.server.constants import API_PORT, CONFIG_DIR, LOG_DIR, OBSERVAL_HOME

server_app = typer.Typer(
    name="server",
    help=(
        "Manage the embedded Observal server (PostgreSQL + ClickHouse + Redis + API).\n\n"
        "Examples:\n"
        "  observal server status\n"
        "  observal server start\n"
        "  observal server logs api"
    ),
    no_args_is_help=True,
)

console = Console()


def _is_json(output: OutputMode) -> bool:
    return output == "json"


def _verified_service_states(orchestrator, *, running: bool, operation: str) -> list[dict[str, str]]:
    """Read final embedded-service state and fail when it contradicts the requested lifecycle action."""
    try:
        statuses = orchestrator.status()
    except Exception as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            "The final embedded service state could not be verified.",
            operation=operation,
            resource="embedded services",
            remediation="Run `observal server status --output json` and inspect the server logs.",
            detail=repr(error),
        )
    expected = "running" if running else "stopped"
    expected_services = {"postgres", "clickhouse", "redis", "api"}
    states = (
        [{"service": service, "status": status} for service, status in statuses.items()]
        if isinstance(statuses, dict)
        else []
    )
    if not isinstance(statuses, dict) or not expected_services.issubset(statuses):
        fail(
            ErrorCategory.UNAVAILABLE,
            "The embedded service status response was incomplete.",
            operation=operation,
            resource="embedded services",
            remediation="Run `observal server status --output json` and inspect the server logs.",
            result={"expected": expected, "services": states},
        )
    verified = bool(statuses) and (
        all(status == "running" for status in statuses.values())
        if running
        else not any(status == "running" for status in statuses.values())
    )
    if not verified:
        fail(
            ErrorCategory.UNAVAILABLE,
            f"Embedded services did not reach the expected {expected} state.",
            operation=operation,
            resource="embedded services",
            remediation="Inspect the returned service states and server logs, then retry.",
            result={"expected": expected, "services": states},
        )
    return states


@contextmanager
def _quiet_output(output: OutputMode):
    """Suppress nested human progress while a command builds its JSON result."""
    if not _is_json(output):
        yield
        return
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        yield


@server_app.command()
def start(
    port: int = typer.Option(API_PORT, "--port", "-p", min=1, max=65535, help="API port"),
    host: str = typer.Option("0.0.0.0", "--host", help="Bind address"),
    background: Annotated[bool, typer.Option("--background", "-d", help="Run in background (daemonize)")] = False,
    output: Annotated[
        OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
    ] = OutputMode.table,
) -> None:
    """Start the embedded services and API.

    JSON mode requires background startup and never emits progress output.

    Examples:
        observal server start
        observal server start --port 9000
        observal server start --background --output json
    """
    import socket

    from observal_cli.server.deps import all_installed, install_dependencies
    from observal_cli.server.orchestrator import Orchestrator, ServiceError
    from observal_cli.server.updater import check_for_update

    if _is_json(output) and not background:
        fail(
            ErrorCategory.VALIDATION,
            "JSON startup requires background mode.",
            operation="Start embedded server",
            resource="startup mode",
            remediation="Pass --background and retry.",
        )

    def port_available(candidate: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
                return True
            except OSError:
                return False

    requested_port = port
    if not port_available(port):
        if port == API_PORT:
            port = next(
                (candidate for candidate in (port + 1, port + 2, port + 10, port + 100) if port_available(candidate)), 0
            )
        else:
            port = 0
        if not port:
            fail(
                ErrorCategory.CONFLICT,
                f"Port {requested_port} is already in use.",
                operation="Start embedded server",
                resource=f"TCP port {requested_port}",
                remediation="Choose another port or stop the conflicting process.",
            )
        if not _is_json(output):
            console.print(f"[yellow]Port {requested_port} in use,[/yellow] using :{port} instead")

    try:
        with _quiet_output(output):
            check_for_update(quiet=True)
            if not all_installed():
                console.print("[blue]==>[/blue] First run: installing database dependencies...")
                install_dependencies()
            orchestrator = Orchestrator(port=port, host=host)
            if orchestrator.is_running():
                fail(
                    ErrorCategory.CONFLICT,
                    "Embedded services are already running.",
                    operation="Start embedded server",
                    resource="embedded server",
                    remediation="Run observal server stop or observal server restart.",
                )
            orchestrator.start_all(foreground=not background)
            if background:
                check_for_update(quiet=False)
    except (ServiceError, RuntimeError) as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            "The embedded server could not start.",
            operation="Start embedded server",
            resource="embedded services",
            remediation="Check local dependencies and server logs, then retry.",
            detail=repr(error),
        )

    services = (
        _verified_service_states(orchestrator, running=True, operation="Start embedded server") if background else []
    )
    if _is_json(output):
        output_json(
            {
                "status": "started",
                "mode": "embedded",
                "host": host,
                "port": port,
                "background": True,
                "used_fallback_port": port != requested_port,
                "services": services,
            }
        )


@server_app.command()
def stop(
    output: Annotated[
        OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
    ] = OutputMode.table,
) -> None:
    """Stop all embedded services.

    Examples:
        observal server stop
        observal server stop --output json
    """
    from observal_cli.server.orchestrator import Orchestrator, ServiceError

    orchestrator = Orchestrator()
    try:
        with _quiet_output(output):
            orchestrator.stop_all()
    except (ServiceError, RuntimeError, OSError) as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            "The embedded server could not stop cleanly.",
            operation="Stop embedded server",
            resource="embedded services",
            remediation="Inspect server status and logs, then retry.",
            detail=repr(error),
        )
    services = _verified_service_states(orchestrator, running=False, operation="Stop embedded server")
    if _is_json(output):
        output_json({"status": "stopped", "mode": "embedded", "services": services})


@server_app.command()
def restart(
    port: int = typer.Option(API_PORT, "--port", "-p", min=1, max=65535, help="API port"),
    host: str = typer.Option("0.0.0.0", "--host", help="Bind address"),
    background: Annotated[bool, typer.Option("--background", "-d", help="Run in background (daemonize)")] = False,
    output: Annotated[
        OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
    ] = OutputMode.table,
) -> None:
    """Restart all embedded services.

    JSON mode requires background startup.

    Examples:
        observal server restart
        observal server restart --background --output json
    """
    from observal_cli.server.orchestrator import Orchestrator, ServiceError

    if _is_json(output) and not background:
        fail(
            ErrorCategory.VALIDATION,
            "JSON restart requires background mode.",
            operation="Restart embedded server",
            resource="startup mode",
            remediation="Pass --background and retry.",
        )
    try:
        with _quiet_output(output):
            orchestrator = Orchestrator(port=port, host=host)
            if orchestrator.is_running():
                orchestrator.stop_all()
            orchestrator.start_all(foreground=not background)
    except ServiceError as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            "The embedded server could not restart.",
            operation="Restart embedded server",
            resource="embedded services",
            remediation="Check local dependencies and server logs, then retry.",
            detail=repr(error),
        )
    services = (
        _verified_service_states(orchestrator, running=True, operation="Restart embedded server") if background else []
    )
    if _is_json(output):
        output_json(
            {
                "status": "restarted",
                "mode": "embedded",
                "host": host,
                "port": port,
                "background": True,
                "services": services,
            }
        )


@server_app.command()
def status(
    output: Annotated[
        OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
    ] = OutputMode.table,
) -> None:
    """Show embedded service status.

    Examples:
        observal server status
        observal server status --output json
    """
    from observal_cli.server.constants import CLICKHOUSE_HTTP_PORT, POSTGRES_PORT, REDIS_PORT
    from observal_cli.server.orchestrator import Orchestrator

    orchestrator = Orchestrator()
    statuses = orchestrator.status()
    ports = {
        "postgres": POSTGRES_PORT,
        "clickhouse": CLICKHOUSE_HTTP_PORT,
        "redis": REDIS_PORT,
        "api": orchestrator.port,
    }
    payload = {
        "healthy": all(state == "running" for state in statuses.values()),
        "services": [
            {"service": service, "status": state, "port": ports.get(service)} for service, state in statuses.items()
        ],
    }
    if _is_json(output):
        output_json(payload)
        return

    table = Table(title="Observal Service Status")
    table.add_column("Service", style="bold")
    table.add_column("Status")
    table.add_column("Port")
    styles = {
        "running": "[green]running[/green]",
        "stopped": "[red]stopped[/red]",
        "not initialized": "[dim]not initialized[/dim]",
    }
    for item in payload["services"]:
        table.add_row(
            str(item["service"]).capitalize(),
            styles.get(str(item["status"]), str(item["status"])),
            str(item["port"] or "-"),
        )
    console.print(table)


@server_app.command()
def logs(
    service: str = typer.Argument(
        None,
        help="Service to show logs for (postgres, clickhouse, redis, api). Default: all.",
    ),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    lines: int = typer.Option(50, "--lines", "-n", min=1, help="Number of lines to show"),
    output: Annotated[
        OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
    ] = OutputMode.table,
) -> None:
    """Show embedded service logs.

    Follow mode emits JSON Lines when JSON output is selected.

    Examples:
        observal server logs
        observal server logs postgres --lines 100
        observal server logs api --follow --output json
    """
    log_files = {
        "postgres": LOG_DIR / "postgres.log",
        "clickhouse": LOG_DIR / "clickhouse-startup.log",
        "redis": LOG_DIR / "redis.log",
        "api": LOG_DIR / "api.log",
    }
    if service and service not in log_files:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown service: {service}.",
            operation="Read embedded server logs",
            resource="service filter",
            remediation=f"Choose one of: {', '.join(log_files)}.",
        )
    if _is_json(output) and follow and not service:
        fail(
            ErrorCategory.VALIDATION,
            "JSON log following requires one service.",
            operation="Follow embedded server logs",
            resource="service filter",
            remediation="Provide postgres, clickhouse, redis, or api.",
        )

    selected = [log_files[service]] if service else [path for path in log_files.values() if path.exists()]
    files = [path for path in selected if path.exists()]
    if not files:
        fail(
            ErrorCategory.NOT_FOUND,
            "No embedded service logs were found.",
            operation="Read embedded server logs",
            resource=str(LOG_DIR),
            remediation="Start the embedded server, then retry.",
        )

    if follow:
        command = (
            ["tail", "-n", str(lines), "-f", str(files[0])]
            if _is_json(output)
            else [
                "tail",
                "-n",
                str(lines),
                "-f",
                *map(str, files),
            ]
        )
        try:
            if _is_json(output):
                process = subprocess.Popen(command, stdout=subprocess.PIPE, text=True)
                try:
                    assert process.stdout is not None
                    for line in process.stdout:
                        output_json_line({"service": service, "line": line.rstrip("\n")})
                finally:
                    process.terminate()
                    process.wait(timeout=5)
            else:
                subprocess.run(command, check=False)
        except KeyboardInterrupt:
            return
        return

    records = []
    for name, path in log_files.items():
        if path not in files:
            continue
        tail = path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
        records.append({"service": name, "path": str(path), "lines": tail})
    if _is_json(output):
        output_json({"follow": False, "logs": records})
        return
    for record in records:
        if len(records) > 1:
            console.print(f"\n[bold]==> {escape(str(record['service']))} <==[/bold]")
        for line in record["lines"]:
            console.print(escape(str(line)))


@server_app.command()
def install(
    upgrade: bool = typer.Option(False, "--upgrade", help="Re-download even if already installed"),
    output: Annotated[
        OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
    ] = OutputMode.table,
) -> None:
    """Download verified embedded database binaries.

    Examples:
        observal server install
        observal server install --upgrade --output json
    """
    from observal_cli.server.deps import install_dependencies

    try:
        with _quiet_output(output):
            install_dependencies(force=upgrade)
    except RuntimeError as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            "Embedded dependency installation failed.",
            operation="Install embedded server dependencies",
            resource="dependency release",
            remediation="Check network access, disk space, and published checksums, then retry.",
            detail=repr(error),
        )
    if _is_json(output):
        output_json({"status": "installed", "services": ["postgres", "clickhouse", "redis"], "refreshed": upgrade})
    else:
        console.print("\n[green]✓[/green] All dependencies installed")
        console.print("  Run [cyan]observal server start[/cyan] to start the server")


@server_app.command()
def reset(
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
    output: Annotated[
        OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
    ] = OutputMode.table,
) -> None:
    """Stop embedded services and wipe database data and generated secrets.

    Examples:
        observal server reset
        observal server reset --force --output json
    """
    from observal_cli.server.orchestrator import Orchestrator

    if _is_json(output) and not force:
        fail(
            ErrorCategory.VALIDATION,
            "JSON reset requires explicit confirmation.",
            operation="Reset embedded server",
            resource="embedded database data",
            remediation="Pass --force and retry.",
        )
    if not force and not typer.confirm("Delete embedded database data and generated secrets?"):
        raise typer.Abort()

    with _quiet_output(output):
        Orchestrator().reset()
    if _is_json(output):
        output_json({"status": "reset", "deleted": ["postgres", "clickhouse", "redis", "generated secrets"]})


@server_app.command()
def config(
    output: Annotated[
        OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
    ] = OutputMode.table,
) -> None:
    """Show embedded server paths and ports.

    Examples:
        observal server config
        observal server config --output json
    """
    from observal_cli.server.constants import CLICKHOUSE_HTTP_PORT, POSTGRES_PORT, REDIS_PORT

    config_file = OBSERVAL_HOME / "observal.yaml"
    payload = {
        "mode": "embedded",
        "home_directory": str(OBSERVAL_HOME),
        "ports": {
            "api": API_PORT,
            "postgres": POSTGRES_PORT,
            "clickhouse": CLICKHOUSE_HTTP_PORT,
            "redis": REDIS_PORT,
        },
        "config_directory": str(CONFIG_DIR),
        "log_directory": str(LOG_DIR),
        "config_file": str(config_file) if config_file.exists() else None,
    }
    if _is_json(output):
        output_json(payload)
        return
    table = Table(title="Observal Server Configuration")
    table.add_column("Setting", style="bold")
    table.add_column("Value")
    table.add_row("Mode", "embedded")
    table.add_row("Home directory", payload["home_directory"])
    for service, port in payload["ports"].items():
        table.add_row(f"{service.capitalize()} port", str(port))
    table.add_row("Config dir", payload["config_directory"])
    table.add_row("Log dir", payload["log_directory"])
    table.add_row("Config file", payload["config_file"] or "[dim]not created (using defaults)[/dim]")
    console.print(table)


# ═══════════════════════════════════════════════════════════
# Server upgrade/rollback commands (Docker mode)
# ═══════════════════════════════════════════════════════════


def _find_compose_dir() -> Path:
    """Find the Docker Compose directory for the Observal deployment."""
    # Check common locations
    candidates = [
        Path.cwd() / "docker",  # dev: project root with docker/ subdir
        Path.cwd(),  # production: cwd IS the compose dir
        Path("/opt/observal"),  # server-package default install
        OBSERVAL_HOME / "docker",
    ]
    for d in candidates:
        if (d / "docker-compose.yml").exists() or (d / "compose.yml").exists():
            return d
    return Path("/opt/observal")  # Default


def _require_compose_dir() -> Path:
    compose_dir = _find_compose_dir()
    if not any((compose_dir / name).is_file() for name in ("docker-compose.yml", "compose.yml")):
        fail(
            ErrorCategory.NOT_FOUND,
            "A Docker Compose deployment was not found.",
            operation="Manage Docker server deployment",
            resource=str(compose_dir),
            remediation="Run the command from a deployment directory containing compose.yml.",
        )
    return compose_dir


def _get_current_server_version(compose_dir: Path) -> str:
    """Get current OBSERVAL_VERSION from .env file."""
    # Check .env in compose dir first, then parent (dev setup has .env at project root)
    optic.trace("compose_dir={}", compose_dir)
    candidates = [
        compose_dir / ".env",
        compose_dir.parent / ".env",
    ]
    for env_file in candidates:
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("OBSERVAL_VERSION="):
                    return line.split("=", 1)[1].strip().strip('"')
    return "unknown"


def _find_env_file(compose_dir: Path) -> Path:
    """Find the .env file (may be in compose dir or parent)."""
    optic.trace("compose_dir={}", compose_dir)
    if (compose_dir / ".env").exists():
        return compose_dir / ".env"
    if (compose_dir.parent / ".env").exists():
        return compose_dir.parent / ".env"
    return compose_dir / ".env"  # Default (will be created)


def _get_health_url(compose_dir: Path) -> str:
    """Get the health check URL from .env or defaults.

    Reads LB_HOST_PORT (preferred) or API_HOST_PORT from .env to determine
    the correct port. Falls back to 8000 (standard API port) if neither is set.
    Every shipped nginx.conf routes /health to the API's readiness check;
    /readyz falls through to the web app there and would pass without the API.
    """
    optic.trace("compose_dir={}", compose_dir)
    env_file = _find_env_file(compose_dir)
    lb_port = None
    api_port = None
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("LB_HOST_PORT="):
                lb_port = line.split("=", 1)[1].strip().strip('"') or None
            elif line.startswith("API_HOST_PORT="):
                api_port = line.split("=", 1)[1].strip().strip('"') or None
    port = lb_port or api_port or "8000"
    return f"http://localhost:{port}/health"


def _refresh_load_balancer(compose_dir: Path) -> None:
    """Restart nginx so it resolves the recreated API and web containers.

    nginx resolves upstream hostnames once at startup, so after `up -d`
    replaces the API container it keeps proxying to the old address.
    Failures only warn: the health check that follows reports the outcome.
    """
    optic.trace("compose_dir={}", compose_dir)
    try:
        result = subprocess.run(
            ["docker", "compose", "restart", "observal-lb"],
            cwd=compose_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        optic.warning("load balancer restart failed error={}", type(error).__name__)
        return
    if result.returncode != 0:
        optic.warning("load balancer restart failed returncode={}", result.returncode)


def _update_env_version(compose_dir: Path, version: str) -> None:
    """Atomically update OBSERVAL_VERSION while preserving unrelated settings."""
    env_file = _find_env_file(compose_dir)
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    for index, line in enumerate(lines):
        if line.startswith("OBSERVAL_VERSION="):
            lines[index] = f"OBSERVAL_VERSION={version}"
            break
    else:
        lines.append(f"OBSERVAL_VERSION={version}")
    env_file.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            "w", encoding="utf-8", dir=env_file.parent, prefix=f".{env_file.name}.", delete=False
        ) as file:
            temporary = Path(file.name)
            file.write("\n".join(lines) + "\n")
            file.flush()
            os.fsync(file.fileno())
        temporary.chmod(0o600)
        temporary.replace(env_file)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _server_upgrade(version: str | None, skip_backup: bool, dry_run: bool, force: bool) -> dict:
    """Apply a Docker server upgrade and return its structured result."""
    from observal_cli import version_check
    from observal_cli.upgrade_lock import UpgradeLockError, acquire_lock, release_lock

    compose_dir = _require_compose_dir()
    current = _get_current_server_version(compose_dir)
    try:
        Version(current)
    except InvalidVersion as error:
        fail(
            ErrorCategory.VALIDATION,
            "The current Docker server version is not configured.",
            operation="Upgrade Docker server",
            resource=str(_find_env_file(compose_dir)),
            remediation="Set OBSERVAL_VERSION to the deployed version and retry.",
            detail=repr(error),
        )

    if version:
        target = version.removeprefix("v")
    else:
        with console.status("Checking for latest version..."):
            rel = version_check._fetch_from_github()
        if not rel:
            fail(
                ErrorCategory.UNAVAILABLE,
                "The latest server release could not be resolved.",
                operation="Upgrade Docker server",
                resource="GitHub releases",
                remediation="Check network access or provide --version explicitly.",
            )
        target = str(rel["latest_version"]).removeprefix("v")

    try:
        Version(target)
    except InvalidVersion as error:
        fail(
            ErrorCategory.VALIDATION,
            "The target server version is invalid.",
            operation="Upgrade Docker server",
            resource=target,
            remediation="Provide a valid released version.",
            detail=repr(error),
        )

    if target == current:
        console.print(f"[green]Already on v{escape(current)}.[/green]")
        return {"status": "current", "current_version": current, "target_version": target, "changed": False}

    # Verify image exists on GHCR before any state changes
    with console.status("Verifying image on GHCR..."):
        if not version_check.verify_server_image_exists(target):
            fail(
                ErrorCategory.NOT_FOUND,
                "The target server image was not found.",
                operation="Upgrade Docker server",
                resource=f"ghcr.io/agenticresourceregistry/observal-api:{target}",
                remediation="Run observal server versions and choose an available version.",
            )

    if dry_run:
        console.print(f"[dim]Dry run: would upgrade v{current} → v{target}[/dim]")
        console.print(f"[dim]  Pull: ghcr.io/agenticresourceregistry/observal-api:{target}[/dim]")
        console.print(f"[dim]  Pull: ghcr.io/agenticresourceregistry/observal-web:{target}[/dim]")
        console.print(f"[dim]  Compose dir: {escape(str(compose_dir))}[/dim]")
        return {
            "status": "planned",
            "current_version": current,
            "target_version": target,
            "compose_directory": str(compose_dir),
            "backup": not skip_backup,
            "changed": False,
        }

    if not force:
        console.print(f"  Current: [dim]v{current}[/dim]")
        console.print(f"  Target:  [green]v{target}[/green]")
        console.print(f"  Images:  [dim]ghcr.io/agenticresourceregistry/observal-{{api,web}}:{target}[/dim]")
        if not typer.confirm("\nProceed with server upgrade?"):
            raise typer.Abort()

    try:
        lock = acquire_lock("server")
    except UpgradeLockError as error:
        fail(
            ErrorCategory.CONFLICT,
            "Another server upgrade operation is active.",
            operation="Upgrade Docker server",
            resource="server upgrade lock",
            remediation="Wait for the active operation to finish, then retry.",
            detail=repr(error),
        )

    backup_path = None
    try:
        # Backup
        if not skip_backup:
            from observal_cli.server.backup import create_backup

            console.print("[blue]==>[/blue] Creating backup...")
            backup_path = create_backup(compose_dir, current)
            console.print(f"  Backup: {escape(str(backup_path))}")

        # Pull new images
        console.print(f"[blue]==>[/blue] Pulling images for v{target}...")
        env = {**os.environ, "OBSERVAL_VERSION": target}
        result = subprocess.run(
            ["docker", "compose", "pull"],
            cwd=compose_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            fail(
                ErrorCategory.UNAVAILABLE,
                "Docker image download failed.",
                operation="Upgrade Docker server",
                resource=f"server images v{target}",
                remediation="Check Docker and registry access, then retry.",
                detail=f"docker_returncode={result.returncode}",
            )

        # Update .env
        _update_env_version(compose_dir, target)

        # Recreate containers
        console.print("[blue]==>[/blue] Recreating containers...")
        result = subprocess.run(
            ["docker", "compose", "up", "-d"],
            cwd=compose_dir,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            _update_env_version(compose_dir, current)
            fail(
                ErrorCategory.UNAVAILABLE,
                "Docker container recreation failed.",
                operation="Upgrade Docker server",
                resource=str(compose_dir),
                remediation="Inspect Docker Compose logs and retry.",
                detail=f"docker_returncode={result.returncode}",
            )
        _refresh_load_balancer(compose_dir)

        # Health check
        console.print("[blue]==>[/blue] Health check...")
        import time

        import httpx

        healthy = False
        for _ in range(24):  # 120s total
            time.sleep(5)
            try:
                resp = httpx.get(_get_health_url(compose_dir), timeout=5)
                if resp.status_code == 200:
                    healthy = True
                    break
            except httpx.RequestError:
                continue

        if not healthy:
            console.print("[red]Health check failed! Rolling back...[/red]")
            _update_env_version(compose_dir, current)
            rollback = subprocess.run(
                ["docker", "compose", "up", "-d"],
                cwd=compose_dir,
                capture_output=True,
                timeout=300,
            )
            _refresh_load_balancer(compose_dir)
            fail(
                ErrorCategory.UNAVAILABLE,
                "The upgraded server failed its health check.",
                operation="Upgrade Docker server",
                resource=_get_health_url(compose_dir),
                remediation="Inspect Docker Compose logs; the previous version was requested again.",
                detail=f"rollback_returncode={rollback.returncode}",
            )

        console.print(f"[green]✓ Upgraded to v{target}[/green]")
        if backup_path:
            console.print(f"  Backup: {escape(str(backup_path))}")
        console.print("  Rollback: [dim]observal server rollback[/dim]")
        return {
            "status": "upgraded",
            "current_version": current,
            "target_version": target,
            "backup": str(backup_path) if backup_path else None,
            "compose_directory": str(compose_dir),
            "changed": True,
        }

    finally:
        release_lock(lock)


@server_app.command(name="upgrade")
def server_upgrade(
    version: str | None = typer.Option(None, "--version", "-v", help="Target version; defaults to latest"),
    skip_backup: bool = typer.Option(False, "--skip-backup", help="Skip pre-upgrade PostgreSQL backup"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the plan without applying changes"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip interactive confirmation"),
    output: Annotated[
        OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
    ] = OutputMode.table,
) -> None:
    """Upgrade a local Docker deployment.

    JSON mutation requires explicit confirmation. Dry run is read-only.

    Examples:
        observal server upgrade --dry-run --output json
        observal server upgrade --version 1.2.3 --force --output json
    """
    if _is_json(output) and not dry_run and not force:
        fail(
            ErrorCategory.VALIDATION,
            "JSON upgrade requires explicit confirmation.",
            operation="Upgrade Docker server",
            resource="Docker deployment",
            remediation="Pass --force and retry.",
        )
    try:
        with _quiet_output(output):
            result = _server_upgrade(version, skip_backup, dry_run, force)
    except (RuntimeError, subprocess.SubprocessError) as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            "The Docker server upgrade could not complete.",
            operation="Upgrade Docker server",
            resource="local Docker deployment",
            remediation="Check Docker, local storage, and network access, then retry.",
            detail=repr(error),
        )
    if _is_json(output):
        output_json(result)


def _server_rollback(from_backup: str | None, force: bool) -> dict:
    """Restore PostgreSQL and Docker image version from one managed backup."""
    from observal_cli.server.backup import BACKUPS_DIR, list_backups, restore_backup
    from observal_cli.upgrade_lock import UpgradeLockError, acquire_lock, release_lock

    compose_dir = _require_compose_dir()
    current = _get_current_server_version(compose_dir)

    backups = list_backups()
    if not backups and not from_backup:
        fail(
            ErrorCategory.NOT_FOUND,
            "No managed server backups were found.",
            operation="Rollback Docker server",
            resource=str(BACKUPS_DIR),
            remediation="Upgrade with backups enabled before attempting rollback.",
        )

    backup_dir = (Path(from_backup).expanduser() if from_backup else Path(backups[0]["path"])).resolve()
    managed_root = BACKUPS_DIR.resolve()
    if not backup_dir.is_relative_to(managed_root):
        fail(
            ErrorCategory.PERMISSION,
            "The backup path is outside the managed backup directory.",
            operation="Rollback Docker server",
            resource=str(backup_dir),
            remediation=f"Choose a backup under {managed_root}.",
        )
    if not backup_dir.is_dir() or not (backup_dir / "pg.dump").is_file():
        fail(
            ErrorCategory.NOT_FOUND,
            "The managed PostgreSQL backup was not found.",
            operation="Rollback Docker server",
            resource=str(backup_dir),
            remediation="Run observal server versions to list available backups.",
        )

    prev_version = backup_dir.name.split("-")[0].removeprefix("v")
    try:
        Version(prev_version)
    except InvalidVersion as error:
        fail(
            ErrorCategory.VALIDATION,
            "The backup directory does not identify a valid server version.",
            operation="Rollback Docker server",
            resource=str(backup_dir),
            remediation="Choose a backup created by observal server upgrade.",
            detail=repr(error),
        )

    if not force:
        console.print(f"  Current: [dim]v{current}[/dim]")
        console.print(f"  Rollback to: [yellow]v{prev_version}[/yellow]")
        console.print(f"  Backup: [dim]{escape(str(backup_dir))}[/dim]")
        if not typer.confirm("\nProceed with rollback?"):
            raise typer.Abort()

    try:
        lock = acquire_lock("server")
    except UpgradeLockError as error:
        fail(
            ErrorCategory.CONFLICT,
            "Another server upgrade operation is active.",
            operation="Rollback Docker server",
            resource="server upgrade lock",
            remediation="Wait for the active operation to finish, then retry.",
            detail=repr(error),
        )

    try:
        # Restore database
        console.print("[blue]==>[/blue] Restoring database...")
        restore_backup(backup_dir, compose_dir)

        # Revert version
        _update_env_version(compose_dir, prev_version)

        # Recreate containers with previous images
        console.print("[blue]==>[/blue] Recreating containers...")
        recreate = subprocess.run(
            ["docker", "compose", "up", "-d"],
            cwd=compose_dir,
            capture_output=True,
            timeout=300,
        )
        if recreate.returncode != 0:
            fail(
                ErrorCategory.UNAVAILABLE,
                "Docker container recreation failed after restoring PostgreSQL.",
                operation="Rollback Docker server",
                resource=str(compose_dir),
                remediation="Inspect Docker Compose logs before retrying.",
                detail=f"docker_returncode={recreate.returncode}",
            )
        _refresh_load_balancer(compose_dir)

        # Health check
        import time

        import httpx

        console.print("[blue]==>[/blue] Health check...")
        healthy = False
        for _ in range(24):
            time.sleep(5)
            try:
                resp = httpx.get(_get_health_url(compose_dir), timeout=5)
                if resp.status_code == 200:
                    healthy = True
                    break
            except httpx.RequestError:
                continue

        if not healthy:
            fail(
                ErrorCategory.UNAVAILABLE,
                "Rollback completed but the server is unhealthy.",
                operation="Rollback Docker server",
                resource=_get_health_url(compose_dir),
                remediation="Inspect Docker Compose logs before taking further action.",
            )
        console.print(f"[green]✓ Rolled back to v{prev_version}[/green]")
        console.print("[yellow]ClickHouse telemetry was not restored.[/yellow]")
        return {
            "status": "rolled_back",
            "from_version": current,
            "to_version": prev_version,
            "backup": str(backup_dir),
            "postgres_restored": True,
            "clickhouse_restored": False,
            "healthy": True,
        }
    finally:
        release_lock(lock)


@server_app.command(name="rollback")
def server_rollback(
    from_backup: str | None = typer.Option(None, "--from-backup", help="Managed backup directory to restore"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip interactive confirmation"),
    output: Annotated[
        OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
    ] = OutputMode.table,
) -> None:
    """Restore PostgreSQL and the Docker image version from backup.

    ClickHouse telemetry is left unchanged. JSON mode requires explicit confirmation.

    Examples:
        observal server rollback --force --output json
        observal server rollback --from-backup ~/.observal/backups/v1.2.2-20260521T120000 --force
    """
    if _is_json(output) and not force:
        fail(
            ErrorCategory.VALIDATION,
            "JSON rollback requires explicit confirmation.",
            operation="Rollback Docker server",
            resource="Docker deployment",
            remediation="Pass --force and retry.",
        )
    try:
        with _quiet_output(output):
            result = _server_rollback(from_backup, force)
    except (RuntimeError, subprocess.SubprocessError) as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            "The Docker server rollback could not complete.",
            operation="Rollback Docker server",
            resource="local Docker deployment",
            remediation="Check Docker and the managed backup, then retry.",
            detail=repr(error),
        )
    if _is_json(output):
        output_json(result)


@server_app.command(name="versions")
def server_versions(
    output: Annotated[
        OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
    ] = OutputMode.table,
) -> None:
    """List Docker image versions and managed PostgreSQL backups.

    Examples:
        observal server versions
        observal server versions --output json
    """
    from observal_cli import version_check
    from observal_cli.server.backup import list_backups

    compose_dir = _require_compose_dir()
    current = _get_current_server_version(compose_dir)
    with _quiet_output(output):
        available = version_check.fetch_available_server_images()
    if not available:
        fail(
            ErrorCategory.UNAVAILABLE,
            "Available server versions could not be loaded.",
            operation="List Docker server versions",
            resource="GHCR server images",
            remediation="Check network and GHCR access, then retry.",
        )
    backups = list_backups()
    backup_versions = {item["name"].split("-")[0].removeprefix("v"): item for item in backups}
    versions = []
    for version in ([current] if current != "unknown" else []) + available[:10]:
        if any(item["version"] == version for item in versions):
            continue
        backup = backup_versions.get(version)
        versions.append(
            {
                "version": version,
                "current": version == current,
                "image_available": version in available,
                "backup": backup,
            }
        )
    payload = {
        "current_version": None if current == "unknown" else current,
        "image_repository": "ghcr.io/agenticresourceregistry/observal-{api,web}",
        "versions": versions,
    }
    if _is_json(output):
        output_json(payload)
        return
    table = Table(title="Server Versions")
    table.add_column("Version", style="bold")
    table.add_column("Status")
    table.add_column("GHCR")
    table.add_column("PostgreSQL backup")
    for item in versions:
        backup = item["backup"]
        table.add_row(
            escape(item["version"]),
            "[green]← current[/green]" if item["current"] else "",
            "✓" if item["image_available"] else "-",
            f"{backup.get('size_mb', 0)} MB" if backup else "-",
        )
    console.print(table)
