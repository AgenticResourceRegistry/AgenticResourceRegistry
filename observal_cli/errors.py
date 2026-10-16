# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Stable error contract for CLI users, scripts, and agents."""

from __future__ import annotations

import inspect
import json
import os
import sys
from contextlib import nullcontext, redirect_stdout
from contextvars import ContextVar
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from io import StringIO
from typing import NoReturn

import click
import httpx
from rich.console import Console
from rich.markup import escape
from typer.core import TyperGroup


class ExitCode(IntEnum):
    """Stable process exit codes for categorized CLI failures."""

    UNEXPECTED = 1
    USAGE = 2
    AUTH = 3
    PERMISSION = 4
    NOT_FOUND = 5
    CONFLICT = 6
    VALIDATION = 7
    RATE_LIMIT = 8
    UNAVAILABLE = 9
    VERSION = 10
    PARTIAL = 11


class ErrorCategory(StrEnum):
    UNEXPECTED = "unexpected"
    USAGE = "usage"
    AUTH = "authentication"
    PERMISSION = "permission"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    VALIDATION = "validation"
    RATE_LIMIT = "rate_limit"
    UNAVAILABLE = "unavailable"
    VERSION = "version_mismatch"


_EXIT_CODES = {
    ErrorCategory.UNEXPECTED: ExitCode.UNEXPECTED,
    ErrorCategory.USAGE: ExitCode.USAGE,
    ErrorCategory.AUTH: ExitCode.AUTH,
    ErrorCategory.PERMISSION: ExitCode.PERMISSION,
    ErrorCategory.NOT_FOUND: ExitCode.NOT_FOUND,
    ErrorCategory.CONFLICT: ExitCode.CONFLICT,
    ErrorCategory.VALIDATION: ExitCode.VALIDATION,
    ErrorCategory.RATE_LIMIT: ExitCode.RATE_LIMIT,
    ErrorCategory.UNAVAILABLE: ExitCode.UNAVAILABLE,
    ErrorCategory.VERSION: ExitCode.VERSION,
}

_invocation_args: ContextVar[tuple[str, ...]] = ContextVar("observal_invocation_args", default=())
_boundary_active: ContextVar[bool] = ContextVar("observal_error_boundary_active", default=False)
_json_error_mode: ContextVar[bool] = ContextVar("observal_json_error_mode", default=False)
_console = Console(stderr=True)


@dataclass(eq=False)
class CliError(click.exceptions.Exit):
    """A safe, actionable failure ready for human or JSON rendering."""

    category: ErrorCategory
    message: str
    operation: str
    resource: str | None = None
    remediation: str | None = None
    request_id: str | None = None
    http_status: int | None = None
    detail: str | None = None
    result: object | None = None

    def __post_init__(self) -> None:
        super().__init__(int(_EXIT_CODES[self.category]))

    @property
    def contract_exit_code(self) -> int:
        return int(_EXIT_CODES[self.category])


def fail(
    category: ErrorCategory,
    message: str,
    *,
    operation: str,
    resource: str | None = None,
    remediation: str | None = None,
    request_id: str | None = None,
    http_status: int | None = None,
    detail: str | None = None,
    result: object | None = None,
) -> NoReturn:
    if category is ErrorCategory.CONFLICT and os.environ.get("OBSERVAL_AUTO_UPDATE_REASON_FILE"):
        from observal_cli.auto_update_policy import record_skip_reason

        record_skip_reason(message)
    error = CliError(
        category=category,
        message=message,
        operation=operation,
        resource=resource,
        remediation=remediation,
        request_id=request_id,
        http_status=http_status,
        detail=detail,
        result=result,
    )
    if not _boundary_active.get():
        frame = inspect.currentframe()
        try:
            caller = frame.f_back if frame else None
            printer = caller.f_globals.get("rprint") if caller else None
            if callable(printer):
                printer(f"[red]{message}[/red]")
            else:
                emit_error(error, json_mode=False)
        finally:
            del frame
    raise error


