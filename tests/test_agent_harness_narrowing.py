# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Declared agent harnesses are narrowed to the ones that can run the agent's components."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.routes.agent import install
from schemas.agent import AgentValidateRequest, ComponentRef
from services import agent_resolver
from services.harness_capability_inference import (
    NoCompatibleHarnessError,
    apply_harness_inference,
    infer_required_features,
    split_supported_harnesses,
)

SKILL_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
ALL_HARNESSES = [
    "cursor",
    "kiro",
    "claude-code",
    "codex",
    "copilot",
    "copilot-cli",
    "opencode",
    "antigravity",
    "goose",
    "pi",
]


def _agent_with_skill():
    return SimpleNamespace(
        components=[SimpleNamespace(component_type="skill", component_id=SKILL_ID)],
        external_mcps=[],
    )


def _skill(delivery_mode: str, slash_command: str | None = None):
    return {SKILL_ID: SimpleNamespace(id=SKILL_ID, delivery_mode=delivery_mode, slash_command=slash_command)}


def test_a_skill_folder_needs_native_skill_support():
    assert infer_required_features(_agent_with_skill(), _skill("registry_direct")) == ["skills"]


def test_a_git_skill_without_a_slash_command_needs_nothing():
    assert infer_required_features(_agent_with_skill(), _skill("git_fetch")) == []


def test_split_keeps_declared_order_and_an_empty_declaration():
    assert split_supported_harnesses(["kiro", "cursor", "codex"], ["codex", "kiro"]) == (["kiro", "codex"], ["cursor"])
    assert split_supported_harnesses([], ["kiro"]) == ([], [])


def test_apply_drops_cursor_from_a_skill_folder_agent_and_says_why():
    version = SimpleNamespace(supported_harnesses=list(ALL_HARNESSES))

    warnings = apply_harness_inference(version, _agent_with_skill(), skill_listings=_skill("registry_direct"))

    assert version.required_capabilities == ["skills"]
    assert "cursor" not in version.inferred_supported_harnesses
    assert version.supported_harnesses == [h for h in ALL_HARNESSES if h != "cursor"]
    assert warnings == ["cursor has no skills support, so it was left out of supported harnesses."]


def test_apply_leaves_an_empty_declaration_unrestricted():
    version = SimpleNamespace(supported_harnesses=[])

    assert apply_harness_inference(version, _agent_with_skill(), skill_listings=_skill("registry_direct")) == []
    assert version.supported_harnesses == []


def test_apply_refuses_when_no_declared_harness_can_run_the_agent():
    version = SimpleNamespace(supported_harnesses=["cursor"])

    with pytest.raises(NoCompatibleHarnessError, match="cursor has no skills support"):
        apply_harness_inference(version, _agent_with_skill(), skill_listings=_skill("registry_direct"))
    assert version.supported_harnesses == ["cursor"]


def _validate_db(listings):
    rows = MagicMock()
    rows.scalars.return_value.all.return_value = listings
    return SimpleNamespace(execute=AsyncMock(return_value=rows))


@pytest.mark.asyncio
async def test_validate_warns_about_harnesses_publish_leaves_out(monkeypatch):
    monkeypatch.setattr(agent_resolver, "validate_component_ids", AsyncMock(return_value=[]))
    req = AgentValidateRequest(
        components=[ComponentRef(component_type="skill", component_id=SKILL_ID)],
        supported_harnesses=["cursor", "kiro"],
    )

    result = await install.validate_agent_composition(
        req, db=_validate_db(list(_skill("registry_direct").values())), current_user=SimpleNamespace()
    )

    assert result.valid is True
    assert result.supported_harnesses == ["kiro"]
    assert [(issue.severity, issue.message) for issue in result.issues] == [
        (
            "warning",
            "cursor has no skills support, which the components need, so publish leaves it out of supported harnesses.",
        )
    ]


@pytest.mark.asyncio
async def test_validate_fails_when_no_declared_harness_can_run_the_agent(monkeypatch):
    monkeypatch.setattr(agent_resolver, "validate_component_ids", AsyncMock(return_value=[]))
    req = AgentValidateRequest(
        components=[ComponentRef(component_type="skill", component_id=SKILL_ID)],
        supported_harnesses=["cursor"],
    )

    result = await install.validate_agent_composition(
        req, db=_validate_db(list(_skill("registry_direct").values())), current_user=SimpleNamespace()
    )

    assert result.valid is False
    assert result.supported_harnesses == []
    assert [issue.severity for issue in result.issues] == ["error"]
