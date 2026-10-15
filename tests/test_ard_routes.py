# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ARD endpoints, artifact endpoint, and conformance against the vendored spec tool."""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.pool import NullPool

import services.dynamic_settings as ds
from api.deps import get_db
from api.ratelimit import limiter
from api.routes import ard, artifacts
from models.discovery_entry import DiscoveryKind
from models.mcp import ListingStatus
from models.skill import SkillListing, SkillVersion
from models.team import TeamMembership
from models.user import UserRole
from services.discovery.projection import project_entity, reproject_all
from services.discovery.serialize import availability
from tests import discovery_support as fx

VENDOR = Path(__file__).resolve().parents[1] / "observal-server" / "vendor" / "ard"
CONFORMANCE_TOOL = VENDOR / "conformance" / "bin" / "conformance-test"
PUBLIC_URL = "https://observal.example.com"


@pytest.fixture(autouse=True)
def _disable_rate_limits():
    enabled = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = enabled


@pytest.fixture()
def settings(monkeypatch):
    """Patch dynamic settings: public URL and publisher identity are mutable per test."""
    state = {"public": False, "public_url": PUBLIC_URL, "publisher_domain": ""}

    async def fake_get(key, default=None):
        if key == "deployment.public_url":
            return state["public_url"]
        if key == "discovery.publisher_domain":
            return state["publisher_domain"]
        return default or ""

    async def fake_get_bool(key, default=None):
        if key == ard.PUBLIC_SEARCH_SETTING:
            return state["public"]
        return bool(default)

    monkeypatch.setattr(ds, "get", fake_get)
    monkeypatch.setattr(ds, "get_bool", fake_get_bool)
    return state


@pytest_asyncio.fixture()
async def sessions():
    engine = fx.make_engine()
    try:
        yield await fx.create_schema(engine)
    finally:
        await engine.dispose()


def _app(sessions, user=None) -> FastAPI:
    app = FastAPI()
    app.include_router(ard.router)
    app.include_router(artifacts.router)

    async def db_override():
        async with sessions() as db:
            yield db

    async def user_override():
        return user

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[ard.discovery_user] = user_override
    return app


async def _seed(sessions):
    async with sessions() as db:
        owner = await fx.user(db)
        stranger = await fx.user(db)
        skill = await fx.skill(db, owner)
        await fx.skill(db, owner, name="Draft Skill", slug="draft-skill", status=ListingStatus.pending)
        await fx.skill(db, owner, name="Secret Skill", slug="secret-skill", is_private=True)
        mcp = await fx.mcp(db, owner)
        await fx.prompt(db, owner)
        await fx.hook(db, owner)
        await fx.sandbox(db, owner)
        await fx.agent(db, owner, components=[("skill", skill.id, "Security Review"), ("mcp", mcp.id, "GitHub")])
        await reproject_all(db, ctx=fx.CTX)
        return owner, stranger, skill


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── Search ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_public_fork_links_and_search_facet_follow_live_visibility(sessions, settings):
    owner, _, source = await _seed(sessions)
    settings["public"] = True
    async with sessions() as db:
        fork = await fx.skill(db, owner, name="Forked Review", slug="forked-review")
        fork.forked_from_id = source.id
        fork.forked_from_version_id = source.latest_version_id
        fork.forked_from_ref = "do-not-serialize/secret@9.9.9"
        await db.flush()
        await reproject_all(db, ctx=fx.CTX)
        fork_id, source_id = fork.id, source.id
    urn = f"urn:air:observal.example.com:skill:{source_id}"
    fork_urn = f"urn:air:observal.example.com:skill:{fork_id}"
    search_body = {"query": {"text": "review", "filter": {"obs:forksOf": urn}}}
    async with _client(_app(sessions)) as client:
        found = await client.post("/api/v1/ard/search", json=search_body)
        assert found.status_code == 200, found.text
        assert [row["identifier"] for row in found.json()["results"]] == [fork_urn]
        assert found.json()["results"][0]["obs:forkedFrom"] == urn
        invalid = await client.post(
            "/api/v1/ard/search",
            json={"query": {"text": "review", "filter": {"obs:forksOf": [urn, urn]}}},
        )
        assert invalid.status_code == 400 and invalid.json()["errorCode"] == "INVALID_ARGUMENT"
        assert (await client.get(f"/api/v1/ard/entries/{fork_urn}")).json()["obs:forkedFrom"] == urn
        listing = await client.get("/api/v1/ard/agents")
        assert next(row for row in listing.json()["items"] if row["identifier"] == fork_urn)["obs:forkedFrom"] == urn
        manifest = await client.get("/.well-known/ard.json")
        assert next(row for row in manifest.json()["entries"] if row["identifier"] == fork_urn)["obs:forkedFrom"] == urn
        assert "do-not-serialize" not in str((found.json(), listing.json(), manifest.json()))

        # No reprojection: the native row must win over a stale public ARD entry.
        async with sessions() as db:
            native = await db.get(type(source), source_id)
            native.is_private = True
            await db.commit()
        assert (await client.post("/api/v1/ard/search", json=search_body)).json()["results"] == []
        detail = await client.get(f"/api/v1/ard/entries/{fork_urn}")
        assert "obs:forkedFrom" not in detail.json()
        assert "do-not-serialize" not in detail.text

        async with sessions() as db:
            native = await db.get(type(source), source_id)
            native.is_private = False
            native_fork = await db.get(type(source), fork_id)
            native_fork.is_private = True
            await db.commit()
        assert (await client.post("/api/v1/ard/search", json=search_body)).json()["results"] == []
        assert "obs:forkedFrom" not in (await client.get(f"/api/v1/ard/entries/{fork_urn}")).json()


