# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared rendering helpers for the Observal CLI."""

from __future__ import annotations

import json as _json
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from rich import print as rprint
from rich.console import Console
from rich.markup import escape as markup_escape
from rich.panel import Panel
from rich.table import Table  # noqa: TC002 - used at runtime

console = Console()


class OutputMode(str, Enum):
    """Supported CLI output formats."""

    table = "table"
    json = "json"


def esc(value: Any) -> str:
    """Render untrusted text as literal characters, not Rich markup.

    Anything the server or an LLM produced can contain square brackets —
    ``array[0]``, ``[/tmp]``, ``[bold]``. Rich reads those as style tags:
    a stray closing tag raises ``MarkupError`` and kills the command, and a
    valid-looking one silently swallows the text. Escape before interpolating.
    """
    return markup_escape("" if value is None else str(value))


# ── Status badges ────────────────────────────────────────

_STATUS_STYLES = {
    "approved": ("✓ approved", "green"),
    "active": ("✓ active", "green"),
    "pending": ("● pending", "yellow"),
    "rejected": ("✗ rejected", "red"),
    "error": ("✗ error", "red"),
    "success": ("✓ success", "green"),
    "inactive": ("○ inactive", "dim"),
}


def status_badge(status: str) -> str:
    label, color = _STATUS_STYLES.get(status, (status, "white"))
    return f"[{color}]{label}[/{color}]"


def listing_status(item: dict) -> str:
    """Status badge for a registry item, marked when an admin recommended it."""
    badge = status_badge(item.get("status", ""))
    return f"{badge}  [cyan]recommended[/cyan]" if item.get("is_recommended") else badge


# ── Relative time ────────────────────────────────────────


def relative_time(iso: str | None) -> str:
    if not iso:
        return "--"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        now = datetime.now(UTC)
        delta = now - dt
        secs = int(delta.total_seconds())
        if secs < 60:
            return "just now"
        if secs < 3600:
            m = secs // 60
            return f"{m}m ago"
        if secs < 86400:
            h = secs // 3600
            return f"{h}h ago"
        d = secs // 86400
        return f"{d}d ago"
    except Exception:
        return iso[:19] if iso else "--"


# ── Registry identities ──────────────────────────────────
# The API returns the canonical ``namespace/slug`` in ``qualified_name``. That
# form is what commands take (``observal agent pull alice/reviewer``), but it
# reads poorly in listings, so we show the bare name with the owning namespace
# beneath it as ``@alice``. Use ``client.canonical_name()`` for anything a user
# is meant to paste back into a command.


def registry_identity(item: dict) -> tuple[str, str | None]:
    """Split a registry item into ``(name, namespace)``.

    Prefers the explicit ``namespace``/``slug`` fields and falls back to parsing
    ``qualified_name`` so older server payloads still resolve a namespace.
    """
    namespace = (item.get("namespace") or "").strip() or None
    name = (item.get("slug") or "").strip() or None

    qualified = (item.get("qualified_name") or "").strip()
    if (not namespace or not name) and "/" in qualified:
        head, _, tail = qualified.partition("/")
        namespace = namespace or head
        name = name or tail

    return name or (item.get("name") or "").strip(), namespace


def display_name(item: dict) -> str:
    """The bare item name, never namespace-qualified."""
    return registry_identity(item)[0]


def handle(item: dict) -> str:
    """The owning namespace as ``@namespace``, for its own table column or field."""
    namespace = registry_identity(item)[1]
    return f"@{namespace}" if namespace else ""


def name_inline(item: dict) -> str:
    """Render ``name @namespace`` when columns are unavailable."""
    name, namespace = registry_identity(item)
    return f"{name} [dim]@{namespace}[/dim]" if namespace else name


# ── Stars ────────────────────────────────────────────────


def star_rating(n: int, max_stars: int = 5) -> str:
    return "[yellow]" + "★" * n + "[/yellow][dim]" + "☆" * (max_stars - n) + "[/dim]"


# ── Output format dispatch ───────────────────────────────


def list_envelope(items: list[Any]) -> dict[str, Any]:
    """Return the universal JSON contract for an unpaginated list."""
    return {"items": items, "total": len(items), "page": 1, "page_size": len(items)}


