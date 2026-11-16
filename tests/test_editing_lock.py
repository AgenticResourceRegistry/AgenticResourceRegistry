# SPDX-FileCopyrightText: 2026 Tyler Riccardi <247895347+tylrcc@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services import editing_lock
from services.editing_lock import (
    LOCK_TTL_MINUTES,
    _is_lock_expired,
    acquire_edit_lock,
    is_actively_editing,
    release_edit_lock,
)

ALICE = uuid.UUID("11111111-1111-1111-1111-111111111111")
BOB = uuid.UUID("22222222-2222-2222-2222-222222222222")


class _FixedClock(datetime):
    """datetime whose now() is pinned to NOW (see the fixed_clock fixture)."""

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return NOW.replace(tzinfo=None)
        return NOW


# One fixed "now" every time-dependent test derives its timestamps from, so
# the suite does not depend on the wall clock.
NOW = _FixedClock(2026, 11, 12, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    """Pin the clock the service itself reads to NOW for every test."""
    monkeypatch.setattr(editing_lock, "datetime", _FixedClock)
    return NOW


def make_version(*, is_editing=False, editing_since=None, editing_by=None):
    """A stand-in for a version row: editing_lock only reads these three fields."""
    return SimpleNamespace(
        is_editing=is_editing,
        editing_since=editing_since,
        editing_by=editing_by,
    )


def fresh_lock(by=ALICE):
    return make_version(
        is_editing=True,
        editing_since=NOW,
        editing_by=by,
    )


def stale_lock(by=ALICE):
    return make_version(
        is_editing=True,
        editing_since=NOW - timedelta(minutes=LOCK_TTL_MINUTES + 1),
        editing_by=by,
    )


class TestIsLockExpired:
    def test_none_counts_as_expired(self):
        assert _is_lock_expired(None) is True

    def test_recent_timestamp_is_not_expired(self):
        assert _is_lock_expired(NOW) is False

    def test_timestamp_older_than_the_ttl_is_expired(self):
        old = NOW - timedelta(minutes=LOCK_TTL_MINUTES + 1)
        assert _is_lock_expired(old) is True

    def test_timestamp_inside_the_ttl_is_not_expired(self):
        recent = NOW - timedelta(minutes=LOCK_TTL_MINUTES - 1)
        assert _is_lock_expired(recent) is False

    def test_naive_timestamp_is_treated_as_utc(self):
        naive_recent = NOW.replace(tzinfo=None)
        naive_old = (NOW - timedelta(minutes=LOCK_TTL_MINUTES + 1)).replace(tzinfo=None)
        assert _is_lock_expired(naive_recent) is False
        assert _is_lock_expired(naive_old) is True


class TestAcquireEditLock:
    def test_free_version_gets_locked(self):
        version = make_version()
        acquire_edit_lock(version, ALICE)
        assert version.is_editing is True
        assert version.editing_by == ALICE
        assert isinstance(version.editing_since, datetime)
        assert _is_lock_expired(version.editing_since) is False

    def test_another_users_unexpired_lock_conflicts(self):
        version = fresh_lock(by=ALICE)
        with pytest.raises(HTTPException) as excinfo:
            acquire_edit_lock(version, BOB)
        assert excinfo.value.status_code == 409
        # The failed attempt leaves the holder's lock untouched.
        assert version.editing_by == ALICE

    def test_same_user_can_reacquire(self):
        version = fresh_lock(by=ALICE)
        before = version.editing_since
        acquire_edit_lock(version, ALICE)
        assert version.is_editing is True
        assert version.editing_by == ALICE
        assert version.editing_since >= before

    def test_another_user_can_take_over_after_expiry(self):
        version = stale_lock(by=ALICE)
        acquire_edit_lock(version, BOB)
        assert version.is_editing is True
        assert version.editing_by == BOB
        assert _is_lock_expired(version.editing_since) is False


class TestReleaseEditLock:
    def test_holder_release_clears_the_lock_fields(self):
        version = fresh_lock(by=ALICE)
        release_edit_lock(version, ALICE)
        assert version.is_editing is False
        assert version.editing_since is None
        assert version.editing_by is None

    def test_non_holder_cannot_release(self):
        version = fresh_lock(by=ALICE)
        with pytest.raises(HTTPException) as excinfo:
            release_edit_lock(version, BOB)
        assert excinfo.value.status_code == 403
        # The failed attempt leaves the lock in place.
        assert version.is_editing is True
        assert version.editing_by == ALICE

    def test_force_releases_regardless_of_holder(self):
        version = fresh_lock(by=ALICE)
        release_edit_lock(version, BOB, force=True)
        assert version.is_editing is False
        assert version.editing_since is None
        assert version.editing_by is None

    def test_releasing_an_unlocked_version_is_a_noop(self):
        version = make_version()
        release_edit_lock(version, ALICE)
        assert version.is_editing is False
        assert version.editing_since is None
        assert version.editing_by is None


class TestIsActivelyEditing:
    def test_not_editing(self):
        assert is_actively_editing(make_version()) is False

    def test_missing_editing_by(self):
        version = make_version(is_editing=True, editing_since=NOW)
        assert is_actively_editing(version) is False

    def test_editing_since_that_is_not_a_datetime(self):
        version = make_version(is_editing=True, editing_since="just now", editing_by=ALICE)
        assert is_actively_editing(version) is False

    def test_expired_lock(self):
        assert is_actively_editing(stale_lock()) is False

    def test_active_lock(self):
        assert is_actively_editing(fresh_lock()) is True