@pytest.mark.asyncio
async def test_search_response_shape(sessions, settings):
    owner, _, skill = await _seed(sessions)
    async with _client(_app(sessions, owner)) as client:
        resp = await client.post(
            "/api/v1/ard/search",
            json={"query": {"text": "review pull request authentication"}, "federation": "none", "pageSize": 3},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) <= {"results", "pageToken", "referrals"}
    assert body["results"]
    top = body["results"][0]
    assert top["identifier"] == f"urn:air:observal.example.com:skill:{skill.id}"
    assert isinstance(top["score"], int) and 0 <= top["score"] <= 100
    assert top["source"] == f"{PUBLIC_URL}/api/v1/ard"
    assert top["type"] == "application/ai-skill+md"
    assert top["obs:approval"] == "approved"
    assert top["obs:availability"] == "now"
    assert top["matchedOn"]
    assert "SHOULD-NEVER-LEAK" not in resp.text


@pytest.mark.asyncio
async def test_search_referrals_mode_returns_an_explicit_empty_list(sessions, settings):
    owner, _, _ = await _seed(sessions)
    async with _client(_app(sessions, owner)) as client:
        resp = await client.post("/api/v1/ard/search", json={"query": {"text": "review"}, "federation": "referrals"})
    assert resp.status_code == 200
    assert resp.json()["referrals"] == []


@pytest.mark.asyncio
async def test_search_requires_text(sessions, settings):
    owner, _, _ = await _seed(sessions)
    async with _client(_app(sessions, owner)) as client:
        resp = await client.post(
            "/api/v1/ard/search", json={"query": {"filter": {"type": ["application/ai-skill+md"]}}}
        )
    assert resp.status_code == 400
    assert resp.json() == {"errorCode": "INVALID_ARGUMENT", "message": "query.text is required"}


@pytest.mark.asyncio
async def test_search_rejects_unknown_filter_and_bad_page_token(sessions, settings):
    owner, _, _ = await _seed(sessions)
    async with _client(_app(sessions, owner)) as client:
        resp = await client.post("/api/v1/ard/search", json={"query": {"text": "x", "filter": {"nope": ["y"]}}})
        assert resp.status_code == 400 and resp.json()["errorCode"] == "INVALID_ARGUMENT"
        resp = await client.post("/api/v1/ard/search", json={"query": {"text": "review"}, "pageToken": "garbage"})
        assert resp.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"query": {"text": "review"}, "pageSize": 0},
        {"query": {"text": "review"}, "pageSize": 1000},
        {"query": {"text": "review"}, "federation": "everything"},
        {"query": "review"},
        ["not", "an", "object"],
    ],
)
async def test_search_schema_errors_use_the_ard_envelope(sessions, settings, body):
    """Appendix B: every 400 carries {errorCode, message}, including body validation failures."""
    owner, _, _ = await _seed(sessions)
    async with _client(_app(sessions, owner)) as client:
        resp = await client.post("/api/v1/ard/search", json=body)
    assert resp.status_code == 400
    assert set(resp.json()) == {"errorCode", "message"}
    assert resp.json()["errorCode"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "message"),
    [
        # "query" is both a request location and the search body's field, so only loc[0] may be stripped.
        ({}, "query: Field required"),
        ({"query": "review"}, "query: Input should be a valid dictionary or object to extract fields from"),
        ({"query": {"text": 5}}, "query.text: Input should be a valid string"),
        ({"query": {"text": "review"}, "pageSize": 0}, "pageSize: Input should be greater than or equal to 1"),
        (
            {"query": {"text": "review"}, "federation": "everything"},
            "federation: Input should be 'auto', 'referrals' or 'none'",
        ),
    ],
)
async def test_search_schema_error_message_names_the_field(sessions, settings, body, message):
    owner, _, _ = await _seed(sessions)
    async with _client(_app(sessions, owner)) as client:
        resp = await client.post("/api/v1/ard/search", json=body)
    assert resp.status_code == 400
    assert resp.json() == {"errorCode": "INVALID_ARGUMENT", "message": message}


