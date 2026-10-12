# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import logging
import time
import uuid
from contextvars import ContextVar
from urllib.parse import quote, urlparse, urlunparse

import httpx
import typer
from loguru import logger as optic

from observal_cli import config
from observal_cli.error_context import caller_context
from observal_cli.errors import CliError, ErrorCategory, fail

logger = logging.getLogger(__name__)

# Cached server version for the process lifetime
_server_version_cache: str | None = None
# Whether version enforcement has already run this session
_version_enforced: bool = False

# Subcommands exempt from version enforcement (user needs these to fix mismatches)
_EXEMPT_SUBCOMMANDS = frozenset({"self", "server"})
_OPTIONAL_AUTH = ContextVar("observal_optional_auth", default=False)
_PUBLIC_POST = ContextVar("observal_public_post", default=False)
_QueryParams = dict | list[tuple[str, str]]


def _get_cli_version() -> str:
    """Get current CLI version string for request headers."""
    try:
        from importlib.metadata import version

        return version("observal-cli")
    except Exception:
        return "0.0.0"


def _client() -> tuple[str, dict]:
    auth_required = not _OPTIONAL_AUTH.get()
    cfg = config.get_or_exit() if auth_required else config.get_or_exit(require_auth=False)
    base_url = cfg["server_url"].rstrip("/")
    headers = {"X-Observal-CLI-Version": _get_cli_version()}
    if token := cfg.get("access_token"):
        headers["Authorization"] = f"Bearer {token}"
    # Run version enforcement once per session (unless exempt subcommand)
    _enforce_version_once(base_url)
    return base_url, headers


def _enforce_version_once(server_url: str) -> None:
    """Run version enforcement exactly once per CLI session.

    Checks if CLI version exactly matches server. Hard exits on mismatch.
    Exempt: `observal self` and `observal server` subcommands.
    """
    global _version_enforced
    if _version_enforced:
        return
    _version_enforced = True

    # Check if current subcommand is exempt (handle flags before subcommand)
    import sys

    positional_args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if positional_args and positional_args[0] in _EXEMPT_SUBCOMMANDS:
        return

    from observal_cli.version_check import check_version_compatibility

    check_version_compatibility(server_url)


def _request_id(response: httpx.Response) -> str | None:
    return next(
        (value for key, value in response.headers.items() if key.lower() in {"x-request-id", "request-id"}),
        None,
    )


def _safe_detail(response: httpx.Response) -> str | None:
    if "application/json" not in response.headers.get("content-type", "").lower():
        return None
    try:
        data = response.json()
    except (ValueError, UnicodeDecodeError):
        return None
    # FastAPI errors use ``detail``; ARD routes use ``{"errorCode", "message"}``.
    detail = (data.get("detail") or data.get("message")) if isinstance(data, dict) else None
    return detail.strip()[:500] if isinstance(detail, str) and detail.strip() else None


def _browse_remediation(path: str) -> str:
    parts = path.strip("/").split("/")
    type_plural = (
        "agents" if len(parts) > 3 and parts[2:4] == ["insights", "agents"] else parts[2] if len(parts) > 2 else ""
    )
    if type_plural.endswith("xes"):
        type_singular = type_plural[:-2]
    elif type_plural.endswith("s"):
        type_singular = type_plural[:-1]
    else:
        type_singular = type_plural
    if type_singular == "agent":
        browse_cmd = "observal agent list"
    elif type_singular in {"mcp", "skill", "hook", "prompt", "sandbox"}:
        browse_cmd = f"observal registry {type_singular} list"
    else:
        return "Check the identifier and retry."
    return f"Check the identifier or run {browse_cmd} to browse available resources."


