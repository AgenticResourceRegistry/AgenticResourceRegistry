# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Saved drafts cannot bypass the same readiness gates as direct submissions."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from api.routes._draft_submission import require_submittable_draft
from schemas.hook import HookSubmitRequest
from schemas.prompt import PromptSubmitRequest
from schemas.sandbox import SandboxSubmitRequest

COMMON = {"name": "example", "version": "1.0.0", "description": "Ready for review", "owner": "alice"}


@pytest.mark.parametrize(
    ("schema", "body", "invalid", "expected"),
    [
        (
            HookSubmitRequest,
            {"event": "PreToolUse", "handler_type": "command", "handler_config": {"command": "echo ok"}},
            {"handler_config": {}},
            "command handler requires",
        ),
        (
            HookSubmitRequest,
            {"event": "PreToolUse", "handler_type": "http", "handler_config": {"url": "https://example.org/handler"}},
            {"handler_config": {"url": "file:///etc/passwd"}},
            "http handler URL",
        ),
        (
            PromptSubmitRequest,
            {"category": "general", "template": "Review {source}"},
            {"template": "   "},
            "template is required",
        ),
        (
            PromptSubmitRequest,
            {"category": "general", "template": "Review {source}"},
            {"category": "not-a-category"},
            "Valid options",
        ),
        (SandboxSubmitRequest, {"runtime_type": "docker", "image": "python:3.12"}, {"image": ""}, "image is required"),
        (
            SandboxSubmitRequest,
            {"runtime_type": "firecracker", "image": "", "runtime_config": {"config_path": "vm.json"}},
            {"runtime_config": {}},
            "firecracker sandboxes require",
        ),
        (
            SandboxSubmitRequest,
            {"runtime_type": "wasm", "image": "", "runtime_config": {"module": "artifact.wasm"}},
            {"runtime_config": {}},
            "wasm sandboxes require",
        ),
    ],
)
def test_direct_and_saved_draft_submission_share_required_fields(schema, body, invalid, expected):
    valid = {**COMMON, **body}
    schema.model_validate(valid)
    require_submittable_draft(schema, SimpleNamespace(**valid))

    incomplete = {**valid, **invalid}
    with pytest.raises(ValidationError, match=expected):
        schema.model_validate(incomplete)
    with pytest.raises(HTTPException, match=expected) as exc:
        require_submittable_draft(schema, SimpleNamespace(**incomplete))
    assert exc.value.status_code == 400
    assert "input_value" not in str(exc.value.detail)


def test_folderless_wasm_or_firecracker_does_not_require_docker_image():
    for runtime, cfg in [("wasm", {"module": "module.wasm"}), ("firecracker", {"config_path": "vm.json"})]:
        require_submittable_draft(
            SandboxSubmitRequest, SimpleNamespace(**COMMON, runtime_type=runtime, image="", runtime_config=cfg)
        )
