# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Fail-closed parsing of local MCP launch metadata for display only."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement

from observal_cli.constants import VALID_MCP_TRANSPORTS
from observal_cli.discovery.models import LaunchKind, SanitizedLaunch
from observal_cli.discovery.redact import is_secret_value, redact_arguments, sanitize_url

_NPM_NAME_RE = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)
_PACKAGE_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_PYTHON_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_PEP503_SEPARATORS_RE = re.compile(r"[-_.]+")
_SHELL_WRAPPERS = {"sh", "bash", "zsh", "cmd", "cmd.exe", "powershell", "pwsh"}
_NPM_SAFE_OPTIONS = {"-y", "--yes", "--silent", "--offline", "--prefer-offline", "--ignore-scripts"}
_MCP_ENVIRONMENT_KEYS = ("env", "environment", "environmentVariables", "environment_variables")
_MCP_HEADER_KEYS = ("headers", "httpHeaders", "http_headers")
_MCP_TRANSPORT_KEYS = ("transport", "type")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9-]+$")


@dataclass(frozen=True)
class LaunchNormalizationResult:
    launch: SanitizedLaunch | None
    complete: bool
    reason: str | None = None


@dataclass(frozen=True)
class McpLaunchMetadata:
    """Non-secret MCP metadata plus whether every recognized field was valid."""

    environment_names: tuple[str, ...] = ()
    header_names: tuple[str, ...] = ()
    transport: str | None = None
    complete: bool = True
    reason: str | None = None


def canonicalize_python_package_name(name: str) -> str:
    """Return the PEP 503 normalized package name."""

    return _PEP503_SEPARATORS_RE.sub("-", name).lower()


def split_npm_package_spec(requirement: str) -> tuple[str, str | None] | None:
    """Split an npm package requirement into package and version/tag."""

    value = requirement.strip()
    if not value or value.startswith(("npm:", "git+", "http://", "https://", "file:")) or "@npm:" in value:
        return None

    version: str | None = None
    if value.startswith("@"):
        slash = value.find("/")
        if slash <= 1:
            return None
        separator = value.rfind("@")
        if separator > slash:
            value, version = value[:separator], value[separator + 1 :]
    elif "@" in value:
        value, version = value.rsplit("@", 1)

    if not _NPM_NAME_RE.fullmatch(value) or (
        version is not None and (not _PACKAGE_VERSION_RE.fullmatch(version) or is_secret_value(version))
    ):
        return None
    return value.lower(), version


def _canonical_python_base(requirement: Requirement) -> str:
    package = canonicalize_python_package_name(requirement.name)
    extras = f"[{','.join(sorted(requirement.extras))}]" if requirement.extras else ""
    return f"{package}{extras}"


def _split_python_requirement(requirement: str) -> tuple[str, str | None, str | None] | None:
    value = requirement.strip()
    if not value:
        return None

    # uv commonly accepts ``name@version`` in addition to PEP 508 syntax.
    if "@" in value and " @ " not in value:
        base, version = value.rsplit("@", 1)
        if (
            base
            and _PACKAGE_VERSION_RE.fullmatch(version)
            and not is_secret_value(version)
            and not any(marker in base for marker in "/\\:")
        ):
            try:
                parsed_base = Requirement(base)
            except InvalidRequirement:
                return None
            if parsed_base.url:
                return None
            package = canonicalize_python_package_name(parsed_base.name)
            return package, f"{_canonical_python_base(parsed_base)}@{version}", version

    try:
        parsed = Requirement(value)
    except InvalidRequirement:
        return None
    if parsed.url:
        return None
    package = canonicalize_python_package_name(parsed.name)
    metadata = ""
    if parsed.extras:
        metadata += f"[{','.join(sorted(parsed.extras))}]"
    if parsed.specifier:
        metadata += str(parsed.specifier)
    if parsed.marker:
        metadata += f"; {parsed.marker}"
    canonical_requirement = f"{package}{metadata}" if metadata else None
    version_metadata = str(parsed.specifier) or None
    return package, canonical_requirement, version_metadata


