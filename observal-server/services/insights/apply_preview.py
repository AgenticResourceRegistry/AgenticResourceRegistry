# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""One staged builder for preview and Apply, with raw checks before any commit.

Review revisions and inbox items are transactional. Preview rolls its savepoint
back; PostgreSQL review-number sequences may have gaps, but no artifact/review/
notification survives. Generated reviews require human publication even under
an auto-publish policy. Never execute generated code here.
"""

import hashlib
import json
import uuid

from sqlalchemy import select

from models.agent import AgentVersion
from models.agent_component import AgentComponent
from models.hook import HookListing, HookVersion
from models.insight_report import InsightReport
from models.prompt import PromptListing, PromptVersion
from models.review import Review
from models.skill import SkillListing, SkillVersion
from services.review.checks import snapshot_checks
from services.review.files import render_files
from services.review.secret_scanner import CREDENTIAL, secret_paths


class PreflightError(ValueError):
    def __init__(self, errors):
        """Retain structured preflight errors for safe API serialization."""
        super().__init__("Proposed bundle failed deterministic preflight")
        self.errors = errors


async def _preview_payload(report_id, db, actor_id, selection):
    """Stage the exact selected artifacts, check safety/delivery, and hash their logical identity."""
    from services.insights.self_learn import _materialize_insight_suggestions

    items = await _materialize_insight_suggestions(report_id, db, actor_id, selection)
    if not items.get("agent_version"):
        raise ValueError("No selected changes can form an agent version; nothing was submitted")
    artifacts = []
    identities = {}
    for key, kind, listing_model, version_model in (
        ("skills", "skill", SkillListing, SkillVersion),
        ("hooks", "hook", HookListing, HookVersion),
        ("prompts", "prompt", PromptListing, PromptVersion),
    ):
        for info in items.get(key, []):
            listing = await db.get(listing_model, uuid.UUID(info["id"]))
            version = await db.get(version_model, listing.latest_version_id)
            identities[str(listing.id)] = f"new:{kind}:{listing.name}:listing"
            identities[str(version.id)] = f"new:{kind}:{listing.name}:version"
            artifacts.append((kind, listing.name, version))
    parent = await db.get(AgentVersion, uuid.UUID(items["agent_version"]["id"]))
    identities[str(parent.id)] = "new:agent:version"
    artifacts.append(("agent", "Agent version", parent))
    base_id = items["agent_version"]["base_version_id"]
    base = await db.get(AgentVersion, uuid.UUID(base_id))
    base_files = await render_files("agent", base, db)
    rendered = []
    proposal_secrets = secret_paths(items, "proposal")
    errors = (
        [{"type": "proposal", "name": "Submission metadata", "check": "secrets", "paths": proposal_secrets}]
        if proposal_secrets
        else []
    )
    reviews = []
    from observal_shared.harness_registry import HARNESS_REGISTRY
    from services.harness import ensure_loaded, get_adapter

    ensure_loaded()
    components = (await db.scalars(select(AgentComponent).where(AgentComponent.agent_version_id == parent.id))).all()
    for component in components:
        override_secrets = secret_paths(
            component.config_override,
            f"components[{component.component_type}:{component.component_id}].config_override",
        )
        if override_secrets:
            errors.append(
                {
                    "type": component.component_type,
                    "name": component.component_name,
                    "check": "secrets",
                    "paths": override_secrets,
                }
            )
        if component.component_type != "hook":
            continue
        hook = await db.get(HookVersion, component.resolved_version_id)
        if hook is None:
            errors.append(
                {
                    "type": "hook",
                    "name": component.component_name,
                    "check": "delivery",
                    "paths": ["Pinned hook unavailable"],
                }
            )
            continue
        config = {
            key: getattr(hook, key)
            for key in ("event", "handler_type", "handler_config", "script_content", "script_filename", "tool_filter")
        }
        config["name"] = component.component_name
        for harness in parent.supported_harnesses or parent.inferred_supported_harnesses or []:
            adapter = get_adapter(harness)
            reasons = (
                adapter.hook_delivery_errors(config, HARNESS_REGISTRY[harness]["default_scope"])
                if adapter
                else ["Harness adapter unavailable"]
            )
            if reasons:
                errors.append(
                    {
                        "type": "hook",
                        "name": component.component_name,
                        "check": "delivery",
                        "paths": [harness + ": " + reason for reason in reasons],
                    }
                )
    for kind, name, version in artifacts:
        files = await render_files(kind, version, db)
        checks = snapshot_checks(files, version, subject_type=kind, base_files=base_files if kind == "agent" else {})
        failures = [c for c in checks if c.get("required") and c["status"] == "fail"]
        errors.extend({"type": kind, "name": name, "check": c["id"], "paths": c["details"]} for c in failures)
        review = await db.scalar(select(Review).where(Review.subject_type == kind, Review.version_id == version.id))
        if review is None:
            raise ValueError("Staged artifact has no review revision")
        reviews.append(
            {"id": str(review.id), "number": review.number, "type": kind, "name": name, "status": review.state.value}
        )
        rendered.append(
            {
                "type": kind,
                "name": name,
                "version": version.version,
                "files": files,
                "base_files": base_files if kind == "agent" else {},
                "checks": checks,
            }
        )

    def normalize(value):
        # Generated UUIDs differ between rollback-only preview and submission;
        # their logical references are stable. Existing references stay literal.
        """Replace newly generated UUIDs with logical references for stable preview fingerprints."""
        if isinstance(value, str):
            for identity, logical in identities.items():
                value = value.replace(identity, logical)
            return value
        if isinstance(value, dict):
            return {normalize(k): normalize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [normalize(v) for v in value]
        return value

    report = await db.get(InsightReport, uuid.UUID(str(report_id)))
    fingerprint = {
        "report_id": str(report_id),
        "base_version_id": base_id,
        "selection": selection,
        "raw_components": normalize(
            [
                {
                    "type": c.component_type,
                    "id": str(c.component_id),
                    "version_id": str(c.resolved_version_id),
                    "config_override": c.config_override,
                }
                for c in components
            ]
        ),
        "artifacts": normalize(rendered),
        "suggestions": report.narrative.get("suggestions"),
        "superseded": items.get("superseded_agent_versions", []),
        "linked_existing": items.get("linked_existing", []),
        "removed_components": items.get("removed_components", []),
    }
    digest = hashlib.sha256(json.dumps(fingerprint, sort_keys=True, default=str).encode()).hexdigest()
    return (
        {
            "ready": not errors,
            "digest": digest,
            "base_version_id": base_id,
            "artifacts": rendered,
            "errors": errors,
            "publication_order": [f"{kind}: {name}" for kind, name, _ in artifacts],
            "state": "preview_only; not_installable",
            "effects": {
                k: items.get(k, []) for k in ("linked_existing", "removed_components", "superseded_agent_versions")
            },
        },
        items,
        reviews,
    )


async def preview_suggestions(report_id, db, actor_id, selection):
    """Render a redacted preview and always roll back its staging savepoint."""
    transaction = await db.begin_nested()
    try:
        payload, _, _ = await _preview_payload(report_id, db, actor_id, selection)
        return _redact_preview(payload)
    finally:
        await transaction.rollback()


async def submit_suggestions(report_id, db, actor_id, selection, *, preview_digest=None):
    """Commit the checked proposal only when its preview digest still matches; never waive review."""
    transaction = await db.begin_nested()
    try:
        payload, items, reviews = await _preview_payload(report_id, db, actor_id, selection)
        if not payload["ready"]:
            raise PreflightError(_redact_preview(payload["errors"]))
        if preview_digest is not None and preview_digest != payload["digest"]:
            raise ValueError("Preview is stale; preview the selected changes again")
        items = {
            **items,
            "reviews": reviews,
            "state": "submitted_for_review; not_publicly_installable",
            "preview_digest": payload["digest"],
        }
        report = await db.get(InsightReport, uuid.UUID(str(report_id)))
        report.applied_items = items
        await db.flush()
        await transaction.commit()
        await db.commit()
        return items
    except Exception:
        if transaction.is_active or db.get_nested_transaction() is transaction:
            await transaction.rollback()
        # Only roll back the proposal savepoint. Rolling back the caller's
        # authentication transaction expires request.state.user and turns a
        # handled preflight/stale error into an audit middleware failure.
        raise


def _redact_preview(value):
    """Recursively hide credential-like values while preserving preview structure."""
    if isinstance(value, str):
        return CREDENTIAL.sub("<redacted>", value)
    if isinstance(value, list):
        return [_redact_preview(v) for v in value]
    if isinstance(value, dict):
        return {_redact_preview(k): _redact_preview(v) for k, v in value.items()}
    return value
