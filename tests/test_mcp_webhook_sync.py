# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""GitHub and GitLab webhook auto-sync for MCP listings.

The receiver and settings routes run against a real in-memory SQLite session.
The sync job runs real git against a local repository, so fetching, version
selection, and the published version row are exercised end to end; only the
clone URL is pointed at the local repository.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models.base import Base
from models.component_bundle import ComponentBundle
from models.mcp import ListingStatus, McpListing, McpValidationResult, McpVersion
from models.mcp_webhook_sync import McpWebhookSync
from models.team import Team, TeamMembership
from models.user import User, UserRole
from services import mcp_webhook_sync as svc

if TYPE_CHECKING:
    from pathlib import Path

REPO_URL = "https://github.com/acme/weather-mcp"
_TABLES = [
    User.__table__,
    Team.__table__,
    TeamMembership.__table__,
    ComponentBundle.__table__,
    McpListing.__table__,
    McpVersion.__table__,
    McpValidationResult.__table__,
    McpWebhookSync.__table__,
]


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _user(username: str = "alice", role: UserRole = UserRole.user) -> User:
    return User(id=uuid.uuid4(), email=f"{username}@example.com", username=username, name=username, role=role)


def _listing(
    owner: User, *, source_url: str | None = REPO_URL, version: str = "1.0.0"
) -> tuple[McpListing, McpVersion]:
    listing = McpListing(
        id=uuid.uuid4(),
        name="weather-mcp",
        namespace=owner.username,
        slug="weather-mcp",
        category="utilities",
        owner=owner.username,
        submitted_by=owner.id,
        co_authors=[],
        is_private=False,
    )
    ver = McpVersion(
        id=uuid.uuid4(),
        listing_id=listing.id,
        version=version,
        description="Weather lookups for agents",
        transport="stdio",
        framework="python-mcp",
        command="uvx",
        args=["weather-mcp"],
        environment_variables=[{"name": "WEATHER_API_KEY", "description": "Owner wrote this", "required": True}],
        supported_harnesses=["claude-code"],
        source_url=source_url,
        status=ListingStatus.approved,
        released_by=owner.id,
        released_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    listing.latest_version_id = ver.id
    return listing, ver


def _sync(listing: McpListing, owner: User, secret: str, **kwargs) -> McpWebhookSync:
    from services.dynamic_settings import encrypt_value

    return McpWebhookSync(
        id=uuid.uuid4(),
        listing_id=listing.id,
        provider=kwargs.get("provider", "github"),
        secret=encrypt_value(secret),
        enabled_by=owner.id,
        sync_on_push=kwargs.get("sync_on_push", True),
        sync_on_release=kwargs.get("sync_on_release", False),
        branch=kwargs.get("branch"),
    )


def _push(branch: str = "main", *, repo: str = REPO_URL, default_branch: str = "main", **extra) -> dict:
    return {
        "ref": f"refs/heads/{branch}",
        "after": "a" * 40,
        "repository": {"clone_url": f"{repo}.git", "html_url": repo, "default_branch": default_branch},
        "head_commit": {"message": "Add forecast tool"},
        **extra,
    }


def _release(tag: str = "v2.0.0", *, action: str = "published", draft: bool = False) -> dict:
    return {
        "action": action,
        "release": {"tag_name": tag, "draft": draft, "body": "Big release"},
        "repository": {"clone_url": f"{REPO_URL}.git", "html_url": REPO_URL, "default_branch": "main"},
    }


GITLAB_URL = "https://gitlab.com/acme/tools/weather-mcp"


def _gl_project(repo: str = GITLAB_URL, default_branch: str = "main") -> dict:
    ssh = "git@" + repo.removeprefix("https://").replace("/", ":", 1) + ".git"
    return {"web_url": repo, "git_http_url": f"{repo}.git", "git_ssh_url": ssh, "default_branch": default_branch}


def _gl_push(branch: str = "main", *, repo: str = GITLAB_URL, after: str = "a" * 40, **extra) -> dict:
    return {
        "object_kind": "push",
        "ref": f"refs/heads/{branch}",
        "before": "b" * 40,
        "after": after,
        "checkout_sha": None if after == "0" * 40 else after,
        "project": _gl_project(repo),
        # GitLab lists the pushed commits; the head is the one matching checkout_sha.
        "commits": [] if after == "0" * 40 else [{"id": after, "message": "Add forecast tool"}, {"id": "c" * 40}],
        **extra,
    }


def _gl_release(tag: str = "v2.0.0", *, action: str = "create") -> dict:
    return {
        "object_kind": "release",
        "action": action,
        "tag": tag,
        "description": "Big release",
        "project": _gl_project(),
    }


# ── Signatures and delivery rules ────────────────────────────


class TestSignature:
    def test_accepts_github_signature(self):
        body = b'{"zen": "Keep it logically awesome."}'
        assert svc.verify_signature("s3cret", body, _sign("s3cret", body))

    @pytest.mark.parametrize(
        "header",
        [None, "", "sha1=abc", "sha256=" + "0" * 64, _sign("other", b"{}")],
    )
    def test_rejects_missing_or_wrong_signature(self, header):
        assert not svc.verify_signature("s3cret", b"{}", header)

    def test_rejects_when_secret_is_empty(self):
        assert not svc.verify_signature("", b"{}", _sign("", b"{}"))


class TestRepoUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/Acme/Weather-MCP",
            "https://github.com/acme/weather-mcp.git",
            "https://github.com/acme/weather-mcp/",
            "git@github.com:acme/weather-mcp.git",
        ],
    )
    def test_equivalent_forms_normalize_equal(self, url):
        assert svc.normalize_repo_url(url) == "github.com/acme/weather-mcp"