@pytest.mark.asyncio
async def test_search_malformed_json_is_named_as_such(sessions, settings):
    owner, _, _ = await _seed(sessions)
    async with _client(_app(sessions, owner)) as client:
        resp = await client.post(
            "/api/v1/ard/search", content=b"{not json", headers={"content-type": "application/json"}
        )
    assert resp.status_code == 400
    assert resp.json() == {"errorCode": "INVALID_ARGUMENT", "message": "Request body is not valid JSON"}


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{"pageSize": "0"}, {"pageSize": "1000"}, {"pageSize": "many"}])
async def test_list_schema_errors_use_the_ard_envelope(sessions, settings, params):
    owner, _, _ = await _seed(sessions)
    async with _client(_app(sessions, owner)) as client:
        resp = await client.get("/api/v1/ard/agents", params=params)
    assert resp.status_code == 400
    assert set(resp.json()) == {"errorCode", "message"}
    assert resp.json()["errorCode"] == "INVALID_ARGUMENT"
    assert "pageSize" in resp.json()["message"]


def test_openapi_documents_400_envelope_not_422_for_ard_routes():
    app = FastAPI()
    app.include_router(ard.router)

    @app.get("/api/v1/other")
    async def other(limit: int = 10):
        return {"limit": limit}

    ard.document_ard_validation_errors(app)
    paths = app.openapi()["paths"]

    for path, method in [
        ("/api/v1/ard/search", "post"),
        ("/api/v1/ard/agents", "get"),
        ("/api/v1/ard/explore", "post"),
    ]:
        responses = paths[path][method]["responses"]
        assert "422" not in responses, path
        assert responses["400"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/ArdError"}
    assert "422" not in paths["/api/v1/ard/entries/{identifier}"]["get"]["responses"]
    # Other routers keep FastAPI's default validation response.
    assert "422" in paths["/api/v1/other"]["get"]["responses"]
    assert app.openapi() is app.openapi()


@pytest.mark.asyncio
async def test_search_anonymous_is_closed_until_public_search_is_on(sessions, settings):
    await _seed(sessions)
    async with _client(_app(sessions, None)) as client:
        closed = await client.post("/api/v1/ard/search", json={"query": {"text": "security review"}})
        assert closed.status_code == 200 and closed.json()["results"] == []
        settings["public"] = True
        opened = await client.post("/api/v1/ard/search", json={"query": {"text": "security review"}})
        names = [r["displayName"] for r in opened.json()["results"]]
        assert "Security Review" in names
        assert "Secret Skill" not in names and "Draft Skill" not in names


@pytest.mark.asyncio
async def test_search_owner_sees_own_pending_and_private(sessions, settings):
    owner, stranger, _ = await _seed(sessions)
    async with _client(_app(sessions, owner)) as client:
        resp = await client.post("/api/v1/ard/search", json={"query": {"text": "skill"}, "pageSize": 20})
    names = {r["displayName"] for r in resp.json()["results"]}
    assert {"Draft Skill", "Secret Skill"} <= names
    async with _client(_app(sessions, stranger)) as client:
        resp = await client.post("/api/v1/ard/search", json={"query": {"text": "skill"}, "pageSize": 20})
    names = {r["displayName"] for r in resp.json()["results"]}
    assert not ({"Draft Skill", "Secret Skill"} & names)


@pytest.mark.asyncio
async def test_discovery_scopes_all_reads_to_personal_and_joined_teamspaces(sessions, settings):
    async with sessions() as db:
        alice = await fx.user(db)
        bob = await fx.user(db)
        admin = await fx.user(db, role=UserRole.admin)
        reviewer = await fx.user(db, role=UserRole.reviewer)
        first_team = await fx.team_with_member(db, alice)
        second_team = await fx.team_with_member(db, alice)
        foreign_team = await fx.team_with_member(db, bob)
        db.add_all(
            [
                TeamMembership(team_id=first_team.id, user_id=bob.id),
                TeamMembership(team_id=second_team.id, user_id=bob.id),
            ]
        )

        async def add(name, owner, *, team=None, private=False, status=ListingStatus.approved):
            listing = await fx.skill(
                db, owner, name=name, team_id=team.id if team else None, is_private=private, status=status
            )
            listing.namespace = team.handle if team else owner.username
            return listing

        own = await add("Scope Personal", alice)
        own_pending = await add("Scope Personal Pending", alice, status=ListingStatus.pending)
        joined_public = await add("Scope Joined Public", bob, team=first_team)
        joined_private = await add("Scope Joined Private", bob, team=first_team, private=True)
        joined_own = await add("Scope Joined Submitted", alice, team=first_team, private=True)
        second_public = await add("Scope Second Public", bob, team=second_team)
        second_private = await add("Scope Second Private", bob, team=second_team, private=True)
        foreign_public = await add("Scope Foreign Public", bob, team=foreign_team)
        foreign_private = await add("Scope Foreign Private", bob, team=foreign_team, private=True)
        other_personal = await add("Scope Other Personal", bob)
        await reproject_all(db, ctx=fx.CTX)

    allowed = {
        own.name,
        joined_public.name,
        joined_private.name,
        joined_own.name,
        second_public.name,
        second_private.name,
    }
    urn = f"urn:air:observal.example.com:skill:{foreign_public.id}"
    foreign_artifact = f"/api/v1/artifacts/skill/{foreign_public.id}/1.2.0"
    joined_artifact = f"/api/v1/artifacts/skill/{joined_public.id}/1.2.0"
    async with _client(_app(sessions, alice)) as client:
        results = await client.post("/api/v1/ard/search", json={"query": {"text": "scope"}, "pageSize": 30})
        assert {r["displayName"] for r in results.json()["results"]} == allowed | {own_pending.name}
        listed = await client.get("/api/v1/ard/agents", params={"pageSize": 30})
        assert {r["displayName"] for r in listed.json()["items"]} == allowed | {own_pending.name}
        assert (await client.get(f"/api/v1/ard/entries/{urn}")).status_code == 404
        assert (await client.get(foreign_artifact)).status_code == 404
        assert (await client.get(joined_artifact)).status_code == 200
        assert (
            await client.get(f"/api/v1/ard/entries/urn:air:observal.example.com:skill:{joined_private.id}")
        ).status_code == 200

    # Revocation must take effect on the next request, even for a public team listing.
    async with sessions() as db:
        membership = (
            await db.execute(
                select(TeamMembership).where(
                    TeamMembership.user_id == alice.id, TeamMembership.team_id == first_team.id
                )
            )
        ).scalar_one()
        await db.delete(membership)
        await db.commit()
    async with _client(_app(sessions, alice)) as client:
        results = await client.post("/api/v1/ard/search", json={"query": {"text": "scope"}, "pageSize": 30})
        assert {r["displayName"] for r in results.json()["results"]} == {
            own.name,
            own_pending.name,
            second_public.name,
            second_private.name,
        }
        assert (await client.get(joined_artifact)).status_code == 404

    settings["public"] = True
    async with _client(_app(sessions, alice)) as client:
        assert (await client.get(foreign_artifact)).status_code == 404
        assert (await client.get(f"/api/v1/ard/entries/{urn}")).status_code == 404
        results = await client.post("/api/v1/ard/search", json={"query": {"text": "scope"}, "pageSize": 30})
        assert foreign_public.name not in {r["displayName"] for r in results.json()["results"]}
    async with _client(_app(sessions, None)) as client:
        results = await client.post("/api/v1/ard/search", json={"query": {"text": "scope"}, "pageSize": 30})
        names = {r["displayName"] for r in results.json()["results"]}
        assert {foreign_public.name, other_personal.name} <= names
        assert foreign_private.name not in names
    async with _client(_app(sessions, reviewer)) as client:
        assert (await client.get(f"/api/v1/ard/entries/{urn}")).status_code == 200
        assert (
            await client.get(f"/api/v1/ard/entries/urn:air:observal.example.com:skill:{foreign_private.id}")
        ).status_code == 404
    async with _client(_app(sessions, admin)) as client:
        assert (
            await client.get(f"/api/v1/ard/entries/urn:air:observal.example.com:skill:{foreign_private.id}")
        ).status_code == 200


@pytest.mark.asyncio
async def test_search_harness_filter_marks_availability(sessions, settings):
    owner, _, _ = await _seed(sessions)
    async with _client(_app(sessions, owner)) as client:
        resp = await client.post(
            "/api/v1/ard/search",
            json={"query": {"text": "release notes", "filter": {"obs:supportedHarnesses": ["copilot-cli"]}}},
        )
    results = resp.json()["results"]
    assert results and results[0]["displayName"] == "Release Notes"
    assert results[0]["obs:availability"] == "now"


@pytest.mark.asyncio
async def test_explore_is_501(sessions, settings):
    async with _client(_app(sessions, None)) as client:
        resp = await client.post("/api/v1/ard/explore", json={"query": {"text": "x"}, "resultType": {"facets": []}})
    assert resp.status_code == 501
    assert resp.json()["errorCode"] == "NOT_IMPLEMENTED"


# ── List ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_is_deterministic_and_filterable(sessions, settings):
    owner, _, _ = await _seed(sessions)
    async with _client(_app(sessions, owner)) as client:
        resp = await client.get("/api/v1/ard/agents", params={"pageSize": 3})
        body = resp.json()
        assert resp.status_code == 200
        assert [i["displayName"] for i in body["items"]] == sorted(i["displayName"] for i in body["items"])
        assert body["total"] == 8 and body["pageToken"]

        rest = await client.get("/api/v1/ard/agents", params={"pageSize": 3, "pageToken": body["pageToken"]})
        assert rest.status_code == 200 and len(rest.json()["items"]) == 3

        filtered = await client.get(
            "/api/v1/ard/agents", params={"filter": "type = 'application/mcp-server-card+json'"}
        )
        assert [i["displayName"] for i in filtered.json()["items"]] == ["GitHub"]

        multiple_names = await client.get(
            "/api/v1/ard/agents", params={"filter": "displayName = 'Security Review',GitHub"}
        )
        assert {i["displayName"] for i in multiple_names.json()["items"]} == {"GitHub", "Security Review"}

        ordered = await client.get("/api/v1/ard/agents", params={"orderBy": "displayName DESC", "pageSize": 1})
        assert ordered.json()["items"][0]["displayName"] == "Security Review"

        bad = await client.get("/api/v1/ard/agents", params={"filter": "colour = 'blue'"})
        assert bad.status_code == 400
        empty = await client.get("/api/v1/ard/agents", params={"filter": "displayName=,,,"})
        assert empty.status_code == 400 and empty.json()["errorCode"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_list_display_name_treats_like_wildcards_as_literals(sessions, settings):
    owner, _, _ = await _seed(sessions)
    async with sessions() as db:
        await fx.skill(db, owner, name="Score_100%")
        await reproject_all(db, ctx=fx.CTX)
    async with _client(_app(sessions, owner)) as client:
        for name in ("%", "_"):
            resp = await client.get("/api/v1/ard/agents", params={"filter": f"displayName = '{name}'"})
            assert resp.status_code == 200
            assert [item["displayName"] for item in resp.json()["items"]] == ["Score_100%"]
            assert resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_list_anonymous_closed(sessions, settings):
    await _seed(sessions)
    async with _client(_app(sessions, None)) as client:
        resp = await client.get("/api/v1/ard/agents")
    assert resp.json() == {"items": [], "total": 0}


# ── Entry by identifier ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_entry_by_identifier_and_legacy_prefix(sessions, settings):
    owner, stranger, skill = await _seed(sessions)
    urn = f"urn:air:observal.example.com:skill:{skill.id}"
    async with _client(_app(sessions, owner)) as client:
        resp = await client.get(f"/api/v1/ard/entries/{urn}")
        assert resp.status_code == 200
        assert resp.json()["identifier"] == urn
        assert resp.json()["representativeQueries"]
        legacy = await client.get(f"/api/v1/ard/entries/{urn.replace('urn:air:', 'urn:ai:')}")
        assert legacy.status_code == 200
        missing = await client.get("/api/v1/ard/entries/urn:air:observal.example.com:skill:nope")
        assert missing.status_code == 404 and missing.json()["errorCode"] == "NOT_FOUND"


# ── Manifest ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_manifest_publishes_registry_entry_and_public_resources(sessions, settings):
    await _seed(sessions)
    async with _client(_app(sessions, None)) as client:
        closed = (await client.get("/.well-known/ard.json")).json()
        assert [e["type"] for e in closed["entries"]] == ["application/ai-registry+json"]
        assert closed["entries"][0]["url"] == f"{PUBLIC_URL}/api/v1/ard"

        settings["public"] = True
        opened = (await client.get("/.well-known/ard.json")).json()
        names = [e["displayName"] for e in opened["entries"][1:]]
        assert "Security Review" in names and "GitHub" in names
        assert "Draft Skill" not in names and "Secret Skill" not in names

        legacy = (await client.get("/.well-known/ai-catalog.json")).json()
        assert legacy == opened


@pytest.mark.asyncio
async def test_manifest_keeps_pinned_identity_when_public_url_moves(sessions, settings):
    settings["publisher_domain"] = "observal.example.com"
    settings["public_url"] = "https://moved.example.net"
    async with _client(_app(sessions, None)) as client:
        registry = (await client.get("/.well-known/ard.json")).json()["entries"][0]

    assert registry["identifier"] == "urn:air:observal.example.com:registry:observal"
    assert registry["trustManifest"] == {"identity": "https://observal.example.com", "identityType": "https"}
    assert registry["url"] == "https://moved.example.net/api/v1/ard"


# ── Artifacts ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_artifact_serves_bytes_with_digest(sessions, settings):
    owner, _, skill = await _seed(sessions)
    async with _client(_app(sessions, owner)) as client:
        resp = await client.get(f"/api/v1/artifacts/skill/{skill.id}/1.2.0")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert resp.headers["Digest"].startswith("sha-256=")
    assert resp.headers["X-Artifact-Digest"].startswith("sha256:")
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert "Look for auth bugs" in resp.text


@pytest.mark.asyncio
async def test_bundled_skill_artifact_is_not_activatable_as_partial_skill(sessions, settings):
    owner, stranger, skill = await _seed(sessions)
    async with sessions() as db:
        version = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == skill.id))).scalar_one()
        version.extra_files = [{"path": "scripts/run.sh", "content": "echo complete", "executable": True}]
        entry = await project_entity(db, DiscoveryKind.skill, skill.id, ctx=fx.CTX)
        await db.commit()
        assert entry.activatable is False
        assert entry.raw_entry["obs:artifactScope"] == "preview"
        assert availability(entry) == "explicit-install"
    async with _client(_app(sessions, owner)) as client:
        result = await client.get(f"/api/v1/artifacts/skill/{skill.id}/1.2.0")
        assert result.status_code == 409
        assert result.json()["errorCode"] == "COMPLETE_FOLDER_REQUIRED"
        assert "echo complete" not in result.text
    # ARD now denies strangers outside the owner's teamspace before disclosing
    # whether this resource-bearing version exists.
    async with _client(_app(sessions, stranger)) as client:
        assert (await client.get(f"/api/v1/artifacts/skill/{skill.id}/1.2.0")).status_code == 404


