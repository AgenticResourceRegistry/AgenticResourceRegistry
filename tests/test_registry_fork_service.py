# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Focused source/pin/provenance policies for agent forks."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models.agent import Agent, AgentStatus, AgentVersion
from models.agent_component import AgentComponent
from models.base import Base
from models.mcp import ListingStatus
from models.skill import SkillListing, SkillVersion
from models.user import UserRole
from services import registry_fork as forks


def _version(version="1.0.0", status=AgentStatus.approved):
    return SimpleNamespace(version=version, status=status)


def _source():
    user_id = uuid.uuid4()
    agent = Agent(
        id=uuid.uuid4(),
        name="Source",
        namespace="alice",
        slug="source",
        owner="alice",
        created_by=user_id,
        is_private=False,
        category="testing",
        deleted_at=None,
    )
    base = AgentVersion(
        id=uuid.uuid4(),
        agent_id=agent.id,
        version="2.3.0",
        status=AgentStatus.approved,
        description="Description",
        prompt="Prompt",
        model_name="example-model",
        model_config_json={"n": [1]},
        models_by_harness={},
        external_mcps=[],
        supported_harnesses=["kiro"],
        success_criteria=None,
        released_by=user_id,
        created_at=datetime.now(UTC),
    )
    base.components = []
    agent.latest_version = base
    agent.versions = [base]
    return agent, base, SimpleNamespace(id=user_id, username="alice", email="alice@example.com")


@pytest.mark.parametrize("status", [AgentStatus.draft, AgentStatus.pending, AgentStatus.rejected, AgentStatus.archived])
def test_never_select_nonapproved_base_even_when_explicit(status):
    versions = [_version("1.0.0", status)]
    with pytest.raises(HTTPException) as error:
        forks._select_base_version(versions, "1.0.0")
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        forks._select_base_version(versions, None)
    assert error.value.status_code == 409


def test_selects_highest_stable_approved_then_approved_prerelease():
    versions = [_version("2.0.0-rc1"), _version("1.9.0"), _version("3.0.0", AgentStatus.archived)]
    assert forks._select_base_version(versions, None).version == "1.9.0"
    assert forks._select_base_version(versions[:1], None).version == "2.0.0-rc1"
    assert forks._select_base_version(versions, "2.0.0-rc1").version == "2.0.0-rc1"
    flagged = _version("5.0.0")
    flagged.is_prerelease = True
    assert forks._select_base_version([flagged, _version("4.0.0")], None).version == "4.0.0"
    with pytest.raises(HTTPException) as error:
        forks._select_base_version(versions, "3.0.0")
    assert error.value.status_code == 404


def test_copy_version_columns_excludes_managed_fields_and_deep_copies_payload():
    original = SkillVersion(supported_harnesses=["kiro"], description="base", version="1.0.0")
    snapshot = forks._copy_version_columns(SkillVersion, original)
    assert "status" not in snapshot and "version" not in snapshot and "description" not in snapshot
    assert snapshot["supported_harnesses"] == ["kiro"]
    snapshot["supported_harnesses"].append("codex")
    assert original.supported_harnesses == ["kiro"]


@pytest.mark.asyncio
async def test_flag_blocks_before_redis(monkeypatch):
    monkeypatch.setattr(forks.dynamic_settings, "get_bool", AsyncMock(return_value=False))
    redis = MagicMock()
    monkeypatch.setattr(forks, "get_redis", redis)
    with pytest.raises(HTTPException) as error:
        await forks._check_rate_limit(uuid.uuid4())
    assert error.value.status_code == 403
    redis.assert_not_called()


@pytest.mark.asyncio
async def test_rate_limit_rejects_excess_forks(monkeypatch):
    monkeypatch.setattr(forks.dynamic_settings, "get_bool", AsyncMock(return_value=True))
    monkeypatch.setattr(forks.dynamic_settings, "get_int", AsyncMock(return_value=1))
    redis = MagicMock()
    redis.eval = AsyncMock(side_effect=[1, 2])
    monkeypatch.setattr(forks, "get_redis", lambda: redis)
    user_id = uuid.uuid4()
    await forks._check_rate_limit(user_id)
    with pytest.raises(HTTPException) as error:
        await forks._check_rate_limit(user_id)
    assert error.value.status_code == 429
    assert redis.eval.await_args.args[2] == f"fork:rate:{user_id}"


