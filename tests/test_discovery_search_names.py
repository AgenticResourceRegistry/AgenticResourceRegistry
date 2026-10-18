# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Exact-name queries in discovery search (OBS-1455)."""

from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from models.discovery_entry import DiscoveryEntry
from services.discovery.projection import reproject_all
from services.discovery.search import rank_entries, search_entries
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


async def _seed_pr_verify(db):
    owner = await fx.user(db)
    for name, slug in [
        ("PR Verify", "pr-verify"),
        ("PR Verify GitHub", "pr-verify-github"),
        ("Unrelated Tool", "unrelated-tool"),
    ]:
        await fx.skill(db, owner, name=name, slug=slug, description="Checks a change.")
    await reproject_all(db, ctx=fx.CTX)
    return owner


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "pr-verify",
        "PR-Verify",
        "pr_verify",
        "PR_VERIFY",
        "pr verify",
        "acme/pr-verify",
        "acme/pr-verify@1.2.0",
        "pr-verify@1.2.0",
        "pr-verify@1.0.100",
        "  pr-verify  ",
    ],
)
async def test_exact_name_finds_and_ranks_the_resource_first(sessions, query):
    async with sessions() as db:
        owner = await _seed_pr_verify(db)
        page = await search_entries(db, text=query, user=owner)
        assert page.results, query
        assert page.results[0].entry.display_name == "PR Verify"
        assert page.results[0].score == 100
        names = await _names(db, query, owner)
        assert names[0] == "PR Verify"
        # Only a qualified query names the namespace; its other residents rank below the exact name.
        assert ("Unrelated Tool" in names) == query.startswith("acme/")
        assert "Unrelated Tool" not in names[:2]


@pytest.mark.asyncio
async def test_hyphenated_query_matches_name_stored_with_spaces(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        await fx.skill(db, owner, name="Release Notes Writer", slug="rn-writer", description="x")
        await fx.skill(db, owner, name="Notes Tool", slug="release-notes", description="x")
        await reproject_all(db, ctx=fx.CTX)
        assert await _names(db, "rn-writer", owner) == ["Release Notes Writer"]
        assert (await _names(db, "release-notes-writer", owner))[0] == "Release Notes Writer"
        scores = {}
        for query in ("release notes", "release-notes", "Release_Notes"):
            page = await search_entries(db, text=query, user=owner)
            scores[query] = {r.entry.display_name: r.score for r in page.results}["Release Notes Writer"]
        assert scores["release-notes"] == scores["release notes"] == scores["Release_Notes"]
        assert scores["release-notes"] >= 89


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["%", "_", "'", "\\", "--", "-_-", "😀"])
async def test_symbol_only_queries_return_nothing(sessions, query):
    async with sessions() as db:
        owner = await _seed_pr_verify(db)
        assert await _names(db, query, owner) == []


@pytest.mark.asyncio
async def test_sql_injection_text_is_literal_and_still_finds_the_name(sessions):
    async with sessions() as db:
        owner = await _seed_pr_verify(db)
        names = await _names(db, "pr-verify'; DROP TABLE x;--", owner)
        assert names[0] == "PR Verify"
        assert "Unrelated Tool" not in names
        assert await _names(db, "unrelated-tool", owner) == ["Unrelated Tool"]  # table still there


@pytest.mark.asyncio
async def test_wildcards_are_literal(sessions):
    async with sessions() as db:
        owner = await _seed_pr_verify(db)
        assert await _names(db, "pr%verify", owner) == ["PR Verify", "PR Verify GitHub"]
        assert await _names(db, "100%", owner) == []
        assert await _names(db, "pr_verify", owner) == ["PR Verify", "PR Verify GitHub"]  # "_" is a separator


@pytest.mark.asyncio
async def test_unicode_and_large_queries(sessions):
    async with sessions() as db:
        owner = await _seed_pr_verify(db)
        assert (await _names(db, "pr-verify café ünï", owner))[:2] == ["PR Verify", "PR Verify GitHub"]
        assert (await _names(db, "pr-verify " + "x" * 1900, owner))[0] == "PR Verify"