class TestPlanDelivery:
    def _sync(self, **kwargs):
        return SimpleNamespace(
            sync_on_push=kwargs.get("sync_on_push", True),
            sync_on_release=kwargs.get("sync_on_release", False),
            branch=kwargs.get("branch"),
        )

    def test_push_to_default_branch_queues_sync(self):
        plan = svc.plan_delivery(self._sync(), REPO_URL, "push", _push())
        assert plan == svc.SyncRequest(trigger="push", ref="main", changelog="Add forecast tool")

    def test_push_to_other_branch_is_ignored(self):
        plan = svc.plan_delivery(self._sync(), REPO_URL, "push", _push("feature/x"))
        assert plan == "push to feature/x ignored; tracking main"

    def test_configured_branch_overrides_default(self):
        plan = svc.plan_delivery(self._sync(branch="release"), REPO_URL, "push", _push("release"))
        assert isinstance(plan, svc.SyncRequest) and plan.ref == "release"
        assert isinstance(svc.plan_delivery(self._sync(branch="release"), REPO_URL, "push", _push("main")), str)

    def test_push_ignored_when_push_sync_off(self):
        plan = svc.plan_delivery(self._sync(sync_on_push=False, sync_on_release=True), REPO_URL, "push", _push())
        assert plan == "push sync is turned off"

    def test_tag_push_and_branch_delete_are_ignored(self):
        tag_push = {**_push(), "ref": "refs/tags/v1.0.0"}
        assert svc.plan_delivery(self._sync(), REPO_URL, "push", tag_push) == "not a branch push"
        assert svc.plan_delivery(self._sync(), REPO_URL, "push", _push(deleted=True)) == "branch was deleted"

    def test_published_release_queues_sync(self):
        plan = svc.plan_delivery(self._sync(sync_on_release=True), REPO_URL, "release", _release())
        assert plan == svc.SyncRequest(trigger="release", ref="v2.0.0", changelog="Big release")

    def test_release_rules(self):
        on = self._sync(sync_on_release=True)
        assert svc.plan_delivery(self._sync(), REPO_URL, "release", _release()) == "release sync is turned off"
        assert svc.plan_delivery(on, REPO_URL, "release", _release(action="created")) == "release created ignored"
        assert svc.plan_delivery(on, REPO_URL, "release", _release(draft=True)) == "draft release ignored"
        assert "not a semantic version" in svc.plan_delivery(on, REPO_URL, "release", _release("nightly"))

    def test_wrong_repository_is_rejected(self):
        with pytest.raises(ValueError, match="different repository"):
            svc.plan_delivery(self._sync(), REPO_URL, "push", _push(repo="https://github.com/evil/fork"))

    def test_ping_and_unknown_events(self):
        assert svc.plan_delivery(self._sync(), REPO_URL, "ping", {}) == "ping received"
        assert svc.plan_delivery(self._sync(), REPO_URL, "issues", _push()) == "issues events are ignored"

    def test_hostile_branch_name_is_refused(self):
        plan = svc.plan_delivery(self._sync(branch="--upload-pack=x"), REPO_URL, "push", _push("--upload-pack=x"))
        assert plan == "branch name is not supported"


class TestGitLab:
    def _sync(self, **kwargs):
        return SimpleNamespace(
            sync_on_push=kwargs.get("sync_on_push", True),
            sync_on_release=kwargs.get("sync_on_release", False),
            branch=kwargs.get("branch"),
        )

    def _plan(self, payload, event=None, sync=None, url=GITLAB_URL):
        event = event or svc.gitlab_event_name(None, payload)
        return svc.plan_gitlab_delivery(sync or self._sync(), url, event, payload)

    def test_token_must_match_exactly(self):
        assert svc.verify_gitlab_token("s3cret", "s3cret")
        for header in (None, "", "s3cre", "s3cret ", "S3CRET"):
            assert not svc.verify_gitlab_token("s3cret", header)
        assert not svc.verify_gitlab_token("", "")

    @pytest.mark.parametrize(
        ("url", "provider"),
        [
            (GITLAB_URL, "gitlab"),
            ("https://gitlab.acme.internal/team/mcp", "gitlab"),
            ("git@gitlab.com:acme/mcp.git", "gitlab"),
            (REPO_URL, "github"),
            ("https://code.acme.com/team/mcp", "github"),
            (None, "github"),
        ],
    )
    def test_provider_is_detected_from_the_host(self, url, provider):
        assert svc.detect_provider(url) == provider

    @pytest.mark.parametrize(
        ("header", "payload", "event"),
        [
            ("Push Hook", {}, "push"),
            ("Tag Push Hook", {}, "tag_push"),
            ("Release Hook", {}, "release"),
            ("System Hook", {"object_kind": "push"}, "push"),
            (None, {"object_kind": "merge_request"}, "merge_request"),
        ],
    )
    def test_event_names(self, header, payload, event):
        assert svc.gitlab_event_name(header, payload) == event

    def test_push_to_default_branch_queues_sync_with_head_commit_message(self):
        assert self._plan(_gl_push()) == svc.SyncRequest(trigger="push", ref="main", changelog="Add forecast tool")

    def test_nested_group_ssh_and_http_urls_match(self):
        assert svc.normalize_repo_url(GITLAB_URL) == "gitlab.com/acme/tools/weather-mcp"
        assert isinstance(self._plan(_gl_push(), url="git@gitlab.com:acme/tools/weather-mcp.git"), svc.SyncRequest)

    def test_push_rules(self):
        assert self._plan(_gl_push("feature/x")) == "push to feature/x ignored; tracking main"
        assert self._plan(_gl_push(after="0" * 40)) == "branch was deleted"
        assert self._plan(_gl_push(), sync=self._sync(sync_on_push=False)) == "push sync is turned off"
        assert isinstance(self._plan(_gl_push("stable"), sync=self._sync(branch="stable")), svc.SyncRequest)

    def test_release_rules(self):
        on = self._sync(sync_on_release=True)
        assert self._plan(_gl_release(), sync=on) == svc.SyncRequest(
            trigger="release", ref="v2.0.0", changelog="Big release"
        )
        assert self._plan(_gl_release()) == "release sync is turned off"
        assert self._plan(_gl_release(action="update"), sync=on) == "release update ignored"
        assert "not a semantic version" in self._plan(_gl_release("nightly"), sync=on)

    def test_tag_pushes_and_other_events_are_ignored(self):
        tag_push = {**_gl_push(), "object_kind": "tag_push", "ref": "refs/tags/v2.0.0"}
        assert "create a release" in self._plan(tag_push, event="tag_push")
        note = {"object_kind": "note", "project": _gl_project()}
        assert self._plan(note) == "note events are ignored"

    def test_wrong_repository_is_rejected(self):
        with pytest.raises(ValueError, match="different repository"):
            self._plan(_gl_push(repo="https://gitlab.com/evil/weather-mcp"))


