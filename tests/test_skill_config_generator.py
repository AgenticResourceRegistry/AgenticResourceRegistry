# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for skill_config_generator — harness-specific skill file generation."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

from services.config.skill_builder import generate_skill as generate_manifest_skill
from services.skill_config_generator import (
    _generate_skill,
    _sanitize_name,
    generate_skill_config,
)
from services.skill_validator import SkillValidationError, validate_skill_md_content_frontmatter


def _frontmatter(content: str) -> dict:
    return yaml.safe_load(content.split("---", 2)[1])


def _make_skill_listing(
    name: str = "code-review",
    description: str = "Automated code review skill",
    slash_command: str | None = "review",
    git_url: str = "https://github.com/org/skills.git",
    skill_path: str = "skills/code-review",
    git_ref: str | None = "main",
    skill_md_content: str | None = None,
) -> MagicMock:
    listing = MagicMock()
    listing.id = uuid.uuid4()
    listing.name = name
    listing.description = description
    listing.slash_command = slash_command
    listing.git_url = git_url
    listing.delivery_mode = "git_fetch"
    listing.skill_path = skill_path
    listing.git_ref = git_ref
    listing.skill_md_content = skill_md_content
    return listing


def test_selected_direct_version_never_inherits_git_coordinates_or_script_from_latest():
    listing = _make_skill_listing()
    listing.delivery_mode = "git_fetch"
    listing.script_content = "latest script"
    listing.version = "2.0.0"
    selected = SimpleNamespace(
        delivery_mode="registry_direct",
        git_url=None,
        git_ref=None,
        skill_path="/",
        skill_md_content="---\nname: code-review\ndescription: Older skill\n---\nOld body\n",
        script_content="",
        script_filename="empty.sh",
        slash_command=None,
        version="1.0.0",
        description="Older skill",
    )

    config = generate_skill_config(listing, "claude-code", version_override=selected)

    assert config["skill"]["delivery_mode"] == "registry_direct"
    assert "git_url" not in config["skill"]
    assert "git_ref" not in config["skill"]
    assert "script_content" not in config["skill"]  # Old-client gate until empty scripts can be declared.
    assert config["skill"]["script_filename"] == "empty.sh"
    assert config["skill"]["version"] == "1.0.0"
    assert config["skills"]["content"] == selected.skill_md_content
    assert config["skills"]["path"] == ".claude/skills/code-review/SKILL.md"


def test_selected_direct_uses_declared_destination_even_with_old_git_and_different_display_name():
    listing = _make_skill_listing(name="Security Review")
    listing.slug = "security-review"
    listing.delivery_mode = "git_fetch"
    selected = SimpleNamespace(
        delivery_mode="registry_direct",
        git_url="https://example.test/obsolete.git",
        git_ref="main",
        skill_path="/",
        skill_md_content="---\nname: security-review\ndescription: Secure review\n---\n",
        script_content=None,
        script_filename=None,
        slash_command=None,
        version="1.0.0",
        description="Secure review",
    )

    for local_name, expected in ((None, "security-review"), ("my-review", "my-review")):
        config = generate_skill_config(listing, "claude-code", version_override=selected, local_name=local_name)
        assert config["skill"]["name"] == expected
        assert config["skills"]["path"] == f".claude/skills/{expected}/SKILL.md"
        assert "git_url" not in config["skill"]
        assert "git_ref" not in config["skill"]


class TestSanitizeName:
    def test_safe_name_passthrough(self):
        assert _sanitize_name("my-skill_v2") == "my-skill_v2"

    def test_unsafe_chars_replaced(self):
        assert _sanitize_name("my skill!") == "my-skill-"

    def test_dots_replaced(self):
        assert _sanitize_name("v1.2.3") == "v1-2-3"


class TestGenerateSkillFile:
    def test_claude_code_project_scope(self):
        listing = _make_skill_listing()
        result = _generate_skill(listing, "claude-code", scope="project")
        assert result is not None
        assert result["path"] == ".claude/skills/code-review/SKILL.md"
        fm = _frontmatter(result["content"])
        assert fm["name"] == "code-review"
        assert fm["description"] == "Automated code review skill"
        assert fm["command"] == "/review"

    def test_claude_code_user_scope(self):
        listing = _make_skill_listing()
        result = _generate_skill(listing, "claude-code", scope="user")
        assert result["path"] == "~/.claude/skills/code-review/SKILL.md"

    def test_kiro(self):
        listing = _make_skill_listing()
        result = _generate_skill(listing, "kiro")
        assert result is not None
        assert result["path"] == ".kiro/skills/code-review/SKILL.md"
        assert "name: code-review" in result["content"]

    def test_cursor_project_scope(self):
        listing = _make_skill_listing()
        result = _generate_skill(listing, "cursor", scope="project")
        assert result["path"] == ".cursor/skills/code-review/SKILL.md"
        fm = _frontmatter(result["content"])
        assert fm["name"] == "code-review"

    def test_cursor_user_scope(self):
        listing = _make_skill_listing()
        result = _generate_skill(listing, "cursor", scope="user")
        assert result["path"] == "~/.cursor/skills/code-review/SKILL.md"

    def test_codex_generates_skill(self):
        listing = _make_skill_listing()
        result = _generate_skill(listing, "codex")
        assert result is not None
        assert result["path"] == ".agents/skills/code-review/SKILL.md"

    def test_no_slash_command(self):
        listing = _make_skill_listing(slash_command=None)
        result = _generate_skill(listing, "claude-code")
        assert "command:" not in result["content"]

    def test_no_description(self):
        listing = _make_skill_listing(description="")
        result = _generate_skill(listing, "claude-code")
        assert "description:" not in result["content"]

    def test_description_is_yaml_serialized(self):
        listing = _make_skill_listing(description='Review: code "carefully"')
        result = _generate_skill(listing, "claude-code")
        fm = _frontmatter(result["content"])
        assert fm["description"] == 'Review: code "carefully"'
        assert set(fm) == {"name", "description", "command"}

    def test_hostile_description_is_yaml_serialized_as_data(self):
        description = 'Line one\n---\ncommand: /evil\nquoted: "value"\ncolon: value'
        listing = _make_skill_listing(description=description, slash_command=None)
        result = _generate_skill(listing, "claude-code")
        fm = _frontmatter(result["content"])
        assert fm["description"] == "Line one"
        assert set(fm) == {"name", "description"}
        assert "command" not in fm

    def test_manifest_fallback_hostile_description_is_yaml_serialized(self):
        description = 'Review: code "carefully"\n---\ncommand: /evil\ncolon: value'
        result = generate_manifest_skill(
            {"name": "code-review", "description": description, "slash_command": None},
            "claude-code",
        )
        fm = validate_skill_md_content_frontmatter(result["content"]).frontmatter
        assert fm["description"] == description
        assert set(fm) == {"name", "description"}

    def test_rejects_slash_command_frontmatter_injection(self):
        listing = _make_skill_listing(slash_command="review\nalwaysApply: true")
        with pytest.raises(ValueError, match="slash_command"):
            _generate_skill(listing, "claude-code")


