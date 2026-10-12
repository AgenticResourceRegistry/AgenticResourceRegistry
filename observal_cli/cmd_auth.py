# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Harishankar <harishankar0301@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Santhosh Raja <santhoshpkraja2004@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
# SPDX-FileCopyrightText: 2026 Swathi Saravanan <ss4522@cornell.edu>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-FileCopyrightText: 2026 Riya Rani <rr1182764@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Auth & config CLI commands."""

from __future__ import annotations

import json as _json
import os
import re
import shutil
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Annotated, NoReturn
from urllib.parse import urlparse

import httpx
import typer
from loguru import logger as optic
from rich import print as rprint
from rich.table import Table

from observal_cli import client, config
from observal_cli.branding import welcome_banner
from observal_cli.errors import CliError, ErrorCategory, fail
from observal_cli.prompts import password_input, quick_choice, text_input
from observal_cli.render import OutputMode, console, esc, kv_panel, output_json, spinner, status_badge
from observal_shared.namespace_rules import NAMESPACE_RULE_TEXT, is_valid_namespace
from observal_shared.secrets import resolve_secret

# ── Auth subgroup ───────────────────────────────────────────

auth_app = typer.Typer(
    name="auth",
    help=(
        "Authentication and account commands\n\n"
        "Examples:\n"
        "  observal auth login\n"
        "  observal auth whoami\n"
        "  observal auth logout"
    ),
    no_args_is_help=True,
)

config_app = typer.Typer(
    help=(
        "CLI configuration\n\n"
        "Examples:\n"
        "  observal config show\n"
        "  observal config set server_url https://observal.example.com\n"
        "  observal config aliases --output json"
    )
)


# ── Auth commands (registered on auth_app) ──────────────────


_PASSWORD_REQUIREMENTS = [
    ("At least 12 characters", lambda p: len(p) >= 12),
    ("One uppercase letter", lambda p: bool(re.search(r"[A-Z]", p))),
    ("One number", lambda p: bool(re.search(r"[0-9]", p))),
    ("One special character", lambda p: bool(re.search(r"[^A-Za-z0-9]", p))),
]


# SSO provider -> (public config flag, menu label). The order is the order the
# login menus list them in, so existing menu numbers stay put when a server
# only enables OIDC and SAML.
_SSO_PROVIDERS: dict[str, tuple[str, str]] = {
    "oidc": ("sso_enabled", "OIDC SSO"),
    "google": ("google_sso_enabled", "Google"),
    "github": ("github_sso_enabled", "GitHub"),
    "saml": ("saml_enabled", "SAML SSO"),
}


def _choose_sso_provider(providers: list[str]) -> str:
    """Ask which provider to use when the server enables more than one.

    Without this, a bare --sso goes to the server's OIDC default, which leaves
    people who sign in with Google or GitHub on the web at an IdP where they
    have no account.
    """
    for index, key in enumerate(providers, start=1):
        rprint(f"  [{index}] {_SSO_PROVIDERS[key][1]}")
    choice = quick_choice("SSO provider", [str(i) for i in range(1, len(providers) + 1)])
    return providers[int(choice) - 1]


def _validate_password(password: str) -> list[str]:
    """Return list of unmet requirement descriptions, empty if valid."""
    return [label for label, check in _PASSWORD_REQUIREMENTS if not check(password)]


def _prompt_password(prompt_text: str = "New password") -> str:
    """Prompt for a password, show requirements, retry until valid."""
    optic.trace("prompt_text={}", prompt_text)
    rprint("\n[dim]Password requirements:[/dim]")
    for label, _ in _PASSWORD_REQUIREMENTS:
        rprint(f"  [dim]· {label}[/dim]")

    while True:
        pw = password_input(prompt_text)
        failed = _validate_password(pw)
        if not failed:
            return pw
        rprint("\n[yellow]Password does not meet requirements:[/yellow]")
        for f in failed:
            rprint(f"  [red]✗[/red] {f}")


def _is_json(output: OutputMode | str) -> bool:
    return output == OutputMode.json or output == "json"


def _json_line(data: dict) -> None:
    print(_json.dumps(data, ensure_ascii=False))


def _secret(name: str, *, operation: str) -> str | None:
    try:
        return resolve_secret(name)
    except ValueError as exc:
        fail(
            ErrorCategory.VALIDATION,
            f"{name} is configured incorrectly.",
            operation=operation,
            resource=name,
            remediation=f"Set only {name} or {name}_FILE, then retry.",
            detail=repr(exc),
        )


def _fail_transport(error: httpx.TransportError, *, operation: str, resource: str) -> NoReturn:
    if isinstance(error, httpx.TimeoutException):
        client._handle_timeout(operation=operation, resource=resource, detail=repr(error))
    client._handle_connect(operation=operation, resource=resource, detail=repr(error))
    raise AssertionError("transport handlers must terminate")


def _raise_for_status(response: httpx.Response, *, path: str, operation: str, resource: str) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        client._handle_error(error, path, operation=operation, resource=resource)


def _save_login(server_url: str, data: dict) -> dict:
    try:
        user = data["user"]
        cfg_data = {
            "server_url": server_url,
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "user_id": user.get("id", ""),
            "user_name": user.get("name", ""),
            "username": user.get("username", ""),
        }
    except (KeyError, TypeError, AttributeError) as exc:
        fail(
            ErrorCategory.UNEXPECTED,
            "The server returned an invalid authentication response.",
            operation="Save authenticated session",
            resource=f"server {server_url}",
            remediation="Check server health and version compatibility, then retry.",
            detail=repr(exc),
        )

    endpoints = _fetch_endpoints(server_url)
    if endpoints:
        cfg_data["web_url"] = endpoints.get("web", "")
    previous = config.load_persisted()
    if previous.get("server_url") != server_url or str(previous.get("user_id", "")) != str(cfg_data["user_id"]):
        # The hooks token belongs to the previous login; hooks prefer it, so every upload would be refused.
        config.remove("api_key")
    config.save(cfg_data)
    return user


def _login_result(server_url: str, user: dict, *, method: str, bootstrapped: bool) -> dict:
    return {
        "authenticated": True,
        "server_url": server_url,
        "method": method,
        "bootstrapped": bootstrapped,
        "user": {
            "id": user.get("id", ""),
            "name": user.get("name", ""),
            "email": user.get("email", ""),
            "role": user.get("role", ""),
            "username": user.get("username", ""),
        },
    }


def _finish_login(
    server_url: str,
    data: dict,
    *,
    output: OutputMode | str,
    method: str,
    bootstrapped: bool = False,
    run_setup: bool = True,
    stream: bool = False,
) -> None:
    user = _save_login(server_url, data)
    result = _login_result(server_url, user, method=method, bootstrapped=bootstrapped)
    if _is_json(output):
        if stream:
            _json_line({"event": "authenticated", **result})
        else:
            output_json(result)
        return

    rprint(f"[green]Logged in as {esc(user.get('name', 'unknown'))}[/green] ({esc(user.get('email', ''))})")
    rprint(f"[dim]Config saved to {config.CONFIG_FILE}[/dim]")
    if run_setup:
        _post_login_setup()


