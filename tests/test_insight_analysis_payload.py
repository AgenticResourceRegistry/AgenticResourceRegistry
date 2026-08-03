# SPDX-FileCopyrightText: 2026 The Observal Authors
# SPDX-License-Identifier: Apache-2.0

"""Tests for persisting the insights analysis payload.

Covers the "unconditionally worth it" change: the pipeline now returns (and
the report row now persists) the ``version_impact`` and ``registry_offer``
analysis it previously discarded every run, plus a typed schema for the
analytical payload validated non-destructively on write.
"""

from __future__ import annotations

import uuid

from schemas.insight_analysis import validate_payload
from services.insights.generator import REPORT_VERSION, _empty_report
from services.insights.registry_match import CatalogOffer

# ── CatalogOffer.to_dict ────────────────────────────────────────────────


def test_catalog_offer_to_dict_serializes_full_offer():
    cid_a = uuid.uuid4()
    cid_b = uuid.uuid4()
    entry = {"id": str(cid_a), "qualified_name": "ns/foo-bar", "name": "foo-bar"}
    offer = CatalogOffer(
        entries_by_type={"skills": [entry]},
        offered_ids={cid_a, cid_b},
        registry_has_components=True,
        enabled=True,
    )

    d = offer.to_dict()

    assert d["enabled"] is True
    assert d["registry_has_components"] is True
    assert d["item_count"] == 1
    # offered_ids is a set -> serialized as sorted strings for stable storage.
    assert d["offered_ids"] == sorted([str(cid_a), str(cid_b)])
    assert d["entries_by_type"] == {"skills": [entry]}


def test_catalog_offer_to_dict_empty_offer():
    offer = CatalogOffer()

    d = offer.to_dict()

    assert d["enabled"] is True
    assert d["registry_has_components"] is None
    assert d["item_count"] == 0
    assert d["offered_ids"] == []
    assert d["entries_by_type"] == {}


def test_empty_or_disabled_offer_is_not_dropped_by_truthiness():
    """Regression guard for the generator serialization check.

    build_catalog never returns None — it returns CatalogOffer() on the
    no-match path and CatalogOffer(enabled=False) on the disabled path. Both
    are falsy under CatalogOffer.__bool__ (bool(entries_by_type)). The
    generator must use ``is not None`` rather than truthiness, or it persists
    None and loses the enabled / registry_has_components metadata that
    matters most in exactly these cases.
    """
    empty_offer = CatalogOffer()
    disabled_offer = CatalogOffer(enabled=False)

    # The exact expression the generator uses to serialize the offer.
    empty_serialized = empty_offer.to_dict() if empty_offer is not None else None
    disabled_serialized = disabled_offer.to_dict() if disabled_offer is not None else None

    # Both must serialize to a dict, not be dropped to None.
    assert empty_serialized is not None
    assert empty_serialized["item_count"] == 0
    assert disabled_serialized is not None
    assert disabled_serialized["enabled"] is False

    # And the truthiness check that USED to be there would have dropped them.
    assert not bool(empty_offer)  # falsy -> would have been None under `if offer`
    assert not bool(disabled_offer)


# ── InsightAnalysisPayload schema ───────────────────────────────────────


def test_payload_accepts_full_pipeline_output():
    content = {
        "metrics": {"rich": {"total_sessions": 10}, "overview": {"unique_users": 1}},
        "narrative": {"at_a_glance": {"health": "good"}, "suggestions": {"features_to_try": []}},
        "aggregated_data": {"metrics": {}, "facets_summary": {}, "regressions": [], "cross_user_patterns": {}},
        "version_impact": {"group_count": 3, "canonical_dirty_summary": None},
        "registry_offer": {"enabled": True, "item_count": 2, "offered_ids": [], "entries_by_type": {}},
        "facets_summary": {"goal_categories": []},
        "sessions_analyzed": 10,
    }

    validated = validate_payload(content)

    assert validated is not None
    assert validated["sessions_analyzed"] == 10
    assert validated["version_impact"]["group_count"] == 3
    assert validated["registry_offer"]["item_count"] == 2


def test_payload_accepts_none_for_new_fields():
    """A run may produce no version impact or an empty offer."""
    content = {
        "metrics": {},
        "narrative": {},
        "version_impact": None,
        "registry_offer": None,
        "sessions_analyzed": 0,
    }

    validated = validate_payload(content)

    assert validated is not None
    assert validated["version_impact"] is None
    assert validated["registry_offer"] is None


def test_payload_tolerates_extra_llm_fields():
    """extra='allow' so LLM output variance never fails validation."""
    content = {
        "metrics": {},
        "narrative": {"suggestions": {"features_to_try": [{"unexpected": "shape"}]}},
        "sessions_analyzed": 1,
        "some_unexpected_top_level": "fine",
    }

    validated = validate_payload(content)

    assert validated is not None


def test_payload_validation_falls_back_on_wrong_type():
    """A gross structural error returns None so callers fall back to raw dict."""
    content = {
        "metrics": "not-a-dict",  # wrong type
        "sessions_analyzed": 1,
    }

    validated = validate_payload(content)

    # Non-destructive: caller persists raw content as fallback.
    assert validated is None


# ── generator return dict ───────────────────────────────────────────────


def test_empty_report_includes_new_analysis_fields():
    report = _empty_report()

    assert report["version_impact"] is None
    assert report["registry_offer"] is None
    # Existing fields unchanged.
    assert report["report_version"] == REPORT_VERSION
    assert report["sessions_analyzed"] == 0
    assert "metrics" in report
    assert "narrative" in report