def json_errors_requested(args: tuple[str, ...] | list[str] | None = None) -> bool:
    """Return whether the invocation explicitly selected JSON output."""
    values = tuple(args) if args is not None else _invocation_args.get()
    for index, value in enumerate(values):
        if value in {"--output=json", "-ojson"}:
            return True
        if value in {"--output", "-o"} and index + 1 < len(values) and values[index + 1] == "json":
            return True
    return False


def machine_output_requested(args: tuple[str, ...] | list[str] | None = None) -> bool:
    """Return whether an invocation selected finite JSON or raw JSON output."""
    values = tuple(args) if args is not None else _invocation_args.get()
    return json_errors_requested(values) or "--raw" in values


def load_json_value(path: str, *, operation: str, noun: str) -> object:
    """Load any CLI-supplied JSON value with categorized failures."""
    try:
        with open(path) as file:
            return json.load(file)
    except json.JSONDecodeError as error:
        fail(
            ErrorCategory.VALIDATION,
            f"The {noun} is not valid JSON.",
            operation=operation,
            resource=path,
            remediation="Correct the JSON and retry.",
            detail=repr(error),
        )
    except FileNotFoundError as error:
        fail(
            ErrorCategory.NOT_FOUND,
            f"The {noun} was not found.",
            operation=operation,
            resource=path,
            remediation="Provide an existing JSON file and retry.",
            detail=repr(error),
        )


def load_json_object(path: str, *, operation: str, noun: str) -> dict:
    """Load a CLI-supplied JSON object with categorized failures."""
    payload = load_json_value(path, operation=operation, noun=noun)
    if not isinstance(payload, dict):
        fail(
            ErrorCategory.VALIDATION,
            f"The {noun} must contain a JSON object.",
            operation=operation,
            resource=path,
            remediation="Replace the file contents with a JSON object and retry.",
        )
    return payload


def debug_requested(args: tuple[str, ...] | list[str] | None = None) -> bool:
    values = tuple(args) if args is not None else _invocation_args.get()
    return "--debug" in values


def _json_stream_requested(command: click.Command, args: tuple[str, ...]) -> bool:
    path = _command_path(command, args)
    return json_errors_requested(args) and (
        (path == "observal auth login" and any(value in {"--sso", "--saml"} for value in args))
        or (path == "observal server logs" and any(value in {"--follow", "-f"} for value in args))
    )


def emit_error(error: CliError, *, json_mode: bool | None = None, debug: bool | None = None) -> None:
    """Write exactly one error document or human error block to stderr."""
    use_json = _json_error_mode.get() if json_mode is None else json_mode
    use_debug = debug_requested() if debug is None else debug

    if use_json:
        payload: dict[str, object] = {
            "error": {
                "category": error.category.value,
                "message": error.message,
                "operation": error.operation,
                "exit_code": error.contract_exit_code,
            }
        }
        body = payload["error"]
        assert isinstance(body, dict)
        if error.resource:
            body["resource"] = error.resource
        if error.remediation:
            body["remediation"] = error.remediation
        if error.request_id:
            body["request_id"] = error.request_id
        if error.http_status is not None:
            body["http_status"] = error.http_status
        if error.result is not None:
            body["result"] = error.result
        if use_debug and error.detail:
            body["detail"] = error.detail
        print(json.dumps(payload, ensure_ascii=False, default=str), file=sys.stderr)
        return

    _console.print(f"[bold red]Error ({escape(error.category.value)}):[/bold red] {escape(error.message)}")
    _console.print(f"[dim]Operation:[/dim] {escape(error.operation)}")
    if error.resource:
        _console.print(f"[dim]Resource:[/dim] {escape(error.resource)}")
    if error.remediation:
        _console.print(f"[dim]Remediation:[/dim] {escape(error.remediation)}")
    if error.request_id:
        _console.print(f"[dim]Request ID:[/dim] {escape(error.request_id)}")
    if use_debug and error.detail:
        _console.print(f"[dim]Detail:[/dim] {escape(error.detail)}")


