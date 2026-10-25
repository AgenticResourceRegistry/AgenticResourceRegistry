# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Submit an edit made while a component is under review, as the web edit forms do.

A draft save on a version under review already reaches the review as a revision,
but only an explicit submit counts as the author's resubmission and answers
requested changes. Component `edit` commands call this so they behave like
`agent edit` and the web Save & submit forms.
"""

from __future__ import annotations

from urllib.parse import quote

from observal_cli import client

UNDER_REVIEW = ("pending", "changes_requested")
_REVIEW_FIELDS = ("status", "review_number", "review_url")


def needs_resubmission(result: dict) -> bool:
    """A pending save that pushed no revision changed nothing; submitting it again would be refused."""
    return not (result.get("status") == "pending" and result.get("review_revision") is None)


def submit_edit_under_review(kind: str, plural: str, resolved: str, result: dict, message: str | None = None) -> dict:
    """Submit the saved edit when the item is under review; return the save result with review fields."""
    if result.get("status") not in UNDER_REVIEW or not needs_resubmission(result):
        return result
    operation = f"Submit {kind} revision"
    query = f"?message={quote(message)}" if message else ""
    submitted = None
    if kind == "skill":
        # Saved skill versions are submitted by version UUID and observed revision.
        versions = client.get(f"/api/v1/skills/{resolved}/versions", operation=operation, resource=resolved)
        items = versions.get("items", []) if isinstance(versions, dict) else versions
        version = next(
            (v for v in items if v.get("version") == result.get("version") and v.get("status") in UNDER_REVIEW),
            None,
        )
        if version is not None:
            manifest = client.get(
                f"/api/v1/skills/{resolved}/versions/{version['id']}/manifest", operation=operation, resource=resolved
            )
            submitted = client.post(
                f"/api/v1/skills/{resolved}/versions/{version['id']}/submit",
                {"observed_revision": manifest["revision"], **({"message": message} if message else {})},
                operation=operation,
                resource=resolved,
            )
    if submitted is None:
        submitted = client.post(f"/api/v1/{plural}/{resolved}/submit{query}", operation=operation, resource=resolved)
    merged = {**result, **{key: submitted[key] for key in _REVIEW_FIELDS if key in submitted}}
    if "status" not in submitted:
        merged["status"] = "pending"  # The skill version route returns a manifest, not the listing.
    return merged
