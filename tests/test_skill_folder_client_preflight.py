# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Agent folder preflight must not touch activation config or existing user files."""

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from typer.testing import CliRunner

from observal_cli import cmd_pull
from observal_cli.cmd_pull import write_install_snippet
from observal_cli.errors import CliError
from observal_cli.main import app
from observal_cli.skill_folder import BundleInstallError, install_folder_bundle, validate_bundle

FIXTURE = Path(__file__).parent / "fixtures" / "skill_folder_install_contract.json"


def _agent_install():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["agent"]["response"]


def test_exact_folder_submit_observes_selected_revision(monkeypatch):
    import observal_cli.cmd_skill as cmd_skill

    monkeypatch.setattr(cmd_skill.client, "resolve_registry_reference", Mock(return_value="listing-uuid"))
    get = Mock(return_value={"version_id": "version-uuid", "revision": "a" * 64})
    post = Mock(return_value={"listing_id": "listing-uuid", "version_id": "version-uuid"})
    monkeypatch.setattr(cmd_skill.client, "get", get)
    monkeypatch.setattr(cmd_skill.client, "post", post)

    result = CliRunner().invoke(
        app,
        ["registry", "skill", "submit", "--submit", "listing-uuid", "--version-id", "version-uuid", "--output", "json"],
    )
    assert result.exit_code == 0, result.output
    get.assert_called_once_with("/api/v1/skills/listing-uuid/versions/version-uuid/manifest")
    post.assert_called_once_with(
        "/api/v1/skills/listing-uuid/versions/version-uuid/submit", {"observed_revision": "a" * 64}
    )


def test_skill_export_verifies_binary_attachment_before_atomic_write(tmp_path, monkeypatch):
    from observal_cli import cmd_skill

    listing_id = "1" * 32
    version_id = "2" * 32
    skill_md = b"---\nname: exported\ndescription: test\n---\n"
    icon = bytes([0, 255]) + b"image"
    files = [
        {"path": path, "size": len(body), "sha256": hashlib.sha256(body).hexdigest(), "mode": "0644"}
        for path, body in [("SKILL.md", skill_md), ("assets/icon.bin", icon)]
    ]
    manifest = {"listing_id": listing_id, "version_id": version_id, "revision": "a" * 64, "files": files}
    monkeypatch.setattr(cmd_skill.client, "resolve_registry_reference", Mock(return_value=listing_id))
    monkeypatch.setattr(cmd_skill.client, "get", Mock(return_value=manifest))

    def get_file(path):
        if path.endswith("SKILL.md"):
            return json.dumps(
                {
                    "version_id": version_id,
                    "revision": manifest["revision"],
                    "file": files[0],
                    "content": skill_md.decode(),
                    "encoding": "utf-8",
                }
            ).encode(), {"content-type": "application/json"}
        return icon, {"content-type": "application/octet-stream"}

    monkeypatch.setattr(cmd_skill.client, "get_bytes_with_headers", get_file)
    dest = tmp_path / "exported"
    result = CliRunner().invoke(
        app, ["registry", "skill", "export", listing_id, str(dest), "--version-id", version_id, "--output", "json"]
    )
    assert result.exit_code == 0, result.output
    assert (dest / "SKILL.md").read_bytes() == skill_md
    assert (dest / "assets" / "icon.bin").read_bytes() == icon

    # Default selection must not mistake a pending draft for the latest
    # approved version; listing detail does not contain latest_version_id.
    history = {"items": [{"id": "3" * 32, "status": "pending"}, {"id": version_id, "status": "approved"}], "total": 2}
    get = Mock(side_effect=lambda path, params=None: history if params else manifest)
    monkeypatch.setattr(cmd_skill.client, "get", get)
    selected = tmp_path / "selected"
    result = CliRunner().invoke(app, ["registry", "skill", "export", listing_id, str(selected), "--output", "json"])
    assert result.exit_code == 0, result.output
    assert (selected / "assets" / "icon.bin").read_bytes() == icon
    assert get.call_args_list[0].args == (f"/api/v1/skills/{listing_id}/versions", {"page": 1, "page_size": 50})

    # A corrupt attachment cannot create even a partial new destination.
    monkeypatch.setattr(
        cmd_skill.client,
        "get_bytes_with_headers",
        lambda path: (b"tampered", {"content-type": "application/octet-stream"}),
    )
    broken = tmp_path / "broken"
    result = CliRunner().invoke(
        app, ["registry", "skill", "export", listing_id, str(broken), "--version-id", version_id, "--output", "json"]
    )
    assert result.exit_code != 0
    assert not broken.exists()


