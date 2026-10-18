# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Human-readable link from a submission result to its review."""

from rich import print as rprint
from rich.markup import escape


def print_review_link(result: dict) -> None:
    number = result.get("review_number")
    if number is not None:
        rprint(f"[dim]Review #{number}: {escape(result.get('review_url') or f'/review/{number}')}[/dim]")


def print_draft_save(result: dict, submit_hint: str) -> None:
    """Say what a draft save did to the review, so an edit never silently goes stale."""
    revision = result.get("review_revision")
    number = result.get("review_number")
    if revision is not None and number is not None:
        rprint(
            f"[cyan]Review #{number} now shows this change as revision {revision}.[/cyan] "
            "[dim]Approvals on earlier revisions follow the review's stale-approval policy.[/dim]"
        )
        print_review_link(result)
    elif result.get("status") in ("draft", "rejected"):
        rprint(f"[dim]Not under review yet. Submit it with: {escape(submit_hint)}[/dim]")
