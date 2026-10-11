# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""PR-style version review commands for authors and reviewers."""

from __future__ import annotations

import json
from urllib.parse import quote

import typer
from rich import print as rprint
from rich.table import Table
from rich.text import Text

from observal_cli import client, config
from observal_cli.errors import ErrorCategory, fail
from observal_cli.render import OutputMode, console, esc, output_json

review_app = typer.Typer(help="Review versions, conversations, checks, and publication gates", no_args_is_help=True)
policy_app = typer.Typer(help="Organization and teamspace review policy", no_args_is_help=True)
review_app.add_typer(policy_app, name="policy")


def _ref(value: str) -> str:
    """Row positions are shortcuts; #number is always an explicit review number."""
    if value.startswith("@"):
        value = config.resolve_alias(value, expected_type="review")
    elif value.isdigit():
        cache = config.load_last_results()
        if cache.get("item_type") == "review" and int(value) <= len(cache["ids"]) and int(value) > 0:
            value = cache["ids"][int(value) - 1]
    if "/" in value:
        value = str(client.get("/api/v1/reviews/resolve", params={"ref": value})["number"])
    return quote(value, safe="")


def _url(ref: str, suffix: str = "") -> str:
    return f"/api/v1/reviews/{_ref(ref)}{suffix}"


def _result(result: object, output: OutputMode, title: str) -> None:
    if output == "json":
        output_json(result)
    elif isinstance(result, dict):
        rprint(f"[green]{esc(title)}[/green]")
        for key, value in result.items():
            if key not in {"files", "hunks", "checks", "revisions", "items"}:
                rprint(f"  [dim]{esc(str(key))}:[/dim] {esc(str(value))}")
    else:
        rprint(result)


