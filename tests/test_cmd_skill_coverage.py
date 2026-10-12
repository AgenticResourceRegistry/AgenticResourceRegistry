# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import builtins
import json
import os
import subprocess
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, call

import pytest
from typer.testing import CliRunner

import observal_cli.cmd_skill as skill
from observal_cli import lockfile
from observal_cli.errors import CliError, ErrorCategory
from observal_cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_cli(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(skill, "spinner", lambda *_args, **_kwargs: nullcontext())


def _skill_item(**overrides):
    item = {
        "id": "skill-123456789",
        "name": "Review Skill",
        "namespace": "alice",
        "slug": "review-skill",
        "qualified_name": "alice/review-skill",
        "version": "1.2.3",
        "latest_version": "1.2.3",
        "status": "approved",
        "validated": True,
        "task_type": "code-review",
        "delivery_mode": "git_fetch",
        "description": "Reviews changes",
        "git_url": "https://github.com/acme/review-skill",
        "git_ref": "main",
        "skill_path": "skills/review",
        "script_filename": None,
        "slash_command": "review",
        "target_agents": ["claude-code", "pi"],
        "created_at": None,
    }
    item.update(overrides)
    return item


def test_skill_fork_pins_observed_approved_base_and_reports_exact_draft(monkeypatch):
    monkeypatch.setattr(skill.client, "resolve_registry_reference", Mock(return_value="listing-uuid"))
    base = {
        "version_id": "base-uuid",
        "revision": "a" * 64,
        "delivery_mode": "registry_direct",
        "version": "1.0.0",
    }
    get = Mock(return_value=base)
    post = Mock(return_value={"version_id": "draft-uuid", "revision": "b" * 64})
    monkeypatch.setattr(skill.client, "get", get)
    monkeypatch.setattr(skill.client, "post", post)
    result = runner.invoke(
        app,
        [
            "registry",
            "skill",
            "fork",
            "alice/example",
            "--version",
            "1.1.0",
            "--description",
            "Add templates",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["version_id"] == "draft-uuid"
    get.assert_called_once_with(
        "/api/v1/skills/listing-uuid/approved-base", operation="Fork skill", resource="alice/example"
    )
    assert post.call_args.args[1] == {
        "base_version_id": "base-uuid",
        "observed_base_revision": "a" * 64,
        "version": "1.1.0",
        "description": "Add templates",
        "changelog": None,
    }


def test_skill_import_folder_sends_complete_snapshot_with_reviewed_base(monkeypatch, tmp_path):
    monkeypatch.setattr(skill.client, "resolve_registry_reference", Mock(return_value="listing-uuid"))
    monkeypatch.setattr(
        skill.client,
        "get",
        Mock(return_value={"version_id": "git-base-uuid", "revision": "a" * 64, "delivery_mode": "git_fetch"}),
    )
    monkeypatch.setattr(
        skill,
        "_capture_skill_snapshot",
        Mock(
            return_value={
                "skill_md_content": "---\nname: example\ndescription: Example\n---\n",
                "extra_files": [{"path": "scripts/run.sh", "content": "echo ok", "executable": True}],
            }
        ),
    )
    post = Mock(return_value={"version_id": "draft-uuid", "revision": "b" * 64})
    monkeypatch.setattr(skill.client, "post", post)
    result = runner.invoke(
        app,
        [
            "registry",
            "skill",
            "import-folder",
            "alice/example",
            "--from-dir",
            str(tmp_path),
            "--version",
            "1.3.0",
            "--description",
            "A complete folder",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["version_id"] == "draft-uuid"
    assert post.call_args.args[0] == "/api/v1/skills/listing-uuid/folder-import-drafts"
    assert post.call_args.args[1]["extra_files"][0]["path"] == "scripts/run.sh"
    assert post.call_args.args[1]["observed_base_revision"] == "a" * 64


def test_skill_fork_validates_folder_before_creation_and_recovers_failed_replacement(monkeypatch, tmp_path):
    monkeypatch.setattr(skill.client, "resolve_registry_reference", Mock(return_value="listing-uuid"))
    monkeypatch.setattr(
        skill.client,
        "get",
        Mock(return_value={"version_id": "base-uuid", "revision": "a" * 64, "delivery_mode": "registry_direct"}),
    )
    post = Mock(return_value={"version_id": "draft-uuid", "revision": "b" * 64})
    monkeypatch.setattr(skill.client, "post", post)
    source = tmp_path / "skill"
    source.mkdir()
    capture = Mock(
        side_effect=CliError(category=ErrorCategory.VALIDATION, message="Invalid folder", operation="Fork skill")
    )
    monkeypatch.setattr(skill, "_capture_skill_snapshot", capture)
    argv = [
        "registry",
        "skill",
        "fork",
        "alice/example",
        "--version",
        "1.1.0",
        "--description",
        "Add templates",
        "--from-dir",
        str(source),
    ]
    assert runner.invoke(app, argv).exit_code != 0
    post.assert_not_called()
    capture.side_effect = None
    capture.return_value = {"skill_md_content": "---\nname: example\n---", "extra_files": []}
    monkeypatch.setattr(
        skill.client,
        "put",
        Mock(side_effect=CliError(category=ErrorCategory.CONFLICT, message="stale", operation="Replace fork files")),
    )
    result = runner.invoke(app, argv)
    assert result.exit_code != 0
    assert "draft-uuid" in result.output
    post.assert_called_once()


def test_registration_path_safety_and_user_destinations(tmp_path):
    parent = Mock()
    skill.register_skill(parent)
    parent.add_typer.assert_called_once_with(skill.skill_app, name="skill")

    base = tmp_path / "base"
    assert skill._is_path_safe(base / "nested", base)
    assert not skill._is_path_safe(tmp_path / "outside", base)
    assert skill._user_skill_dest("claude_code", "review") == Path.home() / ".claude/skills/review"
    assert skill._user_skill_dest("unknown", "review") == Path.home() / ".agents/skills/review"


def test_parse_frontmatter_valid_invalid_and_missing_yaml(monkeypatch):
    content = "---\r\nname: review\r\ndescription: Reviews code\r\ncommand: /review\r\n---\r\nBody"
    assert skill._parse_frontmatter(content) == {
        "name": "review",
        "description": "Reviews code",
        "command": "/review",
    }
    assert skill._parse_frontmatter("# No frontmatter") == {}
    assert skill._parse_frontmatter("---\n- one\n- two\n---") == {}
    assert skill._parse_frontmatter("---\nname: [broken\n---") == {}

    real_import = builtins.__import__

    def import_without_yaml(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("yaml unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_yaml)
    assert skill._parse_frontmatter(content) == {}


def test_submit_existing_draft(monkeypatch):
    post = Mock(return_value={"id": "skill-1"})
    resolve = Mock(return_value="resolved-draft")
    monkeypatch.setattr(skill.client, "post", post)
    monkeypatch.setattr(skill.client, "resolve_registry_reference", resolve)

    submitted = runner.invoke(app, ["registry", "skill", "submit", "--submit", "alice/draft"])
    assert submitted.exit_code == 0, submitted.output
    assert "Draft submitted for review" in submitted.output
    resolve.assert_called_once_with("skill", "alice/draft")
    post.assert_called_once_with("/api/v1/skills/resolved-draft/submit")


def test_submit_rejects_draft_and_existing_draft_together(monkeypatch):
    post = Mock()
    monkeypatch.setattr(skill.client, "post", post)

    result = runner.invoke(
        app,
        ["registry", "skill", "submit", "--draft", "--submit", "skill-1"],
    )

    assert result.exit_code == 7
    assert "Draft creation" in result.output
    post.assert_not_called()


def test_submit_from_file_draft_preserves_payload_and_publish_target(tmp_path, monkeypatch):
    payload = {
        "name": "file-skill",
        "version": "2.0.0",
        "description": "Loaded from JSON",
        "task_type": "testing",
        "delivery_mode": "registry_direct",
        "skill_md_content": "# Test",
    }
    source = tmp_path / "skill.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    publish_target = Mock()
    post = Mock(
        return_value={
            "id": "skill-file",
            "name": "file-skill",
            "qualified_name": "team/file-skill",
            "validated": False,
        }
    )
    monkeypatch.setattr(skill.client, "add_publish_target", publish_target)
    monkeypatch.setattr(skill.client, "post", post)

    result = runner.invoke(
        app,
        [
            "registry",
            "skill",
            "submit",
            "--from-file",
            str(source),
            "--draft",
            "--team",
            "platform",
            "--visibility",
            "team",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Draft submitted" in result.output
    assert "unvalidated" in result.output
    publish_target.assert_called_once_with(payload, "platform", "team")
    post.assert_called_once_with("/api/v1/skills/draft", payload)


@pytest.mark.parametrize(
    ("filename", "contents", "exit_code", "message"),
    [
        ("missing.json", None, 5, "not found"),
        ("broken.json", "{not-json", 7, "not valid JSON"),
    ],
)
def test_submit_from_file_reports_read_failures(tmp_path, filename, contents, exit_code, message):
    source = tmp_path / filename
    if contents is not None:
        source.write_text(contents, encoding="utf-8")

    result = runner.invoke(app, ["registry", "skill", "submit", "--from-file", str(source)])

    assert result.exit_code == exit_code
    assert message in result.output


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "bad-task", "task_type": "unknown", "version": "1.0.0"},
        {"name": "bad-version", "task_type": "general", "version": "not-a-version"},
    ],
)
def test_submit_from_file_validates_payload_fields(payload, tmp_path, monkeypatch):
    source = tmp_path / "skill.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    post = Mock()
    monkeypatch.setattr(skill.client, "post", post)

    result = runner.invoke(app, ["registry", "skill", "submit", "--from-file", str(source)])

    assert result.exit_code == 7
    post.assert_not_called()


def test_submit_registry_direct_parses_metadata_and_script(tmp_path, monkeypatch):
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(
        "---\nname: review-helper\ndescription: Reviews changes\ncommand: /review-now\n---\n\nDo the review.\n",
        encoding="utf-8",
    )
    script = tmp_path / "run.sh"
    script.write_text("#!/bin/sh\necho review\n", encoding="utf-8")
    monkeypatch.setattr(skill.config, "load", Mock(return_value={"username": "alice"}))
    publish_target = Mock()
    monkeypatch.setattr(skill.client, "add_publish_target", publish_target)
    post = Mock(
        return_value={
            "id": "skill-direct",
            "name": "review-helper",
            "qualified_name": "alice/review-helper",
            "validated": True,
        }
    )
    monkeypatch.setattr(skill.client, "post", post)

    result = runner.invoke(
        app,
        [
            "registry",
            "skill",
            "submit",
            "--skill-md",
            str(skill_md),
            "--script",
            str(script),
            "--delivery-mode",
            "registry_direct",
            "--task-type",
            "code-review",
            "--target-agent",
            "claude-code",
            "--target-agent",
            "pi",
            "--harness",
            "claude-code",
            "--team",
            "platform",
            "--visibility",
            "team",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Parsed SKILL.md" in result.output
    assert "Read script" in result.output
    assert "validated" in result.output
    endpoint, payload = post.call_args.args
    assert endpoint == "/api/v1/skills/submit"
    assert payload == {
        "name": "review-helper",
        "version": "1.0.0",
        "description": "Reviews changes",
        "owner": "alice",
        "task_type": "code-review",
        "target_agents": ["claude-code", "pi"],
        "delivery_mode": "registry_direct",
        "supported_harnesses": ["claude-code"],
        "slash_command": "review-now",
        "skill_md_content": skill_md.read_text(encoding="utf-8"),
        "script_content": script.read_text(encoding="utf-8"),
        "script_filename": "run.sh",
    }
    publish_target.assert_called_once_with(payload, "platform", "team")


def test_submit_registry_direct_without_frontmatter_uses_explicit_metadata(tmp_path, monkeypatch):
    skill_md = tmp_path / "PLAIN.md"
    skill_md.write_text("# Plain skill\n\nFollow instructions.\n", encoding="utf-8")
    monkeypatch.setattr(skill.config, "load", Mock(return_value={"username": "alice"}))
    monkeypatch.setattr(skill.client, "add_publish_target", Mock())
    post = Mock(return_value={"id": "plain", "name": "plain-skill"})
    monkeypatch.setattr(skill.client, "post", post)

    result = runner.invoke(
        app,
        [
            "registry",
            "skill",
            "submit",
            "--skill-md",
            str(skill_md),
            "--delivery-mode",
            "registry_direct",
            "--name",
            "plain-skill",
            "--description",
            "Uses explicit metadata",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Parsed SKILL.md" not in result.output
    payload = post.call_args.args[1]
    assert payload["name"] == "plain-skill"
    assert payload["description"] == "Uses explicit metadata"
    assert payload["skill_md_content"] == skill_md.read_text(encoding="utf-8")
    assert "slash_command" not in payload


def test_submit_git_fetch_flags_build_complete_payload(monkeypatch):
    monkeypatch.setattr(skill.config, "load", Mock(return_value={"username": "alice"}))
    monkeypatch.setattr(skill.client, "add_publish_target", Mock())
    post = Mock(return_value={"id": "git-skill", "name": "git-skill", "validated": False})
    monkeypatch.setattr(skill.client, "post", post)

    result = runner.invoke(
        app,
        [
            "registry",
            "skill",
            "submit",
            "--name",
            "git-skill",
            "--version",
            "3.0.0",
            "--description",
            "Fetched from Git",
            "--task-type",
            "documentation",
            "--git-url",
            "https://github.com/acme/skills",
            "--git-ref",
            "v3",
            "--skill-path",
            "skills/docs",
            "--slash-command",
            "docs",
        ],
    )

    assert result.exit_code == 0, result.output
    assert post.call_args.args == (
        "/api/v1/skills/submit",
        {
            "name": "git-skill",
            "version": "3.0.0",
            "description": "Fetched from Git",
            "owner": "alice",
            "task_type": "documentation",
            "target_agents": [],
            "delivery_mode": "git_fetch",
            "supported_harnesses": [],
            "git_url": "https://github.com/acme/skills",
            "skill_path": "skills/docs",
            "git_ref": "v3",
            "slash_command": "docs",
        },
    )


def test_submit_interactive_collects_agents_and_git_source(monkeypatch):
    answers = iter(
        [
            "claude-code, pi, ",
            "interactive-skill",
            "1.4.0",
            "Interactive description",
            "https://github.com/acme/interactive",
            "skills/interactive",
            "release",
        ]
    )
    monkeypatch.setattr(skill, "text_input", lambda *_args, **_kwargs: next(answers))
    monkeypatch.setattr(skill, "select_one", Mock(return_value="debugging"))
    monkeypatch.setattr(skill.config, "load", Mock(return_value={"username": "alice"}))
    monkeypatch.setattr(skill.client, "add_publish_target", Mock())
    post = Mock(return_value={"id": "interactive", "name": "interactive-skill"})
    monkeypatch.setattr(skill.client, "post", post)

    result = runner.invoke(app, ["registry", "skill", "submit"])

    assert result.exit_code == 0, result.output
    assert post.call_args.args[1] == {
        "name": "interactive-skill",
        "version": "1.4.0",
        "description": "Interactive description",
        "owner": "alice",
        "task_type": "debugging",
        "target_agents": ["claude-code", "pi"],
        "delivery_mode": "git_fetch",
        "git_url": "https://github.com/acme/interactive",
        "skill_path": "skills/interactive",
        "git_ref": "release",
    }


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--name", "missing-description"], "name and description"),
        (["--name", "bad-task", "--description", "Bad", "--task-type", "unknown"], "Unknown skill task type"),
        (["--name", "no-git", "--description", "No Git"], "Git URL is required"),
    ],
)
def test_submit_flag_validation(arguments, message, monkeypatch):
    monkeypatch.setattr(skill.config, "load", Mock(return_value={"username": "alice"}))
    post = Mock()
    monkeypatch.setattr(skill.client, "post", post)

    result = runner.invoke(app, ["registry", "skill", "submit", *arguments])

    assert result.exit_code == 7
    assert message in result.output
    post.assert_not_called()


@pytest.mark.parametrize(
    ("option", "filename", "message"),
    [
        ("--skill-md", "missing-skill.md", "SKILL.md file was not found"),
        ("--script", "missing-script.sh", "script file was not found"),
    ],
)
def test_submit_reports_missing_content_files(option, filename, message):
    result = runner.invoke(app, ["registry", "skill", "submit", option, filename])

    assert result.exit_code == 5
    assert message in result.output


def test_list_filters_json_and_caches_results(monkeypatch):
    data = [_skill_item(), _skill_item(id="skill-2", slug="test-skill", name="Test Skill")]
    get = Mock(return_value=data)
    save = Mock()
    monkeypatch.setattr(skill.client, "get", get)
    monkeypatch.setattr(skill.client, "resolve_team_id", Mock(return_value="team-1"))
    monkeypatch.setattr(skill.config, "save_last_results", save)

    result = runner.invoke(
        app,
        [
            "registry",
            "skill",
            "list",
            "--task-type",
            "testing",
            "--target-agent",
            "pi",
            "--harness",
            "claude-code",
            "--search",
            "review",
            "--namespace",
            "@Alice",
            "--team",
            "platform",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"items": data, "total": 2, "page": 1, "page_size": 2}
    get.assert_called_once_with(
        "/api/v1/skills",
        params={
            "task_type": "testing",
            "target_agent": "pi",
            "harness": "claude-code",
            "search": "review",
            "namespace": "alice",
            "team_id": "team-1",
        },
    )
    save.assert_called_once_with(data, "skill")


def test_list_empty_rejects_plain_and_renders_table(monkeypatch):
    item = _skill_item()
    monkeypatch.setattr(skill.client, "get", Mock(side_effect=[[], [item]]))
    save = Mock()
    monkeypatch.setattr(skill.config, "save_last_results", save)

    empty = runner.invoke(app, ["registry", "skill", "list"])
    plain = runner.invoke(app, ["registry", "skill", "list", "--output", "plain"])
    table = runner.invoke(app, ["registry", "skill", "list"])

    assert empty.exit_code == table.exit_code == 0
    assert plain.exit_code == 2
    assert "Error" in plain.output
    assert "plain" in plain.output
    assert "No skills found" in empty.output
    assert "Skills (1)" in table.output
    assert "@alice" in table.output
    assert save.call_count == 2


def test_my_empty_json_rejects_plain_and_renders_table(monkeypatch):
    item = _skill_item(status="pending")
    monkeypatch.setattr(skill.client, "get", Mock(side_effect=[[], [item], [item]]))
    save = Mock()
    monkeypatch.setattr(skill.config, "save_last_results", save)

    empty = runner.invoke(app, ["registry", "skill", "my"])
    plain = runner.invoke(app, ["registry", "skill", "my", "--output", "plain"])
    as_json = runner.invoke(app, ["registry", "skill", "my", "--output", "json"])
    table = runner.invoke(app, ["registry", "skill", "my"])

    assert empty.exit_code == as_json.exit_code == table.exit_code == 0
    assert plain.exit_code == 2
    assert "Error" in plain.output
    assert "plain" in plain.output
    assert "You have no skills" in empty.output
    assert json.loads(as_json.output) == {"items": [item], "total": 1, "page": 1, "page_size": 1}
    assert "My Skills (1)" in table.output
    assert save.call_count == 3


def test_show_renders_metadata_and_json(monkeypatch):
    item = _skill_item(
        delivery_mode="registry_direct",
        git_ref=None,
        script_filename="run.py",
        created_at="not-a-date",
    )
    resolve = Mock(return_value="resolved")
    approved = {"id": "version-uuid", "version": "1.2.3", "status": "approved"}
    manifest = {
        "version_id": "version-uuid",
        "files": [
            {"path": "SKILL.md", "size": 12, "mode": "0644"},
            {"path": "scripts/run.sh", "size": 8, "mode": "0755"},
        ],
    }

    def get_show(url, params=None):
        if url == "/api/v1/skills/resolved":
            return item
        if url.endswith("/versions"):
            assert params == {"page": 1, "page_size": 50}
            return {"items": [approved], "total": 1}
        if url.endswith("/versions/version-uuid/manifest"):
            return manifest
        pytest.fail(f"Unexpected show route: {url}")

    get = Mock(side_effect=get_show)
    monkeypatch.setattr(skill.client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(skill.client, "get", get)

    rendered = runner.invoke(app, ["registry", "skill", "show", "alice/review-skill"])
    as_json = runner.invoke(
        app,
        ["registry", "skill", "show", "alice/review-skill", "--output", "json"],
    )

    assert rendered.exit_code == as_json.exit_code == 0
    assert "review-skill v1.2.3" in rendered.output
    assert "registry_direct" in rendered.output
    assert "run.py" in rendered.output
    assert "/review" in rendered.output
    assert "claude-code, pi" in rendered.output
    assert "not-a-date" in rendered.output
    assert "scripts/run.sh" in rendered.output
    assert "0755" in rendered.output
    assert "export" in rendered.output
    assert json.loads(as_json.output) == {**item, "selected_version": approved, "files": manifest["files"]}
    assert resolve.call_args_list == [call("skill", "alice/review-skill"), call("skill", "alice/review-skill")]
    assert get.call_count == 6


def test_show_archived_direct_skill_keeps_metadata_but_rejects_explicit_archived_version(monkeypatch):
    item = _skill_item(delivery_mode="registry_direct", status="archived")
    monkeypatch.setattr(skill.client, "resolve_registry_reference", Mock(return_value="resolved"))

    def get_show(url, params=None):
        if url == "/api/v1/skills/resolved":
            return item
        if url.endswith("/versions"):
            return {"items": [{"id": "archived-uuid", "version": "1.2.3", "status": "archived"}], "total": 1}
        pytest.fail(f"Archived skill must not fetch a file manifest: {url}")

    monkeypatch.setattr(skill.client, "get", get_show)
    shown = runner.invoke(app, ["registry", "skill", "show", "alice/review-skill", "--output", "json"])
    explicit = runner.invoke(app, ["registry", "skill", "show", "alice/review-skill", "--version", "1.2.3"])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output) == {**item, "selected_version": None, "files": None}
    assert explicit.exit_code != 0
    assert "selected approved skill version is unavailable" in explicit.output


def test_show_surfaces_http_failure(monkeypatch):
    monkeypatch.setattr(skill.client, "resolve_registry_reference", Mock(return_value="missing"))
    monkeypatch.setattr(skill.client, "get", Mock(side_effect=RuntimeError("registry offline")))

    result = runner.invoke(app, ["registry", "skill", "show", "missing"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Error (unexpected)" in result.output
    assert "Run observal registry skill show" in result.output


def test_sparse_clone_copies_requested_source_without_real_git(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    source = checkout / "skills" / "review"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("review", encoding="utf-8")
    destination = tmp_path / "installed"
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(skill.tempfile, "TemporaryDirectory", lambda: nullcontext(str(checkout)))
    monkeypatch.setattr(skill.subprocess, "run", run)

    installed = skill._sparse_clone_skill_dir(
        "https://github.com/acme/skills",
        "/skills/review/SKILL.md",
        "release",
        destination,
    )

    assert installed is True
    assert (destination / "SKILL.md").read_text(encoding="utf-8") == "review"
    assert (checkout / ".git/info/sparse-checkout").read_text(encoding="utf-8") == "skills/review/\n"
    assert run.call_args_list[0] == call(["git", "--version"], check=True, capture_output=True, timeout=5)
    assert run.call_args_list[-1].args[0] == ["git", "checkout", "FETCH_HEAD"]
    assert all(entry.kwargs["cwd"] == checkout for entry in run.call_args_list[1:])


def test_sparse_clone_root_defaults_and_missing_source(tmp_path, monkeypatch):
    checkout = tmp_path / "root-checkout"
    checkout.mkdir()
    (checkout / "SKILL.md").write_text("root", encoding="utf-8")
    destination = tmp_path / "root-installed"
    monkeypatch.setattr(skill.tempfile, "TemporaryDirectory", lambda: nullcontext(str(checkout)))
    monkeypatch.setattr(
        skill.subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 0)),
    )

    assert skill._sparse_clone_skill_dir("https://example.test/repo", None, None, destination)
    assert (destination / "SKILL.md").read_text(encoding="utf-8") == "root"
    assert (checkout / ".git/info/sparse-checkout").read_text(encoding="utf-8") == "/\n"

    missing_checkout = tmp_path / "missing-checkout"
    missing_checkout.mkdir()
    monkeypatch.setattr(skill.tempfile, "TemporaryDirectory", lambda: nullcontext(str(missing_checkout)))
    assert not skill._sparse_clone_skill_dir(
        "https://example.test/repo",
        "skills/missing",
        "main",
        tmp_path / "missing-destination",
    )


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("git missing"),
        subprocess.CalledProcessError(1, ["git"]),
        subprocess.TimeoutExpired(["git"], 5),
    ],
)
def test_sparse_clone_reports_git_probe_failures(monkeypatch, tmp_path, failure):
    monkeypatch.setattr(skill.subprocess, "run", Mock(side_effect=failure))

    assert not skill._sparse_clone_skill_dir("https://example.test/repo", "/", "main", tmp_path / "dest")


def test_sparse_clone_handles_checkout_failure(monkeypatch, tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    calls = 0

    def run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise subprocess.TimeoutExpired(["git", "init"], 30)
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(skill.tempfile, "TemporaryDirectory", lambda: nullcontext(str(checkout)))
    monkeypatch.setattr(skill.subprocess, "run", run)

    assert not skill._sparse_clone_skill_dir("https://example.test/repo", "/", "main", tmp_path / "dest")


def test_install_command_registry_direct_tracks_project_metadata(monkeypatch):
    listing = _skill_item()
    snippet = {
        "skill": {
            "id": "skill-version",
            "name": "Review Skill",
            "version": "2.0.0",
            "delivery_mode": "registry_direct",
            "skill_md_content": "# Review",
            "script_content": "print('review')",
            "script_filename": "run.py",
        },
        "settings": {"enabled": True},
    }
    monkeypatch.setattr(skill.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(skill.client, "get", Mock(return_value=listing))
    post = Mock(return_value={"config_snippet": snippet, "warnings": ["Restart the harness"]})
    monkeypatch.setattr(skill.client, "post", post)
    monkeypatch.setattr(lockfile, "local_registry_name", Mock(return_value="alice-review-skill"))
    upsert = Mock()
    monkeypatch.setattr(lockfile, "upsert_standalone", upsert)
    direct_install = Mock()
    git_install = Mock()
    monkeypatch.setattr(skill, "install_skill_registry_direct", direct_install)
    monkeypatch.setattr(skill, "install_skill_from_git", git_install)

    result = runner.invoke(
        app,
        [
            "registry",
            "skill",
            "install",
            "alice/review-skill",
            "--harness",
            "pi",
            "--scope",
            "project",
            "--version",
            "2.0.0",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Restart the harness" in result.output
    assert "Config for pi" in result.output
    post.assert_called_once_with(
        "/api/v1/skills/resolved/install",
        {
            "harness": "pi",
            "scope": "project",
            "local_name": "alice-review-skill",
            "supported_features": ["skill_extra_files_v1"],
            "version": "2.0.0",
        },
    )
    direct_install.assert_called_once_with(
        name="Review Skill",
        skill_md_content="# Review",
        script_content="print('review')",
        script_filename="run.py",
        harness="pi",
        scope="project",
    )
    git_install.assert_not_called()
    upsert.assert_called_once_with(
        "pi",
        component_type="skill",
        name="Review Skill",
        component_id="skill-version",
        version="2.0.0",
        scope="project",
        directory=str(Path.cwd()),
        namespace="alice",
        slug="review-skill",
        local_name="alice-review-skill",
        version_id=None,
        digest=None,
        requested_version="2.0.0",
    )


def test_install_command_reports_lockfile_failure(monkeypatch):
    listing = _skill_item()
    skill_info = {
        "name": "Review Skill",
        "id": "skill-version",
        "latest_version": "1.3.0",
        "delivery_mode": "git_fetch",
        "git_url": "https://github.com/acme/review",
        "git_ref": "stable",
        "skill_path": "skills/review",
        "skill_md_content": "cached",
    }
    monkeypatch.setattr(skill.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(skill.client, "get", Mock(return_value=listing))
    monkeypatch.setattr(skill.client, "post", Mock(return_value={"config_snippet": {"skill": skill_info}}))
    monkeypatch.setattr(lockfile, "local_registry_name", Mock(return_value="review-skill"))
    monkeypatch.setattr(lockfile, "upsert_standalone", Mock(side_effect=OSError("read only")))
    git_install = Mock()
    monkeypatch.setattr(skill, "install_skill_from_git", git_install)

    result = runner.invoke(
        app,
        ["registry", "skill", "install", "review", "--harness", "claude-code"],
    )

    assert result.exit_code == 9
    assert "installed state" in result.output and "recorded" in result.output
    git_install.assert_called_once_with(
        name="Review Skill",
        git_url="https://github.com/acme/review",
        skill_path="skills/review",
        git_ref="stable",
        harness="claude-code",
        scope="user",
        skill_md_content="cached",
    )


def test_install_command_raw_and_no_write_skip_filesystem_and_lockfile(monkeypatch):
    listing = _skill_item()
    snippet = {"skill": {"name": "Review Skill", "delivery_mode": "git_fetch"}, "value": 1}
    monkeypatch.setattr(skill.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(skill.client, "get", Mock(return_value=listing))
    monkeypatch.setattr(
        skill.client,
        "post",
        Mock(side_effect=[snippet, {"config_snippet": snippet}]),
    )
    monkeypatch.setattr(lockfile, "local_registry_name", Mock(return_value="review-skill"))
    upsert = Mock()
    monkeypatch.setattr(lockfile, "upsert_standalone", upsert)
    direct_install = Mock()
    git_install = Mock()
    monkeypatch.setattr(skill, "install_skill_registry_direct", direct_install)
    monkeypatch.setattr(skill, "install_skill_from_git", git_install)

    raw = runner.invoke(
        app,
        ["registry", "skill", "install", "review", "--harness", "pi", "--raw"],
    )
    no_write = runner.invoke(
        app,
        ["registry", "skill", "install", "review", "--harness", "pi", "--no-write"],
    )

    assert raw.exit_code == no_write.exit_code == 0
    assert json.loads(raw.output) == snippet
    assert "Skill install skipped" in no_write.output
    direct_install.assert_not_called()
    git_install.assert_not_called()
    upsert.assert_not_called()


def test_install_command_surfaces_listing_failure(monkeypatch):
    monkeypatch.setattr(skill.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(skill.client, "get", Mock(side_effect=RuntimeError("lookup failed")))
    post = Mock()
    monkeypatch.setattr(skill.client, "post", post)

    result = runner.invoke(
        app,
        ["registry", "skill", "install", "missing", "--harness", "pi"],
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Error (unexpected)" in result.output
    assert "Run observal registry skill install" in result.output
    post.assert_not_called()


def test_registry_direct_install_writes_content_script_and_project_links(tmp_path, monkeypatch):
    project = tmp_path / "project"
    link = Mock()
    monkeypatch.setattr(skill, "_symlink_for_harnesses", link)

    destination = skill.install_skill_registry_direct(
        name="Review Helper",
        skill_md_content="# Review",
        script_content="#!/bin/sh\necho review\n",
        script_filename="run.sh",
        scope="project",
        cwd=project,
    )

    expected = project / ".agents/skills/review-helper"
    assert destination == expected
    assert (expected / "SKILL.md").read_text(encoding="utf-8") == "# Review"
    script_path = expected / "scripts/run.sh"
    assert script_path.read_text(encoding="utf-8") == "#!/bin/sh\necho review\n"
    assert os.stat(script_path).st_mode & 0o111
    link.assert_called_once_with(project, expected, "review-helper")


def test_registry_direct_install_rejects_missing_content(tmp_path, capsys):
    destination = tmp_path / "direct"

    result = skill.install_skill_registry_direct(name="empty", skill_md_content=None, dest=destination)

    assert result is None
    assert not destination.exists()
    assert "No SKILL.md content" in capsys.readouterr().out


def test_registry_direct_install_rejects_unsafe_script_filename(tmp_path, capsys):
    destination = tmp_path / "direct"

    result = skill.install_skill_registry_direct(
        name="unsafe-script",
        skill_md_content="# Safe",
        script_content="bad",
        script_filename="../escape.sh",
        dest=destination,
    )

    assert result == destination
    assert (destination / "SKILL.md").exists()
    assert not (destination / "escape.sh").exists()
    assert "Unsafe script filename" in capsys.readouterr().out


def test_registry_direct_install_uses_user_destination(tmp_path, monkeypatch):
    destination = tmp_path / "user-direct"
    user_dest = Mock(return_value=destination)
    monkeypatch.setattr(skill, "_user_skill_dest", user_dest)

    result = skill.install_skill_registry_direct(
        name="User Direct",
        skill_md_content="# User",
        ide="pi",
    )

    assert result == destination
    user_dest.assert_called_once_with("pi", "user-direct")


def test_registry_direct_install_keeps_plain_scripts_non_executable(tmp_path):
    destination = tmp_path / "plain-script"

    skill.install_skill_registry_direct(
        name="Plain Script",
        skill_md_content="# Plain",
        script_content="data",
        script_filename="notes.txt",
        dest=destination,
    )

    assert not os.stat(destination / "scripts/notes.txt").st_mode & 0o111


def test_registry_direct_install_rejects_unsafe_skill_name(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(skill, "_sanitize_name", Mock(return_value="../escape"))

    result = skill.install_skill_registry_direct(
        name="ignored",
        skill_md_content="# Unsafe",
        scope="project",
        cwd=tmp_path / "project",
    )

    assert result is None
    assert "Unsafe skill name" in capsys.readouterr().out


def test_git_install_success_and_failures(tmp_path, monkeypatch, capsys):
    project = tmp_path / "project"
    clone = Mock(return_value=True)
    link = Mock()
    monkeypatch.setattr(skill, "_sparse_clone_skill_dir", clone)
    monkeypatch.setattr(skill, "_symlink_for_harnesses", link)

    installed = skill.install_skill_from_git(
        name="Review Helper",
        git_url="https://github.com/acme/review",
        skill_path="skills/review",
        git_ref="stable",
        scope="project",
        cwd=project,
    )
    expected = project / ".agents/skills/review-helper"
    assert installed == expected
    clone.assert_called_once_with(
        "https://github.com/acme/review",
        "skills/review",
        "stable",
        expected,
    )
    link.assert_called_once_with(project, expected, "review-helper")
    assert "Skill directory written" in capsys.readouterr().out

    clone.return_value = False
    failed = tmp_path / "failed"
    assert (
        skill.install_skill_from_git(
            name="Review",
            git_url="https://github.com/acme/review",
            skill_md_content="# Cached",
            dest=failed,
        )
        is None
    )
    assert not (failed / "SKILL.md").exists()
    assert "Git skill clone failed" in capsys.readouterr().out

    empty = tmp_path / "empty"
    assert skill.install_skill_from_git(name="Empty", git_url=None, dest=empty) is None
    assert "Git URL is required" in capsys.readouterr().out


def test_git_install_user_destination_and_unsafe_project_name(tmp_path, monkeypatch, capsys):
    user_destination = tmp_path / "user-skill"
    user_dest = Mock(return_value=user_destination)
    monkeypatch.setattr(skill, "_user_skill_dest", user_dest)
    monkeypatch.setattr(skill, "_sparse_clone_skill_dir", Mock(return_value=True))

    result = skill.install_skill_from_git(
        name="Review",
        git_url="https://example.test/review",
        ide="pi",
    )

    assert result == user_destination
    user_dest.assert_called_once_with("pi", "review")

    monkeypatch.setattr(skill, "_sanitize_name", Mock(return_value="../escape"))
    assert (
        skill.install_skill_from_git(
            name="ignored",
            git_url=None,
            scope="project",
            cwd=tmp_path / "project",
        )
        is None
    )
    assert "Unsafe skill name" in capsys.readouterr().out


def test_symlink_for_existing_harnesses_and_existing_links(tmp_path, capsys):
    canonical = tmp_path / ".agents/skills/review"
    canonical.mkdir(parents=True)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".cursor").mkdir()
    existing = tmp_path / ".cursor/skills/review"
    existing.mkdir(parents=True)

    skill._symlink_for_harnesses(tmp_path, canonical, "review")
    skill._symlink_for_harnesses(tmp_path, canonical, "review")

    link = tmp_path / ".claude/skills/review"
    assert link.is_symlink()
    assert link.resolve() == canonical.resolve()
    assert existing.is_dir() and not existing.is_symlink()
    assert capsys.readouterr().out.count("symlinked") == 1


def test_symlink_failure_is_not_suppressed(tmp_path, monkeypatch):
    canonical = tmp_path / ".agents/skills/review"
    canonical.mkdir(parents=True)
    (tmp_path / ".kiro").mkdir()
    monkeypatch.setattr(Path, "symlink_to", Mock(side_effect=OSError("unsupported")))

    with pytest.raises(OSError, match="unsupported"):
        skill._symlink_for_harnesses(tmp_path, canonical, "review")

    assert not (tmp_path / ".kiro/skills/review").exists()


def test_edit_flags_acquire_lock_and_send_updates(monkeypatch):
    monkeypatch.setattr(skill.client, "resolve_registry_reference", Mock(return_value="resolved"))
    post = Mock(return_value={})
    put = Mock(return_value={"name": "new-name", "status": "draft"})
    monkeypatch.setattr(skill.client, "post", post)
    monkeypatch.setattr(skill.client, "put", put)

    result = runner.invoke(
        app,
        [
            "registry",
            "skill",
            "edit",
            "alice/review",
            "--name",
            "new-name",
            "--description",
            "New description",
            "--version",
            "2.0.0",
            "--task-type",
            "testing",
            "--git-url",
            "https://github.com/acme/new",
            "--git-ref",
            "v2",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Updated new-name" in result.output
    post.assert_called_once_with("/api/v1/skills/resolved/start-edit")
    put.assert_called_once_with(
        "/api/v1/skills/resolved/draft",
        {
            "name": "new-name",
            "description": "New description",
            "version": "2.0.0",
            "task_type": "testing",
            "git_url": "https://github.com/acme/new",
            "git_ref": "v2",
        },
    )


def test_edit_from_file_and_file_failures(tmp_path, monkeypatch):
    resolve = Mock(return_value="resolved")
    monkeypatch.setattr(skill.client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(skill.client, "post", Mock(return_value={}))
    put = Mock(return_value={"name": "file-edit", "status": "pending"})
    monkeypatch.setattr(skill.client, "put", put)
    updates = {"name": "file-edit", "description": "From file"}
    source = tmp_path / "updates.json"
    source.write_text(json.dumps(updates), encoding="utf-8")

    valid = runner.invoke(
        app,
        ["registry", "skill", "edit", "skill-1", "--from-file", str(source)],
    )
    missing = runner.invoke(
        app,
        ["registry", "skill", "edit", "skill-1", "--from-file", str(tmp_path / "missing.json")],
    )
    broken_source = tmp_path / "broken.json"
    broken_source.write_text("{broken", encoding="utf-8")
    broken = runner.invoke(
        app,
        ["registry", "skill", "edit", "skill-1", "--from-file", str(broken_source)],
    )

    assert valid.exit_code == 0, valid.output
    assert missing.exit_code == 5
    assert broken.exit_code == 7
    assert "not found" in missing.output
    assert "not valid JSON" in broken.output
    resolve.assert_called_once_with("skill", "skill-1")
    put.assert_called_once_with("/api/v1/skills/resolved/draft", updates)


@pytest.mark.parametrize("updates", [{"task_type": "unknown"}, {"version": "not-a-version"}])
def test_edit_from_file_validates_before_registry_resolution(updates, tmp_path, monkeypatch):
    source = tmp_path / "updates.json"
    source.write_text(json.dumps(updates), encoding="utf-8")
    resolve = Mock()
    monkeypatch.setattr(skill.client, "resolve_registry_reference", resolve)

    result = runner.invoke(app, ["registry", "skill", "edit", "alice/review", "--from-file", str(source)])

    assert result.exit_code == 7
    resolve.assert_not_called()


def test_edit_validation_conflict_and_save_cancellation(monkeypatch):
    monkeypatch.setattr(skill.client, "resolve_registry_reference", Mock(return_value="resolved"))
    post = Mock()
    monkeypatch.setattr(skill.client, "post", post)
    monkeypatch.setattr(skill.client, "put", Mock())

    no_changes = runner.invoke(app, ["registry", "skill", "edit", "skill-1"])
    assert no_changes.exit_code == 7
    assert "No skill changes" in no_changes.output
    post.assert_not_called()

    post.side_effect = CliError(
        ErrorCategory.CONFLICT,
        "The skill is currently being edited.",
        operation="Edit skill",
        resource="skill registry",
    )
    conflict = runner.invoke(
        app,
        ["registry", "skill", "edit", "skill-1", "--description", "new"],
    )
    assert conflict.exit_code == 6
    assert "currently being edited" in conflict.output

    post.reset_mock(side_effect=True)
    post.return_value = {}
    monkeypatch.setattr(skill.client, "post", post)
    monkeypatch.setattr(
        skill.client,
        "put",
        Mock(
            side_effect=CliError(
                ErrorCategory.UNAVAILABLE,
                "Registry unavailable.",
                operation="Edit skill",
                resource="skill registry",
            )
        ),
    )
    failed = runner.invoke(
        app,
        ["registry", "skill", "edit", "skill-1", "--description", "new"],
    )

    assert failed.exit_code == 9
    assert "Registry unavailable" in failed.output
    post.assert_called_once_with("/api/v1/skills/resolved/start-edit")


def test_skill_archive_unarchive_and_confirmation_cancellation(monkeypatch):
    resolve = Mock(return_value="resolved")
    get = Mock(return_value={"name": "Review Skill"})
    patch = Mock(return_value={})
    monkeypatch.setattr(skill.client, "resolve_registry_reference", resolve)
    monkeypatch.setattr(skill.client, "get", get)
    monkeypatch.setattr(skill.client, "patch", patch)

    archived = runner.invoke(app, ["registry", "skill", "archive", "alice/review", "--yes"])
    restored = runner.invoke(app, ["registry", "skill", "unarchive", "alice/review"], input="y\n")
    cancelled = runner.invoke(app, ["registry", "skill", "archive", "alice/review"], input="n\n")

    assert archived.exit_code == restored.exit_code == 0
    assert cancelled.exit_code == 1
    assert "Skill archived" in archived.output
    assert "Skill restored" in restored.output
    assert "Aborted" in cancelled.output
    assert get.call_args_list == [
        call("/api/v1/skills/resolved"),
        call("/api/v1/skills/resolved"),
    ]
    assert patch.call_args_list == [
        call("/api/v1/skills/resolved/archive"),
        call("/api/v1/skills/resolved/unarchive"),
    ]


def test_skill_co_author_commands_render_and_preserve_http_boundaries(monkeypatch):
    get = Mock(
        side_effect=[
            [{"email": "dev@example.com", "username": "dev", "is_active": False}],
            [],
        ]
    )
    post = Mock(
        side_effect=[
            {"email": "dev@example.com", "username": "dev"},
            {"email": "other@example.com", "username": "other"},
        ]
    )
    delete = Mock(return_value={})
    monkeypatch.setattr(skill.client, "get", get)
    monkeypatch.setattr(skill.client, "post", post)
    monkeypatch.setattr(skill.client, "delete", delete)

    listed = runner.invoke(app, ["registry", "skill", "co-authors", "list", "skill-1"])
    empty = runner.invoke(app, ["registry", "skill", "co-authors", "list", "skill-1"])
    email_added = runner.invoke(
        app,
        ["registry", "skill", "co-authors", "add", "skill-1", "DEV@EXAMPLE.COM"],
    )
    username_added = runner.invoke(
        app,
        ["registry", "skill", "co-authors", "add", "skill-1", "@other"],
    )
    removed = runner.invoke(
        app,
        [
            "registry",
            "skill",
            "co-authors",
            "remove",
            "skill-1",
            "22222222-2222-2222-2222-222222222222",
        ],
    )

    assert all(result.exit_code == 0 for result in (listed, empty, email_added, username_added, removed))
    assert "dev@example.com" in listed.output
    assert any(cell.strip() == "no" for line in listed.output.splitlines() for cell in line.split("│"))
    assert "No co-authors" in empty.output
    assert "Added co-author" in email_added.output
    assert "Co-author removed" in removed.output
    assert post.call_args_list == [
        call("/api/v1/skills/skill-1/co-authors", json_data={"email": "dev@example.com"}),
        call("/api/v1/skills/skill-1/co-authors", json_data={"username": "other"}),
    ]
    delete.assert_called_once_with("/api/v1/skills/skill-1/co-authors/22222222-2222-2222-2222-222222222222")


def test_skill_submit_json_is_noninteractive_and_clean(tmp_path, monkeypatch):
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("# Skill\n", encoding="utf-8")
    monkeypatch.setattr(
        skill.client,
        "post",
        Mock(return_value={"id": "skill-1", "name": "review", "status": "pending", "validated": True}),
    )

    result = runner.invoke(
        app,
        [
            "registry",
            "skill",
            "submit",
            "--skill-md",
            str(skill_md),
            "--delivery-mode",
            "registry_direct",
            "--name",
            "review",
            "--description",
            "Review code",
            "--task-type",
            "code-review",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["id"] == "skill-1"


def test_skill_install_json_reports_local_write(tmp_path, monkeypatch):
    listing = _skill_item()
    skill_info = {
        "id": "skill-1",
        "name": "Review Skill",
        "version": "1.0.0",
        "delivery_mode": "registry_direct",
        "skill_md_content": "# Review",
    }
    monkeypatch.setattr(skill.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(skill.client, "get", Mock(return_value=listing))
    monkeypatch.setattr(skill.client, "post", Mock(return_value={"config_snippet": {"skill": skill_info}}))
    monkeypatch.setattr(lockfile, "local_registry_name", Mock(return_value="review-skill"))
    installed = tmp_path / "review-skill"
    monkeypatch.setattr(skill, "install_skill_registry_direct", Mock(return_value=installed))
    upsert = Mock()
    monkeypatch.setattr(lockfile, "upsert_standalone", upsert)

    result = runner.invoke(
        app,
        ["registry", "skill", "install", "alice/review", "--harness", "pi", "--output", "json"],
    )

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["write_performed"] is True
    assert payload["installed_path"] == str(installed)
    upsert.assert_called_once()


def test_skill_install_does_not_record_failed_write(monkeypatch):
    listing = _skill_item()
    skill_info = {"name": "Review Skill", "delivery_mode": "registry_direct", "skill_md_content": None}
    monkeypatch.setattr(skill.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(skill.client, "get", Mock(return_value=listing))
    monkeypatch.setattr(skill.client, "post", Mock(return_value={"config_snippet": {"skill": skill_info}}))
    monkeypatch.setattr(lockfile, "local_registry_name", Mock(return_value="review-skill"))
    monkeypatch.setattr(skill, "install_skill_registry_direct", Mock(return_value=None))
    upsert = Mock()
    monkeypatch.setattr(lockfile, "upsert_standalone", upsert)

    result = runner.invoke(app, ["registry", "skill", "install", "review", "--harness", "pi"])

    assert result.exit_code == 9
    upsert.assert_not_called()


def test_skill_edit_json_returns_server_result(monkeypatch):
    monkeypatch.setattr(skill.client, "resolve_registry_reference", Mock(return_value="resolved"))
    monkeypatch.setattr(skill.client, "post", Mock(return_value={}))
    monkeypatch.setattr(
        skill.client,
        "put",
        Mock(return_value={"id": "skill-1", "name": "review", "status": "pending"}),
    )

    result = runner.invoke(
        app,
        ["registry", "skill", "edit", "alice/review", "--description", "Updated", "--output", "json"],
    )

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "pending"


@pytest.mark.parametrize(
    "arguments",
    [
        ["list", "--task-type", "coding"],
        ["list", "--harness", "cursor"],
        ["install", "review", "--harness", "cursor"],
        ["install", "review", "--harness", "pi", "--scope", "invalid"],
        ["install", "review", "--harness", "pi", "--raw", "--output", "json"],
    ],
)
def test_skill_validation_uses_stable_exit_code(arguments, monkeypatch):
    get = Mock()
    monkeypatch.setattr(skill.client, "get", get)

    result = runner.invoke(app, ["registry", "skill", *arguments])

    assert result.exit_code == 7
    get.assert_not_called()
