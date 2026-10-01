# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Verified presence for Claude Code skills, tied to the exact file sessions name."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from observal_cli import layer
from observal_cli.harness.claude_code import ClaudeCodeAdapter

AGENT = "00000000-0000-4000-8000-000000000001"
SKILL = "55555555-5555-4555-8555-555555555555"
CONTENT = b"---\nname: review\ndescription: Review code.\n---\n\nReview carefully.\n"
FINGERPRINT = f"sha256-{hashlib.sha256(CONTENT).hexdigest()}"
FIXTURES = Path(__file__).parent / "fixtures" / "component_insights" / "claude_code"


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(ClaudeCodeAdapter, "skill_shadow_paths", lambda self, scope, directory, alias: [])
    return tmp_path


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
        agent["directory"] = component["directory"] = directory
    return {"harnesses": {"claude-code": {"agents": [agent], "standalone": []}}}


def _record(registry: dict, files: dict[str, bytes], project: str | None = None) -> dict:
    manifest = {
        "claude-code": [{"path": path, "hash": f"sha256-{hashlib.sha256(d).hexdigest()}"} for path, d in files.items()]
    }
    (record,) = layer._compute_drift(registry, manifest, project)["skill_verifications"]
    return record


def test_active_personal_skill_matching_its_fingerprint_is_verified_at_its_location(home):
    record = _record(_registry(FINGERPRINT), {"user:skills/review/SKILL.md": CONTENT})
    assert record["status"] == "verified"
    expected = str(home / ".claude" / "skills" / "review" / "SKILL.md")
    assert record["location_sha256"] == hashlib.sha256(expected.encode()).hexdigest()


def test_project_skill_is_verified_against_the_project_directory(home, tmp_path):
    project = tmp_path / "project"
    record = _record(
        _registry(FINGERPRINT, scope="project", directory=str(project)),
        {"project:.claude/skills/review/SKILL.md": CONTENT},
        str(project),
    )
    assert record["status"] == "verified"
    expected = str(project / ".claude" / "skills" / "review" / "SKILL.md")
    assert record["location_sha256"] == hashlib.sha256(expected.encode()).hexdigest()


def test_edited_missing_and_unfingerprinted_skills_are_never_verified(home):
    assert _record(_registry(FINGERPRINT), {"user:skills/review/SKILL.md": CONTENT + b"x"})["status"] == "drifted"
    assert _record(_registry(FINGERPRINT), {})["status"] == "unverified"
    assert _record(_registry(None), {"user:skills/review/SKILL.md": CONTENT})["status"] == "unverified"


def test_a_personal_skill_of_the_same_name_blocks_a_project_skill(home, tmp_path):
    """Personal beats project in Claude Code; a different personal copy makes the project one unverifiable."""
    project = tmp_path / "project"
    record = _record(
        _registry(FINGERPRINT, scope="project", directory=str(project)),
        {"project:.claude/skills/review/SKILL.md": CONTENT, "user:skills/review/SKILL.md": b"other"},
        str(project),
    )
    assert record["status"] == "unverified"


def test_an_enterprise_skill_of_the_same_name_blocks_verification(home, monkeypatch, tmp_path):
    managed = tmp_path / "managed" / ".claude" / "skills" / "review" / "SKILL.md"
    managed.parent.mkdir(parents=True)
    managed.write_bytes(CONTENT)
    monkeypatch.setattr(ClaudeCodeAdapter, "skill_shadow_paths", lambda self, scope, directory, alias: [managed])
    assert _record(_registry(FINGERPRINT), {"user:skills/review/SKILL.md": CONTENT})["status"] == "unverified"


def test_enterprise_paths_follow_the_documented_managed_directories(monkeypatch):
    import observal_cli.harness.claude_code as module

    for platform, root in (("darwin", "/Library/Application Support/ClaudeCode"), ("linux", "/etc/claude-code")):
        monkeypatch.setattr(module.sys, "platform", platform)
        (path,) = ClaudeCodeAdapter().skill_shadow_paths("user", None, "review")
        assert path == Path(root) / ".claude" / "skills" / "review" / "SKILL.md"


def test_identity_entry_binds_fingerprints_and_location_and_is_absent_without_them(home, monkeypatch, tmp_path):
    assert layer.skill_verification_entry("claude-code", _registry(None), None) is None
    first = layer.skill_verification_entry("claude-code", _registry(FINGERPRINT), None)
    assert first["path"] == "observal:skill-verification"
    assert first != layer.skill_verification_entry("claude-code", _registry("sha256-" + "b" * 64), None)
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setattr(Path, "home", lambda: elsewhere)
    assert layer.skill_verification_entry("claude-code", _registry(FINGERPRINT), None)["hash"] != first["hash"]


def test_verifier_digest_equals_the_digest_of_the_file_claude_code_recorded(monkeypatch):
    """The verifier and the extractor name the same file for the recorded sessions."""
    from services.session_parsers.skill_evidence import extract_skill_evidence

    monkeypatch.setattr(Path, "home", lambda: Path("/home/fixture"))
    for name, scope, alias, directory in (
        ("skill_session_model_read.jsonl", "user", "observal-probe", None),
        ("skill_session_slash_command.jsonl", "user", "observal-probe", None),
        ("skill_session_project_skill.jsonl", "project", "project-probe", "/home/fixture/project"),
    ):
        lines = (FIXTURES / name).read_text().splitlines()
        rows = [{"is_source_record": 1, "line_offset": i, "raw_line": line} for i, line in enumerate(lines)]
        (fact,) = extract_skill_evidence("claude-code", rows).evidence
        assert fact.location_sha256 == layer.skill_location_sha256("claude-code", scope, directory, alias), name


def test_presence_index_reads_claude_code_skill_results(home):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "observal-server"))
    from services.layer_components.normalizer import normalize_snapshot

    record = _record(_registry(FINGERPRINT), {"user:skills/review/SKILL.md": CONTENT})
    pins = {
        "schema_version": 2,
        "standalone": [],
        "agents": [
            {
                "id": AGENT,
                "name": "agent",
                "version": "1.0.0",
                "harness": "claude-code",
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
    }
    (occurrence,) = normalize_snapshot(json.loads(json.dumps(pins)), {"skill_verifications": [record]})
    assert occurrence.verification_status == "verified"
    assert occurrence.location_sha256 == record["location_sha256"]
