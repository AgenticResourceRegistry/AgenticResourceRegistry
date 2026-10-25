# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for services/skill_validator.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.skill_validator import (
    SkillValidationError,
    _build_raw_url,
    _parse_frontmatter,
    parse_skill_frontmatter,
    validate_skill_md,
    validate_skill_md_content_frontmatter,
)

# ── _build_raw_url ──────────────────────────────────────────────────────────


class TestBuildRawUrl:
    def test_github_no_suffix(self):
        url = _build_raw_url("https://github.com/owner/repo", "skills/my-skill", "main")
        assert url == "https://raw.githubusercontent.com/owner/repo/main/skills/my-skill/SKILL.md"

    def test_github_dot_git_suffix(self):
        url = _build_raw_url("https://github.com/owner/repo.git", "skills/my-skill", "main")
        assert url == "https://raw.githubusercontent.com/owner/repo/main/skills/my-skill/SKILL.md"

    def test_skill_md_file_path_is_normalized_to_its_directory(self):
        url = _build_raw_url("https://github.com/owner/repo", "skills/my-skill/SKILL.md", "main")
        assert url == "https://raw.githubusercontent.com/owner/repo/main/skills/my-skill/SKILL.md"

    def test_github_root_skill_path(self):
        url = _build_raw_url("https://github.com/owner/repo", "/", "v1.0")
        assert url == "https://raw.githubusercontent.com/owner/repo/v1.0/SKILL.md"

    def test_github_empty_skill_path(self):
        url = _build_raw_url("https://github.com/owner/repo", "", "main")
        assert url == "https://raw.githubusercontent.com/owner/repo/main/SKILL.md"

    def test_non_github_fallback(self):
        url = _build_raw_url("https://gitlab.com/owner/repo", "skills/x", "main")
        assert "raw" in url
        assert "SKILL.md" in url


# ── _parse_frontmatter ──────────────────────────────────────────────────────


class TestParseFrontmatter:
    def test_valid_frontmatter(self):
        content = "---\nname: my-skill\ndescription: Does things\n---\n\n## Body"
        result = _parse_frontmatter(content)
        assert result["name"] == "my-skill"
        assert result["description"] == "Does things"

    def test_with_command_field(self):
        content = "---\nname: review\ndescription: Code review\ncommand: /review\n---\n"
        result = _parse_frontmatter(content)
        assert result["command"] == "/review"

    def test_parse_quoted_command_field(self):
        content = '---\nname: review\ndescription: Code review\ncommand: "/review"\n---\n'
        result = parse_skill_frontmatter(content)
        assert result["command"] == "/review"

    def test_no_frontmatter(self):
        assert _parse_frontmatter("# Just markdown") == {}

    def test_malformed_yaml(self):
        content = "---\nname: [unclosed\n---\n"
        # Should not raise — returns empty dict
        result = _parse_frontmatter(content)
        assert result == {}

    def test_non_dict_yaml(self):
        content = "---\n- item1\n- item2\n---\n"
        assert _parse_frontmatter(content) == {}


# ── validate_skill_md_content_frontmatter ──────────────────────────────────


class TestValidateSkillMdContentFrontmatter:
    def test_no_frontmatter_is_ok(self):
        result = validate_skill_md_content_frontmatter("# Plain markdown")
        assert result.has_frontmatter is False
        assert result.slash_command is None

    def test_accepts_safe_command_and_normalizes(self):
        content = '---\nname: review\ndescription: Code review\ncommand: "/review"\n---\n'
        result = validate_skill_md_content_frontmatter(content)
        assert result.has_frontmatter is True
        assert result.frontmatter["command"] == "/review"
        assert result.slash_command == "review"

    def test_rejects_command_injection(self):
        content = '---\nname: review\ndescription: Code review\ncommand: "review\\nalwaysApply: true"\n---\n'
        with pytest.raises(SkillValidationError, match="Invalid slash command"):
            validate_skill_md_content_frontmatter(content)

    def test_rejects_malformed_yaml_frontmatter(self):
        content = "---\nname: [unclosed\n---\n"
        with pytest.raises(SkillValidationError, match=r"Malformed SKILL\.md frontmatter"):
            validate_skill_md_content_frontmatter(content)

    def test_rejects_missing_closing_frontmatter_delimiter(self):
        content = "---\nname: review\ndescription: Code review\n"
        with pytest.raises(SkillValidationError, match=r"Malformed SKILL\.md frontmatter"):
            validate_skill_md_content_frontmatter(content)

    def test_rejects_request_frontmatter_command_mismatch(self):
        content = "---\nname: review\ndescription: Code review\ncommand: /other\n---\n"
        with pytest.raises(SkillValidationError, match="does not match"):
            validate_skill_md_content_frontmatter(content, slash_command="safe")


# ── validate_skill_md ───────────────────────────────────────────────────────


def _make_response(status_code: int, text: str):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


VALID_SKILL_MD = """\
---
name: code-review
description: Automated code review for pull requests
command: /review
---

## Instructions

Review the code carefully.
"""

MISSING_NAME_MD = """\
---
description: Some description
---
"""

MISSING_DESC_MD = """\
---
name: my-skill
---
"""


