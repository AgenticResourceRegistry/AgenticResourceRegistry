# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""MCP server CLI commands."""

from __future__ import annotations

import json
import re
import sys
from contextlib import nullcontext, redirect_stdout
from io import StringIO
from pathlib import Path

import typer
from loguru import logger as optic
from packaging.version import InvalidVersion, Version
from rich import print as rprint
from rich.table import Table

from observal_cli import client, config
from observal_cli.analyzer import analyze_local
from observal_cli.cmd_fork import fork_detail_rows
from observal_cli.constants import VALID_HARNESSES, VALID_MCP_CATEGORIES
from observal_cli.errors import ErrorCategory, fail, load_json_object
from observal_cli.prompts import fuzzy_select, select_one, text_input
from observal_cli.render import (
    OutputMode,
    console,
    display_name,
    esc,
    handle,
    ide_tags,
    kv_panel,
    listing_status,
    name_inline,
    output_json,
    relative_time,
    spinner,
    status_badge,
)

mcp_app = typer.Typer(
    help=(
        "MCP server registry commands\n\n"
        "Examples:\n"
        "  observal registry mcp list\n"
        "  observal registry mcp show alice/my-server\n"
        "  observal registry mcp install alice/my-server --harness claude-code"
    )
)


# ── Env var configuration helpers ────────────────────────────


def _parse_env_file(file_path: str) -> list[dict]:
    """Parse a .env-style file and return env var dicts."""
    optic.trace("file_path={}", file_path)
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        fail(
            ErrorCategory.NOT_FOUND,
            "The environment file was not found.",
            operation="Install MCP server",
            resource=str(path),
            remediation="Provide an existing environment file and retry.",
        )

    env_vars: list[dict] = []
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key = line.split("=", 1)[0].strip()
        if key and key == key.upper():
            env_vars.append({"name": key, "description": "", "required": True})
    return env_vars


