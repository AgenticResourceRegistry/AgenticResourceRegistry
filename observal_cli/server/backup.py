# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Database backup and restore for Observal server upgrades.

Supports:
  - PostgreSQL: pg_dump (custom format) via Docker exec
  - ClickHouse: schema export via HTTP
  - Backup retention pruning
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003 - used at runtime

from rich import print as rprint

from observal_cli.config import CONFIG_DIR

BACKUPS_DIR = CONFIG_DIR / "backups"
DEFAULT_RETENTION = 3  # Keep last N backups


def create_backup(compose_dir: Path, from_version: str) -> Path:
    """Create a pre-upgrade backup of PostgreSQL + ClickHouse.

    Args:
        compose_dir: Directory containing docker-compose.yml.
        from_version: Current server version (used in backup dir name).

    Returns:
        Path to the backup directory.

    Raises:
        RuntimeError: If backup creation fails.
    """
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    backup_dir = BACKUPS_DIR / f"v{from_version}-{ts}"
    backup_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    backup_dir.chmod(0o700)

    # PostgreSQL backup (custom format for selective restore)
    pg_dump_path = backup_dir / "pg.dump"
    rprint("[dim]  Backing up PostgreSQL...[/dim]")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "observal-db",
            "pg_dump",
            "-U",
            "postgres",
            "-Fc",
            "observal",
        ],
        capture_output=True,
        cwd=compose_dir,
        timeout=300,
    )
    if result.returncode != 0:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise RuntimeError(f"pg_dump failed: {result.stderr.decode()[:200]}")

    pg_dump_path.write_bytes(result.stdout)
    pg_dump_path.chmod(0o600)
    if pg_dump_path.stat().st_size < 100:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise RuntimeError("pg_dump produced empty/tiny file - backup may be invalid")

    pg_size_mb = pg_dump_path.stat().st_size / (1024 * 1024)
    rprint(f"[dim]  PostgreSQL: {pg_size_mb:.1f} MB[/dim]")

    # ClickHouse schema export
    ch_schema_path = backup_dir / "clickhouse_schema.sql"
    rprint("[dim]  Backing up ClickHouse schema...[/dim]")
    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "observal-clickhouse",
                "clickhouse-client",
                "--query",
                "SELECT name, create_table_query FROM system.tables WHERE database = 'observal'",
            ],
            capture_output=True,
            text=True,
            cwd=compose_dir,
            timeout=60,
        )
        if result.returncode == 0:
            ch_schema_path.write_text(result.stdout)
            ch_schema_path.chmod(0o600)
            rprint(f"[dim]  ClickHouse schema: {len(result.stdout)} bytes[/dim]")
        else:
            rprint("[yellow]  ClickHouse schema export failed (non-critical)[/yellow]")
    except (subprocess.TimeoutExpired, OSError):
        rprint("[yellow]  ClickHouse schema export timed out (non-critical)[/yellow]")

    return backup_dir


def restore_backup(backup_path: Path, compose_dir: Path) -> None:
    """Restore PostgreSQL from a backup.

    Args:
        backup_path: Path to backup directory containing pg.dump.
        compose_dir: Directory containing docker-compose.yml.
    """
    pg_dump = backup_path / "pg.dump"
    if not pg_dump.exists():
        raise RuntimeError(f"Backup file not found: {pg_dump}")

    rprint("[dim]  Restoring PostgreSQL...[/dim]")

    # Pipe pg.dump content into pg_restore via docker exec
    with pg_dump.open("rb") as f:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "observal-db",
                "pg_restore",
                "-U",
                "postgres",
                "-d",
                "observal",
                "--clean",
                "--if-exists",
                "--exit-on-error",
            ],
            stdin=f,
            capture_output=True,
            cwd=compose_dir,
            timeout=300,
        )
    if result.returncode != 0:
        raise RuntimeError(f"pg_restore failed: {result.stderr.decode()[:200]}")

    rprint("[dim]  PostgreSQL restored.[/dim]")


def prune_backups(retention: int = DEFAULT_RETENTION) -> list[Path]:
    """Remove old backups beyond retention count.

    Returns list of pruned paths. Never deletes the most recent backup.
    """
    if not BACKUPS_DIR.exists():
        return []

    backups = sorted(BACKUPS_DIR.iterdir(), key=lambda p: p.name, reverse=True)
    if len(backups) <= retention:
        return []

    to_prune = backups[retention:]
    pruned = []
    for path in to_prune:
        if path.is_dir():
            shutil.rmtree(path)
            pruned.append(path)
    return pruned


def list_backups() -> list[dict]:
    """List all available backups with metadata."""
    if not BACKUPS_DIR.exists():
        return []

    results = []
    for path in sorted(BACKUPS_DIR.iterdir(), key=lambda p: p.name, reverse=True):
        if not path.is_dir():
            continue
        pg_dump = path / "pg.dump"
        size_bytes = pg_dump.stat().st_size if pg_dump.exists() else 0
        results.append(
            {
                "path": str(path),
                "name": path.name,
                "size_bytes": size_bytes,
                "size_mb": round(size_bytes / (1024 * 1024), 1),
                "has_pg": pg_dump.exists(),
                "has_ch": (path / "clickhouse_schema.sql").exists(),
            }
        )
    return results


def estimate_backup_size(compose_dir: Path) -> int:
    """Estimate backup size in bytes (for pre-flight disk space check)."""
    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "observal-db",
                "psql",
                "-U",
                "postgres",
                "-t",
                "-c",
                "SELECT pg_database_size('observal');",
            ],
            capture_output=True,
            text=True,
            cwd=compose_dir,
            timeout=10,
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass
    # Fallback: assume 100MB
    return 100 * 1024 * 1024