@pytest.mark.asyncio
async def test_stale_public_skill_discovery_entry_cannot_expose_privatized_release(sessions, settings):
    owner, stranger, skill = await _seed(sessions)
    async with sessions() as db:
        listing = await db.get(SkillListing, skill.id)
        listing.is_private = True  # Discovery projection has not caught up yet.
        await db.commit()
    async with _client(_app(sessions, stranger)) as client:
        assert (await client.get(f"/api/v1/artifacts/skill/{skill.id}/1.2.0")).status_code == 404
    async with _client(_app(sessions, owner)) as client:
        allowed = await client.get(f"/api/v1/artifacts/skill/{skill.id}/1.2.0")
        assert allowed.status_code == 200
        assert allowed.headers["Cache-Control"].startswith("private")


@pytest.mark.asyncio
async def test_stale_approved_artifact_cannot_bypass_global_re_review(sessions, settings):
    owner, stranger, skill = await _seed(sessions)
    async with sessions() as db:
        version = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == skill.id))).scalar_one()
        version.requires_global_review = True
        await db.commit()
    async with _client(_app(sessions, stranger)) as client:
        assert (await client.get(f"/api/v1/artifacts/skill/{skill.id}/1.2.0")).status_code == 404
    async with _client(_app(sessions, owner)) as client:
        result = await client.get(f"/api/v1/artifacts/skill/{skill.id}/1.2.0")
        assert result.status_code == 200
        assert result.headers["Cache-Control"].startswith("private")


