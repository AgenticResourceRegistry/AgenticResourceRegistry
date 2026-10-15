# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Decoded direct skill bundle and shared phase-two contract."""

import base64
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect

from models.skill import SkillListing, SkillVersion
from schemas.skill_resources import SkillFileDeclaration, SkillResource
from services.skill_bundle import MAX_BUNDLE_BYTES, MAX_FILE_BYTES, validate_bundle_path, validate_skill_bundle
from services.skill_validator import SkillValidationError


def bundle(**changes):
    values = {"delivery_mode": "registry_direct", "skill_md_content": "# Example\n", "extra_files": []}
    values.update(changes)
    return validate_skill_bundle(**values)


def test_schema_defaults_and_listing_accessor():
    resource = SkillResource(path="templates/empty.txt", content="")
    assert resource.model_dump() == {
        "path": "templates/empty.txt",
        "content": "",
        "encoding": "utf-8",
        "executable": False,
    }
    listing = SkillListing()
    assert listing.extra_files == []
    with pytest.raises(RuntimeError, match="no latest_version"):
        listing.extra_files = []
    version = SkillVersion(extra_files=[resource.model_dump()])
    listing.latest_version = version
    assert listing.extra_files == [resource.model_dump()]
    listing.extra_files[0]["path"] = "lost-change"
    assert version.extra_files[0]["path"] == "templates/empty.txt"
    listing.extra_files = []
    assert version.extra_files == []
    assert inspect(version).attrs.extra_files.history.has_changes()


def test_manifest_rejects_invalid_digest_mode_and_size():
    expected = bundle()[0].declaration.model_dump()
    for patch in (
        {"sha256": "bad"},
        {"sha256": "F" * 64},
        {"size": -1},
        {"size": True},
        {"mode": "0777"},
        {"content": "secret"},
    ):
        with pytest.raises(ValidationError):
            SkillFileDeclaration.model_validate(expected | patch)


def test_binary_empty_and_metadata():
    files = bundle(
        script_content="",
        script_filename="run.sh",
        extra_files=[{"path": "assets/icon.bin", "content": "AP8=", "encoding": "base64"}],
    )
    assert [(f.path, f.content, f.declaration.mode) for f in files] == [
        ("SKILL.md", b"# Example\n", "0644"),
        ("scripts/run.sh", b"", "0755"),
        ("assets/icon.bin", b"\x00\xff", "0644"),
    ]


@pytest.mark.parametrize(
    "suffix, expected", [(".sh", "0755"), (".bash", "0755"), (".py", "0755"), (".rb", "0755"), (".txt", "0644")]
)
def test_legacy_script_mode_matches_cli_suffixes(suffix, expected):
    assert bundle(script_filename="run" + suffix, script_content="")[1].declaration.mode == expected


def test_empty_binary_file_and_explicit_executable_resource():
    files = bundle(extra_files=[{"path": "bin/tool", "content": "", "encoding": "base64", "executable": True}])
    assert files[1].content == b""
    assert files[1].declaration.size == 0
    assert files[1].declaration.mode == "0755"


def test_fixture_entire_file_set_for_both_install_surfaces():
    fixture = json.loads((Path(__file__).parent / "fixtures/skill_bundle_contract.json").read_text())
    payload = fixture["submit"]
    files = bundle(
        **{key: payload[key] for key in ("skill_md_content", "script_filename", "script_content", "extra_files")}
    )
    declarations = [file.declaration.model_dump() for file in files]
    assert fixture["standalone"]["config_snippet"]["skill"]["files"] == declarations
    assert fixture["agent"]["config_snippet"]["skill_components"][0]["files"] == declarations
    for component in (
        fixture["standalone"]["config_snippet"]["skill"],
        fixture["agent"]["config_snippet"]["skill_components"][0],
    ):
        assert component["destination"] + "/SKILL.md" == fixture["standalone"]["config_snippet"]["skills"]["path"]
        assert component["version_id"] == fixture["standalone"]["version_id"]
        assert component["extra_files"] == payload["extra_files"]
    assert fixture["edit"]["extra_files"] == []  # [] clears
    assert "extra_files" not in fixture["edit_inherit"]  # omitted inherits
    assert fixture["release"]["extra"]["extra_files"] == payload["extra_files"]
    assert fixture["standalone_request"]["supported_features"] == ["skill_extra_files_v1"]
    assert fixture["agent_request"]["supported_features"] == ["skill_extra_files_v1"]


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/absolute",
        "C:/escape",
        "//server/share",
        "a\\b",
        "a//b",
        "a/./b",
        "a/../b",
        "a/",
        "a/. ",
        "a.",
        "file ",
        "NUL.txt",
        "com1",
        "LPT².txt",
        "CONIN$",
        "CONOUT$.txt",
        "NUL .txt",
        "COM1 .log",
        "LPT² .bin",
        "x:y",
        "a?b",
        "a\x00b",
        "a\nb",
        "a\x7fb",
        "a\u0085b",
        "a\u202eb",
        "e\u0301.txt",
        ".git/config",
        "assets/.GIT/hooks/x",
        "a/" * 12 + "x",
        "x" * 101,
        "x" * 100 + "/" + "x" * 100 + "/" + "x" * 41,
        "😀" * 61,
        "\ud800",
    ],
)
def test_unsafe_paths(path):
    with pytest.raises(SkillValidationError):
        validate_bundle_path(path)


@pytest.mark.parametrize("path", ["docs/readme.md", "é.txt", "a/" * 11 + "x", "x" * 100, "😀" * 25])
def test_safe_paths(path):
    assert validate_bundle_path(path) == path


