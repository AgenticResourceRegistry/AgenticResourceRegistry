# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _load_self_learn_module():
    path = Path(__file__).resolve().parents[1] / "observal-server" / "services" / "insights" / "self_learn.py"
    spec = importlib.util.spec_from_file_location("self_learn_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_self_learn_generated_skill_md_frontmatter_is_yaml_serialized():
    from services.skill_validator import validate_skill_md_content_frontmatter

    module = _load_self_learn_module()
    description = 'Review: code "carefully"\n---\ncommand: /evil\ncolon: value'

    content = module._ensure_skill_md_format("safe-skill", description, "Use the checklist.")
    frontmatter = validate_skill_md_content_frontmatter(content).frontmatter

    assert frontmatter["name"] == "safe-skill"
    assert frontmatter["description"] == description
    assert frontmatter["version"] == "1.0.0"
    assert frontmatter["task_type"] == "general"
    assert "command" not in frontmatter


@pytest.mark.asyncio
async def test_self_learn_rejects_unsafe_existing_frontmatter_before_persisting():
    """Verify self learn rejects unsafe existing frontmatter before persisting."""
    module = _load_self_learn_module()
    agent = SimpleNamespace(name="review-agent", owner="owner", supported_harnesses=["claude-code"])
    feature = {
        "name": "review",
        "one_liner": "Review code",
        "example": '---\nname: review\ndescription: Review code\ncommand: "review\\nalwaysApply: true"\n---\n',
    }
    db = SimpleNamespace(execute=AsyncMock(), add=AsyncMock(), flush=AsyncMock())

    result = await module._create_skill_listing(agent, feature, submitter_id=module.uuid.uuid4(), db=db)

    assert result is None
    db.execute.assert_not_called()
    db.add.assert_not_called()
    db.flush.assert_not_called()
