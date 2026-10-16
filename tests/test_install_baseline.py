# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Manual pulls establish an exact baseline; legacy or dirty installs cannot auto-update."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from observal_cli import install_baseline

REGISTRY = "https://example.test"
ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture(autouse=True)
def isolated_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install_baseline, "BASELINE_DIR", tmp_path / "baselines")


def kwargs(root: Path) -> dict:
    return {
        "registry": REGISTRY,
        "harness": "pi",
        "agent_id": ID,
        "scope": "user",
        "root": str(root),
        "version": "1.0.0",
        "lock_digest": "sha256:example",
    }


def test_no_adoption_and_manual_capture_of_file_and_directory(tmp_path: Path) -> None:
    root = tmp_path / "project"
    skill = root / "skills" / "reviewer"
    skill.mkdir(parents=True)
    file = skill / "SKILL.md"
    file.write_text("original")
    shared = root / "mcp.json"
    shared.write_text(json.dumps({"managed": "v1", "unmanaged": "mine"}))
    with pytest.raises(install_baseline.BaselineError):
        install_baseline.verified_files(**kwargs(root))
    install_baseline.capture(**kwargs(root), written_paths=[str(skill), str(shared)])
    assert len(install_baseline.verified_files(**kwargs(root))) == 2
    shared.write_text(json.dumps({"managed": "v1", "unmanaged": "changed"}))
    with pytest.raises(install_baseline.BaselineError, match="changed"):
        install_baseline.verified_files(**kwargs(root))
    shared.write_text(json.dumps({"managed": "v1", "unmanaged": "mine"}))
    (skill / "unexpected.txt").write_text("added")
    with pytest.raises(install_baseline.BaselineError, match="changed"):
        install_baseline.verified_files(**kwargs(root))


def test_chmod_of_owned_file_is_detected_even_when_bytes_are_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    profile = root / "AGENTS.md"
    profile.write_text("managed")
    profile.chmod(0o644)
    install_baseline.capture(**kwargs(root), written_paths=[str(profile)])
    manifest = json.loads(install_baseline._path(REGISTRY, "pi", ID, "user", str(root)).read_text())
    assert manifest["schema"] == 3 and manifest["modes"] == {str(profile): 0o644}
    profile.chmod(0o777)
    with pytest.raises(install_baseline.BaselineError, match="mode has changed"):
        install_baseline.verified_files(**kwargs(root))
    assert profile.read_text() == "managed" and profile.stat().st_mode & 0o777 == 0o777


def test_old_or_incomplete_mode_evidence_requires_manual_repull(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    profile = root / "AGENTS.md"
    profile.write_text("managed")
    install_baseline.capture(**kwargs(root), written_paths=[str(profile)])
    target = install_baseline._path(REGISTRY, "pi", ID, "user", str(root))
    manifest = json.loads(target.read_text())
    legacy = {key: value for key, value in manifest.items() if key != "modes"}
    legacy["schema"] = 2
    target.write_text(json.dumps(legacy))
    with pytest.raises(install_baseline.BaselineError, match="no mode evidence"):
        install_baseline.verified_files(**kwargs(root))
    assert json.loads(target.read_text()) == legacy, "legacy evidence must not be silently adopted"
    manifest["modes"] = {str(profile): True}
    target.write_text(json.dumps(manifest))
    with pytest.raises(install_baseline.BaselineError, match="no valid file modes"):
        install_baseline.verified_files(**kwargs(root))
    manifest["modes"] = {}
    target.write_text(json.dumps(manifest))
    with pytest.raises(install_baseline.BaselineError, match="no valid file modes"):
        install_baseline.verified_files(**kwargs(root))


def test_user_agent_in_other_harness_records_modes_without_enabling_apply(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    profile = root / "goose.md"
    profile.write_text("managed")
    options = {**kwargs(root), "harness": "goose"}
    install_baseline.capture(**options, written_paths=[str(profile)])
    assert len(install_baseline.verified_files(**options)) == 1
    profile.chmod(0o700)
    with pytest.raises(install_baseline.BaselineError, match="mode has changed"):
        install_baseline.verified_files(**options)


def test_shared_file_claim_is_not_silently_adopted(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    shared = root / "mcp.json"
    shared.write_text("{}")
    install_baseline.capture(**kwargs(root), written_paths=[str(shared)])
    with pytest.raises(install_baseline.BaselineError, match="shared"):
        install_baseline.capture(
            **{**kwargs(root), "agent_id": "33333333-3333-4333-8333-333333333333"},
            written_paths=[str(shared)],
        )
    assert install_baseline.verified_files(**kwargs(root))


def test_version_mismatch_missing_file_and_symlink_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    file = root / "AGENTS.md"
    file.write_text("managed")
    install_baseline.capture(**kwargs(root), written_paths=[str(file)])
    with pytest.raises(install_baseline.BaselineError):
        install_baseline.verified_files(**{**kwargs(root), "version": "2.0.0"})
    file.unlink()
    with pytest.raises(install_baseline.BaselineError):
        install_baseline.verified_files(**kwargs(root))
    target = root / "other"
    target.write_text("managed")
    file.symlink_to(target)
    with pytest.raises(install_baseline.BaselineError, match="symbolic link"):
        install_baseline.verified_files(**kwargs(root))
