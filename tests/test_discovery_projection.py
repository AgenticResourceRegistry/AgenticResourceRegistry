# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Discovery index: projection, identity, visibility, search ranking and the reprojection hook."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import jsonschema
import pytest
import pytest_asyncio
from sqlalchemy import select

from models.discovery_entry import DiscoveryEntry, DiscoveryKind, DiscoveryLifecycle, DiscoveryVisibility
from models.mcp import ListingStatus
from models.user import UserRole
from services.discovery import hooks as discovery_hooks
from services.discovery.identity import (
    build_urn,
    identity_uri,
    is_publisher_domain,
    normalize_media_type,
    normalize_urn,
    parse_urn,
    publisher_domain_from_url,
)
from services.discovery.projection import (
    ProjectionContext,
    build_search_document,
    choose_version,
    project_entity,
    reproject_all,
)
from services.discovery.search import (
    InvalidSearchRequestError,
    SearchFilters,
    decode_page_token,
    encode_page_token,
    rank_entries,
    search_entries,
)
from services.discovery.serialize import search_result_item
from services.discovery.visibility import visible_entries_predicate
from tests import discovery_support as fx

SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "observal-server"
    / "vendor"
    / "ard"
    / "spec"
    / "schemas"
    / "ard-entry.schema.json"
)
ENTRY_SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _validate_entry(entry: dict) -> None:
    schema = dict(ENTRY_SCHEMA)
    schema["$ref"] = "#/$defs/ArdEntry"
    jsonschema.validate(instance=entry, schema=schema)


@pytest_asyncio.fixture()
async def sessions():
    engine = fx.make_engine()
    try:
        yield await fx.create_schema(engine)
    finally:
        await engine.dispose()


# ── Identity ─────────────────────────────────────────────────────────────


def test_urn_round_trip_and_shape():
    entity = uuid.uuid4()
    urn = build_urn("observal.example.com", DiscoveryKind.skill, entity)
    assert urn == f"urn:air:observal.example.com:skill:{entity}"
    parsed = parse_urn(urn)
    assert parsed is not None
    assert parsed.publisher == "observal.example.com"
    assert parsed.kind == DiscoveryKind.skill
    assert parsed.entity_id == entity


def test_legacy_urn_prefix_is_normalised():
    assert normalize_urn("urn:ai:acme.com:skill:abc") == "urn:air:acme.com:skill:abc"
    assert parse_urn("urn:ai:acme.com:server:weather").name == "weather"
    assert parse_urn("not a urn") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("application/mcp-server", "application/mcp-server-card+json"),
        ("application/mcp-server+json", "application/mcp-server-card+json"),
        ("application/ai-skill", "application/ai-skill+md"),
        ("Application/AI-Skill+MD; charset=utf-8", "application/ai-skill+md"),
        ("application/x-unknown+json", "application/x-unknown+json"),
        (None, None),
    ],
)
def test_media_type_normalisation(raw, expected):
    assert normalize_media_type(raw) == expected


@pytest.mark.parametrize(
    ("url", "domain"),
    [
        ("https://observal.acme.com", "observal.acme.com"),
        ("observal.acme.com:8443", "observal.acme.com"),
        ("http://localhost:8000", "observal.local"),
        ("http://observal:8000", "observal.local"),  # single label is not an FQDN
        ("http://10.0.0.5", "observal.local"),  # addresses are not publishers
        ("", "observal.local"),
        (None, "observal.local"),
    ],
)
def test_publisher_domain_from_public_url(url, domain):
    assert publisher_domain_from_url(url) == domain


def test_is_publisher_domain_and_identity_uri():
    assert is_publisher_domain("observal.acme.com")
    assert not is_publisher_domain("observal")
    assert not is_publisher_domain("192.168.1.1")
    assert identity_uri("observal.acme.com") == "https://observal.acme.com"