def _result(launch: SanitizedLaunch, *, safe: bool = True) -> LaunchNormalizationResult:
    """Keep only launches whose structured fields can be safely summarized."""
    for key in ("package", "module", "script", "binary", "requirement", "version", "transport"):
        value = getattr(launch, key)
        if value is not None and is_secret_value(value):
            safe = False
    if launch.url is not None and sanitize_url(launch.url, remove_all_query=True) is None:
        safe = False
    if launch.arguments and not redact_arguments(launch.arguments)[1]:
        safe = False
    return LaunchNormalizationResult(launch, safe, None if safe else "unsafe_or_unclassified_arguments")


def _incomplete(reason: str) -> LaunchNormalizationResult:
    return LaunchNormalizationResult(None, False, reason)


def _named_metadata(value: object, *, separator: str) -> tuple[tuple[str, ...], bool]:
    raw_names: list[object]
    if isinstance(value, Mapping):
        raw_names = list(value)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        raw_names = []
        for item in value:
            if isinstance(item, Mapping):
                raw_names.append(item.get("name"))
            elif isinstance(item, str):
                raw_names.append(item.split(separator, 1)[0])
            else:
                return (), False
    else:
        return (), False
    valid_name = _ENV_NAME_RE if separator == "=" else _HEADER_NAME_RE
    if any(not isinstance(name, str) or not valid_name.fullmatch(name.strip()) for name in raw_names):
        return (), False
    return tuple(sorted({name.strip() for name in raw_names}, key=str.casefold)), True


def _metadata_alias(config: Mapping[str, object], keys: tuple[str, ...]) -> tuple[object | None, bool, bool]:
    present = [key for key in keys if key in config]
    if not present:
        return None, False, True
    if len(present) != 1:
        return None, True, False
    return config[present[0]], True, True


def extract_mcp_launch_metadata(config: Mapping[str, object]) -> McpLaunchMetadata:
    """Extract non-secret MCP metadata and fail closed on recognized malformed fields."""

    environment, present, unambiguous = _metadata_alias(config, _MCP_ENVIRONMENT_KEYS)
    if not unambiguous:
        return McpLaunchMetadata(complete=False, reason="ambiguous_environment_metadata")
    environment_names, valid = _named_metadata(environment, separator="=") if present else ((), True)
    if not valid:
        return McpLaunchMetadata(complete=False, reason="malformed_environment")

    headers, present, unambiguous = _metadata_alias(config, _MCP_HEADER_KEYS)
    if not unambiguous:
        return McpLaunchMetadata(environment_names, complete=False, reason="ambiguous_header_metadata")
    header_names, valid = _named_metadata(headers, separator=":") if present else ((), True)
    if not valid:
        return McpLaunchMetadata(environment_names, complete=False, reason="malformed_headers")

    transport_value, present, unambiguous = _metadata_alias(config, _MCP_TRANSPORT_KEYS)
    if not unambiguous:
        return McpLaunchMetadata(environment_names, header_names, complete=False, reason="ambiguous_transport")
    if not present:
        return McpLaunchMetadata(environment_names, header_names)
    if not isinstance(transport_value, str) or not transport_value.strip():
        return McpLaunchMetadata(environment_names, header_names, complete=False, reason="malformed_transport")
    transport = transport_value.strip().casefold().replace("_", "-")
    # Several harnesses use the older `http` spelling for MCP Streamable
    # HTTP. Preserve that input contract while keeping one canonical identity.
    if transport == "http":
        transport = "streamable-http"
    if transport not in VALID_MCP_TRANSPORTS:
        return McpLaunchMetadata(environment_names, header_names, complete=False, reason="unsupported_transport")
    return McpLaunchMetadata(environment_names, header_names, transport)


def _environment_names(environment: Mapping[str, object] | Sequence[str] | None) -> tuple[str, ...]:
    if environment is None:
        return ()
    names = environment.keys() if isinstance(environment, Mapping) else environment
    return tuple(sorted({str(name).split("=", 1)[0] for name in names if str(name).split("=", 1)[0]}))


