# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Verified presence for Pi skills: pull-time fingerprints, snapshot checks, presence index."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from observal_cli import layer
from observal_cli.cmd_pull import _fingerprint_written_skills

AGENT = "00000000-0000-4000-8000-000000000001"
SKILL = "55555555-5555-4555-8555-555555555555"
CONTENT = b"---\nname: review\ndescription: Review code.\n---\n\nReview carefully.\n"


def _h(data: bytes) -> str:
    return f"sha256-{hashlib.sha256(data).hexdigest()}"


FINGERPRINT = _h(CONTENT)


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


def _write(path: Path, data: bytes = CONTENT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _registry(integrity: str | None, *, scope: str = "user", directory: str | None = None) -> dict:
    component = {
        "type": "skill",
        "name": "review",
        "id": SKILL,
        "version": "1.0.0",
        "scope": scope,
        "local_name": "review",
    }
    if integrity:
        component["skill_integrity"] = integrity
    agent = {"id": AGENT, "name": "agent", "version": "1.0.0", "scope": scope, "components": [component]}
    if directory:
        agent["directory"] = directory
        component["directory"] = directory
    return {"harnesses": {"pi": {"agents": [agent], "standalone": []}}}


def _status(registry: dict, files: dict[str, bytes], project: str | None = None) -> str:
    manifest = {"pi": [{"path": path, "hash": _h(data)} for path, data in files.items()]}
    drift = layer._compute_drift(registry, manifest, project)
    (record,) = drift["skill_verifications"]
    return record["status"]


def test_pull_fingerprints_the_skill_file_it_wrote(home):
    profile = _write(home / ".pi/agent/agents/agent/skills/review/SKILL.md")
    components = [
        {"type": "skill", "id": SKILL, "local_name": "review"},
        {"type": "skill", "id": "other", "local_name": "not-written"},
    ]
    snippet = {"skill_components": [{"name": "review", "path": str(profile)}]}
    warnings = _fingerprint_written_skills(snippet, components, home, True)
    assert components[0]["skill_integrity"] == layer.skill_file_fingerprint(profile)
    assert components[0]["skill_integrity"].startswith("sha256-")
    assert "skill_integrity" not in components[1], "an unreadable skill stays unfingerprinted"
    assert warnings == ["Installed skill could not be fingerprinted; its presence will be unverified."]


def test_active_skill_matching_its_fingerprint_is_verified(home):
    fingerprint = FINGERPRINT
    assert _status(_registry(fingerprint), {"user:skills/review/SKILL.md": CONTENT}) == "verified"


def test_changed_active_skill_is_drifted_and_marks_the_layer(home):
    fingerprint = FINGERPRINT
    registry = _registry(fingerprint)
    manifest = {"pi": [{"path": "user:skills/review/SKILL.md", "hash": "sha256-" + "0" * 64}]}
    drift = layer._compute_drift(registry, manifest, None)
    assert drift["skill_verifications"][0]["status"] == "drifted"
    assert drift["is_canonical"] is False


@pytest.mark.parametrize(
    ("case", "files", "integrity"),
    [
        ("no fingerprint (legacy pull)", {"user:skills/review/SKILL.md": CONTENT}, None),
        ("inactive /agent profile: no active file", {}, "fingerprint"),
    ],
)
def test_unverifiable_skill_is_never_drift(home, case, files, integrity):
    fingerprint = FINGERPRINT if integrity else None
    registry = _registry(fingerprint)
    manifest = {"pi": [{"path": p, "hash": _h(d)} for p, d in files.items()]}
    drift = layer._compute_drift(registry, manifest, None)
    assert drift["skill_verifications"][0]["status"] == "unverified", case
    assert not drift["drifted_files"]


def test_same_named_skill_pi_might_load_instead_blocks_verification(home):
    fingerprint = FINGERPRINT
    registry = _registry(fingerprint)
    active = {"user:skills/review/SKILL.md": CONTENT}
    # A different project copy: Pi keeps the first one it discovers.
    assert _status(registry, {**active, "project:.pi/skills/review/SKILL.md": b"other"}) == "unverified"
    # A copy in an unhashed location Pi also reads (~/.agents/skills).
    _write(home / ".agents/skills/review/SKILL.md", b"shadow")
    assert _status(registry, active) == "unverified"


def test_skill_identity_entry_is_absent_without_fingerprints_and_tracks_them(home):
    assert layer.skill_verification_entry("pi", _registry(None), None) is None, "no hash change for legacy pins"
    first = layer.skill_verification_entry("pi", _registry("sha256-" + "a" * 64), None)
    second = layer.skill_verification_entry("pi", _registry("sha256-" + "b" * 64), None)
    assert first["path"] == "observal:skill-verification"
    assert first["hash"] != second["hash"], "a re-pull with new content yields a new layer identity"
    # A same-named copy Pi could load instead changes the identity, so a cached
    # snapshot can never keep reporting the earlier "verified" result.
    _write(home / ".agents/skills/review/SKILL.md", b"shadow")
    shadowed = layer.skill_verification_entry("pi", _registry("sha256-" + "a" * 64), None)
    assert shadowed["hash"] != first["hash"]
    # MCP pins are untouched by the skill entry.
    assert layer.pi_mcp_verification_entry(_registry("sha256-" + "a" * 64), None) is None


def test_presence_index_verifies_skills_only_from_skill_results():
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "observal-server"))
    from services.layer_components.normalizer import normalize_snapshot

    pins = {
        "schema_version": 2,
        "agents": [
            {
                "id": AGENT,
                "name": "agent",
                "version": "1.0.0",
                "harness": "pi",
                "scope": "user",
                "components": [
                    {
                        "type": "skill",
                        "id": SKILL,
                        "name": "review",
                        "version": "1.0.0",
                        "scope": "user",
                        "local_name": "review",
                    }
                ],
            }
        ],
        "standalone": [],
    }
    record = {
        "harness": "pi",
        "component_id": SKILL,
        "alias": "review",
        "scope": "user",
        "parent_agent_id": AGENT,
        "status": "verified",
    }
    verified = normalize_snapshot(pins, {"skill_verifications": [record]})
    assert [o.verification_status for o in verified] == ["verified"]
    # The same key reported as an MCP result never verifies a skill.
    assert [o.verification_status for o in normalize_snapshot(pins, {"mcp_verifications": [record]})] == ["unverified"]
    drifted = normalize_snapshot(pins, {"skill_verifications": [record | {"status": "drifted"}]})
    assert [o.verification_status for o in drifted] == ["drifted"]
    # Two identities claiming one installed skill name cannot be verified.
    twin = json.loads(json.dumps(pins))
    twin["agents"][0]["components"].append(
        twin["agents"][0]["components"][0] | {"id": "66666666-6666-4666-8666-666666666666"}
    )
    statuses = {
        o.raw_listing_id: o.verification_status for o in normalize_snapshot(twin, {"skill_verifications": [record]})
    }
    assert set(statuses.values()) == {"unverified"}