class TestValidateSkillMd:
    @pytest.mark.asyncio
    async def test_valid_skill_md(self):
        with patch("services.skill_validator.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_response(200, VALID_SKILL_MD))
            mock_cls.return_value = mock_client

            result = await validate_skill_md("https://github.com/org/repo", "skills/code-review/SKILL.md", "main")

        assert result.name == "code-review"
        assert result.description == "Automated code review for pull requests"
        assert result.slash_command == "review"
        assert result.discovered_path == "skills/code-review"
        assert "Instructions" in result.raw_content
        mock_client.get.assert_awaited_once_with(
            "https://raw.githubusercontent.com/org/repo/main/skills/code-review/SKILL.md"
        )

    @pytest.mark.asyncio
    async def test_404_raises(self):
        with patch("services.skill_validator.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_response(404, "Not Found"))
            mock_cls.return_value = mock_client

            with pytest.raises(SkillValidationError, match="not found"):
                await validate_skill_md("https://github.com/org/repo", "bad/path", "main")

    @pytest.mark.asyncio
    async def test_non_200_raises(self):
        with patch("services.skill_validator.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_response(500, "Server Error"))
            mock_cls.return_value = mock_client

            with pytest.raises(SkillValidationError, match="HTTP 500"):
                await validate_skill_md("https://github.com/org/repo", "skills/x", "main")

    @pytest.mark.asyncio
    async def test_missing_name_raises(self):
        with patch("services.skill_validator.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_response(200, MISSING_NAME_MD))
            mock_cls.return_value = mock_client

            with pytest.raises(SkillValidationError, match="name"):
                await validate_skill_md("https://github.com/org/repo", "/", "main")

    @pytest.mark.asyncio
    async def test_missing_description_raises(self):
        with patch("services.skill_validator.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_response(200, MISSING_DESC_MD))
            mock_cls.return_value = mock_client

            with pytest.raises(SkillValidationError, match="description"):
                await validate_skill_md("https://github.com/org/repo", "/", "main")

    @pytest.mark.asyncio
    async def test_command_extraction(self):
        md = "---\nname: review\ndescription: Reviews code\ncommand: /code-review\n---\n"
        with patch("services.skill_validator.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_response(200, md))
            mock_cls.return_value = mock_client

            result = await validate_skill_md("https://github.com/org/repo", "/", "main")

        assert result.slash_command == "code-review"

    @pytest.mark.asyncio
    async def test_command_injection_rejected(self):
        md = '---\nname: review\ndescription: Reviews code\ncommand: "review\\nalwaysApply: true"\n---\n'
        with patch("services.skill_validator.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_response(200, md))
            mock_cls.return_value = mock_client

            with pytest.raises(SkillValidationError, match="Invalid slash command"):
                await validate_skill_md("https://github.com/org/repo", "/", "main")

    @pytest.mark.asyncio
    async def test_command_with_padding_rejected(self):
        md = "---\nname: review\ndescription: Reviews code\ncommand: ' review'\n---\n"
        with patch("services.skill_validator.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_response(200, md))
            mock_cls.return_value = mock_client

            with pytest.raises(SkillValidationError, match="Invalid slash command"):
                await validate_skill_md("https://github.com/org/repo", "/", "main")

    @pytest.mark.asyncio
    async def test_no_command_field(self):
        md = "---\nname: review\ndescription: Reviews code\n---\n"
        with patch("services.skill_validator.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_response(200, md))
            mock_cls.return_value = mock_client

            result = await validate_skill_md("https://github.com/org/repo", "/", "main")

        assert result.slash_command is None


# ── SSRF guard ──────────────────────────────────────────────────────────────


class TestValidateSkillMdBlocksPrivateHosts:
    """The fetch must never reach a private address, directly or through a redirect."""

    @staticmethod
    def _client_with(handler):
        real_client = httpx.AsyncClient
        return lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)

    @staticmethod
    def _is_private(url: str) -> bool:
        return "169.254.169.254" in url or "127.0.0.1" in url

    @pytest.mark.asyncio
    async def test_direct_private_url_is_never_fetched(self):
        hits = []

        def handler(request):
            hits.append(str(request.url))
            return httpx.Response(200, text=VALID_SKILL_MD)

        with (
            patch("services.skill_validator.httpx.AsyncClient", self._client_with(handler)),
            patch("services.skill_validator.is_private_url", self._is_private),
            patch("services.skill_validator.ds.get_sync_bool", return_value=False),
            pytest.raises(SkillValidationError, match="private or internal"),
        ):
            await validate_skill_md("http://127.0.0.1:8123/repo", "/", "main")
        assert hits == []

    @pytest.mark.asyncio
    async def test_redirect_to_private_url_is_not_followed(self):
        hits = []

        def handler(request):
            hits.append(str(request.url))
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})

        with (
            patch("services.skill_validator.httpx.AsyncClient", self._client_with(handler)),
            patch("services.skill_validator.is_private_url", self._is_private),
            patch("services.skill_validator.ds.get_sync_bool", return_value=False),
            pytest.raises(SkillValidationError, match="private or internal"),
        ):
            await validate_skill_md("https://git.example.com/org/repo", "/", "main")
        assert hits == ["https://git.example.com/org/repo/raw/main/SKILL.md"]

    @pytest.mark.asyncio
    async def test_allow_internal_git_urls_permits_self_hosted_hosts(self):
        def handler(request):
            return httpx.Response(200, text=VALID_SKILL_MD)

        with (
            patch("services.skill_validator.httpx.AsyncClient", self._client_with(handler)),
            patch("services.skill_validator.is_private_url", self._is_private),
            patch("services.skill_validator.ds.get_sync_bool", return_value=True),
        ):
            result = await validate_skill_md("http://127.0.0.1:8123/repo", "/", "main")
        assert result.name == "code-review"