def emit_warning(
    category: str,
    message: str,
    *,
    operation: str,
    remediation: str | None = None,
    detail: str | None = None,
) -> None:
    """Emit a nonfatal startup warning without contaminating command stdout."""
    if _json_error_mode.get():
        body: dict[str, object] = {
            "category": category,
            "message": message,
            "operation": operation,
        }
        if remediation:
            body["remediation"] = remediation
        if debug_requested() and detail:
            body["detail"] = detail
        print(json.dumps({"warning": body}, ensure_ascii=False), file=sys.stderr)
        return

    _console.print(f"[bold yellow]Warning ({escape(category)}):[/bold yellow] {escape(message)}")
    if remediation:
        _console.print(f"[dim]Remediation:[/dim] {escape(remediation)}")


def _resolve_command(command: click.Command, args: tuple[str, ...]) -> tuple[click.Command, str]:
    path = [command.name or "observal"]
    current = command
    for value in args:
        commands = getattr(current, "commands", {})
        if value in commands:
            current = commands[value]
            path.append(value)
    return current, " ".join(path)


def _command_path(command: click.Command, args: tuple[str, ...]) -> str:
    return _resolve_command(command, args)[1]


def _uses_json_output(command: click.Command, args: tuple[str, ...]) -> bool:
    resolved, _path = _resolve_command(command, args)
    has_format_option = any(
        param.name == "output"
        and "json" in {getattr(choice, "value", choice) for choice in getattr(param.type, "choices", ())}
        for param in resolved.params
    )
    return has_format_option and json_errors_requested(args)


def _uses_machine_output(command: click.Command, args: tuple[str, ...]) -> bool:
    resolved, _path = _resolve_command(command, args)
    has_raw_option = any(param.name == "raw" for param in resolved.params)
    return _uses_json_output(command, args) or (has_raw_option and "--raw" in args)


class PartialResultExit(click.exceptions.Exit):
    """A nonzero batch outcome whose structured result must remain on stdout."""

    def __init__(self) -> None:
        super().__init__(int(ExitCode.PARTIAL))


def exit_partial() -> NoReturn:
    """Exit with the stable partial-result code after rendering the batch result."""
    raise PartialResultExit()


class _BoundaryError(Exception):
    def __init__(self, error: CliError) -> None:
        self.error = error


class _BoundaryExitError(Exception):
    def __init__(self, error: click.exceptions.Exit) -> None:
        self.error = error


class _BoundaryPartialExitError(Exception):
    def __init__(self, error: PartialResultExit) -> None:
        self.error = error