def test_pinned_domain_wins_over_public_url():
    ctx = ProjectionContext.from_public_url("https://new-host.example.com", pinned_domain="observal.acme.com")
    assert ctx.publisher_domain == "observal.acme.com", "identifiers do not follow a moved public URL"
    assert ctx.artifact_base_url == "https://new-host.example.com", "artifact locations do"
    ctx = ProjectionContext.from_public_url("https://new-host.example.com", pinned_domain="observal")
    assert ctx.publisher_domain == "new-host.example.com", "an invalid pin is ignored"


def test_placeholder_identity_transitions_once_to_a_real_domain():
    placeholder = ProjectionContext.from_public_url("http://localhost:8000")
    configured = ProjectionContext.from_public_url("https://observal.acme.com", pinned_domain="")
    pinned = ProjectionContext.from_public_url("https://moved.example.net", pinned_domain=configured.publisher_domain)

    assert placeholder.publisher_domain == "observal.local"
    assert configured.publisher_domain == "observal.acme.com"
    assert pinned.publisher_domain == "observal.acme.com"
    assert pinned.artifact_base_url == "https://moved.example.net"


def test_projection_context_builds_artifact_urls():
    ctx = ProjectionContext.from_public_url("https://observal.acme.com/")
    entity = uuid.uuid4()
    assert ctx.artifact_url(DiscoveryKind.mcp, entity, "1.2.3") == (
        f"https://observal.acme.com/api/v1/artifacts/mcp/{entity}/1.2.3"
    )


def test_search_document_is_lowercase_deduplicated_tokens():
    doc = build_search_document("Security Review", ["Review PRs", "security"], None, "code_review")
    assert doc == "security review prs code"


# ── Version selection ────────────────────────────────────────────────────


class _V:
    def __init__(self, version, status, prerelease=False):
        self.version = version
        self.status = status
        self.is_prerelease = prerelease
        self.created_at = fx.NOW


def test_choose_version_prefers_latest_approved_over_newer_pending():
    versions = [_V("1.0.0", ListingStatus.approved), _V("1.1.0", ListingStatus.pending)]
    chosen, lifecycle = choose_version(object(), versions)
    assert chosen.version == "1.0.0"
    assert lifecycle == DiscoveryLifecycle.approved


def test_choose_version_exposes_pending_when_nothing_is_approved():
    chosen, lifecycle = choose_version(object(), [_V("0.1.0", ListingStatus.pending)])
    assert chosen.version == "0.1.0"
    assert lifecycle == DiscoveryLifecycle.pending


def test_choose_version_archived_latest_wins():
    versions = [_V("1.0.0", ListingStatus.approved), _V("1.1.0", ListingStatus.archived)]
    _, lifecycle = choose_version(object(), versions)
    assert lifecycle == DiscoveryLifecycle.archived


def test_choose_version_skips_prereleases_when_a_stable_approved_exists():
    versions = [_V("1.0.0", ListingStatus.approved), _V("2.0.0", ListingStatus.approved, prerelease=True)]
    chosen, _ = choose_version(object(), versions)
    assert chosen.version == "1.0.0"


def test_choose_version_empty():
    assert choose_version(object(), []) == (None, DiscoveryLifecycle.draft)


# ── Projection ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skill_projects_to_conformant_entry(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        listing = await fx.skill(db, owner)
        entry = await project_entity(db, DiscoveryKind.skill, listing.id, ctx=fx.CTX)
        await db.commit()

        assert entry.ard_identifier == f"urn:air:observal.example.com:skill:{listing.id}"
        assert entry.media_type == "application/ai-skill+md"
        assert entry.lifecycle_status == DiscoveryLifecycle.approved
        assert entry.visibility == DiscoveryVisibility.public
        assert entry.version == "1.2.0"
        assert entry.native_ref == "acme/security-review@1.2.0"
        assert entry.artifact_url.endswith(f"/api/v1/artifacts/skill/{listing.id}/1.2.0")
        assert entry.artifact_digest.startswith("sha256:")
        assert 2 <= len(entry.representative_queries) <= 5
        assert entry.representative_queries_source == "derived"
        assert "code review" in entry.capabilities
        assert "security" in entry.search_document and "review" in entry.search_document
        assert entry.activatable is True
        _validate_entry(entry.raw_entry)
        assert entry.raw_entry["obs:nativeRef"] == "acme/security-review@1.2.0"
        trust = entry.raw_entry["trustManifest"]
        assert trust == {"identity": "https://observal.example.com", "identityType": "https"}
        assert isinstance(trust["identity"], str), "spec: identity is a URI string, not an object"


