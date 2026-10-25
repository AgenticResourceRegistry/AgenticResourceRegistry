# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Frozen install fixture must remain byte-for-byte aligned with the server encoder."""

import base64
import hashlib
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from models.mcp import ListingStatus
from models.skill import SkillVersion
from observal_cli.skill_folder import MAX_BUNDLE_FILES, MAX_TREE_SIZE, validate_bundle
from observal_shared.harness_registry import HARNESS_REGISTRY
from schemas.agent import AgentInstallRequest, AgentInstallResponse
from schemas.skill import SkillInstallRequest, SkillInstallResponse
from schemas.skill_resources import SkillInstallFolder
from services.agent_lock import LOCK_VERSION, SKILL_DIGEST_ALG_V2, content_digest, lock_digest
from services.harness import generate_agent_config
from services.skill_bundle import (
    complete_skill_folder,
    declared_skill_folder_name,
    prepare_agent_skill_folders,
    validate_skill_bundle,
)
from services.skill_config_generator import generate_skill_config
from services.skill_validator import SkillValidationError
from tests import discovery_support as ds


def _legacy_direct_bundle(*, extra_files=None, skill_md_content=None, script_content="echo ok"):
    version = SimpleNamespace(
        id=uuid.uuid4(),
        version="1.0.0",
        description="Historical skill",
        task_type="general",
        target_agents=[],
        supported_harnesses=["pi"],
        delivery_mode="registry_direct",
        skill_path="/",
        skill_md_content=skill_md_content or "---\nname: legacy\ndescription: Historical skill\n---\n",
        script_filename="run.sh",
        script_content=script_content,
        extra_files=extra_files or [],
    )
    return complete_skill_folder(uuid.uuid4(), version, skill_file_path=".pi/skills/legacy/SKILL.md")


def test_maximum_extra_files_with_legacy_script_install_through_client_contract():
    bundle = _legacy_direct_bundle(extra_files=[{"path": f"templates/{i}", "content": ""} for i in range(128)])
    assert len(bundle.files) == MAX_BUNDLE_FILES == 130
    assert len(validate_bundle(bundle.model_dump(mode="json")).files) == 130


def test_grandfathered_large_direct_files_install_without_widening_authoring_limit():
    content = "---\nname: legacy\ndescription: Historical skill\n---\n" + "x" * (2 * 1024 * 1024)
    bundle = _legacy_direct_bundle(skill_md_content=content)
    assert len(bundle.files[0].content) > 2 * 1024 * 1024
    assert validate_bundle(bundle.model_dump(mode="json")).total_size < MAX_TREE_SIZE
    with pytest.raises(SkillValidationError, match="per-file"):
        validate_skill_bundle(delivery_mode="registry_direct", skill_md_content=content, extra_files=[])
    large_script = _legacy_direct_bundle(script_content="x" * (2 * 1024 * 1024 + 1))
    assert len(validate_bundle(large_script.model_dump(mode="json")).files[1].content) > 2 * 1024 * 1024
    with pytest.raises(SkillValidationError, match="bundle"):
        validate_skill_bundle(
            delivery_mode="registry_direct",
            skill_md_content=content,
            script_filename="run.sh",
            script_content="x" * MAX_TREE_SIZE,
            extra_files=[],
            enforce_limits=False,
        )