@pytest.mark.asyncio
async def test_artifact_is_publicly_cacheable_only_when_public_registry_is_enabled(sessions, settings):
    owner, _, skill = await _seed(sessions)
    path = f"/api/v1/artifacts/skill/{skill.id}/1.2.0"
    async with sessions() as db:
        from sqlalchemy import select

        from models.discovery_entry import DiscoveryEntry

        private_entry = (
            await db.execute(select(DiscoveryEntry).where(DiscoveryEntry.display_name == "Secret Skill"))
        ).scalar_one()
    private_path = f"/api/v1/artifacts/skill/{private_entry.local_entity_id}/{private_entry.version}"
    async with _client(_app(sessions, owner)) as client:
        private_response = await client.get(path)
        assert private_response.status_code == 200
        assert private_response.headers["Cache-Control"] == "private, no-store"

        settings["public"] = True
        signed_in_response = await client.get(path)
        assert signed_in_response.status_code == 200
        assert signed_in_response.headers["Cache-Control"] == "private, no-store"
        private_listing_response = await client.get(private_path)
        assert private_listing_response.status_code == 200
        assert private_listing_response.headers["Cache-Control"] == "private, no-store"

    async with _client(_app(sessions, None)) as client:
        anonymous_response = await client.get(path)
        assert anonymous_response.status_code == 200
        assert anonymous_response.headers["Cache-Control"].startswith("public")


