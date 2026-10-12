# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Phase 2 authoring is permitted, but delivery and review remain fail-closed."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from api.routes import component_versions as versions
from api.routes import review, skill
from models.mcp import ListingStatus
from models.skill import SkillVersion
from models.user import UserRole
from schemas.component_version import VersionReviewRequest
from schemas.skill import SkillDraftRequest, SkillInstallRequest, SkillSubmitRequest, SkillUpdateRequest
from services.skill_revisions import skill_content_revision
from tests import discovery_support as ds
from tests.test_component_versions_routes import OWNER_ID, _request
from tests.test_component_versions_routes import _db as version_db
from tests.test_component_versions_routes import _listing as version_listing
from tests.test_skill_routes import (
    LISTING_ID,
    _db,
    _listing,
    _mock_skill_lock,
    _prepare_new_rows,
    _refresh_new_listing,
    _target,
    _user,
)

RESOURCE = {"path": "assets/icon.bin", "content": "AP8=", "encoding": "base64"}
MD = "# Review\n"


def test_typed_requests_null_and_contract():
    with (Path(__file__).parent / "fixtures/skill_bundle_contract.json").open(encoding="utf-8") as file:
        contract = json.load(file)
    draft = SkillDraftRequest(**{**contract["submit"], "version": "0.1.0"})
    assert draft.extra_files[0].model_dump()["encoding"] == "base64"
    assert SkillUpdateRequest(extra_files=[]).extra_files == []
    with pytest.raises(ValidationError):
        SkillUpdateRequest(extra_files=None)
    with pytest.raises(ValidationError):
        SkillDraftRequest(name="x", extra_files=None)


