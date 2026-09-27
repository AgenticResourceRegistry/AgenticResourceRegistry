# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import re
from typing import TYPE_CHECKING

from loguru import logger as optic

from models.mcp import McpListing
from services.shared.utils import registry_item_slug
from services.shared.utils import sanitize_name as _sanitize_name

if TYPE_CHECKING:
    from services.harness import McpConfigContext

_SHELL_META_RE = re.compile(r"[|;&`><\n\r]|\$\(|\$\{")
_DANGEROUS_CMD_RE = re.compile(
    r"^(?:curl|wget|bash|sh|zsh|fish|dash|python|perl|ruby|nc|ncat|netcat|powershell|cmd\.exe)$",
    re.IGNORECASE,
)


def validate_mcp_command(command: str, args: list[str] | None = None) -> None:
    """Raise ValueError if command contains shell metacharacters or uses a dangerous program."""
    optic.trace("validating MCP command: {} {}", command, args)
    if not command:
        return
    full = " ".join([command, *list(args or [])])
    if _SHELL_META_RE.search(full):
        raise ValueError("MCP command contains shell metacharacters")
    cmd_base = command.strip().split()[0] if command.strip() else ""
    if _DANGEROUS_CMD_RE.match(cmd_base):
        raise ValueError(f"MCP command uses a disallowed program: {cmd_base!r}")


_DOLLAR_VAR = re.compile(r"\$\{([A-Z][A-Z0-9_]+)\}|\$([A-Z][A-Z0-9_]+)")


def _substitute_dollar_vars(args: list[str], env: dict[str, str] | None) -> list[str]:
    """Replace $VAR and ${VAR} patterns in args with values from env dict."""
    optic.trace("substituting dollar vars in {} args", len(args))
    if not env:
        return list(args)

    def _replacer(m: re.Match) -> str:
        optic.trace("replacing variable: {}", m.group(0))
        var_name = m.group(1) or m.group(2)
        return env.get(var_name, m.group(0))  # keep original if no value

    return [_DOLLAR_VAR.sub(_replacer, arg) for arg in args]


def _build_run_command(
    name: str,
    framework: str | None,
    docker_image: str | None = None,
    server_env: dict[str, str] | None = None,
    stored_command: str | None = None,
    stored_args: list[str] | None = None,
) -> list[str]:
    """Return the appropriate run command based on the MCP framework.

    - Stored command/args: use as-is (set during analysis or by publisher)
    - Docker: docker run -i --rm [-e KEY=VAL ...] <image>
    - TypeScript: npx -y <name>
    - Go: <name> (assumes binary on PATH)
    - Python / unknown: python -m <name>
    """
    optic.trace("building run command for {} (framework={}, docker={})", name, framework, docker_image)
    # Use stored command/args if available, substituting $VAR placeholders
    if stored_command is not None:
        cmd = [stored_command]
        if stored_args:
            cmd.extend(_substitute_dollar_vars(stored_args, server_env))
        return cmd

    # Legacy path: infer from framework/docker_image
    fw = (framework or "").lower()
    if docker_image:
        cmd = ["docker", "run", "-i", "--rm"]
        for k, v in (server_env or {}).items():
            cmd.extend(["-e", f"{k}={v}"])
        cmd.append(docker_image)
        return cmd
    if "typescript" in fw or "ts" in fw:
        return ["npx", "-y", name]
    if "go" in fw:
        return [name]
    return ["python", "-m", name]


def _build_server_env(listing: McpListing, env_values: dict[str, str] | None = None) -> dict[str, str]:
    """Build env dict from the listing's declared environment_variables and user-supplied values."""
    optic.trace("building server env for MCP listing")
    env: dict[str, str] = {}
    for var in listing.environment_variables or []:
        name = var["name"] if isinstance(var, dict) else var.name
        env[name] = (env_values or {}).get(name, "")
    return env


def _build_mcp_context(
    listing: McpListing,
    *,
    env_values: dict[str, str] | None = None,
    header_values: dict[str, str] | None = None,
    local_name: str | None = None,
) -> "McpConfigContext":
    """Normalize a listing before harness-specific formatting."""
    from services.harness import McpConfigContext

    name = _sanitize_name(local_name or registry_item_slug(listing))
    server_env = _build_server_env(listing, env_values)
    transport = (listing.transport or "").lower()
    if transport == "http":  # Legacy listings accepted before submit normalized the alias.
        transport = "streamable-http"
    if listing.url and transport not in ("sse", "streamable-http", ""):
        raise ValueError(f"Unsupported remote MCP transport: {transport!r}")
    url = listing.url if listing.url and transport != "stdio" else None
    run_cmd = (
        []
        if url
        else _build_run_command(
            name,
            listing.framework,
            listing.docker_image,
            server_env,
            stored_command=listing.command,
            stored_args=listing.args,
        )
    )

    return McpConfigContext(
        name=name,
        command=run_cmd[0] if run_cmd else "",
        args=run_cmd[1:],
        server_env=server_env,
        headers=dict(header_values or {}),
        transport=transport or "sse",
        url=url,
        auto_approve=list(listing.auto_approve or []),
    )


def versioned_mcp_listing(listing: McpListing, version: object):
    """Expose a selected MCP version to the existing harness config generator."""

    class SelectedListing:
        def __getattr__(self, field: str):
            if field == "latest_version":
                return version
            if field in {
                "version",
                "command",
                "args",
                "url",
                "transport",
                "framework",
                "docker_image",
                "environment_variables",
                "headers",
                "auto_approve",
                "setup_instructions",
            }:
                return getattr(version, field)
            return getattr(listing, field)

    return SelectedListing()


def generate_config(
    listing: McpListing,
    harness: str,
    observal_url: str = "",
    env_values: dict[str, str] | None = None,
    header_values: dict[str, str] | None = None,
    local_name: str | None = None,
) -> dict:
    """Generate one MCP configuration through the canonical harness adapter."""
    from services.harness import ensure_loaded, get_adapter

    optic.debug("generating MCP config for harness={}", harness)
    ensure_loaded()
    adapter = get_adapter(harness)
    if adapter is None:
        raise ValueError(f"No adapter registered for harness: {harness!r}")
    ctx = _build_mcp_context(
        listing,
        env_values=env_values,
        header_values=header_values,
        local_name=local_name,
    )
    return adapter.format_mcp_config(ctx)
