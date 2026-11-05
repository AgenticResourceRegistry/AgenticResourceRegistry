# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Structured redaction helpers for component discovery.

Discovery redacts before evidence crosses an adapter/provider boundary. These
helpers deliberately preserve names and behavior-relevant non-secret values
while replacing values that could contain credentials with one stable marker.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from observal_cli.discovery.models import DiagnosticCode, DiagnosticSeverity, DiscoveryDiagnostic
from observal_cli.discovery.serialize import privacy_safe_path

REDACTION_MARKER = "<secret>"

_SECRET_KEY_RE = re.compile(
    r"(?:^|[-_.])(?:api[-_]?key|access[-_]?key|secret(?:[-_]?key)?|token|password|passwd|pwd|credential|"
    r"authorization|auth|cookie|session|private[-_]?key|client[-_]?secret|proxy)(?:$|[-_.])",
    re.IGNORECASE,
)
_SECRET_OPTION_RE = re.compile(
    r"^--?(?:api[-_]?key|access[-_]?key|secret(?:[-_]?key)?|token|password|passwd|pwd|credential|"
    r"authorization|auth|cookie|header|proxy|client[-_]?secret)(?:=|$)",
    re.IGNORECASE,
)
_ENV_OPTION_RE = re.compile(r"^--?(?:env|environment)(?:=|$)", re.IGNORECASE)
_ENV_REFERENCE_RE = re.compile(r"^(?:\$[A-Za-z_][A-Za-z0-9_]*|\$\{[A-Za-z_][A-Za-z0-9_]*\})$")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{6,}\b")
_TOKEN_PREFIX_RE = re.compile(
    r"\b(?:sk-(?:proj-)?|sk-ant-|gh[opusr]_|github_pat_|glpat-|xox[baprs]-|npm_|hf_|AKIA|ASIA|AIza|SG\.)"
    r"[A-Za-z0-9_.-]{8,}\b"
)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?-----END(?: [A-Z0-9]+)? PRIVATE KEY-----",
    re.DOTALL,
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:api[-_]?key|access[-_]?key|secret(?:[-_]?key)?|token|password|passwd|pwd|credential|"
    r"authorization|client[-_]?secret)\b[\"']?\s*[:=]\s*[\"']?)([^\"'\s,;}]+)"
)
_AUTH_HEADER_RE = re.compile(r"(?i)(\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*:\s*)([^\r\n]+)")
_URL_USERINFO_RE = re.compile(r"(\b[A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]*@")
# Paths remain behavior-relevant launch data; dedicated matchers above still
# catch prefixed tokens, JWTs, and private keys that contain dots or slashes.
_HIGH_ENTROPY_RE = re.compile(r"^(?=.{32,}$)(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9_+=-]+$")


def is_secret_name(name: str) -> bool:
    stripped = name.strip()
    compact = re.sub(r"[^a-z0-9]", "", stripped.casefold())
    return bool(
        _SECRET_KEY_RE.search(stripped)
        or compact in {"auth", "cookie", "password", "passwd", "proxy", "pwd", "session"}
        or any(
            marker in compact
            for marker in (
                "apikey",
                "accesskey",
                "authorization",
                "clientsecret",
                "credential",
                "privatekey",
                "secret",
                "token",
            )
        )
    )


def _is_reference(value: str) -> bool:
    return bool(_ENV_REFERENCE_RE.fullmatch(value.strip()))


def is_secret_value(value: str) -> bool:
    stripped = value.strip()
    return bool(
        _JWT_RE.search(stripped)
        or _TOKEN_PREFIX_RE.search(stripped)
        or _PRIVATE_KEY_RE.search(stripped)
        or _HIGH_ENTROPY_RE.fullmatch(stripped)
    )


def sanitize_url(url: str, *, remove_all_query: bool = False) -> str | None:
    """Remove URL credentials, fragments, and secret query parameters.

    Returns ``None`` for unsupported or malformed URLs. Non-secret query
    parameters are retained in sorted order because they may affect behavior.
    """

    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return None

        scheme = parts.scheme.lower()
        host = parts.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        try:
            port = parts.port
        except ValueError:
            return None
        if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
            host = f"{host}:{port}"

        path = parts.path or "/"
        segments = [unquote(segment) for segment in path.split("/")]
        # Credentials also turn up in URL paths, not just userinfo/query.
        # Fail closed rather than display a partly redacted endpoint.
        if any(
            is_secret_value(segment) or (index > 0 and is_secret_name(segments[index - 1]) and segment)
            for index, segment in enumerate(segments)
        ):
            return None
        if path != "/":
            path = path.rstrip("/") or "/"

        query_items = []
        if not remove_all_query:
            query_items = sorted(
                (key, value)
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
                if not is_secret_name(key) and not is_secret_value(value)
            )
        return urlunsplit((scheme, host, path, urlencode(query_items, doseq=True), ""))
    except (TypeError, UnicodeError, ValueError):
        return None