def _header_names(headers: Mapping[str, object] | Sequence[str] | None) -> tuple[str, ...]:
    if headers is None:
        return ()
    names = headers.keys() if isinstance(headers, Mapping) else headers
    return tuple(sorted({str(name).split(":", 1)[0] for name in names if str(name).split(":", 1)[0]}, key=str.casefold))


def _base_command(command: str) -> str:
    return Path(command).name.casefold()


def _normalize_npm(
    command: str,
    arguments: Sequence[str],
    environment_names: tuple[str, ...],
    header_names: tuple[str, ...],
    transport: str | None,
    selected_binary: str | None,
) -> LaunchNormalizationResult:
    values = list(arguments)
    executable = _base_command(command)
    if executable in {"npx", "npx.cmd"}:
        while values and values[0] in {"-y", "--yes"}:
            values.pop(0)
    else:
        if not values or values.pop(0).casefold() != "exec":
            return _incomplete("unsupported_npm_invocation")
        while values and values[0] != "--":
            option = values.pop(0)
            if not option.startswith("-"):
                return _incomplete("npm_exec_separator_required")
            if option not in _NPM_SAFE_OPTIONS:
                return _incomplete("unsupported_npm_option")
        if not values or values.pop(0) != "--":
            return _incomplete("npm_exec_separator_required")

    if not values:
        return _incomplete("package_required")
    requirement = values.pop(0)
    package_spec = split_npm_package_spec(requirement)
    if package_spec is None:
        return _incomplete("unsupported_npm_requirement")
    package, version = package_spec
    safe_arguments, arguments_safe = redact_arguments(values)
    launch = SanitizedLaunch(
        kind=LaunchKind.NPM,
        package=package,
        binary=selected_binary or package.rsplit("/", 1)[-1],
        requirement=requirement if version is not None else None,
        version=version,
        arguments=safe_arguments,
        environment_names=environment_names,
        header_names=header_names,
        transport=transport,
    )
    return _result(launch, safe=arguments_safe)


def _normalize_uv(
    arguments: Sequence[str],
    environment_names: tuple[str, ...],
    header_names: tuple[str, ...],
    transport: str | None,
    selected_binary: str | None,
) -> LaunchNormalizationResult:
    values = list(arguments)
    if not values:
        return _incomplete("package_required")
    requirement = values.pop(0)
    package_spec = _split_python_requirement(requirement)
    if package_spec is None:
        return _incomplete("unsupported_python_requirement")
    package, canonical_requirement, version_metadata = package_spec
    safe_arguments, arguments_safe = redact_arguments(values)
    launch = SanitizedLaunch(
        kind=LaunchKind.UV,
        package=package,
        binary=selected_binary or package,
        requirement=canonical_requirement,
        version=version_metadata,
        arguments=safe_arguments,
        environment_names=environment_names,
        header_names=header_names,
        transport=transport,
    )
    return _result(launch, safe=arguments_safe)


def _resolve_script(script: str, source_root: Path | None, working_dir: Path | None) -> str | None:
    if source_root is None:
        return None
    root = source_root.expanduser().resolve(strict=False)
    candidate = Path(script).expanduser()
    if not candidate.is_absolute():
        candidate = (working_dir or root) / candidate
    resolved = candidate.resolve(strict=False)
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return None
    return relative.as_posix()