@pytest.mark.asyncio
async def test_artifact_hides_unapproved_versions_from_non_owners(sessions, settings):
    owner, stranger, skill = await _seed(sessions)
    async with sessions() as db:
        team = await fx.team_with_member(db, owner)
        db.add(TeamMembership(team_id=team.id, user_id=stranger.id))
        row = await db.get(type(skill), skill.id)
        row.team_id = team.id
        row.namespace = team.handle
        await fx.add_skill_version(db, row, owner, version="1.3.0", status=ListingStatus.pending, set_latest=False)
        await reproject_all(db, ctx=fx.CTX)
    async with _client(_app(sessions, stranger)) as client:
        assert (await client.get(f"/api/v1/artifacts/skill/{skill.id}/1.3.0")).status_code == 404
        assert (await client.get(f"/api/v1/artifacts/skill/{skill.id}/1.2.0")).status_code == 200
    async with _client(_app(sessions, owner)) as client:
        pending = await client.get(f"/api/v1/artifacts/skill/{skill.id}/1.3.0")
        assert pending.status_code == 200
        assert pending.headers["Cache-Control"] == "private, no-store"


@pytest.mark.asyncio
async def test_artifact_anonymous_and_unknown(sessions, settings):
    _, _, skill = await _seed(sessions)
    async with _client(_app(sessions, None)) as client:
        assert (await client.get(f"/api/v1/artifacts/skill/{skill.id}/1.2.0")).status_code == 404
        settings["public"] = True
        assert (await client.get(f"/api/v1/artifacts/skill/{skill.id}/1.2.0")).status_code == 200
        assert (await client.get(f"/api/v1/artifacts/skill/{skill.id}/9.9.9")).status_code == 404
        assert (await client.get(f"/api/v1/artifacts/rocket/{skill.id}/1.2.0")).status_code == 404