def _handle_error(
    error: httpx.HTTPStatusError,
    path: str = "",
    *,
    operation: str | None = None,
    resource: str | None = None,
) -> None:
    """Convert an HTTP status into the stable CLI error contract."""
    optic.trace("error={}, path={}", error, path)
    response = error.response
    code = response.status_code
    detail = _safe_detail(response)
    context = {
        "operation": operation or f"Request {path or 'server resource'}",
        "resource": resource or path or "Observal server",
        "request_id": _request_id(response),
        "http_status": code,
        "detail": repr(error),
    }

    if code == 401:
        fail(
            ErrorCategory.AUTH,
            "Authentication failed.",
            remediation="Run observal auth login to authenticate again.",
            **context,
        )
    if code == 403:
        fail(
            ErrorCategory.PERMISSION,
            detail or "You do not have permission to perform this operation.",
            remediation="Ask an administrator or resource owner for the required access.",
            **context,
        )
    if code == 404:
        fail(
            ErrorCategory.NOT_FOUND,
            detail or "The requested resource was not found.",
            remediation=_browse_remediation(path),
            **context,
        )
    if code == 409:
        fail(
            ErrorCategory.CONFLICT,
            detail or "The requested change conflicts with current state.",
            remediation="Refresh the resource state and retry the operation.",
            **context,
        )
    if code == 426:
        fail(
            ErrorCategory.VERSION,
            detail or "The CLI and server versions are incompatible.",
            remediation="Install the CLI version required by the server.",
            **context,
        )
    if code == 429:
        retry_after = response.headers.get("Retry-After", "a few seconds")
        fail(
            ErrorCategory.RATE_LIMIT,
            "The server rate limit was reached.",
            remediation=f"Retry in {retry_after}.",
            **context,
        )
    if code >= 500:
        fail(
            ErrorCategory.UNAVAILABLE,
            f"The server returned HTTP {code}.",
            remediation="Check server health and logs, then run observal doctor.",
            **context,
        )
    fail(
        ErrorCategory.VALIDATION,
        detail or f"The server rejected the request with HTTP {code}.",
        remediation="Correct the request input and retry.",
        **context,
    )


def _handle_connect(*, operation: str | None = None, resource: str | None = None, detail: str | None = None) -> None:
    """Convert a connection failure into the stable CLI error contract."""
    server_url = config.load().get("server_url", "not set")
    fail(
        ErrorCategory.UNAVAILABLE,
        "Cannot reach the Observal server.",
        operation=operation or "Connect to Observal",
        resource=resource or f"server {server_url}",
        remediation="Check the server URL and service health, then run observal doctor.",
        detail=detail,
    )


def _handle_timeout(
    path: str = "",
    *,
    operation: str | None = None,
    resource: str | None = None,
    detail: str | None = None,
) -> None:
    """Convert a timeout into the stable CLI error contract."""
    optic.trace("path={}", path)
    timeout = config.get_timeout()
    fail(
        ErrorCategory.UNAVAILABLE,
        f"The request timed out after {timeout} seconds.",
        operation=operation or f"Request {path or 'server resource'}",
        resource=resource or path or "Observal server",
        remediation="Increase OBSERVAL_TIMEOUT if appropriate and check server health with observal doctor.",
        detail=detail,
    )


def _try_refresh_token() -> bool:
    """Attempt to refresh the access token using the stored refresh token.

    Returns True if the refresh succeeded and config was updated.
    """
    cfg = config.load()
    refresh_token = cfg.get("refresh_token")
    server_url = cfg.get("server_url", "").rstrip("/")
    if not refresh_token or not server_url:
        return False

    try:
        r = httpx.post(
            f"{server_url}/api/v1/auth/token/refresh",
            json={"refresh_token": refresh_token},
            timeout=10,
        )
        if r.status_code != 200:
            return False
        data = r.json()
        config.save(
            {
                "access_token": data["access_token"],
                "refresh_token": data["refresh_token"],
            }
        )
        return True
    except Exception:
        return False


_MAX_RETRIES = 3
_RETRY_STATUSES = {429, 503, 504}