def _ensure_cli_matches_server(server_url: str) -> None:
    """Block login when the CLI does not exactly match the server version.

    The server is the source of truth. CLI pings the server to get its
    version, then requires an exact match. Shows the correct upgrade or
    downgrade command so the user knows exactly what to run.
    """
    from packaging.version import InvalidVersion, Version

    from observal_cli.version_check import get_current_version

    cli_ver_str = get_current_version()
    if cli_ver_str == "0.0.0":
        return

    try:
        r = httpx.get(f"{server_url}/api/v1/config/version", timeout=10)
        r.raise_for_status()
        server_ver = r.json().get("server_version")
    except Exception:
        return

    if not server_ver or server_ver == "dev":
        return

    try:
        cli_version = Version(cli_ver_str)
        server_version = Version(server_ver)
    except InvalidVersion:
        return

    if cli_version == server_version:
        return

    from observal_cli.install_detector import upgrade_command

    if cli_version > server_version:
        install_command = f"observal self downgrade --version {server_ver}"
    else:
        install_command = upgrade_command(server_ver)
    fail(
        ErrorCategory.VERSION,
        f"CLI version {cli_ver_str} does not match server version {server_ver}.",
        operation="Authenticate with Observal",
        resource=f"server {server_url}",
        remediation=f"Run {install_command} and retry login.",
    )


@auth_app.command()
def login(
    server: str = typer.Option(None, "--server", "-s", help="Server URL"),
    email: str = typer.Option(None, "--email", "-e", help="Email or username"),
    password: str = typer.Option(None, "--password", "-p", help="Password"),
    name: str = typer.Option(None, "--name", "-n", help="Your name (used for admin setup)"),
    sso: bool = typer.Option(
        False, "--sso", help="Authenticate via browser SSO; asks which provider when the server enables several"
    ),
    saml: bool = typer.Option(False, "--saml", help="Authenticate via browser SAML SSO"),
    output: Annotated[
        OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
    ] = OutputMode.table,
    no_setup: Annotated[bool, typer.Option("--no-setup", help="Skip post-login skill installation and doctor")] = False,
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="SSO provider: oidc, saml, google or github (implies --sso)"),
    ] = None,
):
    """Connect to Observal.

    Human mode asks for the server URL; leave it blank for http://localhost.
    On a fresh server, creates the first admin account. On an initialized
    server, authenticates with credentials or browser SSO. Set
    OBSERVAL_PASSWORD or OBSERVAL_PASSWORD_FILE to avoid exposing a password
    in shell history. JSON credential login requires complete inputs and never
    prompts. JSON SSO emits JSON Lines authorization and completion events.

    Examples:
        observal auth login
        observal auth login --server https://observal.example.com --email alice --output json --no-setup
        observal auth login --sso --provider google
    """
    json_mode = _is_json(output)
    if not json_mode:
        welcome_banner()

    from observal_cli.lockfile import migrate_lockfile_v1

    stored = config.load()
    previous_server = stored.get("server_url")
    if previous_server:
        migrate_lockfile_v1(str(previous_server))

    if server:
        server_url = server.rstrip("/")
    elif json_mode:
        server_url = str(stored.get("server_url") or "http://localhost:80").rstrip("/")
    else:
        server_url = (
            text_input("Server URL (leave blank for http://localhost)", default="") or "http://localhost"
        ).rstrip("/")

    candidates = [server_url]
    parsed_server = urlparse(server_url)
    if (
        not server
        and parsed_server.hostname in {"localhost", "127.0.0.1", "::1"}
        and parsed_server.port
        not in {
            None,
            80,
            443,
        }
    ):
        fallback_host = f"[{parsed_server.hostname}]" if ":" in parsed_server.hostname else parsed_server.hostname
        candidates.append(f"{parsed_server.scheme or 'http'}://{fallback_host}")

    last_transport_error: httpx.TransportError | None = None
    with nullcontext() if json_mode else spinner("Connecting..."):
        for candidate in candidates:
            resource = f"server {candidate}"
            try:
                response = httpx.get(f"{candidate}/health", timeout=10)
            except httpx.TransportError as error:
                last_transport_error = error
                continue
            except Exception as error:
                fail(
                    ErrorCategory.UNEXPECTED,
                    "The server health check failed unexpectedly.",
                    operation="Check server before login",
                    resource=resource,
                    remediation="Retry with debug output and inspect server health if the failure persists.",
                    detail=repr(error),
                )
            _raise_for_status(
                response,
                path="/health",
                operation="Check server before login",
                resource=resource,
            )
            try:
                health_data = response.json()
            except (ValueError, TypeError, AttributeError) as error:
                fail(
                    ErrorCategory.UNEXPECTED,
                    "The server returned an invalid health response.",
                    operation="Check server before login",
                    resource=resource,
                    remediation="Check server health and version compatibility, then retry.",
                    detail=repr(error),
                )
            if not isinstance(health_data, dict):
                fail(
                    ErrorCategory.UNEXPECTED,
                    "The server returned an invalid health response.",
                    operation="Check server before login",
                    resource=resource,
                    remediation="Check server health and version compatibility, then retry.",
                )
            server_url = candidate
            break
        else:
            assert last_transport_error is not None
            _fail_transport(
                last_transport_error,
                operation="Check server before login",
                resource=f"server {candidates[-1]}",
            )

    _ensure_cli_matches_server(server_url)
    initialized = health_data.get("initialized", True)
    run_setup = not no_setup and not json_mode
    supplied_password = password or _secret("OBSERVAL_PASSWORD", operation="Authenticate with Observal")

    if not initialized:
        if json_mode:
            if not email or not name or not supplied_password:
                fail(
                    ErrorCategory.VALIDATION,
                    "Fresh-server JSON login requires email, name, and a password.",
                    operation="Initialize Observal administrator",
                    resource=resource,
                    remediation=(
                        "Provide email and name, and set OBSERVAL_PASSWORD or OBSERVAL_PASSWORD_FILE, then retry."
                    ),
                )
            admin_email, admin_name, admin_password = email, name, supplied_password
        else:
            rprint("[green]Connected.[/green] No users yet; setting up the administrator account.")
            admin_email = email or text_input("Admin email")
            admin_name = name or text_input("Admin name", default="admin")
            admin_password = supplied_password or _prompt_password("Admin password")
            if supplied_password is None:
                confirm = password_input("Confirm password")
                if admin_password != confirm:
                    fail(
                        ErrorCategory.VALIDATION,
                        "Passwords do not match.",
                        operation="Initialize Observal administrator",
                        resource="administrator password",
                        remediation="Enter matching passwords and retry.",
                    )

        failed = _validate_password(admin_password)
        if failed:
            fail(
                ErrorCategory.VALIDATION,
                "The administrator password does not meet security requirements.",
                operation="Initialize Observal administrator",
                resource="administrator password",
                remediation="Use at least 12 characters with uppercase, number, and special characters.",
            )

        try:
            with nullcontext() if json_mode else spinner("Creating admin account..."):
                response = httpx.post(
                    f"{server_url}/api/v1/auth/init",
                    json={"email": admin_email, "name": admin_name, "password": admin_password},
                    timeout=30,
                )
            if response.status_code == 400 and "already initialized" in response.text.lower():
                fail(
                    ErrorCategory.CONFLICT,
                    "The server was initialized by another user before this request completed.",
                    operation="Initialize Observal administrator",
                    resource=resource,
                    remediation="Retry login with an existing account.",
                    http_status=400,
                )
            _raise_for_status(
                response,
                path="/api/v1/auth/init",
                operation="Initialize Observal administrator",
                resource=resource,
            )
            data = response.json()
        except CliError:
            raise
        except httpx.TransportError as error:
            _fail_transport(error, operation="Initialize Observal administrator", resource=resource)
        except (ValueError, TypeError) as error:
            fail(
                ErrorCategory.UNEXPECTED,
                "The server returned an invalid initialization response.",
                operation="Initialize Observal administrator",
                resource=resource,
                remediation="Check server health and version compatibility, then retry.",
                detail=repr(error),
            )
        _finish_login(
            server_url,
            data,
            output=output,
            method="bootstrap",
            bootstrapped=True,
            run_setup=run_setup,
        )
        return

    if not json_mode:
        rprint("[green]Connected.[/green]")

    if saml and provider and provider.lower() != "saml":
        fail(
            ErrorCategory.VALIDATION,
            f"--saml and --provider {provider} name different providers.",
            operation="Authenticate with SSO",
            resource=resource,
            remediation="Pass either --saml or --provider, not both.",
        )
    sso_provider: str | None = "saml" if saml else (provider.lower() if provider else None)
    if sso_provider is not None and sso_provider not in _SSO_PROVIDERS:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown SSO provider: {provider}.",
            operation="Authenticate with SSO",
            resource=resource,
            remediation=f"Use one of: {', '.join(_SSO_PROVIDERS)}.",
        )
    sso_mode = bool(sso or sso_provider)
    direct_sso = sso_mode
    sso_only = False
    enabled_providers: list[str] = []
    try:
        public_response = httpx.get(f"{server_url}/api/v1/config/public", timeout=5)
        if public_response.status_code == 200:
            public = public_response.json()
            sso_only = bool(public.get("sso_only"))
            enabled_providers = [key for key, (flag, _label) in _SSO_PROVIDERS.items() if public.get(flag)]
            if sso_provider and sso_provider not in enabled_providers:
                label = _SSO_PROVIDERS[sso_provider][1]
                fail(
                    ErrorCategory.VALIDATION,
                    f"{label} is not configured on this server.",
                    operation=f"Authenticate with {label}",
                    resource=resource,
                    remediation=f"Use an enabled authentication method or ask an administrator to configure {label}.",
                )
            if sso_only:
                sso_mode = True
                direct_sso = True
    except CliError:
        raise
    except (httpx.HTTPError, ValueError, TypeError):
        pass

    # A bare --sso (or an SSO-only server) names no provider. With one enabled,
    # use it; with several, ask in a terminal. JSON mode and non-interactive
    # stdin never prompt, so they keep the server's default (OIDC, then SAML)
    # for existing scripts.
    if direct_sso and sso_provider is None:
        if len(enabled_providers) == 1:
            sso_provider = enabled_providers[0]
        elif len(enabled_providers) > 1 and not json_mode and sys.stdin.isatty():
            sso_provider = _choose_sso_provider(enabled_providers)

    if json_mode and not (sso or saml or provider) and not (email and supplied_password):
        fail(
            ErrorCategory.VALIDATION,
            "JSON login requires complete credentials or an explicit SSO option.",
            operation="Authenticate with Observal",
            resource=resource,
            remediation=("Provide email and OBSERVAL_PASSWORD, or select SSO or SAML, then retry."),
        )

    if not json_mode and not sso_mode and not (email or supplied_password):
        rprint("  [1] CLI email/username + password")
        rprint("  [2] Web sign-in")
        for index, key in enumerate(enabled_providers, start=3):
            rprint(f"  [{index}] {_SSO_PROVIDERS[key][1]}")
        choice = quick_choice("Login method", [str(i) for i in range(1, 3 + len(enabled_providers))])
        if choice == "2":
            sso_mode = True
        elif choice != "1":
            sso_mode = True
            direct_sso = True
            sso_provider = enabled_providers[int(choice) - 3]

    if sso_mode:
        _do_device_flow_login(
            server_url,
            direct_sso=direct_sso,
            provider=sso_provider,
            output=output,
            run_setup=run_setup,
        )
        return

    login_email = email or text_input("Email or username")
    login_password = supplied_password or password_input("Password")
    _do_password_login(
        server_url,
        login_email,
        login_password,
        output=output,
        run_setup=run_setup,
    )