def output_json(data: Any, *, raw: bool = False) -> None:
    if isinstance(data, list) and not raw:
        data = list_envelope(data)
    elif isinstance(data, dict) and isinstance(data.get("items"), list) and not raw:
        data = dict(data)
        data.setdefault("total", len(data["items"]))
        data.setdefault("page", 1)
        data.setdefault("page_size", len(data["items"]))
    print(_json.dumps(data, default=str, ensure_ascii=False, indent=2))


def output_json_line(data: Any) -> None:
    """Write one compact JSON Lines record for a streaming command."""
    print(_json.dumps(data, default=str, ensure_ascii=False, separators=(",", ":")), flush=True)


def output_table(table: Table) -> None:
    console.print(table)


# ── Detail panels ────────────────────────────────────────


def kv_panel(title: str, fields: list[tuple[str, str]], border_style: str = "blue") -> Panel:
    lines = []
    for k, v in fields:
        lines.append(f"[bold]{k}:[/bold] {v}")
    return Panel("\n".join(lines), title=f"[bold]{title}[/bold]", border_style=border_style, expand=False)


# ── harness tag rendering ────────────────────────────────────

_HARNESS_COLORS = {
    "cursor": "cyan",
    "kiro": "magenta",
    "claude_code": "yellow",
    "claude-code": "yellow",
    "codex": "bright_blue",
    "copilot": "bright_magenta",
}


def ide_tags(harnesses: list[str]) -> str:
    parts = []
    for harness in harnesses:
        color = _HARNESS_COLORS.get(harness, "white")
        parts.append(f"[{color}]{harness}[/{color}]")
    return " ".join(parts) if parts else "[dim]none[/dim]"


# ── Progress spinner context ─────────────────────────────


def spinner(msg: str = "Loading..."):
    return console.status(f"[dim]{msg}[/dim]", spinner="dots")


# ── Message helpers ─────────────────────────────────────────


def error(msg: str, *, hint: str | None = None):
    """Print an error message with optional hint."""
    rprint(f"[bold red]Error:[/bold red] {msg}")
    if hint:
        rprint(f"[dim]  Hint: {hint}[/dim]")


def warning(msg: str):
    """Print a warning message."""
    rprint(f"[yellow]Warning:[/yellow] {msg}")


def success(msg: str):
    """Print a success message."""
    rprint(f"[green]Success:[/green] {msg}")


# ── Model display helpers ──
# Reads the pre-computed ``display`` field from the server API response
# (computed by ``services/model_display.py``). Falls back to raw model_id
# when display data isn't available (e.g. offline mirror, bare model_id lookup).

_MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _format_date_short(d: date) -> str:
    return f"{_MONTH_NAMES[d.month - 1]} {d.day}, {d.year}"


def _force_secondary(row: dict, is_rolling: bool) -> str | None:
    """Derive a secondary label when the caller wants forced disambiguation."""
    if is_rolling:
        return "latest"
    rd = row.get("release_date")
    if rd:
        try:
            d = datetime.fromisoformat(str(rd)).date() if not isinstance(rd, date) else rd
            return _format_date_short(d)
        except (ValueError, TypeError):
            pass
    return None


def format_model(row: dict, *, disambiguate: bool = False) -> tuple[str, str | None, bool]:
    """Format a model catalog row for CLI display.

    Returns ``(primary, secondary, is_rolling)``. Reads the server-computed
    ``display`` field when available; falls back to the model_id.
    """
    display = row.get("display")
    if isinstance(display, dict):
        primary = display.get("primary") or row.get("model_id", "")
        secondary = display.get("secondary")
        is_rolling = bool(display.get("is_rolling"))
        if disambiguate and not secondary:
            secondary = _force_secondary(row, is_rolling)
        return primary, secondary, is_rolling

    # Fallback: no pre-computed display (offline mirror or bare model_id lookup)
    primary = (row.get("display_name") or row.get("model_id") or "").strip()
    is_rolling = not primary[-8:].isdigit() if primary else False
    secondary = _force_secondary(row, is_rolling) if disambiguate else None
    return primary, secondary, is_rolling


def annotate_models(rows: list[dict]) -> list[dict]:
    """Return a new list where each row gets a ``_display`` dict with primary/secondary."""
    out: list[dict] = []
    for r in rows:
        annotated = dict(r)
        p, s, rolling = format_model(r, disambiguate=True)
        annotated["_display"] = {"primary": p, "secondary": s, "is_rolling": rolling}
        out.append(annotated)
    return out