class TestVersions:
    @pytest.mark.parametrize(
        ("existing", "declared", "expected"),
        [
            (["1.0.0"], None, "1.0.1"),
            (["1.0.0", "1.2.9", "1.1.0"], None, "1.2.10"),
            (["1.0.0"], "1.4.0", "1.4.0"),
            (["2.0.0"], "1.4.0", "2.0.1"),
            (["1.0.0"], "1.0.0", "1.0.1"),
            (["1.0.0-beta"], None, "1.0.1"),
            ([], None, "0.0.1"),
            (["not-semver"], None, "0.0.1"),
        ],
    )
    def test_next_push_version(self, existing, declared, expected):
        assert svc.next_push_version(existing, declared) == expected

    @pytest.mark.parametrize(
        ("tag", "version"), [("v1.2.3", "1.2.3"), ("1.2.3", "1.2.3"), ("v1.0.0-rc.1", "1.0.0-rc.1")]
    )
    def test_tag_to_version(self, tag, version):
        assert svc.tag_to_version(tag) == version

    @pytest.mark.parametrize("tag", ["latest", "v1.2", "release-1.2.3"])
    def test_tag_to_version_rejects(self, tag):
        assert svc.tag_to_version(tag) is None

    def test_build_version_ignores_a_guessed_docker_image(self):
        owner = _user()
        listing, current = _listing(owner)
        current.command, current.args = None, None
        listing.latest_version = current
        guessed = {
            "docker_image": "ghcr.io/acme/weather-mcp:latest",
            "docker_image_suggested": True,
            "command": "docker",
            "args": ["run", "-i", "--rm", "ghcr.io/acme/weather-mcp:latest"],
        }
        ver = svc.build_version(
            listing, guessed, version="1.0.1", trigger="push", ref="main", sha="c" * 40, notes="", actor_id=owner.id
        )
        assert (ver.docker_image, ver.command, ver.args) == (None, None, None)

        declared = {**guessed, "docker_image_suggested": False}
        ver = svc.build_version(
            listing, declared, version="1.0.1", trigger="push", ref="main", sha="c" * 40, notes="", actor_id=owner.id
        )
        assert ver.docker_image == "ghcr.io/acme/weather-mcp:latest" and ver.command == "docker"

    def test_declared_version_from_pyproject_then_package_json(self, tmp_path):
        assert svc.detect_declared_version(str(tmp_path)) is None
        (tmp_path / "package.json").write_text('{"version": "3.1.0"}')
        assert svc.detect_declared_version(str(tmp_path)) == "3.1.0"
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.9.0"\n')
        assert svc.detect_declared_version(str(tmp_path)) == "0.9.0"

    def test_build_version_keeps_owner_config_and_adds_detected_env(self):
        owner = _user()
        listing, current = _listing(owner)
        listing.latest_version = current
        analysis = {
            "command": "python",
            "args": ["-m", "weather"],
            "tools": [{"name": "forecast", "docstring": "Get the forecast"}],
            "environment_variables": [
                {"name": "WEATHER_API_KEY", "description": "detected", "required": True},
                {"name": "WEATHER_REGION", "description": "Region code", "required": True},
            ],
        }
        ver = svc.build_version(
            listing,
            analysis,
            version="1.0.1",
            trigger="push",
            ref="main",
            sha="b" * 40,
            notes="Add forecast",
            actor_id=owner.id,
        )
        assert (ver.command, ver.args) == ("uvx", ["weather-mcp"])
        assert ver.environment_variables == [
            {"name": "WEATHER_API_KEY", "description": "Owner wrote this", "required": True},
            {"name": "WEATHER_REGION", "description": "Region code", "required": False},
        ]
        assert ver.tools_schema == {"tools": [{"name": "forecast", "description": "Get the forecast"}]}
        assert (ver.source_url, ver.source_ref, ver.resolved_sha) == (REPO_URL, "main", "b" * 40)
        assert ver.status == ListingStatus.approved and ver.reviewed_by == owner.id
        assert ver.changelog.startswith("Synced from GitHub main at bbbbbbbbbbbb.")
        assert ver.description == "Weather lookups for agents"
        # The current version is untouched: lockfiles hash its content.
        assert current.resolved_sha is None and current.environment_variables[0]["description"] == "Owner wrote this"


# ── Database fixtures ────────────────────────────────────────


@asynccontextmanager
async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _seed(sessions, *rows):
    async with sessions() as session:
        session.add_all(rows)
        await session.commit()


