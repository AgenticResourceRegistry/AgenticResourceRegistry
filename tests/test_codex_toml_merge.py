# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Adversarial Codex MCP TOML pull regressions (dummy secrets only)."""

import builtins
import json
import sys
import tomllib
from pathlib import Path
from threading import Event, Thread
from unittest.mock import MagicMock

import pytest

from observal_cli import cmd_pull
from observal_cli.errors import CliError, ErrorCategory, emit_error


@pytest.mark.parametrize(
    "source",
    [
        '[mcp_servers.demo]\ncommand = "old"\n[mcp_servers.demo.env]\nTOKEN = "old"\n',
        '[mcp_servers.demo]\ncommand = "old"\nenv.TOKEN = "old"\n',
        '[mcp_servers.demo.env]\nTOKEN = "old"\n',
        'mcp_servers.demo = {command = "old", env = {TOKEN = "old"}}\n',
        '[mcp_servers."demo"] # user note\nenv = {TOKEN = "old"}\n',
    ],
)
def test_nested_env_replacement_removes_stale_secret_and_repeats(tmp_path, source):
    path = tmp_path / "config.toml"
    path.write_text(source)
    incoming = {"mcp_servers": {"demo": {"command": "new", "env": {"TOKEN": "new"}}}}
    assert cmd_pull._write_file(path, incoming, merge_mcp=True) == "merged"
    assert tomllib.loads(path.read_text()) == incoming
    assert "old" not in path.read_text()
    cmd_pull._write_file(path, incoming, merge_mcp=True)
    assert tomllib.loads(path.read_text()) == incoming
    cmd_pull._write_file(path, {"mcp_servers": {"demo": {"command": "bare"}}}, merge_mcp=True)
    assert tomllib.loads(path.read_text()) == {"mcp_servers": {"demo": {"command": "bare"}}}
    assert "TOKEN" not in path.read_text()


def test_literal_keys_fresh_and_merge_preserves_unrelated_data(tmp_path):
    path = tmp_path / "config.toml"
    name = 'foo.bar "\\ 雪'
    payload = {
        "mcp_servers": {
            name: {
                "command": "ok",
                'field.with "\\ 雪': ["literal"],
                "env": {'X.Trace "\\ 雪': "dummy"},
                "headers": {"a.b": "x", "obsolete": "dummy-old"},
            }
        }
    }
    assert cmd_pull._write_file(path, payload, merge_mcp=True) == "created"
    assert tomllib.loads(path.read_text()) == payload
    path.write_text(
        '# leading\nmodel = "gpt" # owned by user\n'
        '[mcp_servers.other] # untouched\ncommand = "mine"\n'
        "[unrelated] # trailing\nvalue = nan\n" + path.read_text()
    )
    updated = {"mcp_servers": {name: {"command": "changed", "headers": {"a.b": "new"}}}}
    assert cmd_pull._write_file(path, updated, merge_mcp=True) == "merged"
    data = tomllib.loads(path.read_text())
    assert data["mcp_servers"][name] == updated["mcp_servers"][name]
    assert "obsolete" not in path.read_text()
    assert data["mcp_servers"]["other"] == {"command": "mine"}
    assert data["model"] == "gpt"
    for comment in ("# leading", "# owned by user", "# untouched", "# trailing"):
        assert comment in path.read_text()


def test_inline_root_repull_replaces_only_target(tmp_path):
    path = tmp_path / "config.toml"
    source = (
        '# user settings\nmodel = "gpt"\n'
        'mcp_servers = {other = {command = "mine"}, demo = {command = "old", env = {TOKEN = "stale"}}} # keep\n'
        "[unrelated]\nvalue = 42\n"
    )
    path.write_text(source)
    incoming = {"mcp_servers": {"demo": {"command": "new", "env": {"TOKEN": "fresh"}}}}
    assert cmd_pull._write_file(path, incoming, merge_mcp=True) == "merged"
    result = path.read_text()
    assert tomllib.loads(result) == {
        "model": "gpt",
        "mcp_servers": {"other": {"command": "mine"}, **incoming["mcp_servers"]},
        "unrelated": {"value": 42},
    }
    assert "# keep" in result and "# user settings" in result
    assert "stale" not in result
    assert cmd_pull._write_file(path, incoming, merge_mcp=True) == "merged"
    assert path.read_text() == result