@pytest.mark.asyncio
async def test_other_users_do_not_see_private_exact_names(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        stranger = await fx.user(db)
        await fx.skill(db, owner, name="Secret Verify", slug="secret-verify", is_private=True)
        await reproject_all(db, ctx=fx.CTX)
        assert await _names(db, "secret-verify", owner) == ["Secret Verify"]
        assert await _names(db, "secret_verify", owner) == ["Secret Verify"]
        assert await _names(db, "secret-verify", stranger) == []
        assert await _names(db, "secret-verify", None, public_search_enabled=True) == []


@pytest.mark.asyncio
async def test_older_exact_match_survives_candidate_limit(sessions, monkeypatch):
    from datetime import UTC, datetime

    from services.discovery import search as search_mod

    monkeypatch.setattr(search_mod, "CANDIDATE_LIMIT", 20)
    async with sessions() as db:
        owner = await fx.user(db)
        await fx.skill(db, owner, name="PR Verify", slug="pr-verify", description="Checks a change.")
        for i in range(30):
            await fx.skill(db, owner, name=f"Prompt Helper {i}", slug=f"prompt-helper-{i}", description="x")
        await reproject_all(db, ctx=fx.CTX)
        for e in (await db.execute(select(DiscoveryEntry))).scalars().all():
            e.last_seen_at = datetime.now(UTC) - timedelta(days=0 if e.display_name != "PR Verify" else 5)
        await db.commit()
        page = await search_entries(db, text="pr-verify", user=owner)
        assert [r.entry.display_name for r in page.results] == ["PR Verify"]
        assert page.results[0].score == 100


@pytest.mark.asyncio
async def test_short_tokens_rank_without_error(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        await fx.skill(db, owner, name="CI Runner", slug="ci-runner", description="x")
        await fx.skill(db, owner, name="Other", slug="other", description="x")
        await reproject_all(db, ctx=fx.CTX)
        assert await _names(db, "ci", owner) == ["CI Runner"]
        assert await _names(db, "ci-runner", owner) == ["CI Runner"]


@pytest.mark.asyncio
async def test_token_with_a_thousand_hyphen_parts_does_not_blow_up_the_query(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        await fx.skill(db, owner, name="A Skill", slug="a-skill", description="x")
        await reproject_all(db, ctx=fx.CTX)
        # 1999 characters: under the query length limit, far over SQLite's expression depth of 1000.
        assert await _names(db, "-".join(["a"] * 1000), owner) == []
        assert await _names(db, "a-skill " + "-".join(["a"] * 900), owner) == ["A Skill"]  # the real name still ranks


@pytest.mark.parametrize(
    ("text", "tokens"),
    [
        ("@playwright/mcp", ["playwright"]),
        ("@modelcontextprotocol/server-github", ["modelcontextprotocol", "server-github"]),
        ("@scope/pkg@1.2.3", ["scope", "pkg"]),
        ("user@example.com", ["user", "example", "com"]),
        ("find user@example.com tool", ["user", "example", "com", "tool"]),
        ("slug@1.0.0", ["slug"]),
        ("acme/slug@1.0.0", ["acme", "slug"]),
        ("acme/slug@v2", ["acme", "slug"]),
        ("slug@1.2.0rc1", ["slug"]),
        ("slug@1password", ["slug", "1password"]),
        ("user@123.com", ["user", "123", "com"]),
        ("slug@1.2.0-beta.1", ["slug"]),
        ("slug@latest", ["slug"]),
        ("acme/slug@beta tool", ["acme", "slug", "tool"]),
        ("@scope/pkg@next", ["scope", "pkg"]),
        ("@latest/pkg", ["latest", "pkg"]),
        ("pr-verify@1.0,verify-pr", ["pr-verify", "verify-pr"]),
        ("pr-verify@1.0.0 verify-pr", ["pr-verify", "verify-pr"]),
    ],
)
def test_query_tokens_keep_scoped_names_and_strip_only_versions(text, tokens):
    from services.discovery.search import query_tokens

    assert query_tokens(text) == tokens


@pytest.mark.asyncio
async def test_scoped_npm_name_is_searchable(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        await fx.skill(db, owner, name="Playwright MCP", slug="playwright-mcp", description="x")
        await reproject_all(db, ctx=fx.CTX)
        assert await _names(db, "@playwright/mcp", owner) == ["Playwright MCP"]


@pytest.mark.asyncio
async def test_bare_namespace_is_a_tag_match_not_a_name_match(sessions):
    async with sessions() as db:
        owner = await _seed_pr_verify(db)  # all in namespace "acme"
        # A bare namespace still matches through the entries' namespace tag, but never as a name: nothing
        # reaches the 100 that a resource actually called "acme" would get.
        bare = await search_entries(db, text="acme", user=owner)
        assert bare.results and max(r.score for r in bare.results) < 100
        qualified = await search_entries(db, text="acme/pr-verify", user=owner)
        assert qualified.results[0].entry.display_name == "PR Verify"
        assert qualified.results[0].score == 100


def test_rank_entries_is_pure_for_empty_input():
    assert rank_entries("pr-verify", []) == []


# ── round 2 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_exact_slug_ending_in_s_ranks_as_full_name_match(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        await fx.skill(db, owner, name="Aardvark", slug="pr-review-gen", description="x")
        await fx.skill(db, owner, name="PR Reviews", slug="pr-reviews", description="x")
        await fx.skill(db, owner, name="GitHub Actions", slug="github-actions", description="x")
        await reproject_all(db, ctx=fx.CTX)
        page = await search_entries(db, text="pr-reviews", user=owner)
        assert (page.results[0].entry.display_name, page.results[0].score) == ("PR Reviews", 100)
        page = await search_entries(db, text="github-actions", user=owner)
        assert (page.results[0].entry.display_name, page.results[0].score) == ("GitHub Actions", 100)


@pytest.mark.asyncio
async def test_underscore_in_stored_name_matches_hyphenated_query(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        await fx.skill(db, owner, name="pr_verify", slug="zzz", description="x")
        await fx.skill(db, owner, name="Aaa Other", slug="aaa-other", description="pr verify things")
        await reproject_all(db, ctx=fx.CTX)
        page = await search_entries(db, text="pr-verify", user=owner)
        assert page.results[0].entry.display_name == "pr_verify"
        assert page.results[0].score == 100


@pytest.mark.asyncio
async def test_slash_in_prose_does_not_make_namespace_a_name(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        await fx.skill(db, owner, name="Alpha", slug="alpha", description="x")
        await fx.skill(db, owner, name="Beta", slug="beta", description="x")
        await reproject_all(db, ctx=fx.CTX)
        # "acme" is only the namespace; "and/or" is not a reference to it.
        bare = {r.entry.display_name: r.score for r in (await search_entries(db, text="acme", user=owner)).results}
        for query in ("and/or acme", "read/acme"):
            page = await search_entries(db, text=query, user=owner)
            assert all(r.score <= bare[r.entry.display_name] for r in page.results), query
        page = await search_entries(db, text="read/acme alpha", user=owner)
        assert page.results[0].entry.display_name == "Alpha"
        assert page.results[0].score < 60
        # A real reference still names its own namespace.
        assert set(await _names(db, "acme/alpha", owner)) == {"Alpha", "Beta"}


def test_hyphen_run_found_after_more_than_eight_leading_words():
    from types import SimpleNamespace

    from services.discovery.search import _fields

    entry = SimpleNamespace(
        display_name="One Two Three Four Five Six Seven Eight Nine Release Notes",
        native_ref="acme/long@1.0.0",
        representative_queries=[],
        capabilities=[],
        tags=[],
        description="",
    )
    assert "release-notes" in _fields(entry)["name"]
