# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Standalone component installs honour a requested version and report what they installed."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routes import hook, mcp, skill
from models.hook import HookVersion
from models.mcp import ListingStatus, McpVersion
from schemas.hook import HookInstallRequest
from schemas.mcp import McpInstallRequest
from schemas.skill import SkillInstallRequest
from tests import discovery_support as ds


@pytest.fixture
async def session():
    from models.hook import HookDownload
    from models.mcp import McpDownload
    from models.skill import SkillDownload

    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    async with engine.begin() as conn:
        for table in (McpDownload.__table__, HookDownload.__table__, SkillDownload.__table__):
            await conn.run_sync(table.create)
    async with maker() as db:
        yield db
    await engine.dispose()


def _endpoints():
    return patch("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "http://observal.test"}))


async def test_mcp_install_generates_the_requested_version(session):
    owner = await ds.user(session)
    listing = await ds.mcp(session, owner)  # 1.4.2 server-github
    newer = McpVersion(
        id=uuid.uuid4(),
        listing_id=listing.id,
        version="2.0.0",
        description="new",
        status=ListingStatus.approved,
        transport="stdio",
        command="npx",
        args=["-y", "@acme/server-github@2"],
        released_by=owner.id,
        released_at=ds.NOW,
    )
    session.add(newer)
    await session.flush()
    listing.latest_version_id = newer.id
    await session.commit()

    with _endpoints():
        latest = await mcp.install_mcp(str(listing.id), McpInstallRequest(harness="cursor"), None, session, owner)
        pinned = await mcp.install_mcp(
            str(listing.id), McpInstallRequest(harness="cursor", version="1.4.2"), None, session, owner
        )
        with pytest.raises(HTTPException) as missing:
            await mcp.install_mcp(
                str(listing.id), McpInstallRequest(harness="cursor", version="9.9.9"), None, session, owner
            )

    assert (latest.version, latest.version_id) == ("2.0.0", newer.id)
    assert "@acme/server-github@2" in json.dumps(latest.config_snippet)
    assert pinned.version == "1.4.2"
    assert "@modelcontextprotocol/server-github" in json.dumps(pinned.config_snippet)
    assert "@acme/server-github@2" not in json.dumps(pinned.config_snippet)
    assert pinned.digest != latest.digest
    assert missing.value.status_code == 404


async def test_hook_install_generates_the_requested_version(session):
    owner = await ds.user(session)
    listing = await ds.hook(session, owner)  # 2.0.0 "npm run lint"
    newer = HookVersion(
        id=uuid.uuid4(),
        listing_id=listing.id,
        version="3.0.0",
        description="new",
        status=ListingStatus.approved,
        event="PostToolUse",
        handler_type="command",
        handler_config={"command": "npm run lint:strict"},
        released_by=owner.id,
        released_at=ds.NOW,
    )
    session.add(newer)
    await session.flush()
    listing.latest_version_id = newer.id
    await session.commit()

    pinned = await hook.install_hook(
        str(listing.id), HookInstallRequest(harness="claude-code", version="2.0.0"), None, session, owner
    )

    assert pinned.version == "2.0.0"
    assert "npm run lint" in json.dumps(pinned.config_snippet)
    assert "lint:strict" not in json.dumps(pinned.config_snippet)


async def test_skill_install_reports_its_version_without_a_request(session):
    owner = await ds.user(session)
    listing = await ds.skill(session, owner, version="1.2.0")
    await session.commit()

    with _endpoints():
        response = await skill.install_skill(
            str(listing.id), SkillInstallRequest(harness="claude-code"), None, session, owner
        )

    assert response.version == "1.2.0"
    assert response.config_snippet["skill"]["version"] == "1.2.0"
    assert response.digest.startswith("sha256:")


async def test_kiro_skill_install_returns_user_scope_and_script(session):
    owner = await ds.user(session)
    listing = await ds.skill(
        session, owner, version="1.2.0", content="---\nname: review\ndescription: Review\n---\n# Review\n"
    )
    await session.commit()

    with _endpoints():
        response = await skill.install_skill(
            str(listing.id), SkillInstallRequest(harness="kiro", scope="user"), None, session, owner
        )

    assert response.version == "1.2.0"
    assert response.config_snippet["skills"]["path"] == "~/.kiro/skills/security-review/SKILL.md"