def normalize_mcp_definition(
    config: Mapping[str, object],
    *,
    source_root: Path | None = None,
    working_dir: Path | None = None,
) -> LaunchNormalizationResult:
    """Parse a structured MCP definition for local inventory display only."""

    command_value = config.get("command")
    raw_arguments = config.get("args", [])
    if isinstance(command_value, Sequence) and not isinstance(command_value, (str, bytes)):
        command_parts = list(command_value)
        if not command_parts or any(not isinstance(part, str) for part in command_parts):
            return _incomplete("malformed_command")
        command = command_parts[0]
        arguments: Sequence[str] = command_parts[1:]
    else:
        command = command_value if isinstance(command_value, str) else None
        if not isinstance(raw_arguments, Sequence) or isinstance(raw_arguments, (str, bytes)):
            return _incomplete("malformed_arguments")
        if any(not isinstance(argument, str) for argument in raw_arguments):
            return _incomplete("malformed_arguments")
        arguments = raw_arguments

    url_value = config.get("url", config.get("serverUrl"))
    if url_value is not None and not isinstance(url_value, str):
        return _incomplete("malformed_url")
    metadata = extract_mcp_launch_metadata(config)
    normalized = normalize_launch(
        command=command,
        arguments=arguments,
        url=url_value,
        environment=metadata.environment_names,
        headers=metadata.header_names,
        transport=metadata.transport,
        source_root=source_root,
        working_dir=working_dir,
    )
    if not metadata.complete:
        return LaunchNormalizationResult(launch=normalized.launch, complete=False, reason=metadata.reason)
    return normalized


def normalize_launch(
    *,
    command: str | None = None,
    arguments: Sequence[str] = (),
    url: str | None = None,
    environment: Mapping[str, object] | Sequence[str] | None = None,
    headers: Mapping[str, object] | Sequence[str] | None = None,
    transport: str | None = None,
    selected_binary: str | None = None,
    source_root: Path | None = None,
    working_dir: Path | None = None,
) -> LaunchNormalizationResult:
    """Normalize one structured launch without parsing shell command strings."""

    environment_names = _environment_names(environment)
    header_names = _header_names(headers)

    if url is not None:
        sanitized_url = sanitize_url(url, remove_all_query=True)
        if sanitized_url is None:
            return _incomplete("unsupported_url")
        launch = SanitizedLaunch(
            kind=LaunchKind.URL,
            url=sanitized_url,
            environment_names=environment_names,
            header_names=header_names,
            transport=transport,
        )
        return _result(launch)

    if not command:
        return _incomplete("command_or_url_required")
    if any(character.isspace() for character in command.strip()):
        return _incomplete("shell_command_string_unsupported")

    executable = _base_command(command)
    if executable in _SHELL_WRAPPERS:
        return _incomplete("shell_wrapper_unsupported")
    if executable in {"npx", "npx.cmd", "npm", "npm.cmd"}:
        return _normalize_npm(command, arguments, environment_names, header_names, transport, selected_binary)
    if executable == "uvx":
        return _normalize_uv(arguments, environment_names, header_names, transport, selected_binary)
    if executable == "uv":
        values = list(arguments)
        if len(values) < 2 or values[:2] != ["tool", "run"]:
            return _incomplete("unsupported_uv_invocation")
        return _normalize_uv(values[2:], environment_names, header_names, transport, selected_binary)
    if executable in {"python", "python3", "python.exe", "python3.exe"}:
        values = list(arguments)
        if len(values) < 2 or values[0] != "-m" or not _PYTHON_MODULE_RE.fullmatch(values[1]):
            return _incomplete("unsupported_python_invocation")
        module = values[1]
        safe_arguments, arguments_safe = redact_arguments(values[2:])
        launch = SanitizedLaunch(
            kind=LaunchKind.PYTHON_MODULE,
            module=module,
            binary=executable,
            arguments=safe_arguments,
            environment_names=environment_names,
            header_names=header_names,
            transport=transport,
        )
        return _result(launch, safe=arguments_safe)
    if executable in {"node", "node.exe"}:
        values = list(arguments)
        if not values:
            return _incomplete("script_required")
        script = _resolve_script(values[0], source_root, working_dir)
        if script is None:
            return _incomplete("script_outside_source_root")
        safe_arguments, arguments_safe = redact_arguments(values[1:])
        launch = SanitizedLaunch(
            kind=LaunchKind.NODE,
            script=script,
            binary=executable,
            arguments=safe_arguments,
            environment_names=environment_names,
            header_names=header_names,
            transport=transport,
        )
        return _result(launch, safe=arguments_safe)

    return _incomplete("unknown_executable")
