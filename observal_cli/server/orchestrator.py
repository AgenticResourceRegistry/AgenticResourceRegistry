# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Process orchestrator for embedded Observal services.

Manages PostgreSQL, ClickHouse, and Redis as local subprocesses, then
starts the FastAPI application server. Handles startup ordering,
health checks, initialization, and graceful shutdown.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
from loguru import logger as optic
from rich.console import Console

from observal_cli.server.config_gen import (
    ensure_dirs,
    generate_all_configs,
    generate_pg_hba_conf,
    generate_secret,
)
from observal_cli.server.constants import (
    API_PORT,
    CLICKHOUSE_HTTP_PORT,
    CONFIG_DIR,
    DATA_DIR,
    KEYS_DIR,
    LOG_DIR,
    OBSERVAL_HOME,
    POSTGRES_PORT,
    REDIS_PORT,
    RUN_DIR,
    get_bin_paths,
    get_data_paths,
    get_pid_paths,
)

console = Console()


class ServiceError(Exception):
    """Raised when a service fails to start or become healthy."""


class Orchestrator:
    """Manages lifecycle of all embedded services."""

    def __init__(self, *, port: int = API_PORT, host: str = "127.0.0.1"):
        self.bins = get_bin_paths()
        self.pids = get_pid_paths()
        active_port_file = RUN_DIR / "api.port"
        if port == API_PORT and active_port_file.exists():
            try:
                port = int(active_port_file.read_text().strip())
            except (OSError, ValueError):
                pass
        self.port = port
        self.host = host
        self.data = get_data_paths()
        self._processes: dict[str, subprocess.Popen] = {}
        self._log_handles: list = []
        self._secrets: dict[str, str] | None = None

    # ── Secrets management ─────────────────────────────────────

    def _secrets_path(self) -> Path:
        return OBSERVAL_HOME / ".secrets"

    def _load_or_create_secrets(self) -> dict[str, str]:
        """Load existing secrets or generate new ones on first run."""
        secrets_file = self._secrets_path()
        secrets: dict[str, str] = {}

        if secrets_file.exists():
            for line in secrets_file.read_text().splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    secrets[key.strip()] = value.strip()

        changed = False
        for key, length in [
            ("POSTGRES_PASSWORD", 24),
            ("SECRET_KEY", 32),
        ]:
            if key not in secrets:
                secrets[key] = generate_secret(length)
                changed = True

        if changed:
            secrets_file.parent.mkdir(parents=True, exist_ok=True)
            secrets_file.write_text("\n".join(f"{k}={v}" for k, v in secrets.items()) + "\n")
            secrets_file.chmod(0o600)

        return secrets

    def _check_immediate_death(self, proc: subprocess.Popen, service: str) -> None:
        """Check if a subprocess died immediately after launch."""
        time.sleep(0.2)
        if proc.poll() is not None:
            log_file = LOG_DIR / f"{service}-startup.log"
            msg = f"{service.capitalize()} exited immediately (code {proc.returncode})."
            if log_file.exists():
                msg += f" Check logs: {log_file}"
            raise ServiceError(msg)

    # ── Environment setup ──────────────────────────────────────

    def _build_env(self) -> dict[str, str]:
        """Build environment variables for the FastAPI server."""
        if self._secrets is None:
            self._secrets = self._load_or_create_secrets()
        env = os.environ.copy()
        env.update(
            {
                "DATABASE_URL": f"postgresql+asyncpg://observal@127.0.0.1:{POSTGRES_PORT}/observal",
                "CLICKHOUSE_URL": f"clickhouse://default@127.0.0.1:{CLICKHOUSE_HTTP_PORT}/observal",
                "REDIS_URL": f"redis://127.0.0.1:{REDIS_PORT}",
                "SECRET_KEY": self._secrets["SECRET_KEY"],
                "JWT_KEY_DIR": str(KEYS_DIR),
                "LOG_LEVEL": "INFO",
                "OBSERVAL_EMBEDDED": "1",
            }
        )
        return env

    # ── PostgreSQL ─────────────────────────────────────────────

    def _pg_is_initialized(self) -> bool:
        """Check if PostgreSQL data directory has been initialized."""
        return (self.data["postgres"] / "PG_VERSION").exists()

    def _init_postgres(self) -> None:
        """Initialize PostgreSQL data directory with initdb."""
        console.print("[blue]==>[/blue] Initializing PostgreSQL data directory...")
        data_dir = self.data["postgres"]
        data_dir.mkdir(parents=True, exist_ok=True)

        # Use trust auth for the embedded server (bound to 127.0.0.1 only)
        result = subprocess.run(
            [
                str(self.bins["initdb"]),
                "-D",
                str(data_dir),
                "-U",
                "observal",
                "--auth=trust",
                "--encoding=UTF8",
                "--locale=C",
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise ServiceError(f"initdb failed:\n{result.stderr}")

        # Write our custom postgresql.conf
        generate_all_configs()
        # Copy the generated conf into the data dir
        custom_conf = CONFIG_DIR / "postgresql.conf"
        target_conf = data_dir / "postgresql.conf"
        target_conf.write_text(custom_conf.read_text())

        # Generate pg_hba.conf (trust-based for local embedded use)
        generate_pg_hba_conf()

    def start_postgres(self) -> None:
        """Start PostgreSQL server."""
        if not self._pg_is_initialized():
            self._init_postgres()

        data_dir = self.data["postgres"]
        log_file = LOG_DIR / "postgres.log"

        optic.info("starting PostgreSQL (data_dir={}, port={})", data_dir, POSTGRES_PORT)
        console.print("[blue]==>[/blue] Starting PostgreSQL...")

        result = subprocess.run(
            [
                str(self.bins["pg_ctl"]),
                "start",
                "-D",
                str(data_dir),
                "-l",
                str(log_file),
                "-o",
                f"-p {POSTGRES_PORT} -k {RUN_DIR}",
                "-w",  # Wait for startup
                "-t",
                "30",  # Timeout 30s
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            optic.error("PostgreSQL failed to start: {}", result.stderr[:200])
            raise ServiceError(f"PostgreSQL failed to start:\n{result.stderr}\nCheck logs: {log_file}")

        # Wait for ready
        self._wait_for_postgres()

        # Create the observal database if it doesn't exist
        self._ensure_database()

        console.print("[green]\u2713[/green] PostgreSQL ready")
        optic.info("PostgreSQL is ready (port={})", POSTGRES_PORT)

    def _wait_for_postgres(self, timeout: int = 30) -> None:
        """Wait for PostgreSQL to accept connections."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = subprocess.run(
                [
                    str(self.bins["pg_isready"]),
                    "-h",
                    "127.0.0.1",
                    "-p",
                    str(POSTGRES_PORT),
                    "-U",
                    "observal",
                ],
                capture_output=True,
            )
            if result.returncode == 0:
                return
            time.sleep(0.5)
        optic.error("PostgreSQL did not become ready within {}s", timeout)
        raise ServiceError(f"PostgreSQL did not become ready within {timeout}s")

    def _ensure_database(self) -> None:
        """Create the 'observal' database if it doesn't exist."""
        result = subprocess.run(
            [
                str(self.bins["createdb"]),
                "-h",
                "127.0.0.1",
                "-p",
                str(POSTGRES_PORT),
                "-U",
                "observal",
                "observal",
            ],
            capture_output=True,
            text=True,
        )
        # Error is fine if DB already exists
        if result.returncode != 0 and "already exists" not in result.stderr:
            raise ServiceError(f"Failed to create database:\n{result.stderr}")

    def stop_postgres(self) -> None:
        """Stop PostgreSQL server."""
        data_dir = self.data["postgres"]
        if not self._pg_is_initialized():
            return

        subprocess.run(
            [
                str(self.bins["pg_ctl"]),
                "stop",
                "-D",
                str(data_dir),
                "-m",
                "fast",
                "-t",
                "10",
            ],
            capture_output=True,
        )

    # ── ClickHouse ─────────────────────────────────────────────

    def start_clickhouse(self) -> None:
        """Start ClickHouse server."""
        config_path = CONFIG_DIR / "clickhouse-config.xml"
        if not config_path.exists():
            generate_all_configs()

        # Ensure data subdirs exist
        ch_data = self.data["clickhouse"]
        for subdir in ("tmp", "user_files", "format_schemas"):
            (ch_data / subdir).mkdir(parents=True, exist_ok=True)

        console.print("[blue]==>[/blue] Starting ClickHouse...")
        optic.info("starting ClickHouse")

        log_handle = (LOG_DIR / "clickhouse-startup.log").open("w")
        self._log_handles.append(log_handle)
        proc = subprocess.Popen(
            [
                str(self.bins["clickhouse"]),
                "server",
                "--config-file",
                str(config_path),
                "--pid-file",
                str(self.pids["clickhouse"]),
            ],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self._processes["clickhouse"] = proc

        # Write PID
        self.pids["clickhouse"].write_text(str(proc.pid))

        # Fail fast if process died on launch
        self._check_immediate_death(proc, "clickhouse")

        # Wait for healthy
        self._wait_for_clickhouse()

        # Create the 'observal' database (required before app can connect)
        self._ensure_clickhouse_database()

        console.print("[green]\u2713[/green] ClickHouse ready")
        optic.info("ClickHouse is ready")

    def _ensure_clickhouse_database(self) -> None:
        """Create the 'observal' database in ClickHouse if it doesn't exist."""
        url = f"http://127.0.0.1:{CLICKHOUSE_HTTP_PORT}/"
        try:
            resp = httpx.post(
                url,
                content="CREATE DATABASE IF NOT EXISTS observal",
                timeout=10,
            )
            if resp.status_code != 200:
                console.print(f"[yellow]Warning:[/yellow] ClickHouse CREATE DATABASE returned {resp.status_code}")
        except httpx.ConnectError:
            raise ServiceError("ClickHouse became unreachable while creating database")

    def _wait_for_clickhouse(self, timeout: int = 30) -> None:
        """Wait for ClickHouse HTTP endpoint to respond."""
        deadline = time.time() + timeout
        url = f"http://127.0.0.1:{CLICKHOUSE_HTTP_PORT}/ping"
        while time.time() < deadline:
            try:
                resp = httpx.get(url, timeout=2)
                if resp.status_code == 200:
                    return
            except httpx.ConnectError:
                pass
            time.sleep(0.5)
        raise ServiceError(
            f"ClickHouse did not become ready within {timeout}s. Check logs: {LOG_DIR / 'clickhouse-startup.log'}"
        )

    def stop_clickhouse(self) -> None:
        """Stop ClickHouse server."""
        pid_file = self.pids["clickhouse"]
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                # Wait for exit
                for _ in range(20):
                    try:
                        os.kill(pid, 0)
                        time.sleep(0.5)
                    except ProcessLookupError:
                        break
                else:
                    os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass
            pid_file.unlink(missing_ok=True)

        if "clickhouse" in self._processes:
            proc = self._processes.pop("clickhouse")
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    # ── Redis ──────────────────────────────────────────────────

    def start_redis(self) -> None:
        """Start Redis server."""
        config_path = CONFIG_DIR / "redis.conf"
        if not config_path.exists():
            generate_all_configs()

        console.print("[blue]==>[/blue] Starting Redis...")
        optic.info("starting Redis")

        proc = subprocess.Popen(
            [
                str(self.bins["redis_server"]),
                str(config_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._processes["redis"] = proc
        self.pids["redis"].write_text(str(proc.pid))

        # Fail fast if process died on launch
        self._check_immediate_death(proc, "redis")

        # Wait for ready
        self._wait_for_redis()
        console.print("[green]\u2713[/green] Redis ready")
        optic.info("Redis is ready")

    def _wait_for_redis(self, timeout: int = 10) -> None:
        """Wait for Redis to accept connections."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = subprocess.run(
                [
                    str(self.bins["redis_cli"]),
                    "-h",
                    "127.0.0.1",
                    "-p",
                    str(REDIS_PORT),
                    "ping",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and "PONG" in result.stdout:
                return
            time.sleep(0.3)
        raise ServiceError(f"Redis did not become ready within {timeout}s. Check logs: {LOG_DIR / 'redis.log'}")

    def stop_redis(self) -> None:
        """Stop Redis server."""
        pid_file = self.pids["redis"]
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                for _ in range(10):
                    try:
                        os.kill(pid, 0)
                        time.sleep(0.5)
                    except ProcessLookupError:
                        break
            except (ProcessLookupError, ValueError):
                pass
            pid_file.unlink(missing_ok=True)

        if "redis" in self._processes:
            proc = self._processes.pop("redis")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    # ── FastAPI Server ─────────────────────────────────────────

    def start_api(self, *, foreground: bool = True) -> subprocess.Popen | None:
        """Start the FastAPI/Uvicorn server.

        Args:
            foreground: If True, run in foreground (blocks). If False, daemonize.

        Returns:
            The subprocess.Popen if backgrounded, None if foreground.
        """
        env = self._build_env()
        server_dir = self._find_server_dir()
        python = self._find_python()

        cmd = [
            python,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--log-level",
            "info",
        ]

        console.print(f"[blue]==>[/blue] Starting Observal API on :{self.port}...")
        optic.info("starting Observal API on port {}", self.port)

        if foreground:
            proc = subprocess.Popen(
                cmd,
                cwd=str(server_dir),
                env=env,
            )
            self._processes["api"] = proc
            self.pids["api"].write_text(str(proc.pid))
            (RUN_DIR / "api.port").write_text(str(self.port))
            return None
        else:
            log_handle = (LOG_DIR / "api.log").open("a")
            self._log_handles.append(log_handle)
            proc = subprocess.Popen(
                cmd,
                cwd=str(server_dir),
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            self._processes["api"] = proc
            self.pids["api"].write_text(str(proc.pid))
            (RUN_DIR / "api.port").write_text(str(self.port))
            return proc

    def _find_server_dir(self) -> Path:
        """Locate the observal-server directory.

        Works both in development (source tree) and in PyInstaller bundle.
        """
        # PyInstaller bundle: look for server code in _MEIPASS
        if hasattr(sys, "_MEIPASS"):
            bundle_dir = Path(sys._MEIPASS)
            if (bundle_dir / "main.py").exists():
                return bundle_dir
            if (bundle_dir / "observal-server" / "main.py").exists():
                return bundle_dir / "observal-server"

        # Development: look relative to this file
        repo_root = Path(__file__).resolve().parents[2]
        server_dir = repo_root / "observal-server"
        if server_dir.exists():
            return server_dir

        raise ServiceError(
            "Cannot locate observal-server directory. Ensure you're running from the repo or a packaged binary."
        )

    def _find_python(self) -> str:
        """Find the correct Python interpreter for running the server.

        In development, uses the project .venv (which has server deps).
        In PyInstaller bundle, uses sys.executable (everything is bundled).
        """
        if hasattr(sys, "_MEIPASS"):
            return sys.executable

        # Development: use project .venv which has all server dependencies
        repo_root = Path(__file__).resolve().parents[2]
        venv_python = repo_root / ".venv" / "bin" / "python"
        if venv_python.exists():
            return str(venv_python)

        # Fallback to sys.executable
        return sys.executable

    def stop_api(self) -> None:
        """Stop the FastAPI server."""
        pid_file = self.pids["api"]
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                for _ in range(10):
                    try:
                        os.kill(pid, 0)
                        time.sleep(0.5)
                    except ProcessLookupError:
                        break
                else:
                    os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass
            pid_file.unlink(missing_ok=True)
        (RUN_DIR / "api.port").unlink(missing_ok=True)

        if "api" in self._processes:
            proc = self._processes.pop("api")
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    # ── Initialization ─────────────────────────────────────────

    def _is_first_run(self) -> bool:
        """Check if this is the first time starting (no data exists)."""
        return not self._pg_is_initialized()

    def run_migrations(self) -> None:
        """Apply PostgreSQL and ClickHouse migrations before API startup."""
        env = self._build_env()
        server_dir = self._find_server_dir()
        python = self._find_python()
        if not (server_dir / "alembic.ini").exists():
            raise ServiceError(f"Cannot find Alembic configuration in {server_dir}")

        commands = (
            ([python, "-m", "alembic", "upgrade", "head"], "PostgreSQL"),
            ([python, "-m", "services.clickhouse.migrations"], "ClickHouse"),
        )
        for command, database in commands:
            console.print(f"[blue]==>[/blue] Running {database} migrations...")
            result = subprocess.run(
                command,
                cwd=str(server_dir),
                env=env,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise ServiceError(f"{database} migration failed: {result.stderr[:200]}")

    # ── Auto-bootstrap ─────────────────────────────────────────

    def _auto_bootstrap(self) -> dict | None:
        """Bootstrap the admin user after API is healthy.

        Waits for the API to respond on /livez (simple liveness, no DB check),
        then POSTs to /api/v1/auth/bootstrap.
        Ignores 400 (already initialized) - idempotent.
        """
        base_url = f"http://127.0.0.1:{self.port}"

        # Wait for API liveness (no DB dependency - just confirms Uvicorn is up)
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                resp = httpx.get(f"{base_url}/livez", timeout=2)
                if resp.status_code == 200:
                    break
            except (httpx.ConnectError, httpx.ReadTimeout):
                pass
            time.sleep(0.5)
        else:
            console.print("[yellow]Warning:[/yellow] API did not become ready for bootstrap")
            return None

        # Call bootstrap
        try:
            resp = httpx.post(f"{base_url}/api/v1/auth/bootstrap", timeout=10)
            if resp.status_code == 200:
                payload = resp.json()
                if not isinstance(payload, dict) or not payload.get("access_token") or not payload.get("refresh_token"):
                    console.print("[yellow]Warning:[/yellow] Bootstrap returned invalid credentials")
                    return None
                console.print("[green]\u2713[/green] Admin user created")
                return payload
            if resp.status_code != 400:
                console.print(f"[yellow]Warning:[/yellow] Bootstrap returned {resp.status_code}")
        except (httpx.ConnectError, httpx.ReadTimeout, ValueError):
            console.print("[yellow]Warning:[/yellow] Could not complete API bootstrap")
        return None

    def _configure_cli(self, credentials: dict | None) -> None:
        """Point the CLI at the embedded server and persist real bootstrap tokens."""
        from observal_cli.config import load, remove, save

        cfg = load()
        target_url = f"http://localhost:{self.port}"
        if cfg.get("server_url") == target_url and not credentials:
            return
        if cfg.get("server_url") != target_url:
            remove("access_token", "refresh_token", "api_key")
        values = {"server_url": target_url}
        if credentials:
            user = credentials.get("user")
            values.update(
                access_token=credentials["access_token"],
                refresh_token=credentials["refresh_token"],
                user_id=str(user.get("id", "")) if isinstance(user, dict) else "",
            )
        save(values)

    def _install_hooks(self) -> list[str]:
        """Install harness telemetry hooks (Claude Code, Kiro, etc.) if not already present.

        Runs the equivalent of `observal doctor patch --all-harnesses` non-interactively.
        This ensures traces flow to the embedded server without manual setup.
        """
        warnings: list[str] = []

        # Claude Code hooks
        claude_dir = Path.home() / ".claude"
        if claude_dir.is_dir():
            try:
                from observal_cli import settings_reconciler
                from observal_cli.harness_specs.claude_code_hooks_spec import get_desired_hooks

                desired_hooks = get_desired_hooks()
                changes = settings_reconciler.reconcile(desired_hooks, {}, dry_run=False)
                if changes:
                    console.print("[green]\u2713[/green] Claude Code hooks installed")
            except Exception as error:
                optic.warning("Claude Code hook installation failed: {}", error)
                warnings.append("Claude Code hooks were not installed; run observal doctor patch.")

        # Kiro hooks
        kiro_agents_dir = Path.home() / ".kiro" / "agents"
        if kiro_agents_dir.is_dir():
            try:
                from observal_cli.cmd_doctor import _patch_kiro

                if _patch_kiro(dry_run=False):
                    console.print("[green]\u2713[/green] Kiro hooks installed")
            except Exception as error:
                optic.warning("Kiro hook installation failed: {}", error)
                warnings.append("Kiro hooks were not installed; run observal doctor patch.")
        return warnings

    # ── Full lifecycle ─────────────────────────────────────────

    def start_all(self, *, foreground: bool = True) -> None:
        """Start all services in correct order.

        Args:
            foreground: If True, API runs in foreground (blocks on Ctrl+C).
        """
        ensure_dirs()
        KEYS_DIR.mkdir(parents=True, exist_ok=True)

        first_run = self._is_first_run()

        try:
            self.start_postgres()
            self.start_clickhouse()
            self.start_redis()

            if first_run:
                console.print("[blue]==>[/blue] First run - initializing databases...")

            self.run_migrations()

            self.start_api(foreground=foreground)

            # Auto-bootstrap admin user for embedded mode
            credentials = self._auto_bootstrap()

            # Auto-configure CLI to point at this server
            self._configure_cli(credentials)

            # Install harness hooks for telemetry (non-interactive)
            for warning in self._install_hooks():
                console.print(f"[yellow]Warning:[/yellow] {warning}")

            console.print()
            console.print("[bold green]\u2713 Observal is running![/bold green]")
            console.print()
            console.print(f"  Dashboard:  [cyan]http://localhost:{self.port}[/cyan]")
            console.print(f"  API:        [cyan]http://localhost:{self.port}/api/v1/[/cyan]")
            console.print(f"  Health:     [cyan]http://localhost:{self.port}/readyz[/cyan]")
            console.print()
            console.print(f"  Data:       {DATA_DIR}")
            console.print(f"  Logs:       {LOG_DIR}")
            console.print(f"  Config:     {CONFIG_DIR}")
            console.print()
            console.print("  Press [bold]Ctrl+C[/bold] to stop")
            console.print()

            if foreground and "api" in self._processes:
                try:
                    self._processes["api"].wait()
                except KeyboardInterrupt:
                    console.print("\n[yellow]Shutting down...[/yellow]")
                    self.stop_all()

        except Exception as error:
            console.print(f"\n[red]Error:[/red] {error}")
            console.print("[yellow]Cleaning up...[/yellow]")
            self.stop_all()
            if isinstance(error, ServiceError):
                raise
            raise ServiceError(str(error)) from error

    def stop_all(self) -> None:
        """Stop all services in reverse order."""
        self.stop_api()
        self.stop_redis()
        self.stop_clickhouse()
        self.stop_postgres()

        # Close any open log file handles
        for handle in self._log_handles:
            try:
                handle.close()
            except OSError as exc:
                optic.debug("Failed to close log handle: error={}", exc)

        self._log_handles.clear()

        console.print("[green]\u2713[/green] All services stopped")

    def status(self) -> dict[str, str]:
        """Check status of all services.

        Returns dict mapping service name to status string.
        """
        statuses = {}

        # PostgreSQL
        if self._pg_is_initialized():
            result = subprocess.run(
                [
                    str(self.bins["pg_isready"]),
                    "-h",
                    "127.0.0.1",
                    "-p",
                    str(POSTGRES_PORT),
                    "-U",
                    "observal",
                ],
                capture_output=True,
            )
            statuses["postgres"] = "running" if result.returncode == 0 else "stopped"
        else:
            statuses["postgres"] = "not initialized"

        # ClickHouse
        try:
            resp = httpx.get(
                f"http://127.0.0.1:{CLICKHOUSE_HTTP_PORT}/ping",
                timeout=2,
            )
            statuses["clickhouse"] = "running" if resp.status_code == 200 else "stopped"
        except httpx.ConnectError:
            statuses["clickhouse"] = "stopped"

        # Redis
        if self.bins["redis_cli"].exists():
            result = subprocess.run(
                [
                    str(self.bins["redis_cli"]),
                    "-h",
                    "127.0.0.1",
                    "-p",
                    str(REDIS_PORT),
                    "ping",
                ],
                capture_output=True,
                text=True,
            )
            statuses["redis"] = "running" if "PONG" in (result.stdout or "") else "stopped"
        else:
            statuses["redis"] = "not initialized"

        # API
        try:
            resp = httpx.get(f"http://127.0.0.1:{self.port}/livez", timeout=2)
            statuses["api"] = "running" if resp.status_code == 200 else "stopped"
        except httpx.ConnectError:
            statuses["api"] = "stopped"

        return statuses

    def is_running(self) -> bool:
        """Check if any services are currently running."""
        s = self.status()
        return any(v == "running" for v in s.values())

    def reset(self) -> None:
        """Stop all services and wipe data directories."""
        if self.is_running():
            self.stop_all()

        console.print("[yellow]==>[/yellow] Wiping data directories...")
        import shutil

        data_root = DATA_DIR.resolve()
        for path in self.data.values():
            resolved = path.resolve()
            if not resolved.is_relative_to(data_root):
                raise ServiceError(f"Refusing to delete unmanaged path: {resolved}")
            if path.exists():
                shutil.rmtree(path)

        # Remove secrets to regenerate on next start
        secrets_file = self._secrets_path()
        secrets_file.unlink(missing_ok=True)

        console.print("[green]\u2713[/green] Reset complete. Run 'observal server start' to reinitialize.")
