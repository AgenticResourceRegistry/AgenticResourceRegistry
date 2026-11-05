# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Every Agent has a model: a missing one is "auto" (each harness's default), set at creation."""

from __future__ import annotations

import pytest

from observal_shared.agent_model import AUTO_MODEL, is_auto_model, normalize_model_name


@pytest.mark.parametrize("value", [None, "", "   ", 3])
def test_missing_model_normalizes_to_auto(value):
    assert normalize_model_name(value) == AUTO_MODEL
    assert is_auto_model(value)


def test_explicit_model_is_kept():
    assert normalize_model_name(" gpt-4o ") == "gpt-4o"
    assert not is_auto_model("gpt-4o")
    assert is_auto_model("AUTO")


def test_agent_requests_default_and_normalize_the_model():
    from schemas.agent import AgentCreateRequest, AgentUpdateRequest, AgentVersionCreateRequest
    from schemas.bulk import BulkAgentItem

    base = {"name": "helper", "version": "1.0.0", "owner": "o", "prompt": "p"}
    assert AgentCreateRequest(**base).model_name == AUTO_MODEL
    assert AgentCreateRequest(**base, model_name="  ").model_name == AUTO_MODEL
    assert AgentCreateRequest(**base, model_name="gpt-4o").model_name == "gpt-4o"
    assert AgentVersionCreateRequest(version="1.0.1").model_name == AUTO_MODEL
    assert BulkAgentItem(name="helper").model_name == AUTO_MODEL
    # An edit that omits the model keeps it; an explicit blank resets it to auto.
    assert AgentUpdateRequest().model_name is None
    assert AgentUpdateRequest(model_name="").model_name == AUTO_MODEL


def test_agent_schema_check_does_not_require_a_model():
    from types import SimpleNamespace

    from services.review.checks import snapshot_checks

    version = SimpleNamespace(
        description="d", prompt="p", model_name="", gaming_flags=None, __table__=SimpleNamespace(columns=[])
    )
    schema = next(c for c in snapshot_checks({}, version, subject_type="agent") if c["id"] == "schema")
    assert schema["status"] == "pass"


@pytest.mark.asyncio
async def test_auto_model_uses_each_harness_default():
    from services.harness.helpers import _model_name_to_frontmatter
    from services.model_resolver import resolve_model_for_harness

    assert _model_name_to_frontmatter(AUTO_MODEL) == ""
    emitted, warnings = await resolve_model_for_harness("claude-code", model_name=AUTO_MODEL)
    assert emitted is None and warnings == []
    # A per-harness override still wins over the auto default.
    emitted, _ = await resolve_model_for_harness(
        "kiro", model_name=AUTO_MODEL, models_by_harness={"kiro": "claude-sonnet-4-6"}
    )
    assert emitted == "claude-sonnet-4.6"


def test_cli_claude_code_treats_auto_as_no_saved_model():
    from observal_cli.harness import ensure_loaded, get_adapter

    ensure_loaded()
    adapter = get_adapter("claude-code")
    assert adapter.saved_model({"model_name": AUTO_MODEL, "models_by_harness": {}}) is None
    assert adapter.saved_model({"model_name": "claude-opus-4", "models_by_harness": {}}) == "claude-opus-4"