def _request_with_retry(
    method: str,
    url: str,
    headers: dict,
    *,
    params: _QueryParams | None = None,
    json: object | None = None,
    send_json: bool = False,
) -> httpx.Response:
    """Execute HTTP with transient retries for GET requests only.

    On 401, attempts a token refresh and retries once. Mutations are never
    retried after a transient response because their server state may be
    unknown.
    """
    optic.trace("method={}, url={}", method, url)
    timeout = config.get_timeout()
    func = getattr(httpx, method)

    request_headers = headers
    kwargs: dict = {"headers": request_headers, "timeout": timeout}
    if params is not None:
        kwargs["params"] = params
    if send_json and json is None:
        request_headers = {**headers, "Content-Type": "application/json"}
        kwargs["headers"] = request_headers
        kwargs["content"] = b"null"
    elif json is not None:
        kwargs["json"] = json

    safe_url = urlunparse(urlparse(url)._replace(netloc=urlparse(url).hostname or ""))
    optic.debug("{} {}", method.upper(), safe_url)
    t0 = time.monotonic()

    for attempt in range(_MAX_RETRIES):
        r = func(url, **kwargs)

        # Auto-refresh on 401
        if r.status_code == 401 and attempt == 0 and _try_refresh_token():
            # Update headers with new token and retry
            cfg = config.load()
            headers["Authorization"] = f"Bearer {cfg['access_token']}"
            request_headers["Authorization"] = headers["Authorization"]
            kwargs["headers"] = request_headers
            optic.debug("token refreshed, retrying")
            continue

        if method != "get" or r.status_code not in _RETRY_STATUSES or attempt == _MAX_RETRIES - 1:
            elapsed = (time.monotonic() - t0) * 1000
            optic.debug("{} {} -> {} ({:.0f}ms)", method.upper(), safe_url, r.status_code, elapsed)
            r.raise_for_status()
            return r
        # Honor Retry-After header if present
        retry_after = r.headers.get("Retry-After")
        delay = float(retry_after) if retry_after else 0.5 * (2**attempt)
        logger.debug(f"Retrying {method.upper()} {safe_url} (attempt {attempt + 1}, delay {delay:.1f}s)")
        optic.debug("retrying {} {} (attempt {}, delay {:.1f}s)", method.upper(), safe_url, attempt + 1, delay)
        time.sleep(delay)
    return r  # unreachable but satisfies type checker


def resolve_registry_reference(item_type: str, reference: str) -> str:
    """Resolve a typed row, alias, or qualified registry reference to a UUID."""
    item_type = {
        "agents": "agent",
        "mcps": "mcp",
        "skills": "skill",
        "hooks": "hook",
        "prompts": "prompt",
        "sandboxes": "sandbox",
    }.get(item_type, item_type)
    resolved = (
        config.resolve_alias(reference, expected_type=item_type)
        if reference.isdigit()
        else config.resolve_alias(reference)
    )
    if "/" not in resolved:
        return resolved
    data = get(
        "/api/v1/registry/resolve",
        params={"type": item_type, "identifier": resolved},
    )
    return str(data["id"])


def canonical_name(item: dict) -> str:
    return item.get("qualified_name") or item.get("name", "")


def resolve_team_id(reference: str) -> str:
    """Resolve a team UUID or handle via GET /teams/by-handle.

    Falls back to scanning the authenticated team list when the by-handle
    lookup fails for any reason — a missing team, an older server that
    predates the route, or a transport error. The fallback goes through
    ``get()``, which owns the user-facing error handling for a server that is
    genuinely unreachable.
    """
    value = reference.strip().lstrip("@").lower()
    try:
        return str(uuid.UUID(value))
    except ValueError:
        pass
    try:
        base, headers = _client()
        # Encode the raw CLI input so a reference containing "/" or ".." can
        # never change which endpoint receives the caller's bearer token.
        r = _request_with_retry("get", f"{base}/api/v1/teams/by-handle/{quote(value, safe='')}", headers)
        return str(r.json()["id"])
    except (CliError, typer.Exit, KeyboardInterrupt):
        # Control flow, not a lookup failure. `_client()` runs version
        # enforcement, which hard-exits on a server/CLI mismatch; swallowing
        # that Exit would let the command run on against an incompatible server.
        raise
    except Exception:
        # The by-handle lookup is an optimization. Any real failure (HTTP error,
        # transport failure, incomplete config) falls through to the scan below,
        # which goes through get() and owns the user-facing error handling.
        pass
    teams = get("/api/v1/teams/all")
    for team in teams:
        if str(team.get("handle", "")).lower() == value:
            return str(team["id"])
    raise typer.BadParameter(f"No teamspace with handle '{reference}'", param_hint="team")