@pytest.mark.asyncio
async def test_private_source_restricts_target_to_original_teamspace():
    source, _, owner = _source()
    source.is_private = True
    source.team_id = uuid.uuid4()
    db = MagicMock()
    for target in (
        SimpleNamespace(team_id=uuid.uuid4(), visibility="team"),
        SimpleNamespace(team_id=source.team_id, visibility="public"),
    ):
        with pytest.raises(HTTPException) as error:
            await forks._enforce_private_source_policy(source, target, owner, db)
        assert error.value.status_code == 409
    await forks._enforce_private_source_policy(
        source, SimpleNamespace(team_id=source.team_id, visibility="team"), owner, db
    )


@pytest.mark.asyncio
async def test_private_personal_source_never_becomes_public():
    source, _, owner = _source()
    source.is_private = True
    db = MagicMock()
    db.get = AsyncMock(
        return_value=SimpleNamespace(
            is_personal=True,
            is_private=True,
            created_by=owner.id,
        )
    )
    with pytest.raises(HTTPException) as error:
        await forks._enforce_private_source_policy(
            source, SimpleNamespace(team_id=None, visibility="public"), owner, db
        )
    assert error.value.status_code == 409
    await forks._enforce_private_source_policy(
        source, SimpleNamespace(team_id=uuid.uuid4(), visibility="team"), owner, db
    )


