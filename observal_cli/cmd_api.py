# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Authenticated JSON escape hatch for Observal API endpoints."""

from __future__ import annotations

import json
import sys
from typing import Any
from urllib.parse import unquote

import typer
from rich import print as rprint
from rich.table import Table

from observal_cli import client
from observal_cli.errors import ErrorCategory, fail, load_json_value
from observal_cli.render import OutputMode, esc, output_json

_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_NO_BODY = object()


def _api_path(value: str) -> str:
    path = "/" + value.strip().lstrip("/")
    decoded = path
    while True:
        expanded = unquote(decoded)
        if expanded == decoded:
            break
        decoded = expanded
    if (
        not path.startswith("/api/v1/")
        or not decoded.startswith("/api/v1/")
        or "?" in decoded
        or "#" in decoded
        or ".." in decoded.split("/")
    ):
        fail(
            ErrorCategory.VALIDATION,
            "API path must be a canonical /api/v1 endpoint without a query string.",
            operation="Call Observal API",
            resource="API path",
            remediation="Pass query values with --param KEY=VALUE.",
        )
    return path


def _params(values: list[str] | None) -> list[tuple[str, str]] | None:
    if not values:
        return None
    result: list[tuple[str, str]] = []
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key:
            fail(
                ErrorCategory.VALIDATION,
                f"Invalid API parameter: {value}.",
                operation="Call Observal API",
                resource="API query parameters",
                remediation="Use --param KEY=VALUE for every query parameter.",
            )
        result.append((key, item))
    return result


def _stdin_body() -> Any:
    if sys.stdin.isatty():
        return _NO_BODY
    content = sys.stdin.read().strip()
    if not content:
        return _NO_BODY
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as error:
        fail(
            ErrorCategory.VALIDATION,
            "API standard input is not valid JSON.",
            operation="Call Observal API",
            resource="API request body",
            remediation="Pipe one valid JSON value or use --from-file.",
            detail=repr(error),
        )
    return payload


def _render_response(response: Any) -> None:
    table = Table(title="API response")
    if isinstance(response, dict):
        table.add_column("field", style="cyan")
        table.add_column("value")
        for key, value in response.items():
            rendered = (
                json.dumps(value, default=str, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
            )
            table.add_row(esc(key), esc(rendered))
    elif isinstance(response, list):
        table.add_column("#", style="dim")
        table.add_column("value")
        for index, value in enumerate(response, 1):
            table.add_row(str(index), esc(json.dumps(value, default=str, ensure_ascii=False)))
    else:
        table.add_column("value")
        table.add_row(esc(response))
    rprint(table)


def api_request(
    method: str = typer.Argument(help="HTTP method: GET, POST, PUT, PATCH, or DELETE."),
    path: str = typer.Argument(help="Relative /api/v1 endpoint path."),
    from_file: str | None = typer.Option(None, "--from-file", "-f", help="Read a JSON request body."),
    param: list[str] | None = typer.Option(None, "--param", help="Query parameter as KEY=VALUE; repeatable."),
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json."),
):
    """Call an authenticated Observal JSON API endpoint.

    JSON from standard input is used when --from-file is omitted. The command
    uses the configured bearer token and never accepts arbitrary auth headers.

    Examples:
      observal api GET /api/v1/teams --output json
      observal api GET /api/v1/agents --param limit=10 --output json
      observal api POST /api/v1/teams --from-file team.json --output json
    """
    method = method.strip().upper()
    if method not in _METHODS:
        fail(
            ErrorCategory.VALIDATION,
            f"Unsupported API method: {method}.",
            operation="Call Observal API",
            resource="HTTP method",
            remediation=f"Choose from: {', '.join(sorted(_METHODS))}.",
        )
    endpoint = _api_path(path)
    query = _params(param)
    body = (
        load_json_value(from_file, operation="Call Observal API", noun="API request file")
        if from_file
        else _stdin_body()
    )
    if method == "GET" and body is not _NO_BODY:
        fail(
            ErrorCategory.VALIDATION,
            "GET does not accept a request body in this command.",
            operation="Call Observal API",
            resource="API request body",
            remediation="Remove the body or choose POST, PUT, PATCH, or DELETE.",
        )

    request_options: dict[str, Any] = {
        "params": query,
        "json_data": None if body is _NO_BODY else body,
        "operation": "Call Observal API",
        "resource": "Observal API endpoint",
    }
    if body is None:
        request_options["send_json"] = True
    response = client.request_json(method, endpoint, **request_options)

    if output == "json":
        output_json(response, raw=True)
        return
    _render_response(response)


def register_api(app: typer.Typer) -> None:
    app.command("api")(api_request)
