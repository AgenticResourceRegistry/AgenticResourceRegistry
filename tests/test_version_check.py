# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deterministic behavioral tests for ``observal_cli.version_check``."""

from __future__ import annotations

import hashlib
import hmac
import importlib.metadata
import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import httpx
import pytest
import rich
import typer

from observal_cli import version_check

FIXED_NOW = datetime(2026, 7, 8, 9, 10, 11, tzinfo=UTC)
FIXED_ISO = FIXED_NOW.isoformat()
LONG_OPTION = "-" * 2
VERSION_OPTION = f"{LONG_OPTION}version"
_MISSING = object()
_REAL_MACHINE_KEY = version_check._machine_key


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return FIXED_NOW.replace(tzinfo=None)
        return FIXED_NOW.astimezone(tz)


def blocked(name: str):
    def fail(*args: object, **kwargs: object):
        raise AssertionError(f"unmocked boundary: {name}, args={args}, kwargs={kwargs}")

    return fail


def response(
    status_code: int = 200,
    *,
    data: object = _MISSING,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    kwargs: dict[str, object] = {"headers": headers or {}}
    if data is not _MISSING:
        kwargs["json"] = data
    elif content is not None:
        kwargs["content"] = content
    return httpx.Response(status_code, **kwargs)


@pytest.fixture(autouse=True)
def isolated_boundaries(tmp_path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    config_dir = tmp_path / "config"
    cache_file = config_dir / "version_cache.json"
    config = {
        "update_check": True,
        "update_check_interval": version_check.CHECK_INTERVAL_DEFAULT,
        "update_check_repo": "",
        "server_url": "",
        "access_token": "",
    }
    load_config = MagicMock(side_effect=lambda: dict(config))
    http_get = MagicMock(side_effect=blocked("httpx.get"))
    http_head = MagicMock(side_effect=blocked("httpx.head"))

    monkeypatch.setattr(version_check, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(version_check, "CACHE_FILE", cache_file)
    monkeypatch.setattr(version_check, "load_config", load_config)
    monkeypatch.setattr(version_check, "datetime", FixedDateTime)
    monkeypatch.setattr(version_check.time, "time", lambda: FIXED_NOW.timestamp())
    monkeypatch.setattr(version_check, "_machine_key", lambda: b"test-machine")
    monkeypatch.setattr(version_check.httpx, "get", http_get)
    monkeypatch.setattr(version_check.httpx, "head", http_head)
    monkeypatch.delenv("OBSERVAL_NO_UPDATE_CHECK", raising=False)

    return SimpleNamespace(
        config=config,
        config_dir=config_dir,
        cache_file=cache_file,
        load_config=load_config,
        http_get=http_get,
        http_head=http_head,
    )


@pytest.fixture
def current_version(monkeypatch: pytest.MonkeyPatch) -> str:
    value = "1.2.3"
    monkeypatch.setattr(version_check, "get_current_version", lambda: value)
    return value


@pytest.fixture
def messages(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    output: list[str] = []
    monkeypatch.setattr(rich, "print", output.append)
    return output


def test_update_available_defaults_to_upgrade_and_is_frozen() -> None:
    update = version_check.UpdateAvailable("1.0.0", "2.0.0", "release", "published", "github")

    assert update == version_check.UpdateAvailable(
        current="1.0.0",
        latest="2.0.0",
        release_url="release",
        published_at="published",
        source="github",
        direction="upgrade",
    )
    with pytest.raises(AttributeError):
        update.latest = "3.0.0"  # type: ignore[misc]


def test_get_current_version_reads_installed_distribution(monkeypatch: pytest.MonkeyPatch) -> None:
    metadata_version = MagicMock(return_value="2.4.1rc1")
    monkeypatch.setattr(importlib.metadata, "version", metadata_version)

    assert version_check.get_current_version() == "2.4.1rc1"
    metadata_version.assert_called_once_with("observal-cli")


def test_get_current_version_uses_development_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = importlib.metadata.PackageNotFoundError("observal-cli")
    metadata_version = MagicMock(side_effect=failure)
    monkeypatch.setattr(importlib.metadata, "version", metadata_version)

    assert version_check.get_current_version() == "0.0.0"
    metadata_version.assert_called_once_with("observal-cli")


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("1.0.0", True),
        ("v1.0.0", True),
        ("2.0.0.dev1", True),
        ("1.0.0rc1", False),
        ("0.9.9", False),
        ("invalid", False),
    ],
)
def test_version_floor_uses_pep440_versions(target: str, expected: bool) -> None:
    assert version_check.check_version_floor(target) is expected


@pytest.mark.parametrize(
    ("latest", "current", "expected"),
    [
        ("1.10.0", "1.9.9", True),
        ("2.0.0rc1", "1.9.9", True),
        ("2.0.0rc1", "2.0.0", False),
        ("1.0.0", "1.0.0", False),
        ("0.9.0", "1.0.0", False),
        ("invalid", "1.0.0", False),
        ("1.0.0", "invalid", False),
    ],
)
def test_is_newer_compares_valid_versions_and_rejects_invalid_ones(latest: str, current: str, expected: bool) -> None:
    assert version_check._is_newer(latest, current) is expected


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("", version_check.GITHUB_REPO_DEFAULT), ("example/releases", "example/releases")],
)
def test_github_repo_uses_configured_value_or_default(
    configured: str,
    expected: str,
    isolated_boundaries: SimpleNamespace,
) -> None:
    isolated_boundaries.config["update_check_repo"] = configured

    assert version_check._github_repo() == expected
    isolated_boundaries.load_config.assert_called_once_with()


