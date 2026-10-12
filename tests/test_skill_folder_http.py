# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The negotiated folder contract through the actual FastAPI route and schema."""

import base64
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select

from api.deps import get_db, get_registry_user
from api.routes import skill
from models.skill import SkillDownload, SkillVersion
from schemas.skill import SkillCandidateDraftRequest, SkillFolderDraftRequest
from schemas.skill_resources import SkillDraftRebaseRequest
from services.skill_revisions import skill_content_revision
from tests import discovery_support as ds

CONTRACT = json.loads((Path(__file__).parent / "fixtures" / "skill_folder_install_contract.json").read_text())


def test_strict_folder_authoring_uuid_fields_accept_json_strings_not_invalid_types():
    identity = str(uuid.uuid4())
    revision = "a" * 64
    candidate = SkillCandidateDraftRequest.model_validate(
        {
            "base_version_id": identity,
            "observed_base_revision": revision,
            "version": "1.1.0",
            "description": "Change",
        }
    )
    assert str(candidate.base_version_id) == identity
    rebase = SkillDraftRebaseRequest.model_validate(
        {
            "observed_revision": revision,
            "current_version_id": identity,
            "observed_current_revision": revision,
        }
    )
    assert str(rebase.current_version_id) == identity
    request = SkillFolderDraftRequest.model_validate(
        {
            "name": "folder",
            "owner": "author",
            "version": "1.0.0",
            "description": "Folder",
            "skill_md_content": "---\nname: folder\ndescription: Folder\n---\n",
            "extra_files": [],
            "team_id": identity,
        }
    )
    assert str(request.team_id) == identity
    with pytest.raises(ValidationError):
        SkillCandidateDraftRequest.model_validate(
            {
                "base_version_id": 123,
                "observed_base_revision": revision,
                "version": "1.1.0",
                "description": "Change",
            }
        )


@pytest.mark.asyncio
async def test_http_selected_skill_needs_capability_and_deliberate_rollout(monkeypatch):
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    async with engine.begin() as conn:
        await conn.run_sync(SkillDownload.__table__.create)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner)
            version = await db.get(SkillVersion, listing.latest_version_id)
            version.extra_files = [
                {
                    "path": "data/binary.bin",
                    "content": base64.b64encode(b"\x00\xff").decode(),
                    "encoding": "base64",
                    "executable": False,
                }
            ]
            version.content_revision = skill_content_revision(listing, version)
            await db.commit()
            listing_id, version_id = listing.id, version.id

        app = FastAPI()
        app.include_router(skill.router)

        async def session():
            async with maker() as db:
                yield db

        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_registry_user] = lambda: owner
        monkeypatch.setattr("api.routes.config.derive_endpoints", AsyncMock(return_value={"api": "https://api.test"}))
        url = f"/api/v1/skills/{listing_id}/install"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            old = await client.post(url, json={"harness": "pi", "version": "1.2.0"})
            assert old.status_code == 409
            assert old.json() == {"detail": CONTRACT["refusals"]["old_client_standalone"]["detail"]}
            opted = {"harness": "pi", "version": "1.2.0", "supported_features": ["skill_extra_files_v1"]}
            monkeypatch.setattr("services.dynamic_settings.get_sync_bool", lambda *_args: True)
            before_rollout = await client.post(url, json=opted)
            assert before_rollout.status_code == 409
            assert before_rollout.json() == {"detail": CONTRACT["refusals"]["not_rolled_out_standalone"]["detail"]}
            monkeypatch.setattr("services.dynamic_settings.get_bool", AsyncMock(return_value=True))
            unsupported = await client.post(url, json={**opted, "harness": "kiro"})
            assert unsupported.status_code == 409
            assert unsupported.json() == {"detail": CONTRACT["refusals"]["unsupported_skills_harness"]["detail"]}
            selected = await client.post(url, json=opted)
            assert selected.status_code == 200, selected.text
            payload = selected.json()
            assert payload["bundle"]["version_id"] == str(version_id)
            assert [file["path"] for file in payload["bundle"]["files"]] == ["SKILL.md", "data/binary.bin"]
            binary = payload["bundle"]["files"][1]
            assert base64.b64decode(binary["content"]) == b"\x00\xff"
            assert binary["mode"] == "0644"
        async with maker() as db:
            version = await db.get(SkillVersion, version_id)
            assert version.download_count == 1
            assert (
                (await db.execute(select(SkillDownload).where(SkillDownload.listing_id == listing_id))).scalars().all()
            )
    finally:
        await engine.dispose()