def test_verification_records_and_binds_the_absolute_active_location(home, tmp_path, monkeypatch):
    """The verifier names the exact file it fingerprinted; moving it changes identity."""
    _write(home / ".pi" / "agent" / "skills" / "review" / "SKILL.md")
    files = {"user:skills/review/SKILL.md": CONTENT}
    manifest = {"pi": [{"path": path, "hash": _h(data)} for path, data in files.items()]}
    (record,) = layer._compute_drift(_registry(FINGERPRINT), manifest, None)["skill_verifications"]
    expected = str(home / ".pi" / "agent" / "skills" / "review" / "SKILL.md")
    assert record["location_sha256"] == hashlib.sha256(expected.encode()).hexdigest()

    project = tmp_path / "project"
    project.mkdir()
    (scoped,) = layer._compute_drift(
        _registry(FINGERPRINT, scope="project", directory=str(project)), manifest, str(project)
    )["skill_verifications"]
    project_location = str(project / ".pi" / "skills" / "review" / "SKILL.md")
    assert scoped["location_sha256"] == hashlib.sha256(project_location.encode()).hexdigest()

    before = layer.skill_verification_entry("pi", _registry(FINGERPRINT), None)["hash"]
    other_home = tmp_path / "elsewhere"
    other_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: other_home)
    assert layer.skill_verification_entry("pi", _registry(FINGERPRINT), None)["hash"] != before


def test_presence_index_keeps_only_a_well_formed_skill_location():
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "observal-server"))
    from services.layer_components.normalizer import normalize_snapshot

    component = {
        "type": "skill",
        "id": SKILL,
        "name": "review",
        "version": "1.0.0",
        "scope": "user",
        "local_name": "review",
    }
    pins = {
        "schema_version": 2,
        "standalone": [],
        "agents": [
            {
                "id": AGENT,
                "name": "agent",
                "version": "1.0.0",
                "harness": "pi",
                "scope": "user",
                "components": [component],
            }
        ],
    }
    record = {
        "harness": "pi",
        "component_id": SKILL,
        "alias": "review",
        "scope": "user",
        "parent_agent_id": AGENT,
        "status": "verified",
    }
    digest = "a" * 64
    (located,) = normalize_snapshot(pins, {"skill_verifications": [record | {"location_sha256": digest}]})
    assert located.location_sha256 == digest
    for bad in ("", "A" * 64, "/home/u/.pi/agent/skills/review/SKILL.md", None):
        (item,) = normalize_snapshot(pins, {"skill_verifications": [record | {"location_sha256": bad}]})
        assert item.location_sha256 == ""
    (mcp,) = normalize_snapshot(
        {**pins, "agents": [{**pins["agents"][0], "components": [component | {"type": "mcp"}]}]},
        {"mcp_verifications": [record | {"location_sha256": digest}]},
    )
    assert mcp.location_sha256 == "", "only skills carry a location"