def redact_text(value: str) -> str:
    """Redact recognizable credentials while preserving ordinary text."""

    if _is_reference(value):
        return value
    value = _URL_USERINFO_RE.sub(lambda match: match.group(1), value)
    if "://" in value and not any(character.isspace() for character in value):
        sanitized_url = sanitize_url(value)
        if sanitized_url is not None:
            return sanitized_url

    if is_secret_value(value):
        return REDACTION_MARKER

    redacted = _PRIVATE_KEY_RE.sub(REDACTION_MARKER, value)
    redacted = _JWT_RE.sub(REDACTION_MARKER, redacted)
    redacted = _TOKEN_PREFIX_RE.sub(REDACTION_MARKER, redacted)
    redacted = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}{REDACTION_MARKER}", redacted)
    redacted = _AUTH_HEADER_RE.sub(lambda match: f"{match.group(1)}{REDACTION_MARKER}", redacted)

    # Sanitize URLs embedded as complete whitespace-delimited values. This
    # covers connection strings and diagnostic URLs without attempting to
    # parse arbitrary prose as shell syntax.
    words = re.split(r"(\s+)", redacted)
    for index, word in enumerate(words):
        core = word.lstrip("([{\"'")
        prefix = word[: len(word) - len(core)]
        stripped = core.rstrip(",;)]}\"'")
        suffix = core[len(stripped) :]
        if "://" not in stripped:
            continue
        sanitized = sanitize_url(stripped)
        if sanitized is not None:
            words[index] = prefix + sanitized + suffix
    return "".join(words)


def redact_arguments(arguments: Sequence[str]) -> tuple[tuple[str, ...], bool]:
    """Fail closed when an arbitrary argument value cannot be classified.

    Option names and environment references are safe to display. Values for
    recognized secret/environment options are deliberately replaced. Every
    other positional or ``--option=value`` value is opaque: redact it and mark
    the launch non-canonical so it cannot receive an exact fingerprint.
    """

    values = [str(argument) for argument in arguments]
    redacted: list[str] = []
    index = 0
    safe = True
    while index < len(values):
        argument = values[index]
        if _SECRET_OPTION_RE.match(argument):
            if "=" in argument:
                option, secret_value = argument.split("=", 1)
                replacement = secret_value if _is_reference(secret_value) else REDACTION_MARKER
                redacted.append(f"{option}={replacement}")
            else:
                redacted.append(argument)
                if index + 1 < len(values):
                    secret_value = values[index + 1]
                    redacted.append(secret_value if _is_reference(secret_value) else REDACTION_MARKER)
                    index += 1
                else:
                    safe = False
        elif _ENV_OPTION_RE.match(argument):
            if "=" in argument:
                option, assignment = argument.split("=", 1)
                name, separator, raw_value = assignment.partition("=")
                replacement = raw_value if separator and _is_reference(raw_value) else REDACTION_MARKER
                redacted.append(f"{option}={name}={replacement}" if name else f"{option}={REDACTION_MARKER}")
            else:
                redacted.append(argument)
                if index + 1 < len(values):
                    assignment = values[index + 1]
                    name, separator, raw_value = assignment.partition("=")
                    replacement = raw_value if separator and _is_reference(raw_value) else REDACTION_MARKER
                    redacted.append(f"{name}={replacement}" if name else REDACTION_MARKER)
                    index += 1
                else:
                    safe = False
        elif argument.startswith("-") and "=" not in argument:
            # A standalone option name carries no value. If the next token is a
            # value, that token is handled conservatively on the next pass.
            redacted.append(redact_text(argument))
        elif _is_reference(argument):
            redacted.append(argument)
        else:
            # There is no universal CLI grammar that can prove an arbitrary
            # value is non-secret. Redact it rather than extending a denylist.
            if "=" in argument and argument.startswith("-"):
                option, _value = argument.split("=", 1)
                redacted.append(f"{option}={REDACTION_MARKER}")
            else:
                redacted.append(REDACTION_MARKER)
            safe = False
        index += 1
    return tuple(redacted), safe


def _redact_mapping(value: Mapping[Any, Any], *, parent_key: str | None) -> dict[str, Any]:
    redact_all_values = bool(parent_key and parent_key.casefold() in {"env", "environment", "headers", "http_headers"})
    result: dict[str, Any] = {}
    for raw_key in sorted(value, key=lambda item: str(item)):
        key = str(raw_key)
        item = value[raw_key]
        if redact_all_values or is_secret_name(key):
            if isinstance(item, str) and _is_reference(item):
                result[key] = item
            else:
                result[key] = REDACTION_MARKER
        else:
            result[key] = redact_value(item, key=key)
    return result


def redact_value(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact nested discovery configuration."""

    if isinstance(value, Mapping):
        return _redact_mapping(value, parent_key=key)
    if isinstance(value, (list, tuple)):
        if key and key.casefold() in {"args", "arguments", "command_args"}:
            return list(redact_arguments([str(item) for item in value])[0])
        return [redact_value(item, key=key) for item in value]
    if isinstance(value, Path):
        return privacy_safe_path(value)
    if isinstance(value, str):
        if key and is_secret_name(key) and not _is_reference(value):
            return REDACTION_MARKER
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))


def sanitize_diagnostic_message(message: object) -> str:
    """Build a one-line redacted diagnostic message from an exception or value."""

    safe = redact_text(str(message)).replace("\r", " ").replace("\n", " ")
    return " ".join(safe.split())


def make_diagnostic(
    code: DiagnosticCode,
    severity: DiagnosticSeverity,
    provider: str,
    message: object,
    *,
    source: str | Path | None = None,
) -> DiscoveryDiagnostic:
    """Construct a diagnostic after sanitizing both message and source."""

    return DiscoveryDiagnostic(
        code=code,
        severity=severity,
        provider=provider,
        source=privacy_safe_path(source) if source is not None else None,
        message=sanitize_diagnostic_message(message),
    )