@pytest.mark.asyncio
async def test_mcp_artifact_never_contains_env_values(sessions, settings):
    owner, _, _ = await _seed(sessions)
    async with sessions() as db:
        from sqlalchemy import select

        from models.discovery_entry import DiscoveryEntry

        entry = (await db.execute(select(DiscoveryEntry).where(DiscoveryEntry.kind == DiscoveryKind.mcp))).scalar_one()
    async with _client(_app(sessions, owner)) as client:
        resp = await client.get(f"/api/v1/artifacts/mcp/{entry.local_entity_id}/{entry.version}")
    assert resp.status_code == 200
    card = resp.json()
    assert card["environmentVariables"] == ["GITHUB_TOKEN"]
    assert "SHOULD-NEVER-LEAK" not in resp.text
    assert resp.headers["X-Artifact-Digest"] == entry.artifact_digest


# ── Conformance ──────────────────────────────────────────────────────────


def _run_tool(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CONFORMANCE_TOOL), *args], capture_output=True, text=True, timeout=120)


@pytest.mark.asyncio
async def test_manifest_passes_official_conformance(sessions, settings, tmp_path):
    await _seed(sessions)
    settings["public"] = True
    async with _client(_app(sessions, None)) as client:
        manifest = (await client.get("/.well-known/ard.json")).json()
    assert len(manifest["entries"]) >= 7
    path = tmp_path / "ard.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = _run_tool("manifest", str(path))
    assert result.returncode == 0, result.stdout
    assert "CONFORMANCE STATUS: PASS" in result.stdout
    assert "critical specification errors" in result.stdout and " 0 critical" in result.stdout


