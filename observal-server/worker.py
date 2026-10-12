# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""arq background worker: startup/shutdown hooks, job registration, and cron scheduling."""

from arq.cron import cron
from arq.worker import func
from loguru import logger as optic

from jobs.catalog import batch_generate_insights, generate_insight_report, refresh_user_profiles
from jobs.maintenance import (
    maintain_clickhouse,
    purge_inbox_items,
    reproject_discovery_entries,
    sync_component_sources,
)
from jobs.mcp_sync import MAX_TRIES as MCP_SYNC_MAX_TRIES
from jobs.mcp_sync import sync_mcp_webhook
from jobs.migration import MAX_MIGRATION_JOB_TIMEOUT_SECONDS, purge_migration_artifacts, run_migration_job
from jobs.review_checks import run_review_checks
from jobs.usage_ping import submit_usage_ping
from logging_config import setup_logging
from services.alert_evaluator import evaluate_alerts
from services.optic import setup_optic
from services.redis import parse_redis_settings
from services.retention import run_retention_purge

setup_logging()
setup_optic(mode="local")  # Worker always uses local mode for dev visibility


async def startup(ctx: dict):
    import services.dynamic_settings as ds
    from services.insights import configure_insights

    ds.load_external_settings()
    await ds.load_sync_cache()
    configure_insights()
    optic.info("arq worker started")
    optic.info(
        "worker started with {} functions, {} cron jobs", len(WorkerSettings.functions), len(WorkerSettings.cron_jobs)
    )


async def shutdown(ctx: dict):
    optic.info("arq worker shutting down")
    optic.info("worker shutting down")


class WorkerSettings:
    """arq worker configuration."""

    functions = [
        sync_component_sources,
        func(sync_mcp_webhook, max_tries=MCP_SYNC_MAX_TRIES),
        evaluate_alerts,
        maintain_clickhouse,
        generate_insight_report,
        batch_generate_insights,
        run_retention_purge,
        func(run_migration_job, timeout=MAX_MIGRATION_JOB_TIMEOUT_SECONDS),
        refresh_user_profiles,
        purge_inbox_items,
        submit_usage_ping,
        reproject_discovery_entries,
        run_review_checks,
    ]
    cron_jobs = [
        cron(sync_component_sources, hour={0, 6, 12, 18}, minute={0}),  # Every 6 hours
        cron(evaluate_alerts, second={0}, timeout=55),  # Every minute
        cron(run_review_checks, second={0, 15, 30, 45}, timeout=300),  # Automated review checks
        cron(maintain_clickhouse, hour={0, 4, 8, 12, 16, 20}, minute={0}, timeout=120),  # Every 4 hours
        cron(batch_generate_insights, weekday={0}, hour={6}, minute={0}, timeout=300),  # Weekly Monday 6AM
        cron(
            run_retention_purge, hour={1, 7, 13, 19}, minute={30}, timeout=3600, unique=True
        ),  # Every 6 hours (retention)
        cron(
            purge_migration_artifacts, hour={2, 8, 14, 20}, minute={0}, timeout=300, unique=True
        ),  # Every 6 hours (artifacts)
        cron(refresh_user_profiles, hour={3}, minute={15}, timeout=600, unique=True),  # Daily 3:15AM
        cron(purge_inbox_items, hour={4}, minute={45}, timeout=300, unique=True),  # Daily 4:45AM
        # Poll at each supported boundary; the job applies the super admin's selected cadence.
        cron(submit_usage_ping, hour={0, 6, 12, 18}, minute={30}, timeout=90, unique=True),
        # Safety net under the per-change reprojection hook.
        cron(reproject_discovery_entries, hour={1, 7, 13, 19}, minute={5}, timeout=600, unique=True),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = parse_redis_settings()
    max_jobs = 15
    job_timeout = 600  # 10 min default; migration has a function-specific 24-hour ceiling