class ErrorHandlingGroup(TyperGroup):
    """Root command group that enforces the CLI error contract."""

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except CliError as error:
            raise _BoundaryError(error) from error
        except PartialResultExit as error:
            raise _BoundaryPartialExitError(error) from error
        except click.UsageError:
            raise
        except click.exceptions.Exit as error:
            if error.exit_code:
                raise _BoundaryExitError(error) from error
            raise

    def main(self, args=None, prog_name=None, complete_var=None, standalone_mode=True, **extra):
        invocation = tuple(args if args is not None else sys.argv[1:])
        token = _invocation_args.set(invocation)
        boundary_token = _boundary_active.set(True)
        json_token = _json_error_mode.set(_uses_machine_output(self, invocation))
        operation = f"Run {_command_path(self, invocation)}"
        captured = StringIO() if _json_error_mode.get() and not _json_stream_requested(self, invocation) else None
        output_context = redirect_stdout(captured) if captured is not None else nullcontext()
        try:
            with output_context:
                result = super().main(
                    args=list(invocation),
                    prog_name=prog_name,
                    complete_var=complete_var,
                    standalone_mode=False,
                    **extra,
                )
            if captured is not None:
                sys.stdout.write(captured.getvalue())
            return result
        except _BoundaryError as wrapped:
            emit_error(wrapped.error)
            code = wrapped.error.contract_exit_code
        except _BoundaryPartialExitError as wrapped:
            if captured is not None:
                sys.stdout.write(captured.getvalue())
            code = int(wrapped.error.exit_code)
        except _BoundaryExitError as wrapped:
            code = int(wrapped.error.exit_code)
            emit_error(
                CliError(
                    ErrorCategory.UNEXPECTED,
                    "The command could not complete.",
                    operation=operation,
                    resource=_command_path(self, invocation),
                    remediation="Retry with --debug for additional diagnostics.",
                )
            )
        except CliError as error:
            emit_error(error)
            code = error.contract_exit_code
        except click.UsageError as error:
            failure = CliError(
                ErrorCategory.USAGE,
                error.format_message(),
                operation=operation,
                remediation=f"Run {_command_path(self, invocation)} --help for valid usage.",
                detail=repr(error),
            )
            emit_error(failure)
            code = failure.contract_exit_code
        except click.Abort:
            if standalone_mode:
                click.echo("Aborted!", file=sys.stderr)
                raise SystemExit(1) from None
            raise
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            category = (
                ErrorCategory.AUTH
                if status == 401
                else ErrorCategory.PERMISSION
                if status == 403
                else ErrorCategory.NOT_FOUND
                if status == 404
                else ErrorCategory.CONFLICT
                if status == 409
                else ErrorCategory.VERSION
                if status == 426
                else ErrorCategory.RATE_LIMIT
                if status == 429
                else ErrorCategory.UNAVAILABLE
                if status >= 500
                else ErrorCategory.VALIDATION
            )
            failure = CliError(
                category,
                f"The server rejected the operation with HTTP {status}.",
                operation=operation,
                resource="Observal server",
                remediation="Check the request and server health, then retry.",
                request_id=next(
                    (value for key, value in error.response.headers.items() if key.lower() == "x-request-id"),
                    None,
                ),
                http_status=status,
                detail=repr(error),
            )
            emit_error(failure)
            code = failure.contract_exit_code
        except httpx.RequestError as error:
            failure = CliError(
                ErrorCategory.UNAVAILABLE,
                "The network operation failed.",
                operation=operation,
                resource="Observal service",
                remediation="Check network connectivity and service health, then retry.",
                detail=repr(error),
            )
            emit_error(failure)
            code = failure.contract_exit_code
        except PermissionError as error:
            failure = CliError(
                ErrorCategory.PERMISSION,
                "The operation was denied by the filesystem.",
                operation=operation,
                resource=str(error.filename or "local filesystem"),
                remediation="Check file ownership and permissions, then retry.",
                detail=repr(error),
            )
            emit_error(failure)
            code = failure.contract_exit_code
        except FileNotFoundError as error:
            failure = CliError(
                ErrorCategory.NOT_FOUND,
                "A required local file or command was not found.",
                operation=operation,
                resource=str(error.filename or "local resource"),
                remediation="Check the path and required dependencies, then retry.",
                detail=repr(error),
            )
            emit_error(failure)
            code = failure.contract_exit_code
        except TimeoutError as error:
            failure = CliError(
                ErrorCategory.UNAVAILABLE,
                "The local operation timed out.",
                operation=operation,
                resource="local operation",
                remediation="Check the dependent service or process, then retry.",
                detail=repr(error),
            )
            emit_error(failure)
            code = failure.contract_exit_code
        except click.exceptions.Exit as error:
            code = int(error.exit_code)
            if code:
                failure = CliError(
                    ErrorCategory.UNEXPECTED,
                    "The command failed without a categorized error.",
                    operation=operation,
                    remediation="Retry with --debug and report the failure if it persists.",
                )
                emit_error(failure)
                code = failure.contract_exit_code
        except Exception as error:
            if type(error).__module__.startswith(("click.", "typer.")) and callable(
                getattr(error, "format_message", None)
            ):
                failure = CliError(
                    ErrorCategory.USAGE,
                    error.format_message(),
                    operation=operation,
                    remediation=f"Run {_command_path(self, invocation)} --help for valid usage.",
                    detail=repr(error),
                )
            else:
                failure = CliError(
                    ErrorCategory.UNEXPECTED,
                    "The command failed unexpectedly.",
                    operation=operation,
                    remediation="Retry with --debug and report the failure if it persists.",
                    detail=repr(error),
                )
            emit_error(failure)
            code = failure.contract_exit_code
        finally:
            _invocation_args.reset(token)
            _boundary_active.reset(boundary_token)
            _json_error_mode.reset(json_token)

        if standalone_mode:
            raise SystemExit(code)
        return code
