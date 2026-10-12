# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A selected legacy release must not destroy ownership of a reviewed folder."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from observal_cli import cmd_skill, lockfile
from observal_cli.errors import CliError, ErrorCategory


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    machine = tmp_path / "machine"
    monkeypatch.setattr(lockfile, "CONFIG_DIR", machine)
    monkeypatch.setattr(lockfile, "LOCKFILE_PATH", machine / "lockfile.json")
    monkeypatch.setattr(lockfile, "_LOCKFILE_LOCK", machine / "lockfile.lock")
    monkeypatch.setattr(lockfile, "current_registry_url", lambda: "https://registry.example")
    destination = tmp_path / "home" / "demo"
    monkeypatch.setattr(cmd_skill, "_user_skill_dest", lambda _harness, _name: destination)
    return machine, destination


def _managed(isolated: tuple[Path, Path], *, name: str = "demo") -> dict:
    _, destination = isolated
    destination.mkdir(parents=True)
    (destination / "SKILL.md").write_text("reviewed bytes\n")
    proof = {
        "target": str(destination),
        "listing_id": "listing-1",
        "source": "standalone",
        "harness": "pi",
        "scope": "user",
        "registry_url": "https://registry.example",
        "version_id": "approved-folder-id",
        "digest": "sha256:folder",
    }
    data = {
        "registries": {
            "https://registry.example": {
                "server_url": "https://registry.example",
                "harnesses": {
                    "pi": {
                        "agents": [],
                        "standalone": [
                            {
                                "type": "skill",
                                "id": "listing-1",
                                "scope": "user",
                                "version": "1.0.0",
                                "folder_receipt": proof,
                            }
                        ],
                    },
                },
            }
        },
    }
    lockfile.write_lockfile(data)
    return proof


@pytest.mark.parametrize("mode", ["registry_direct", "git_fetch"])
def test_legacy_delivery_refuses_managed_ownership_before_writing(isolated, mode):
    _managed(isolated)
    _, destination = isolated
    with (
        pytest.raises(RuntimeError, match="verified folder"),
        cmd_skill._protect_legacy_skill_install(
            listing_id="listing-1",
            name="demo",
            harness="pi",
            scope="user",
            directory=None,
        ),
    ):
        if mode == "registry_direct":
            cmd_skill.install_skill_registry_direct(name="demo", skill_md_content="overwritten", dest=destination)
        else:
            (destination / "SKILL.md").write_text("overwritten")
    assert (destination / "SKILL.md").read_text() == "reviewed bytes\n"
    with pytest.raises(RuntimeError, match="verified skill folder"):
        lockfile.upsert_standalone(
            "pi", component_type="skill", component_id="listing-1", name="demo", scope="user", version="0.9.0"
        )
    assert lockfile.read_lockfile()["registries"]["https://registry.example"]["harnesses"]["pi"]["standalone"][0][
        "folder_receipt"
    ]


def test_legacy_receipt_in_other_folder_also_blocks_install(isolated, monkeypatch):
    _managed(isolated)
    _, destination = isolated
    alternate = destination.with_name("old-alias")
    monkeypatch.setattr(cmd_skill, "_user_skill_dest", lambda _harness, _name: alternate)
    with (
        pytest.raises(RuntimeError, match="verified folder"),
        cmd_skill._protect_legacy_skill_install(
            listing_id="listing-1",
            name=alternate.name,
            harness="pi",
            scope="user",
            directory=None,
        ),
    ):
        alternate.mkdir()
    assert not alternate.exists()


@pytest.mark.parametrize("mode", ["registry_direct", "git_fetch"])
def test_cli_legacy_version_refuses_managed_folder_before_legacy_writer(isolated, monkeypatch, mode):
    _managed(isolated)
    monkeypatch.setattr(lockfile, "local_registry_name", lambda *_args, **_kwargs: "demo")
    listing = {
        "id": "listing-1",
        "name": "demo",
        "namespace": "owner",
        "slug": "demo",
        "version": "2.0.0",
        "delivery_mode": "git_fetch",
    }
    monkeypatch.setattr(cmd_skill.client, "resolve_registry_reference", Mock(return_value="listing-1"))
    monkeypatch.setattr(cmd_skill.client, "get", Mock(side_effect=[listing, {"extra_files": []}]))
    monkeypatch.setattr(
        cmd_skill.client,
        "post_public",
        Mock(
            return_value={
                "version_id": "old-version-id",
                "version": "0.9.0",
                "config_snippet": {
                    "skill": {
                        "id": "listing-1",
                        "name": "demo",
                        "delivery_mode": mode,
                        "skill_md_content": "legacy bytes",
                        "git_url": "https://example.org/demo.git",
                    }
                },
            }
        ),
    )
    writer = Mock()
    monkeypatch.setattr(
        cmd_skill, "install_skill_from_git" if mode == "git_fetch" else "install_skill_registry_direct", writer
    )
    with pytest.raises(CliError) as error:
        cmd_skill.skill_install("listing-1", "pi", "user", False, False, "0.9.0", "json", False, False, None)
    assert error.value.category == ErrorCategory.CONFLICT
    writer.assert_not_called()
    assert (isolated[1] / "SKILL.md").read_text() == "reviewed bytes\n"


def test_existing_unmanaged_single_file_remains_installable(isolated):
    _, destination = isolated
    destination.mkdir(parents=True)
    (destination / "SKILL.md").write_text("historical\n")
    with cmd_skill._protect_legacy_skill_install(
        listing_id="listing-1",
        name="demo",
        harness="pi",
        scope="user",
        directory=None,
    ):
        cmd_skill.install_skill_registry_direct(name="demo", skill_md_content="new legacy\n", dest=destination)
    assert (destination / "SKILL.md").read_text() == "new legacy\n"


def test_selected_old_folder_uses_declared_name_when_latest_listing_is_git(isolated, monkeypatch):
    monkeypatch.setattr(lockfile, "local_registry_name", lambda *_args, **_kwargs: "other")
    listing = {
        "id": "listing-1",
        "namespace": "owner",
        "slug": "other",
        "name": "Other",
        "version": "2.0.0",
        "delivery_mode": "git_fetch",
    }
    selected = {
        "id": "folder-id",
        "version": "1.0.0",
        "extra_files": [{"path": "templates/example"}],
        "skill_md_content": "---\nname: declared\ndescription: Skill\n---\n",
    }
    monkeypatch.setattr(cmd_skill.client, "resolve_registry_reference", Mock(return_value="listing-1"))
    get = Mock(side_effect=[listing, selected])
    post = Mock(return_value={"version_id": "folder-id", "version": "1.0.0", "config_snippet": {"skill": {}}})
    monkeypatch.setattr(cmd_skill.client, "get", get)
    monkeypatch.setattr(cmd_skill.client, "post_public", post)
    # A no-write request avoids touching a harness, but still exercises
    # selected-version request building and server name validation.
    cmd_skill.skill_install("listing-1", "pi", "user", False, True, "1.0.0", "json", False, False, None)
    assert post.call_args.args[1]["local_name"] == "declared"
    assert post.call_args.args[1]["version"] == "1.0.0"