@pytest.mark.parametrize(
    "paths",
    [
        ["SKILL.md"],
        ["skill.md"],
        ["scripts/run.sh"],
        ["SCRIPTS/RUN.SH"],
        ["a", "a/b"],
        ["a/b", "a"],
        ["a/B", "A/b"],
        ["scripts", "scripts/run.sh"],
        ["skill.md/subfile"],
    ],
)
def test_collisions(paths):
    with pytest.raises(SkillValidationError, match=r"collid|overlap|Duplicate"):
        bundle(script_filename="run.sh", script_content="", extra_files=[{"path": p, "content": ""} for p in paths])


@pytest.mark.parametrize("content", ["?", "AP8", "AP8=\n", "AP8=!!", "😀"])
def test_invalid_base64(content):
    with pytest.raises(SkillValidationError):
        bundle(extra_files=[{"path": "a", "content": content, "encoding": "base64"}])


@pytest.mark.parametrize(
    "item",
    [
        {"path": "a", "content": "x", "encoding": "hex"},
        {"path": "a", "content": "x", "executable": 1},
        {"path": "a", "content": "x", "executable": "true"},
        {"path": "a", "content": "x", "mode": "0777"},
        {"path": "a", "content": "\ud800"},
        {"path": "a"},
    ],
)
def test_invalid_resource(item):
    with pytest.raises((SkillValidationError, ValidationError)):
        bundle(extra_files=[item])


def test_decoded_size_is_not_encoded_string_length():
    encoded = base64.b64encode(b"\xff" * MAX_FILE_BYTES).decode("ascii")
    assert len(bundle(extra_files=[{"path": "binary", "content": encoded, "encoding": "base64"}])) == 2
    encoded = base64.b64encode(b"\xff" * (MAX_FILE_BYTES + 1)).decode("ascii")
    with pytest.raises(SkillValidationError, match="per-file"):
        bundle(extra_files=[{"path": "binary", "content": encoded, "encoding": "base64"}])
    assert len(bundle(extra_files=[{"path": "utf8", "content": "é" * (MAX_FILE_BYTES // 2)}])) == 2
    with pytest.raises(SkillValidationError, match="per-file"):
        bundle(extra_files=[{"path": "utf8", "content": "é" * (MAX_FILE_BYTES // 2 + 1)}])


def test_per_file_and_bundle_boundaries():
    assert len(bundle(extra_files=[{"path": "a", "content": "x" * MAX_FILE_BYTES}])) == 2
    with pytest.raises(SkillValidationError, match="per-file"):
        bundle(extra_files=[{"path": "a", "content": "x" * (MAX_FILE_BYTES + 1)}])
    assert (
        len(
            bundle(
                skill_md_content="x" * (MAX_BUNDLE_BYTES - MAX_FILE_BYTES),
                extra_files=[{"path": "a", "content": "x" * MAX_FILE_BYTES}],
            )
        )
        == 2
    )
    with pytest.raises(SkillValidationError, match="bundle"):
        bundle(
            skill_md_content="x" * MAX_FILE_BYTES,
            script_filename="small.sh",
            script_content="x",
            extra_files=[{"path": "a", "content": "x" * MAX_FILE_BYTES}],
        )
    assert len(bundle(extra_files=[{"path": f"{i}", "content": ""} for i in range(128)])) == 129
    with pytest.raises(SkillValidationError, match="Too many"):
        bundle(extra_files=[{"path": f"{i}", "content": ""} for i in range(129)])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"script_filename": "run.sh"},
        {"script_content": ""},
        {"script_filename": "../run.sh", "script_content": ""},
        {"script_filename": "nested/run.sh", "script_content": ""},
        {"skill_md_content": None},
        {"skill_md_content": ""},
        {"skill_md_content": "\ud800"},
    ],
)
def test_invalid_legacy_fields(kwargs):
    with pytest.raises(SkillValidationError):
        bundle(**kwargs)


def test_document_and_legacy_script_obey_per_file_cap_on_new_writes():
    with pytest.raises(SkillValidationError, match="per-file"):
        bundle(skill_md_content="x" * (MAX_FILE_BYTES + 1))
    with pytest.raises(SkillValidationError, match="per-file"):
        bundle(script_filename="large.sh", script_content="x" * (MAX_FILE_BYTES + 1))
    # Existing stored rows can still be read under historical grandfathering.
    assert len(bundle(script_filename="large.sh", script_content="x" * (MAX_FILE_BYTES + 1), enforce_limits=False)) == 2


def test_git_compatibility_and_inherited_effective_bundle():
    assert validate_skill_bundle(delivery_mode="git_fetch", skill_md_content=None, extra_files=[]) == ()
    with pytest.raises(SkillValidationError, match="both be set"):
        validate_skill_bundle(delivery_mode="git_fetch", skill_md_content=None, script_filename="old", extra_files=[])
    with pytest.raises(SkillValidationError, match="git_fetch"):
        validate_skill_bundle(
            delivery_mode="git_fetch", skill_md_content=None, extra_files=[SkillResource(path="a", content="")]
        )
    assert (
        validate_skill_bundle(
            delivery_mode="git_fetch", skill_md_content=None, script_filename="go.sh", script_content="", extra_files=[]
        )
        == ()
    )
    assert len(bundle(extra_files=[])) == 1
    assert len(bundle(extra_files=[{"path": "a", "content": ""}])) == 2
    with pytest.raises(SkillValidationError):
        validate_skill_bundle(delivery_mode="unknown", skill_md_content="x", extra_files=[])
    with pytest.raises(SkillValidationError, match="list"):
        bundle(extra_files=None)
