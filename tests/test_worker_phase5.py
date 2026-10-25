# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for Redis service and arq worker: Phase 5."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.redis import close, get_redis, publish

# --- Redis client ---


class TestGetRedis:
    def test_returns_redis_instance(self):
        with patch("services.redis.aioredis.ConnectionPool.from_url") as mock_pool:
            mock_pool.return_value = MagicMock()
            r = get_redis()
            assert r is not None


# --- Publish ---


class TestPublish:
    @pytest.mark.asyncio
    async def test_publishes_json(self):
        mock_redis = AsyncMock()
        with patch("services.redis.get_redis", return_value=mock_redis):
            await publish("test:channel", {"key": "value"})
            mock_redis.publish.assert_called_once_with("test:channel", json.dumps({"key": "value"}))

    @pytest.mark.asyncio
    async def test_silent_on_error(self):
        mock_redis = AsyncMock()
        mock_redis.publish.side_effect = ConnectionError("connection refused")
        with patch("services.redis.get_redis", return_value=mock_redis):
            await publish("ch", {})  # should not raise


# --- Close ---


class TestClose:
    @pytest.mark.asyncio
    async def test_disconnects_pool(self):
        mock_pool = AsyncMock()
        with patch("services.redis._pool", mock_pool):
            await close()


# --- Worker ---


class TestWorkerSettings:
    def test_has_functions(self):
        from worker import WorkerSettings

        assert len(WorkerSettings.functions) > 0

    def test_has_redis_settings(self):
        from worker import WorkerSettings

        assert WorkerSettings.redis_settings is not None

    def test_job_timeout(self):
        from worker import WorkerSettings

        assert WorkerSettings.job_timeout == 600

    def test_max_jobs(self):
        from worker import WorkerSettings

        assert WorkerSettings.max_jobs == 15

    def test_cron_jobs_run_the_intended_number_of_times_per_week(self):
        from datetime import UTC, datetime, timedelta

        from arq.cron import next_cron

        from worker import WorkerSettings

        # arq treats an omitted minute= as every minute, so hour= alone fires 60 times per hour.
        expected_runs_per_week = {
            "cron:sync_component_sources": 4 * 7,
            "cron:evaluate_alerts": 60 * 24 * 7,
            "cron:run_review_checks": 4 * 60 * 24 * 7,
            "cron:maintain_clickhouse": 6 * 7,
            "cron:batch_generate_insights": 1,
            "cron:run_retention_purge": 4 * 7,
            "cron:purge_migration_artifacts": 4 * 7,
            "cron:refresh_user_profiles": 7,
            "cron:purge_inbox_items": 7,
            "cron:submit_usage_ping": 4 * 7,
            "cron:reproject_discovery_entries": 4 * 7,
            "cron:backfill_layer_components": 7,
            "cron:backfill_component_activity": 7,
            "cron:replay_activity_revision": 12 * 24 * 7,
        }

        week_start = datetime(2026, 1, 5, tzinfo=UTC)  # a Monday
        week_end = week_start + timedelta(days=7)
        job_names = [job.name for job in WorkerSettings.cron_jobs]
        assert len(job_names) == len(set(job_names)), f"duplicate cron jobs registered: {job_names}"

        actual_runs_per_week = {}
        for job in WorkerSettings.cron_jobs:
            # next_cron rather than job.calculate_next, which mutates the shared CronJob.
            schedule = {
                field: getattr(job, field)
                for field in ("month", "day", "weekday", "hour", "minute", "second", "microsecond")
            }
            runs = 0
            run_at = next_cron(week_start - timedelta(seconds=1), **schedule)
            while run_at < week_end:
                runs += 1
                run_at = next_cron(run_at, **schedule)
            actual_runs_per_week[job.name] = runs

        assert actual_runs_per_week == expected_runs_per_week


# --- Docker compose ---

COMPOSE_PATH = str(Path(__file__).resolve().parent.parent / "docker" / "docker-compose.yml")


class TestDockerCompose:
    def test_redis_service_exists(self):
        import yaml

        with open(COMPOSE_PATH) as f:
            compose = yaml.safe_load(f)
        assert "observal-redis" in compose["services"]
        assert compose["services"]["observal-redis"]["image"] == "redis:8-alpine"

    def test_worker_service_exists(self):
        import yaml

        with open(COMPOSE_PATH) as f:
            compose = yaml.safe_load(f)
        assert "observal-worker" in compose["services"]
        assert "worker" in str(compose["services"]["observal-worker"]["command"])

    def test_redis_volume_exists(self):
        import yaml

        with open(COMPOSE_PATH) as f:
            compose = yaml.safe_load(f)
        assert "redisdata" in compose["volumes"]

    def test_api_depends_on_init(self):
        import yaml

        with open(COMPOSE_PATH) as f:
            compose = yaml.safe_load(f)
        deps = compose["services"]["observal-api"]["depends_on"]
        assert "observal-init" in deps

    def test_init_service_exists(self):
        import yaml

        with open(COMPOSE_PATH) as f:
            compose = yaml.safe_load(f)
        assert "observal-init" in compose["services"]

    def test_lb_service_exists(self):
        import yaml

        with open(COMPOSE_PATH) as f:
            compose = yaml.safe_load(f)
        assert "observal-lb" in compose["services"]
