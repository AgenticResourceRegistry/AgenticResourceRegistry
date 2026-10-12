# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Agent installs generate every component from the version the agent release pinned.

Before pins were enforced, only sandboxes honoured them: MCP servers, skills,
hooks, and prompts were generated from each listing's latest release, so the
same agent version installed different content as soon as a component shipped
a new version.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.deps import get_db, get_registry_user
from api.routes.agent._router import router as agent_router
from api.routes.agent.install import install_agent
from models.agent import AgentStatus, AgentVersion
from models.agent_component import AgentComponent
from models.hook import HookVersion
from models.mcp import ListingStatus, McpVersion
from models.prompt import PromptVersion
from models.sandbox import SandboxVersion
from models.skill import SkillVersion
from schemas.agent import AgentInstallRequest
from services.agent_lock import VERSION_MODELS, content_digest, lock_agent_version
from services.skill_revisions import skill_content_revision
from tests import discovery_support as ds

CONTRACT = json.loads((Path(__file__).parent / "fixtures" / "skill_folder_install_contract.json").read_text())


@pytest.fixture
async def session():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    async with maker() as db:
        yield db
    await engine.dispose()


def _new_release(kind: str, listing, owner, marker: str):
    common = {
        "id": uuid.uuid4(),
        "listing_id": listing.id,
        "version": "9.0.0",
        "description": "newer release",
        "status": ListingStatus.approved,
        "released_by": owner.id,
        "released_at": ds.NOW,
    }
    if kind == "mcp":
        return McpVersion(**common, transport="stdio", command="npx", args=["-y", marker])
    if kind == "skill":
        return SkillVersion(
            **common, task_type="code_review", delivery_mode="registry_direct", skill_md_content=f"---\n---\n{marker}"
        )
    if kind == "hook":
        return HookVersion(**common, event="PostToolUse", handler_type="command", handler_config={"command": marker})
    if kind == "prompt":
        return PromptVersion(**common, category="documentation", template=marker)
    return SandboxVersion(**common, runtime_type="docker", image=marker, network_policy="none")


_FACTORIES = {
    "mcp": (ds.mcp, "@modelcontextprotocol/server-github"),
    "skill": (ds.skill, "Look for auth bugs."),
    "hook": (ds.hook, "npm run lint"),
    "prompt": (ds.prompt, "Write release notes for"),
    "sandbox": (ds.sandbox, "python:3.12-slim"),
}


async def _pinned_agent(db, owner, kind: str):
    factory, pinned_marker = _FACTORIES[kind]
    listing = await factory(db, owner)
    agent = await ds.agent(db, owner, components=[(kind, listing.id, listing.name)])
    link = (await db.execute(select(AgentComponent))).scalar_one()
    version_model = VERSION_MODELS[kind]
    pinned = (await db.execute(select(version_model).where(version_model.id == listing.latest_version_id))).scalar_one()
    link.resolved_version = pinned.version
    version = (await db.execute(select(AgentVersion))).scalar_one()
    await lock_agent_version(db, agent, version)
    await db.commit()
    return listing, agent, pinned_marker