@auth_app.command()
def logout(
    output: Annotated[
        OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
    ] = OutputMode.table,
):
    """Clear saved credentials.

    Revokes the remote session when possible, then removes local access and
    refresh tokens. Remote revocation failure never blocks local cleanup.

    Examples:
        observal auth logout
        observal auth logout --output json
    """
    existed = config.CONFIG_FILE.exists()
    attempted = False
    revoked: bool | None = None
    if existed:
        try:
            raw_cfg = _json.loads(config.CONFIG_FILE.read_text())
        except (OSError, _json.JSONDecodeError) as error:
            fail(
                ErrorCategory.VALIDATION,
                "The local authentication configuration cannot be read.",
                operation="Log out of Observal",
                resource=str(config.CONFIG_FILE),
                remediation="Repair or remove the configuration file, then retry.",
                detail=repr(error),
            )

        access_token = raw_cfg.get("access_token")
        refresh_token = raw_cfg.get("refresh_token")
        server_url = raw_cfg.get("server_url", "").rstrip("/")
        if access_token and server_url:
            attempted = True
            try:
                response = httpx.post(
                    f"{server_url}/api/v1/auth/logout",
                    json={"refresh_token": refresh_token or None},
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=5,
                )
                revoked = response.is_success
            except httpx.HTTPError:
                revoked = False
        config.remove("access_token", "refresh_token", "api_key")

    result = {
        "logged_out": True,
        "config_existed": existed,
        "local_tokens_cleared": True,
        "remote_revocation_attempted": attempted,
        "remote_revoked": revoked,
    }
    if _is_json(output):
        output_json(result)
        return

    rprint("[green]Logged out.[/green]" if existed else "[dim]No config to clear.[/dim]")
    if revoked is False:
        rprint("[yellow]The remote session could not be revoked, but local credentials were removed.[/yellow]")
    if existed:
        rprint(
            "[dim]Harness hooks will stop sending telemetry. "
            "Run [bold]observal doctor cleanup[/bold] to remove managed hooks.[/dim]"
        )


