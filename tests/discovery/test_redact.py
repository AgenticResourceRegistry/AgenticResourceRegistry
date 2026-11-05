# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

import pytest

from observal_cli.discovery.redact import (
    REDACTION_MARKER,
    redact_arguments,
    redact_text,
    redact_value,
    sanitize_url,
)


@pytest.mark.parametrize(
    "value",
    [
        "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890abcd",
        "npm_aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890abcd",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcdefghijk",
        "AKIAIOSFODNN7EXAMPLE",
        "abcdefghijklmnopqrstuvwxyz1234567890ABCDEF",
    ],
)
def test_common_tokens_and_high_entropy_values_are_redacted(value: str) -> None:
    assert redact_text(value) == REDACTION_MARKER


def test_unclassified_argument_values_are_redacted_and_fail_closed() -> None:
    arguments, safe = redact_arguments(["--mode", "read", "--key", "hunter2", "-k", "another", "${API_KEY}"])

    assert not safe
    assert arguments == ("--mode", "<secret>", "--key", "<secret>", "-k", "<secret>", "${API_KEY}")


def test_unclassified_path_arguments_are_redacted() -> None:
    path = "servers/mcp-filesystem/dist/index1.js"

    arguments, safe = redact_arguments([path])

    assert not safe
    assert arguments == ("<secret>",)


def test_secret_options_are_redacted_in_equals_and_adjacent_forms() -> None:
    arguments, safe = redact_arguments(
        ["--token=first-secret", "--password", "second-secret", "--header", "Authorization: Bearer third-secret"]
    )

    assert safe
    assert arguments == (
        "--token=<secret>",
        "--password",
        "<secret>",
        "--header",
        "<secret>",
    )


def test_environment_references_are_preserved_for_secret_options() -> None:
    arguments, safe = redact_arguments(["--token=${API_TOKEN}", "--password", "$PASSWORD"])

    assert safe
    assert arguments == ("--token=${API_TOKEN}", "--password", "$PASSWORD")


def test_missing_secret_option_value_is_not_safe_to_fingerprint() -> None:
    arguments, safe = redact_arguments(["--mode", "read", "--token"])

    assert arguments == ("--mode", "<secret>", "--token")
    assert not safe


def test_recursive_redaction_preserves_names_and_removes_nested_values(tmp_path: Path) -> None:
    raw = {
        "handler": {
            "env": {"API_KEY": "environment-secret", "MODE": "read", "REFERENCE": "${SHARED_TOKEN}"},
            "headers": {"Authorization": "Bearer header-secret", "X-Mode": "read"},
            "args": ["--mode", "read", "--token", "argument-secret"],
            "nested": [{"password": "password-secret", "path": tmp_path / "private" / "hook.sh"}],
        }
    }

    redacted = redact_value(raw)
    serialized = json.dumps(redacted)

    for secret in ("environment-secret", "header-secret", "argument-secret", "password-secret", str(tmp_path)):
        assert secret not in serialized
    assert redacted["handler"]["env"] == {
        "API_KEY": "<secret>",
        "MODE": "<secret>",
        "REFERENCE": "${SHARED_TOKEN}",
    }
    assert redacted["handler"]["headers"] == {"Authorization": "<secret>", "X-Mode": "<secret>"}
    assert redacted["handler"]["args"] == ["--mode", "<secret>", "--token", "<secret>"]
    assert redacted["handler"]["nested"][0]["path"].endswith("/hook.sh")


def test_url_redaction_removes_userinfo_default_port_fragment_and_secret_queries() -> None:
    url = (
        "HTTPS://alice:password@Example.COM:443/mcp/?accessToken=secret&mode=read&value="
        "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890abcd#private"
    )

    assert sanitize_url(url) == "https://example.com/mcp?mode=read"


def test_connection_string_credentials_are_removed_from_text() -> None:
    redacted = redact_text("failed to connect to https://alice:password@example.test:8443/mcp?api_key=secret")

    assert redacted == "failed to connect to https://example.test:8443/mcp"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("postgres://alice:s3cret@db.test/app", "postgres://db.test/app"),
        ("redis://:password@cache.test:6379/0", "redis://cache.test:6379/0"),
        (
            "failed at postgres://alice:s3cret@db.test/app",
            "failed at postgres://db.test/app",
        ),
    ],
)
def test_non_http_url_credentials_are_removed(value: str, expected: str) -> None:
    assert redact_text(value) == expected


def test_private_keys_are_removed() -> None:
    begin_marker = "-----BEGIN PRIVATE" + " KEY-----"
    end_marker = "-----END PRIVATE" + " KEY-----"
    private_key = f"{begin_marker}\nprivate-material\n{end_marker}"

    assert redact_text(private_key) == REDACTION_MARKER
