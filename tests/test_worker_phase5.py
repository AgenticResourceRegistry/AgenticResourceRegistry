# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for Redis service and arq worker: Phase 5."""

import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

import pytest
import redis.asyncio as aioredis
import yaml
from arq.connections import RedisSettings
from redis.exceptions import AuthenticationError

import services.redis as redis_module
from services.redis import close, get_redis, publish

# --- Redis client ---


class TestGetRedis:
    def test_builds_clients_on_a_cached_pool(self):
        """get_redis must create one pool from REDIS_URL and share it across clients."""
        from config import settings

        sentinel_pool = MagicMock()
        with (
            patch("services.redis._pool", None),
            patch("services.redis.aioredis.ConnectionPool.from_url", return_value=sentinel_pool) as mock_from_url,
        ):
            first = get_redis()
            second = get_redis()

        mock_from_url.assert_called_once_with(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            socket_connect_timeout=settings.REDIS_SOCKET_TIMEOUT,
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
        )
        assert isinstance(first, aioredis.Redis)
        # Both clients must ride the same cached pool: a second from_url call
        # would mean get_pool() never caches and every call builds a new pool.
        assert first.connection_pool is sentinel_pool
        assert second.connection_pool is sentinel_pool


# --- Publish ---


class TestPublish:
    @pytest.mark.asyncio
    async def test_publishes_json(self):
        mock_redis = AsyncMock()
        with patch("services.redis.get_redis", return_value=mock_redis):
            await publish("test:channel", {"key": "value"})
            mock_redis.publish.assert_awaited_once_with("test:channel", json.dumps({"key": "value"}))

    @pytest.mark.asyncio
    async def test_retries_then_gives_up_without_raising(self):
        """Transient failures are retried up to max_attempts, then swallowed."""
        mock_redis = AsyncMock()
        mock_redis.publish.side_effect = ConnectionError("connection refused")
        with (
            patch("services.redis.get_redis", return_value=mock_redis),
            patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
        ):
            await publish("ch", {})  # should not raise
        assert mock_redis.publish.await_count == 3
        # Backoff happens between attempts only, never after the final failure.
        assert mock_sleep.await_count == 2

    @pytest.mark.asyncio
    async def test_retries_until_publish_succeeds(self):
        mock_redis = AsyncMock()
        mock_redis.publish.side_effect = [ConnectionError("flaky"), None]
        with (
            patch("services.redis.get_redis", return_value=mock_redis),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            await publish("ch", {"k": "v"})
        assert mock_redis.publish.await_count == 2

    @pytest.mark.asyncio
    async def test_auth_errors_propagate_without_retry(self):
        """Authentication/authorization failures must surface, not be retried away."""
        mock_redis = AsyncMock()
        mock_redis.publish.side_effect = AuthenticationError("bad credentials")
        with (
            patch("services.redis.get_redis", return_value=mock_redis),
            patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
            pytest.raises(AuthenticationError),
        ):
            await publish("ch", {})
        assert mock_redis.publish.await_count == 1
        mock_sleep.assert_not_awaited()


# --- Close ---


class TestClose:
    @pytest.mark.asyncio
    async def test_disconnects_both_pools_and_clears_globals(self):
        mock_pool = AsyncMock()
        mock_arq_pool = AsyncMock()
        with (
            patch("services.redis._pool", mock_pool),
            patch("services.redis._arq_pool", mock_arq_pool),
        ):
            await close()
            mock_arq_pool.close.assert_awaited_once()
            mock_pool.disconnect.assert_awaited_once()
            # Globals must be reset so a later get_redis() builds a fresh pool
            # instead of reusing disconnected connections.
            assert redis_module._pool is None
            assert redis_module._arq_pool is None

    @pytest.mark.asyncio
    async def test_tolerates_never_initialized_pools(self):
        """close() before any connection was opened must not raise."""
        with (
            patch("services.redis._pool", None),
            patch("services.redis._arq_pool", None),
        ):
            await close()


# --- Worker ---

EXPECTED_FUNCTIONS = {
    "sync_component_sources",
    "sync_mcp_webhook",
    "evaluate_alerts",
    "maintain_clickhouse",
    "generate_insight_report",
    "batch_generate_insights",
    "run_retention_purge",
    "run_migration_job",
    "refresh_user_profiles",
    "purge_inbox_items",
    "submit_usage_ping",
    "reproject_discovery_entries",
    "backfill_layer_components",
    "project_component_activity",
    "backfill_component_activity",
    "replay_activity_revision",
    "run_review_checks",
}


def _registered_name(entry):
    """arq ``func()`` wraps coroutines in Function(name=...); bare coroutines have __name__."""
    return getattr(entry, "name", None) or entry.__name__


class TestWorkerSettings:
    def test_registers_every_expected_job(self):
        """The registered set must equal the jobs the worker is meant to run.

        ``len(functions) > 0`` passed even if every job were dropped; comparing
        names fails when a job is renamed, dropped, or registered twice.
        """
        from worker import WorkerSettings

        names = [_registered_name(f) for f in WorkerSettings.functions]
        assert set(names) == EXPECTED_FUNCTIONS
        assert len(names) == len(set(names)), f"duplicate job registrations: {names}"
        for entry in WorkerSettings.functions:
            coroutine = getattr(entry, "coroutine", entry)
            assert inspect.iscoroutinefunction(coroutine), f"{entry} is not an async job"

    def test_function_options_are_applied(self):
        """func() wrappers must carry the job-specific limits they were declared with."""
        from jobs.mcp_sync import MAX_TRIES as MCP_SYNC_MAX_TRIES
        from jobs.migration import MAX_MIGRATION_JOB_TIMEOUT_SECONDS
        from worker import WorkerSettings

        by_name = {_registered_name(f): f for f in WorkerSettings.functions}
        assert by_name["sync_mcp_webhook"].max_tries == MCP_SYNC_MAX_TRIES
        assert by_name["run_migration_job"].timeout_s == MAX_MIGRATION_JOB_TIMEOUT_SECONDS

    def test_redis_settings_come_from_redis_url(self):
        """redis_settings must be parsed from REDIS_URL, not hardcoded defaults."""
        from config import settings
        from worker import WorkerSettings

        parsed = urlparse(settings.REDIS_URL)
        rs = WorkerSettings.redis_settings
        assert isinstance(rs, RedisSettings)
        assert rs.host == (parsed.hostname or "localhost")
        assert rs.port == (parsed.port or 6379)
        assert rs.database == int(parsed.path.lstrip("/") or 0)
        assert rs.password == parsed.password

    def test_startup_shutdown_hooks_are_wired(self):
        import worker

        assert worker.WorkerSettings.on_startup is worker.startup
        assert worker.WorkerSettings.on_shutdown is worker.shutdown
        assert inspect.iscoroutinefunction(worker.WorkerSettings.on_startup)
        assert inspect.iscoroutinefunction(worker.WorkerSettings.on_shutdown)

    def test_job_timeout(self):
        from worker import WorkerSettings

        assert WorkerSettings.job_timeout == 600

    def test_max_jobs(self):
        from worker import WorkerSettings

        assert WorkerSettings.max_jobs == 15

    @pytest.mark.asyncio
    async def test_settings_build_a_runnable_arq_worker(self):
        """arq.Worker validates the whole registration surface the container relies on."""
        from arq.worker import Worker

        from worker import WorkerSettings

        w = Worker(
            functions=WorkerSettings.functions,
            cron_jobs=WorkerSettings.cron_jobs,
            redis_settings=WorkerSettings.redis_settings,
            on_startup=WorkerSettings.on_startup,
            on_shutdown=WorkerSettings.on_shutdown,
            max_jobs=WorkerSettings.max_jobs,
            job_timeout=WorkerSettings.job_timeout,
            handle_signals=False,  # the container's -c snippet owns signal handling; also unsupported on Windows
        )
        assert set(w.functions) == EXPECTED_FUNCTIONS | {job.name for job in WorkerSettings.cron_jobs}
        assert len(w.cron_jobs) == len(WorkerSettings.cron_jobs)
        assert w.max_jobs == WorkerSettings.max_jobs
        assert w.job_timeout_s == WorkerSettings.job_timeout
        assert w.on_startup is WorkerSettings.on_startup
        assert w.on_shutdown is WorkerSettings.on_shutdown
        assert w.redis_settings == WorkerSettings.redis_settings

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


@pytest.fixture(scope="module")
def compose():
    with open(COMPOSE_PATH) as f:
        return yaml.safe_load(f)


class TestDockerCompose:
    """The compose file must wire a stack that actually boots, not just name keys.

    These tests assert the wiring semantics — startup ordering conditions, the
    real WorkerSettings object in the worker command, health checks the
    ordering relies on, and declared volumes — so they fail when the stack
    would not come up, not merely when a key is renamed.
    """

    def test_worker_command_runs_the_real_worker_settings(self, compose):
        command = compose["services"]["observal-worker"]["command"]
        assert command[0] == "/app/.venv/bin/python"
        assert command[1] == "-c"
        snippet = command[2]
        compile(snippet, "<observal-worker command>", "exec")  # must at least parse
        assert "from arq import run_worker" in snippet
        assert "from worker import WorkerSettings" in snippet
        assert "run_worker(WorkerSettings)" in snippet
        # The object the snippet names must be importable and shaped like an
        # arq settings class, else the container exits the moment it starts.
        from arq import run_worker

        import worker

        assert callable(run_worker)
        assert worker.WorkerSettings.functions
        assert inspect.iscoroutinefunction(worker.WorkerSettings.on_startup)
        assert inspect.iscoroutinefunction(worker.WorkerSettings.on_shutdown)
        assert isinstance(worker.WorkerSettings.redis_settings, RedisSettings)

    def test_worker_waits_for_migrations_and_healthy_redis(self, compose):
        deps = compose["services"]["observal-worker"]["depends_on"]
        # observal-init runs the Alembic and ClickHouse migrations; a weaker
        # condition lets the worker start polling jobs on an unmigrated schema.
        assert deps["observal-init"]["condition"] == "service_completed_successfully"
        # arq cannot pull jobs until redis answers PING.
        assert deps["observal-redis"]["condition"] == "service_healthy"

    def test_worker_shares_api_build_and_artifact_volume(self, compose):
        worker_svc = compose["services"]["observal-worker"]
        api_svc = compose["services"]["observal-api"]
        # The jobs package lives in the api codebase, so worker and api must be
        # built from the same Dockerfile and tag.
        assert worker_svc["build"]["dockerfile"] == api_svc["build"]["dockerfile"] == "docker/Dockerfile.api"
        assert worker_svc["image"] == api_svc["image"]
        # run_migration_job writes artifacts the api serves; both must mount /data.
        assert "MIGRATION_ARTIFACT_ROOT=/data/migration_artifacts" in worker_svc["environment"]
        assert "apidata:/data" in worker_svc["volumes"]
        assert "apidata:/data" in api_svc["volumes"]

    def test_redis_service_is_pingable_and_persistent(self, compose):
        redis_svc = compose["services"]["observal-redis"]
        assert str(redis_svc["image"]).startswith("redis:")
        # service_healthy conditions on api/init/worker only resolve if a
        # healthcheck exists; redis-cli ping is what answers it.
        assert "redis-cli" in " ".join(str(t) for t in redis_svc["healthcheck"]["test"])
        assert "redisdata:/data" in redis_svc["volumes"]
        assert "redisdata" in compose["volumes"]

    def test_api_waits_for_migrations_and_healthy_redis(self, compose):
        deps = compose["services"]["observal-api"]["depends_on"]
        assert deps["observal-init"]["condition"] == "service_completed_successfully"
        assert deps["observal-redis"]["condition"] == "service_healthy"

    def test_init_waits_for_every_datastore(self, compose):
        deps = compose["services"]["observal-init"]["depends_on"]
        for datastore in ("observal-db", "observal-clickhouse", "observal-redis"):
            assert deps[datastore]["condition"] == "service_healthy", datastore

    def test_lb_only_serves_a_healthy_api(self, compose):
        lb = compose["services"]["observal-lb"]
        assert lb["depends_on"]["observal-api"]["condition"] == "service_healthy"
        assert any(str(v).startswith("./nginx.conf") for v in lb["volumes"])

    def test_every_named_volume_mount_is_declared(self, compose):
        declared = set(compose.get("volumes") or {})
        for service_name, svc in compose["services"].items():
            for mount in svc.get("volumes") or []:
                source = str(mount).split(":", 1)[0]
                if source.startswith((".", "/", "~")):
                    continue  # bind mount, not a named volume
                assert source in declared, f"{service_name} mounts undeclared volume {source}"