def add_publish_target(payload: dict, team: str | None, visibility: str | None) -> None:
    """Add and validate the teamspace target fields for a publish request."""
    target_visibility = (visibility or "public").strip().lower()
    if target_visibility not in {"public", "team"}:
        raise typer.BadParameter("visibility must be 'public' or 'team'", param_hint="visibility")
    if target_visibility == "team" and not team:
        raise typer.BadParameter("--visibility team requires --team", param_hint="team")
    payload["visibility"] = target_visibility
    if team:
        payload["team_id"] = resolve_team_id(team)


def _request(
    method: str,
    path: str,
    *,
    operation: str,
    resource: str,
    params: _QueryParams | None = None,
    json_data: object | None = None,
    auth_required: bool = True,
    send_json: bool = False,
) -> httpx.Response:
    optional_auth_token = _OPTIONAL_AUTH.set(not auth_required)
    try:
        base, headers = _client()
    finally:
        _OPTIONAL_AUTH.reset(optional_auth_token)
    request_kwargs: dict = {}
    if params is not None:
        request_kwargs["params"] = params
    if json_data is not None or send_json:
        request_kwargs["json"] = json_data
    if send_json:
        request_kwargs["send_json"] = True
    try:
        return _request_with_retry(method, f"{base}{path}", headers, **request_kwargs)
    except httpx.HTTPStatusError as error:
        _handle_error(error, path, operation=operation, resource=resource)
    except (httpx.ReadTimeout, httpx.ConnectTimeout) as error:
        _handle_timeout(path, operation=operation, resource=resource, detail=repr(error))
    except httpx.ConnectError as error:
        _handle_connect(operation=operation, resource=resource, detail=repr(error))


def _json_response(response: httpx.Response, *, operation: str, resource: str, allow_empty: bool = False) -> object:
    if allow_empty and (response.status_code == 204 or not response.content):
        return {}
    try:
        return response.json()
    except (ValueError, UnicodeDecodeError) as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            "The server returned an invalid JSON response.",
            operation=operation,
            resource=resource,
            remediation="Check server health and version compatibility, then retry.",
            request_id=_request_id(response),
            http_status=response.status_code,
            detail=repr(error),
        )


def _error_context(
    operation: str | None,
    resource: str | None,
    *,
    default_operation: str,
    default_resource: str,
) -> tuple[str, str]:
    audited_operation, audited_resource = caller_context(default_operation, default_resource)
    return operation or audited_operation, resource or audited_resource


def request_json(
    method: str,
    path: str,
    *,
    params: _QueryParams | None = None,
    json_data: object | None = None,
    send_json: bool = False,
    operation: str | None = None,
    resource: str | None = None,
) -> object:
    """Call one authenticated JSON endpoint through the shared error contract."""
    method = method.lower()
    operation, resource = _error_context(
        operation,
        resource,
        default_operation=f"Call {method.upper()} {path}",
        default_resource=path,
    )
    response = _request(
        method,
        path,
        operation=operation,
        resource=resource,
        params=params,
        json_data=json_data,
        send_json=send_json,
    )
    return _json_response(
        response,
        operation=operation,
        resource=resource,
        allow_empty=method in {"post", "put", "patch", "delete"},
    )


