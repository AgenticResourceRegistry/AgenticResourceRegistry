# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import delete, select

import services.dynamic_settings as ds
from config import settings
from database import engine
from models import Base
from models.enterprise_config import RESTART_PENDING_KEY, EnterpriseConfig
from services.audit import setup_audit, shutdown_audit
from services.audit.event_handlers import register_audit_handlers
from services.audit.event_handlers import shutdown_audit as shutdown_audit_handlers
from services.cache import close_cache, init_cache
from services.clickhouse import init_clickhouse
from services.crypto import init_key_manager
from services.migration_uploads import configure_migration_upload_tempdir
from services.redis import close as close_redis


async def ensure_columns(conn) -> None:
    """Add columns that may be missing on existing databases."""
    from sqlalchemy import text

    stmts = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255)",
        "ALTER TABLE mcp_listings ADD COLUMN IF NOT EXISTS environment_variables JSONB",
        "ALTER TABLE agent_versions ADD COLUMN IF NOT EXISTS models_by_harness JSONB NOT NULL DEFAULT '{}'::jsonb",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url TEXT",
    ]
    for stmt in stmts:
        try:
            await conn.execute(text(stmt))
        except Exception:
            pass

    try:
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_demo BOOLEAN DEFAULT false"))
    except Exception:
        pass


async def run_startup_tasks() -> None:
    """Initialize application dependencies used by the FastAPI lifespan."""
    if not settings.SKIP_DDL_ON_STARTUP:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await ensure_columns(conn)
        await init_clickhouse()

    ds.load_external_settings()
    await ds.load_sync_cache()
    artifact_root = os.environ.get("MIGRATION_ARTIFACT_ROOT") or ds.get_sync(
        "migration.artifact_root",
        str(Path.home() / ".observal" / "migration_artifacts"),
    )
    configure_migration_upload_tempdir(artifact_root)
    await ds.import_sso_env_once()
    await ds.reencrypt_on_key_rotation()

    from api.routes.auth import configure_oauth_client

    configure_oauth_client()

    await init_cache()
    retired_key_retention_days = max(
        ds.get_sync_int("jwt.refresh_token_expire_days", 30),
        (ds.get_sync_int("jwt.access_token_expire_minutes", 60) + 1439) // 1440,
        (ds.get_sync_int("jwt.hooks_token_expire_minutes", 43200) + 1439) // 1440,
    )
    init_key_manager(
        key_dir=settings.JWT_KEY_DIR,
        key_password=settings.JWT_KEY_PASSWORD,
        algorithm=settings.JWT_SIGNING_ALGORITHM,
        retired_key_retention_days=retired_key_retention_days,
    )

    from database import async_session as session_factory

    async with session_factory() as db:
        result = await db.execute(
            select(EnterpriseConfig).where(EnterpriseConfig.key == "jwt.refresh_token_expire_days")
        )
        cfg = result.scalar_one_or_none()
        if cfg and cfg.value == "7":
            cfg.value = "30"
            await db.commit()
            await ds.invalidate("jwt.refresh_token_expire_days")
            await ds.refresh_sync_cache()

    from services.demo_accounts import seed_demo_accounts

    async with session_factory() as db:
        await seed_demo_accounts(db)

    setup_audit()
    register_audit_handlers()

    from services.insights import configure_insights

    configure_insights()

    await start_discovery()

    # A successful startup applies all restart-required settings.
    async with session_factory() as db:
        await db.execute(delete(EnterpriseConfig).where(EnterpriseConfig.key == RESTART_PENDING_KEY))
        await db.commit()


async def start_discovery() -> None:
    """Install the reprojection hook and backfill the index if it is empty.

    The backfill runs in the background so a large registry never delays
    startup; the maintenance cron covers anything that fails here.
    """
    import asyncio

    from sqlalchemy import func

    from database import async_session as session_factory
    from models.discovery_entry import DiscoveryEntry
    from services.discovery import hooks as discovery_hooks
    from services.discovery.projection import reproject_all

    discovery_hooks.install()

    async with session_factory() as db:
        count = (await db.execute(select(func.count()).select_from(DiscoveryEntry))).scalar_one()
    if count:
        return

    async def _backfill() -> None:
        try:
            async with session_factory() as db:
                await reproject_all(db)
        except Exception:
            from loguru import logger as optic

            optic.exception("discovery backfill failed")

    task = asyncio.get_running_loop().create_task(_backfill())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


_background_tasks: set = set()


async def run_shutdown_tasks() -> None:
    """Release application dependencies used by the FastAPI lifespan."""
    import asyncio

    from services.discovery import hooks as discovery_hooks

    await discovery_hooks.drain()
    if _background_tasks:
        # Give the initial backfill a bounded chance to commit; never hang shutdown on it.
        _done, pending = await asyncio.wait(list(_background_tasks), timeout=10)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    await shutdown_audit()
    await shutdown_audit_handlers()

    await close_cache()
    await close_redis()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await run_startup_tasks()
    yield
    await run_shutdown_tasks()