@pytest.mark.asyncio
async def test_submit_persists_extras_without_auto_approval(monkeypatch):
    db = _db()
    db.flush.side_effect = lambda: _prepare_new_rows(db)
    db.refresh.side_effect = lambda listing: _refresh_new_listing(db, listing)
    monkeypatch.setattr(skill, "resolve_publish_target", AsyncMock(return_value=_target(auto_approve=True)))
    monkeypatch.setattr(skill, "identity_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(skill, "commit_or_name_conflict", AsyncMock())
    publish = AsyncMock()
    monkeypatch.setattr(skill.inbox, "on_publish", publish)
    await skill.submit_skill(
        SkillSubmitRequest(
            name="Review Skill",
            version="1.0.0",
            description="Review",
            owner="alice",
            task_type="code-review",
            delivery_mode="registry_direct",
            skill_md_content=MD,
            extra_files=[RESOURCE],
        ),
        db,
        _user(),
    )
    version = db.add.call_args.args[0]
    assert version.extra_files[0]["content"] == "AP8="
    assert version.status == ListingStatus.pending
    assert publish.call_args.kwargs["auto_approved"] is False


@pytest.mark.asyncio
async def test_draft_inherits_clears_and_rejects_git_transition(monkeypatch):
    listing = _listing(status=ListingStatus.draft, submitted_by=_user().id, skill_md_content=MD)
    ver = listing.latest_version
    ver.delivery_mode = "registry_direct"
    ver.extra_files = [RESOURCE]
    ver.content_revision = skill_content_revision(listing, ver)
    db = _db()
    _mock_skill_lock(monkeypatch, listing)
    monkeypatch.setattr(skill, "resolve_listing", AsyncMock(return_value=listing))
    monkeypatch.setattr(skill, "commit_or_name_conflict", AsyncMock())

    validate = Mock(wraps=skill.validate_skill_bundle)
    monkeypatch.setattr(skill, "validate_skill_bundle", validate)
    await skill.update_skill_draft(
        str(LISTING_ID), SkillUpdateRequest(description="changed", observed_revision=ver.content_revision), db, _user()
    )
    assert validate.call_args.kwargs["enforce_limits"] is False
    assert ver.extra_files == [RESOURCE]
    with pytest.raises(HTTPException) as exc:
        await skill.update_skill_draft(
            str(LISTING_ID),
            SkillUpdateRequest(delivery_mode="git_fetch", observed_revision=ver.content_revision),
            db,
            _user(),
        )
    assert exc.value.status_code == 422
    assert validate.call_args.kwargs["enforce_limits"] is True
    await skill.update_skill_draft(
        str(LISTING_ID),
        SkillUpdateRequest(delivery_mode="git_fetch", extra_files=[], observed_revision=ver.content_revision),
        db,
        _user(),
    )
    assert ver.extra_files == []
    assert ver.delivery_mode == "git_fetch"


@pytest.mark.asyncio
async def test_standalone_selected_version_refuses_before_accounting(monkeypatch):
    listing = _listing()
    latest = listing.latest_version
    latest.delivery_mode = "registry_direct"
    latest.skill_md_content = MD
    latest.extra_files = [RESOURCE]
    old = _listing().latest_version
    old.delivery_mode = "registry_direct"
    old.skill_md_content = MD
    old.extra_files = []
    old.requires_global_review = False
    db = _db()
    monkeypatch.setattr(skill, "resolve_visible_listing", AsyncMock(return_value=listing))
    monkeypatch.setattr(skill, "_selected_skill_release", AsyncMock(return_value=(listing, old)))
    monkeypatch.setattr("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "https://test"}))
    monkeypatch.setattr(skill, "commit_or_name_conflict", AsyncMock())
    monkeypatch.setattr("services.skill_config_generator.generate_skill_config", Mock(return_value={}))
    await skill.install_skill(str(LISTING_ID), SkillInstallRequest(harness="pi", version="old"), None, db, _user())
    assert old.download_count == 8

    old.extra_files = [RESOURCE]
    db.add.reset_mock()
    with pytest.raises(HTTPException) as exc:
        await skill.install_skill(str(LISTING_ID), SkillInstallRequest(harness="pi", version="old"), None, db, _user())
    assert exc.value.status_code == 409
    db.add.assert_not_called()

    old.extra_files = []
    old.requires_global_review = True
    with pytest.raises(HTTPException) as marked:
        await skill.install_skill(str(LISTING_ID), SkillInstallRequest(harness="pi", version="old"), None, db, _user())
    assert marked.value.status_code == 409
    db.add.assert_not_called()

    old.requires_global_review = False
    old.script_filename = "empty.sh"
    old.script_content = ""
    with pytest.raises(HTTPException) as empty_script:
        await skill.install_skill(str(LISTING_ID), SkillInstallRequest(harness="pi", version="old"), None, db, _user())
    assert empty_script.value.status_code == 409
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_marked_legacy_release_cannot_install_or_count_downloads():
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner, status=ListingStatus.approved)
            version = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing.id))).scalar_one()
            version.requires_global_review = True
            await db.commit()
            listing_id, version_id, owner_id = listing.id, version.id, owner.id

        async with maker() as db:
            owner = await db.get(type(owner), owner_id)
            with pytest.raises(HTTPException) as exc:
                await skill.install_skill(
                    str(listing_id), SkillInstallRequest(harness="pi", version="1.2.0"), None, db, owner
                )
            assert exc.value.status_code == 409
            await db.rollback()

        async with maker() as db:
            version = (await db.execute(select(SkillVersion).where(SkillVersion.id == version_id))).scalar_one()
            assert version.download_count == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_version_inheritance_validation_and_no_shared_json(monkeypatch):
    listing = version_listing("skill", owner_id=OWNER_ID)
    listing.latest_version.extra_files = [RESOURCE]
    listing.latest_version.content_revision = skill_content_revision(listing, listing.latest_version)
    db = version_db()
    from tests.test_component_versions_routes import _result

    async def execute(stmt):
        if "skill_versions.status IN" in str(stmt):
            return _result(scalar=listing.latest_version)
        return _result()

    db.execute.side_effect = execute
    monkeypatch.setattr(
        versions, "lock_skill_version", AsyncMock(return_value=(listing.latest_version_id, listing.latest_version))
    )
    monkeypatch.setattr(versions, "check_listing_visibility_async", AsyncMock(return_value=True))
    monkeypatch.setattr(versions, "resolve_visible_listing", AsyncMock(return_value=listing))
    monkeypatch.setattr(versions, "get_effective_component_permission", Mock(return_value="owner"))
    monkeypatch.setattr(versions.inbox, "on_publish", AsyncMock())
    user = SimpleNamespace(id=OWNER_ID)
    await versions._publish_version(
        str(listing.id), _request("skill"), type(listing), type(listing.latest_version), "skill", db, user
    )
    new = db.add.call_args.args[0]
    assert new.extra_files == [RESOURCE]
    assert new.extra_files is not listing.latest_version.extra_files
    new.extra_files[0]["content"] = "changed"
    assert listing.latest_version.extra_files[0]["content"] == "AP8="

    db.add.reset_mock()
    await versions._publish_version(
        str(listing.id),
        _request("skill", extra={"task_type": "code-review", "extra_files": []}),
        type(listing),
        type(listing.latest_version),
        "skill",
        db,
        user,
    )
    assert db.add.call_args.args[0].extra_files == []

    db.add.reset_mock()
    with pytest.raises(HTTPException) as exc:
        await versions._publish_version(
            str(listing.id),
            _request("skill", extra={"task_type": "code-review", "delivery_mode": "git_fetch"}),
            type(listing),
            type(listing.latest_version),
            "skill",
            db,
            user,
        )
    assert exc.value.status_code == 422
    db.add.assert_not_called()
    with pytest.raises(HTTPException) as null_exc:
        await versions._publish_version(
            str(listing.id),
            _request("skill", extra={"task_type": "code-review", "extra_files": None}),
            type(listing),
            type(listing.latest_version),
            "skill",
            db,
            user,
        )
    assert null_exc.value.status_code == 422