def _reserved_socket() -> socket.socket:
    """Bind an ephemeral port and retain ownership until Uvicorn starts."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    return sock


@pytest.mark.asyncio
async def test_registry_api_passes_official_conformance(settings, tmp_path):
    """Run the tool's registry mode against a live server backed by a file SQLite database."""
    import uvicorn

    db_path = tmp_path / "registry.sqlite"
    engine = fx.make_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    sessions = await fx.create_schema(engine)
    owner, _, _ = await _seed(sessions)
    settings["public"] = True

    app = _app(sessions, owner)
    sock = _reserved_socket()
    port = sock.getsockname()[1]
    config = uvicorn.Config(app, log_level="warning", loop="asyncio")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 15
        while not server.started and time.time() < deadline:
            await asyncio.sleep(0.05)
        assert server.started, "uvicorn did not start"
        result = await asyncio.to_thread(_run_tool, "registry", f"http://127.0.0.1:{port}/api/v1/ard")
        assert result.returncode == 0, result.stdout
        assert "CONFORMANCE STATUS: PASS" in result.stdout
        assert "POST /search responded successfully" in result.stdout
        assert "GET /agents responded successfully" in result.stdout
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        await engine.dispose()


# ── List filter parser ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("clause", "expected"),
    [
        ("type = 'application/ai-skill+md'", ("type", "=", "'application/ai-skill+md'")),
        ("createdAfter>='2026-01-01'", ("createdAfter", ">=", "'2026-01-01'")),
        ("  displayName   =   Review  ", ("displayName", "=", "Review")),
        ("publisherId = a.com,b.com", ("publisherId", "=", "a.com,b.com")),
    ],
)
def test_parse_clause_accepts_spec_shapes(clause, expected):
    assert ard._parse_clause(clause.strip()) == expected


@pytest.mark.parametrize("clause", ["= x", "type", "type ~ x", "type =", "1abc = x"])
def test_parse_clause_rejects_malformed(clause):
    with pytest.raises(ard.InvalidSearchRequestError):
        ard._parse_clause(clause)


def test_split_clauses_is_case_insensitive_and_ignores_blanks():
    assert ard._split_clauses("type = a and displayName = b AND  ") == ["type = a", "displayName = b"]


def test_split_clauses_keeps_and_inside_quotes():
    assert ard._split_clauses("displayName = 'Research AND Development' AND type = x") == [
        "displayName = 'Research AND Development'",
        "type = x",
    ]
    assert ard._split_clauses('displayName = "a and b"') == ['displayName = "a and b"']
    assert ard._split_clauses("brandname = x") == ["brandname = x"], "AND inside a word is not a separator"


def test_filter_parser_scales_linearly_on_adversarial_whitespace():
    """A tenfold input increase should stay within twice the expected linear growth."""
    import statistics
    import time

    def median_runtime(size: int) -> float:
        expression = "A=a" + " " * size
        samples = []
        for _ in range(7):
            started = time.perf_counter()
            for clause in ard._split_clauses(expression):
                ard._parse_clause(clause)
            samples.append(time.perf_counter() - started)
        return statistics.median(samples)

    size_ratio = 10
    small = median_runtime(20_000)
    large = median_runtime(20_000 * size_ratio)
    assert large <= small * size_ratio * 2, f"expected linear scaling, got {large / small:.1f}x"


def test_filter_errors_never_echo_foreign_exception_text():
    with pytest.raises(ard.InvalidSearchRequestError) as info:
        ard._parse_timestamp("'not a date'")
    assert info.value.message == "timestamp filters must be ISO 8601 values"
    assert info.value.__cause__ is None
