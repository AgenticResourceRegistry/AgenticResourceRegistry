# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Standalone delivery health must be validated independently of ownership."""

import json

import pytest

from observal_cli.harness import ensure_loaded, get_adapter
from observal_cli.harness.protocol import SessionSource
from observal_cli.harness_specs.kiro_hooks_spec import build_kiro_hooks_file


@pytest.mark.parametrize("problem", ["version", "ownership_only", "prompt_only", "empty", "wrong_type", "disabled"])
def test_incomplete_standalone_cannot_disable_inline_stop(problem, tmp_path, monkeypatch):
    """Standalone ownership is not proof of runnable Stop coverage or complete doctor health."""
    config = build_kiro_hooks_file()
    if problem == "version":
        config["version"] = "invalid"
    elif problem == "ownership_only":
        config["hooks"] = [{"name": "observal-session-push-stop", "trigger": "Stop"}]
    elif problem == "prompt_only":
        config["hooks"] = config["hooks"][:1]
    elif problem == "empty":
        config["hooks"][-1]["action"]["command"] = ""
    elif problem == "wrong_type":
        config["hooks"][-1]["action"]["type"] = "http"
    else:
        config["hooks"][-1]["enabled"] = False
    hooks = tmp_path / ".kiro/hooks"
    hooks.mkdir(parents=True)
    (hooks / "observal.json").write_text(json.dumps(config))
    path = tmp_path / "messages.jsonl"
    path.write_text("{}\n")
    ensure_loaded()
    adapter = get_adapter("kiro")
    monkeypatch.setenv("OBSERVAL_KIRO_ENGINE", "v3")
    source = SessionSource("kiro", "session", path, str(tmp_path))
    assert adapter.accepts_session_hook(source, {"hook_event_name": "Stop"}, "inline", tmp_path)
    assert adapter.detect_hooks(tmp_path / ".kiro") != "installed"


def test_standalone_selects_only_covered_event(tmp_path):
    """A valid prompt-only standalone can handle Prompt, but inline Stop remains active."""
    config = build_kiro_hooks_file()
    config["hooks"] = config["hooks"][:1]
    hooks = tmp_path / ".kiro/hooks"
    hooks.mkdir(parents=True)
    (hooks / "observal.json").write_text(json.dumps(config))
    ensure_loaded()
    adapter = get_adapter("kiro")
    source = SessionSource("kiro", "session", tmp_path / "messages.jsonl", str(tmp_path))
    assert not adapter.accepts_session_hook(source, {"hook_event_name": "UserPromptSubmit"}, "inline", tmp_path)
    assert adapter.accepts_session_hook(source, {"hook_event_name": "Stop"}, "inline", tmp_path)
    assert adapter.accepts_session_hook(source, {}, "inline", tmp_path)
