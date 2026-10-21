# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Redis-backed response cache for dashboard and OTEL endpoints."""

import hashlib
import time

from loguru import logger as optic
from redis import asyncio as aioredis
from starlette.requests import Request

from config import settings
from services.auth_header import bearer_token

CACHE_PREFIX = "observal-cache"

_redis: aioredis.Redis | None = None


def _request_key_builder(func, namespace="", *, request: Request | None = None, **kwargs):
    """Build cache key from auth identity + path + query string.

    Per-user identity prevents cross-user cache poisoning (SEC-023).
    """
    prefix = f"{CACHE_PREFIX}:{namespace}" if namespace else CACHE_PREFIX
    url = request.url.path if request else func.__name__
    qs = str(request.query_params) if request and request.query_params else ""

    identity = "anon"
    if request:
        auth = request.headers.get("authorization", "")
        if bearer_token(auth) is not None:
            identity = hashlib.sha256(auth.encode(), usedforsecurity=False).hexdigest()[:16]

    raw = f"{identity}:{url}?{qs}" if qs else f"{identity}:{url}"
    return f"{prefix}:{hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()}"


async def init_cache() -> None:
    """Initialize FastAPICache with a Redis backend."""
    _t0 = time.perf_counter()
    global _redis
    from fastapi_cache import FastAPICache
    from fastapi_cache.backends.redis import RedisBackend

    _redis = aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=False,
        socket_connect_timeout=settings.REDIS_SOCKET_TIMEOUT,
        socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
    )
    FastAPICache.init(RedisBackend(_redis), prefix=CACHE_PREFIX, key_builder=_request_key_builder)
    _elapsed = (time.perf_counter() - _t0) * 1000
    optic.info("response cache initialized (Redis, prefix='{}', {:.0f}ms)", CACHE_PREFIX, _elapsed)


async def close_cache() -> None:
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None
        optic.debug("response cache connection closed")


async def invalidate_all() -> int:
    """Delete every key under the cache prefix. Returns count deleted."""
    _t0 = time.perf_counter()
    if not _redis:
        return 0
    cursor, keys = 0, []
    pattern = f"{CACHE_PREFIX}:*"
    while True:
        cursor, batch = await _redis.scan(cursor=cursor, match=pattern, count=500)
        keys.extend(batch)
        if cursor == 0:
            break
    if keys:
        await _redis.delete(*keys)
    _elapsed = (time.perf_counter() - _t0) * 1000
    optic.debug("cache invalidated: {} keys deleted ({:.0f}ms)", len(keys), _elapsed)
    return len(keys)


async def invalidate_namespace(namespace: str) -> int:
    """Delete keys matching a specific namespace."""
    if not _redis:
        return 0
    pattern = f"{CACHE_PREFIX}:{namespace}:*"
    cursor, keys = 0, []
    while True:
        cursor, batch = await _redis.scan(cursor=cursor, match=pattern, count=500)
        keys.extend(batch)
        if cursor == 0:
            break
    if keys:
        await _redis.delete(*keys)
    optic.trace("invalidated {} keys in namespace '{}'", len(keys), namespace)
    return len(keys)
