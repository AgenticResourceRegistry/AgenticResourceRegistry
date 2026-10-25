# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Search-quality regression test.

``tests/fixtures/discovery_benchmark.json`` holds hand-written developer
requests paired with the resources that should come back. This test measures
Recall@5 and MRR@10 for the ranker on its own and for the full SQL-prefilter +
ranker path, and fails if either drops below the recorded floor. Raise the
floors when the ranker improves; never lower them to make a change pass.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

from models.discovery_entry import (
    DiscoveryEntry,
    DiscoveryKind,
    DiscoveryLifecycle,
    DiscoverySourceKind,
    DiscoveryVisibility,
)
from services.discovery.identity import KIND_MEDIA_TYPES
from services.discovery.projection import build_search_document
from services.discovery.search import rank_entries, search_entries
from tests import discovery_support as fx

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "discovery_benchmark.json"
BENCHMARK = json.loads(FIXTURE.read_text(encoding="utf-8"))

# Floors, not targets. Recorded from the first, lexical-only ranker (recall@5
# 0.957 / mrr@10 0.933 for the ranker alone; 0.936 / 0.922 end to end). Its
# known misses are pure synonyms ("PRs" for "pull requests", "ticket" for
# "issue"), which is what semantic retrieval is for. Move the floors up when
# the ranker improves; never down to make a change pass.
RECALL_AT_5_FLOOR = 0.90
COVERAGE_AT_5_FLOOR = 0.80  # every expected resource, not just one, must tend to appear
MRR_AT_10_FLOOR = 0.85
ZERO_RESULT_CEILING = 0.05


def _entry(spec: dict) -> DiscoveryEntry:
    kind = DiscoveryKind(spec["kind"])
    slug = spec["id"]
    return DiscoveryEntry(
        id=uuid.uuid5(uuid.NAMESPACE_URL, slug),
        ard_identifier=f"urn:air:bench.test:{kind.value}:{uuid.uuid5(uuid.NAMESPACE_URL, slug)}",
        kind=kind,
        media_type=KIND_MEDIA_TYPES[kind],
        display_name=spec["name"],
        description=spec["description"],
        representative_queries=spec["queries"],
        capabilities=spec["capabilities"],
        tags=spec["tags"],
        version="1.0.0",
        artifact_url=f"https://bench.test/api/v1/artifacts/{kind.value}/{slug}/1.0.0",
        native_ref=f"bench/{slug}@1.0.0",
        local_entity_id=uuid.uuid5(uuid.NAMESPACE_URL, slug),
        source_kind=DiscoverySourceKind.mirrored,  # synthetic catalog entry: no native row backs it
        publisher_domain="bench.test",
        visibility=DiscoveryVisibility.public,
        lifecycle_status=DiscoveryLifecycle.approved,
        activatable=True,
        search_document=build_search_document(
            spec["name"], slug, kind.value, spec["description"], spec["queries"], spec["capabilities"], spec["tags"]
        ),
        raw_entry={},
        content_hash="bench",
    )


ENTRIES = [_entry(spec) for spec in BENCHMARK["entries"]]
SLUG_BY_ID = {e.local_entity_id: spec["id"] for e, spec in zip(ENTRIES, BENCHMARK["entries"], strict=True)}


def _metrics(rankings: dict[str, list[str]]) -> dict[str, float]:
    recall_hits = 0
    coverage = 0.0
    reciprocal = 0.0
    zero = 0
    for case in BENCHMARK["queries"]:
        got = rankings[case["text"]]
        expected = set(case["expected"])
        top5 = set(got[:5])
        if not got:
            zero += 1
        if expected & top5:
            recall_hits += 1
        # Per-resource: a query expecting three resources gets 1/3 credit per one found.
        coverage += len(expected & top5) / len(expected)
        for rank, slug in enumerate(got[:10], start=1):
            if slug in expected:
                reciprocal += 1 / rank
                break
    n = len(BENCHMARK["queries"])
    return {
        "recall@5": recall_hits / n,
        "coverage@5": coverage / n,
        "mrr@10": reciprocal / n,
        "zero_result_rate": zero / n,
    }


def _report(label: str, metrics: dict[str, float], rankings: dict[str, list[str]]) -> str:
    misses = [
        f"  {case['text']!r}: expected {case['expected']}, got {rankings[case['text']][:5]}"
        for case in BENCHMARK["queries"]
        if not (set(case["expected"]) & set(rankings[case["text"]][:5]))
    ]
    lines = [f"{label}: " + ", ".join(f"{k}={v:.3f}" for k, v in metrics.items())]
    if misses:
        lines.append("  misses:")
        lines.extend(misses)
    return "\n".join(lines)


def test_fixture_is_well_formed():
    ids = [spec["id"] for spec in BENCHMARK["entries"]]
    assert len(ids) == len(set(ids)), "duplicate entry ids"
    for case in BENCHMARK["queries"]:
        assert case["expected"], case
        assert set(case["expected"]) <= set(ids), f"unknown expected id in {case}"
    for spec in BENCHMARK["entries"]:
        assert 2 <= len(spec["queries"]) <= 5, spec["id"]


def test_ranker_meets_quality_floors():
    rankings = {
        case["text"]: [SLUG_BY_ID[r.entry.local_entity_id] for r in rank_entries(case["text"], ENTRIES)]
        for case in BENCHMARK["queries"]
    }
    metrics = _metrics(rankings)
    report = _report("ranker", metrics, rankings)
    print(report)
    assert metrics["recall@5"] >= RECALL_AT_5_FLOOR, report
    assert metrics["coverage@5"] >= COVERAGE_AT_5_FLOOR, report
    assert metrics["mrr@10"] >= MRR_AT_10_FLOOR, report
    assert metrics["zero_result_rate"] <= ZERO_RESULT_CEILING, report


@pytest_asyncio.fixture()
async def sessions():
    engine = fx.make_engine()
    try:
        yield await fx.create_schema(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_end_to_end_search_does_not_lose_recall_to_the_prefilter(sessions):
    """The SQL prefilter must hand the ranker every candidate it needs."""
    async with sessions() as db:
        for entry in ENTRIES:
            db.add(entry)
        await db.commit()

        rankings: dict[str, list[str]] = {}
        for case in BENCHMARK["queries"]:
            page = await search_entries(db, text=case["text"], user=None, public_search_enabled=True, page_size=10)
            rankings[case["text"]] = [SLUG_BY_ID[r.entry.local_entity_id] for r in page.results]

    metrics = _metrics(rankings)
    report = _report("end-to-end", metrics, rankings)
    print(report)
    assert metrics["recall@5"] >= RECALL_AT_5_FLOOR, report
    assert metrics["coverage@5"] >= COVERAGE_AT_5_FLOOR, report
    assert metrics["mrr@10"] >= MRR_AT_10_FLOOR, report
    assert metrics["zero_result_rate"] <= ZERO_RESULT_CEILING, report