@auth_app.command()
def whoami(
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Show current authenticated user.

    Queries the server for the user associated with the stored access token.

    Examples:
        observal auth whoami
        observal auth whoami --output json
    """
    config.get_or_exit()
    with nullcontext() if _is_json(output) else spinner("Checking..."):
        user = client.get("/api/v1/auth/whoami")
    if _is_json(output):
        output_json(user)
        return
    console.print(
        kv_panel(
            esc(user["name"]),
            [
                ("Username", f"@{esc(user['username'])}" if user.get("username") else "[dim]not set[/dim]"),
                ("Email", esc(user["email"])),
                ("Role", status_badge(esc(user.get("role", "user")))),
                ("ID", f"[dim]{esc(user['id'])}[/dim]"),
            ],
        )
    )


@auth_app.command()
def status(
    output: Annotated[
        OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
    ] = OutputMode.table,
):
    """Check authenticated server connectivity and local outbox health.

    Returns authentication code 3 when credentials are absent and unavailable
    code 9 when the configured server cannot be reached.

    Examples:
        observal auth status
        observal auth status --output json
    """
    cfg = config.load()
    url = str(cfg.get("server_url") or "").rstrip("/")
    if not url or not cfg.get("access_token"):
        fail(
            ErrorCategory.AUTH,
            "Observal authentication is not configured.",
            operation="Check authentication status",
            resource=str(config.CONFIG_FILE),
            remediation="Run observal auth login or configure an access token, then retry.",
        )

    ok, latency = client.health()
    if not ok:
        fail(
            ErrorCategory.UNAVAILABLE,
            "The configured Observal server is unreachable.",
            operation="Check authentication status",
            resource=f"server {url}",
            remediation="Check the server URL and service health, then retry.",
        )

    outbox: dict[str, object]
    try:
        from observal_cli.telemetry_buffer import stats as buffer_stats

        buf = buffer_stats()
        outbox = {
            "available": True,
            "total": buf.get("total", 0),
            "pending": buf.get("pending", 0),
            "bytes": buf.get("bytes", 0),
            "oldest_pending": buf.get("oldest_pending"),
        }
    except Exception as error:
        outbox = {"available": False, "error": type(error).__name__}

    result = {
        "server_url": url,
        "authenticated": True,
        "health": {"reachable": True, "latency_ms": round(latency, 3)},
        "outbox": outbox,
    }
    if _is_json(output):
        output_json(result)
        return

    rprint(f"  Server:  {esc(url)}")
    rprint("  Auth:    [green]configured[/green]")
    color = "green" if latency < 200 else "yellow" if latency < 1000 else "red"
    rprint(f"  Health:  [{color}]ok[/{color}] ({latency:.0f}ms)")
    if outbox["available"] and outbox["total"]:
        pending = int(outbox["pending"])
        label = f"[yellow]{pending} pending[/yellow]" if pending else "[green]0 pending[/green]"
        rprint()
        rprint(f"  Outbox:  {label} batches, {int(outbox['bytes']) / 1024:.1f} KiB")
        if outbox["oldest_pending"]:
            rprint(f"  Oldest:  {esc(outbox['oldest_pending'])} UTC")
    elif not outbox["available"]:
        rprint("  Outbox:  [yellow]status unavailable[/yellow]")


@auth_app.command(name="change-password")
def change_password(
    output: Annotated[
        OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
    ] = OutputMode.table,
):
    """Change your password.

    Both modes read OBSERVAL_CURRENT_PASSWORD and OBSERVAL_NEW_PASSWORD,
    including their FILE forms. Human mode prompts for missing values; JSON
    mode requires both values and never prompts.

    Examples:
        observal auth change-password
        observal auth change-password --output json
    """
    cfg = config.load()
    if not cfg.get("server_url") or not cfg.get("access_token"):
        fail(
            ErrorCategory.AUTH,
            "An authenticated session is required to change the password.",
            operation="Change password",
            resource=str(config.CONFIG_FILE),
            remediation="Run observal auth login and retry.",
        )

    json_mode = _is_json(output)
    current = _secret("OBSERVAL_CURRENT_PASSWORD", operation="Change password")
    new_password = _secret("OBSERVAL_NEW_PASSWORD", operation="Change password")
    if json_mode and (not current or not new_password):
        fail(
            ErrorCategory.VALIDATION,
            "JSON password change requires current and new password secrets.",
            operation="Change password",
            resource="password input",
            remediation=("Set OBSERVAL_CURRENT_PASSWORD and OBSERVAL_NEW_PASSWORD, or their FILE forms, then retry."),
        )

    current = current or password_input("Current password")
    if new_password is None:
        new_password = _prompt_password("New password")
        confirmation = password_input("Confirm password")
        if new_password != confirmation:
            fail(
                ErrorCategory.VALIDATION,
                "Passwords do not match.",
                operation="Change password",
                resource="new password",
                remediation="Enter matching passwords and retry.",
            )

    if _validate_password(new_password):
        fail(
            ErrorCategory.VALIDATION,
            "The new password does not meet security requirements.",
            operation="Change password",
            resource="new password",
            remediation="Use at least 12 characters with uppercase, number, and special characters.",
        )

    with nullcontext() if json_mode else spinner("Changing password..."):
        result = client.put(
            "/api/v1/auth/profile/password",
            {"current_password": current, "new_password": new_password},
        )
    payload = result or {"changed": True}
    if _is_json(output):
        output_json(payload)
    else:
        rprint("[green]Password changed successfully.[/green]")


@auth_app.command(name="set-username")
def set_username(
    username: str = typer.Argument(..., help="Username (3-32 chars, lowercase alphanumeric, hyphens and dots)"),
    output: Annotated[
        OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
    ] = OutputMode.table,
):
    """Set or update your username.

    Examples:
        observal auth set-username alice
        observal auth set-username my-dev-handle --output json
        observal auth set-username my.dev.handle
    """
    optic.trace("username={}", username)
    if username != username.strip().lower() or not is_valid_namespace(username):
        fail(
            ErrorCategory.VALIDATION,
            NAMESPACE_RULE_TEXT,
            operation="Update username",
            resource="username",
            remediation="Choose a valid registry namespace and retry.",
        )

    with nullcontext() if _is_json(output) else spinner("Updating username..."):
        result = client.put("/api/v1/auth/profile/username", {"username": username})
    config.save({"username": result.get("username", username)})
    if _is_json(output):
        output_json(result)
    else:
        rprint(f"[green]Username set to @{esc(result.get('username', username))}[/green]")


def version_callback():
    """Show CLI version."""
    from importlib.metadata import version as pkg_version

    try:
        v = pkg_version("observal-cli")
    except Exception:
        v = "dev"
    rprint(f"observal [bold]{v}[/bold]")


# ── Helper functions ────────────────────────────────────────


def _fetch_endpoints(server_url: str) -> dict:
    """Fetch service endpoint URLs from the discovery endpoint.

    Returns a dict with api, web URLs.
    Falls back to sensible defaults if the endpoint is unavailable.
    """
    optic.trace("server_url={}", server_url)
    try:
        r = httpx.get(f"{server_url.rstrip('/')}/api/v1/config/endpoints", timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


def _do_password_login(
    server_url: str,
    email: str,
    password: str,
    *,
    output: OutputMode | str = OutputMode.table,
    run_setup: bool = True,
) -> None:
    """Authenticate with email or username and password."""
    optic.trace("server_url={}, email={}", server_url, email)
    json_mode = _is_json(output)
    resource = f"server {server_url}"
    try:
        with nullcontext() if json_mode else spinner("Authenticating..."):
            response = httpx.post(
                f"{server_url}/api/v1/auth/login",
                json={"email": email, "password": password},
                timeout=30,
            )
        _raise_for_status(
            response,
            path="/api/v1/auth/login",
            operation="Authenticate with password",
            resource=resource,
        )
        data = response.json()
    except CliError:
        raise
    except httpx.TransportError as error:
        _fail_transport(error, operation="Authenticate with password", resource=resource)
    except (ValueError, TypeError) as error:
        fail(
            ErrorCategory.UNEXPECTED,
            "The server returned an invalid login response.",
            operation="Authenticate with password",
            resource=resource,
            remediation="Check server health and version compatibility, then retry.",
            detail=repr(error),
        )

    if data.get("must_change_password"):
        if not json_mode:
            rprint("[yellow]Your administrator requires a password change.[/yellow]")
        new_password = _secret("OBSERVAL_NEW_PASSWORD", operation="Complete required password change")
        if new_password is None:
            if json_mode:
                fail(
                    ErrorCategory.VALIDATION,
                    "JSON login requires a new password for the mandatory password change.",
                    operation="Complete required password change",
                    resource="new password",
                    remediation="Set OBSERVAL_NEW_PASSWORD or OBSERVAL_NEW_PASSWORD_FILE, then retry.",
                )
            new_password = _prompt_password("New password")
            confirmation = password_input("Confirm new password")
            if new_password != confirmation:
                fail(
                    ErrorCategory.VALIDATION,
                    "Passwords do not match.",
                    operation="Complete required password change",
                    resource="new password",
                    remediation="Enter matching passwords and retry.",
                )
        if _validate_password(new_password):
            fail(
                ErrorCategory.VALIDATION,
                "The new password does not meet security requirements.",
                operation="Complete required password change",
                resource="new password",
                remediation="Use at least 12 characters with uppercase, number, and special characters.",
            )
        try:
            with nullcontext() if json_mode else spinner("Changing password..."):
                changed = httpx.put(
                    f"{server_url}/api/v1/auth/profile/password",
                    json={"current_password": password, "new_password": new_password},
                    headers={"Authorization": f"Bearer {data['access_token']}"},
                    timeout=30,
                )
            _raise_for_status(
                changed,
                path="/api/v1/auth/profile/password",
                operation="Complete required password change",
                resource="user account",
            )
        except CliError:
            raise
        except httpx.TransportError as error:
            _fail_transport(error, operation="Complete required password change", resource=resource)
        if not json_mode:
            rprint("[green]Password changed.[/green]")

    _finish_login(
        server_url,
        data,
        output=output,
        method="password",
        run_setup=run_setup,
    )


def _do_device_flow_login(
    server_url: str,
    direct_sso: bool = False,
    provider: str | None = None,
    *,
    output: OutputMode | str = OutputMode.table,
    run_setup: bool = True,
) -> None:
    """Authenticate via browser using the device authorization flow."""
    optic.trace("server_url={}", server_url)
    import time
    import webbrowser

    json_mode = _is_json(output)
    resource = f"server {server_url}"
    try:
        with nullcontext() if json_mode else spinner("Requesting device authorization..."):
            response = httpx.post(
                f"{server_url}/api/v1/auth/device/authorize",
                json={"sso": direct_sso, "provider": provider},
                timeout=10,
            )
        _raise_for_status(
            response,
            path="/api/v1/auth/device/authorize",
            operation="Request device authorization",
            resource=resource,
        )
        data = response.json()
        device_code = data["device_code"]
        user_code = data["user_code"]
        verification_uri = data["verification_uri"]
        verification_uri_complete = data["verification_uri_complete"]
        expires_in = data["expires_in"]
        interval = data.get("interval", 5)
    except CliError:
        raise
    except httpx.TransportError as error:
        _fail_transport(error, operation="Request device authorization", resource=resource)
    except (KeyError, ValueError, TypeError) as error:
        fail(
            ErrorCategory.UNEXPECTED,
            "The server returned an invalid device authorization response.",
            operation="Request device authorization",
            resource=resource,
            remediation="Check server health and version compatibility, then retry.",
            detail=repr(error),
        )

    parsed_verification = urlparse(verification_uri)
    parsed_server = urlparse(server_url)
    if parsed_verification.hostname in ("localhost", "127.0.0.1", "::1") and parsed_server.hostname not in (
        "localhost",
        "127.0.0.1",
        "::1",
        None,
    ):
        base = f"{parsed_server.scheme}://{parsed_server.netloc}"
        path = parsed_verification.path or "/device"
        verification_uri = f"{base}{path}"
        original_query = urlparse(data.get("verification_uri_complete", "")).query
        verification_uri_complete = f"{base}{path}?{original_query}" if original_query else f"{base}{path}"
        optic.debug("rewrote localhost verification_uri to {}", verification_uri)

    if json_mode:
        _json_line(
            {
                "event": "authorization_required",
                "verification_uri": verification_uri,
                "verification_uri_complete": verification_uri_complete,
                "user_code": user_code,
                "expires_in": expires_in,
                "interval": interval,
            }
        )
    else:
        rprint()
        rprint("[bold]To sign in, open this URL in your browser:[/bold]")
        rprint()
        rprint(f"  [link={verification_uri_complete}]{verification_uri}[/link]")
        rprint()
        rprint(f"  Then enter code: [bold cyan]{user_code}[/bold cyan]")
        rprint()

    try:
        import platform
        import subprocess as _sp

        opened = False
        system = platform.system()
        if system == "Darwin":
            _sp.Popen(["open", verification_uri_complete], stderr=_sp.DEVNULL, stdout=_sp.DEVNULL)
            opened = True
        elif system == "Linux":
            try:
                wsl_ok = _sp.run(["wslpath", "-w", "/"], capture_output=True).returncode == 0
            except (OSError, ValueError):
                wsl_ok = False
            if wsl_ok:
                _sp.Popen(
                    ["powershell.exe", "-NoProfile", "-c", f"Start-Process '{verification_uri_complete}'"],
                    stderr=_sp.DEVNULL,
                    stdout=_sp.DEVNULL,
                )
            else:
                _sp.Popen(["xdg-open", verification_uri_complete], stderr=_sp.DEVNULL, stdout=_sp.DEVNULL)
            opened = True
        else:
            webbrowser.open(verification_uri_complete)
            opened = True
        if opened and not json_mode:
            rprint("[dim]Browser opened automatically.[/dim]")
    except Exception:
        if not json_mode:
            rprint("[dim]Could not open browser automatically. Please open the URL manually.[/dim]")

    if not json_mode:
        rprint()
        rprint("[dim]Waiting for authorization...[/dim]", end="")

    deadline = time.monotonic() + expires_in
    while time.monotonic() < deadline:
        time.sleep(interval)
        try:
            response = httpx.post(
                f"{server_url}/api/v1/auth/device/token",
                json={
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                timeout=10,
            )
        except httpx.RequestError:
            if not json_mode:
                rprint(".", end="", flush=True)
            continue

        if response.status_code == 200:
            try:
                token_data = response.json()
            except ValueError as error:
                fail(
                    ErrorCategory.UNEXPECTED,
                    "The server returned an invalid device token response.",
                    operation="Complete device authorization",
                    resource=resource,
                    remediation="Check server health and version compatibility, then retry.",
                    detail=repr(error),
                )
            if not json_mode:
                rprint(" [green]authorized![/green]")
            _finish_login(
                server_url,
                token_data,
                output=output,
                method=provider or "sso",
                run_setup=run_setup,
                stream=json_mode,
            )
            return

        if response.status_code == 428:
            if not json_mode:
                rprint(".", end="", flush=True)
            continue

        try:
            error = response.json().get("error", "unknown_error")
        except ValueError:
            error = "unknown_error"
        if error == "expired_token":
            fail(
                ErrorCategory.AUTH,
                "The device authorization code expired.",
                operation="Complete device authorization",
                resource="device authorization",
                remediation="Start login again to request a new code.",
                http_status=response.status_code,
            )
        if error == "access_denied":
            fail(
                ErrorCategory.PERMISSION,
                "Device authorization was denied.",
                operation="Complete device authorization",
                resource="device authorization",
                remediation="Approve the browser authorization request and retry.",
                http_status=response.status_code,
            )
        _raise_for_status(
            response,
            path="/api/v1/auth/device/token",
            operation="Complete device authorization",
            resource=resource,
        )

    fail(
        ErrorCategory.UNAVAILABLE,
        "Device authorization timed out.",
        operation="Complete device authorization",
        resource="device authorization",
        remediation="Start login again and complete browser authorization before the code expires.",
    )


_CONFIG_KEYS = {"server_url", "timeout", "update_check", "update_check_interval", "update_check_repo"}
_ALIAS_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


def _parse_bool(value: str, *, key: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    fail(
        ErrorCategory.VALIDATION,
        f"{key} must be true or false.",
        operation="Update CLI configuration",
        resource=key,
        remediation=f"Run observal config set {key} true or {key} false.",
    )


def _normalize_config_value(key: str, value: str) -> object:
    if key not in _CONFIG_KEYS:
        fail(
            ErrorCategory.VALIDATION,
            f"{key} is not a user-configurable setting.",
            operation="Update CLI configuration",
            resource=key,
            remediation=f"Choose one of: {', '.join(sorted(_CONFIG_KEYS))}.",
        )

    normalized = value.strip()
    if key == "server_url":
        parsed = urlparse(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            fail(
                ErrorCategory.VALIDATION,
                "server_url must be an HTTP or HTTPS URL without embedded credentials.",
                operation="Update CLI configuration",
                resource=key,
                remediation="Provide a URL such as https://observal.example.com.",
            )
        return normalized.rstrip("/")
    if key in {"timeout", "update_check_interval"}:
        try:
            number = int(normalized)
        except ValueError:
            number = 0
        minimum = 1 if key == "timeout" else 60
        if number < minimum:
            fail(
                ErrorCategory.VALIDATION,
                f"{key} must be an integer of at least {minimum}.",
                operation="Update CLI configuration",
                resource=key,
                remediation=f"Provide an integer of at least {minimum} and retry.",
            )
        return number
    if key == "update_check":
        return _parse_bool(normalized, key=key)
    if key == "update_check_repo":
        if normalized and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", normalized):
            fail(
                ErrorCategory.VALIDATION,
                "update_check_repo must use owner/repository format.",
                operation="Update CLI configuration",
                resource=key,
                remediation="Provide a value such as Observal/Observal or an empty string.",
            )
        return normalized
    return normalized


def _safe_config(cfg: dict) -> dict:
    visible = {
        key: cfg.get(key)
        for key in (
            "server_url",
            "timeout",
            "update_check",
            "update_check_interval",
            "update_check_repo",
            "user_id",
            "user_name",
            "username",
            "web_url",
        )
        if key in cfg
    }
    visible["access_token_configured"] = bool(cfg.get("access_token"))
    visible["refresh_token_configured"] = bool(cfg.get("refresh_token"))
    visible["hooks_token_configured"] = bool(cfg.get("api_key"))
    return visible


def _validate_alias_name(name: str) -> None:
    if not _ALIAS_PATTERN.fullmatch(name):
        fail(
            ErrorCategory.VALIDATION,
            "Alias names must start with a letter and contain only letters, numbers, dots, underscores, or hyphens.",
            operation="Update CLI alias",
            resource=name,
            remediation="Choose an alias of at most 64 characters without spaces or a leading at sign.",
        )


def register_config(app: typer.Typer):
    """Register config subcommands."""

    @config_app.command(name="show")
    def config_show(
        output: Annotated[
            OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
        ] = OutputMode.table,
    ):
        """Show effective CLI configuration without exposing credentials.

        Examples:
            observal config show
            observal config show --output json
        """
        safe = _safe_config(config.load())
        if _is_json(output):
            output_json(safe)
            return

        table = Table(title="CLI Configuration", show_lines=False)
        table.add_column("Setting", style="bold")
        table.add_column("Value")
        for key, value in safe.items():
            rendered = str(value).lower() if isinstance(value, bool) else "" if value is None else str(value)
            table.add_row(key, esc(rendered))
        console.print(table)

    @config_app.command(name="set")
    def config_set(
        key: str = typer.Argument(..., help="Config key"),
        value: str = typer.Argument(..., help="Config value"),
        output: Annotated[
            OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
        ] = OutputMode.table,
    ):
        """Set a validated user-managed CLI setting.

        Examples:
            observal config set server_url https://observal.example.com
            observal config set timeout 60 --output json
            observal config set update_check false
        """
        optic.trace("key={}", key)
        normalized = _normalize_config_value(key, value)
        if key == "server_url":
            from observal_cli.lockfile import LOCKFILE_PATH, migrate_lockfile_v1

            previous_server = config.load_persisted().get("server_url")
            if previous_server and previous_server != normalized:
                try:
                    migrate_lockfile_v1(str(previous_server))
                except (RuntimeError, ValueError) as error:
                    fail(
                        ErrorCategory.VALIDATION,
                        "The installed-state lockfile could not be migrated.",
                        operation="Update CLI server",
                        resource=str(LOCKFILE_PATH),
                        remediation="Repair or remove the invalid lockfile, then retry.",
                        detail=repr(error),
                    )
        config.save({key: normalized})
        effective = config.load().get(key)
        result = {"key": key, "value": normalized, "persisted": True, "effective": effective}
        if _is_json(output):
            output_json(result)
        else:
            rprint(f"[green]Set {esc(key)}.[/green]")

    @config_app.command(name="path")
    def config_path(
        output: Annotated[
            OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
        ] = OutputMode.table,
    ):
        """Show the config file path.

        Examples:
            observal config path
            observal config path --output json
        """
        result = {"path": str(config.CONFIG_FILE), "exists": config.CONFIG_FILE.exists()}
        if _is_json(output):
            output_json(result)
        else:
            print(result["path"])

    @config_app.command(name="alias")
    def config_alias(
        name: str = typer.Argument(..., help="Alias name used as @name"),
        target: str | None = typer.Argument(None, help="Target reference; omit to remove"),
        output: Annotated[
            OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
        ] = OutputMode.table,
    ):
        """Set or remove a local registry reference alias.

        Examples:
            observal config alias reviewer alice/reviewer
            observal config alias reviewer alice/reviewer --output json
            observal config alias reviewer
        """
        optic.trace("name={}, has_target={}", name, target is not None)
        _validate_alias_name(name)
        aliases = config.load_aliases()
        if target is not None:
            normalized_target = target.strip()
            if not normalized_target:
                fail(
                    ErrorCategory.VALIDATION,
                    "Alias targets must not be empty.",
                    operation="Update CLI alias",
                    resource=name,
                    remediation="Provide a UUID, namespace/slug, name, or another supported reference.",
                )
            changed = aliases.get(name) != normalized_target
            aliases[name] = normalized_target
            if changed:
                config.save_aliases(aliases)
            result = {"action": "set", "alias": name, "target": normalized_target, "changed": changed}
            message = f"@{esc(name)} → {esc(normalized_target)}"
        else:
            removed = aliases.pop(name, None)
            changed = removed is not None
            if changed:
                config.save_aliases(aliases)
            result = {"action": "removed", "alias": name, "target": removed, "changed": changed}
            message = f"Removed @{esc(name)}" if changed else f"Alias @{esc(name)} was already absent"

        if _is_json(output):
            output_json(result)
        else:
            color = "green" if changed else "yellow"
            rprint(f"[{color}]{message}[/{color}]")

    @config_app.command(name="aliases")
    def config_aliases(
        output: Annotated[
            OutputMode, typer.Option("--output", "-o", help="Output format: table or json")
        ] = OutputMode.table,
    ):
        """List all local aliases.

        Examples:
            observal config aliases
            observal config aliases --output json
        """
        items = [{"alias": name, "target": target} for name, target in sorted(config.load_aliases().items())]
        if _is_json(output):
            output_json({"items": items, "total": len(items)})
            return
        if not items:
            rprint("[dim]No aliases set. Use: observal config alias <name> <reference>[/dim]")
            return

        table = Table(title="CLI Aliases", show_lines=False)
        table.add_column("Alias", style="bold")
        table.add_column("Target")
        for item in items:
            table.add_row(f"@{esc(item['alias'])}", esc(item["target"]))
        console.print(table)

    app.add_typer(config_app, name="config")


def _post_login_setup():
    """Post-login setup: install skills and the Pi extension unconditionally, then run doctor."""
    _install_observal_skill()
    _install_or_check_pi_extension()
    _generate_initial_layer_snapshot()
    rprint()
    try:
        from unittest.mock import MagicMock

        from observal_cli.cmd_doctor import doctor

        # Call doctor inline so stdin prompts work naturally.
        # Pass a fake ctx with invoked_subcommand=None so it runs the check logic.
        ctx = MagicMock()
        ctx.invoked_subcommand = None
        doctor(ctx=ctx, yes=False)
    except (SystemExit, typer.Exit, typer.Abort):
        pass  # Normal exit from doctor
    except Exception as e:
        rprint(f"[yellow]Could not run doctor: {e}[/yellow]")
        rprint("  Run [bold]observal doctor[/bold] manually to configure your harnesses.")


def _post_auth_onboarding():
    """Detect local harness configs and show what was found."""
    try:
        _ide_dirs = {
            "Claude Code": (Path.home() / ".claude", "claude-code"),
            "Kiro CLI": (Path.home() / ".kiro", "kiro"),
            "Cursor": (Path.home() / ".cursor", "cursor"),
            "Codex": (Path.home() / ".codex", "codex"),
            "Copilot": (Path.home() / ".vscode", "copilot"),
            "OpenCode": (Path.home() / ".config" / "opencode", "opencode"),
            "Pi": (Path.home() / ".pi" / "agent", "pi"),
        }

        found: list[tuple[str, str, int, int]] = []  # (label, ide_key, agents, mcps)
        for label, (dir_path, ide_key) in _ide_dirs.items():
            if not dir_path.is_dir():
                continue
            agents = mcps = 0
            try:
                from observal_cli.harness import NotSupportedError, ensure_loaded, get_adapter

                ensure_loaded()
                adapter = get_adapter(ide_key)
                result = adapter.scan_home(dir_path.parent)
                agents = len(result.agents)
                mcps = len(result.mcps)
            except (KeyError, NotSupportedError):
                pass
            if agents > 0 or mcps > 0:
                found.append((label, ide_key, agents, mcps))

        if not found:
            return

        rprint()
        rprint("[bold]\N{ELECTRIC LIGHT BULB} Detected local harness configs.[/bold]")
        rprint()
        for label, _key, agents, mcps in found:
            parts = []
            if agents:
                parts.append(f"{agents} agent{'s' if agents != 1 else ''}")
            if mcps:
                parts.append(f"{mcps} MCP{'s' if mcps != 1 else ''}")
            rprint(f"  [bold]{label}[/bold] - {', '.join(parts)} found")
        rprint()
        rprint("[dim]Run `observal doctor patch --all-harnesses` to instrument telemetry.[/dim]")

    except Exception:
        pass


def _generate_initial_layer_snapshot():
    """Generate ~/.observal/layer_snapshot.json scanning all detected harnesses.

    Runs once after login to establish the initial baseline of the user's
    harness configuration state. Silent on failure.
    """
    try:
        from observal_cli.layer import ensure_local_snapshot

        ensure_local_snapshot()
    except Exception:
        pass  # Never block login on snapshot failure


def _install_observal_skill():
    """Install the bundled Observal skills to all detected harness skill directories."""
    from observal_cli.skill_installer import install_observal_skill

    install_observal_skill()


def _install_or_check_pi_extension():
    """Install or refresh the bundled Pi telemetry extension when Pi is detected.

    Runs unconditionally (no prompt), mirroring skill install. An npm-configured
    install is left untouched (only reported if stale). Never blocks login -
    failures are printed and swallowed.
    """
    try:
        from observal_cli import pi_extension

        status = pi_extension.check_status()
        if status.state == pi_extension.NOT_DETECTED:
            return
        if status.action == "dedupe":
            # Deleting a file mid-login is too surprising; doctor patch does it.
            rprint(f"[yellow]{esc(status.message)}[/yellow]")
        elif status.action in ("install", "refresh", "migrate", "restore"):
            result = pi_extension.install_or_refresh(dry_run=False, status=status)
            verb = {"install": "Installed", "refresh": "Updated", "migrate": "Migrated", "restore": "Restored"}[
                status.action
            ]
            rprint(f"[green]✓ {verb} the Pi telemetry extension.[/green]")
            if result.backup is not None:
                rprint(f"  [dim]Kept the previous file at {esc(result.backup)}.[/dim]")
            if status.action != "install":
                rprint("  [dim]Restart Pi or run /reload to activate.[/dim]")
        elif status.action == "adopt":
            pi_extension.install_or_refresh(dry_run=False, status=status)
        elif status.message:
            rprint(f"[yellow]{esc(status.message)}[/yellow]")
    except Exception as exc:
        rprint(f"[yellow]Could not check the Pi telemetry extension: {exc}[/yellow]")
        rprint("  Run [bold]observal doctor[/bold] manually to check it.")


def _run_doctor_patch(ide_name: str):
    """Run 'observal doctor patch --harness <name>' as a subprocess."""
    optic.trace("ide_name={}", ide_name)
    import subprocess
    import sys

    try:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run(
            [sys.executable, "-m", "observal_cli.main", "doctor", "patch", "--harness", ide_name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=env,
        )
        if result.stdout:
            rprint(result.stdout.rstrip())
        if result.returncode != 0 and result.stderr:
            rprint(f"[yellow]{result.stderr.rstrip()}[/yellow]")
    except Exception as e:
        rprint(f"[yellow]Could not run doctor patch: {e}[/yellow]")
        rprint(f"Run [bold]observal doctor patch --harness {ide_name}[/bold] manually.")


def _configure_cursor(server_url: str):
    """Check for Cursor (harness or CLI) and offer to configure its telemetry hooks."""
    optic.trace("server_url={}", server_url)
    cursor_dir = Path.home() / ".cursor"

    try:
        cursor_exists = cursor_dir.is_dir() or shutil.which("cursor")
        if not cursor_exists:
            return

        if not typer.confirm(
            "\nDetected Cursor. Configure telemetry -> Observal?",
            default=True,
        ):
            return

        _run_doctor_patch("cursor")

    except Exception as e:
        rprint(f"\n[yellow]Could not configure Cursor automatically: {e}[/yellow]")
        rprint("Run [bold]observal doctor patch --harness cursor[/bold] to set up manually.")


def _configure_kiro(server_url: str):
    """Check for Kiro CLI and offer to configure its telemetry hooks."""
    optic.trace("server_url={}", server_url)
    kiro_dir = Path.home() / ".kiro"

    try:
        kiro_exists = kiro_dir.is_dir() or shutil.which("kiro-cli") or shutil.which("kiro")
        if not kiro_exists:
            return

        if not typer.confirm(
            "\nDetected Kiro CLI. Configure telemetry -> Observal?",
            default=True,
        ):
            return

        _run_doctor_patch("kiro")

    except Exception as e:
        rprint(f"\n[yellow]Could not configure Kiro automatically: {e}[/yellow]")
        rprint("Run [bold]observal doctor patch --harness kiro[/bold] to set up manually.")


def _configure_codex(server_url: str):
    """Check for Codex CLI and configure telemetry via doctor patch."""
    optic.trace("server_url={}", server_url)
    codex_dir = Path.home() / ".codex"

    try:
        codex_exists = codex_dir.is_dir() or shutil.which("codex")
        if not codex_exists:
            return

        if not typer.confirm(
            "\nDetected Codex CLI. Configure telemetry -> Observal?",
            default=True,
        ):
            return

        _run_doctor_patch("codex")

    except Exception as e:
        rprint(f"\n[yellow]Could not configure Codex automatically: {e}[/yellow]")
        rprint("Run [bold]observal doctor patch --harness codex[/bold] manually.")


def _configure_copilot(server_url: str):
    """Check for GitHub Copilot (VS Code) and configure telemetry via doctor patch."""
    optic.trace("server_url={}", server_url)
    try:
        vscode_dir = Path.home() / ".vscode"
        if not vscode_dir.is_dir():
            return

        # Check for an actual Copilot extension rather than just VS Code existing.
        extensions_dir = vscode_dir / "extensions"
        has_copilot = extensions_dir.is_dir() and any(
            p.name.startswith("github.copilot") for p in extensions_dir.iterdir()
        )
        if not has_copilot:
            return

        if not typer.confirm(
            "\nDetected GitHub Copilot. Configure telemetry -> Observal?",
            default=True,
        ):
            return

        _run_doctor_patch("copilot")

    except Exception:
        pass


def _configure_copilot_cli(server_url: str):
    """Check for Copilot CLI and configure telemetry via doctor patch."""
    optic.trace("server_url={}", server_url)
    try:
        # The copilot binary is the definitive signal.
        # ~/.copilot/config.json can be created by a previous observal doctor patch,
        # so its presence alone doesn't mean Copilot CLI is actually installed.
        if not shutil.which("copilot"):
            return

        if not typer.confirm(
            "\nDetected Copilot CLI. Configure telemetry -> Observal?",
            default=True,
        ):
            return

        _run_doctor_patch("copilot-cli")

    except Exception:
        pass


def _configure_opencode(server_url: str):
    """Check for OpenCode and configure telemetry via doctor patch."""
    optic.trace("server_url={}", server_url)
    try:
        # The opencode binary is the strongest signal. The official installer
        # commonly places it at ~/.opencode/bin/opencode without adding it to PATH.
        # ~/.config/opencode/opencode.json can be created by a previous Observal
        # doctor patch, so accept config only when a binary is present.
        opencode_bin = Path.home() / ".opencode" / "bin" / "opencode"
        if not shutil.which("opencode") and not opencode_bin.exists():
            return

        if not typer.confirm(
            "\nDetected OpenCode. Configure telemetry -> Observal?",
            default=True,
        ):
            return

        _run_doctor_patch("opencode")

    except Exception:
        pass


def _configure_claude_code(server_url: str, access_token: str):
    """Check for Claude Code and configure telemetry via doctor patch.

    Fetches a long-lived hooks token first (needed by the patch command),
    then delegates to 'observal doctor patch --harness claude-code'.
    """
    optic.trace("server_url={}", server_url)
    claude_dir = Path.home() / ".claude"

    try:
        claude_exists = claude_dir.is_dir() or shutil.which("claude")
        if not claude_exists:
            return

        if not typer.confirm(
            "\nDetected Claude Code. Configure telemetry -> Observal?",
            default=True,
        ):
            return

        # Fetch a long-lived hooks token and save to config before patching
        hooks_token = _fetch_hooks_token(server_url, access_token)
        if hooks_token:
            cfg = config.load()
            cfg["api_key"] = hooks_token
            config.save(cfg)

        _run_doctor_patch("claude-code")

    except Exception as e:
        rprint(f"\n[yellow]Could not configure Claude Code automatically: {e}[/yellow]")
        rprint("Run [bold]observal doctor patch --harness claude-code[/bold] manually.")


def _fetch_hooks_token(server_url: str, access_token: str) -> str:
    """Call /auth/hooks-token to get a long-lived token for telemetry hooks.

    Falls back to the session access_token if the endpoint fails.
    """
    optic.trace("server_url={}", server_url)
    try:
        r = httpx.post(
            f"{server_url.rstrip('/')}/api/v1/auth/hooks-token",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("access_token", access_token)
    except Exception:
        pass
    return access_token
