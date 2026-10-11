# SPDX-FileCopyrightText: 2025 Observal Contributors
# SPDX-FileCopyrightText: 2026 Riya Rani <rr1182764@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Backend integration tests — real HTTP against a running Docker stack.

These tests verify the full request → DB → response cycle without mocking.
Requires: `make up` (Docker stack running on localhost:8000).

Run:
    cd observal-server
    uv run --with pytest --with pytest-asyncio --with httpx pytest ../tests/test_integration.py -v
"""

import os
import uuid

import httpx
import pytest

BASE = os.getenv("INTEGRATION_BASE_URL", "http://localhost:8000")
ADMIN_EMAIL = os.getenv("INTEGRATION_ADMIN_EMAIL", "admin@demo.example")
ADMIN_PASSWORD = os.getenv("INTEGRATION_ADMIN_PASSWORD", "admin-changeme")


def _api_reachable() -> bool:
    """Check if the API server is running."""
    try:
        r = httpx.get(f"{BASE}/health", timeout=2)
        return r.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not _api_reachable(), reason="Docker stack not running (make up)"),
]

# Login once at import time to avoid rate limiting
_token_cache: str | None = None


async def _get_token() -> str:
    global _token_cache
    if _token_cache:
        return _token_cache
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
        for attempt in range(3):
            r = await c.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
            if r.status_code == 200:
                _token_cache = r.json()["access_token"]
                return _token_cache
            if r.status_code == 429:
                import asyncio

                await asyncio.sleep(15)
                continue
            raise AssertionError(f"Login failed: {r.text}")
    raise AssertionError("Login failed after retries (rate limited)")


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
async def admin_headers():
    token = await _get_token()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
async def client():
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
        yield c


# ── Auth Round-Trip ──────────────────────────────────────────────────────────


class TestAuth:
    """Auth lifecycle: login → whoami → token refresh → logout."""

    @pytest.mark.asyncio
    async def test_login_returns_token(self, client):
        r = await client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200
        data = r.json()
        assert "access_token" in data
        assert "user" in data
        assert data["user"]["role"] in ("admin", "developer", "user")

    @pytest.mark.asyncio
    async def test_whoami(self, client, admin_headers):
        r = await client.get("/api/v1/auth/whoami", headers=admin_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["email"] == ADMIN_EMAIL
        assert "role" in data

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, client):
        r = await client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"})
        assert r.status_code in (401, 403, 429)

    @pytest.mark.asyncio
    async def test_whoami_no_token(self, client):
        r = await client.get("/api/v1/auth/whoami")
        assert r.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_token_refresh(self, client):
        # Login to get refresh token
        r = await client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        refresh_token = r.json().get("refresh_token")
        if not refresh_token:
            pytest.skip("No refresh token returned")
        r2 = await client.post("/api/v1/auth/token/refresh", json={"refresh_token": refresh_token})
        assert r2.status_code == 200
        assert "access_token" in r2.json()


# ── MCP CRUD Lifecycle ───────────────────────────────────────────────────────


async def _publish_own_review(client, headers, number):
    """A single-account integration fixture requires the audited super-admin exception."""
    me = await client.get("/api/v1/auth/whoami", headers=headers)
    if me.json()["role"] != "super_admin":
        pytest.skip("Own-work publishing requires a super-admin override or an independent reviewer")
    result = await client.post(
        f"/api/v1/reviews/{number}/publish",
        headers=headers,
        json={"override_reason": "Single-account integration verification"},
    )
    assert result.status_code == 200, result.text
    return result


class TestMcpCrud:
    """MCP lifecycle: submit → review → publish → list."""

    @pytest.fixture(autouse=True)
    def _mcp_name(self):
        self.mcp_name = f"integ-mcp-{uuid.uuid4().hex[:8]}"

    @pytest.mark.asyncio
    async def test_submit_mcp(self, client, admin_headers):
        r = await client.post(
            "/api/v1/mcps/submit",
            headers=admin_headers,
            json={
                "name": self.mcp_name,
                "version": "1.0.0",
                "description": "Integration test MCP",
                "owner": "admin",
                "category": "developer-tools",
                "git_url": "https://github.com/example/repo.git",
                "command": "node",
                "args": ["index.js"],
                "environment_variables": [
                    {"name": "API_KEY", "description": "Key", "required": True},
                ],
            },
        )
        assert r.status_code == 200, f"Submit failed: {r.text}"
        data = r.json()
        assert data["name"] == self.mcp_name
        assert data["status"] == "pending"
        assert data["review_number"] and data["review_url"] == f"/review/{data['review_number']}"

    @pytest.mark.asyncio
    async def test_get_mcp_by_name(self, client, admin_headers):
        # Submit first
        await client.post(
            "/api/v1/mcps/submit",
            headers=admin_headers,
            json={
                "name": self.mcp_name,
                "version": "1.0.0",
                "description": "Test",
                "owner": "admin",
                "category": "developer-tools",
                "git_url": "https://github.com/example/repo.git",
                "command": "node",
                "args": ["index.js"],
            },
        )
        r = await client.get(f"/api/v1/mcps/{self.mcp_name}", headers=admin_headers)
        assert r.status_code == 200
        assert r.json()["name"] == self.mcp_name

    @pytest.mark.asyncio
    async def test_approve_mcp(self, client, admin_headers):
        # Submit
        created = await client.post(
            "/api/v1/mcps/submit",
            headers=admin_headers,
            json={
                "name": self.mcp_name,
                "version": "1.0.0",
                "description": "Test approve",
                "owner": "admin",
                "category": "developer-tools",
                "git_url": "https://github.com/example/repo.git",
                "command": "node",
                "args": ["index.js"],
            },
        )
        # Verdict and publication are separate operations; a single-account
        # integration test can publish only through the audited exception.
        number = created.json()["review_number"]
        detail = await client.get(f"/api/v1/reviews/{number}", headers=admin_headers)
        assert detail.status_code == 200 and detail.json()["state"] == "open"
        r = await _publish_own_review(client, admin_headers, number)
        assert r.json()["state"] == "published"

        # Verify status changed
        r2 = await client.get(f"/api/v1/mcps/{self.mcp_name}", headers=admin_headers)
        assert r2.json()["status"] == "approved"

    @pytest.mark.asyncio
    async def test_submit_invalid_category(self, client, admin_headers):
        r = await client.post(
            "/api/v1/mcps/submit",
            headers=admin_headers,
            json={
                "name": self.mcp_name,
                "version": "1.0.0",
                "description": "Bad category",
                "owner": "admin",
                "category": "not-a-real-category",
                "git_url": "https://github.com/example/repo.git",
                "command": "node",
                "args": ["index.js"],
            },
        )
        assert r.status_code == 422


# ── Agent Lifecycle ──────────────────────────────────────────────────────────


class TestAgentLifecycle:
    """Agent: create → approve → get → delete."""

    @pytest.fixture(autouse=True)
    def _agent_name(self):
        self.agent_name = f"integ-agent-{uuid.uuid4().hex[:8]}"

    @pytest.mark.asyncio
    async def test_create_and_approve_agent(self, client, admin_headers):
        # Create
        r = await client.post(
            "/api/v1/agents",
            headers=admin_headers,
            json={
                "name": self.agent_name,
                "description": "Integration test agent",
                "version": "1.0.0",
                "owner": "admin",
                "model_name": "claude-sonnet-4-20250514",
                "prompt": "You are a test agent.",
            },
        )
        assert r.status_code == 200, f"Create failed: {r.text}"
        assert r.json()["name"] == self.agent_name
        agent_id = r.json()["id"]

        assert r.json()["review_number"]
        await _publish_own_review(client, admin_headers, r.json()["review_number"])

        # Verify in list
        r3 = await client.get("/api/v1/agents", headers=admin_headers)
        names = [a["name"] for a in r3.json()]
        assert self.agent_name in names

    @pytest.mark.asyncio
    async def test_delete_agent(self, client, admin_headers):
        # Create
        created = await client.post(
            "/api/v1/agents",
            headers=admin_headers,
            json={
                "name": self.agent_name,
                "description": "To delete",
                "version": "1.0.0",
                "owner": "admin",
                "model_name": "claude-sonnet-4-20250514",
                "prompt": "You are a test agent.",
            },
        )
        agent_id = created.json()["id"]
        r = await client.delete(f"/api/v1/agents/{self.agent_name}", headers=admin_headers)
        assert r.status_code == 200

        r2 = await client.get(f"/api/v1/agents/{self.agent_name}", headers=admin_headers)
        assert r2.status_code == 404

        deleted = await client.get("/api/v1/agents/deleted", headers=admin_headers)
        assert deleted.status_code == 200
        assert self.agent_name in {a["name"] for a in deleted.json()}

        recreated = await client.post(
            "/api/v1/agents",
            headers=admin_headers,
            json={
                "name": self.agent_name,
                "description": "Reused name",
                "version": "1.0.0",
                "owner": "admin",
                "model_name": "claude-sonnet-4-20250514",
                "prompt": "You are a test agent.",
            },
        )
        assert recreated.status_code == 200, recreated.text

        restore = await client.patch(f"/api/v1/agents/{agent_id}/restore", headers=admin_headers, json={})
        assert restore.status_code == 409


# ── Prompt CRUD ──────────────────────────────────────────────────────────────


class TestPromptCrud:
    """Prompt: submit → get → delete."""

    @pytest.fixture(autouse=True)
    def _prompt_name(self):
        self.prompt_name = f"integ-prompt-{uuid.uuid4().hex[:8]}"

    @pytest.mark.asyncio
    async def test_submit_and_get_prompt(self, client, admin_headers):
        r = await client.post(
            "/api/v1/prompts/submit",
            headers=admin_headers,
            json={
                "name": self.prompt_name,
                "version": "1.0.0",
                "description": "Integration test prompt",
                "owner": "admin",
                "category": "general",
                "template": "You are a helpful assistant. Summarize: {{input}}",
            },
        )
        assert r.status_code == 200, f"Submit failed: {r.text}"

        r2 = await client.get(f"/api/v1/prompts/{self.prompt_name}", headers=admin_headers)
        assert r2.status_code == 200
        assert r2.json()["name"] == self.prompt_name


# ── Feedback ─────────────────────────────────────────────────────────────────


class TestFeedback:
    """Feedback: rate an MCP → confirm rating appears."""

    @pytest.mark.asyncio
    async def test_rate_mcp_and_verify(self, client, admin_headers):
        mcp_name = f"integ-fb-{uuid.uuid4().hex[:8]}"
        # Submit MCP
        r = await client.post(
            "/api/v1/mcps/submit",
            headers=admin_headers,
            json={
                "name": mcp_name,
                "version": "1.0.0",
                "description": "For feedback test",
                "owner": "admin",
                "category": "developer-tools",
                "git_url": "https://github.com/example/repo.git",
                "command": "node",
                "args": ["index.js"],
            },
        )
        mcp_id = r.json()["id"]

        # Rate it
        r2 = await client.post(
            "/api/v1/feedback",
            headers=admin_headers,
            json={
                "listing_id": mcp_id,
                "listing_type": "mcp",
                "rating": 5,
                "comment": "Great MCP!",
            },
        )
        assert r2.status_code in (200, 201), f"Feedback failed: {r2.text}"

        # Verify feedback appears
        r3 = await client.get(f"/api/v1/feedback/mcp/{mcp_id}", headers=admin_headers)
        assert r3.status_code == 200
        ratings = r3.json()
        assert any(fb["rating"] == 5 for fb in ratings)


# ── List / Sort / Pagination ─────────────────────────────────────────────────


class TestListAndSort:
    """List MCPs with sorting and pagination."""

    @pytest.mark.asyncio
    async def test_list_mcps_with_limit(self, client, admin_headers):
        r = await client.get("/api/v1/mcps", headers=admin_headers, params={"limit": 2})
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    @pytest.mark.asyncio
    async def test_list_mcps_with_search(self, client, admin_headers):
        # Create a uniquely named MCP
        name = f"searchable-{uuid.uuid4().hex[:8]}"
        created = await client.post(
            "/api/v1/mcps/submit",
            headers=admin_headers,
            json={
                "name": name,
                "version": "1.0.0",
                "description": "Searchable",
                "owner": "admin",
                "category": "developer-tools",
                "git_url": "https://github.com/example/repo.git",
                "command": "node",
                "args": ["index.js"],
            },
        )
        # Publish so it shows in the public list.
        await _publish_own_review(client, admin_headers, created.json()["review_number"])

        r = await client.get("/api/v1/mcps", headers=admin_headers, params={"search": name})
        assert r.status_code == 200
        names = [m["name"] for m in r.json()]
        assert name in names


# ── RBAC ─────────────────────────────────────────────────────────────────────


class TestRbac:
    """Non-admin cannot approve or delete others' items."""

    @pytest.mark.asyncio
    async def test_unauthenticated_cannot_submit(self, client):
        r = await client.post(
            "/api/v1/mcps/submit",
            json={
                "name": "should-fail",
                "version": "1.0.0",
                "description": "No auth",
                "owner": "admin",
                "category": "developer-tools",
                "git_url": "https://github.com/example/repo.git",
                "command": "node",
                "args": ["index.js"],
            },
        )
        assert r.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_unauthenticated_cannot_approve(self, client):
        r = await client.post("/api/v1/reviews/42/submissions", json={"verdict": "approve"})
        assert r.status_code in (401, 403)


# ── Error Cases ──────────────────────────────────────────────────────────────


class TestErrorCases:
    """Proper error responses for invalid input."""

    @pytest.mark.asyncio
    async def test_missing_required_fields(self, client, admin_headers):
        r = await client.post("/api/v1/mcps/submit", headers=admin_headers, json={"name": "x"})
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_get_nonexistent_mcp(self, client, admin_headers):
        r = await client.get(f"/api/v1/mcps/{uuid.uuid4()}", headers=admin_headers)
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_approve_nonexistent(self, client, admin_headers):
        r = await client.post(
            f"/api/v1/reviews/{uuid.uuid4()}/submissions", headers=admin_headers, json={"verdict": "approve"}
        )
        assert r.status_code == 404