@pytest.mark.asyncio
async def test_inaccessible_source_never_falls_back_to_saved_identity():
    """Batched provenance keeps the row-level visibility rules, in a constant number of queries."""
    from sqlalchemy import event

    from models.team import Team, TeamMembership, TeamRole

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            owner_id, member_id, outsider_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            team = Team(id=uuid.uuid4(), name="T", handle="t", is_private=True, created_by=owner_id)
            db.add(team)
            await db.flush()
            db.add(TeamMembership(team_id=team.id, user_id=member_id, role=TeamRole.member))
            source = SkillListing(
                id=uuid.uuid4(),
                name="S",
                namespace="t",
                slug="s",
                owner="t",
                submitted_by=owner_id,
                team_id=team.id,
                is_private=True,
                co_authors=[],
            )
            db.add(source)
            await db.flush()
            base = SkillVersion(
                id=uuid.uuid4(),
                listing_id=source.id,
                version="2.0.0",
                status=ListingStatus.approved,
                description="d",
                task_type="review",
                released_by=owner_id,
                released_at=datetime.now(UTC),
            )
            db.add(base)
            await db.flush()
            source.latest_version_id = base.id
            forks_rows = []
            for index in range(5):
                fork = SkillListing(
                    id=uuid.uuid4(),
                    name=f"F{index}",
                    namespace="t",
                    slug=f"f{index}",
                    owner="t",
                    submitted_by=member_id,
                    team_id=team.id,
                    is_private=False,
                    co_authors=[],
                    forked_from_id=source.id,
                    forked_from_version_id=base.id,
                    forked_from_ref="t/s@2.0.0",
                    forked_at=datetime.now(UTC),
                )
                db.add(fork)
                forks_rows.append(fork)
            orphan = SkillListing(
                id=uuid.uuid4(),
                name="O",
                namespace="t",
                slug="o",
                owner="t",
                submitted_by=member_id,
                is_private=False,
                co_authors=[],
                forked_from_ref="gone/source@1.0.0",
                forked_at=datetime.now(UTC),
            )
            db.add(orphan)
            await db.commit()

            statements = []

            def count(*_args):
                statements.append(1)

            event.listen(engine.sync_engine, "before_cursor_execute", count)
            outsider = SimpleNamespace(id=outsider_id, role=UserRole.user)

            async def queries_for(ids):
                async with async_sessionmaker(engine, expire_on_commit=False)() as fresh:
                    rows = (await fresh.execute(select(SkillListing).where(SkillListing.id.in_(ids)))).scalars().all()
                    statements.clear()
                    await forks.provenance_for_many(rows, outsider, fresh)
                    return len(statements)

            # The query count does not grow with the number of forks.
            assert await queries_for([forks_rows[0].id]) == await queries_for([row.id for row in forks_rows])
            hidden = await forks.provenance_for_many([*forks_rows, orphan], outsider, db)
            unavailable = [{"available": False, "forked_at": row.forked_at} for row in [*forks_rows, orphan]]
            assert [hidden[row.id] for row in [*forks_rows, orphan]] == unavailable
            assert "t/s" not in str(hidden) and "2.0.0" not in str(hidden)

            member = SimpleNamespace(id=member_id, role=UserRole.user)
            visible = await forks.provenance_for(forks_rows[0], member, db)
            assert visible["available"] is True and visible["qualified_name"] == "t/s" and visible["version"] == "2.0.0"
            admin = SimpleNamespace(id=uuid.uuid4(), role=UserRole.admin)
            assert (await forks.provenance_for(forks_rows[0], admin, db))["available"] is True
            assert await forks.provenance_for(forks_rows[0], None, db) == unavailable[0]
            event.remove(engine.sync_engine, "before_cursor_execute", count)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fork_agent_rejects_unapproved_source_before_creating_rows(monkeypatch):
    source, _, user = _source()
    source.latest_version.status = AgentStatus.pending
    monkeypatch.setattr(forks, "_check_rate_limit", AsyncMock())
    db = MagicMock()
    with pytest.raises(HTTPException) as error:
        await forks.fork_agent(db, source, forks.ForkRequestSpec(), current_user=user)
    assert error.value.status_code == 409
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_fork_agent_creates_independent_draft_and_rebuilds_snapshot(monkeypatch):
    source, base, user = _source()
    monkeypatch.setattr(forks, "_check_rate_limit", AsyncMock())
    monkeypatch.setattr(forks, "identity_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(
        forks,
        "resolve_publish_target",
        AsyncMock(
            return_value=SimpleNamespace(
                namespace="alice",
                slug="variant",
                owner="alice",
                team_id=None,
                visibility="public",
            )
        ),
    )
    monkeypatch.setattr(forks, "validate_component_ids", AsyncMock(return_value=[]))
    pinned = AsyncMock(return_value=[])
    monkeypatch.setattr(forks, "attach_pinned_components", pinned)
    monkeypatch.setattr(forks, "lock_agent_version", AsyncMock())
    monkeypatch.setattr(forks, "build_yaml_snapshot", AsyncMock(return_value="snapshot"))
    monkeypatch.setattr(forks, "infer_required_features", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(forks, "compute_supported_harnesses", lambda *_args: ["kiro"])
    db = MagicMock()
    db.add = MagicMock()

    async def flush():
        for call in db.add.call_args_list:
            if call.args[0].id is None:
                call.args[0].id = uuid.uuid4()

    db.flush = AsyncMock(side_effect=flush)
    result = await forks.fork_agent(db, source, forks.ForkRequestSpec(name="Variant"), current_user=user)
    assert result.entity.id != source.id
    assert result.entity.forked_from_id == source.id
    assert result.entity.forked_from_version_id == base.id
    assert result.version.status == AgentStatus.draft
    assert result.version.yaml_snapshot == "snapshot"
    assert result.version.lock_snapshot is None  # lock mock; original never copied
    assert result.version.prompt == base.prompt
    assert result.version.model_config_json is not base.model_config_json
    assert pinned.await_args.kwargs["require_approved"] is True
    assert source.name == "Source" and base.status == AgentStatus.approved


@pytest.mark.asyncio
async def test_unapproved_source_pin_is_replaced_with_approved_release(monkeypatch):
    source, base, user = _source()
    component_id = uuid.uuid4()
    pending = SimpleNamespace(id=uuid.uuid4(), version="3.0.0", status=AgentStatus.pending)
    approved = SimpleNamespace(id=uuid.uuid4(), version="2.0.0", status=AgentStatus.approved)
    base.components = [
        AgentComponent(
            component_type="skill",
            component_id=component_id,
            component_name="review",
            resolved_version_id=pending.id,
            resolved_version=pending.version,
            config_override={"n": [1]},
            agent_version_id=base.id,
            order_index=0,
        )
    ]
    monkeypatch.setattr(forks, "_check_rate_limit", AsyncMock())
    monkeypatch.setattr(forks, "identity_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(
        forks,
        "resolve_publish_target",
        AsyncMock(
            return_value=SimpleNamespace(
                namespace="alice",
                slug="variant",
                owner="alice",
                team_id=None,
                visibility="public",
            )
        ),
    )
    monkeypatch.setattr(
        forks,
        "_versions_for",
        AsyncMock(
            return_value={
                ("skill", component_id): [pending, approved],
            }
        ),
    )
    validator = AsyncMock(return_value=[])
    monkeypatch.setattr(forks, "validate_component_ids", validator)
    pin = AsyncMock(return_value=[])
    monkeypatch.setattr(forks, "attach_pinned_components", pin)
    monkeypatch.setattr(forks, "lock_agent_version", AsyncMock())
    monkeypatch.setattr(forks, "build_yaml_snapshot", AsyncMock(return_value="yaml"))
    monkeypatch.setattr(forks, "infer_required_features", lambda *_a, **_kw: [])
    monkeypatch.setattr(forks, "compute_supported_harnesses", lambda *_a: [])
    db = MagicMock()
    db.flush = AsyncMock(
        side_effect=lambda: [setattr(c.args[0], "id", c.args[0].id or uuid.uuid4()) for c in db.add.call_args_list]
    )
    rows = MagicMock()
    rows.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=rows)
    result = await forks.fork_agent(db, source, forks.ForkRequestSpec(name="variant"), current_user=user)
    assert result.warnings and "2.0.0" in result.warnings[0]
    refs = pin.await_args.args[2]
    assert refs[0]["version"] == "2.0.0"
    assert refs[0]["config_override"] is not base.components[0].config_override
    assert validator.await_args.kwargs["require_approved"] is False  # listing may have newer pending release