@review_app.command("list")
def list_reviews(
    needs: str | None = typer.Option(None, "--needs", help="my-review or ready-to-publish"),
    state: str = typer.Option("open", "--state"),
    mine: bool = typer.Option(False, "--mine"),
    requested: bool = typer.Option(False, "--requested"),
    type_filter: str | None = typer.Option(None, "--type"),
    team_id: str | None = typer.Option(None, "--team-id"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """List reviews visible to you; row numbers work with other review commands."""
    params: dict[str, str] = {"state": state, "limit": "100"}
    if needs:
        if needs not in ("my-review", "ready-to-publish"):
            fail(ErrorCategory.VALIDATION, "Use --needs my-review or ready-to-publish", operation="List reviews")
        params["needs"] = needs.replace("-", "_")
    if mine:
        params["author"] = "me"
    if requested:
        params["requested"] = "me"
    if type_filter:
        params["type"] = type_filter
    if team_id:
        params["team_id"] = team_id
    result = client.get("/api/v1/reviews", params=params)
    items = result["items"]
    config.save_last_results(items, "review")
    if output == "json":
        output_json(result)
        return
    table = Table(title="Reviews")
    for column in ("Row", "Review", "Type", "Version", "State", "Approvals", "Threads"):
        table.add_column(column)
    for index, item in enumerate(items, 1):
        gate = item["gate"]
        table.add_row(
            str(index),
            f"#{item['number']} {item['title']}",
            item["subject_type"],
            item["version"],
            item["state"],
            f"{gate['approvals']}/{gate['required']}",
            str(item["threads"]["unresolved"]),
        )
    console.print(table)
    if result.get("next_cursor"):
        rprint("[dim]More reviews available; narrow your filters to find them.[/dim]")


@review_app.command("show")
def show_review(ref: str, output: OutputMode = typer.Option("table", "--output", "-o")):
    """Show a review and its publication gate."""
    detail = client.get(_url(ref))
    if output == "json":
        output_json(detail)
        return
    rprint(f"[bold]{esc(detail['title'])}[/bold] [dim]#{detail['number']}[/dim] · {detail['state']}")
    rprint(f"{detail['subject_type']} v{detail['version']} · revision {detail['head_revision']}")
    gate = detail["gate"]
    rprint(f"Gate: {'ready' if gate['ready'] else 'blocked'} · {gate['approvals']}/{gate['required']} approvals")
    for requirement in gate["requirements"]:
        rprint(f"  [yellow]• {esc(str(requirement))}[/yellow]")
    if detail.get("body"):
        rprint(f"\n{esc(detail['body'])}")
    rprint(f"\n[dim]Open: /review/{detail['number']}[/dim]")


@review_app.command("files")
def files(ref: str, output: OutputMode = typer.Option("table", "--output", "-o")):
    """List changed virtual files."""
    rows = client.get(_url(ref, "/files"))
    if output == "json":
        output_json(rows)
        return
    table = Table(title="Files changed")
    for name in ("Status", "File", "+", "-"):
        table.add_column(name)
    for row in rows:
        table.add_row(row["status"], row["path"], str(row["additions"]), str(row["deletions"]))
    console.print(table)


@review_app.command("diff")
def diff(
    ref: str,
    file: str | None = typer.Option(None, "--file"),
    from_: str = typer.Option("base", "--from"),
    to: str = typer.Option("head", "--to"),
    since_my_review: bool = typer.Option(False, "--since-my-review"),
    full: bool = typer.Option(False, "--full"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Show numbered unified diff hunks (server redaction applies)."""
    if since_my_review:
        detail = client.get(_url(ref))
        last = detail.get("my_last_submission") or {}
        number = next((r["number"] for r in detail["revisions"] if r["id"] == last.get("revision_id")), None)
        if number is not None:
            from_ = f"r{number}"
    params: dict[str, str] = {"from": from_, "to": to}
    if file:
        params["path"] = file
    if full:
        params["full"] = "true"
    rows = client.get(_url(ref, "/diff"), params=params)
    if output == "json":
        output_json(rows)
        return
    for row in rows:
        if row["status"] == "unchanged":
            continue
        rprint(f"[bold]{esc(row['path'])}[/bold] ({row['status']}) +{row['additions']} -{row['deletions']}")
        if row["too_large"]:
            rprint("[yellow]Large file; retry with --full.[/yellow]")
        for hunk in row["hunks"]:
            rprint(
                f"[cyan]@@ -{hunk['base_start']},{hunk['base_lines']} +{hunk['head_start']},{hunk['head_lines']} @@[/cyan]"
            )
            for line in hunk["lines"]:
                prefix = {"add": "+", "del": "-", "ctx": " "}[line["t"]]
                console.print(Text(f"{prefix}{line['s']}", style={"add": "green", "del": "red", "ctx": ""}[line["t"]]))


@review_app.command("checks")
def checks(ref: str, output: OutputMode = typer.Option("table", "--output", "-o")):
    """Show validation and advisory checks."""
    result = client.get(_url(ref, "/checks"))
    if output == "json":
        output_json(result)
    else:
        for check in result:
            rprint(f"{check['status']}: {esc(check['name'])} {'(required)' if check['required'] else ''}")


@review_app.command("gate")
def gate(ref: str, output: OutputMode = typer.Option("table", "--output", "-o")):
    """Show publication requirements."""
    _result(client.get(_url(ref, "/gate")), output, "Publication gate")


@review_app.command("threads")
def threads(
    ref: str,
    unresolved: bool = typer.Option(False, "--unresolved"),
    file: str | None = typer.Option(None, "--file"),
    outdated: bool = typer.Option(False, "--outdated"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """List inline and general conversations."""
    params = {}
    if unresolved:
        params["resolved"] = "false"
    if file:
        params["path"] = file
    if outdated:
        params["outdated"] = "true"
    rows = client.get(_url(ref, "/threads"), params=params or None)
    if output == "json":
        output_json(rows)
    else:
        for row in rows:
            rprint(f"[bold]{row['id']}[/bold] {esc(row.get('path') or 'general')}:{row.get('start_line') or '-'}")
            for comment in row["comments"]:
                rprint(f"  {esc(comment['body'])}")


@review_app.command("comment")
def comment(
    ref: str,
    body: str = typer.Option(..., "--body"),
    file: str | None = typer.Option(None, "--file"),
    line: int | None = typer.Option(None, "--line"),
    end_line: int | None = typer.Option(None, "--end-line"),
    side: str = typer.Option("head", "--side"),
    suggest: str | None = typer.Option(None, "--suggest"),
    draft: bool = typer.Option(False, "--draft"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Add a general or line-anchored comment, optionally to a pending review."""
    if bool(file) != bool(line) or (end_line is not None and line is None) or (suggest and not file):
        fail(
            ErrorCategory.VALIDATION,
            "File comments need --file and --line; suggestions need a file",
            operation="Comment on review",
        )
    data: dict = {"body": body, "as_draft": draft}
    if file:
        data.update(path=file, side=side, start_line=line)
    if end_line is not None:
        data["end_line"] = end_line
    if suggest:
        data["suggestion"] = suggest
    _result(client.post(_url(ref, "/threads"), data), output, "Comment added")


@review_app.command("reply")
def reply(
    ref: str,
    thread_id: str,
    body: str = typer.Option(..., "--body"),
    draft: bool = typer.Option(False, "--draft"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Reply to a conversation."""
    _result(
        client.post(_url(ref, f"/threads/{quote(thread_id, safe='')}/comments"), {"body": body, "as_draft": draft}),
        output,
        "Reply added",
    )


@review_app.command("resolve")
def resolve(ref: str, thread_id: str, output: OutputMode = typer.Option("table", "--output", "-o")):
    """Resolve a conversation."""
    _result(client.post(_url(ref, f"/threads/{quote(thread_id, safe='')}/resolve")), output, "Conversation resolved")


@review_app.command("unresolve")
def unresolve(ref: str, thread_id: str, output: OutputMode = typer.Option("table", "--output", "-o")):
    """Reopen a conversation."""
    _result(client.post(_url(ref, f"/threads/{quote(thread_id, safe='')}/unresolve")), output, "Conversation reopened")


@review_app.command("submit")
def submit_review(
    ref: str,
    approve: bool = typer.Option(False, "--approve"),
    request_changes: bool = typer.Option(False, "--request-changes"),
    comment_only: bool = typer.Option(False, "--comment"),
    body: str = typer.Option("", "--body"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Submit pending comments with a verdict; publishing is a separate action."""
    if sum((approve, request_changes, comment_only)) != 1:
        fail(
            ErrorCategory.VALIDATION,
            "Choose exactly one: --approve, --request-changes or --comment",
            operation="Submit review",
        )
    if request_changes and not body.strip():
        fail(ErrorCategory.VALIDATION, "A change request needs --body", operation="Submit review")
    verdict = "approve" if approve else "request_changes" if request_changes else "comment"
    _result(client.post(_url(ref, "/submissions"), {"verdict": verdict, "body": body}), output, "Review submitted")


@review_app.command("approve")
def approve(
    ref: str, body: str = typer.Option("", "--body"), output: OutputMode = typer.Option("table", "--output", "-o")
):
    """Submit an approval, without publishing the version."""
    submit_review(ref, approve=True, request_changes=False, comment_only=False, body=body, output=output)


@review_app.command("request-changes")
def request_changes(
    ref: str, body: str = typer.Option(..., "--body"), output: OutputMode = typer.Option("table", "--output", "-o")
):
    """Request changes on a review."""
    submit_review(ref, approve=False, request_changes=True, comment_only=False, body=body, output=output)


@review_app.command("draft")
def draft(
    ref: str,
    discard: bool = typer.Option(False, "--discard"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """View or discard your pending review comments."""
    suffix = "/submissions/draft"
    _result(
        client.delete(_url(ref, suffix)) if discard else client.get(_url(ref, suffix)),
        output,
        "Draft discarded" if discard else "Pending draft",
    )


@review_app.command("request-reviewer")
def request_reviewer(ref: str, user: str, output: OutputMode = typer.Option("table", "--output", "-o")):
    """Request a reviewer by user UUID."""
    _result(client.post(_url(ref, "/reviewers"), {"user_id": user}), output, "Reviewer requested")


@review_app.command("dismiss")
def dismiss(
    ref: str,
    submission_id: str,
    reason: str = typer.Option(..., "--reason"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Dismiss a verdict (admin or team owner, reason required)."""
    _result(
        client.post(_url(ref, f"/submissions/{quote(submission_id, safe='')}/dismiss"), {"reason": reason}),
        output,
        "Verdict dismissed",
    )


@review_app.command("publish")
def publish(
    ref: str,
    category: str | None = typer.Option(None, "--category"),
    override_reason: str | None = typer.Option(None, "--override-reason"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Publish once the gate is ready (super admins can override with a reason)."""
    _result(
        client.post(_url(ref, "/publish"), {"category": category, "override_reason": override_reason}),
        output,
        "Version published",
    )


@review_app.command("close")
def close(
    ref: str, reason: str = typer.Option(..., "--reason"), output: OutputMode = typer.Option("table", "--output", "-o")
):
    """Close and reject a review."""
    _result(client.post(_url(ref, "/close"), {"reason": reason}), output, "Review closed")


@review_app.command("withdraw")
def withdraw(ref: str, output: OutputMode = typer.Option("table", "--output", "-o")):
    """Withdraw your own review."""
    _result(client.post(_url(ref, "/withdraw")), output, "Review withdrawn")


@review_app.command("mute")
def mute(ref: str, output: OutputMode = typer.Option("table", "--output", "-o")):
    """Mute review activity except final outcomes."""
    _result(client.put(_url(ref, "/subscription"), {"mode": "muted"}), output, "Review muted")


@review_app.command("watch")
def watch(ref: str, output: OutputMode = typer.Option("table", "--output", "-o")):
    """Watch review activity."""
    _result(client.put(_url(ref, "/subscription"), {"mode": "watching"}), output, "Watching review")


def _policy_path(team: str | None) -> str:
    return f"/api/v1/teams/{quote(team, safe='')}/review-policy" if team else "/api/v1/admin/review-policy"


@policy_app.command("show")
def policy_show(
    team: str | None = typer.Option(None, "--team"), output: OutputMode = typer.Option("table", "--output", "-o")
):
    """Show organization or teamspace review policy."""
    _result(client.get(_policy_path(team)), output, "Review policy")


@policy_app.command("set")
def policy_set(
    key: str,
    value: str,
    team: str | None = typer.Option(None, "--team"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Set a policy key; use required_approvals.TYPE and a count for per-type gates."""
    allowed = {"self_approval", "auto_publish", "dismiss_stale_approvals", "require_resolved_threads"}
    if key not in allowed and not key.startswith("required_approvals."):
        fail(ErrorCategory.VALIDATION, "Unknown review policy key", operation="Set review policy")
    path = _policy_path(team)
    current = client.get(path) or {}
    policy = {
        "required_approvals": {},
        "self_approval": "not_counted",
        "auto_publish": False,
        "dismiss_stale_approvals": True,
        "require_resolved_threads": False,
        **current,
    }
    if key.startswith("required_approvals."):
        try:
            count = int(value)
        except ValueError:
            fail(ErrorCategory.VALIDATION, "Approval count must be an integer", operation="Set review policy")
        policy["required_approvals"][key.split(".", 1)[1]] = count
    elif key == "self_approval":
        policy[key] = value
    else:
        try:
            parsed = json.loads(value.lower())
        except ValueError:
            parsed = None
        if not isinstance(parsed, bool):
            fail(ErrorCategory.VALIDATION, "Use true or false", operation="Set review policy")
        policy[key] = parsed
    _result(client.put(path, policy), output, "Review policy updated")
