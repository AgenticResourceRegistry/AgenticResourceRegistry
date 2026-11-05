# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The --harness filter in discovery search (OBS-1455)."""

from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from models.discovery_entry import DiscoveryEntry, DiscoveryKind, DiscoverySourceKind
from services.discovery.identity import MEDIA_TYPE_A2A
from services.discovery.projection import reproject_all
from services.discovery.search import SearchFilters, search_entries
from tests import discovery_support as fx


@pytest_asyncio.fixture()
async def sessions():
    engine = fx.make_engine()
    try:
        yield await fx.create_schema(engine)
    finally:
        await engine.dispose()


async def _names(db, text, user, **kw):
    page = await search_entries(db, text=text, user=user, page_size=50, **kw)
    return [r.entry.display_name for r in page.results]


async def _seed_all_kinds(db):
    owner = await fx.user(db)
    skill = await fx.skill(db, owner, name="Declared Skill", slug="decl-skill", harnesses=["cursor"])
    await fx.skill(db, owner, name="Undeclared Skill", slug="undecl-skill", harnesses=[])
    await fx.skill(db, owner, name="Other Harness Skill", slug="other-skill", harnesses=["pi"])
    await fx.mcp(db, owner)
    await fx.prompt(db, owner)
    await fx.hook(db, owner)
    await fx.sandbox(db, owner)
    await fx.agent(db, owner, components=[("skill", skill.id, "Declared Skill")])
    await reproject_all(db, ctx=fx.CTX)
    return owner


async def _clear_harnesses(db, *, keep: set[str] = frozenset()):
    for entry in (await db.execute(select(DiscoveryEntry))).scalars().all():
        if entry.display_name not in keep:
            entry.supported_harnesses = []
    await db.commit()


@pytest.mark.asyncio
async def test_harness_filter_excludes_undeclared_of_every_kind(sessions):
    async with sessions() as db:
        owner = await _seed_all_kinds(db)
        await _clear_harnesses(db, keep={"Declared Skill"})
        filters = SearchFilters.from_ard_filter({"obs:supportedHarnesses": ["cursor"]})
        page = await search_entries(db, text="review release lint python github skill", filters=filters, user=owner)
        assert [r.entry.display_name for r in page.results] == ["Declared Skill"]

        unfiltered = await search_entries(db, text="review release lint python github skill", user=owner, page_size=50)
        assert {r.entry.kind for r in unfiltered.results} >= {
            DiscoveryKind.skill,
            DiscoveryKind.mcp,
            DiscoveryKind.prompt,
            DiscoveryKind.hook,
            DiscoveryKind.sandbox,
            DiscoveryKind.agent,
        }


@pytest.mark.asyncio
async def test_harness_filter_keeps_only_declared_matches_and_any_of_several(sessions):
    async with sessions() as db:
        owner = await _seed_all_kinds(db)
        one = SearchFilters.from_ard_filter({"obs:supportedHarnesses": ["pi"]})
        assert await _names(db, "skill", owner, filters=one) == ["Other Harness Skill"]
        both = SearchFilters.from_ard_filter({"obs:supportedHarnesses": ["pi", "cursor"]})
        assert sorted(await _names(db, "skill", owner, filters=both)) == ["Declared Skill", "Other Harness Skill"]
        none = SearchFilters.from_ard_filter({"obs:supportedHarnesses": ["goose"]})
        assert await _names(db, "skill", owner, filters=none) == []


@pytest.mark.asyncio
async def test_harness_filter_keeps_remote_a2a_agents(sessions):
    async with sessions() as db:
        owner = await _seed_all_kinds(db)
        await _clear_harnesses(db)
        remote = (
            await db.execute(select(DiscoveryEntry).where(DiscoveryEntry.display_name == "Undeclared Skill"))
        ).scalar_one()
        remote.source_kind = DiscoverySourceKind.imported
        remote.media_type = MEDIA_TYPE_A2A
        await db.commit()
        filters = SearchFilters.from_ard_filter({"obs:supportedHarnesses": ["cursor"]})
        assert await _names(db, "undeclared", owner, filters=filters) == ["Undeclared Skill"]
        assert await _names(db, "other harness", owner, filters=filters) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["reviewer", "admin"])
async def test_harness_filter_applies_to_privileged_users_too(sessions, role):
    from models.user import UserRole

    async with sessions() as db:
        await _seed_all_kinds(db)
        privileged = await fx.user(db, UserRole(role))
        filters = SearchFilters.from_ard_filter({"obs:supportedHarnesses": ["cursor"]})
        page = await search_entries(db, text="skill", filters=filters, user=privileged)
        assert all("cursor" in (r.entry.supported_harnesses or []) for r in page.results)