def _parse_assignments(values: list[str] | None, label: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values or []:
        key, separator, raw = value.partition("=")
        if not separator or not key.strip():
            fail(
                ErrorCategory.VALIDATION,
                f"Invalid {label} assignment.",
                operation="Install MCP server",
                resource=label,
                remediation=f"Provide {label} values as KEY=VALUE.",
            )
        parsed[key.strip()] = raw.strip("\"'")
    return parsed


def _configure_env_vars_interactive(detected: list[dict]) -> list[dict]:
    """Interactive env var configuration at submit time.

    Offers three paths:
      1. Review and edit auto-detected vars
      2. Load from an env file path
      3. Enter manually
    """
    optic.trace("detected={}", detected)
    is_tty = sys.stdin.isatty()

    if detected:
        rprint(f"\n[bold]Auto-detected {len(detected)} env var(s):[/bold]")
        for ev in detected:
            rprint(f"  [cyan]*[/cyan] {ev['name']}")

    rprint("\n[bold]How would you like to configure environment variables?[/bold]")

    if is_tty:
        choices = []
        if detected:
            choices.append("Review auto-detected vars")
        choices.extend(["Load from .env file", "Enter manually", "Skip (no env vars)"])
        choice = select_one("Env var configuration", choices)
    else:
        if detected:
            rprint("  1. Review auto-detected vars")
            rprint("  2. Load from .env file")
            rprint("  3. Enter manually")
            rprint("  4. Skip (no env vars)")
            raw = text_input("Choose", default="1")
        else:
            rprint("  1. Load from .env file")
            rprint("  2. Enter manually")
            rprint("  3. Skip (no env vars)")
            raw = text_input("Choose", default="3")
        choice_map = {
            "1": "Review auto-detected vars" if detected else "Load from .env file",
            "2": "Load from .env file" if detected else "Enter manually",
            "3": "Enter manually" if detected else "Skip (no env vars)",
            "4": "Skip (no env vars)",
        }
        choice = choice_map.get(raw, "Skip (no env vars)")

    if choice == "Skip (no env vars)":
        return []

    if choice == "Load from .env file":
        file_path = text_input("Path to .env file (e.g. .env.example)")
        env_vars = _parse_env_file(file_path)
        if not env_vars:
            rprint("[yellow]No variables found in file.[/yellow]")
            return []
        rprint(f"\n[green]Loaded {len(env_vars)} var(s) from file.[/green]")
        return _review_env_vars(env_vars)

    if choice == "Enter manually":
        return _enter_env_vars_manually()

    # Review auto-detected
    return _review_env_vars(detected)


def _review_env_vars(env_vars: list[dict]) -> list[dict]:
    """Let the developer review, remove, and annotate each env var."""
    optic.trace("env_vars={}", env_vars)
    reviewed: list[dict] = []

    rprint("\n[bold]Review each variable[/bold]\n")

    for ev in env_vars:
        action = text_input(
            f"  {ev['name']} - keep? [Enter=keep / r=remove / o=optional]",
            default="",
        )
        action = action.strip().lower()

        if action == "r":
            rprint("    [dim]removed[/dim]")
            continue

        required = action != "o"
        desc = ev.get("description", "")
        if not desc:
            desc = text_input(f"    Description for {ev['name']} (optional)", default="")

        reviewed.append({"name": ev["name"], "description": desc, "required": required})
        status = "[green]required[/green]" if required else "[yellow]optional[/yellow]"
        rprint(f"    {status}")

    # Offer to add more
    while True:
        add_more = text_input("\n  Add another env var? (name or Enter to finish)", default="")
        if not add_more:
            break
        desc = text_input(f"    Description for {add_more} (optional)", default="")
        req = typer.confirm("    Required?", default=True)
        reviewed.append({"name": add_more.strip().upper(), "description": desc, "required": req})

    return reviewed


def _enter_env_vars_manually() -> list[dict]:
    """Prompt the developer to enter env vars one by one."""
    env_vars: list[dict] = []
    rprint("\n[bold]Enter env vars one at a time[/bold] [dim](empty name to finish)[/dim]\n")

    while True:
        name = text_input("  Variable name (or Enter to finish)", default="")
        if not name:
            break
        name = name.strip().upper()
        desc = text_input(f"    Description for {name} (optional)", default="")
        req = typer.confirm("    Required?", default=True)
        env_vars.append({"name": name, "description": desc, "required": req})

    return env_vars


# ── Dollar-sign variable detection ──────────────────────────

_DOLLAR_VAR_RE = re.compile(r"\$\{?([A-Z][A-Z0-9_]+)\}?")


def _dollar_to_placeholder(value: str) -> str:
    """Replace $VAR / ${VAR} references with <VAR> placeholders.

    Examples:
        "Bearer $TOKEN"           → "Bearer <TOKEN>"
        "Bearer $TOKEN1 $TOKEN2"  → "Bearer <TOKEN1> <TOKEN2>"
        "$API_KEY"                → "<API_KEY>"
    """
    optic.trace("value={}", value)
    return _DOLLAR_VAR_RE.sub(lambda m: f"<{m.group(1)}>", value)


def _extract_dollar_vars(args: list[str], env: dict[str, str]) -> list[str]:
    """Extract unique $VAR / ${VAR} references from args and env values.

    Returns a sorted list of uppercase variable names found in the args list
    and the *values* (not keys) of the env dict, filtered to exclude
    system/infrastructure vars (PATH, HOME, CI_*, etc.).
    """
    optic.trace("args={}, env={}", args, env)
    from observal_cli.analyzer import _is_filtered_env_var

    found: set[str] = set()
    for arg in args:
        found.update(_DOLLAR_VAR_RE.findall(arg))
    for value in env.values():
        if isinstance(value, str):
            found.update(_DOLLAR_VAR_RE.findall(value))
    return sorted(name for name in found if not _is_filtered_env_var(name))


# ── Direct config helpers ────────────────────────────────────


def _unwrap_mcp_config(cfg: dict) -> tuple[dict, str | None]:
    """Unwrap nested mcpServers / named-server wrappers.

    Accepts three shapes:
      1. {"mcpServers": {"name": {config}}}
      2. {"name": {config}}  (single key whose value has command/url/type)
      3. {config}            (bare config with command/args or url)

    Returns (inner_config, server_name | None).
    """
    # Shape 1: wrapped under mcpServers
    optic.trace("cfg={}", cfg)
    if "mcpServers" in cfg and isinstance(cfg["mcpServers"], dict):
        servers = cfg["mcpServers"]
        if len(servers) == 1:
            server_name, inner = next(iter(servers.items()))
            if isinstance(inner, dict):
                return inner, server_name
        return cfg, None

    # Shape 3: bare config - has a direct config key
    if cfg.get("command") or cfg.get("url") or cfg.get("type"):
        return cfg, None

    # Shape 2: single named key wrapping a config dict
    if len(cfg) == 1:
        server_name, inner = next(iter(cfg.items()))
        if isinstance(inner, dict) and (inner.get("command") or inner.get("url") or inner.get("type")):
            return inner, server_name

    return cfg, None


def _parse_server_json_manifest(cfg: dict) -> dict | None:
    """Parse a server.json manifest format (packages[]/remotes[] arrays).

    Also handles the MCP registry format where data is nested under a "server" key:
      {"server": {"name": "...", "remotes": [...]}, "_meta": {...}}

    Returns parsed dict if this looks like a server.json manifest, None otherwise.
    """
    # Handle registry format: unwrap "server" envelope
    optic.trace("cfg={}", cfg)
    manifest = cfg
    server_meta = cfg.get("server")
    if isinstance(server_meta, dict) and ("remotes" in server_meta or "packages" in server_meta):
        manifest = server_meta

    if "packages" not in manifest and "remotes" not in manifest:
        return None

    parsed: dict = {}
    env_vars: list[dict] = []

    # Extract server name/description from registry metadata
    if server_meta and isinstance(server_meta, dict):
        reg_name = server_meta.get("title") or server_meta.get("name")
        if reg_name:
            parsed["_server_name"] = reg_name
        reg_desc = server_meta.get("description")
        if reg_desc:
            parsed["_description"] = reg_desc

    # packages[].runtimeArguments - Docker -e flags
    for pkg in manifest.get("packages", []):
        for arg in pkg.get("runtimeArguments", []):
            value = arg.get("value", "")
            # Pattern: "ENV_VAR={placeholder}" - extract the var name before '='
            if "=" in value:
                var_name = value.split("=", 1)[0]
                if var_name and var_name == var_name.upper():
                    desc = arg.get("description", "")
                    env_vars.append({"name": var_name, "description": desc, "required": True})

    # remotes[].variables - URL-interpolated secrets
    for remote in manifest.get("remotes", []):
        url = remote.get("url", "")
        if url and not parsed.get("url"):
            parsed["url"] = url
            parsed["transport"] = remote.get("type", "sse")
        for var_key, var_meta in (remote.get("variables") or {}).items():
            desc = var_meta.get("description", "") if isinstance(var_meta, dict) else ""
            env_vars.append({"name": var_key, "description": desc, "required": True})

    if env_vars:
        parsed["environment_variables"] = env_vars

    # Determine transport: URL means SSE/HTTP, packages-only means stdio/docker
    if not parsed.get("url"):
        has_remotes = bool(manifest.get("remotes"))
        if not has_remotes:
            # Packages-only manifest implies stdio (Docker typically)
            parsed["transport"] = "stdio"
            parsed["framework"] = "docker"
        # else: remotes without a URL - don't assume transport

    return parsed


def _parse_direct_config(cfg: dict) -> dict:
    """Normalize a JSON config dict into submit-ready fields.

    Accepts:
    - harness config: wrapped (mcpServers) or bare {command, args} / {url, type}
    - server.json manifest: {packages: [...]} / {remotes: [...]}

    Handles two transport shapes:
    - stdio: {command, args, env}
    - SSE/HTTP: {url, type, headers, autoApprove}
    """
    # Try server.json manifest format first
    optic.trace("cfg={}", cfg)
    manifest_result = _parse_server_json_manifest(cfg)
    if manifest_result is not None:
        return manifest_result

    inner, server_name = _unwrap_mcp_config(cfg)
    parsed: dict = {}
    if server_name:
        parsed["_server_name"] = server_name

    if inner.get("url") and not inner.get("command"):
        # SSE / streamable-http transport
        transport = inner.get("type", "sse")
        parsed["transport"] = transport
        parsed["url"] = inner["url"]

        # Convert headers dict {name: value} → list of {name, value, description, required}
        raw_headers = inner.get("headers") or {}
        if isinstance(raw_headers, dict):
            parsed["headers"] = [
                {"name": k, "value": v, "description": "", "required": True} for k, v in raw_headers.items()
            ]
        elif isinstance(raw_headers, list):
            parsed["headers"] = raw_headers

        if inner.get("autoApprove"):
            parsed["auto_approve"] = inner["autoApprove"]

        # env as environment_variables
        raw_env = inner.get("env") or {}
        if isinstance(raw_env, dict):
            parsed["environment_variables"] = [{"name": k, "description": "", "required": True} for k in raw_env]

        # Detect $VAR references in header values and env values
        dollar_vars = _extract_dollar_vars([], {**raw_headers, **raw_env})
        existing_names = {ev["name"] for ev in parsed.get("environment_variables", [])}
        for var_name in dollar_vars:
            if var_name not in existing_names:
                parsed.setdefault("environment_variables", []).append(
                    {"name": var_name, "description": "", "required": True}
                )
                existing_names.add(var_name)
        if dollar_vars:
            parsed["_dollar_vars_detected"] = dollar_vars

    elif inner.get("command"):
        # stdio transport
        parsed["transport"] = "stdio"
        parsed["command"] = inner["command"]
        parsed["args"] = inner.get("args") or []

        # Derive framework from command
        cmd = inner["command"]
        if cmd == "docker":
            parsed["framework"] = "docker"
            # Extract docker_image: last non-flag arg
            args = parsed["args"]
            for arg in reversed(args):
                if not arg.startswith("-"):
                    parsed["docker_image"] = arg
                    break
        elif cmd in ("python", "python3"):
            parsed["framework"] = "python"
        elif cmd in ("npx", "node"):
            parsed["framework"] = "typescript"
        else:
            parsed["framework"] = None

        # env as environment_variables
        raw_env = inner.get("env") or {}
        if isinstance(raw_env, dict):
            parsed["environment_variables"] = [{"name": k, "description": "", "required": True} for k in raw_env]

        # Detect $VAR references in args and env values
        dollar_vars = _extract_dollar_vars(parsed["args"], raw_env)
        existing_names = {ev["name"] for ev in parsed.get("environment_variables", [])}
        for var_name in dollar_vars:
            if var_name not in existing_names:
                parsed.setdefault("environment_variables", []).append(
                    {"name": var_name, "description": "", "required": True}
                )
                existing_names.add(var_name)
        if dollar_vars:
            parsed["_dollar_vars_detected"] = dollar_vars

        if inner.get("autoApprove"):
            parsed["auto_approve"] = inner["autoApprove"]

    return parsed


def _build_config_preview(server_name: str, parsed: dict) -> dict:
    """Build a mcp.json-style preview dict for display during submit."""
    optic.trace("server_name={}, parsed={}", server_name, parsed)
    preview: dict = {}

    if parsed.get("url"):
        # SSE / streamable-http preview
        preview["type"] = parsed.get("transport", "sse")
        preview["url"] = parsed["url"]
        if parsed.get("headers"):
            preview["headers"] = {
                h["name"]: _dollar_to_placeholder(h["value"])
                if _DOLLAR_VAR_RE.search(h.get("value", ""))
                else h.get("value", f"<{h['name']}>")
                for h in parsed["headers"]
            }
        env_vars = parsed.get("environment_variables") or []
        if env_vars:
            preview["env"] = {ev["name"]: f"<{ev['name']}>" for ev in env_vars}
        if parsed.get("auto_approve"):
            preview["autoApprove"] = parsed["auto_approve"]
        preview["disabled"] = False
    else:
        # stdio preview
        command = parsed.get("command", "")
        args = [_dollar_to_placeholder(a) if _DOLLAR_VAR_RE.search(a) else a for a in (parsed.get("args") or [])]

        # Inject -e flags for docker env vars
        env_vars = parsed.get("environment_variables") or []
        if command == "docker" and env_vars:
            # Find the image position (last non-flag arg) and inject -e before it
            insert_idx = len(args)
            for i in range(len(args) - 1, -1, -1):
                if not args[i].startswith("-"):
                    insert_idx = i
                    break
            for ev in reversed(env_vars):
                args.insert(insert_idx, f"{ev['name']}=<{ev['name']}>")
                args.insert(insert_idx, "-e")

        preview["command"] = command
        preview["args"] = args
        if env_vars:
            preview["env"] = {ev["name"]: f"<{ev['name']}>" for ev in env_vars}

    return {server_name: preview}


# ── Implementation functions (shared by canonical + deprecated) ──


def _submit_impl(git_url, name, category, yes, direct_config=False, draft=False, team=None, visibility=None):
    # ── Path B/C: Direct JSON config (no git URL needed) ─────
    optic.trace("git_url={}, name={}", git_url, name)
    if direct_config:
        rprint("[bold]Paste your MCP server JSON config below.[/bold]")
        rprint("[dim]Press Enter on an empty line when done.[/dim]\n")
        lines: list[str] = []
        has_content = False
        while True:
            try:
                line = input()
            except EOFError:
                break
            if line.strip() == "":
                if has_content:
                    break
            else:
                has_content = True
                lines.append(line)
        raw_text = "\n".join(lines).strip()
        if not raw_text:
            fail(
                ErrorCategory.VALIDATION,
                "No MCP configuration was provided.",
                operation="Submit MCP server",
                resource="standard input",
                remediation="Pipe or paste an MCP JSON configuration and retry.",
            )
        try:
            cfg = json.loads(raw_text)
        except json.JSONDecodeError:
            # Long single-line pastes can get split by the terminal, retry without newlines.
            try:
                cfg = json.loads("".join(part.strip() for part in lines))
            except json.JSONDecodeError as error:
                fail(
                    ErrorCategory.VALIDATION,
                    "The MCP configuration is not valid JSON.",
                    operation="Submit MCP server",
                    resource="standard input",
                    remediation="Correct the JSON and retry.",
                    detail=repr(error),
                )

        parsed = _parse_direct_config(cfg)
        _name = name or parsed.pop("_server_name", None) or "my-mcp-server"
        _parsed_desc = parsed.pop("_description", None)

        # Extract dollar-sign input variables before preview
        dollar_vars = parsed.pop("_dollar_vars_detected", None)
        git_analysis: dict = {}
        if git_url:
            with spinner("Checking git repo for local OCI setup..."):
                git_analysis = analyze_local(git_url)
            if git_analysis.get("setup_instructions"):
                rprint("[green]✓[/green] Found local OCI setup instructions from git repo.")
            elif git_analysis.get("error"):
                rprint(f"[yellow]Git analysis skipped:[/yellow] {git_analysis['error']}")

        rprint("\n[bold]Config preview:[/bold]")
        console.print_json(json.dumps(_build_config_preview(_name, parsed), indent=2))

        if dollar_vars:
            placeholders = " ".join(f"<{v}>" for v in dollar_vars)
            rprint(f"\n[bold]The user variables are:[/bold] [cyan]{placeholders}[/cyan]")
            rprint(
                "[dim]These will become install-time prompts - users must supply"
                " values before the server can run.[/dim]"
            )

        if not yes:
            if not typer.confirm("\nSubmit this config?", default=True):
                raise typer.Abort()

            # Let creator review/confirm input dependencies
            if dollar_vars:
                rprint("\n[bold]Confirm input dependencies:[/bold]")
                parsed["environment_variables"] = _review_env_vars(parsed.get("environment_variables", []))

            _name = name or text_input("Server name", default=_name)
            _desc_default = _parsed_desc or ""
            _desc = text_input("Description (what does this server do?)", default=_desc_default or "")
            while not _desc.strip():
                rprint("[yellow]Description is required.[/yellow]")
                _desc = text_input("Description (what does this server do?)")
            _desc = _desc.strip()
            _owner = config.load().get("username", "")
            _category = category or select_one("Category", VALID_MCP_CATEGORIES, default="general")
        else:
            if dollar_vars:
                rprint(f"\n[dim]Auto-detected {len(dollar_vars)} input variable(s) from $VAR patterns.[/dim]")
            _desc = _parsed_desc or _name
            _owner = config.load().get("username", "")
            _category = category or "general"

        supported_harnesses = list(VALID_HARNESSES)
        submit_payload: dict = {
            "name": _name,
            "version": "0.1.0",
            "category": _category,
            "description": _desc,
            "owner": _owner,
            "supported_harnesses": supported_harnesses,
            "environment_variables": parsed.get("environment_variables", []),
        }
        if git_url:
            submit_payload["git_url"] = git_url
        if git_analysis.get("setup_instructions"):
            submit_payload["setup_instructions"] = git_analysis["setup_instructions"]
        if git_analysis.get("docker_image") and not parsed.get("docker_image"):
            submit_payload["docker_image"] = git_analysis["docker_image"]
        if parsed.get("command"):
            submit_payload["command"] = parsed["command"]
        if parsed.get("args") is not None:
            submit_payload["args"] = parsed["args"]
        if parsed.get("url"):
            submit_payload["url"] = parsed["url"]
        if parsed.get("headers"):
            submit_payload["headers"] = parsed["headers"]
        if parsed.get("auto_approve"):
            submit_payload["auto_approve"] = parsed["auto_approve"]
        if parsed.get("transport"):
            submit_payload["transport"] = parsed["transport"]
        if parsed.get("framework"):
            submit_payload["framework"] = parsed["framework"]
        if parsed.get("docker_image"):
            submit_payload["docker_image"] = parsed["docker_image"]
        if git_analysis and not git_analysis.get("error"):
            submit_payload["client_analysis"] = {
                "tools": git_analysis.get("tools", []),
                "issues": git_analysis.get("issues", []),
                "framework": git_analysis.get("framework", ""),
                "entry_point": git_analysis.get("entry_point", ""),
                "command": git_analysis.get("command"),
                "args": git_analysis.get("args"),
                "docker_image": git_analysis.get("docker_image"),
            }

        client.add_publish_target(submit_payload, team, visibility)
        endpoint = "/api/v1/mcps/draft" if draft else "/api/v1/mcps/submit"
        label = "Saving draft..." if draft else "Submitting..."
        with spinner(label):
            result = client.post(endpoint, submit_payload)
        msg = "Draft saved!" if draft else "Submitted!"
        rprint(f"\n[green]{msg}[/green] ID: [bold]{result['id']}[/bold]")
        rprint(f"  Install: [cyan]observal registry mcp install {client.canonical_name(result)}[/cyan]")
        rprint(f"  Status: {status_badge(result.get('status', 'pending'))}")
        return result

    # ── Path A: Git URL analysis ─────────────────────────────
    rprint(
        "\n[yellow]Note:[/yellow] Git analysis is best-effort and not a long-term supported feature."
        "\n      Environment variable detection may not cover all cases - please review"
        "\n      and add any missing variables manually.\n"
    )
    analyzed_locally = False
    with spinner("Analyzing repository..."):
        try:
            prefill = analyze_local(git_url)
            if prefill.get("error"):
                rprint(f"[yellow]Local analysis issue:[/yellow] {prefill['error']}")
                rprint("[dim]Falling back to server-side analysis...[/dim]")
                try:
                    prefill = client.post("/api/v1/mcps/analyze", {"git_url": git_url})
                except SystemExit:
                    rprint("[yellow]Server analysis also failed. Fill in details manually.[/yellow]")
                    prefill = {}
            else:
                analyzed_locally = True
        except (OSError, ValueError, RuntimeError):
            # Local analysis can fail with filesystem/git/parsing errors
            try:
                prefill = client.post("/api/v1/mcps/analyze", {"git_url": git_url})
            except SystemExit:
                rprint("[yellow]Could not analyze repo. Fill in details manually.[/yellow]")
                prefill = {}

    # ── Analysis summary ──────────────────────────────────────
    detected_name = prefill.get("name", "")
    detected_desc = prefill.get("description", "")
    detected_ver = prefill.get("version", "0.1.0")
    detected_framework = prefill.get("framework", "")
    tools = prefill.get("tools", [])

    detected_env_vars = prefill.get("environment_variables", [])
    issues = prefill.get("issues", [])
    error = prefill.get("error", "")

    # Extract command/args/docker fields from analysis
    detected_command = prefill.get("command")
    detected_args = prefill.get("args")
    detected_docker_image = prefill.get("docker_image")
    detected_docker_suggested = prefill.get("docker_image_suggested", False)
    detected_setup = prefill.get("setup_instructions", "")

    rprint("\n[bold]--- Analysis Results ---[/bold]")

    if error:
        rprint(f"  [bold red]Error:[/bold red] {error}")
        rprint("  [dim]You can still submit manually, but the server could not be analyzed.[/dim]")
        if not yes and not typer.confirm("Continue with manual submission?", default=False):
            raise typer.Abort()
    else:
        if detected_name:
            rprint(f"  Server name:  [cyan]{detected_name}[/cyan]")
        if detected_desc:
            rprint(f"  Description:  [dim]{detected_desc[:80]}{'...' if len(detected_desc) > 80 else ''}[/dim]")
        if tools:
            rprint(f"  Tools found:  [green]{len(tools)}[/green]")
            for t in tools[:10]:
                doc = t.get("docstring", t.get("description", ""))
                rprint(f"    [cyan]*[/cyan] {t.get('name', '?')}: {doc[:60] if doc else '[dim](no description)[/dim]'}")
            if len(tools) > 10:
                rprint(f"    [dim]...and {len(tools) - 10} more[/dim]")
        if detected_env_vars:
            rprint(f"  Env vars:     [green]{len(detected_env_vars)}[/green]")
            for ev in detected_env_vars:
                ev_name = ev.get("name", ev) if isinstance(ev, dict) else ev
                rprint(f"    [cyan]*[/cyan] {ev_name}")
        if detected_setup:
            rprint(f"  Setup:        [dim]{detected_setup.splitlines()[0]}[/dim]")
        if not detected_name and not tools:
            rprint("  [dim]No MCP metadata detected. You will need to fill in all fields manually.[/dim]")

        if issues:
            rprint(f"\n  [bold yellow]Warnings ({len(issues)}):[/bold yellow]")
            for issue in issues:
                rprint(f"    [yellow]![/yellow] {issue}")
            rprint()
            if not yes and not typer.confirm("This server has quality issues. Submit anyway?", default=False):
                raise typer.Abort()

    rprint("[bold]------------------------[/bold]\n")

    # ── Auto-accept detected fields, only prompt for missing/required ──
    # MCP servers are harness-agnostic - config generation handles all harnesses.
    supported_harnesses = list(VALID_HARNESSES)

    # Build parsed dict from analysis for config preview
    parsed: dict = {}
    if detected_command:
        parsed["command"] = detected_command
        parsed["args"] = detected_args or []
        parsed["transport"] = "stdio"
        parsed["environment_variables"] = detected_env_vars
        if detected_docker_image:
            parsed["docker_image"] = detected_docker_image

    # Derive framework from command
    _framework: str | None = None
    if detected_command:
        if detected_command == "docker":
            _framework = "docker"
        elif detected_command in ("python", "python3"):
            _framework = "python"
        elif detected_command in ("npx", "node"):
            _framework = "typescript"
        elif detected_framework:
            fw_lower = detected_framework.lower()
            if "typescript" in fw_lower or "ts" in fw_lower:
                _framework = "typescript"
            elif "go" in fw_lower:
                _framework = "go"
            elif "docker" in fw_lower:
                _framework = "docker"
            else:
                _framework = "python"
    elif detected_framework:
        fw_lower = detected_framework.lower()
        if "typescript" in fw_lower or "ts" in fw_lower:
            _framework = "typescript"
        elif "go" in fw_lower:
            _framework = "go"
        elif "docker" in fw_lower:
            _framework = "docker"
        else:
            _framework = "python"
    elif prefill.get("entry_point"):
        _framework = "python"

    # Command/args confirmation
    _command = detected_command
    _args = detected_args
    _docker_image = detected_docker_image

    if yes:
        _name = name or detected_name
        _version = detected_ver
        _desc = detected_desc
        _owner = config.load().get("username", "")
        _category = category or "general"
        if not _framework:
            _framework = "python"
        _setup = detected_setup
        _changelog = "Initial release"
        # Detect $VAR patterns in args and merge into env vars
        dollar_vars = _extract_dollar_vars(_args or [], {})
        existing_names = {(ev.get("name", ev) if isinstance(ev, dict) else ev) for ev in detected_env_vars}
        for var_name in dollar_vars:
            if var_name not in existing_names:
                detected_env_vars.append({"name": var_name, "description": "", "required": True})
                existing_names.add(var_name)
        if dollar_vars:
            rprint(f"\n[dim]Auto-detected {len(dollar_vars)} input variable(s) from $VAR patterns in args.[/dim]")
        env_vars = detected_env_vars
    else:
        # Show config preview if command was detected
        if detected_command:
            preview_name = name or detected_name or "my-server"
            rprint("[bold]Startup config:[/bold]")
            console.print_json(json.dumps(_build_config_preview(preview_name, parsed), indent=2))
            if detected_docker_suggested:
                rprint(
                    f"  [dim](Docker image [cyan]{detected_docker_image}[/cyan]"
                    " was inferred from the GitHub URL - verify it exists)[/dim]"
                )
            choice = (
                text_input(
                    "Startup config looks correct? [Y/n/edit]",
                    default="Y",
                )
                .strip()
                .lower()
            )
            if choice == "n":
                raise typer.Abort()
            elif choice == "edit":
                _command = text_input("Command", default=detected_command or "")
                raw_args = text_input(
                    "Args (space-separated)",
                    default=" ".join(detected_args) if detected_args else "",
                )
                _args = raw_args.split() if raw_args.strip() else []
                # Re-derive framework
                if _command == "docker":
                    _framework = "docker"
                    for arg in reversed(_args):
                        if not arg.startswith("-"):
                            _docker_image = arg
                            break
                elif _command in ("python", "python3"):
                    _framework = "python"
                elif _command in ("npx", "node"):
                    _framework = "typescript"
        elif not detected_command:
            rprint("[dim]No startup command was detected.[/dim]")
            custom_cmd = text_input("Command (e.g. docker, python, npx - Enter to skip)", default="")
            if custom_cmd:
                _command = custom_cmd
                raw_args = text_input("Args (space-separated)", default="")
                _args = raw_args.split() if raw_args.strip() else []
                if _command == "docker":
                    _framework = "docker"
                    for arg in reversed(_args):
                        if not arg.startswith("-"):
                            _docker_image = arg
                            break
                elif _command in ("python", "python3"):
                    _framework = "python"
                elif _command in ("npx", "node"):
                    _framework = "typescript"

        # Name: auto-accept if detected, otherwise ask
        if name:
            _name = name
        elif detected_name:
            _name = detected_name
            rprint(f"  Server name: [cyan]{_name}[/cyan] [dim](from analysis)[/dim]")
        else:
            _name = text_input("Server name")

        # Version: auto-accept detected
        _version = detected_ver
        rprint(f"  Version:     [cyan]{_version}[/cyan]")

        # Description: auto-accept if detected, otherwise ask
        if detected_desc:
            _desc = detected_desc
            rprint(
                f"  Description: [cyan]{_desc[:60]}{'...' if len(_desc) > 60 else ''}[/cyan] [dim](from analysis)[/dim]"
            )
        else:
            _desc = text_input("Description (what does this server do?)")

        _owner = config.load().get("username", "")
        rprint()

        _category = category or select_one("Category", VALID_MCP_CATEGORIES, default="general")

        _setup = text_input("Setup instructions (optional, press Enter to skip)", default=detected_setup)
        _changelog = text_input("Changelog", default="Initial release")

        # Detect $VAR patterns in final args and merge into detected env vars
        dollar_vars = _extract_dollar_vars(_args or [], {})
        existing_names = {(ev.get("name", ev) if isinstance(ev, dict) else ev) for ev in detected_env_vars}
        for var_name in dollar_vars:
            if var_name not in existing_names:
                detected_env_vars.append({"name": var_name, "description": "", "required": True})
                existing_names.add(var_name)
        if dollar_vars:
            rprint("\n[bold yellow]Input variables detected in args:[/bold yellow]")
            rprint(
                "[dim]Dollar-sign variables will become install-time"
                " dependencies - users will be prompted for these values.[/dim]\n"
            )
            for var in dollar_vars:
                rprint(f"  [cyan]$[/cyan]{var}")
            rprint()

        # Interactive env var configuration - developer reviews, edits,
        # or provides env vars instead of blindly including auto-detected ones.
        env_vars = _configure_env_vars_interactive(detected_env_vars)

    submit_payload = {
        "git_url": git_url,
        "name": _name,
        "version": _version,
        "category": _category,
        "description": _desc,
        "owner": _owner,
        "supported_harnesses": supported_harnesses,
        "environment_variables": env_vars,
        "setup_instructions": _setup,
        "changelog": _changelog,
    }
    if _framework:
        submit_payload["framework"] = _framework
    if _docker_image:
        submit_payload["docker_image"] = _docker_image
    if _command:
        submit_payload["command"] = _command
    if _args is not None:
        submit_payload["args"] = _args

    if analyzed_locally:
        submit_payload["client_analysis"] = {
            "tools": prefill.get("tools", []),
            "issues": prefill.get("issues", []),
            "framework": prefill.get("framework", ""),
            "entry_point": prefill.get("entry_point", ""),
            "command": prefill.get("command"),
            "args": prefill.get("args"),
            "docker_image": prefill.get("docker_image"),
            "setup_instructions": prefill.get("setup_instructions"),
        }

    client.add_publish_target(submit_payload, team, visibility)
    endpoint = "/api/v1/mcps/draft" if draft else "/api/v1/mcps/submit"
    label = "Saving draft..." if draft else "Submitting..."
    with spinner(label):
        result = client.post(endpoint, submit_payload)
    msg = "Draft saved!" if draft else "Submitted!"
    rprint(f"\n[green]{msg}[/green] ID: [bold]{result['id']}[/bold]")
    rprint(f"  Install: [cyan]observal registry mcp install {client.canonical_name(result)}[/cyan]")
    if _framework:
        rprint(f"  Framework: [cyan]{_framework}[/cyan]")
    rprint(f"  Status: {status_badge(result.get('status', 'pending'))}")
    return result


def _list_impl(category, search, limit, sort, output, interactive=False, namespace=None, team=None):
    optic.trace("category={}, search={}, namespace={}, team={}", category, search, namespace, team)
    params = {}
    if category:
        params["category"] = category
    if search:
        params["search"] = search
    if namespace:
        params["namespace"] = namespace.lstrip("@").lower()
    if team:
        params["team_id"] = client.resolve_team_id(team)

    fetch_ctx = nullcontext() if output == "json" else spinner("Fetching MCP servers...")
    with fetch_ctx:
        data = client.get("/api/v1/mcps", params=params)

    if not data:
        config.save_last_results([], "mcp")
        if output == "json":
            output_json([])
        else:
            rprint("[dim]No MCP servers found.[/dim]")
        return

    if interactive and output != "json":

        def _display(item: dict) -> str:
            optic.trace("item={}", item)
            return f"{name_inline(item)}  v{item.get('version', '?')}  [{item.get('category', '')}]  {item.get('owner', '')}"

        selected = fuzzy_select(data, _display, label="Select MCP server")
        if selected:
            _show_impl(str(selected["id"]), "table")
        return

    # Sort
    key_map = {"name": "name", "category": "category", "version": "version"}
    sk = key_map.get(sort, "name")
    data = sorted(data, key=lambda x: x.get(sk, ""))[:limit]

    # Cache IDs for numeric shorthand
    config.save_last_results(data, "mcp")

    if output == "json":
        output_json(data)
        return

    table = Table(title=f"MCP Servers ({len(data)})", show_lines=False, padding=(0, 1))
    table.add_column("#", style="dim", width=3)
    table.add_column("Name", style="bold cyan", no_wrap=True)
    table.add_column("Version", style="green")
    table.add_column("Category")
    table.add_column("Namespace", style="dim")
    table.add_column("harnesses")
    table.add_column("ID", style="dim", max_width=12)
    for i, item in enumerate(data, 1):
        table.add_row(
            str(i),
            esc(display_name(item)),
            esc(item.get("version", "")),
            esc(item.get("category", "")),
            esc(handle(item)),
            ide_tags(item.get("supported_harnesses", [])),
            esc(str(item["id"])[:8] + "…"),
        )
    console.print(table)


def _show_impl(mcp_id, output):
    optic.trace("mcp_id={}, output={}", mcp_id, output)
    resolved = client.resolve_registry_reference("mcp", mcp_id)
    fetch_ctx = nullcontext() if output == "json" else spinner()
    with fetch_ctx:
        item = client.get(f"/api/v1/mcps/{resolved}")

    if output == "json":
        output_json(item)
        return

    console.print(
        kv_panel(
            f"{esc(display_name(item))} v{esc(item.get('version', '?'))}",
            [
                ("Status", listing_status(item)),
                ("Category", esc(item.get("category", "N/A"))),
                ("Namespace", esc(handle(item) or "N/A")),
                ("Description", esc(item.get("description", ""))),
                ("harnesses", ide_tags(item.get("supported_harnesses", []))),
                ("Git", esc(item.get("git_url", "N/A"))),
                ("Setup", esc(item.get("setup_instructions") or "none")),
                ("Changelog", esc(item.get("changelog") or "none")),
                ("Created", esc(relative_time(item.get("created_at")))),
                *fork_detail_rows(item),
                ("ID", f"[dim]{esc(item['id'])}[/dim]"),
            ],
            border_style="cyan",
        )
    )

    if item.get("validation_results"):
        rprint("\n[bold]Validation:[/bold]")
        for v in item["validation_results"]:
            icon = "[green]✓[/green]" if v["passed"] else "[red]✗[/red]"
            rprint(f"  {icon} {esc(v['stage'])}: {esc(v.get('details', '') or 'passed')}")


def _install_input_definitions(listing: dict, field: str, kind: str) -> list[dict]:
    """Validate server-provided input metadata before using it in machine workflows."""
    definitions = listing.get(field, [])
    if definitions is None:
        definitions = []
    if not isinstance(definitions, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip()
        for item in definitions
    ):
        fail(
            ErrorCategory.UNAVAILABLE,
            "The server returned invalid MCP installation requirements.",
            operation="Install MCP server",
            resource=listing.get("qualified_name") or listing.get("name") or "MCP server",
            remediation="Check server compatibility and retry.",
            result={"invalid_input_kind": kind},
        )
    return definitions


def _install_impl(
    mcp_id,
    harness,
    raw,
    version=None,
    *,
    env_overrides: dict[str, str] | None = None,
    header_overrides: dict[str, str] | None = None,
    env_file: str | None = None,
    no_prompt: bool = False,
    output: OutputMode = OutputMode.table,
):
    optic.trace("mcp_id={}, harness={}, version={}", mcp_id, harness, version)
    import json as _json

    resolved = client.resolve_registry_reference("mcp", mcp_id)

    machine_output = raw or output == "json"
    fetch_context = nullcontext() if machine_output else spinner("Fetching server details...")
    with fetch_context:
        listing = client.get(f"/api/v1/mcps/{resolved}")
        # Required inputs come from the version that will be installed, not the latest listing.
        spec = client.get(f"/api/v1/mcps/{resolved}/versions/{version}") if version else listing
    env_var_list = _install_input_definitions(spec, "environment_variables", "environment_variable")
    header_list = _install_input_definitions(spec, "headers", "header")

    # Build env overrides from --env flags and --env-file
    _env_from_flags: dict[str, str] = dict(env_overrides) if env_overrides else {}
    if env_file:
        for ev in _parse_env_file(env_file):
            if ev["name"] not in _env_from_flags:
                _env_from_flags[ev["name"]] = ""
        # Re-parse as key=value (env file has names only), read actual values from file
        path = Path(env_file).expanduser().resolve()
        if path.exists():
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, _, v = line.partition("=")
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k:
                        _env_from_flags[k] = v

    _header_from_flags: dict[str, str] = dict(header_overrides) if header_overrides else {}
    skip_prompts = machine_output or no_prompt

    env_values: dict[str, str] = {}
    if output == "json":
        missing_inputs = [
            {"kind": "environment_variable", "name": ev["name"]}
            for ev in env_var_list
            if ev.get("required", True) and not _env_from_flags.get(ev["name"])
        ]
        missing_inputs.extend(
            {"kind": "header", "name": header["name"]}
            for header in header_list
            if header.get("required", True) and not _header_from_flags.get(header["name"])
        )
        if missing_inputs:
            fail(
                ErrorCategory.VALIDATION,
                "MCP installation requires values that are unavailable in non-interactive mode.",
                operation="Install MCP server",
                resource=listing.get("qualified_name") or listing.get("name") or resolved,
                remediation="Provide non-secret values explicitly; for credentials, use interactive table mode.",
                result={"needs_input": True, "inputs": missing_inputs},
            )
    if env_var_list and not skip_prompts:
        required = [ev for ev in env_var_list if ev.get("required", True)]
        optional = [ev for ev in env_var_list if not ev.get("required", True)]

        if required:
            rprint(f"\n[bold]This server requires {len(required)} environment variable(s):[/bold]")
            for ev in required:
                if ev["name"] in _env_from_flags:
                    env_values[ev["name"]] = _env_from_flags[ev["name"]]
                    rprint(f"  [green]✓[/green] {ev['name']} [dim](from --env)[/dim]")
                else:
                    desc = f" [dim]({ev['description']})[/dim]" if ev.get("description") else ""
                    val = text_input(f"  {ev['name']}{desc}")
                    env_values[ev["name"]] = val

        if optional:
            rprint(f"\n[dim]{len(optional)} optional env var(s) available:[/dim]")
            for ev in optional:
                if ev["name"] in _env_from_flags:
                    env_values[ev["name"]] = _env_from_flags[ev["name"]]
                    rprint(f"  [green]✓[/green] {ev['name']} [dim](from --env)[/dim]")
                else:
                    desc = f" [dim]({ev['description']})[/dim]" if ev.get("description") else ""
                    val = text_input(f"  {ev['name']}{desc} (press Enter to skip)", default="")
                    if val:
                        env_values[ev["name"]] = val
    elif env_var_list and skip_prompts:
        # Non-interactive: use --env flag values, placeholders for the rest
        for ev in env_var_list:
            if ev["name"] in _env_from_flags:
                env_values[ev["name"]] = _env_from_flags[ev["name"]]
            else:
                env_values[ev["name"]] = f"<{ev['name']}>"

    # Prompt for headers (SSE/HTTP servers with auth)
    header_values: dict[str, str] = {}
    if header_list and not skip_prompts:
        required_headers = [h for h in header_list if h.get("required", True)]
        optional_headers = [h for h in header_list if not h.get("required", True)]
        if required_headers:
            rprint(f"\n[bold]This server requires {len(required_headers)} header(s):[/bold]")
            for h in required_headers:
                if h["name"] in _header_from_flags:
                    header_values[h["name"]] = _header_from_flags[h["name"]]
                    rprint(f"  [green]✓[/green] {h['name']} [dim](from --header)[/dim]")
                else:
                    desc = f" [dim]({h['description']})[/dim]" if h.get("description") else ""
                    val = text_input(f"  {h['name']}{desc}")
                    header_values[h["name"]] = val
        if optional_headers:
            rprint(f"\n[dim]{len(optional_headers)} optional header(s) available:[/dim]")
            for h in optional_headers:
                if h["name"] in _header_from_flags:
                    header_values[h["name"]] = _header_from_flags[h["name"]]
                    rprint(f"  [green]✓[/green] {h['name']} [dim](from --header)[/dim]")
                else:
                    desc = f" [dim]({h['description']})[/dim]" if h.get("description") else ""
                    val = text_input(f"  {h['name']}{desc} (press Enter to skip)", default="")
                    if val:
                        header_values[h["name"]] = val
    elif header_list and skip_prompts:
        for h in header_list:
            if h["name"] in _header_from_flags:
                header_values[h["name"]] = _header_from_flags[h["name"]]
            else:
                header_values[h["name"]] = f"<{h['name']}>"

    from observal_cli.lockfile import local_registry_name

    local_name = local_registry_name(harness, "mcp", listing["namespace"], listing["slug"])
    generate_context = nullcontext() if machine_output else spinner(f"Generating {harness} config...")
    with generate_context:
        install_body = {
            "harness": harness,
            "local_name": local_name,
            "env_values": env_values,
            "header_values": header_values,
        }
        if version:
            install_body["version"] = version
        result = client.post_public(
            f"/api/v1/mcps/{resolved}/install",
            install_body,
        )

    snippet = result.get("config_snippet", {})
    if raw:
        print(_json.dumps(snippet, indent=2))
        return
    if output == "json":
        output_json(result)
        return

    harness_config_paths = {
        "kiro": ".kiro/settings/mcp.json",
        "cursor": ".cursor/mcp.json",
        "claude-code": "(run the command below)",
        "opencode": ".config/opencode/opencode.json",
        "codex": "~/.codex/config.toml",
    }

    rprint(f"\n[bold]Config for {harness}:[/bold]\n")
    console.print_json(_json.dumps(snippet, indent=2))
    config_path = harness_config_paths.get(harness, "")
    if config_path and not config_path.startswith("("):
        rprint(f"\n[dim]Add to:[/dim] [bold]{config_path}[/bold]")
        rprint(
            f"[dim]Or pipe:[/dim] observal registry mcp install {esc(mcp_id)} "
            f"--harness {esc(harness)} --raw > {esc(config_path)}"
        )

    warnings = result.get("warnings") or []
    for warning in warnings:
        rprint(f"\n[yellow]Warning:[/yellow] {esc(warning)}")

    setup = listing.get("setup_instructions")
    if setup and not any(setup in warning for warning in warnings):
        rprint(f"\n[yellow]Setup required before use:[/yellow]\n{esc(setup)}")

    # Warn about any empty env vars the user skipped
    missing = [k for k, v in env_values.items() if not v or v.startswith("<")]
    if missing:
        rprint(f"\n[yellow]Warning: {len(missing)} env var(s) still need values:[/yellow]")
        for m in missing:
            rprint(f"  [yellow]![/yellow] {m}")
        rprint("[dim]Set these in your harness config or shell environment before running the server.[/dim]")


# ── Canonical commands (on mcp_app) ─────────────────────────


@mcp_app.command()
def submit(
    git_url: str = typer.Option(None, "--git", "-g", help="Optional git repo for local OCI setup detection"),
    name: str = typer.Option(None, "--name", "-n", help="Skip name prompt"),
    category: str = typer.Option(None, "--category", "-c", help="Skip category prompt"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept JSON-derived defaults"),
    config: bool = typer.Option(False, "--config", hidden=True, help="(deprecated) JSON paste is now the default"),
    draft: bool = typer.Option(False, "--draft", help="Save as draft instead of submitting for review"),
    submit_draft: str | None = typer.Option(None, "--submit", help="Submit a draft for review (MCP ID)"),
    team: str | None = typer.Option(None, "--team", help="Teamspace UUID or handle"),
    visibility: str | None = typer.Option(None, "--visibility", help="Visibility: public or team"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Submit an MCP server to the registry.

    Opens a JSON paste prompt where you provide the same config format used
    in your harness (e.g. mcpServers block). Optionally pass --git so Observal
    clones the repo and detects local OCI setup instructions for Dockerfile,
    Containerfile, or compose build MCPs.

    Only submit servers you created or are the point-of-contact for.
    Submissions go into a pending review queue unless saved as a draft.
    You can install your own submissions immediately without approval.

    Environment variables containing $VAR or ${VAR} patterns in args or
    header values are auto-detected and become install-time prompts.

    Examples:
        observal registry mcp submit
        observal registry mcp submit --git https://github.com/org/mcp-server --yes
        observal registry mcp submit --submit my-server --output json
    """
    if draft and submit_draft:
        fail(
            ErrorCategory.VALIDATION,
            "Draft creation and draft submission cannot be requested together.",
            operation="Submit MCP server",
            resource="submit options",
            remediation="Choose either draft creation or draft submission and retry.",
        )
    if output == "json" and not yes and not submit_draft:
        fail(
            ErrorCategory.VALIDATION,
            "JSON mode requires non-interactive submission.",
            operation="Submit MCP server",
            resource="submit options",
            remediation="Pass the defaults-acceptance option and provide MCP JSON on standard input.",
        )
    if submit_draft:
        resolved = client.resolve_registry_reference("mcp", submit_draft)
        submit_context = nullcontext() if output == "json" else spinner("Submitting draft for review...")
        with submit_context:
            result = client.post(f"/api/v1/mcps/{resolved}/submit")
        if output == "json":
            output_json(result)
        else:
            rprint(f"[green]✓ Draft submitted for review![/green] ID: [bold]{result['id']}[/bold]")
        return
    if output != "json":
        if config:
            rprint("[dim]Note: JSON paste is already the default.[/dim]")
        rprint("[dim]Note: Only submit components you created or represent.[/dim]")
    if output == "json":
        with redirect_stdout(StringIO()):
            result = _submit_impl(
                git_url,
                name,
                category,
                yes,
                direct_config=True,
                draft=draft,
                team=team,
                visibility=visibility,
            )
        output_json(result)
        return
    _submit_impl(
        git_url,
        name,
        category,
        yes,
        direct_config=True,
        draft=draft,
        team=team,
        visibility=visibility,
    )


@mcp_app.command(name="list")
def list_mcps(
    category: str | None = typer.Option(None, "--category", "-c", help="Filter by category"),
    search: str | None = typer.Option(None, "--search", "-s", help="Search by name/description"),
    namespace: str | None = typer.Option(None, "--namespace", help="Filter by user or team namespace"),
    team: str | None = typer.Option(None, "--team", help="Only items owned by this teamspace"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Interactive search mode"),
    limit: int = typer.Option(50, "--limit", "-n", min=1, max=200, help="Max results"),
    sort: str = typer.Option("name", "--sort", help="Sort by: name, category, version"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """List approved MCP servers in the registry.

    Shows publicly approved servers by default. Use --search for keyword
    filtering, --category to narrow by type, and --sort to change ordering.
    Results are cached locally so you can reference them by row number in
    subsequent show/install/delete commands.

    Interactive mode (--interactive) opens a fuzzy-search picker and
    displays full details of the selected server.

    Examples:
        observal registry mcp list
        observal registry mcp list --search postgres
        observal registry mcp list --category ai-ml --output json
    """
    if category and category not in VALID_MCP_CATEGORIES:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown MCP category: {category}.",
            operation="List MCP servers",
            resource="category filter",
            remediation=f"Choose one of: {', '.join(VALID_MCP_CATEGORIES)}.",
        )
    if sort not in {"name", "category", "version"}:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown MCP sort field: {sort}.",
            operation="List MCP servers",
            resource="sort option",
            remediation="Choose name, category, or version.",
        )
    _list_impl(category, search, limit, sort, output, interactive=interactive, namespace=namespace, team=team)


@mcp_app.command(name="my")
def mcp_my(
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """List your own MCP servers across all statuses.

    Shows servers you submitted regardless of approval state (pending,
    approved, rejected, draft). Useful for checking submission status
    or finding draft IDs to resume editing.

    Examples:
        # List your servers in a table
        observal registry mcp my

        # JSON output for scripting
        observal registry mcp my --output json
    """
    optic.trace("output={}", output)
    fetch_ctx = nullcontext() if output == "json" else spinner("Fetching your MCPs...")
    with fetch_ctx:
        data = client.get("/api/v1/mcps/my")
    if not data:
        config.save_last_results([], "mcp")
        if output == "json":
            output_json([])
        else:
            rprint("[dim]You have no MCP servers.[/dim]")
        return
    config.save_last_results(data, "mcp")
    if output == "json":
        output_json(data)
        return
    table = Table(title=f"My MCPs ({len(data)})", show_lines=False, padding=(0, 1))
    table.add_column("#", style="dim", width=3)
    table.add_column("Name", style="bold cyan", no_wrap=True)
    table.add_column("Version", style="green")
    table.add_column("Namespace", style="dim")
    table.add_column("Status")
    table.add_column("ID", style="dim", max_width=12)
    for i, item in enumerate(data, 1):
        table.add_row(
            str(i),
            esc(display_name(item)),
            esc(item.get("version", "")),
            esc(handle(item)),
            status_badge(item.get("status", "")),
            esc(str(item["id"])[:8] + "…"),
        )
    console.print(table)


@mcp_app.command()
def show(
    mcp_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output: table, json"),
):
    """Show full details of an MCP server.

    Displays metadata, validation results, supported harnesses, env vars,
    and timestamps for a given server. Accepts a UUID, server name,
    row number from the last list command, or an @alias.

    Examples:
        observal registry mcp show my-server
        observal registry mcp show @fav
        observal registry mcp show my-server --output json
    """
    optic.trace("mcp_id={}, output={}", mcp_id, output)
    _show_impl(mcp_id, output)


@mcp_app.command()
def install(
    mcp_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    harness: str = typer.Option(..., "--harness", "-i", help="Target harness"),
    raw: bool = typer.Option(False, "--raw", help="Output raw JSON only (for piping)"),
    version: str | None = typer.Option(
        None, "--version", "-V", help="Install a specific version (e.g. '2.1.0'). Defaults to latest."
    ),
    env: list[str] | None = typer.Option(None, "--env", "-e", help="Environment variable (KEY=VALUE, repeatable)"),
    header: list[str] | None = typer.Option(None, "--header", help="Header value (KEY=VALUE, repeatable)"),
    env_file: str | None = typer.Option(None, "--env-file", help="Path to .env file for environment variables"),
    no_prompt: bool = typer.Option(False, "--no-prompt", "-y", help="Skip interactive prompts"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Generate an install config snippet for an MCP server.

    Produces harness-specific configuration that you paste into your editor's
    MCP settings file. Prompts for required environment variables and
    headers interactively (unless --raw or --no-prompt is used).

    Use --env KEY=VALUE to pass environment variables non-interactively
    (repeatable). Use --header KEY=VALUE for headers. Use --env-file to
    load values from a .env file.

    The --raw flag outputs bare JSON suitable for piping directly into
    config files, with placeholder values for any missing env vars.

    Examples:
        observal registry mcp install my-server --harness claude-code
        observal registry mcp install my-server --harness claude-code --env-file .env --no-prompt
        observal registry mcp install my-server --harness cursor --raw > .cursor/mcp.json
    """
    optic.trace("mcp_id={}, harness={}", mcp_id, harness)
    if raw and output == "json":
        fail(
            ErrorCategory.VALIDATION,
            "Raw config output and JSON operation output cannot be combined.",
            operation="Install MCP server",
            resource="output options",
            remediation="Choose either raw config output or JSON operation output.",
        )
    if harness not in VALID_HARNESSES:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown harness: {harness}.",
            operation="Install MCP server",
            resource="harness",
            remediation=f"Choose one of: {', '.join(VALID_HARNESSES)}.",
        )
    if version:
        try:
            Version(version)
        except InvalidVersion as error:
            fail(
                ErrorCategory.VALIDATION,
                "The requested MCP version is invalid.",
                operation="Install MCP server",
                resource=version,
                remediation="Provide a valid version and retry.",
                detail=repr(error),
            )
    env_overrides = _parse_assignments(env, "environment variable")
    header_overrides = _parse_assignments(header, "header")
    if output == "json" and not no_prompt:
        fail(
            ErrorCategory.VALIDATION,
            "JSON mode cannot prompt for MCP installation values.",
            operation="Install MCP server",
            resource="MCP installation",
            remediation="Add --no-prompt and provide every required value, or use interactive table mode.",
        )
    _install_impl(
        mcp_id,
        harness,
        raw,
        version=version,
        env_overrides=env_overrides or None,
        header_overrides=header_overrides or None,
        env_file=env_file,
        no_prompt=no_prompt,
        output=output,
    )


@mcp_app.command(name="edit")
def edit_mcp(
    mcp_id: str = typer.Argument(..., help="ID, name, row number, or @alias"),
    from_file: str | None = typer.Option(None, "--from-file", "-f", help="Load updates from JSON file"),
    name: str | None = typer.Option(None, "--name", "-n", help="New listing name"),
    description: str | None = typer.Option(None, "--description", "-d", help="New description"),
    category: str | None = typer.Option(None, "--category", "-c", help="New category"),
    version: str | None = typer.Option(None, "--version", "-v", help="New version string"),
    git_url: str | None = typer.Option(None, "--git-url", help="New git URL"),
    command: str | None = typer.Option(None, "--command", help="New command"),
    url: str | None = typer.Option(None, "--url", help="New URL"),
    bump: str | None = typer.Option(None, "--bump", help="Version bump type: patch, minor, or major (skips prompt)"),
    changelog: str | None = typer.Option(None, "--changelog", help="Changelog text for new version (skips prompt)"),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Edit an MCP server submission.

    For draft, pending, or rejected listings: edits the submission in place.
    For approved listings: publishes a new version with a semver bump
    (you will be prompted to choose patch, minor, or major).

    Without flags, opens an interactive JSON paste prompt (same format as
    submit). You can also pass individual fields via options, or load a
    complete update from a JSON file with --from-file.

    Examples:
        observal registry mcp edit my-server
        observal registry mcp edit my-server -d "New description" -c databases
        observal registry mcp edit my-server --from-file updates.json --output json
    """
    optic.trace("mcp_id={}, from_file={}", mcp_id, from_file)
    resolved = client.resolve_registry_reference("mcp", mcp_id)
    if from_file:
        updates = load_json_object(from_file, operation="Edit MCP server", noun="MCP update file")
    else:
        updates = {}
        if name is not None:
            updates["name"] = name
        if description is not None:
            updates["description"] = description
        if category is not None:
            updates["category"] = category
        if version is not None:
            updates["version"] = version
        if git_url is not None:
            updates["git_url"] = git_url
        if command is not None:
            updates["command"] = command
        if url is not None:
            updates["url"] = url

    if output == "json" and not updates:
        fail(
            ErrorCategory.VALIDATION,
            "JSON mode requires explicit MCP updates.",
            operation="Edit MCP server",
            resource=mcp_id,
            remediation="Provide an update file or one or more field options.",
        )

    if not updates:
        # Interactive JSON paste mode (like submit)
        rprint("[bold]Paste your updated MCP server JSON config below.[/bold]")
        rprint("[dim]Press Enter on an empty line when done.[/dim]\n")
        lines: list[str] = []
        has_content = False
        while True:
            try:
                line = input()
            except EOFError:
                break
            if line.strip() == "":
                if has_content:
                    break
            else:
                has_content = True
                lines.append(line)
        raw_text = "\n".join(lines).strip()
        if not raw_text:
            fail(
                ErrorCategory.VALIDATION,
                "No MCP updates were provided.",
                operation="Edit MCP server",
                resource="standard input",
                remediation="Provide an MCP JSON configuration and retry.",
            )
        try:
            cfg = json.loads(raw_text)
        except json.JSONDecodeError:
            try:
                cfg = json.loads("".join(part.strip() for part in lines))
            except json.JSONDecodeError as error:
                fail(
                    ErrorCategory.VALIDATION,
                    "The MCP update is not valid JSON.",
                    operation="Edit MCP server",
                    resource="standard input",
                    remediation="Correct the JSON and retry.",
                    detail=repr(error),
                )

        parsed = _parse_direct_config(cfg)
        _name = parsed.pop("_server_name", None)
        _desc = parsed.pop("_description", None)
        parsed.pop("_dollar_vars_detected", None)

        # Build updates from parsed config
        if _name:
            updates["name"] = _name
        if _desc:
            updates["description"] = _desc
        if parsed.get("command"):
            updates["command"] = parsed["command"]
        if parsed.get("args") is not None:
            updates["args"] = parsed["args"]
        if parsed.get("url"):
            updates["url"] = parsed["url"]
        if parsed.get("transport"):
            updates["transport"] = parsed["transport"]
        if parsed.get("framework"):
            updates["framework"] = parsed["framework"]
        if parsed.get("environment_variables"):
            updates["environment_variables"] = parsed["environment_variables"]

        rprint("\n[bold]Config preview:[/bold]")
        preview_name = _name or mcp_id
        console.print_json(json.dumps(_build_config_preview(preview_name, parsed), indent=2))

        if not typer.confirm("\nApply these changes?", default=True):
            raise typer.Abort()

    if not updates:
        fail(
            ErrorCategory.VALIDATION,
            "No MCP changes could be parsed.",
            operation="Edit MCP server",
            resource=mcp_id,
            remediation="Provide at least one supported MCP field.",
        )

    status_context = nullcontext() if output == "json" else spinner("Checking listing status...")
    with status_context:
        listing = client.get(f"/api/v1/mcps/{resolved}")

    if listing.get("status") == "approved":
        if bump and bump not in {"patch", "minor", "major"}:
            fail(
                ErrorCategory.VALIDATION,
                f"Unknown version bump: {bump}.",
                operation="Edit MCP server",
                resource="version bump",
                remediation="Choose patch, minor, or major.",
            )
        if output == "json" and not bump:
            fail(
                ErrorCategory.VALIDATION,
                "JSON mode requires an explicit version bump for an approved MCP server.",
                operation="Edit MCP server",
                resource=mcp_id,
                remediation="Provide a patch, minor, or major version bump.",
            )
        current_ver = str(listing.get("version") or "0.1.0")
        try:
            release = Version(current_ver).release
        except InvalidVersion as error:
            fail(
                ErrorCategory.UNAVAILABLE,
                "The registry returned an invalid current MCP version.",
                operation="Edit MCP server",
                resource=mcp_id,
                remediation="Correct the registry version and retry.",
                detail=repr(error),
            )
        major, minor, patch = (*release, 0, 0, 0)[:3]
        bump_type = bump or select_one("Version bump", ["patch", "minor", "major"], default="patch")
        if bump_type == "major":
            new_version = f"{major + 1}.0.0"
        elif bump_type == "minor":
            new_version = f"{major}.{minor + 1}.0"
        else:
            new_version = f"{major}.{minor}.{patch + 1}"
        update_changelog = changelog or ""
        if changelog is None and output != "json":
            update_changelog = text_input("Changelog (what changed?)", default="")
        version_description = updates.pop("description", None) or listing.get("description", "")
        updates.pop("name", None)
        body: dict = {"version": new_version, "description": version_description}
        if updates:
            body["extra"] = updates
        if update_changelog.strip():
            body["changelog"] = update_changelog.strip()
        publish_context = nullcontext() if output == "json" else spinner("Publishing new version...")
        with publish_context:
            result = client.post(f"/api/v1/mcps/{resolved}/versions", body)
        if output == "json":
            output_json(result)
        else:
            rprint(f"[green]✓ Published v{esc(new_version)}[/green] for [bold]{esc(result.get('name', mcp_id))}[/bold]")
        return

    client.post(f"/api/v1/mcps/{resolved}/start-edit")
    save_context = nullcontext() if output == "json" else spinner("Saving changes...")
    with save_context:
        result = client.put(f"/api/v1/mcps/{resolved}/draft", updates)
    if output == "json":
        output_json(result)
    else:
        rprint(f"[green]✓ Updated {esc(result['name'])}[/green] (status: {esc(result.get('status', 'unknown'))})")
