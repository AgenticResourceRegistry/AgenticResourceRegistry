# SPDX-FileCopyrightText: 2026 SK Akram <skakram00zz@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import patch

import pytest

from observal_shared.migration.connections import parse_clickhouse_url
from services.redis import parse_redis_settings


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("clickhouse://u:p@ch:8123/obs", ("http://ch:8123", "obs", "u", "p")),
        ("clickhouse://ch/obs", ("http://ch:8123", "obs", "default", "")),
        ("clickhouses://u:p@ch.cloud/obs", ("https://ch.cloud:8443", "obs", "u", "p")),
        ("clickhouses://u:p@ch.cloud:9440/obs", ("https://ch.cloud:9440", "obs", "u", "p")),
        ("https://u:p@ch.cloud/obs", ("https://ch.cloud:8443", "obs", "u", "p")),
        ("http://u:p@ch/obs", ("http://ch:8123", "obs", "u", "p")),
        ("clickhouse://us%40er:p%40ss%2Fw%3Aord@ch/obs", ("http://ch:8123", "obs", "us@er", "p@ss/w:ord")),
    ],
)
def test_parse_clickhouse_url(url, expected):
    assert parse_clickhouse_url(url) == expected


def _redis_settings(url):
    with patch("services.redis.settings") as settings:
        settings.REDIS_URL = url
        return parse_redis_settings()


def test_redis_plain_url():
    s = _redis_settings("redis://cache:6380/2")
    assert (s.host, s.port, s.database, s.ssl) == ("cache", 6380, 2, False)
    assert s.username is None
    assert s.password is None


def test_redis_tls_url_with_credentials():
    s = _redis_settings("rediss://acl%20user:p%40ss%3Aword@cache.aws:6379/0")
    assert s.ssl is True
    assert s.username == "acl user"
    assert s.password == "p@ss:word"


def test_redis_password_only_url():
    s = _redis_settings("redis://:secret@cache:6379")
    assert s.username is None
    assert s.password == "secret"