@asynccontextmanager
async def _api(sessions, user: User | None, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from api.deps import get_current_user, get_db
    from api.ratelimit import limiter
    from api.routes import mcp_webhook_sync as routes
    from main import app

    queued: list = []

    async def fake_enqueue(sync_id, request):
        queued.append((sync_id, request))

    monkeypatch.setattr(routes, "enqueue_sync", fake_enqueue)
    limiter.enabled = False

    async def _db():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = _db
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, queued
    finally:
        app.dependency_overrides.clear()


# ── Settings routes ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_owner_enables_sync_and_sees_secret_once(monkeypatch):
    owner = _user()
    listing, ver = _listing(owner)
    async with _database() as sessions:
        await _seed(sessions, owner, listing, ver)
        async with _api(sessions, owner, monkeypatch) as (client, _):
            assert (await client.get(f"/api/v1/mcps/{listing.id}/webhook-sync")).json()["enabled"] is False

            resp = await client.put(
                f"/api/v1/mcps/{listing.id}/webhook-sync", json={"sync_on_push": True, "sync_on_release": True}
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["enabled"] and len(body["secret"]) == 64
            assert body["webhook_url"] == f"http://test/api/v1/webhooks/github/mcp/{body['id']}"

            again = await client.put(
                f"/api/v1/mcps/{listing.id}/webhook-sync", json={"sync_on_push": False, "sync_on_release": True}
            )
            assert again.json()["secret"] is None and again.json()["sync_on_push"] is False
            assert (await client.get(f"/api/v1/mcps/{listing.id}/webhook-sync")).json()["secret"] is None

            rotated = await client.post(f"/api/v1/mcps/{listing.id}/webhook-sync/rotate-secret")
            assert rotated.json()["secret"] not in (None, body["secret"])

        async with sessions() as session:
            stored = (await session.execute(select(McpWebhookSync))).scalar_one()
            assert stored.secret.startswith("enc:"), "the secret must be encrypted at rest"


@pytest.mark.asyncio
async def test_webhook_url_uses_dedicated_public_url_when_set(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "WEBHOOK_PUBLIC_URL", "https://hooks.example.com/")
    owner = _user()
    listing, ver = _listing(owner)
    async with _database() as sessions:
        await _seed(sessions, owner, listing, ver)
        async with _api(sessions, owner, monkeypatch) as (client, _):
            body = (await client.put(f"/api/v1/mcps/{listing.id}/webhook-sync", json={"sync_on_push": True})).json()
            assert body["webhook_url"] == f"https://hooks.example.com/api/v1/webhooks/github/mcp/{body['id']}"


@pytest.mark.asyncio
async def test_settings_require_owner_trigger_and_git_url(monkeypatch):
    owner, stranger = _user("alice"), _user("mallory")
    listing, ver = _listing(owner)
    bare, bare_ver = _listing(owner, source_url=None)
    bare.slug = "no-repo"
    async with _database() as sessions:
        await _seed(sessions, owner, stranger, listing, ver, bare, bare_ver)
        async with _api(sessions, stranger, monkeypatch) as (client, _):
            resp = await client.put(f"/api/v1/mcps/{listing.id}/webhook-sync", json={})
            assert resp.status_code == 403
        async with _api(sessions, owner, monkeypatch) as (client, _):
            resp = await client.put(
                f"/api/v1/mcps/{listing.id}/webhook-sync", json={"sync_on_push": False, "sync_on_release": False}
            )
            assert resp.status_code == 422
            resp = await client.put(f"/api/v1/mcps/{bare.id}/webhook-sync", json={})
            assert resp.status_code == 400
            assert (await client.post(f"/api/v1/mcps/{listing.id}/webhook-sync/run")).status_code == 404


@pytest.mark.asyncio
async def test_sync_requires_a_reviewed_listing(monkeypatch, local_repo):
    owner = _user()
    listing, ver = _listing(owner)
    ver.status = ListingStatus.pending
    sync = _sync(listing, owner, "s3cret")
    async with _database() as sessions:
        await _seed(sessions, owner, listing, ver, sync)
        async with _api(sessions, owner, monkeypatch) as (client, _):
            resp = await client.put(f"/api/v1/mcps/{listing.id}/webhook-sync", json={})
            assert resp.status_code == 400 and "approved" in resp.json()["detail"]
        # A sync row left from before (or a pending listing) never publishes past review.
        assert await _run(sessions, sync.id, svc.SyncRequest("push", "main"), monkeypatch) is None
        versions, _ = await _versions(sessions, listing.id)
        assert set(versions) == {"1.0.0"}


@pytest.mark.asyncio
async def test_manual_run_queues_tracked_branch(monkeypatch):
    owner = _user()
    listing, ver = _listing(owner)
    sync = _sync(listing, owner, "s3cret", branch="release")
    async with _database() as sessions:
        await _seed(sessions, owner, listing, ver, sync)
        async with _api(sessions, owner, monkeypatch) as (client, queued):
            resp = await client.post(f"/api/v1/mcps/{listing.id}/webhook-sync/run")
            assert resp.status_code == 202 and resp.json()["last_sync_status"] == "queued"
            assert queued == [(sync.id, svc.SyncRequest(trigger="manual", ref="release"))]
            assert (await client.delete(f"/api/v1/mcps/{listing.id}/webhook-sync")).status_code == 200
            assert (await client.get(f"/api/v1/mcps/{listing.id}/webhook-sync")).json()["enabled"] is False


@pytest.mark.asyncio
async def test_queue_failure_marks_sync_failed_instead_of_stuck_queued(monkeypatch):
    from api.routes import mcp_webhook_sync as routes

    owner = _user()
    listing, ver = _listing(owner)
    sync = _sync(listing, owner, "s3cret")
    async with _database() as sessions:
        await _seed(sessions, owner, listing, ver, sync)
        async with _api(sessions, owner, monkeypatch) as (client, _):

            async def broken_enqueue(sync_id, request):
                raise ConnectionError("redis down")

            monkeypatch.setattr(routes, "enqueue_sync", broken_enqueue)
            assert (await client.post(f"/api/v1/mcps/{listing.id}/webhook-sync/run")).status_code == 503
            assert (await _deliver(client, sync.id, _push())).status_code == 503

        async with sessions() as session:
            stored = await session.get(McpWebhookSync, sync.id)
            assert stored.last_sync_status == "failed"
            assert "worker queue is unavailable" in stored.last_sync_error
            assert stored.last_event == "push"


@pytest.mark.asyncio
async def test_queue_error_after_the_job_ran_keeps_the_worker_result(monkeypatch):
    from api.routes import mcp_webhook_sync as routes

    owner = _user()
    listing, ver = _listing(owner)
    sync = _sync(listing, owner, "s3cret")
    async with _database() as sessions:
        await _seed(sessions, owner, listing, ver, sync)
        async with _api(sessions, owner, monkeypatch) as (client, _):

            async def enqueue_then_fail(sync_id, request):
                # The job reached the queue and the worker finished it before the error surfaced.
                async with sessions() as worker:
                    row = await worker.get(McpWebhookSync, sync_id)
                    row.last_sync_status = "success"
                    row.last_version = "0.3.0"
                    await worker.commit()
                raise ConnectionError("connection reset after write")

            monkeypatch.setattr(routes, "enqueue_sync", enqueue_then_fail)
            assert (await _deliver(client, sync.id, _push())).status_code == 503

        async with sessions() as session:
            stored = await session.get(McpWebhookSync, sync.id)
            assert stored.last_sync_status == "success"
            assert stored.last_sync_error is None
            assert stored.last_version == "0.3.0"


# ── Public receiver ──────────────────────────────────────────


async def _deliver(client, sync_id, payload, *, event="push", secret="s3cret", signature=None):
    body = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-Hub-Signature-256": signature if signature is not None else _sign(secret, body),
    }
    return await client.post(f"/api/v1/webhooks/github/mcp/{sync_id}", content=body, headers=headers)


@pytest.mark.asyncio
async def test_receiver_queues_signed_push_without_a_user_token(monkeypatch):
    owner = _user()
    listing, ver = _listing(owner)
    sync = _sync(listing, owner, "s3cret")
    async with _database() as sessions:
        await _seed(sessions, owner, listing, ver, sync)
        async with _api(sessions, None, monkeypatch) as (client, queued):
            resp = await _deliver(client, sync.id, _push())
            assert resp.status_code == 202, resp.text
            assert resp.json() == {"status": "queued", "reason": None, "trigger": "push", "ref": "main"}
            assert queued == [(sync.id, svc.SyncRequest(trigger="push", ref="main", changelog="Add forecast tool"))]

        async with sessions() as session:
            stored = await session.get(McpWebhookSync, sync.id)
            assert (stored.last_event, stored.last_sync_status) == ("push", "queued")


def _receiver_request(sync_id, peer: str, forwarded_for: str | None = None):
    from starlette.requests import Request

    headers = [(b"x-forwarded-for", forwarded_for.encode())] if forwarded_for else []
    scope = {
        "type": "http",
        "method": "POST",
        "path": f"/api/v1/webhooks/github/mcp/{sync_id}",
        "headers": headers,
        "query_string": b"",
        "client": (peer, 40000),
        "path_params": {"sync_id": sync_id},
    }
    return Request(scope)


def test_receiver_rate_limit_is_per_sync_even_behind_one_proxy_address(monkeypatch):
    import api.ratelimit as ratelimit
    from api.routes.mcp_webhook_sync import _webhook_rate_key

    monkeypatch.setattr(ratelimit.ds, "get_sync", lambda key: "10.0.0.0/8")
    a, b = uuid.uuid4(), uuid.uuid4()

    # API Gateway: the ALB (10.0.0.5) forwards for the VPC link (10.0.1.9), never the sender.
    via_gateway_a = _webhook_rate_key(_receiver_request(a, "10.0.0.5", "10.0.1.9"))
    via_gateway_b = _webhook_rate_key(_receiver_request(b, "10.0.0.5", "10.0.1.9"))
    assert via_gateway_a != via_gateway_b

    # A public ALB still separates senders on the same hook.
    github = _webhook_rate_key(_receiver_request(a, "10.0.0.5", "140.82.115.1"))
    attacker = _webhook_rate_key(_receiver_request(a, "10.0.0.5", "198.51.100.7"))
    assert len({github, attacker, via_gateway_a}) == 3


@pytest.mark.asyncio
async def test_flooding_one_webhook_url_does_not_throttle_another(monkeypatch):
    from limits.storage import MemoryStorage
    from limits.strategies import FixedWindowRateLimiter

    from api.ratelimit import limiter

    storage = MemoryStorage()
    monkeypatch.setattr(limiter, "_storage", storage)
    monkeypatch.setattr(limiter, "_limiter", FixedWindowRateLimiter(storage))
    async with _database() as sessions, _api(sessions, None, monkeypatch) as (client, _queued):
        limiter.enabled = True
        try:
            flooded, other = uuid.uuid4(), uuid.uuid4()
            statuses = [(await _deliver(client, flooded, _push())).status_code for _ in range(61)]
            assert statuses[:60] == [404] * 60
            assert statuses[60] == 429
            assert (await _deliver(client, other, _push())).status_code == 404
        finally:
            limiter.enabled = False


@pytest.mark.asyncio
async def test_receiver_rejects_bad_signatures_and_unknown_hooks(monkeypatch):
    owner = _user()
    listing, ver = _listing(owner)
    sync = _sync(listing, owner, "s3cret")
    async with _database() as sessions:
        await _seed(sessions, owner, listing, ver, sync)
        async with _api(sessions, None, monkeypatch) as (client, queued):
            assert (await _deliver(client, sync.id, _push(), secret="wrong")).status_code == 401
            assert (await _deliver(client, sync.id, _push(), signature="")).status_code == 401
            assert (await _deliver(client, uuid.uuid4(), _push())).status_code == 404
            other_repo = _push(repo="https://github.com/evil/fork")
            assert (await _deliver(client, sync.id, other_repo)).status_code == 422
            assert queued == []


@pytest.mark.asyncio
async def test_receiver_ignores_untracked_events_without_touching_status(monkeypatch):
    owner = _user()
    listing, ver = _listing(owner)
    sync = _sync(listing, owner, "s3cret")
    sync.last_sync_status = "success"
    async with _database() as sessions:
        await _seed(sessions, owner, listing, ver, sync)
        async with _api(sessions, None, monkeypatch) as (client, queued):
            resp = await _deliver(client, sync.id, _push("feature/x"))
            assert resp.status_code == 202
            assert resp.json()["status"] == "ignored" and "feature/x" in resp.json()["reason"]
            ping = await _deliver(client, sync.id, {"zen": "hi"}, event="ping")
            assert ping.json() == {"status": "ignored", "reason": "ping received", "trigger": None, "ref": None}
            assert queued == []
        async with sessions() as session:
            stored = await session.get(McpWebhookSync, sync.id)
            assert (stored.last_event, stored.last_sync_status) == ("ping", "success")


async def _gl_deliver(client, sync_id, payload, *, event="Push Hook", token="s3cret"):
    headers = {"Content-Type": "application/json", "X-Gitlab-Event": event}
    if token is not None:
        headers["X-Gitlab-Token"] = token
    return await client.post(f"/api/v1/webhooks/gitlab/mcp/{sync_id}", content=json.dumps(payload), headers=headers)


@pytest.mark.asyncio
async def test_enabling_sync_detects_gitlab_and_can_be_overridden(monkeypatch):
    owner = _user()
    listing, ver = _listing(owner, source_url=GITLAB_URL)
    async with _database() as sessions:
        await _seed(sessions, owner, listing, ver)
        async with _api(sessions, owner, monkeypatch) as (client, _):
            url = f"/api/v1/mcps/{listing.id}/webhook-sync"
            body = (await client.put(url, json={"sync_on_push": True})).json()
            assert body["provider"] == "gitlab"
            assert body["webhook_url"] == f"http://test/api/v1/webhooks/gitlab/mcp/{body['id']}"

            # Self-hosted GitLab on a neutral hostname, or a mistaken guess, is fixed by picking the provider.
            switched = (await client.put(url, json={"sync_on_push": True, "provider": "github"})).json()
            assert switched["provider"] == "github" and "/webhooks/github/" in switched["webhook_url"]
            assert switched["secret"] is None, "changing the provider keeps the secret"
            assert (await client.put(url, json={"sync_on_push": True, "provider": "bitbucket"})).status_code == 422


@pytest.mark.asyncio
async def test_gitlab_receiver_queues_push_and_release_with_the_secret_token(monkeypatch):
    owner = _user()
    listing, ver = _listing(owner, source_url=GITLAB_URL)
    sync = _sync(listing, owner, "s3cret", provider="gitlab", sync_on_release=True)
    async with _database() as sessions:
        await _seed(sessions, owner, listing, ver, sync)
        async with _api(sessions, None, monkeypatch) as (client, queued):
            resp = await _gl_deliver(client, sync.id, _gl_push())
            assert resp.status_code == 202, resp.text
            assert resp.json() == {"status": "queued", "reason": None, "trigger": "push", "ref": "main"}
            release = await _gl_deliver(client, sync.id, _gl_release(), event="Release Hook")
            assert release.json()["trigger"] == "release"
            assert queued == [
                (sync.id, svc.SyncRequest(trigger="push", ref="main", changelog="Add forecast tool")),
                (sync.id, svc.SyncRequest(trigger="release", ref="v2.0.0", changelog="Big release")),
            ]
            tag = await _gl_deliver(client, sync.id, {**_gl_push(), "object_kind": "tag_push"}, event="Tag Push Hook")
            assert tag.json()["status"] == "ignored"

        async with sessions() as session:
            stored = await session.get(McpWebhookSync, sync.id)
            assert (stored.last_event, stored.last_sync_status) == ("tag_push", "queued")


@pytest.mark.asyncio
async def test_gitlab_receiver_rejects_bad_tokens_and_the_other_providers_hooks(monkeypatch):
    owner = _user()
    listing, ver = _listing(owner, source_url=GITLAB_URL)
    gl_sync = _sync(listing, owner, "s3cret", provider="gitlab")
    other, other_ver = _listing(owner)
    other.slug = other.name = "other-mcp"
    gh_sync = _sync(other, owner, "s3cret")
    async with _database() as sessions:
        await _seed(sessions, owner, listing, ver, other, other_ver, gl_sync, gh_sync)
        async with _api(sessions, None, monkeypatch) as (client, queued):
            assert (await _gl_deliver(client, gl_sync.id, _gl_push(), token="wrong")).status_code == 401
            assert (await _gl_deliver(client, gl_sync.id, _gl_push(), token=None)).status_code == 401
            # Each sync only answers on its own provider's endpoint.
            assert (await _gl_deliver(client, gh_sync.id, _gl_push())).status_code == 404
            assert (await _deliver(client, gl_sync.id, _push(repo=GITLAB_URL))).status_code == 404
            evil = _gl_push(repo="https://gitlab.com/evil/weather-mcp")
            assert (await _gl_deliver(client, gl_sync.id, evil)).status_code == 422
            assert queued == []


# ── The sync job against a real git repository ───────────────


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "weather-mcp"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "server.py").write_text(
        "from fastmcp import FastMCP\n"
        "import os\n"
        "mcp = FastMCP('weather')\n"
        "API_KEY = os.environ['WEATHER_API_KEY']\n\n"
        "@mcp.tool()\n"
        "def forecast(city: str) -> str:\n"
        '    """Return the weather forecast for a city."""\n'
        "    return city\n"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "first")
    return repo


