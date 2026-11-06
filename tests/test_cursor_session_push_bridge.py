# SPDX-FileCopyrightText: 2026 Hussain Alsaibai <hussain.alsaibai@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Cursor session-push bridge tests.

The bridge exists because Cursor treats ``preToolUse`` as a permission hook:
it parses the hook's stdout and blocks the tool call when the output is not
valid permission JSON. These tests pin that contract.
"""

from __future__ import annotations

import json

from observal_cli.hooks import cursor_session_push


def test_allow_response_is_valid_permission_json():
    payload = json.loads(cursor_session_push._allow_response())

    assert payload == {"continue": True, "permission": "allow"}


def test_allow_response_never_denies():
    payload = json.loads(cursor_session_push._allow_response())

    assert payload["permission"] != "deny"


def test_bridge_main_prints_the_permission_response(monkeypatch, capsys):
    called: dict[str, object] = {}

    def fake_shared_main(home=None, harness="claude-code"):
        called["home"] = home
        called["harness"] = harness

    monkeypatch.setattr(cursor_session_push, "_shared_main", fake_shared_main)

    cursor_session_push.main()

    out = capsys.readouterr().out.strip()
    assert json.loads(out) == {"continue": True, "permission": "allow"}
    assert called["harness"] == "cursor"


def test_bridge_main_prints_the_response_even_when_delivery_raises(monkeypatch, capsys):
    def boom(home=None, harness="claude-code"):
        raise RuntimeError("delivery exploded")

    monkeypatch.setattr(cursor_session_push, "_shared_main", boom)

    try:
        cursor_session_push.main()
    except RuntimeError:
        pass

    out = capsys.readouterr().out.strip()
    assert json.loads(out) == {"continue": True, "permission": "allow"}