class MachinePath:
    def __init__(self, exists: bool, value: bytes = b"", failure: OSError | None = None) -> None:
        self._exists = exists
        self._value = value
        self._failure = failure

    def exists(self) -> bool:
        return self._exists

    def read_bytes(self) -> bytes:
        if self._failure:
            raise self._failure
        return self._value


def test_machine_key_tries_both_linux_identifiers_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    paths = {
        "/etc/machine-id": MachinePath(True, failure=PermissionError("denied")),
        "/var/lib/dbus/machine-id": MachinePath(True, value=b" dbus-machine\n"),
    }
    requested: list[str] = []
    run = MagicMock(side_effect=blocked("subprocess.run"))
    monkeypatch.setattr(version_check, "_machine_key", _REAL_MACHINE_KEY)
    monkeypatch.setattr(version_check, "Path", lambda value: requested.append(value) or paths[value])
    monkeypatch.setattr(version_check.subprocess, "run", run)

    assert version_check._machine_key() == b"dbus-machine"
    assert requested == ["/etc/machine-id", "/var/lib/dbus/machine-id"]
    run.assert_not_called()


def test_machine_key_reads_macos_platform_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    run = MagicMock(return_value=SimpleNamespace(stdout='    "IOPlatformUUID" = "ABC-123"\n'))
    monkeypatch.setattr(version_check, "_machine_key", _REAL_MACHINE_KEY)
    monkeypatch.setattr(version_check, "Path", lambda value: MachinePath(False))
    monkeypatch.setattr(version_check.subprocess, "run", run)

    assert version_check._machine_key() == b"ABC-123"
    run.assert_called_once_with(
        ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
        capture_output=True,
        text=True,
        timeout=3,
    )