def test_server_bundle_fixture_binds_selected_files_modes_and_v2_digest():
    example = json.loads((Path(__file__).parent / "fixtures" / "skill_folder_install_contract.json").read_text())
    expected = example["standalone"]["response_bundle"]
    parsed = SkillInstallFolder.model_validate_json(json.dumps(expected))
    assert expected["digest"].startswith("observal-content-v2:sha256:")
    assert expected["skill_file_path"] == ".pi/skills/example/SKILL.md"
    for file in parsed.files:
        data = base64.b64decode(file.content, validate=True)
        assert len(data) == file.size
        assert hashlib.sha256(data).hexdigest() == file.sha256
        assert file.version_id == parsed.version_id
    assert {file.path: file.mode for file in parsed.files} == {
        "SKILL.md": "0644",
        "assets/icon.bin": "0644",
        "scripts/run.sh": "0755",
    }

    row = SimpleNamespace(
        id=uuid.UUID(expected["version_id"]),
        version="1.1.0",
        description="Example skill",
        task_type="general",
        target_agents=[],
        supported_harnesses=["pi"],
        delivery_mode="registry_direct",
        skill_path="/",
        skill_md_content=base64.b64decode(parsed.files[0].content).decode(),
        script_filename=None,
        script_content=None,
        extra_files=[
            {"path": file.path, "content": file.content, "encoding": "base64", "executable": file.mode == "0755"}
            for file in parsed.files[1:]
        ],
    )
    actual = complete_skill_folder(uuid.UUID(expected["listing_id"]), row, skill_file_path=expected["skill_file_path"])
    assert json.loads(actual.model_dump_json()) == expected

    assert SkillInstallRequest.model_validate(example["standalone"]["request"]).version == row.version
    standalone = SkillInstallResponse.model_validate_json(json.dumps(example["standalone"]["response"]))
    listing = SimpleNamespace(
        id=uuid.UUID(expected["listing_id"]), name="Example", namespace="acme", slug="example", version=row.version
    )
    config = generate_skill_config(
        listing, "pi", server_url="https://api.example.test", scope="project", version_override=row
    )
    assert config.pop("skills")["path"] == expected["skill_file_path"]
    config["skill"].pop("skill_md_content")
    config["skill"]["bundle_version_id"] = expected["version_id"]
    assert standalone.config_snippet == config
    assert standalone.bundle == actual
    assert standalone.digest == expected["digest"]
    assert json.loads(standalone.model_dump_json()) == example["standalone"]["response"]

    agent_request = AgentInstallRequest.model_validate(example["agent"]["request"])
    assert agent_request.supported_features == ["skill_extra_files_v1"]
    agent = AgentInstallResponse.model_validate_json(json.dumps(example["agent"]["response"]))
    assert len(agent.skill_bundles) == 1
    selected = agent.skill_bundles[0]
    configured = agent.config_snippet["skill_components"]
    assert len(configured) == 1
    assert configured[0]["bundle_version_id"] == str(selected.version_id)
    assert configured[0]["path"] == selected.skill_file_path
    assert "skills" not in agent.config_snippet  # No duplicate partial SKILL.md write.
    agent_source = SimpleNamespace(
        id=agent.agent_id,
        name="Example Agent",
        namespace="acme",
        slug="example-agent",
        prompt="",
        external_mcps=[],
        components=[SimpleNamespace(component_type="skill", component_id=listing.id)],
    )
    skill_source = SimpleNamespace(
        id=listing.id,
        name="Example",
        namespace="acme",
        slug="example",
        description=row.description,
        task_type=row.task_type,
        delivery_mode=row.delivery_mode,
        skill_path=row.skill_path,
        skill_md_content=row.skill_md_content,
    )
    snippet = generate_agent_config(
        agent_source,
        "pi",
        observal_url="https://api.example.test",
        skill_listings={listing.id: skill_source},
        component_names={str(listing.id): "Example"},
        options={"scope": "project"},
    )
    for component in snippet["skill_components"]:
        for field in ("skill_md_content", "script_content", "script_filename"):
            component.pop(field, None)
        component["bundle_version_id"] = expected["version_id"]
    assert snippet == agent.config_snippet
    expected_path = HARNESS_REGISTRY["pi"]["skills"]["project"].format(name="example")
    assert selected.skill_file_path == expected_path.replace(".pi/", ".pi/agents/example-agent/", 1)
    assert (
        selected.files
        == complete_skill_folder(uuid.UUID(expected["listing_id"]), row, skill_file_path=selected.skill_file_path).files
    )
    assert selected.digest == expected["digest"]
    entry = agent.lock["components"][0]
    assert entry["id"] == str(selected.listing_id)
    assert entry["version_id"] == str(selected.version_id)
    assert entry["digest"] == selected.digest
    assert entry["source"] == "lock"
    assert agent.lock["status"] == "locked"
    assert agent.lock["problems"] == []
    documented_lock = {
        "lock_version": LOCK_VERSION,
        "digest_alg": SKILL_DIGEST_ALG_V2,
        "agent": {
            "id": str(agent.agent_id),
            "qualified_name": "acme/example-agent",
            "version": agent.version,
            "version_id": "33333333-3333-4333-8333-333333333333",
        },
        "status": agent.lock["status"],
        "components": [{key: value for key, value in entry.items() if key != "source"}],
        "external_mcps": [],
    }
    assert agent.lock["digest"] == lock_digest(documented_lock)
    assert json.loads(agent.model_dump_json()) == example["agent"]["response"]