def get(
    path: str,
    params: _QueryParams | None = None,
    *,
    operation: str | None = None,
    resource: str | None = None,
) -> dict:
    optic.trace("path={}, params={}", path, params)
    operation, resource = _error_context(
        operation,
        resource,
        default_operation=f"Fetch {path}",
        default_resource=path,
    )
    response = _request(
        "get",
        path,
        operation=operation,
        resource=resource,
        params=params,
        auth_required=False,
    )
    return _json_response(response, operation=operation, resource=resource)


def get_text(
    path: str,
    params: _QueryParams | None = None,
    *,
    content_type: str | None = None,
    operation: str | None = None,
    resource: str | None = None,
) -> str:
    """GET a text response, optionally enforcing its media type."""
    optic.trace("path={}, params={}, content_type={}", path, params, content_type)
    operation, resource = _error_context(
        operation,
        resource,
        default_operation=f"Fetch {path}",
        default_resource=path,
    )
    response = _request(
        "get",
        path,
        operation=operation,
        resource=resource,
        params=params,
        auth_required=False,
    )
    actual_content_type = response.headers.get("content-type", "").lower()
    if content_type and content_type.lower() not in actual_content_type:
        fail(
            ErrorCategory.UNAVAILABLE,
            f"The server returned content type {actual_content_type or 'missing'} instead of {content_type}.",
            operation=operation,
            resource=resource,
            remediation="Check server health and version compatibility, then retry.",
            request_id=_request_id(response),
            http_status=response.status_code,
        )
    return response.text


def get_bytes_with_headers(
    path: str,
    params: _QueryParams | None = None,
    *,
    operation: str | None = None,
    resource: str | None = None,
) -> tuple[bytes, dict[str, str]]:
    """Read an authenticated file response without assuming it is JSON.

    Used by skill-folder previews: UTF-8 files are JSON, binary files are raw
    octet-stream attachments. The ordinary ``get`` API intentionally rejects
    non-JSON responses.
    """
    operation, resource = _error_context(
        operation,
        resource,
        default_operation=f"Fetch {path}",
        default_resource=path,
    )
    response = _request(
        "get",
        path,
        operation=operation,
        resource=resource,
        params=params,
        auth_required=False,
    )
    return response.content, {key.lower(): value for key, value in response.headers.items()}


def get_with_headers(
    path: str,
    params: _QueryParams | None = None,
    *,
    operation: str | None = None,
    resource: str | None = None,
) -> tuple[dict, dict[str, str]]:
    """Like ``get()``, but also returns response headers with lowercase keys."""
    optic.trace("path={}, params={}", path, params)
    operation, resource = _error_context(
        operation,
        resource,
        default_operation=f"Fetch {path}",
        default_resource=path,
    )
    response = _request(
        "get",
        path,
        operation=operation,
        resource=resource,
        params=params,
        auth_required=False,
    )
    headers = {key.lower(): value for key, value in response.headers.items()}
    return _json_response(response, operation=operation, resource=resource), headers


def post(
    path: str,
    json_data: dict | None = None,
    *,
    operation: str | None = None,
    resource: str | None = None,
) -> dict:
    optic.trace("path={}, has_json_data={}", path, json_data is not None)
    operation, resource = _error_context(
        operation,
        resource,
        default_operation=f"Create or act on {path}",
        default_resource=path,
    )
    response = _request(
        "post",
        path,
        operation=operation,
        resource=resource,
        json_data=json_data,
        auth_required=not _PUBLIC_POST.get(),
    )
    return _json_response(response, operation=operation, resource=resource, allow_empty=True)


def post_public(
    path: str,
    json_data: dict | None = None,
    *,
    operation: str | None = None,
    resource: str | None = None,
) -> dict:
    """POST to a read-like public endpoint, using credentials when available."""
    public_post_token = _PUBLIC_POST.set(True)
    try:
        if operation is None and resource is None:
            return post(path, json_data)
        return post(path, json_data, operation=operation, resource=resource)
    finally:
        _PUBLIC_POST.reset(public_post_token)