@pytest.mark.parametrize(
    "target",
    ['[mcp_servers.demo]\ncommand = "old"\n\n', '[mcp_servers.demo.env]\nTOKEN = "stale"\n'],
)
def test_comment_preceding_unrelated_server_survives_target_replacement(tmp_path, target):
    path = tmp_path / "config.toml"
    path.write_text(target + '# belongs to other\n[mcp_servers.other]\ncommand = "mine"\n')
    cmd_pull._write_file(path, {"mcp_servers": {"demo": {"command": "new"}}}, merge_mcp=True)
    result = path.read_text()
    assert "# belongs to other" in result
    assert result.index("# belongs to other") < result.index("[mcp_servers.other]")
    assert tomllib.loads(result) == {"mcp_servers": {"demo": {"command": "new"}, "other": {"command": "mine"}}}


@pytest.mark.parametrize(
    "target",
    ['[mcp_servers.demo]\ncommand = "old"\n', '[mcp_servers.demo.env]\nTOKEN = "old"\n'],
)
def test_last_server_replacement_keeps_unrelated_section_comment(tmp_path, target):
    path = tmp_path / "config.toml"
    path.write_text(target + "\n# belongs to unrelated\n[unrelated]\nvalue = 1\n")
    incoming = {"mcp_servers": {"demo": {"command": "new"}}}
    assert cmd_pull._write_file(path, incoming, merge_mcp=True) == "merged"
    result = path.read_text()
    assert "# belongs to unrelated\n[unrelated]" in result
    assert tomllib.loads(result) == {**incoming, "unrelated": {"value": 1}}
    assert cmd_pull._write_file(path, incoming, merge_mcp=True) == "merged"
    assert path.read_text() == result