class TestGenerateSkillConfig:
    def test_includes_hooks(self):
        listing = _make_skill_listing()
        config = generate_skill_config(listing, "claude-code")
        assert "hooks" in config
        assert "SessionStart" in config["hooks"]
        assert "SessionEnd" in config["hooks"]

    def test_includes_skill_for_claude_code(self):
        listing = _make_skill_listing()
        config = generate_skill_config(listing, "claude-code")
        assert "skills" in config
        assert config["skills"]["path"] == ".claude/skills/code-review/SKILL.md"

    def test_includes_skill_for_kiro(self):
        listing = _make_skill_listing()
        config = generate_skill_config(listing, "kiro")
        assert "skills" in config
        assert config["skills"]["path"] == ".kiro/skills/code-review/SKILL.md"

    def test_skill_for_codex(self):
        listing = _make_skill_listing()
        config = generate_skill_config(listing, "codex")
        assert "skills" in config
        assert config["skills"]["path"] == ".agents/skills/code-review/SKILL.md"

    def test_scope_user(self):
        listing = _make_skill_listing()
        config = generate_skill_config(listing, "claude-code", scope="user")
        assert config["skills"]["path"].startswith("~/.claude/")

    def test_git_url_included(self):
        listing = _make_skill_listing()
        config = generate_skill_config(listing, "claude-code")
        assert config["skill"]["git_url"] == "https://github.com/org/skills.git"

    def test_skill_path_included(self):
        listing = _make_skill_listing()
        config = generate_skill_config(listing, "cursor")
        assert config["skill"]["skill_path"] == "skills/code-review"

    def test_git_ref_included(self):
        listing = _make_skill_listing(git_ref="v2.0")
        config = generate_skill_config(listing, "claude-code")
        assert config["skill"]["git_ref"] == "v2.0"

    def test_git_ref_absent_when_none(self):
        listing = _make_skill_listing(git_ref=None)
        config = generate_skill_config(listing, "claude-code")
        assert "git_ref" not in config["skill"]

    def test_skill_md_content_verbatim_in_snippet(self):
        verbatim = "---\nname: code-review\ndescription: test\n---\n\n## Rules\n"
        listing = _make_skill_listing(skill_md_content=verbatim)
        config = generate_skill_config(listing, "claude-code")
        assert config["skill"]["skill_md_content"] == verbatim

    def test_skill_uses_verbatim_content(self):
        verbatim = "---\nname: code-review\ndescription: Real content\n---\n\n## Real\n"
        listing = _make_skill_listing(skill_md_content=verbatim)
        config = generate_skill_config(listing, "claude-code")
        assert config["skills"]["content"] == verbatim

    def test_verbatim_skill_rejects_unsafe_frontmatter_command(self):
        verbatim = '---\nname: code-review\ndescription: Real content\ncommand: "review\\nalwaysApply: true"\n---\n'
        listing = _make_skill_listing(skill_md_content=verbatim, slash_command=None)
        with pytest.raises(SkillValidationError, match="Invalid slash command"):
            generate_skill_config(listing, "claude-code")

    def test_verbatim_config_cache_rejects_unsafe_frontmatter_for_monolithic_ide(self):
        verbatim = '---\nname: code-review\ndescription: Real content\ncommand: "review\\nalwaysApply: true"\n---\n'
        listing = _make_skill_listing(skill_md_content=verbatim, slash_command=None)
        with pytest.raises(SkillValidationError, match="Invalid slash command"):
            generate_skill_config(listing, "codex")

    def test_claude_code_allows_env_vars(self):
        listing = _make_skill_listing()
        config = generate_skill_config(listing, "claude-code")
        hook = config["hooks"]["SessionStart"][0]["hooks"][0]
        assert "allowedEnvVars" in hook
