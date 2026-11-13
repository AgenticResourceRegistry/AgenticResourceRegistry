# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Manual recovery for local harness sessions missed by automatic delivery."""

from __future__ import annotations

import sqlite3
from contextlib import nullcontext, redirect_stdout
from io import StringIO

import typer
from rich import print as rprint
from typer.models import OptionInfo

from observal_cli.errors import ErrorCategory, fail
from observal_cli.harness import ensure_loaded, get_adapter, get_all_adapters
from observal_cli.render import OutputMode, esc, output_json
from observal_cli.sessions.base import (
    drain_outbox,
    drain_session_source,
    load_config,
    read_cursor_state,
    recover_cursor_from_server,
)
from observal_cli.sessions.delivery_guard import DeliveryAttempt


def _value(value):
    return value.default if isinstance(value, OptionInfo) else value


def _capture(output: OutputMode | str):
    return redirect_stdout(StringIO()) if _value(output) == "json" else nullcontext()


def reconcile(
    harness: str = typer.Option("", "--harness", "-i", help="Target one harness"),
    since_hours: int = typer.Option(168, "--since", min=1, max=8760, help="Recent-session window in hours"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview without network or cursor changes"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Backfill local session records missed by automatic hook delivery.

    Examples:
      observal reconcile --output json
      observal reconcile --harness kiro --since 24 --output json
      observal reconcile --since 24 --dry-run --output json
    """
    harness = _value(harness).strip().lower()
    since_hours = _value(since_hours)
    dry_run = _value(dry_run)
    output = _value(output)
    if not 1 <= since_hours <= 8760:
        fail(
            ErrorCategory.VALIDATION,
            "Reconcile window must contain 1 to 8,760 hours.",
            operation="Reconcile local sessions",
            resource="session discovery window",
            remediation="Choose a positive window no greater than one year.",
        )

    cfg = load_config()
    if cfg is None or not cfg.get("user_id"):
        fail(
            ErrorCategory.AUTH,
            "Observal session delivery is not configured.",
            operation="Reconcile local sessions",
            resource="CLI authentication configuration",
            remediation="Run `observal auth login` and retry.",
        )

    ensure_loaded()
    adapters = get_all_adapters()
    if harness and harness not in adapters:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown harness: {harness}.",
            operation="Reconcile local sessions",
            resource="harness",
            remediation=f"Choose from: {', '.join(sorted(adapters))}.",
        )
    targets = [harness] if harness else [name for name, adapter in adapters.items() if adapter.is_installed()]

    rejections: list[tuple[str, str, int]] = []
    outbox_drained: bool | None = None
    with _capture(output):
        if not dry_run:
            try:
                outbox_drained = drain_outbox(cfg, rejections=rejections)
            except (OSError, sqlite3.Error, RuntimeError) as error:
                fail(
                    ErrorCategory.UNAVAILABLE,
                    "Could not read or deliver the durable session outbox.",
                    operation="Reconcile local sessions",
                    resource="session outbox",
                    remediation="Check local storage and server connectivity, then retry.",
                    detail=repr(error),
                )
            if not outbox_drained:
                rprint("[yellow]The durable session outbox still has pending records.[/yellow]")

        results = [_reconcile_harness(target, cfg, since_hours, dry_run, rejections) for target in targets]

        summary_keys = (
            "discovered",
            "pushed",
            "finalized",
            "queued",
            "rejected",
            "would_push",
            "would_finalize",
            "up_to_date",
            "skipped",
            "errors",
        )
        summary = {key: sum(result[key] for result in results) for key in summary_keys}
        summary["rejected"] = len(dict.fromkeys(rejections))

        if dry_run:
            rprint(
                f"\n[yellow]Dry run:[/yellow] {summary['would_push']} session(s) would send records; "
                f"{summary['would_finalize']} would send final metadata."
            )
        elif summary["pushed"] or summary["finalized"]:
            rprint(
                f"\n[green]✓ Delivered {summary['pushed']} session(s) and finalized "
                f"{summary['finalized']} session(s).[/green]"
            )
        elif not targets:
            rprint("[dim]No installed harnesses were detected.[/dim]")
        else:
            rprint("[dim]No new sessions to deliver.[/dim]")
        if summary["queued"]:
            rprint(f"[yellow]{summary['queued']} session(s) remain queued for retry.[/yellow]")
        if summary["rejected"]:
            rprint(f"[red]{summary['rejected']} session batch(es) were rejected and quarantined.[/red]")

    rejection_rows = [
        {"harness": rejected_harness, "session_id": session_id, "http_status": status}
        for rejected_harness, session_id, status in dict.fromkeys(rejections)
    ]
    result = {
        "dry_run": dry_run,
        "since_hours": since_hours,
        "outbox_drained": outbox_drained,
        "targets": results,
        "summary": summary,
        "rejections": rejection_rows,
    }
    if output == "json":
        output_json(result)


def _reconcile_harness(
    harness: str,
    cfg: dict,
    since_hours: int,
    dry_run: bool,
    rejections: list[tuple[str, str, int]] | None = None,
) -> dict:
    """Discover and reconcile one harness into an explicit per-session result."""
    adapter = get_adapter(harness)
    try:
        sources = adapter.discover_session_sources(since_hours=since_hours)
    except OSError as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            f"Could not discover {harness} sessions.",
            operation="Reconcile local sessions",
            resource=harness,
            remediation="Check the harness session directory and permissions, then retry.",
            detail=repr(error),
        )

    # Preserve source-group ownership while reconciling separately stored children.
    seen = set()
    groups = []
    for source in sources:
        candidates = []
        for candidate in (source, *adapter.related_session_sources(source)):
            if candidate.checkpoint_key not in seen:
                seen.add(candidate.checkpoint_key)
                candidates.append(candidate)
        if candidates:
            groups.append((source, candidates))

    result = {
        "harness": harness,
        "discovered": sum(len(candidates) for _source, candidates in groups),
        "pushed": 0,
        "finalized": 0,
        "queued": 0,
        "rejected": 0,
        "would_push": 0,
        "would_finalize": 0,
        "up_to_date": 0,
        "skipped": 0,
        "errors": 0,
        "sessions": [],
    }
    if not sources:
        if not dry_run:
            rprint(f"[dim]No {esc(harness)} sessions found[/dim]")
        return result

    rprint(f"[cyan]{esc(harness)}: scanning sessions...[/cyan]")
    rejections = rejections if rejections is not None else []
    for parent, candidates in groups:
        related = tuple(candidate for candidate in candidates if candidate is not parent)
        final = adapter.session_reconciliation_final(parent, cfg)
        event = {"session_id": parent.session_id, "hook_event_name": "Reconcile"}
        guard = (
            nullcontext(DeliveryAttempt())
            if dry_run
            else adapter.session_delivery_guard(
                parent,
                event,
                cfg,
                related=related,
                final=final,
            )
        )
        with guard as attempt:
            if not attempt.proceed:
                for candidate in candidates:
                    key = "skipped" if attempt.reason else "up_to_date"
                    result[key] += 1
                    session = {"session_id": candidate.session_id, "status": key, "bytes_new": 0}
                    if attempt.reason:
                        session["reason"] = attempt.reason
                    result["sessions"].append(session)
                continue
            pending = adapter.session_finalization_pending(parent, cfg)
            delivered = True
            for candidate in candidates:
                delivered = (
                    _reconcile_source(
                        adapter,
                        candidate,
                        cfg,
                        result,
                        dry_run,
                        rejections,
                        final,
                        pending,
                    )
                    and delivered
                )
            attempt.delivered = delivered
    return result


def _reconcile_source(adapter, source, cfg, result, dry_run, rejections, final, pending_finalization):
    """Reconcile one source while its parent group's delivery guard is held."""
    session = {"session_id": source.session_id, "status": "skipped", "bytes_new": 0}
    if source.path is None:
        result["skipped"] += 1
        session["reason"] = "source path unavailable"
        result["sessions"].append(session)
        return False
    try:
        size = source.path.stat().st_size
    except OSError as error:
        result["errors"] += 1
        session.update(status="error", reason=type(error).__name__)
        result["sessions"].append(session)
        rprint(f"  [red]✗[/red] {esc(source.session_id)} could not be read")
        return False

    if not adapter.should_capture_session(source):
        result["skipped"] += 1
        session["reason"] = "outside collection scope"
        result["sessions"].append(session)
        return True

    local_offset, _line_count, finalized = read_cursor_state(source.checkpoint_key)
    bytes_new = max(size - local_offset, 0)
    session["bytes_new"] = bytes_new
    if dry_run:
        if bytes_new:
            result["would_push"] += 1
            session["status"] = "would_push"
            rprint(f"  [dim]Would push:[/dim] {esc(source.session_id)} ({bytes_new} bytes new)")
        elif final and (not finalized or pending_finalization):
            result["would_finalize"] += 1
            session["status"] = "would_finalize"
            rprint(f"  [dim]Would finalize:[/dim] {esc(source.session_id)}")
        else:
            result["up_to_date"] += 1
            session["status"] = "up_to_date"
        result["sessions"].append(session)
        return True

    if finalized and local_offset >= size and not pending_finalization:
        result["up_to_date"] += 1
        session["status"] = "up_to_date"
        result["sessions"].append(session)
        return True

    try:
        recovered = recover_cursor_from_server(source, cfg)
    except (OSError, sqlite3.Error) as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            "Could not update the local session checkpoint.",
            operation="Reconcile local sessions",
            resource=source.session_id,
            remediation="Check local state storage and retry.",
            detail=repr(error),
        )
    if recovered is None:
        result["errors"] += 1
        session.update(status="checkpoint_mismatch", reason="server checkpoint does not match local source")
        result["sessions"].append(session)
        rprint(f"  [yellow]↻[/yellow] {esc(source.session_id)} checkpoint does not match local source")
        return False
    offset, _line_count = recovered
    session["bytes_new"] = max(size - offset, 0)
    rejections_before = len(rejections)
    try:
        delivered = drain_session_source(
            source,
            cfg,
            hook_event="Reconcile",
            final=final,
            extra_fields=adapter.session_extra_fields(source, {"event": "Reconcile"}, final),
            extra_records=adapter.session_extra_records(source, {"event": "Reconcile"}, final),
            rejections=rejections,
        )
    except sqlite3.Error as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            "Could not write to the durable session outbox.",
            operation="Reconcile local sessions",
            resource="session outbox",
            remediation="Check local storage and retry.",
            detail=repr(error),
        )
    except OSError as error:
        result["errors"] += 1
        session.update(status="error", reason=type(error).__name__)
        result["sessions"].append(session)
        rprint(f"  [red]✗[/red] {esc(source.session_id)} could not be queued")
        return False

    source_rejections = [
        rejection
        for rejection in rejections[rejections_before:]
        if rejection[0] == source.harness and rejection[1] == source.session_id
    ]
    if source_rejections:
        result["rejected"] += len(source_rejections)
        session.update(status="rejected", http_status=source_rejections[-1][2])
        rprint(f"  [red]✗[/red] {esc(source.session_id)} rejected by server")
    elif delivered:
        superseded = (
            final
            and not read_cursor_state(source.checkpoint_key)[2]
            and not adapter.session_reconciliation_final(source, cfg)
        )
        key = "pushed" if offset < size else "skipped" if superseded else "finalized" if final else "up_to_date"
        if superseded:
            session["reason"] = "finalization intent superseded"
        result[key] += 1
        session["status"] = key
        rprint(f"  [green]✓[/green] {esc(source.session_id)}")
    else:
        result["queued"] += 1
        session["status"] = "queued"
        rprint(f"  [yellow]↻[/yellow] {esc(source.session_id)} queued for retry")
    result["sessions"].append(session)
    return bool(delivered) and not source_rejections


def register_reconcile(app: typer.Typer) -> None:
    app.command(
        "reconcile",
        help=(
            "Backfill local session records missed by automatic hook delivery\n\n"
            "Examples:\n"
            "  observal reconcile --output json\n"
            "  observal reconcile --harness kiro --since 24 --output json\n"
            "  observal reconcile --dry-run --output json"
        ),
    )(reconcile)
