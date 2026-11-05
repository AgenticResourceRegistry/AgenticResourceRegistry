# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""quick_choice reads single keys on Windows without termios."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from observal_cli import prompts


def _windows_console(monkeypatch, keys: str) -> None:
    pending = iter(keys)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(getwch=lambda: next(pending)))
    # termios does not exist on Windows; importing it must not be attempted.
    monkeypatch.setitem(sys.modules, "termios", None)


def test_quick_choice_reads_keypress_on_windows(monkeypatch):
    _windows_console(monkeypatch, "x92")

    assert prompts.quick_choice("SSO provider", ["1", "2"]) == "2"


def test_quick_choice_ctrl_c_on_windows(monkeypatch):
    _windows_console(monkeypatch, "\x03")

    with pytest.raises(KeyboardInterrupt):
        prompts.quick_choice("SSO provider", ["1", "2"])