@pytest.mark.parametrize(
    "result",
    [
        SimpleNamespace(stdout="no platform identifier"),
        SimpleNamespace(stdout='"IOPlatformUUID" = malformed'),
        FileNotFoundError("ioreg missing"),
    ],
)
def test_machine_key_falls_back_to_hostname(
    result: SimpleNamespace | Exception,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MagicMock(side_effect=result if isinstance(result, Exception) else None, return_value=result)
    hostname = MagicMock(return_value="host.example")
    monkeypatch.setattr(version_check, "_machine_key", _REAL_MACHINE_KEY)
    monkeypatch.setattr(version_check, "Path", lambda value: MachinePath(False))
    monkeypatch.setattr(version_check.subprocess, "run", run)
    monkeypatch.setattr(version_check.socket, "gethostname", hostname)

    assert version_check._machine_key() == b"host.example"
    hostname.assert_called_once_with()


def test_cache_hmac_uses_machine_key_and_truncated_sha256(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b'{"latest":"2.0.0"}'
    monkeypatch.setattr(version_check, "_machine_key", lambda: b"machine-secret")

    assert version_check._cache_hmac(payload) == hmac.new(b"machine-secret", payload, hashlib.sha256).hexdigest()[:16]


@pytest.mark.parametrize("contents", ["", "   \n", "[]", "null", "not json"])
def test_read_cache_rejects_empty_non_mapping_and_invalid_json(
    contents: str,
    isolated_boundaries: SimpleNamespace,
) -> None:
    isolated_boundaries.cache_file.parent.mkdir(parents=True)
    isolated_boundaries.cache_file.write_text(contents)

    assert version_check._read_cache() is None


def test_read_cache_returns_none_when_file_is_missing(isolated_boundaries: SimpleNamespace) -> None:
    assert version_check._read_cache() is None


def test_read_cache_accepts_legacy_unsigned_mapping(isolated_boundaries: SimpleNamespace) -> None:
    data = {"last_checked": FIXED_ISO, "latest_version": "2.0.0"}
    isolated_boundaries.cache_file.parent.mkdir(parents=True)
    isolated_boundaries.cache_file.write_text(json.dumps(data))

    assert version_check._read_cache() == data


def test_read_cache_verifies_signature_without_returning_it(isolated_boundaries: SimpleNamespace) -> None:
    data = {"last_checked": FIXED_ISO, "latest_version": "2.0.0"}
    payload = json.dumps(data, sort_keys=True).encode()
    stored = {**data, "_hmac": version_check._cache_hmac(payload)}
    isolated_boundaries.cache_file.parent.mkdir(parents=True)
    isolated_boundaries.cache_file.write_text(json.dumps(stored))

    assert version_check._read_cache() == data

    stored["latest_version"] = "99.0.0"
    isolated_boundaries.cache_file.write_text(json.dumps(stored))
    assert version_check._read_cache() is None


@pytest.mark.parametrize(
    "failure",
    [OSError("read failed"), ValueError("bad data"), UnicodeDecodeError("utf-8", b"x", 0, 1, "bad")],
)
def test_read_cache_translates_file_failures_to_cache_misses(
    failure: Exception,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_file = MagicMock()
    cache_file.exists.return_value = True
    cache_file.read_text.side_effect = failure
    monkeypatch.setattr(version_check, "CACHE_FILE", cache_file)

    assert version_check._read_cache() is None


def test_write_cache_is_private_atomic_signed_and_does_not_mutate_input(
    isolated_boundaries: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = {"latest_version": "2.0.0", "last_checked": FIXED_ISO, "_hmac": "stale"}
    original = dict(data)
    real_umask = version_check.os.umask
    umask_calls: list[tuple[int, int]] = []

    def recording_umask(mask: int) -> int:
        previous = real_umask(mask)
        umask_calls.append((mask, previous))
        return previous

    monkeypatch.setattr(version_check.os, "umask", recording_umask)

    version_check._write_cache(data)

    clean = {"latest_version": "2.0.0", "last_checked": FIXED_ISO}
    payload = json.dumps(clean, sort_keys=True).encode()
    expected = {**clean, "_hmac": version_check._cache_hmac(payload)}
    assert isolated_boundaries.cache_file.read_text() == json.dumps(expected, indent=2)
    assert stat.S_IMODE(isolated_boundaries.cache_file.stat().st_mode) == 0o600
    assert not isolated_boundaries.cache_file.with_suffix(".tmp").exists()
    assert data == original
    assert umask_calls[0][0] == 0o077
    assert umask_calls[1][0] == umask_calls[0][1]


@pytest.mark.parametrize("failure_at", ["write", "replace"])
def test_write_cache_restores_umask_when_file_operation_fails(
    failure_at: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_file = MagicMock()
    temporary = MagicMock()
    cache_file.with_suffix.return_value = temporary
    failure = OSError(f"{failure_at} failed")
    if failure_at == "write":
        temporary.write_text.side_effect = failure
    else:
        temporary.replace.side_effect = failure
    config_dir = MagicMock()
    umask = MagicMock(return_value=0o027)
    monkeypatch.setattr(version_check, "CACHE_FILE", cache_file)
    monkeypatch.setattr(version_check, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(version_check.os, "umask", umask)

    with pytest.raises(OSError) as error:
        version_check._write_cache({"latest_version": "2.0.0"})

    assert error.value is failure
    config_dir.mkdir.assert_called_once_with(parents=True, exist_ok=True)
    cache_file.with_suffix.assert_called_once_with(".tmp")
    umask.assert_has_calls([call(0o077), call(0o027)])
    if failure_at == "write":
        temporary.replace.assert_not_called()
    else:
        temporary.replace.assert_called_once_with(cache_file)


def timestamp(seconds_from_now: float) -> str:
    return (FIXED_NOW + timedelta(seconds=seconds_from_now)).isoformat()


@pytest.mark.parametrize(
    ("cache", "interval", "expected"),
    [
        (None, 60, True),
        ({}, 60, True),
        ({"last_checked": None}, 60, True),
        ({"last_checked": "not a timestamp"}, 60, True),
        ({"last_checked": timestamp(1)}, 60, True),
        ({"last_checked": timestamp(-59.999)}, 60, False),
        ({"last_checked": timestamp(-60)}, 60, True),
    ],
)
def test_should_check_uses_exact_interval_and_rejects_clock_skew(
    cache: dict | None,
    interval: int,
    expected: bool,
) -> None:
    assert version_check._should_check(cache, interval) is expected


def test_fetch_from_server_returns_canonical_version_with_exact_request(
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get = MagicMock(return_value=response(data={"server_version": "v2.4.1rc1"}))
    monkeypatch.setattr(version_check.httpx, "get", get)

    assert version_check._fetch_from_server("https://registry.example.test", "secret-token") == {
        "latest_version": "v2.4.1rc1",
        "release_url": "",
        "published_at": "",
        "source": "server",
        "server_version": "v2.4.1rc1",
    }
    get.assert_called_once_with(
        "https://registry.example.test/api/v1/config/version",
        timeout=version_check.CHECK_TIMEOUT,
        headers={
            "Authorization": "Bearer secret-token",
            "User-Agent": f"observal-cli/{current_version}",
        },
    )


@pytest.mark.parametrize("status_code", [401, 429, 503])
def test_fetch_from_server_rejects_http_status_without_retry(
    status_code: int,
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_response = MagicMock(status_code=status_code)
    server_response.json.side_effect = blocked("response.json")
    get = MagicMock(return_value=server_response)
    monkeypatch.setattr(version_check.httpx, "get", get)

    assert version_check._fetch_from_server("https://registry.example.test", "token") is None
    assert get.call_count == 1
    server_response.json.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [None, [], "mapping required", {}, {"server_version": ""}, {"server_version": "invalid version"}],
)
def test_fetch_from_server_rejects_invalid_payloads(
    payload: object,
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(version_check.httpx, "get", MagicMock(return_value=response(data=payload)))

    assert version_check._fetch_from_server("https://registry.example.test", "token") is None


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout("slow"),
        json.JSONDecodeError("invalid", "x", 0),
        KeyError("server_version"),
    ],
)
def test_fetch_from_server_swallows_transport_and_decode_failures(
    failure: Exception,
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if isinstance(failure, httpx.HTTPError):
        get = MagicMock(side_effect=failure)
    else:
        server_response = MagicMock(status_code=200)
        server_response.json.side_effect = failure
        get = MagicMock(return_value=server_response)
    monkeypatch.setattr(version_check.httpx, "get", get)

    assert version_check._fetch_from_server("https://registry.example.test", "token") is None


GITHUB_RELEASE = {
    "tag_name": "v2.4.1",
    "html_url": "https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/releases/tag/v2.4.1",
    "published_at": "2026-07-08T08:00:00Z",
    "prerelease": False,
}


def github_headers(current: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"observal-cli/{current}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def test_fetch_from_github_stable_release_uses_exact_endpoint_and_headers(
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get = MagicMock(return_value=response(data=GITHUB_RELEASE))
    monkeypatch.setattr(version_check.httpx, "get", get)

    assert version_check._fetch_from_github() == {
        "latest_version": "2.4.1",
        "release_url": GITHUB_RELEASE["html_url"],
        "published_at": GITHUB_RELEASE["published_at"],
        "prerelease": False,
        "source": "github",
    }
    get.assert_called_once_with(
        f"{version_check.GITHUB_API_BASE}/{version_check.GITHUB_REPO_DEFAULT}/releases/latest",
        timeout=version_check.CHECK_TIMEOUT,
        headers=github_headers(current_version),
        follow_redirects=False,
    )


def test_fetch_from_github_prerelease_uses_list_endpoint_and_first_release(
    isolated_boundaries: SimpleNamespace,
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_boundaries.config["update_check_repo"] = "example/project"
    prerelease = {
        **GITHUB_RELEASE,
        "tag_name": "v3.0.0-rc.1",
        "prerelease": True,
    }
    get = MagicMock(return_value=response(data=[prerelease, GITHUB_RELEASE]))
    monkeypatch.setattr(version_check.httpx, "get", get)

    result = version_check._fetch_from_github(include_pre=True)

    assert result == {
        "latest_version": "3.0.0-rc.1",
        "release_url": prerelease["html_url"],
        "published_at": prerelease["published_at"],
        "prerelease": True,
        "source": "github",
    }
    get.assert_called_once_with(
        f"{version_check.GITHUB_API_BASE}/example/project/releases?per_page=1",
        timeout=version_check.CHECK_TIMEOUT,
        headers=github_headers(current_version),
        follow_redirects=False,
    )


@pytest.mark.parametrize("status_code", [404, 429, 500])
def test_fetch_from_github_rejects_http_status_without_retry(
    status_code: int,
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_response = MagicMock(status_code=status_code)
    release_response.content = b""
    release_response.json.side_effect = blocked("response.json")
    get = MagicMock(return_value=release_response)
    monkeypatch.setattr(version_check.httpx, "get", get)

    assert version_check._fetch_from_github() is None
    assert get.call_count == 1
    release_response.json.assert_not_called()


def test_fetch_from_github_enforces_response_size_before_json_decode(
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_response = MagicMock(status_code=200)
    release_response.content = b"x" * (version_check.MAX_RESPONSE_SIZE + 1)
    release_response.json.side_effect = blocked("response.json")
    monkeypatch.setattr(version_check.httpx, "get", MagicMock(return_value=release_response))

    assert version_check._fetch_from_github() is None
    release_response.json.assert_not_called()


@pytest.mark.parametrize(
    ("include_pre", "payload"),
    [
        (False, None),
        (False, []),
        (False, "release mapping required"),
        (True, []),
        (True, [None]),
        (True, {"tag_name": "invalid version"}),
        (False, {"tag_name": "invalid version"}),
        (False, {}),
    ],
)
def test_fetch_from_github_rejects_invalid_release_payloads(
    include_pre: bool,
    payload: object,
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(version_check.httpx, "get", MagicMock(return_value=response(data=payload)))

    assert version_check._fetch_from_github(include_pre=include_pre) is None


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("offline"),
        json.JSONDecodeError("invalid", "x", 0),
        KeyError("tag_name"),
        IndexError("missing release"),
    ],
)
def test_fetch_from_github_swallows_network_and_decode_failures(
    failure: Exception,
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if isinstance(failure, httpx.HTTPError):
        get = MagicMock(side_effect=failure)
    else:
        release_response = MagicMock(status_code=200, content=b"small")
        release_response.json.side_effect = failure
        get = MagicMock(return_value=release_response)
    monkeypatch.setattr(version_check.httpx, "get", get)

    assert version_check._fetch_from_github() is None


def test_fetch_available_server_images_authenticates_filters_and_sorts(
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_response = response(data={"token": "registry-token"})
    tags_response = response(
        data={
            "tags": [
                "latest",
                "sha-deadbeef",
                "v1.2.0",
                "1.10.0",
                "2.0.0rc1",
                "2.0.0",
                "invalid version",
            ]
        }
    )
    get = MagicMock(side_effect=[token_response, tags_response])
    monkeypatch.setattr(version_check.httpx, "get", get)

    assert version_check.fetch_available_server_images() == ["2.0.0", "2.0.0rc1", "1.10.0", "1.2.0"]
    assert get.call_args_list == [
        call(
            "https://ghcr.io/token?scope=repository:agenticresourceregistry/observal-api:pull",
            timeout=10,
            headers={"User-Agent": f"observal-cli/{current_version}"},
        ),
        call(
            f"{version_check.GHCR_API_BASE}/observal-api/tags/list",
            timeout=10,
            headers={
                "Authorization": "Bearer registry-token",
                "Accept": "application/vnd.oci.image.index.v1+json",
                "User-Agent": f"observal-cli/{current_version}",
            },
        ),
    ]


@pytest.mark.parametrize("status_code", [401, 429, 503])
def test_fetch_available_server_images_stops_on_token_http_failure(
    status_code: int,
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get = MagicMock(return_value=response(status_code))
    monkeypatch.setattr(version_check.httpx, "get", get)

    assert version_check.fetch_available_server_images() == []
    assert get.call_count == 1


def test_fetch_available_server_images_requires_token(
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get = MagicMock(return_value=response(data={"token": ""}))
    monkeypatch.setattr(version_check.httpx, "get", get)

    assert version_check.fetch_available_server_images() == []
    assert get.call_count == 1


def test_fetch_available_server_images_rejects_tag_http_failure(
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get = MagicMock(side_effect=[response(data={"token": "token"}), response(429)])
    monkeypatch.setattr(version_check.httpx, "get", get)

    assert version_check.fetch_available_server_images() == []
    assert get.call_count == 2


@pytest.mark.parametrize(
    "failure",
    [httpx.ReadTimeout("slow registry"), json.JSONDecodeError("invalid", "x", 0), KeyError("token")],
)
def test_fetch_available_server_images_swallows_network_and_json_failures(
    failure: Exception,
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if isinstance(failure, httpx.HTTPError):
        get = MagicMock(side_effect=failure)
    else:
        token_response = MagicMock(status_code=200)
        token_response.json.side_effect = failure
        get = MagicMock(return_value=token_response)
    monkeypatch.setattr(version_check.httpx, "get", get)

    assert version_check.fetch_available_server_images() == []


def test_verify_server_image_exists_uses_exact_manifest_request(
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get = MagicMock(return_value=response(data={"token": "registry-token"}))
    head = MagicMock(return_value=response(200))
    monkeypatch.setattr(version_check.httpx, "get", get)
    monkeypatch.setattr(version_check.httpx, "head", head)

    assert version_check.verify_server_image_exists("2.4.1") is True
    get.assert_called_once_with(
        "https://ghcr.io/token?scope=repository:agenticresourceregistry/observal-api:pull",
        timeout=10,
        headers={"User-Agent": f"observal-cli/{current_version}"},
    )
    head.assert_called_once_with(
        f"{version_check.GHCR_API_BASE}/observal-api/manifests/2.4.1",
        timeout=10,
        headers={
            "Authorization": "Bearer registry-token",
            "Accept": "application/vnd.oci.image.index.v1+json",
            "User-Agent": f"observal-cli/{current_version}",
        },
    )


@pytest.mark.parametrize("status_code", [401, 429, 503])
def test_verify_server_image_exists_stops_on_token_http_failure(
    status_code: int,
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get = MagicMock(return_value=response(status_code))
    head = MagicMock(side_effect=blocked("httpx.head"))
    monkeypatch.setattr(version_check.httpx, "get", get)
    monkeypatch.setattr(version_check.httpx, "head", head)

    assert version_check.verify_server_image_exists("2.4.1") is False
    head.assert_not_called()


def test_verify_server_image_exists_returns_false_for_missing_manifest(
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(version_check.httpx, "get", MagicMock(return_value=response(data={"token": "token"})))
    monkeypatch.setattr(version_check.httpx, "head", MagicMock(return_value=response(404)))

    assert version_check.verify_server_image_exists("missing") is False


@pytest.mark.parametrize("failure_at", ["token", "manifest", "json"])
def test_verify_server_image_exists_swallows_transport_and_json_failures(
    failure_at: str,
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = httpx.ConnectError("offline")
    if failure_at == "token":
        get = MagicMock(side_effect=failure)
        head = MagicMock()
    elif failure_at == "manifest":
        get = MagicMock(return_value=response(data={"token": "token"}))
        head = MagicMock(side_effect=failure)
    else:
        token_response = MagicMock(status_code=200)
        token_response.json.side_effect = json.JSONDecodeError("invalid", "x", 0)
        get = MagicMock(return_value=token_response)
        head = MagicMock()
    monkeypatch.setattr(version_check.httpx, "get", get)
    monkeypatch.setattr(version_check.httpx, "head", head)

    assert version_check.verify_server_image_exists("2.4.1") is False


def test_resolve_update_source_prefers_reachable_configured_server(
    isolated_boundaries: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_boundaries.config.update(
        server_url="https://registry.example.test///",
        access_token="server-token",
    )
    release = {"latest_version": "2.0.0", "source": "server"}
    fetch_server = MagicMock(return_value=release)
    fetch_github = MagicMock(side_effect=blocked("fetch github"))
    monkeypatch.setattr(version_check, "_fetch_from_server", fetch_server)
    monkeypatch.setattr(version_check, "_fetch_from_github", fetch_github)

    assert version_check._resolve_update_source() is release
    fetch_server.assert_called_once_with("https://registry.example.test", "server-token")
    fetch_github.assert_not_called()


def test_resolve_update_source_falls_back_to_github_after_server_failure(
    isolated_boundaries: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_boundaries.config.update(
        server_url="https://registry.example.test/",
        access_token="server-token",
    )
    github_release = {"latest_version": "2.0.0", "source": "github"}
    fetch_server = MagicMock(return_value=None)
    fetch_github = MagicMock(return_value=github_release)
    monkeypatch.setattr(version_check, "_fetch_from_server", fetch_server)
    monkeypatch.setattr(version_check, "_fetch_from_github", fetch_github)

    assert version_check._resolve_update_source() is github_release
    fetch_server.assert_called_once_with("https://registry.example.test", "server-token")
    fetch_github.assert_called_once_with()


@pytest.mark.parametrize(
    ("server_url", "token"),
    [("", ""), ("https://registry.example.test", ""), ("", "token")],
)
def test_resolve_update_source_skips_incomplete_server_configuration(
    server_url: str,
    token: str,
    isolated_boundaries: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_boundaries.config.update(server_url=server_url, access_token=token)
    fetch_server = MagicMock(side_effect=blocked("fetch server"))
    fetch_github = MagicMock(return_value=None)
    monkeypatch.setattr(version_check, "_fetch_from_server", fetch_server)
    monkeypatch.setattr(version_check, "_fetch_from_github", fetch_github)

    assert version_check._resolve_update_source() is None
    fetch_server.assert_not_called()
    fetch_github.assert_called_once_with()


def test_maybe_check_honors_config_opt_out_before_cache_or_network(
    isolated_boundaries: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_boundaries.config["update_check"] = False
    read_cache = MagicMock(side_effect=blocked("read cache"))
    resolve = MagicMock(side_effect=blocked("resolve source"))
    monkeypatch.setattr(version_check, "_read_cache", read_cache)
    monkeypatch.setattr(version_check, "_resolve_update_source", resolve)

    assert version_check.maybe_check() is None
    isolated_boundaries.load_config.assert_called_once_with()
    read_cache.assert_not_called()
    resolve.assert_not_called()


@pytest.mark.parametrize("value", ["1", "true", "0"])
def test_maybe_check_honors_nonempty_environment_opt_out(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_cache = MagicMock(side_effect=blocked("read cache"))
    monkeypatch.setenv("OBSERVAL_NO_UPDATE_CHECK", value)
    monkeypatch.setattr(version_check, "_read_cache", read_cache)

    assert version_check.maybe_check() is None
    read_cache.assert_not_called()


def test_maybe_check_returns_exact_cached_github_notice_without_network(
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = {
        "last_checked": FIXED_ISO,
        "latest_version": "2.0.0",
        "release_url": "https://example.test/release",
        "published_at": "2026-07-08T08:00:00Z",
    }
    resolve = MagicMock(side_effect=blocked("resolve source"))
    write_cache = MagicMock(side_effect=blocked("write cache"))
    monkeypatch.setattr(version_check, "_read_cache", lambda: cache)
    monkeypatch.setattr(version_check, "_resolve_update_source", resolve)
    monkeypatch.setattr(version_check, "_write_cache", write_cache)

    assert version_check.maybe_check() == version_check.UpdateAvailable(
        current=current_version,
        latest="2.0.0",
        release_url="https://example.test/release",
        published_at="2026-07-08T08:00:00Z",
        source="github",
        direction="upgrade",
    )
    resolve.assert_not_called()
    write_cache.assert_not_called()


def test_maybe_check_returns_exact_cached_server_downgrade_notice(
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = {
        "last_checked": FIXED_ISO,
        "latest_version": "1.0.0",
        "release_url": "ignored for server downgrade",
        "published_at": "server publication",
        "source": "server",
    }
    monkeypatch.setattr(version_check, "_read_cache", lambda: cache)

    assert version_check.maybe_check() == version_check.UpdateAvailable(
        current=current_version,
        latest="1.0.0",
        release_url="",
        published_at="server publication",
        source="server",
        direction="downgrade",
    )


@pytest.mark.parametrize(
    "cache",
    [
        {"last_checked": FIXED_ISO},
        {"last_checked": FIXED_ISO, "latest_version": "1.2.3"},
        {"last_checked": FIXED_ISO, "latest_version": "1.0.0", "source": "github"},
    ],
)
def test_maybe_check_returns_none_for_fresh_cache_without_action(
    cache: dict,
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve = MagicMock(side_effect=blocked("resolve source"))
    monkeypatch.setattr(version_check, "_read_cache", lambda: cache)
    monkeypatch.setattr(version_check, "_resolve_update_source", resolve)

    assert version_check.maybe_check() is None
    resolve.assert_not_called()


def test_maybe_check_records_failed_refresh_with_exact_time_and_preserved_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_cache = {
        "last_checked": timestamp(-version_check.CHECK_INTERVAL_DEFAULT),
        "latest_version": "1.9.0",
        "source": "github",
    }
    write_cache = MagicMock()
    monkeypatch.setattr(version_check, "_read_cache", lambda: stale_cache)
    monkeypatch.setattr(version_check, "_resolve_update_source", lambda: None)
    monkeypatch.setattr(version_check, "_write_cache", write_cache)

    assert version_check.maybe_check() is None
    write_cache.assert_called_once_with(
        {
            **stale_cache,
            "last_checked": FIXED_ISO,
            "fetch_failed": True,
        }
    )


def test_maybe_check_writes_exact_github_cache_and_returns_fresh_notice(
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = {
        "latest_version": "2.0.0",
        "release_url": "https://example.test/releases/2.0.0",
        "published_at": "2026-07-08T08:00:00Z",
        "source": "github",
        "prerelease": False,
    }
    write_cache = MagicMock()
    monkeypatch.setattr(version_check, "_read_cache", lambda: None)
    monkeypatch.setattr(version_check, "_resolve_update_source", lambda: release)
    monkeypatch.setattr(version_check, "_write_cache", write_cache)

    assert version_check.maybe_check() == version_check.UpdateAvailable(
        current=current_version,
        latest="2.0.0",
        release_url="https://example.test/releases/2.0.0",
        published_at="2026-07-08T08:00:00Z",
        source="github",
        direction="upgrade",
    )
    write_cache.assert_called_once_with(
        {
            "last_checked": FIXED_ISO,
            "latest_version": "2.0.0",
            "release_url": "https://example.test/releases/2.0.0",
            "published_at": "2026-07-08T08:00:00Z",
            "source": "github",
            "server_version": "",
            "fetch_failed": False,
        }
    )


def test_maybe_check_returns_fresh_server_downgrade_and_caches_server_version(
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = {
        "latest_version": "1.0.0",
        "release_url": "",
        "published_at": "",
        "source": "server",
        "server_version": "1.0.0",
    }
    write_cache = MagicMock()
    monkeypatch.setattr(version_check, "_read_cache", lambda: None)
    monkeypatch.setattr(version_check, "_resolve_update_source", lambda: release)
    monkeypatch.setattr(version_check, "_write_cache", write_cache)

    assert version_check.maybe_check() == version_check.UpdateAvailable(
        current=current_version,
        latest="1.0.0",
        release_url="",
        published_at="",
        source="server",
        direction="downgrade",
    )
    assert write_cache.call_args.args[0]["server_version"] == "1.0.0"


def test_maybe_check_caches_equal_release_without_notice(
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_cache = MagicMock()
    monkeypatch.setattr(version_check, "_read_cache", lambda: None)
    monkeypatch.setattr(
        version_check,
        "_resolve_update_source",
        lambda: {"latest_version": current_version},
    )
    monkeypatch.setattr(version_check, "_write_cache", write_cache)

    assert version_check.maybe_check() is None
    assert write_cache.call_args.args[0] == {
        "last_checked": FIXED_ISO,
        "latest_version": current_version,
        "release_url": "",
        "published_at": "",
        "source": "github",
        "server_version": "",
        "fetch_failed": False,
    }


@pytest.mark.parametrize("failure_at", ["interval", "resolve", "write", "current"])
def test_maybe_check_never_leaks_boundary_failures(
    failure_at: str,
    isolated_boundaries: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = RuntimeError(f"{failure_at} failed")
    if failure_at == "interval":
        isolated_boundaries.config["update_check_interval"] = "invalid"
    else:
        monkeypatch.setattr(version_check, "_read_cache", lambda: None)
        if failure_at == "resolve":
            monkeypatch.setattr(version_check, "_resolve_update_source", MagicMock(side_effect=failure))
        else:
            monkeypatch.setattr(
                version_check,
                "_resolve_update_source",
                lambda: {"latest_version": "2.0.0", "source": "github"},
            )
            if failure_at == "write":
                monkeypatch.setattr(version_check, "_write_cache", MagicMock(side_effect=failure))
            else:
                monkeypatch.setattr(version_check, "_write_cache", MagicMock())
                monkeypatch.setattr(version_check, "get_current_version", MagicMock(side_effect=failure))

    assert version_check.maybe_check() is None


def test_maybe_check_is_silent_and_returns_notice_data(
    current_version: str,
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(version_check, "_read_cache", lambda: None)
    monkeypatch.setattr(
        version_check,
        "_resolve_update_source",
        lambda: {"latest_version": "2.0.0", "source": "github"},
    )
    monkeypatch.setattr(version_check, "_write_cache", MagicMock())

    assert version_check.maybe_check() is not None
    assert messages == []


def test_fetch_all_releases_paginates_with_exact_requests_and_filters_prereleases(
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_page = [
        {
            "tag_name": "v2.0.0",
            "published_at": "2026-07-08",
            "prerelease": False,
            "html_url": "stable-url",
        },
        {
            "tag_name": "v3.0.0rc1",
            "published_at": "2026-07-09",
            "prerelease": True,
            "html_url": "preview-url",
        },
    ]
    get = MagicMock(side_effect=[response(data=first_page), response(data=[])])
    monkeypatch.setattr(version_check.httpx, "get", get)

    assert version_check.fetch_all_releases() == [
        {
            "version": "2.0.0",
            "published_at": "2026-07-08",
            "prerelease": False,
            "url": "stable-url",
        }
    ]
    assert get.call_args_list == [
        call(
            f"{version_check.GITHUB_API_BASE}/{version_check.GITHUB_REPO_DEFAULT}/releases?per_page=10&page=1",
            timeout=15,
            headers=github_headers(current_version),
        ),
        call(
            f"{version_check.GITHUB_API_BASE}/{version_check.GITHUB_REPO_DEFAULT}/releases?per_page=10&page=2",
            timeout=15,
            headers=github_headers(current_version),
        ),
    ]


def test_fetch_all_releases_includes_prereleases_when_requested(
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview = {
        "tag_name": "v3.0.0rc1",
        "published_at": "preview-date",
        "prerelease": True,
        "html_url": "preview-url",
    }
    get = MagicMock(side_effect=[response(data=[preview]), response(data=[])])
    monkeypatch.setattr(version_check.httpx, "get", get)

    assert version_check.fetch_all_releases(include_pre=True) == [
        {
            "version": "3.0.0rc1",
            "published_at": "preview-date",
            "prerelease": True,
            "url": "preview-url",
        }
    ]


@pytest.mark.parametrize("failure", [response(429), httpx.ConnectError("offline")])
def test_fetch_all_releases_stops_on_http_or_network_failure(
    failure: httpx.Response | Exception,
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get = MagicMock(side_effect=failure if isinstance(failure, Exception) else None, return_value=failure)
    monkeypatch.setattr(version_check.httpx, "get", get)

    assert version_check.fetch_all_releases() == []
    assert get.call_count == 1


def test_fetch_all_releases_has_ten_page_safety_cap(
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = {"tag_name": "v2.0.0"}
    get = MagicMock(return_value=response(data=[release]))
    monkeypatch.setattr(version_check.httpx, "get", get)

    assert len(version_check.fetch_all_releases()) == 10
    assert get.call_count == 10
    assert get.call_args.args[0].endswith("per_page=10&page=10")


def test_check_version_compatibility_skips_development_install_before_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_cache = MagicMock(side_effect=blocked("read cache"))
    monkeypatch.setattr(version_check, "get_current_version", lambda: "0.0.0")
    monkeypatch.setattr(version_check, "_read_cache", read_cache)

    assert version_check.check_version_compatibility("https://registry.example.test") is None
    read_cache.assert_not_called()


def test_check_version_compatibility_uses_fresh_server_cache_without_network(
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = {
        "server_version": current_version,
        "source": "server",
        "last_checked": FIXED_ISO,
    }
    get = MagicMock(side_effect=blocked("httpx.get"))
    monkeypatch.setattr(version_check, "_read_cache", lambda: cache)
    monkeypatch.setattr(version_check.httpx, "get", get)

    assert version_check.check_version_compatibility("https://registry.example.test") is None
    get.assert_not_called()


def test_check_version_compatibility_fetches_stale_cache_with_exact_url(
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = {
        "server_version": "9.0.0",
        "source": "server",
        "last_checked": timestamp(-60),
    }
    get = MagicMock(return_value=response(data={"server_version": current_version}))
    monkeypatch.setattr(version_check, "_read_cache", lambda: cache)
    monkeypatch.setattr(version_check.httpx, "get", get)

    assert version_check.check_version_compatibility("https://registry.example.test///") is None
    get.assert_called_once_with(
        "https://registry.example.test/api/v1/config/version",
        timeout=5,
    )


@pytest.mark.parametrize("status_code", [401, 429, 503])
def test_check_version_compatibility_ignores_non_success_status(
    status_code: int,
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_response = MagicMock(status_code=status_code)
    server_response.json.side_effect = blocked("response.json")
    get = MagicMock(return_value=server_response)
    monkeypatch.setattr(version_check, "_read_cache", lambda: None)
    monkeypatch.setattr(version_check.httpx, "get", get)

    assert version_check.check_version_compatibility("https://registry.example.test") is None
    assert get.call_count == 1
    server_response.json.assert_not_called()


@pytest.mark.parametrize(
    "outcome",
    [
        httpx.ConnectError("offline"),
        json.JSONDecodeError("invalid", "x", 0),
        None,
        "dev",
        "invalid version",
    ],
)
def test_check_version_compatibility_skips_unusable_server_versions(
    outcome: Exception | str | None,
    current_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if isinstance(outcome, httpx.HTTPError):
        get = MagicMock(side_effect=outcome)
    elif isinstance(outcome, json.JSONDecodeError):
        server_response = MagicMock(status_code=200)
        server_response.json.side_effect = outcome
        get = MagicMock(return_value=server_response)
    else:
        get = MagicMock(return_value=response(data={"server_version": outcome}))
    monkeypatch.setattr(version_check, "_read_cache", lambda: None)
    monkeypatch.setattr(version_check.httpx, "get", get)

    assert version_check.check_version_compatibility("https://registry.example.test") is None


def test_check_version_compatibility_skips_invalid_cli_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(version_check, "get_current_version", lambda: "development")
    monkeypatch.setattr(version_check, "_read_cache", lambda: None)
    monkeypatch.setattr(
        version_check.httpx,
        "get",
        MagicMock(return_value=response(data={"server_version": "2.0.0"})),
    )

    assert version_check.check_version_compatibility("https://registry.example.test") is None


def test_check_version_compatibility_dev_server_prints_source_install(
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from observal_cli import install_detector

    monkeypatch.setattr(version_check, "get_current_version", lambda: "1.13.1")
    monkeypatch.setattr(version_check, "_read_cache", lambda: None)
    monkeypatch.setattr(
        version_check.httpx,
        "get",
        MagicMock(return_value=response(data={"server_version": "1.15.0-dev.0"})),
    )
    monkeypatch.setattr(
        install_detector,
        "detect",
        lambda: install_detector.InstallInfo(install_detector.InstallMethod.UV_TOOL, Path("/x/observal"), True, "uv"),
    )

    with pytest.raises(typer.Exit) as error:
        version_check.check_version_compatibility("https://registry.example.test")

    assert error.value.exit_code == 1
    assert (
        "uv tool install --force git+https://github.com/AgenticResourceRegistry/AgenticResourceRegistry@main"
        in messages[0]
    )
    assert "observal-cli==" not in messages[0]


def test_check_version_compatibility_accepts_matching_prerelease_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(version_check, "get_current_version", lambda: "1.15.0-dev.0")
    monkeypatch.setattr(version_check, "_read_cache", lambda: None)
    monkeypatch.setattr(
        version_check.httpx,
        "get",
        MagicMock(return_value=response(data={"server_version": "1.15.0-dev.0"})),
    )

    assert version_check.check_version_compatibility("https://registry.example.test") is None


def test_check_version_compatibility_prints_exact_upgrade_guidance_and_exits(
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from observal_cli import install_detector

    upgrade_command = MagicMock(return_value="install exact version 2.0.0")
    monkeypatch.setattr(version_check, "get_current_version", lambda: "1.0.0")
    monkeypatch.setattr(version_check, "_read_cache", lambda: None)
    monkeypatch.setattr(
        version_check.httpx,
        "get",
        MagicMock(return_value=response(data={"server_version": "2.0.0"})),
    )
    monkeypatch.setattr(install_detector, "upgrade_command", upgrade_command)

    with pytest.raises(typer.Exit) as error:
        version_check.check_version_compatibility("https://registry.example.test")

    assert error.value.exit_code == 1
    upgrade_command.assert_called_once_with("2.0.0")
    assert messages == [
        "\n[bold red]✖ CLI version 1.0.0 is behind server 2.0.0.[/bold red]\n"
        "  Upgrade the CLI to match your server:\n\n"
        "    [cyan]install exact version 2.0.0[/cyan]\n"
    ]


def test_check_version_compatibility_prints_exact_downgrade_guidance_and_exits(
    messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(version_check, "get_current_version", lambda: "2.0.0")
    monkeypatch.setattr(version_check, "_read_cache", lambda: None)
    monkeypatch.setattr(
        version_check.httpx,
        "get",
        MagicMock(return_value=response(data={"server_version": "1.0.0"})),
    )

    with pytest.raises(typer.Exit) as error:
        version_check.check_version_compatibility("https://registry.example.test")

    assert error.value.exit_code == 1
    expected_command = f"observal self downgrade {VERSION_OPTION} 1.0.0"
    assert messages == [
        "\n[bold red]✖ CLI version 2.0.0 is ahead of server 1.0.0.[/bold red]\n"
        "  Downgrade the CLI to match your server:\n\n"
        f"    [cyan]{expected_command}[/cyan]\n"
    ]