@pytest.mark.asyncio
async def test_undeclared_harness_excluded_from_search_but_usable(sessions):
    from services.discovery.serialize import availability

    async with sessions() as db:
        owner = await fx.user(db)
        await fx.skill(db, owner, name="PR Verify", slug="pr-verify", harnesses=[])
        await reproject_all(db, ctx=fx.CTX)
        entry = (await db.execute(select(DiscoveryEntry))).scalar_one()
        filters = SearchFilters.from_ard_filter({"obs:supportedHarnesses": ["pi"]})
        assert await _names(db, "pr verify", owner, filters=filters) == []
        assert await _names(db, "pr verify", owner) == ["PR Verify"]
        # Policy: use/inspect only refuse resources declaring OTHER harnesses.
        assert availability(entry, "pi") != "unsupported-in-harness"
        entry.supported_harnesses = ["cursor"]
        assert availability(entry, "pi") == "unsupported-in-harness"


@pytest.mark.asyncio
async def test_harness_filter_survives_candidate_limit(sessions, monkeypatch):
    import services.discovery.search as search_mod

    monkeypatch.setattr(search_mod, "CANDIDATE_LIMIT", 20)
    async with sessions() as db:
        owner = await fx.user(db)
        await fx.skill(db, owner, name="Review Declared", slug="review-declared", harnesses=["pi"])
        for i in range(30):
            await fx.skill(db, owner, name=f"Review Other {i}", slug=f"review-other-{i}", harnesses=[])
        await reproject_all(db, ctx=fx.CTX)
        # Make the declared entry the oldest so the limit would cut it.
        declared = (
            await db.execute(select(DiscoveryEntry).where(DiscoveryEntry.display_name == "Review Declared"))
        ).scalar_one()
        declared.last_seen_at = declared.last_seen_at - timedelta(days=30)
        await db.commit()
        filters = SearchFilters.from_ard_filter({"obs:supportedHarnesses": ["pi"]})
        assert await _names(db, "review", owner, filters=filters) == ["Review Declared"]
        assert len(await _names(db, "review", owner)) == 20


@pytest.mark.asyncio
async def test_harness_filter_matches_whole_ids_only_and_exempts_a2a(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        await fx.skill(db, owner, name="Zeta Pi", slug="zeta-pi", harnesses=["goose", "pi"])
        await fx.skill(db, owner, name="Zeta Copilot", slug="zeta-copilot", harnesses=["copilot"])
        await fx.skill(db, owner, name="Zeta Extra", slug="zeta-extra", harnesses=["pi-extra"])
        await fx.skill(db, owner, name="Zeta Cursor", slug="zeta-cursor", harnesses=["cursor"])
        await fx.skill(db, owner, name="Zeta Remote", slug="zeta-remote", harnesses=[])
        await reproject_all(db, ctx=fx.CTX)
        remote = (
            await db.execute(select(DiscoveryEntry).where(DiscoveryEntry.display_name == "Zeta Remote"))
        ).scalar_one()
        remote.source_kind = DiscoverySourceKind.imported
        remote.media_type = MEDIA_TYPE_A2A
        await db.commit()

        def f(*ids):
            return SearchFilters.from_ard_filter({"obs:supportedHarnesses": list(ids)})

        assert sorted(await _names(db, "zeta", owner, filters=f("pi"))) == ["Zeta Pi", "Zeta Remote"]
        assert sorted(await _names(db, "zeta", owner, filters=f("pi", "cursor"))) == [
            "Zeta Cursor",
            "Zeta Pi",
            "Zeta Remote",
        ]
        assert sorted(await _names(db, "zeta", owner, filters=f("pi-extra"))) == ["Zeta Extra", "Zeta Remote"]
        # LIKE wildcards in a harness id are literal.
        assert await _names(db, "zeta", owner, filters=f("%")) == ["Zeta Remote"]
        assert await _names(db, "zeta", owner, filters=f("p_")) == ["Zeta Remote"]


@pytest.mark.asyncio
async def test_harness_filter_matches_case_exactly_on_every_dialect(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        await fx.skill(db, owner, name="Zeta Mixed", slug="zeta-mixed", harnesses=["Cursor"])
        await reproject_all(db, ctx=fx.CTX)
        filters = SearchFilters.from_ard_filter({"obs:supportedHarnesses": ["cursor"]})
        assert await _names(db, "zeta", owner, filters=filters) == []
