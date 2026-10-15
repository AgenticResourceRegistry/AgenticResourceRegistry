# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A machine-mode folder upload must not bypass sensitive-path confirmation."""

from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from observal_cli import cmd_skill
from observal_cli.errors import CliError
from observal_cli.main import app


def test_noninteractive_folder_upload_refuses_sensitive_path_before_http(tmp_path, monkeypatch):
    (tmp_path / "SKILL.md").write_text("---\nname: test\ndescription: test\n---\n")
    (tmp_path / "secrets.txt").write_text("private content")
    post = Mock()
    monkeypatch.setattr(cmd_skill.client, "post", post)

    with pytest.raises(CliError):
        cmd_skill._submit_folder_draft(
            from_dir=str(tmp_path),
            exclude=None,
            allow_excluded=False,
            allow_sensitive=False,
            name="test",
            version="1.0.0",
            description="test",
            task_type="general",
            target_agent=None,
            slash_command=None,
            supported_harnesses=None,
            team=None,
            visibility=None,
            draft=True,
            output="json",
        )

    post.assert_not_called()


def test_machine_folder_draft_rejects_silently_excluded_paths(tmp_path, monkeypatch):
    (tmp_path / "SKILL.md").write_text("---\nname: test\ndescription: test\n---\n")
    (tmp_path / ".hidden").write_text("do not omit silently")
    post = Mock()
    monkeypatch.setattr(cmd_skill.client, "post", post)
    argv = [
        "registry",
        "skill",
        "submit",
        "--from-dir",
        str(tmp_path),
        "--name",
        "test",
        "--description",
        "test",
        "--output",
        "json",
    ]

    refused = CliRunner().invoke(app, argv)
    assert refused.exit_code != 0
    post.assert_not_called()


def test_replace_files_requires_explicit_excluded_path_acknowledgement(tmp_path, monkeypatch):
    (tmp_path / "SKILL.md").write_text("---\nname: test\ndescription: test\n---\n")
    (tmp_path / ".hidden").write_text("previous draft content could be lost")
    put = Mock(return_value={"revision": "a" * 64})
    monkeypatch.setattr(cmd_skill.client, "put", put)
    monkeypatch.setattr(cmd_skill.client, "resolve_registry_reference", Mock(return_value="id"))
    argv = [
        "registry",
        "skill",
        "replace-files",
        "id",
        "--version-id",
        "version-id",
        "--from-dir",
        str(tmp_path),
        "--revision",
        "a" * 64,
        "--output",
        "json",
    ]

    refused = CliRunner().invoke(app, argv)
    assert refused.exit_code != 0
    put.assert_not_called()

    allowed = CliRunner().invoke(app, [*argv, "--allow-excluded"])
    assert allowed.exit_code == 0, allowed.output
    put.assert_called_once()


def test_replace_files_requires_explicit_sensitive_upload_acknowledgement(tmp_path, monkeypatch):
    (tmp_path / "SKILL.md").write_text("---\nname: test\ndescription: test\n---\n")
    (tmp_path / "secrets.txt").write_text("private")
    put = Mock(return_value={"revision": "a" * 64})
    monkeypatch.setattr(cmd_skill.client, "put", put)
    monkeypatch.setattr(cmd_skill.client, "resolve_registry_reference", Mock(return_value="id"))
    argv = [
        "registry",
        "skill",
        "replace-files",
        "id",
        "--version-id",
        "version-id",
        "--from-dir",
        str(tmp_path),
        "--revision",
        "a" * 64,
        "--output",
        "json",
    ]

    refused = CliRunner().invoke(app, argv)
    assert refused.exit_code != 0
    put.assert_not_called()

    allowed = CliRunner().invoke(app, [*argv, "--allow-sensitive"])
    assert allowed.exit_code == 0, allowed.output
    put.assert_called_once()