@pytest.mark.parametrize(
    "harness", ["claude-code", "codex", "copilot", "copilot-cli", "opencode", "antigravity", "goose", "pi"]
)
@pytest.mark.parametrize("scope", ["project", "user"])
def test_negotiated_agent_uses_pinned_frontmatter_name_even_if_registry_slug_differs(harness, scope):
    skill_id = uuid.uuid4()
    version = SimpleNamespace(
        id=uuid.uuid4(),
        version="1.1.0",
        description="Portable skill",
        task_type="general",
        target_agents=[],
        supported_harnesses=[harness],
        delivery_mode="registry_direct",
        skill_path="/",
        skill_md_content="---\nname: café\ndescription: Portable skill\n---\n# Body\n",
        script_filename=None,
        script_content=None,
        extra_files=[{"path": "references/guide.md", "content": "Guide\n"}],
    )
    listing = SimpleNamespace(
        id=skill_id,
        name="Display title",
        namespace="acme",
        slug="registry-alias",
        description=version.description,
        task_type=version.task_type,
        delivery_mode=version.delivery_mode,
        skill_md_content=version.skill_md_content,
        pinned_version=version,
        skill_path="/",
    )
    agent = SimpleNamespace(
        id=uuid.uuid4(),
        name="Agent",
        namespace="acme",
        slug="example-agent",
        description="Portable agent",
        prompt="",
        external_mcps=[],
        components=[SimpleNamespace(component_type="skill", component_id=skill_id)],
    )
    names = {skill_id: declared_skill_folder_name(version.skill_md_content)}
    snippet = generate_agent_config(
        agent,
        harness,
        skill_listings={skill_id: listing},
        skill_folder_names=names,
        options={"scope": scope},
    )
    if scope == "user" and harness in {"copilot", "copilot-cli"}:
        with pytest.raises(SkillValidationError, match="different Agent scope"):
            prepare_agent_skill_folders({skill_id: listing}, snippet, harness, scope=scope, folder_names=names)
        return  # These adapters have no user-scoped Agent profile in the harness registry.
    folders = prepare_agent_skill_folders({skill_id: listing}, snippet, harness, scope=scope, folder_names=names)
    assert len(folders) == 1
    assert folders[0].skill_file_path.endswith("/café/SKILL.md")
    # The pull client requires exactly one component naming the bundle and its destination, for every harness.
    [component] = snippet["skill_components"]
    assert component["name"] == "café"
    assert component["path"] == folders[0].skill_file_path
    assert component["bundle_version_id"] == str(folders[0].version_id)
    assert "skills" not in snippet


def test_agent_colliding_declared_names_refuse_without_renaming_either_skill():
    names = {uuid.uuid4(): "same-name", uuid.uuid4(): "same-name"}
    listings = {
        key: SimpleNamespace(delivery_mode="registry_direct", slug=f"unique-{n}", namespace="acme")
        for n, key in enumerate(names)
    }
    with pytest.raises(SkillValidationError, match="collide"):
        prepare_agent_skill_folders(listings, {"skill_components": [], "skills": []}, "pi", folder_names=names)


@pytest.mark.parametrize("name", ["UPPER", "bad--name", "-leading", "trailing-", "x" * 65, "CON"])
def test_complete_folder_name_refuses_invalid_or_unportable_frontmatter(name):
    content = f"---\nname: {name}\ndescription: Test\n---\n# Body\n"
    with pytest.raises(SkillValidationError):
        declared_skill_folder_name(content)


@pytest.mark.parametrize("name", [" padded ", "\ufb01le", "\uff46\uff55\uff4c\uff4c"])  # padded, ligature, fullwidth
def test_complete_folder_name_refuses_names_that_would_be_rewritten(name):
    # The installed directory must equal the stored frontmatter name, not a stripped or NFKC-folded variant.
    content = f'---\nname: "{name}"\ndescription: Test\n---\n# Body\n'
    with pytest.raises(SkillValidationError):
        declared_skill_folder_name(content)


@pytest.mark.asyncio
async def test_fixture_digest_includes_actual_persisted_skill_version_defaults():
    example = json.loads((Path(__file__).parent / "fixtures" / "skill_folder_install_contract.json").read_text())
    expected = example["standalone"]["response_bundle"]
    engine = ds.make_engine()
    maker = await ds.create_schema(engine)
    try:
        async with maker() as db:
            owner = await ds.user(db)
            listing = await ds.skill(db, owner, status=ListingStatus.approved)
            version = await db.get(SkillVersion, listing.latest_version_id)
            version.version = "1.1.0"
            version.description = "Example skill"
            version.task_type = "general"
            version.supported_harnesses = ["pi"]
            version.skill_md_content = base64.b64decode(expected["files"][0]["content"]).decode()
            version.extra_files = [
                {"path": "scripts/run.sh", "content": "echo ok\n", "executable": True},
                {"path": "assets/icon.bin", "content": "AP8=", "encoding": "base64", "executable": False},
            ]
            await db.flush()
            assert version.skill_path == "/"
            assert version.target_agents == []
            assert content_digest("skill", version) == expected["digest"]
    finally:
        await engine.dispose()