@pytest.mark.asyncio
async def test_fork_round_trip_with_real_database_and_snapshot(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(forks, "_check_rate_limit", AsyncMock())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            actor = SimpleNamespace(id=uuid.uuid4(), username="alice", email="alice@example.com", role=UserRole.user)
            source = Agent(
                id=uuid.uuid4(),
                name="Source",
                namespace="alice",
                slug="source",
                owner="alice",
                created_by=actor.id,
                category="testing",
                is_private=False,
            )
            db.add(source)
            await db.flush()
            base = AgentVersion(
                id=uuid.uuid4(),
                agent_id=source.id,
                version="2.3.0",
                status=AgentStatus.approved,
                description="review",
                prompt="Review carefully",
                model_name="example-model",
                model_config_json={},
                models_by_harness={},
                external_mcps=[],
                supported_harnesses=["kiro"],
                required_capabilities=[],
                inferred_supported_harnesses=[],
                released_by=actor.id,
            )
            db.add(base)
            await db.flush()
            source.latest_version_id = base.id
            skill = SkillListing(
                id=uuid.uuid4(),
                name="Review skill",
                namespace="alice",
                slug="review-skill",
                owner="alice",
                submitted_by=actor.id,
                is_private=False,
                co_authors=[],
            )
            db.add(skill)
            await db.flush()
            skill_version = SkillVersion(
                id=uuid.uuid4(),
                listing_id=skill.id,
                version="1.2.0",
                status=ListingStatus.approved,
                description="Review",
                task_type="review",
                skill_path="/",
                delivery_mode="registry_direct",
                skill_md_content="# Review",
                supported_harnesses=["kiro"],
                released_by=actor.id,
                released_at=datetime.now(UTC),
            )
            db.add(skill_version)
            await db.flush()
            skill.latest_version_id = skill_version.id
            db.add(
                AgentComponent(
                    id=uuid.uuid4(),
                    agent_version_id=base.id,
                    component_type="skill",
                    component_id=skill.id,
                    component_name="Review skill",
                    resolved_version="1.2.0",
                    resolved_version_id=skill_version.id,
                    order_index=0,
                    config_override={"mode": "strict"},
                )
            )
            await db.commit()
            source = (await db.execute(select(Agent).where(Agent.id == source.id))).scalar_one()
            result = await forks.fork_agent(db, source, forks.ForkRequestSpec(name="Variant"), current_user=actor)
            await db.commit()
            assert result.version.yaml_snapshot and result.version.lock_snapshot
            pins = (
                (await db.execute(select(AgentComponent).where(AgentComponent.agent_version_id == result.version.id)))
                .scalars()
                .all()
            )
            assert len(pins) == 1
            pin = pins[0]
            assert pin.component_id == skill.id and pin.resolved_version_id == skill_version.id
            assert pin.config_override == {"mode": "strict"}
            assert result.version.status == AgentStatus.draft
            assert result.entity.qualified_name == "alice/variant"
            assert await forks.agent_fork_counts(db, [source.id]) == {}
            result.version.status = AgentStatus.approved
            await db.commit()
            assert await forks.agent_fork_counts(db, [source.id]) == {source.id: 1}
            result.entity.is_private = True
            await db.commit()
            assert await forks.agent_fork_counts(db, [source.id]) == {}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rejected_fork_never_consumes_rate_limit(monkeypatch):
    """Name conflicts and bad versions are retried by design; they must not burn quota."""
    source, _, owner = _source()
    reserve = AsyncMock()
    monkeypatch.setattr(forks, "_check_rate_limit", reserve)
    monkeypatch.setattr(
        forks,
        "resolve_publish_target",
        AsyncMock(
            return_value=SimpleNamespace(
                namespace="alice", slug="taken", owner="alice", team_id=None, visibility="public"
            )
        ),
    )
    monkeypatch.setattr(forks, "identity_exists", AsyncMock(return_value=True))
    with pytest.raises(HTTPException) as error:
        await forks.fork_agent(MagicMock(), source, forks.ForkRequestSpec(name="Taken"), current_user=owner)
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        await forks.fork_agent(MagicMock(), source, forks.ForkRequestSpec(version="9.9.9"), current_user=owner)
    assert error.value.status_code == 404
    reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_failure_after_reservation_returns_the_slot(monkeypatch):
    source, _, owner = _source()
    monkeypatch.setattr(forks, "_check_rate_limit", AsyncMock())
    release = AsyncMock()
    monkeypatch.setattr(forks, "_release_fork_slot", release)
    monkeypatch.setattr(
        forks,
        "resolve_publish_target",
        AsyncMock(
            return_value=SimpleNamespace(
                namespace="alice", slug="copy", owner="alice", team_id=None, visibility="public"
            )
        ),
    )
    monkeypatch.setattr(forks, "identity_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(forks, "validate_component_ids", AsyncMock(return_value=[]))
    db = MagicMock()
    db.flush = AsyncMock(side_effect=HTTPException(status_code=409, detail="race"))
    with pytest.raises(HTTPException):
        await forks.fork_agent(db, source, forks.ForkRequestSpec(name="Copy"), current_user=owner)
    release.assert_awaited_once_with(owner.id)


@pytest.mark.asyncio
async def test_rate_limiter_outage_is_a_clear_retryable_error(monkeypatch):
    monkeypatch.setattr(forks.dynamic_settings, "get_bool", AsyncMock(return_value=True))
    monkeypatch.setattr(forks.dynamic_settings, "get_int", AsyncMock(return_value=30))
    redis = MagicMock()
    redis.eval = AsyncMock(side_effect=ConnectionError("down"))
    monkeypatch.setattr(forks, "get_redis", lambda: redis)
    with pytest.raises(HTTPException) as error:
        await forks._check_rate_limit(uuid.uuid4())
    assert error.value.status_code == 503


def test_semver_precedence_prefers_release_over_its_prereleases():
    assert forks._version_key("1.0.0") > forks._version_key("1.0.0-rc.1")
    assert forks._version_key("1.0.0-rc.10") > forks._version_key("1.0.0-rc.2")
    assert forks._version_key("1.0.0-rc.1") > forks._version_key("1.0.0-1")
    assert forks._version_key("1.0.0-alpha.1") > forks._version_key("1.0.0-alpha")


def test_legacy_approved_version_string_does_not_block_default_fork():
    versions = [_version("01.0.0"), _version("1.2.0"), _version("v0.9")]
    assert forks._select_base_version(versions, None).version == "1.2.0"
    # Still addressable exactly, but the copied version must then be replaced.
    legacy = forks._select_base_version(versions, "01.0.0")
    with pytest.raises(HTTPException) as error:
        forks._fork_version_string(legacy, None)
    assert error.value.status_code == 422 and "new_version" in error.value.detail
    assert forks._fork_version_string(legacy, "1.0.1") == "1.0.1"


def test_repin_prefers_highest_stable_release_over_newer_prerelease():
    rows = [_version("1.0.0"), _version("1.0.0-rc.1"), _version("2.0.0-beta")]
    assert forks._highest_approved(rows).version == "1.0.0"
    assert forks._highest_approved(rows[1:]).version == "2.0.0-beta"