@pytest.mark.asyncio
async def test_projection_is_idempotent(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        listing = await fx.skill(db, owner)
        first = await project_entity(db, DiscoveryKind.skill, listing.id, ctx=fx.CTX)
        await db.commit()
        first_id, first_hash, first_indexed = first.id, first.content_hash, first.indexed_at

        second = await project_entity(db, DiscoveryKind.skill, listing.id, ctx=fx.CTX)
        await db.commit()
        rows = (await db.execute(select(DiscoveryEntry))).scalars().all()

        assert len(rows) == 1
        assert second.id == first_id
        assert second.content_hash == first_hash
        assert second.indexed_at == first_indexed


@pytest.mark.asyncio
async def test_rename_keeps_identifier_and_updates_name(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        listing = await fx.skill(db, owner)
        before = await project_entity(db, DiscoveryKind.skill, listing.id, ctx=fx.CTX)
        await db.commit()
        urn, old_hash = before.ard_identifier, before.content_hash

        listing.name = "Security Audit"
        listing.slug = "security-audit"
        await db.flush()
        after = await project_entity(db, DiscoveryKind.skill, listing.id, ctx=fx.CTX)
        await db.commit()

        assert after.ard_identifier == urn
        assert after.display_name == "Security Audit"
        assert after.native_ref == "acme/security-audit@1.2.0"
        assert after.content_hash != old_hash


@pytest.mark.asyncio
async def test_recommendation_reprojects_as_its_own_term(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        listing = await fx.skill(db, owner)
        before = await project_entity(db, DiscoveryKind.skill, listing.id, ctx=fx.CTX)
        await db.commit()
        assert before.raw_entry["obs:recommended"] is False
        old_hash = before.content_hash

        listing.is_recommended = True
        await db.flush()
        after = await project_entity(db, DiscoveryKind.skill, listing.id, ctx=fx.CTX)
        await db.commit()

        assert after.raw_entry["obs:recommended"] is True
        assert after.content_hash != old_hash
        _validate_entry(after.raw_entry)


@pytest.mark.asyncio
async def test_lifecycle_follows_approval(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        listing = await fx.skill(db, owner, status=ListingStatus.pending)
        entry = await project_entity(db, DiscoveryKind.skill, listing.id, ctx=fx.CTX)
        assert entry.lifecycle_status == DiscoveryLifecycle.pending
        assert entry.activatable is False

        listing.latest_version.status = ListingStatus.approved
        await db.flush()
        entry = await project_entity(db, DiscoveryKind.skill, listing.id, ctx=fx.CTX)
        assert entry.lifecycle_status == DiscoveryLifecycle.approved
        assert entry.activatable is True

        listing.latest_version.status = ListingStatus.archived
        await db.flush()
        entry = await project_entity(db, DiscoveryKind.skill, listing.id, ctx=fx.CTX)
        assert entry.lifecycle_status == DiscoveryLifecycle.archived
        assert entry.activatable is False


@pytest.mark.asyncio
async def test_newer_pending_version_does_not_hide_approved_one(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        listing = await fx.skill(db, owner, version="1.0.0")
        await fx.add_skill_version(db, listing, owner, version="1.1.0", status=ListingStatus.pending, set_latest=False)
        entry = await project_entity(db, DiscoveryKind.skill, listing.id, ctx=fx.CTX)
        assert entry.version == "1.0.0"
        assert entry.lifecycle_status == DiscoveryLifecycle.approved


@pytest.mark.asyncio
async def test_deleted_listing_is_tombstoned(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        listing = await fx.skill(db, owner)
        await project_entity(db, DiscoveryKind.skill, listing.id, ctx=fx.CTX)
        await db.commit()
        listing_id = listing.id
        await db.delete(listing)
        await db.commit()

        entry = await project_entity(db, DiscoveryKind.skill, listing_id, ctx=fx.CTX)
        assert entry is not None
        assert entry.tombstoned_at is not None
        assert entry.activatable is False


@pytest.mark.asyncio
async def test_soft_deleted_agent_is_tombstoned(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        row = await fx.agent(db, owner)
        await project_entity(db, DiscoveryKind.agent, row.id, ctx=fx.CTX)
        row.deleted_at = fx.NOW
        await db.flush()
        entry = await project_entity(db, DiscoveryKind.agent, row.id, ctx=fx.CTX)
        assert entry.tombstoned_at is not None


@pytest.mark.asyncio
async def test_unknown_entity_projects_to_nothing(sessions):
    async with sessions() as db:
        assert await project_entity(db, DiscoveryKind.skill, uuid.uuid4(), ctx=fx.CTX) is None


@pytest.mark.asyncio
async def test_private_visibility_mapping(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        team = await fx.team_with_member(db, owner)
        personal = await fx.skill(db, owner, name="Personal", is_private=True)
        shared = await fx.skill(db, owner, name="Team Skill", is_private=True, team_id=team.id)
        e1 = await project_entity(db, DiscoveryKind.skill, personal.id, ctx=fx.CTX)
        e2 = await project_entity(db, DiscoveryKind.skill, shared.id, ctx=fx.CTX)
        assert e1.visibility == DiscoveryVisibility.owner
        assert e2.visibility == DiscoveryVisibility.team
        assert e2.team_id == team.id


@pytest.mark.asyncio
async def test_all_six_kinds_project_and_validate(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        skill = await fx.skill(db, owner)
        mcp = await fx.mcp(db, owner)
        await fx.prompt(db, owner)
        await fx.hook(db, owner)
        await fx.sandbox(db, owner)
        await fx.agent(db, owner, components=[("skill", skill.id, "Security Review"), ("mcp", mcp.id, "GitHub")])

        stats = await reproject_all(db, ctx=fx.CTX)
        assert stats.projected == 6
        assert stats.failed == 0

        rows = (await db.execute(select(DiscoveryEntry))).scalars().all()
        assert {r.kind for r in rows} == set(DiscoveryKind) - {DiscoveryKind.external}
        for row in rows:
            _validate_entry(row.raw_entry)
            assert row.artifact_digest
            assert row.raw_entry["url"] == row.artifact_url

        by_kind = {r.kind: r for r in rows}
        assert by_kind[DiscoveryKind.hook].activatable is False, "hooks are explicit-install only"
        assert "list_pull_requests" in by_kind[DiscoveryKind.mcp].capabilities
        assert "SHOULD-NEVER-LEAK" not in json.dumps(by_kind[DiscoveryKind.mcp].raw_entry)
        assert by_kind[DiscoveryKind.mcp].raw_entry["obs:requiresCredentials"] is True
        assert "skill:Security Review" in by_kind[DiscoveryKind.agent].capabilities


@pytest.mark.asyncio
async def test_reproject_all_isolates_failures_and_keeps_their_entries(sessions, monkeypatch):
    """One resource that fails to project must neither abort the batch nor be tombstoned."""
    from services.discovery import adapters, projection

    async with sessions() as db:
        owner = await fx.user(db)
        good = await fx.skill(db, owner, name="Good")
        bad = await fx.skill(db, owner, name="Bad", slug="bad")
        await reproject_all(db, ctx=fx.CTX)

        original = adapters.project_skill

        def exploding(listing, version):
            if listing.id == bad.id:
                raise RuntimeError("boom")
            return original(listing, version)

        monkeypatch.setitem(projection.ADAPTERS, DiscoveryKind.skill, exploding)
        good_row = (
            await db.execute(select(DiscoveryEntry).where(DiscoveryEntry.local_entity_id == good.id))
        ).scalar_one()
        good_row.content_hash = "stale"  # forces the batch to rewrite this entry despite the failure
        await db.commit()

        stats = await reproject_all(db, ctx=fx.CTX)
        assert stats.failed == 1 and stats.tombstoned == 0
        rows = {r.display_name: r for r in (await db.execute(select(DiscoveryEntry))).scalars().all()}
        assert rows["Bad"].tombstoned_at is None, "a failed projection is unknown, not gone"
        assert rows["Good"].content_hash != "stale", "the batch still committed the healthy entry"


@pytest.mark.asyncio
async def test_reproject_all_tombstones_stale_entries(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        listing = await fx.skill(db, owner)
        await reproject_all(db, ctx=fx.CTX)
        await db.delete(listing)
        await db.commit()

        stats = await reproject_all(db, ctx=fx.CTX)
        assert stats.tombstoned == 1
        row = (await db.execute(select(DiscoveryEntry))).scalar_one()
        assert row.tombstoned_at is not None


# ── Visibility ───────────────────────────────────────────────────────────


async def _visible(db, user) -> set[str]:
    stmt = select(DiscoveryEntry.display_name).where(visible_entries_predicate(user))
    return set((await db.execute(stmt)).scalars().all())


@pytest.mark.asyncio
async def test_visibility_mirrors_registry_rules(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        co_author = await fx.user(db)
        member = await fx.user(db)
        stranger = await fx.user(db)
        admin = await fx.user(db, role=UserRole.admin)
        reviewer = await fx.user(db, role=UserRole.reviewer)
        team = await fx.team_with_member(db, member)

        await fx.skill(db, owner, name="Public Approved")
        await fx.skill(db, owner, name="Public Pending", status=ListingStatus.pending, co_authors=[str(co_author.id)])
        await fx.skill(db, owner, name="Public Rejected", slug="public-rejected", status=ListingStatus.rejected)
        await fx.skill(db, owner, name="Public Archived", status=ListingStatus.archived)
        await fx.skill(db, owner, name="Personal Private", is_private=True)
        await fx.skill(db, owner, name="Team Private", is_private=True, team_id=team.id)
        await reproject_all(db, ctx=fx.CTX)

        assert await _visible(db, None) == {"Public Approved"}
        assert await _visible(db, stranger) == set()
        # ARD is narrower than registry browsing: a co-author outside the
        # listing's namespace cannot discover it, even when it is public.
        # A submitter removed from a teamspace cannot discover its items either.
        assert await _visible(db, owner) == {"Public Approved", "Public Pending", "Public Rejected", "Personal Private"}
        assert await _visible(db, co_author) == set()
        assert await _visible(db, member) == {"Team Private"}
        # Reviewers see their queue (pending), not other people's rejections or drafts.
        assert await _visible(db, reviewer) == {"Public Approved", "Public Pending"}
        assert await _visible(db, admin) == {
            "Public Approved",
            "Public Pending",
            "Public Rejected",
            "Personal Private",
            "Team Private",
        }


@pytest.mark.asyncio
async def test_archived_only_when_asked_for(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        await fx.skill(db, owner, name="Old Thing", status=ListingStatus.archived)
        await reproject_all(db, ctx=fx.CTX)
        stmt = select(DiscoveryEntry.display_name).where(
            visible_entries_predicate(owner, lifecycles=(DiscoveryLifecycle.archived,))
        )
        assert set((await db.execute(stmt)).scalars().all()) == {"Old Thing"}


# ── Search ───────────────────────────────────────────────────────────────


async def _seeded(db):
    owner = await fx.user(db)
    await fx.skill(db, owner, name="Security Review")
    await fx.skill(
        db,
        owner,
        name="Playwright Test Writer",
        slug="playwright-tests",
        description="Generates end-to-end Playwright tests for React pages.",
        task_type="testing",
        harnesses=["pi"],
    )
    await fx.mcp(db, owner)
    await fx.prompt(db, owner)
    await fx.hook(db, owner)
    await fx.sandbox(db, owner)
    await reproject_all(db, ctx=fx.CTX)
    return owner


@pytest.mark.asyncio
async def test_search_natural_language_finds_the_right_skill(sessions):
    async with sessions() as db:
        owner = await _seeded(db)
        page = await search_entries(db, text="review a pull request for authentication vulnerabilities", user=owner)
        assert page.results
        top = page.results[0]
        assert top.entry.display_name == "Security Review"
        assert 0 <= top.score <= 100
        assert "authentication" in top.matched_on or "review" in top.matched_on


@pytest.mark.asyncio
async def test_search_exact_name_ranks_first(sessions):
    async with sessions() as db:
        owner = await _seeded(db)
        page = await search_entries(db, text="GitHub", user=owner)
        assert page.results[0].entry.display_name == "GitHub"
        assert page.results[0].score >= 75


@pytest.mark.asyncio
async def test_search_type_filter_and_harness_filter(sessions):
    async with sessions() as db:
        owner = await _seeded(db)
        filters = SearchFilters.from_ard_filter({"type": ["application/ai-skill+md"]})
        page = await search_entries(db, text="tests review", filters=filters, user=owner)
        assert {r.entry.kind for r in page.results} == {DiscoveryKind.skill}

        filters = SearchFilters.from_ard_filter({"obs:supportedHarnesses": ["pi"]})
        page = await search_entries(db, text="tests", filters=filters, user=owner)
        assert [r.entry.display_name for r in page.results] == ["Playwright Test Writer"]


@pytest.mark.asyncio
async def test_search_legacy_type_alias_is_accepted(sessions):
    async with sessions() as db:
        owner = await _seeded(db)
        filters = SearchFilters.from_ard_filter({"type": "application/mcp-server"})
        page = await search_entries(db, text="pull requests", filters=filters, user=owner)
        assert [r.entry.kind for r in page.results] == [DiscoveryKind.mcp]


@pytest.mark.asyncio
async def test_search_nonsense_returns_nothing_and_empty_is_rejected(sessions):
    async with sessions() as db:
        owner = await _seeded(db)
        page = await search_entries(db, text="xyzzyplugh frobnicate", user=owner)
        assert page.results == []
        with pytest.raises(InvalidSearchRequestError):
            await search_entries(db, text="   ", user=owner)


@pytest.mark.asyncio
async def test_search_anonymous_requires_public_flag(sessions):
    async with sessions() as db:
        await _seeded(db)
        closed = await search_entries(db, text="security review", user=None)
        assert closed.results == []
        open_ = await search_entries(db, text="security review", user=None, public_search_enabled=True)
        assert open_.results


@pytest.mark.asyncio
async def test_search_pagination_is_stable_and_complete(sessions):
    async with sessions() as db:
        owner = await _seeded(db)
        first = await search_entries(db, text="acme", user=owner, page_size=2)
        assert len(first.results) == 2 and first.next_page_token
        seen = [r.entry.ard_identifier for r in first.results]
        token = first.next_page_token
        while token:
            page = await search_entries(db, text="acme", user=owner, page_size=2, page_token=token)
            seen.extend(r.entry.ard_identifier for r in page.results)
            token = page.next_page_token
        assert len(seen) == len(set(seen)) == first.total


def test_page_token_rejects_tampering():
    import base64

    token = encode_page_token(4, "abc")
    assert decode_page_token(token, "abc") == 4
    overflow = base64.urlsafe_b64encode(b'{"o": 1e999, "f": "abc"}').decode().rstrip("=")
    with pytest.raises(InvalidSearchRequestError):
        decode_page_token(overflow, "abc")
    with pytest.raises(InvalidSearchRequestError):
        decode_page_token(token, "other-query")
    with pytest.raises(InvalidSearchRequestError):
        decode_page_token("!!not-base64", "abc")


def test_type_and_kind_filters_are_separate_predicates():
    filters = SearchFilters.from_ard_filter({"type": ["application/mcp-server-card+json"], "obs:kind": ["skill"]})
    assert filters.media_types == ["application/mcp-server-card+json"]
    assert filters.kinds == [DiscoveryKind.skill], "type must not leak into the kind predicate"


@pytest.mark.asyncio
async def test_disjoint_type_and_kind_return_nothing(sessions):
    async with sessions() as db:
        owner = await _seeded(db)
        filters = SearchFilters.from_ard_filter({"type": ["application/mcp-server-card+json"], "obs:kind": ["skill"]})
        page = await search_entries(db, text="pull requests github", filters=filters, user=owner)
        assert page.results == []


@pytest.mark.asyncio
async def test_broad_query_ranks_the_best_match_even_among_many_candidates(sessions):
    """The candidate cap must not drop a top-relevance entry that merely has an old last_seen_at."""
    from datetime import UTC, datetime, timedelta

    from services.discovery import search as search_mod

    async with sessions() as db:
        old = datetime(2026, 1, 1, tzinfo=UTC)
        target = _entry("Widget Deployer", description="deploy widget")
        target.local_entity_id = uuid.uuid4()
        target.media_type = "application/ai-skill+md"
        target.version = "1.0.0"
        target.artifact_url = "https://x.test/a"
        target.publisher_domain = "x.test"
        target.visibility = DiscoveryVisibility.public
        target.search_document = "widget deployer deploy widget"
        target.last_seen_at = old
        db.add(target)
        for i in range(600):
            filler = _entry(f"Filler {i:03d}", description="widget adjacent")
            filler.local_entity_id = uuid.uuid4()
            filler.media_type = "application/ai-skill+md"
            filler.version = "1.0.0"
            filler.artifact_url = f"https://x.test/f{i}"
            filler.publisher_domain = "x.test"
            filler.visibility = DiscoveryVisibility.public
            filler.search_document = f"filler {i:03d} widget adjacent"
            filler.last_seen_at = old + timedelta(days=1 + i)
            db.add(filler)
        await db.commit()

        monkeypatch_limit = search_mod.CANDIDATE_LIMIT
        assert monkeypatch_limit > 600
        page = await search_entries(db, text="widget deployer", user=None, public_search_enabled=True, page_size=3)
        assert page.results[0].entry.display_name == "Widget Deployer"


def test_unknown_filter_term_is_rejected():
    with pytest.raises(InvalidSearchRequestError):
        SearchFilters.from_ard_filter({"trustManifest.attestations.type": ["SOC2"]})
    with pytest.raises(InvalidSearchRequestError):
        SearchFilters.from_ard_filter({"obs:kind": ["spaceship"]})


def _entry(name, description="", queries=(), caps=(), lifecycle=DiscoveryLifecycle.approved):
    return DiscoveryEntry(
        ard_identifier=f"urn:air:x.test:skill:{uuid.uuid5(uuid.NAMESPACE_DNS, name)}",
        kind=DiscoveryKind.skill,
        display_name=name,
        description=description,
        representative_queries=list(queries),
        capabilities=list(caps),
        tags=[],
        native_ref=f"acme/{name.lower().replace(' ', '-')}@1.0.0",
        lifecycle_status=lifecycle,
    )


def test_rank_entries_prefers_name_then_queries_then_description():
    entries = [
        _entry("Unrelated", description="mentions security once"),
        _entry("Notes", queries=["security review of a pull request"]),
        _entry("Security Review"),
    ]
    ranked = rank_entries("security review", entries)
    assert [r.entry.display_name for r in ranked] == ["Security Review", "Notes", "Unrelated"]
    assert ranked[0].score > ranked[1].score > ranked[2].score
    assert ranked[0].matched_on == ["security", "review"]


def test_rank_entries_is_deterministic_on_ties():
    entries = [_entry("Beta Tool"), _entry("Alpha Tool")]
    ranked = rank_entries("tool", entries)
    assert [r.entry.display_name for r in ranked] == ["Alpha Tool", "Beta Tool"]
    assert rank_entries("tool", list(reversed(entries)))[0].entry.display_name == "Alpha Tool"


def test_rank_entries_approved_before_pending_on_equal_score():
    ranked = rank_entries("tool", [_entry("A Tool", lifecycle=DiscoveryLifecycle.pending), _entry("B Tool")])
    assert ranked[0].entry.display_name == "B Tool"


def test_recommendation_breaks_ties_without_touching_score():
    plain, endorsed = _entry("A Tool"), _entry("B Tool")
    endorsed.raw_entry = {"obs:recommended": True}
    ranked = rank_entries("tool", [plain, endorsed])
    assert [r.entry.display_name for r in ranked] == ["B Tool", "A Tool"]
    assert ranked[0].score == ranked[1].score
    # A better match still wins over a recommended weaker one.
    weak = _entry("Notes", description="mentions a tool once")
    weak.raw_entry = {"obs:recommended": True}
    assert [r.entry.display_name for r in rank_entries("tool", [weak, plain])] == ["A Tool", "Notes"]
    for r in ranked:
        r.entry.visibility = DiscoveryVisibility.public
    assert search_result_item(ranked[0], source="s")["obs:recommended"] is True
    assert search_result_item(ranked[1], source="s")["obs:recommended"] is False


# ── Reprojection hook ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_session_hook_schedules_reprojection_for_changed_resources(sessions):
    captured: list[set] = []
    discovery_hooks.set_scheduler(captured.append)
    discovery_hooks.install()
    try:
        async with sessions() as db:
            owner = await fx.user(db)
            listing = await fx.skill(db, owner)
            await db.commit()
        assert captured and (DiscoveryKind.skill, listing.id) in captured[-1]

        captured.clear()
        async with sessions() as db:
            row = (await db.execute(select(type(listing)).where(type(listing).id == listing.id))).scalar_one()
            row.name = "Renamed"
            await db.commit()
        assert captured and captured[-1] == {(DiscoveryKind.skill, listing.id)}

        captured.clear()
        async with sessions() as db:
            await fx.user(db)  # untracked model: nothing scheduled
            await db.commit()
        assert captured == []

        captured.clear()
        async with sessions() as db:
            await fx.skill(db, owner, name="Rolled Back")
            await db.flush()
            await db.rollback()
        assert captured == []
    finally:
        discovery_hooks.set_scheduler(None)
        discovery_hooks.uninstall()


@pytest.mark.asyncio
async def test_session_hook_waits_for_the_outermost_commit(sessions):
    """A SAVEPOINT release also fires after_commit; scheduling there is too early.

    The inbox delivery path wraps its writes in begin_nested() inside the same
    transaction that created the listing, which is exactly how the first real
    submit slipped past the index.
    """
    captured: list[set] = []
    discovery_hooks.set_scheduler(captured.append)
    discovery_hooks.install()
    try:
        async with sessions() as db:
            owner = await fx.user(db)
            listing = await fx.skill(db, owner)
            async with db.begin_nested():
                await fx.user(db)  # unrelated work inside a savepoint
            assert captured == [], "savepoint release must not schedule"
            await db.commit()
        assert captured and (DiscoveryKind.skill, listing.id) in captured[-1]
    finally:
        discovery_hooks.set_scheduler(None)
        discovery_hooks.uninstall()


def test_collect_dirty_ignores_discovery_entries_and_unknown_types():
    entry = DiscoveryEntry(kind=DiscoveryKind.skill, local_entity_id=uuid.uuid4())
    assert discovery_hooks.collect_dirty([entry, object()]) == set()