@pytest.mark.asyncio
async def test_version_review_approval_refuses_resource_bytes(monkeypatch):
    listing = version_listing("skill", owner_id=OWNER_ID, status=ListingStatus.pending)
    listing.latest_version.extra_files = [RESOURCE]
    db = version_db()
    from tests.test_component_versions_routes import _result

    db.execute.return_value = _result(scalar=listing.latest_version)
    db.refresh = AsyncMock()
    monkeypatch.setattr(versions, "resolve_visible_listing", AsyncMock(return_value=listing))
    monkeypatch.setattr(versions, "get_effective_component_permission", Mock(return_value="owner"))
    monkeypatch.setattr(
        versions, "lock_skill_version", AsyncMock(return_value=(listing.latest_version_id, listing.latest_version))
    )
    with pytest.raises(HTTPException) as exc:
        await versions._review_version(
            str(listing.id),
            listing.latest_version.version,
            VersionReviewRequest(action="approve"),
            type(listing),
            type(listing.latest_version),
            "skill",
            db,
            SimpleNamespace(id=OWNER_ID, role=UserRole.admin),
        )
    assert exc.value.status_code == 409
    db.commit.assert_not_awaited()


def test_review_guard_targets_resource_versions():
    listing = _listing()
    listing.latest_version.delivery_mode = "registry_direct"
    listing.latest_version.extra_files = [RESOURCE]
    listing.versions = [listing.latest_version]
    with pytest.raises(HTTPException) as exc:
        review._refuse_unreviewable_skill(listing)
    assert exc.value.status_code == 409
    listing.latest_version.extra_files = []
    review._refuse_unreviewable_skill(listing)


def test_summary_never_serializes_resources():
    listing = version_listing("skill", owner_id=OWNER_ID)
    listing.latest_version.extra_files = [RESOURCE]
    assert "extra_files" not in versions._version_to_dict(listing.latest_version, "skill", summary=True)
    assert versions._version_to_dict(listing.latest_version, "skill")["extra_files"] == [RESOURCE]