def test_new_server_in_dotted_key_config_keeps_top_level_model(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('mcp_servers.demo.command = "old"\nmodel = "gpt"\n')
    incoming = {"mcp_servers": {"extra": {"command": "new"}}}
    assert cmd_pull._write_file(path, incoming, merge_mcp=True) == "merged"
    result = path.read_text()
    assert tomllib.loads(result) == {
        "mcp_servers": {"demo": {"command": "old"}, "extra": {"command": "new"}},
        "model": "gpt",
    }
    assert cmd_pull._write_file(path, incoming, merge_mcp=True) == "merged"
    assert path.read_text() == result


def test_windows_lock_imports_msvcrt_without_fcntl(tmp_path, monkeypatch):
    real_import = builtins.__import__
    windows = MagicMock(LK_LOCK=1, LK_UNLCK=2)

    def import_without_fcntl(name, *args, **kwargs):
        if name == "fcntl":
            raise AssertionError("POSIX fcntl must not be imported on Windows")
        if name == "msvcrt":
            return windows
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(builtins, "__import__", import_without_fcntl)
    path = tmp_path / "config.toml"
    with cmd_pull._toml_write_lock(path):
        assert windows.locking.call_args.args[1:] == (windows.LK_LOCK, 1)
        assert (tmp_path / ".config.toml.observal.lock").read_bytes() == b"\0"
    assert [call.args[1:] for call in windows.locking.call_args_list] == [(windows.LK_LOCK, 1), (windows.LK_UNLCK, 1)]


def test_empty_server_map_is_valid_and_noop(tmp_path):
    path = tmp_path / "config.toml"
    incoming = {"mcp_servers": {}}
    assert cmd_pull._write_file(path, incoming, merge_mcp=True) == "created"
    assert tomllib.loads(path.read_text()) == incoming
    source = b'# user settings\nmodel = "gpt"\n[mcp_servers.mine]\ncommand = "mine"\n'
    path.write_bytes(source)
    assert cmd_pull._write_file(path, incoming, merge_mcp=True) == "merged"
    assert path.read_bytes() == source
    path.write_text('mcp_servers = "not a table"\n')
    with pytest.raises(CliError) as exc:
        cmd_pull._write_file_checked(path, incoming, merge_mcp=True)
    assert exc.value.category == ErrorCategory.CONFLICT
    assert path.read_text() == 'mcp_servers = "not a table"\n'


def test_snippet_codex_path_and_failure_tracking(tmp_path):
    path = tmp_path / ".codex" / "config.toml"
    path.parent.mkdir()
    path.write_text('[mcp_servers.demo.env]\nTOKEN = "stale"\n')
    snippet = {"mcp_config": {"path": ".codex/config.toml", "content": {"mcp_servers": {"demo": {"command": "echo"}}}}}
    args = dict(harness="codex", adapter=MagicMock(), target_dir=tmp_path, agent_id="dummy", is_user_scope=False)
    written, failed = cmd_pull.write_install_snippet(snippet, **args)
    assert written == [(str(path), "merged")]
    assert not failed
    assert tomllib.loads(path.read_text()) == snippet["mcp_config"]["content"]
    snippet["mcp_config"]["content"] = {"mcp_servers": {}}
    written, failed = cmd_pull.write_install_snippet(snippet, **args)
    assert written == [(str(path), "merged")]
    assert tomllib.loads(path.read_text()) == {"mcp_servers": {"demo": {"command": "echo"}}}
    path.write_text('mcp_servers = "invalid"\n')
    with pytest.raises(CliError) as exc:
        cmd_pull.write_install_snippet(snippet, **args)
    assert exc.value.result["files"] == []
    assert exc.value.result["failed_path"] == str(path)
    assert exc.value.result["partial"] is False


@pytest.mark.parametrize(
    "source", ['[mcp_servers.demo]\nenv = "dummy-sentinel"\n[bad\n', 'mcp_servers = "dummy-sentinel"\n']
)
def test_invalid_source_is_secret_safe_and_unchanged(tmp_path, capsys, source):
    path = tmp_path / "config.toml"
    path.write_text(source)
    with pytest.raises(CliError) as exc:
        cmd_pull._write_file_checked(path, {"mcp_servers": {"demo": {"command": "ok"}}}, merge_mcp=True)
    assert exc.value.category == ErrorCategory.CONFLICT
    emit_error(exc.value, json_mode=True, debug=True)
    output = capsys.readouterr()
    assert "dummy-sentinel" not in output.err + output.out
    assert json.loads(output.err)["error"]["category"] == "conflict"
    assert path.read_text() == source


@pytest.mark.parametrize("value", [None, object(), {1: "dummy-sentinel"}, {"key": None}])
def test_unsupported_values_leave_original_untouched(tmp_path, value):
    path = tmp_path / "config.toml"
    source = b'[mcp_servers.demo]\ncommand = "original"\n'
    path.write_bytes(source)
    with pytest.raises(CliError) as exc:
        cmd_pull._write_file_checked(path, {"mcp_servers": {"demo": {"env": value}}}, merge_mcp=True)
    assert exc.value.category == ErrorCategory.CONFLICT
    assert exc.value.detail is None
    assert path.read_bytes() == source


@pytest.mark.parametrize("initial", [None, b'[mcp_servers.demo]\ncommand = "old"\n'])
def test_concurrent_edit_or_creation_is_detected(tmp_path, monkeypatch, initial):
    path = tmp_path / "config.toml"
    if initial is not None:
        path.write_bytes(initial)
    external = b"# changed by editor\n"
    render = cmd_pull._toml_document

    def race(*args, **kwargs):
        result = render(*args, **kwargs)
        path.write_bytes(external)
        return result

    monkeypatch.setattr(cmd_pull, "_toml_document", race)
    with pytest.raises(CliError) as exc:
        cmd_pull._write_file_checked(path, {"mcp_servers": {"demo": {"command": "new"}}}, merge_mcp=True)
    assert exc.value.category == ErrorCategory.CONFLICT
    assert path.read_bytes() == external
    assert not [p for p in tmp_path.glob(".config.toml.*") if p.name != ".config.toml.observal.lock"]


def test_cooperating_writers_wait_on_same_lock(tmp_path):
    path = tmp_path / "config.toml"
    started, finished = Event(), Event()

    def writer():
        started.set()
        cmd_pull._write_file(path, {"mcp_servers": {"demo": {"command": "ok"}}}, merge_mcp=True)
        finished.set()

    with cmd_pull._toml_write_lock(path):
        thread = Thread(target=writer)
        thread.start()
        assert started.wait(2)
        assert not finished.wait(0.1)
    thread.join(timeout=3)
    assert finished.is_set()
    assert tomllib.loads(path.read_text()) == {"mcp_servers": {"demo": {"command": "ok"}}}


def test_replace_failure_keeps_original_and_removes_temp(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    original = b'[mcp_servers.demo]\ncommand = "old"\n'
    path.write_bytes(original)
    replace = Path.replace

    def unavailable(self, target):
        if target == path:
            raise OSError("dummy-sentinel")
        return replace(self, target)

    monkeypatch.setattr(Path, "replace", unavailable)
    with pytest.raises(CliError) as exc:
        cmd_pull._write_file_checked(path, {"mcp_servers": {"demo": {"command": "new"}}}, merge_mcp=True)
    assert exc.value.category == ErrorCategory.UNAVAILABLE
    assert exc.value.detail is None
    assert path.read_bytes() == original
    assert not [p for p in tmp_path.glob(".config.toml.*") if p.name != ".config.toml.observal.lock"]
