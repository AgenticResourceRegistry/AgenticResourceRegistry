# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Human-readable link from a submission result to its review."""

from rich import print as rprint
from rich.markup import escape


def print_review_link(result: dict) -> None:
    number = result.get("review_number")
    if number is not None:
        rprint(f"[dim]Review #{number}: {escape(result.get('review_url') or f'/review/{number}')}[/dim]")
