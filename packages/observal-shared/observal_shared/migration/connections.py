# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Connection parameter dataclasses and helpers for PostgreSQL and ClickHouse."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

from loguru import logger as optic

from observal_shared.migration.exceptions import ConnectionFailedError

if TYPE_CHECKING:
    import asyncpg


@dataclass(frozen=True)
class PgConnParams:
    """PostgreSQL connection parameters."""

    dsn: str


@dataclass(frozen=True)
class ChConnParams:
    """ClickHouse connection parameters."""

    url: str  # original clickhouse:// or clickhouses:// URL

    @property
    def http_url(self) -> str:
        """Derive the HTTP base URL from the connection string."""
        http_url, _, _, _ = parse_clickhouse_url(self.url)
        return http_url

    @property
    def database(self) -> str:
        _, db, _, _ = parse_clickhouse_url(self.url)
        return db

    @property
    def user(self) -> str:
        _, _, user, _ = parse_clickhouse_url(self.url)
        return user

    @property
    def password(self) -> str:
        _, _, _, password = parse_clickhouse_url(self.url)
        return password


def parse_clickhouse_url(url: str) -> tuple[str, str, str, str]:
    """Parse clickhouse://user:pass@host:port/db -> (http_url, db, user, password).

    Supports ``clickhouses://`` for TLS (maps to https, default port 8443).
    """
    if url.startswith("clickhouses://"):
        raw = "https://" + url[len("clickhouses://") :]
        default_port = 8443
    elif url.startswith("clickhouse://"):
        raw = "http://" + url[len("clickhouse://") :]
        default_port = 8123
    else:
        raw = url
        default_port = 8443 if url.startswith("https://") else 8123
    parsed = urlparse(raw)
    if not parsed.hostname:
        raise ValueError("ClickHouse URL requires a hostname")
    scheme = "https" if raw.startswith("https") else "http"
    http_url = f"{scheme}://{parsed.hostname}:{parsed.port or default_port}"
    db = (parsed.path or "/").strip("/") or "default"
    user = unquote(parsed.username) if parsed.username else "default"
    password = unquote(parsed.password) if parsed.password else ""
    return http_url, db, user, password


async def connect_pg(params: PgConnParams) -> asyncpg.Connection:
    """Establish asyncpg connection, verify alembic_version table exists.

    Raises ConnectionFailedError on failure (no typer.Exit).
    """
    import asyncpg

    # Strip SQLAlchemy dialect suffixes (e.g. postgresql+asyncpg:// → postgresql://)
    dsn = params.dsn
    clean_url = dsn.split("+")[0] + dsn[dsn.index("://") :] if "+asyncpg" in dsn or "+psycopg" in dsn else dsn

    try:
        conn = await asyncpg.connect(clean_url)
    except (asyncpg.InvalidCatalogNameError, asyncpg.InvalidPasswordError, OSError, Exception) as exc:
        optic.error("Database connection failed: {} {}", type(exc).__name__, exc)
        raise ConnectionFailedError(f"Database connection failed: {type(exc).__name__}: {exc}") from exc

    # Verify this is an Observal database
    result = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'alembic_version')"
    )
    if not result:
        await conn.close()
        raise ConnectionFailedError("Database does not contain an Observal schema (alembic_version table not found).")

    return conn


async def connect_ch(params: ChConnParams) -> None:
    """Verify ClickHouse is reachable via a health-check query.

    Raises ConnectionFailedError on failure.
    """
    import httpx

    http_url, db, user, password = parse_clickhouse_url(params.url)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            resp = await client.post(http_url, content="SELECT 1", auth=(user, password), params={"database": db})
            resp.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        optic.error("ClickHouse health check failed: {}", exc)
        raise ConnectionFailedError(f"ClickHouse connection failed: {exc}") from exc