def test_export_accepts_server_manifest_with_legacy_script_and_128_extras(tmp_path, monkeypatch):
    from observal_cli import cmd_skill

    listing_id, version_id = "1" * 32, "2" * 32
    md = b"---\nname: legacy\ndescription: complete folder\n---\n"
    entries = [("SKILL.md", md), ("scripts/run.sh", b"echo ok")]
    entries += [(f"templates/{i}", b"") for i in range(128)]
    files = [
        {"path": path, "size": len(body), "sha256": hashlib.sha256(body).hexdigest(), "mode": "0644"}
        for path, body in entries
    ]
    manifest = {"listing_id": listing_id, "version_id": version_id, "revision": "a" * 64, "files": files}
    monkeypatch.setattr(cmd_skill.client, "resolve_registry_reference", Mock(return_value=listing_id))
    monkeypatch.setattr(cmd_skill.client, "get", Mock(return_value=manifest))
    contents = dict(entries)
    declarations = {file["path"]: file for file in files}

    def get_file(url):
        path = url.split("/files/", 1)[1]
        return json.dumps(
            {
                "version_id": version_id,
                "revision": manifest["revision"],
                "file": declarations[path],
                "content": contents[path].decode(),
                "encoding": "utf-8",
            }
        ).encode(), {"content-type": "application/json"}

    monkeypatch.setattr(cmd_skill.client, "get_bytes_with_headers", get_file)
    dest = tmp_path / "exported"
    result = CliRunner().invoke(
        app, ["registry", "skill", "export", listing_id, str(dest), "--version-id", version_id, "--output", "json"]
    )
    assert result.exit_code == 0, result.output
    assert (dest / "scripts" / "run.sh").read_bytes() == b"echo ok"
    assert len(list(dest.rglob("*"))) == 132  # 130 files and two directories.


def test_complete_folder_is_written_before_agent_activation(tmp_path, monkeypatch):
    response = _agent_install()
    snippet = response["config_snippet"]
    dest = tmp_path / ".pi" / "agents" / "example-agent" / "skills" / "example"
    observed = []

    def checked_write(path, content, *, merge_mcp=False):
        assert (dest / "SKILL.md").is_file(), "Agent config was written before required folder"
        observed.append(path)
        return "created"

    monkeypatch.setattr(cmd_pull, "_write_file_checked", checked_write)
    written, failed = write_install_snippet(
        snippet,
        harness="pi",
        adapter=MagicMock(),
        target_dir=tmp_path,
        agent_id=response["agent_id"],
        is_user_scope=False,
        skill_bundles=response["skill_bundles"],
        lock=response["lock"],
    )
    assert failed == []
    assert observed
    assert any(status == "installed (complete folder)" for _, status in written)


def test_stage_integrity_detects_same_size_file_corruption(tmp_path, monkeypatch):
    bundle = validate_bundle(_agent_install()["skill_bundles"][0])
    original_write = Path.write_bytes

    def corrupt_stage_file(path, content):
        if path.name == "SKILL.md" and ".stage." in str(path):
            content = b"X" + content[1:]
        return original_write(path, content)

    monkeypatch.setattr(Path, "write_bytes", corrupt_stage_file)
    target = tmp_path / "installed-skill"
    with pytest.raises(BundleInstallError):
        install_folder_bundle(bundle, target)
    assert not target.exists()
    assert not list(tmp_path.glob(".installed-skill.stage.*"))


def test_duplicate_bundle_destination_refuses_before_any_folder_write(tmp_path):
    response = _agent_install()
    snippet = response["config_snippet"]
    snippet["skill_components"].append(dict(snippet["skill_components"][0]))
    target = tmp_path / ".pi" / "agents" / "example-agent" / "skills" / "example"

    with pytest.raises(CliError):
        write_install_snippet(
            snippet,
            harness="pi",
            adapter=MagicMock(),
            target_dir=tmp_path,
            agent_id=response["agent_id"],
            is_user_scope=False,
            skill_bundles=response["skill_bundles"],
            lock=response["lock"],
        )

    assert not target.exists(), "All bundle destination conflicts must fail before any folder is written"
    assert not (tmp_path / "AGENTS.md").exists()


def test_existing_skill_refuses_before_any_agent_config_write(tmp_path):
    response = _agent_install()
    snippet = response["config_snippet"]
    snippet["mcp_config"] = {"path": "settings.json", "content": {"enabled": True}}
    dest = tmp_path / ".pi" / "agents" / "example-agent" / "skills" / "example"
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("someone else's skill", encoding="utf-8")
    (dest / "notes.txt").write_text("private user data", encoding="utf-8")

    with pytest.raises(CliError):
        write_install_snippet(
            snippet,
            harness="pi",
            adapter=MagicMock(),
            target_dir=tmp_path,
            agent_id=response["agent_id"],
            is_user_scope=False,
            skill_bundles=response["skill_bundles"],
            lock=response["lock"],
        )

    assert (dest / "notes.txt").read_text() == "private user data"
    assert (dest / "SKILL.md").read_text() == "someone else's skill"
    assert not (tmp_path / "settings.json").exists()


@pytest.mark.parametrize("corruption", ["missing", "wrong_digest", "wrong_path"])
def test_invalid_required_bundle_refuses_before_agent_config(tmp_path, corruption):
    response = _agent_install()
    snippet = response["config_snippet"]
    snippet["mcp_config"] = {"path": "settings.json", "content": {"enabled": True}}
    if corruption == "missing":
        response["skill_bundles"] = []
    elif corruption == "wrong_digest":
        response["skill_bundles"][0]["digest"] = "observal-content-v2:sha256:invalid"
    else:
        response["skill_bundles"][0]["skill_file_path"] = ".claude/skills/other/SKILL.md"

    with pytest.raises(CliError):
        write_install_snippet(
            snippet,
            harness="pi",
            adapter=MagicMock(),
            target_dir=tmp_path,
            agent_id=response["agent_id"],
            is_user_scope=False,
            skill_bundles=response["skill_bundles"],
            lock=response["lock"],
        )

    assert not (tmp_path / "settings.json").exists()
