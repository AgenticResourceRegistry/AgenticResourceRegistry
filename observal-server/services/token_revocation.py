# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""User-wide token revocation.

``revoked_user:{id}`` holds the epoch microsecond before which every token issued
to the user is dead. Tokens issued afterwards are unaffected, so a new login
never has to clear the key, and nothing can bring an older token back.

The key never expires. It must outlive every token it revokes, whatever token
lifetimes are configured, and Redis never evicts keys without a TTL under
``volatile-lru``. It costs one small key per user who has ever been revoked.
"""

import time

# Before timestamps, the key held the literal "1". Real markers are epoch microseconds.
_LEGACY_MARKER_MAX = 1_000_000_000

# Only ever move the marker forward, so a replica with a slower clock cannot revive tokens.
_RAISE_MARKER = """
local current = tonumber(redis.call('GET', KEYS[1])) or 0
if tonumber(ARGV[1]) > current then redis.call('SET', KEYS[1], ARGV[1]) end
"""


def _key(user_id) -> str:
    return f"revoked_user:{user_id}"


async def revoke_user_tokens(redis, user_id) -> None:
    """Invalidate every token issued to the user up to now."""
    await redis.eval(_RAISE_MARKER, 1, _key(user_id), str(time.time_ns() // 1_000))


async def get_revocation_marker(redis, user_id) -> str | None:
    return await redis.get(_key(user_id))


def token_issued_us(payload: dict) -> int | None:
    """When a token was issued, in epoch µs: ``iat_us`` if present, else the 1s ``iat`` of older tokens.

    Microseconds keep "issued before the revocation" and "issued after it" apart: the work
    between a revocation and the next issuance (or the reverse) takes far longer than 1µs.
    """
    if payload.get("iat_us") is not None:
        return int(payload["iat_us"])
    iat = payload.get("iat")
    return None if iat is None else int(iat) * 1_000_000


def is_revoked(marker: str | None, issued_us: int | None) -> bool:
    """Whether a token issued at ``issued_us`` (see ``token_issued_us``) is dead under ``marker``."""
    if marker is None:
        return False
    try:
        revoked_at = int(marker)
    except (TypeError, ValueError):
        return True
    if revoked_at < _LEGACY_MARKER_MAX:
        return True  # legacy "1": block everything until the next login upgrades it
    return issued_us is None or issued_us < revoked_at


async def upgrade_legacy_revocation(redis, user_id) -> None:
    """On login, turn a legacy "1" marker into a timestamp so the new tokens work and old ones stay dead."""
    marker = await get_revocation_marker(redis, user_id)
    if marker is not None and is_revoked(marker, 2**62):
        await revoke_user_tokens(redis, user_id)
