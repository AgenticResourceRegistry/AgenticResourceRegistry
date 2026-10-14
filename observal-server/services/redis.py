# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Redis client and pub/sub helpers for background jobs and subscriptions."""

import json
import time
from urllib.parse import urlparse

import redis.asyncio as aioredis
from arq import create_pool as arq_create_pool
from arq.connections import ArqRedis, RedisSettings
from loguru import logger as optic
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from config import settings

_pool: aioredis.ConnectionPool | None = None


def get_pool() -> aioredis.ConnectionPool:
    global _pool
    if _pool is None:
        optic.debug(
            "creating Redis connection pool (host={}, max_connections={})",
            urlparse(settings.REDIS_URL).hostname,
            settings.REDIS_MAX_CONNECTIONS,
        )
        _pool = aioredis.ConnectionPool.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            socket_connect_timeout=settings.REDIS_SOCKET_TIMEOUT,
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
        )
    return _pool


def get_redis() -> aioredis.Redis:
    return aioredis.Redis(connection_pool=get_pool())


async def publish(channel: str, data: dict):
    """Publish a message to a Redis pub/sub channel (for GraphQL subscriptions)."""
    import asyncio

    _t0 = time.perf_counter()
    r = get_redis()
    attempts = 0
    max_attempts = 3
    while attempts < max_attempts:
        try:
            await r.publish(channel, json.dumps(data))
            _elapsed = (time.perf_counter() - _t0) * 1000
            optic.trace("published to channel '{}' ({:.0f}ms)", channel, _elapsed)
            return
        except (ConnectionError, OSError, RedisConnectionError, RedisTimeoutError) as e:
            attempts += 1
            if attempts >= max_attempts:
                _elapsed = (time.perf_counter() - _t0) * 1000
                optic.warning(
                    "failed to publish to '{}' after {} attempts: {} - subscribers will miss this update",
                    channel,
                    max_attempts,
                    e,
                )
                return
            optic.trace("publish to '{}' failed, retrying ({}/{}): {}", channel, attempts, max_attempts, e)
            await asyncio.sleep(0.5 * attempts)


async def subscribe(channel: str):
    """Subscribe to a Redis pub/sub channel. Yields parsed messages. Auto-reconnects."""
    import asyncio

    optic.debug("subscribing to Redis channel '{}'", channel)
    max_reconnects = 5
    reconnect_count = 0
    while reconnect_count < max_reconnects:
        pubsub_pool = aioredis.ConnectionPool.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=2,
            socket_connect_timeout=settings.REDIS_SOCKET_TIMEOUT,
        )
        r = aioredis.Redis(connection_pool=pubsub_pool)
        pubsub = r.pubsub()
        try:
            await pubsub.subscribe(channel)
            optic.trace("subscribed to '{}', listening for messages", channel)
            async for message in pubsub.listen():
                reconnect_count = 0
                if message["type"] == "message":
                    try:
                        yield json.loads(message["data"])
                    except (json.JSONDecodeError, TypeError):
                        optic.trace("malformed message on '{}', skipping", channel)
                        continue
        except (ConnectionError, OSError, TimeoutError, RedisConnectionError, RedisTimeoutError) as e:
            reconnect_count += 1
            optic.warning(
                "lost connection to '{}', reconnecting ({}/{}) - {}",
                channel,
                reconnect_count,
                max_reconnects,
                e,
            )
            await asyncio.sleep(1.0 * reconnect_count)
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.close()
            except Exception:
                pass
            try:
                await pubsub_pool.disconnect()
            except Exception:
                pass

    optic.error(
        "gave up reconnecting to '{}' after {} attempts - "
        "real-time updates on this channel are dead until server restart",
        channel,
        max_reconnects,
    )


def parse_redis_settings() -> RedisSettings:
    """Parse REDIS_URL into arq RedisSettings."""
    parsed = urlparse(settings.REDIS_URL)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        password=parsed.password,
        database=int(parsed.path.lstrip("/") or 0),
    )


_arq_pool: ArqRedis | None = None


async def _get_arq_pool() -> ArqRedis:
    global _arq_pool
    if _arq_pool is None:
        optic.debug("creating arq Redis pool for background jobs")
        _arq_pool = await arq_create_pool(parse_redis_settings())
    return _arq_pool


async def ping() -> bool:
    """Check Redis connectivity. Returns True if healthy."""
    _t0 = time.perf_counter()
    try:
        r = get_redis()
        result = await r.ping()
        _elapsed = (time.perf_counter() - _t0) * 1000
        optic.trace("Redis ping: ok ({:.0f}ms)", _elapsed)
        return result
    except Exception as e:
        _elapsed = (time.perf_counter() - _t0) * 1000
        optic.error("Redis unreachable ({:.0f}ms): {}", _elapsed, e)
        return False


async def close():
    optic.debug("shutting down Redis connections")
    global _pool, _arq_pool
    if _arq_pool:
        await _arq_pool.close()
        _arq_pool = None
        optic.trace("arq pool closed")
    if _pool:
        await _pool.disconnect()
        _pool = None
        optic.trace("connection pool disconnected")
    optic.debug("Redis shutdown complete")
