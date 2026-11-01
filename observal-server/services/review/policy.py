# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Organization-wide policy, with teamspace overrides for private/public subjects."""

import json
from dataclasses import dataclass, field

import services.dynamic_settings as ds

SUBJECT_TYPES = ("agent", "mcp", "skill", "hook", "prompt", "sandbox")


@dataclass(frozen=True)
class ApprovalPolicy:
    required_approvals: dict[str, int] = field(default_factory=lambda: dict.fromkeys(SUBJECT_TYPES, 1))
    self_approval: str = "not_counted"
    dismiss_stale_approvals: bool = True
    require_resolved_threads: bool = False
    auto_publish: bool = False
    source: str = "organization"
    # Teamspace-only: the policy for items published to the public registry from this teamspace.
    public: "ApprovalPolicy | None" = None


def parse_policy(raw: str | dict | None, *, source="organization", _nested=False) -> ApprovalPolicy:
    data = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
    if not isinstance(data, dict):
        raise ValueError("Review policy must be an object")
    counts = dict.fromkeys(SUBJECT_TYPES, 1)
    for key, value in data.get("required_approvals", {}).items():
        # Zero approvals is allowed: with auto_publish it is the "auto approve" setting.
        if key not in counts or type(value) is not int or not 0 <= value <= 5:
            raise ValueError("Approval counts must be 0-5 for a known subject type")
        counts[key] = value
    public = data.get("public")
    if public is not None and (_nested or source != "teamspace"):
        raise ValueError("Only a teamspace policy may carry a public-namespace policy")
    if data.get("self_approval", "not_counted") not in ("not_counted", "counted"):
        raise ValueError("Invalid self-approval policy")
    flags = ("dismiss_stale_approvals", "require_resolved_threads", "auto_publish")
    if any(type(data.get(key, False)) is not bool for key in flags):
        raise ValueError("Review policy flags must be booleans")
    return ApprovalPolicy(
        counts,
        data.get("self_approval", "not_counted"),
        data.get("dismiss_stale_approvals", True),
        data.get("require_resolved_threads", False),
        data.get("auto_publish", False),
        source,
        parse_policy(public, source="teamspace", _nested=True) if public is not None else None,
    )


async def policy_for(review, *, db=None, org_raw=None, team_raw=None) -> ApprovalPolicy:
    org = parse_policy(org_raw if org_raw is not None else await ds.get("review.policy", default="{}"))
    if db is not None:
        # Visibility and team ownership may change after the review was opened.
        # The public registry must never inherit a former private team's lower bar.
        from services.review.decisions import _target

        subject, _ = await _target(db, review)
        team_id, is_private = subject.team_id, subject.is_private
    else:
        team_id, is_private = review.team_id, review.is_private
    if not team_id:
        return org
    raw = team_raw if team_raw is not None else await ds.get(f"review.policy.team.{team_id}", default="")
    if not raw:
        return org
    team = parse_policy(raw, source="teamspace")
    if is_private:
        return team
    # A public-namespace policy replaces the private one for public items; either way it can
    # only tighten the organization policy, never relax it.
    public = team.public or team
    return ApprovalPolicy(
        {key: max(org.required_approvals[key], public.required_approvals[key]) for key in SUBJECT_TYPES},
        "not_counted" if org.self_approval == "not_counted" else public.self_approval,
        org.dismiss_stale_approvals or public.dismiss_stale_approvals,
        org.require_resolved_threads or public.require_resolved_threads,
        org.auto_publish and public.auto_publish,
        "organization + teamspace",
    )