async def _install(db, agent, user, **request):
    harness = request.pop("harness", "claude-code")
    options = request.pop("options", {"scope": "project"})
    rollout_enabled = request.pop("rollout_enabled", False)
    import services.dynamic_settings as settings

    original_setting = settings.get_bool

    async def setting(key, default=None):
        if key == "registry.skill_folder_delivery_enabled":
            return rollout_enabled
        return await original_setting(key, default)

    with (
        patch("api.routes.agent.install._ds.get_bool", side_effect=setting),
        patch("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "http://observal.test"})),
        patch("services.download_tracker.record_agent_download", AsyncMock()),
    ):
        return await install_agent(
            str(agent.id),
            AgentInstallRequest(harness=harness, options=options, **request),
            request=None,
            db=db,
            current_user=user,
        )


@pytest.mark.parametrize("kind", sorted(_FACTORIES))
async def test_install_uses_the_pinned_release_after_a_newer_one_is_approved(session, kind):
    owner = await ds.user(session)
    listing, agent, pinned_marker = await _pinned_agent(session, owner, kind)
    newer = _new_release(kind, listing, owner, "NEWER-RELEASE-MARKER")
    session.add(newer)
    await session.flush()
    listing.latest_version_id = newer.id
    await session.commit()

    response = await _install(session, agent, owner)

    generated = json.dumps(response.config_snippet)
    assert "NEWER-RELEASE-MARKER" not in generated
    assert pinned_marker in generated
    assert response.version == "3.1.0"
    assert response.lock["status"] == "locked"
    assert response.lock["problems"] == []
    assert response.lock["components"][0]["source"] == "lock"
    assert response.lock["digest"].startswith("sha256:")


async def test_http_pinned_agent_returns_complete_binary_folder_and_refuses_old_client(session):
    owner = await ds.user(session)
    listing, agent, _ = await _pinned_agent(session, owner, "skill")
    pinned = await session.get(SkillVersion, listing.latest_version_id)
    pinned.extra_files = [{"path": "assets/icon.bin", "content": "AP8=", "encoding": "base64"}]
    pinned.content_revision = skill_content_revision(listing, pinned)
    version = (await session.execute(select(AgentVersion))).scalar_one()
    await lock_agent_version(session, agent, version)
    await session.commit()

    app = FastAPI()
    app.include_router(agent_router)

    async def db_session():
        yield session

    app.dependency_overrides[get_db] = db_session
    app.dependency_overrides[get_registry_user] = lambda: owner
    settings_key = "registry.skill_folder_delivery_enabled"
    import services.dynamic_settings as settings

    original = settings.get_bool

    async def enabled(key, default=None):
        return True if key == settings_key else await original(key, default)

    with (
        patch("api.routes.agent.install._ds.get_bool", side_effect=enabled),
        patch("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "http://observal.test"})),
        patch("services.download_tracker.record_agent_download", AsyncMock()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            route = f"/api/v1/agents/{agent.id}/install"
            old = await client.post(route, json={"harness": "pi", "options": {"scope": "project"}})
            assert old.status_code == 409
            assert old.json() == {"detail": CONTRACT["refusals"]["old_client_agent"]["detail"]}
            opted = await client.post(
                route,
                json={"harness": "pi", "options": {"scope": "project"}, "supported_features": ["skill_extra_files_v1"]},
            )
            assert opted.status_code == 200, opted.text
            body = opted.json()
            assert len(body["skill_bundles"]) == 1
            bundle = body["skill_bundles"][0]
            assert bundle["listing_id"] == str(listing.id)
            assert bundle["version_id"] == str(pinned.id)
            assert [file["path"] for file in bundle["files"]] == ["SKILL.md", "assets/icon.bin"]
            assert bundle["files"][1]["content"] == "AP8="
            assert bundle["files"][1]["mode"] == "0644"
            assert body["lock"]["components"][0]["digest"] == bundle["digest"]
            assert body["config_snippet"]["skill_components"][0]["bundle_version_id"] == str(pinned.id)
            assert "skills" not in body["config_snippet"]


async def test_opted_in_resource_less_direct_agent_still_declares_skill_md(session):
    owner = await ds.user(session)
    listing, agent, _ = await _pinned_agent(session, owner, "skill")
    legacy = await _install(session, agent, owner)
    assert legacy.skill_bundles == []
    opted = await _install(session, agent, owner, supported_features=["skill_extra_files_v1"])
    assert len(opted.skill_bundles) == 1
    folder = opted.skill_bundles[0]
    assert folder.listing_id == listing.id
    assert [file.path for file in folder.files] == ["SKILL.md"]
    assert folder.digest.startswith("sha256:")  # Stable resource-less v1 pin.


@pytest.mark.parametrize(
    "harness", ["claude-code", "pi", "codex", "copilot", "copilot-cli", "opencode", "antigravity", "goose"]
)
@pytest.mark.parametrize("scope", ["project", "user"])
async def test_agent_bundle_preserves_pinned_tree_for_every_skill_harness(session, harness, scope):
    owner = await ds.user(session)
    listing, agent, _ = await _pinned_agent(session, owner, "skill")
    pinned = (
        await session.execute(select(SkillVersion).where(SkillVersion.id == listing.latest_version_id))
    ).scalar_one()
    pinned.extra_files = [
        {"path": "templates/a.txt", "content": "first"},
        {"path": "scripts/run.sh", "content": "echo hi", "executable": True},
    ]
    pinned.content_revision = skill_content_revision(listing, pinned)
    version = (await session.execute(select(AgentVersion))).scalar_one()
    await lock_agent_version(session, agent, version)
    await session.commit()

    with pytest.raises(HTTPException, match="skill_extra_files_v1") as old_client:
        await _install(session, agent, owner, harness=harness)
    assert old_client.value.detail == CONTRACT["refusals"]["old_client_agent"]["detail"]
    with pytest.raises(HTTPException, match="disabled until fleet rollout") as not_rolled_out:
        await _install(
            session,
            agent,
            owner,
            harness=harness,
            options={"scope": scope},
            supported_features=["skill_extra_files_v1"],
        )
    assert not_rolled_out.value.detail == CONTRACT["refusals"]["not_rolled_out_agent"]["detail"]
    if scope == "user" and harness in {"copilot", "copilot-cli"}:
        with pytest.raises(HTTPException, match="different Agent scope") as unsupported_scope:
            await _install(
                session,
                agent,
                owner,
                harness=harness,
                options={"scope": scope},
                supported_features=["skill_extra_files_v1"],
                rollout_enabled=True,
            )
        assert unsupported_scope.value.status_code == 409
        return
    response = await _install(
        session,
        agent,
        owner,
        harness=harness,
        options={"scope": scope},
        supported_features=["skill_extra_files_v1"],
        rollout_enabled=True,
    )
    assert len(response.skill_bundles) == 1
    folder = response.skill_bundles[0]
    assert folder.version_id == pinned.id
    assert folder.digest == content_digest("skill", pinned)
    assert folder.skill_file_path.endswith("/SKILL.md")
    assert {file.path for file in folder.files} == {"SKILL.md", "templates/a.txt", "scripts/run.sh"}
    assert response.lock["components"][0]["digest"] == folder.digest
    assert "first" not in json.dumps(response.config_snippet)
    assert "skill_md_content" not in json.dumps(response.config_snippet)
    assert all(file["path"] != folder.skill_file_path for file in response.config_snippet.get("skills", []))


@pytest.mark.parametrize("harness", ["kiro", "cursor"])
async def test_bundle_refuses_harness_without_verified_skills_capability(session, harness):
    owner = await ds.user(session)
    listing, agent, _ = await _pinned_agent(session, owner, "skill")
    pinned = (
        await session.execute(select(SkillVersion).where(SkillVersion.id == listing.latest_version_id))
    ).scalar_one()
    pinned.extra_files = [{"path": "templates/a.txt", "content": "first"}]
    pinned.content_revision = skill_content_revision(listing, pinned)
    version = (await session.execute(select(AgentVersion))).scalar_one()
    await lock_agent_version(session, agent, version)
    await session.commit()
    with pytest.raises(HTTPException, match="does not support complete skill folders") as refused:
        await _install(
            session, agent, owner, harness=harness, supported_features=["skill_extra_files_v1"], rollout_enabled=True
        )
    assert refused.value.status_code == 409
    assert refused.value.detail == CONTRACT["refusals"]["unsupported_skills_harness_agent"]["detail"]


@pytest.mark.parametrize("resource", ["extra", "empty_script"])
async def test_pinned_direct_resource_is_refused_before_config_and_download(session, resource):
    owner = await ds.user(session)
    listing, agent, _ = await _pinned_agent(session, owner, "skill")
    pinned = (
        await session.execute(select(SkillVersion).where(SkillVersion.id == listing.latest_version_id))
    ).scalar_one()
    pinned.delivery_mode = "registry_direct"
    pinned.skill_md_content = "# Pinned\n"
    if resource == "extra":
        pinned.extra_files = [{"path": "assets/icon.bin", "content": "AP8=", "encoding": "base64"}]
    else:
        pinned.script_filename = "empty.sh"
        pinned.script_content = ""
    newer = _new_release("skill", listing, owner, "resource-less latest")
    session.add(newer)
    await session.flush()
    listing.latest_version_id = newer.id
    await session.commit()

    with patch("api.routes.agent.install.generate_agent_config") as generate:
        with pytest.raises(HTTPException) as refused:
            await _install(session, agent, owner)
        generate.assert_not_called()
    assert refused.value.status_code == 409
    assert refused.value.detail == CONTRACT["refusals"]["stale_v2_pin_agent"]["detail"].replace("Example", listing.name)


async def test_v2_skill_pin_cannot_be_downgraded_to_resource_less_in_non_strict_install(session):
    owner = await ds.user(session)
    listing, agent, _ = await _pinned_agent(session, owner, "skill")
    pinned = await session.get(SkillVersion, listing.latest_version_id)
    pinned.extra_files = [{"path": "assets/icon.bin", "content": "AP8=", "encoding": "base64"}]
    pinned.content_revision = skill_content_revision(listing, pinned)
    version = (await session.execute(select(AgentVersion))).scalar_one()
    await lock_agent_version(session, agent, version)
    link = (await session.execute(select(AgentComponent))).scalar_one()
    assert link.resolved_digest.startswith("observal-content-v2:")
    await session.commit()

    # Simulate a historical row changed after approval: the current row now
    # looks resource-less, but the agent approved a v2 whole-folder pin.
    pinned.extra_files = []
    await session.commit()
    with patch("api.routes.agent.install.generate_agent_config") as generate:
        with pytest.raises(HTTPException) as refused:
            await _install(session, agent, owner, strict=False)
        generate.assert_not_called()
    assert refused.value.status_code == 409
    assert "pinned folder differs" in refused.value.detail


@pytest.mark.parametrize(
    "status,marked", [(ListingStatus.pending, False), (ListingStatus.pending, True), (ListingStatus.approved, True)]
)
async def test_non_strict_install_never_serves_unapproved_or_marked_skill(session, status, marked):
    owner = await ds.user(session)
    listing, agent, _ = await _pinned_agent(session, owner, "skill")
    pinned = (
        await session.execute(select(SkillVersion).where(SkillVersion.id == listing.latest_version_id))
    ).scalar_one()
    pinned.status = status
    pinned.requires_global_review = marked
    await session.commit()

    with patch("api.routes.agent.install.generate_agent_config") as generate:
        with pytest.raises(HTTPException) as refused:
            await _install(session, agent, owner)
        generate.assert_not_called()
    assert refused.value.status_code == 409
    assert "not approved or requires public review" in refused.value.detail


async def test_missing_explicit_skill_uuid_never_substitutes_semver_or_latest_in_non_strict_mode(session):
    owner = await ds.user(session)
    listing, agent, _ = await _pinned_agent(session, owner, "skill")
    link = (await session.execute(select(AgentComponent).where(AgentComponent.component_id == listing.id))).scalar_one()
    assert link.resolved_version == "1.2.0"
    link.resolved_version_id = uuid.uuid4()
    await session.commit()

    with patch("api.routes.agent.install.generate_agent_config") as generate:
        with pytest.raises(HTTPException) as refused:
            await _install(session, agent, owner, strict=False)
        generate.assert_not_called()
    assert refused.value.status_code == 409
    assert "pinned version no longer exists" in refused.value.detail


async def test_legacy_pin_falls_back_to_latest_with_a_warning_and_strict_refuses(session):
    owner = await ds.user(session)
    listing = await ds.mcp(session, owner)
    agent = await ds.agent(session, owner, components=[("mcp", listing.id, "GitHub")])
    link = (await session.execute(select(AgentComponent))).scalar_one()
    link.resolved_version = "latest"
    await session.commit()

    response = await _install(session, agent, owner)
    with pytest.raises(HTTPException) as refused:
        await _install(session, agent, owner, strict=True)

    assert response.lock["status"] == "unlocked"
    assert response.lock["components"][0]["source"] == "fallback-latest"
    assert any("has no locked version" in warning for warning in response.warnings)
    assert refused.value.status_code == 409
    assert refused.value.detail.startswith("Strict install refused: mcp 'GitHub' is not locked.")


async def test_content_changed_after_locking_is_reported(session):
    owner = await ds.user(session)
    listing, agent, _marker = await _pinned_agent(session, owner, "mcp")
    pinned = (await session.execute(select(McpVersion).where(McpVersion.listing_id == listing.id))).scalar_one()
    pinned.args = ["-y", "@evil/server-github"]
    await session.commit()

    response = await _install(session, agent, owner)
    with pytest.raises(HTTPException) as refused:
        await _install(session, agent, owner, strict=True)

    assert content_digest("mcp", pinned) != (await session.execute(select(AgentComponent))).scalar_one().resolved_digest
    assert response.lock["problems"] == ["mcp 'GitHub' 1.4.2 changed after it was locked"]
    assert refused.value.status_code == 409


async def test_requested_older_agent_version_installs_its_own_pins(session):
    owner = await ds.user(session)
    listing, agent, _marker = await _pinned_agent(session, owner, "mcp")
    newer_mcp = _new_release("mcp", listing, owner, "NEWER-RELEASE-MARKER")
    session.add(newer_mcp)
    await session.flush()
    listing.latest_version_id = newer_mcp.id
    newer_agent = AgentVersion(
        id=uuid.uuid4(),
        agent_id=agent.id,
        version="4.0.0",
        description="Uses the newer MCP",
        prompt="Review.",
        model_name="claude-sonnet-4",
        status=AgentStatus.approved,
        released_by=owner.id,
        released_at=ds.NOW,
    )
    session.add(newer_agent)
    await session.flush()
    session.add(
        AgentComponent(
            agent_version_id=newer_agent.id,
            component_type="mcp",
            component_id=listing.id,
            component_name="GitHub",
            resolved_version="9.0.0",
            resolved_version_id=newer_mcp.id,
        )
    )
    agent.latest_version_id = newer_agent.id
    await session.commit()

    latest = await _install(session, agent, owner)
    pinned = await _install(session, agent, owner, version="3.1.0")

    assert latest.version == "4.0.0"
    assert "NEWER-RELEASE-MARKER" in json.dumps(latest.config_snippet)
    assert pinned.version == "3.1.0"
    assert "NEWER-RELEASE-MARKER" not in json.dumps(pinned.config_snippet)