def put(
    path: str,
    json_data: dict | None = None,
    *,
    operation: str | None = None,
    resource: str | None = None,
) -> dict:
    optic.trace("path={}, has_json_data={}", path, json_data is not None)
    operation, resource = _error_context(
        operation,
        resource,
        default_operation=f"Replace {path}",
        default_resource=path,
    )
    response = _request("put", path, operation=operation, resource=resource, json_data=json_data)
    return _json_response(response, operation=operation, resource=resource)


def patch(
    path: str,
    json_data: dict | None = None,
    *,
    operation: str | None = None,
    resource: str | None = None,
) -> dict:
    optic.trace("path={}, has_json_data={}", path, json_data is not None)
    operation, resource = _error_context(
        operation,
        resource,
        default_operation=f"Update {path}",
        default_resource=path,
    )
    response = _request("patch", path, operation=operation, resource=resource, json_data=json_data)
    return _json_response(response, operation=operation, resource=resource)


def delete(path: str, *, operation: str | None = None, resource: str | None = None) -> dict:
    optic.trace("path={}", path)
    operation, resource = _error_context(
        operation,
        resource,
        default_operation=f"Delete {path}",
        default_resource=path,
    )
    response = _request("delete", path, operation=operation, resource=resource)
    return _json_response(response, operation=operation, resource=resource, allow_empty=True)


def get_registered_agents_only() -> bool:
    """Check whether deployment registered-agent enforcement is enabled.

    Returns False when the server is not configured or the policy endpoint is unavailable.
    """
    try:
        cfg = config.load()
        server_url = cfg.get("server_url", "").rstrip("/")
        token = cfg.get("access_token", "")
        if not server_url or not token:
            return False
        r = httpx.get(
            f"{server_url}/api/v1/admin/registered-agents-only",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        if r.status_code == 200:
            return r.json().get("registered_agents_only", False)
        return False
    except Exception:
        return False


def get_registered_agent_names() -> set[str]:
    """Fetch the set of registered (approved) agent names from the server.

    Returns empty set on any error (fail-open).
    """
    try:
        cfg = config.load()
        server_url = cfg.get("server_url", "").rstrip("/")
        token = cfg.get("access_token", "")
        if not server_url or not token:
            return set()
        r = httpx.get(
            f"{server_url}/api/v1/agents",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        if r.status_code == 200:
            return {item.get("name", "") for item in r.json() if item.get("name")}
    except Exception:
        pass
    return set()


def get_registered_mcp_names() -> set[str]:
    """Fetch the set of registered (approved) MCP names from the server.

    Returns empty set on any error (fail-open).
    """
    try:
        cfg = config.load()
        server_url = cfg.get("server_url", "").rstrip("/")
        token = cfg.get("access_token", "")
        if not server_url or not token:
            return set()
        r = httpx.get(
            f"{server_url}/api/v1/mcp",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        if r.status_code == 200:
            return {item.get("name", "") for item in r.json() if item.get("name")}
    except Exception:
        pass
    return set()


def health() -> tuple[bool, float]:
    """Check server health. Returns (ok, latency_ms)."""
    cfg = config.load()
    url = cfg.get("server_url", "").rstrip("/")
    if not url:
        return False, 0
    try:
        t0 = time.monotonic()
        r = httpx.get(f"{url}/health", timeout=5)
        latency = (time.monotonic() - t0) * 1000
        return r.status_code == 200, latency
    except Exception:
        return False, 0


def server_supports(feature: str) -> bool:
    """Check if the connected server supports a given feature.

    Uses version negotiation: effective = min(cli_version, server_version).
    Feature availability is determined by the features registry.
    """
    optic.trace("feature={}", feature)
    global _server_version_cache
    if _server_version_cache is None:
        try:
            data = get("/api/v1/config/version")
            _server_version_cache = data.get("server_version", "0.0.0")
        except Exception:
            return False

    from packaging.version import Version

    from observal_cli.features import is_available

    cli_ver = _get_cli_version()
    try:
        effective = str(min(Version(cli_ver), Version(_server_version_cache)))
    except Exception:
        effective = _server_version_cache

    return is_available(feature, effective)
