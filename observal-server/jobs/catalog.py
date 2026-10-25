# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Insights background jobs."""

from loguru import logger as optic


async def generate_insight_report(ctx: dict, report_id: str):
    """Background job: generate an insight report for an agent."""
    optic.info("insight_report_started: report_id={}", report_id)
    try:
        from services.insights import run_single_report

        await run_single_report(report_id)
    except Exception as e:
        optic.error("insight_report_job_failed: report_id={}, error={}", report_id, str(e))


async def batch_generate_insights(ctx: dict):
    """Cron job: discover agents needing reports and queue generation."""
    optic.debug("batch_generate_insights")
    try:
        from services.insights import discover_and_queue_reports

        queued = await discover_and_queue_reports()
        if queued > 0:
            optic.info("insight_batch_queued_reports: count={}", queued)
    except Exception as e:
        optic.error("insight_batch_failed: error={}", str(e))


async def refresh_user_profiles(ctx: dict):
    """Cron job: rebuild work profiles for users with recent session activity.

    Best-effort warm-up only. Correctness never depends on this job: a stale
    or missing profile is rebuilt lazily on first request, and a user skipped
    here keeps an old ``computed_at`` so that lazy path still fires. It exists
    so the registry home page rarely pays for a ClickHouse scan.

    Cost scales with *active* users, not registered ones. One query collects
    everyone with a session in the window, so an idle account costs nothing
    instead of a round trip to discover it has no sessions. If that lookup
    fails the sweep falls back to trying every user — degraded, not silently
    disabled.
    """
    optic.debug("refresh_user_profiles")
    try:
        from sqlalchemy import select

        from database import async_session
        from models.user import User
        from observal_shared.migration.constants import DEFAULT_PROJECT_ID
        from services.user_profile import get_or_build_profile, users_with_recent_activity

        active = await users_with_recent_activity()
        if active is None:
            optic.warning("user_profile_activity_lookup_unavailable_sweeping_all")

        refreshed = 0
        skipped = 0
        async with async_session() as db:
            users = (await db.execute(select(User).where(User.auth_provider != "deactivated"))).scalars().all()
            for user in users:
                if active is not None and (DEFAULT_PROJECT_ID, str(user.id)) not in active:
                    skipped += 1
                    continue
                try:
                    await get_or_build_profile(db, user.id, DEFAULT_PROJECT_ID, force=True)
                    refreshed += 1
                except Exception as e:
                    # One bad user must not stop the sweep. The session is
                    # shared across the loop, so a database error leaves it in
                    # an aborted transaction: without this rollback every
                    # later user fails too, and one fault is misreported as N.
                    await db.rollback()
                    optic.warning("user_profile_refresh_failed: user_id={}, error={}", str(user.id), str(e))
        optic.info("user_profiles_refreshed: count={}, skipped_inactive={}", refreshed, skipped)
    except Exception as e:
        optic.error("user_profile_batch_failed: error={}", str(e))