@pytest.fixture
def local_repo(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(svc, "_build_clone_url", lambda _url: str(repo))
    monkeypatch.setattr(svc, "_validate_git_url", lambda _url: None)
    return repo


async def _run(sessions, sync_id, request, monkeypatch):
    import database

    monkeypatch.setattr(database, "async_session", sessions)
    return await svc.run_sync(str(sync_id), request)


async def _versions(sessions, listing_id):
    async with sessions() as session:
        rows = (await session.execute(select(McpVersion).where(McpVersion.listing_id == listing_id))).scalars().all()
        listing = await session.get(McpListing, listing_id)
        return {v.version: v for v in rows}, listing


@pytest.mark.asyncio
async def test_push_sync_publishes_new_approved_version_from_branch_tip(local_repo, monkeypatch):
    owner = _user()
    listing, ver = _listing(owner)
    sync = _sync(listing, owner, "s3cret")
    async with _database() as sessions:
        await _seed(sessions, owner, listing, ver, sync)

        outcome = await _run(sessions, sync.id, svc.SyncRequest("push", "main", "first"), monkeypatch)
        head = _git(local_repo, "rev-parse", "HEAD")
        assert outcome == svc.SyncOutcome(status="success", detail="Published version 1.0.1", version="1.0.1", sha=head)

        versions, stored = await _versions(sessions, listing.id)
        new = versions["1.0.1"]
        assert stored.latest_version_id == new.id
        assert (new.status, new.released_by, new.resolved_sha, new.source_ref) == (
            ListingStatus.approved,
            owner.id,
            head,
            "main",
        )
        assert new.tools_schema == {
            "tools": [{"name": "forecast", "description": "Return the weather forecast for a city."}]
        }
        assert versions["1.0.0"].resolved_sha is None

        # The same commit again (a redelivery) publishes nothing.
        again = await _run(sessions, sync.id, svc.SyncRequest("push", "main"), monkeypatch)
        assert again.status == "skipped" and "already version 1.0.1" in again.detail

        # A new commit that declares its own version uses it.
        (local_repo / "pyproject.toml").write_text('[project]\nname = "weather"\nversion = "1.5.0"\n')
        _git(local_repo, "add", ".")
        _git(local_repo, "commit", "-q", "-m", "bump")
        outcome = await _run(sessions, sync.id, svc.SyncRequest("push", "main", "bump"), monkeypatch)
        assert outcome.version == "1.5.0"

        async with sessions() as session:
            stored_sync = await session.get(McpWebhookSync, sync.id)
            assert (stored_sync.last_sync_status, stored_sync.last_version) == ("success", "1.5.0")


@pytest.mark.asyncio
async def test_gitlab_sync_publishes_and_says_where_it_came_from(local_repo, monkeypatch):
    owner = _user()
    listing, ver = _listing(owner, source_url=GITLAB_URL)
    sync = _sync(listing, owner, "s3cret", provider="gitlab")
    async with _database() as sessions:
        await _seed(sessions, owner, listing, ver, sync)
        outcome = await _run(sessions, sync.id, svc.SyncRequest("push", "main", "Add forecast"), monkeypatch)
        assert outcome.status == "success" and outcome.version == "1.0.1"
        versions, _ = await _versions(sessions, listing.id)
        assert versions["1.0.1"].changelog.startswith("Synced from GitLab main at ")
        assert versions["1.0.1"].changelog.endswith("Add forecast")


@pytest.mark.asyncio
async def test_default_branch_is_resolved_when_none_is_configured(local_repo, monkeypatch):
    owner = _user()
    listing, ver = _listing(owner)
    sync = _sync(listing, owner, "s3cret")
    async with _database() as sessions:
        await _seed(sessions, owner, listing, ver, sync)
        outcome = await _run(sessions, sync.id, svc.SyncRequest("manual", None), monkeypatch)
        assert outcome.status == "success"
        versions, _ = await _versions(sessions, listing.id)
        assert versions["1.0.1"].source_ref == "main"


@pytest.mark.asyncio
async def test_release_sync_uses_tag_and_does_not_demote_latest(local_repo, monkeypatch):
    owner = _user()
    listing, ver = _listing(owner, version="3.0.0")
    sync = _sync(listing, owner, "s3cret", sync_on_release=True)
    _git(local_repo, "tag", "-a", "v2.1.0", "-m", "backport")
    _git(local_repo, "tag", "v3.1.0")
    async with _database() as sessions:
        await _seed(sessions, owner, listing, ver, sync)

        backport = await _run(sessions, sync.id, svc.SyncRequest("release", "v2.1.0", "notes"), monkeypatch)
        assert backport.version == "2.1.0"
        versions, stored = await _versions(sessions, listing.id)
        assert stored.latest_version_id == ver.id, "an older release must not become latest"
        assert versions["2.1.0"].changelog == "Synced from GitHub release v2.1.0.\n\nnotes"

        newer = await _run(sessions, sync.id, svc.SyncRequest("release", "v3.1.0"), monkeypatch)
        versions, stored = await _versions(sessions, listing.id)
        assert stored.latest_version_id == versions["3.1.0"].id
        assert newer.sha == _git(local_repo, "rev-parse", "HEAD")

        dup = await _run(sessions, sync.id, svc.SyncRequest("release", "v3.1.0"), monkeypatch)
        assert dup.status == "skipped"


@pytest.mark.asyncio
async def test_fetch_failures_retry_before_failing(local_repo, monkeypatch):
    from arq import Retry

    from jobs import mcp_sync

    owner = _user()
    listing, ver = _listing(owner)
    sync = _sync(listing, owner, "s3cret")
    async with _database() as sessions:
        await _seed(sessions, owner, listing, ver, sync)
        import database

        monkeypatch.setattr(database, "async_session", sessions)
        args = (str(sync.id), "release", "v0.9.0", "")

        with pytest.raises(Retry) as retry:
            await mcp_sync.sync_mcp_webhook({"job_try": 1}, *args)
        assert retry.value.defer_score == 15_000
        async with sessions() as session:
            stored = await session.get(McpWebhookSync, sync.id)
            assert stored.last_sync_status == "queued" and stored.last_sync_error.endswith("Retrying.")

        # The tag shows up before the next attempt.
        _git(local_repo, "tag", "v0.9.0")
        await mcp_sync.sync_mcp_webhook({"job_try": 2}, *args)
        versions, _ = await _versions(sessions, listing.id)
        assert "0.9.0" in versions

        # On the last attempt a fetch failure is recorded instead of retried.
        await mcp_sync.sync_mcp_webhook({"job_try": mcp_sync.MAX_TRIES}, str(sync.id), "release", "v8.0.0", "")
        async with sessions() as session:
            stored = await session.get(McpWebhookSync, sync.id)
            assert stored.last_sync_status == "failed"
            assert stored.last_sync_error.startswith("Failed to fetch the repository")


@pytest.mark.asyncio
async def test_failures_are_recorded_for_the_owner(local_repo, monkeypatch):
    owner, former = _user("alice"), _user("bob")
    listing, ver = _listing(owner)
    sync = _sync(listing, owner, "s3cret", branch="missing-branch")
    async with _database() as sessions:
        await _seed(sessions, owner, former, listing, ver, sync)
        assert await _run(sessions, sync.id, svc.SyncRequest("push", "missing-branch"), monkeypatch) is None
        async with sessions() as session:
            stored = await session.get(McpWebhookSync, sync.id)
            assert stored.last_sync_status == "failed"
            assert stored.last_sync_error.startswith("Failed to fetch the repository")
            # Ownership is checked at sync time, not only when sync was turned on.
            stored.enabled_by = former.id
            await session.commit()
        assert await _run(sessions, sync.id, svc.SyncRequest("push", "main"), monkeypatch) is None
        async with sessions() as session:
            stored = await session.get(McpWebhookSync, sync.id)
            assert "no longer has owner access" in stored.last_sync_error
        versions, _ = await _versions(sessions, listing.id)
        assert set(versions) == {"1.0.0"}


@pytest.mark.asyncio
async def test_sync_stops_when_the_enabler_leaves_the_private_teamspace(local_repo, monkeypatch):
    owner = _user()
    team = Team(id=uuid.uuid4(), name="Weather", handle="weather", is_private=True, created_by=owner.id)
    membership = TeamMembership(id=uuid.uuid4(), team_id=team.id, user_id=owner.id)
    listing, ver = _listing(owner)
    listing.is_private, listing.team_id = True, team.id
    sync = _sync(listing, owner, "s3cret")
    async with _database() as sessions:
        await _seed(sessions, owner, team, membership, listing, ver, sync)
        assert (await _run(sessions, sync.id, svc.SyncRequest("push", "main"), monkeypatch)).status == "success"

        # Still the listing's submitter, so "owner" by permission, but no longer in the teamspace.
        async with sessions() as session:
            await session.delete(await session.get(TeamMembership, membership.id))
            await session.commit()
        _git(local_repo, "commit", "-q", "--allow-empty", "-m", "after removal")
        assert await _run(sessions, sync.id, svc.SyncRequest("push", "main"), monkeypatch) is None
        async with sessions() as session:
            assert "no longer has owner access" in (await session.get(McpWebhookSync, sync.id)).last_sync_error
        versions, _ = await _versions(sessions, listing.id)
        assert set(versions) == {"1.0.0", "1.0.1"}


@pytest.mark.asyncio
async def test_removal_from_the_teamspace_during_a_fetch_stops_the_publish(local_repo, monkeypatch):
    import asyncio
    import threading

    owner = _user()
    team = Team(id=uuid.uuid4(), name="Weather", handle="weather", is_private=True, created_by=owner.id)
    membership = TeamMembership(id=uuid.uuid4(), team_id=team.id, user_id=owner.id)
    listing, ver = _listing(owner)
    listing.is_private, listing.team_id = True, team.id
    sync = _sync(listing, owner, "s3cret")
    real_fetch = svc._fetch_checkout
    fetched, release = threading.Event(), threading.Event()

    def slow_fetch(*args):
        result = real_fetch(*args)
        fetched.set()
        release.wait(timeout=30)
        return result

    async with _database() as sessions:
        await _seed(sessions, owner, team, membership, listing, ver, sync)
        monkeypatch.setattr(svc, "_fetch_checkout", slow_fetch)
        job = asyncio.create_task(_run(sessions, sync.id, svc.SyncRequest("push", "main"), monkeypatch))
        while not fetched.is_set():
            await asyncio.sleep(0.01)

        # Passed the check before fetching, then removed from the teamspace mid-fetch.
        async with sessions() as session:
            await session.delete(await session.get(TeamMembership, membership.id))
            await session.commit()
        release.set()
        assert await job is None
        async with sessions() as session:
            assert "no longer has owner access" in (await session.get(McpWebhookSync, sync.id)).last_sync_error
        versions, _ = await _versions(sessions, listing.id)
        assert set(versions) == {"1.0.0"}


@pytest.mark.asyncio
async def test_an_older_fetch_that_finishes_last_is_not_published(local_repo, monkeypatch):
    import asyncio
    import threading

    owner = _user()
    listing, ver = _listing(owner)
    sync = _sync(listing, owner, "s3cret")
    real_fetch = svc._fetch_checkout
    fetched, release = threading.Event(), threading.Event()

    def slow_fetch(*args):
        result = real_fetch(*args)
        fetched.set()
        release.wait(timeout=30)
        return result

    async with _database() as sessions:
        await _seed(sessions, owner, listing, ver, sync)
        first_sha = _git(local_repo, "rev-parse", "HEAD")
        monkeypatch.setattr(svc, "_fetch_checkout", slow_fetch)
        slow = asyncio.create_task(_run(sessions, sync.id, svc.SyncRequest("push", "main"), monkeypatch))
        while not fetched.is_set():
            await asyncio.sleep(0.01)

        # A second push lands and syncs completely while the first is still in flight.
        monkeypatch.setattr(svc, "_fetch_checkout", real_fetch)
        _git(local_repo, "commit", "-q", "--allow-empty", "-m", "second")
        newer = await _run(sessions, sync.id, svc.SyncRequest("push", "main"), monkeypatch)
        assert (newer.status, newer.version) == ("success", "1.0.1")

        release.set()
        late = await slow
        assert late.status == "skipped" and first_sha[:12] in late.detail
        versions, stored = await _versions(sessions, listing.id)
        assert set(versions) == {"1.0.0", "1.0.1"}
        assert stored.latest_version_id == versions["1.0.1"].id
        async with sessions() as session:
            assert (await session.get(McpWebhookSync, sync.id)).last_synced_sha == newer.sha
