# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Adversarial Codex MCP TOML pull regressions (dummy secrets only)."""

import builtins
import json
import os
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
    assert tomllib.loads(path.read_text(encoding="utf-8")) == payload
    path.write_text(
        '# leading\nmodel = "gpt" # owned by user\n'
        '[mcp_servers.other] # untouched\ncommand = "mine"\n'
        "[unrelated] # trailing\nvalue = nan\n" + path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    updated = {"mcp_servers": {name: {"command": "changed", "headers": {"a.b": "new"}}}}
    assert cmd_pull._write_file(path, updated, merge_mcp=True) == "merged"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["mcp_servers"][name] == updated["mcp_servers"][name]
    assert "obsolete" not in path.read_text(encoding="utf-8")
    assert data["mcp_servers"]["other"] == {"command": "mine"}
    assert data["model"] == "gpt"
    for comment in ("# leading", "# owned by user", "# untouched", "# trailing"):
        assert comment in path.read_text(encoding="utf-8")


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


@pytest.mark.parametrize("preexisting", [False, True])
def test_windows_lock_imports_msvcrt_without_fcntl(tmp_path, monkeypatch, preexisting):
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
    if preexisting:
        (tmp_path / ".config.toml.observal.lock").write_bytes(b"")
    with cmd_pull._toml_write_lock(path):
        assert windows.locking.call_args.args[1:] == (windows.LK_LOCK, 1)
        assert (tmp_path / ".config.toml.observal.lock").read_bytes() == b"\0"
    assert [call.args[1:] for call in windows.locking.call_args_list] == [(windows.LK_LOCK, 1), (windows.LK_UNLCK, 1)]


def test_windows_lock_symlink_is_rejected_before_writing(tmp_path, monkeypatch):
    real_import = builtins.__import__
    windows = MagicMock(LK_LOCK=1, LK_UNLCK=2)

    def import_msvcrt(name, *args, **kwargs):
        return windows if name == "msvcrt" else real_import(name, *args, **kwargs)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(builtins, "__import__", import_msvcrt)
    config = tmp_path / "config.toml"
    config.write_bytes(b"")
    lock = tmp_path / ".config.toml.observal.lock"
    lock.symlink_to(config)
    with pytest.raises(CliError) as exc:
        cmd_pull._write_file_checked(config, {"mcp_servers": {"demo": {"command": "ok"}}}, merge_mcp=True)
    assert exc.value.category == ErrorCategory.CONFLICT
    assert config.read_bytes() == b""
    assert lock.is_symlink()
    windows.locking.assert_not_called()


def test_windows_lock_hardlink_is_rejected_before_writing(tmp_path, monkeypatch):
    real_import = builtins.__import__
    windows = MagicMock(LK_LOCK=1, LK_UNLCK=2)

    def import_msvcrt(name, *args, **kwargs):
        return windows if name == "msvcrt" else real_import(name, *args, **kwargs)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(builtins, "__import__", import_msvcrt)
    config = tmp_path / "config.toml"
    config.write_bytes(b"")
    lock = tmp_path / ".config.toml.observal.lock"
    os.link(config, lock)
    with pytest.raises(CliError) as exc:
        cmd_pull._write_file_checked(config, {"mcp_servers": {"demo": {"command": "ok"}}}, merge_mcp=True)
    assert exc.value.category == ErrorCategory.CONFLICT
    assert config.read_bytes() == lock.read_bytes() == b""
    windows.locking.assert_not_called()


def test_windows_lock_hardlink_swap_during_open_is_rejected(tmp_path, monkeypatch):
    real_import = builtins.__import__
    windows = MagicMock(LK_LOCK=1, LK_UNLCK=2)

    def import_msvcrt(name, *args, **kwargs):
        return windows if name == "msvcrt" else real_import(name, *args, **kwargs)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(builtins, "__import__", import_msvcrt)
    config = tmp_path / "config.toml"
    config.write_bytes(b"")
    lock = tmp_path / ".config.toml.observal.lock"
    lock.write_bytes(b"")
    original_open = os.open

    def swapped_open(path, *args, **kwargs):
        if path == lock:
            lock.unlink()
            os.link(config, lock)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapped_open)
    with pytest.raises(CliError) as exc:
        cmd_pull._write_file_checked(config, {"mcp_servers": {"demo": {"command": "ok"}}}, merge_mcp=True)
    assert exc.value.category == ErrorCategory.CONFLICT
    assert config.read_bytes() == lock.read_bytes() == b""
    windows.locking.assert_not_called()


def test_windows_lock_reparse_point_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    config = tmp_path / "config.toml"
    config.write_bytes(b"")
    lock = tmp_path / ".config.toml.observal.lock"
    lock.write_bytes(b"sentinel")
    lstat = Path.lstat

    def reparse(self, *args, **kwargs):
        result = lstat(self, *args, **kwargs)
        if self == lock:
            return MagicMock(st_mode=result.st_mode, st_file_attributes=0x400)
        return result

    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(CliError) as exc:
        cmd_pull._write_file_checked(config, {"mcp_servers": {"demo": {"command": "ok"}}}, merge_mcp=True)
    assert exc.value.category == ErrorCategory.CONFLICT
    assert lock.read_bytes() == b"sentinel"
    assert config.read_bytes() == b""


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


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory descriptor semantics")
def test_snippet_codex_parent_swap_after_validation_does_not_write_outside(tmp_path, monkeypatch):
    parent = tmp_path / ".codex"
    parent.mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-outside-codex"
    outside.mkdir()
    original = b'model = "outside"\n'
    (outside / "config.toml").write_bytes(original)
    snippet = {"mcp_config": {"path": ".codex/config.toml", "content": {"mcp_servers": {"demo": {"command": "ok"}}}}}
    resolve = cmd_pull._resolve_path

    def swap_after_validation(*args, **kwargs):
        result = resolve(*args, **kwargs)
        if args[0] == ".codex/config.toml":
            parent.rename(tmp_path / "moved-codex")
            parent.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(cmd_pull, "_resolve_path", swap_after_validation)
    with pytest.raises(CliError) as exc:
        cmd_pull.write_install_snippet(
            snippet, harness="codex", adapter=MagicMock(), target_dir=tmp_path, agent_id="dummy", is_user_scope=False
        )
    assert exc.value.category == ErrorCategory.CONFLICT
    assert exc.value.result["files"] == []
    assert exc.value.result["failed_path"] == str(tmp_path / ".codex" / "config.toml")
    assert (outside / "config.toml").read_bytes() == original
    assert not (outside / ".config.toml.observal.lock").exists()


@pytest.mark.parametrize("symlink", [False, True])
def test_snippet_codex_user_home_path_is_allowed(tmp_path, monkeypatch, symlink):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    parent = home / ".codex"
    parent.mkdir()
    requested = parent / "config.toml"
    actual = home / "actual.toml" if symlink else requested
    if symlink:
        requested.symlink_to(actual)
    original = b'model = "mine"\n'
    actual.write_bytes(original)
    snippet = {"mcp_config": {"path": "~/.codex/config.toml", "content": {"mcp_servers": {"demo": {"command": "ok"}}}}}
    written, failed = cmd_pull.write_install_snippet(
        snippet,
        harness="codex",
        adapter=MagicMock(),
        target_dir=tmp_path / "project",
        agent_id="dummy",
        is_user_scope=True,
    )
    assert written == [(str(actual), "merged")]
    assert failed == []
    assert tomllib.loads(actual.read_text(encoding="utf-8")) == {
        "model": "mine",
        "mcp_servers": {"demo": {"command": "ok"}},
    }
    if symlink:
        assert requested.is_symlink()


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory descriptor semantics")
@pytest.mark.parametrize("swap_parent", [False, True])
def test_snippet_codex_symlink_retarget_during_render_is_conflict(tmp_path, monkeypatch, swap_parent):
    parent = tmp_path / ".codex"
    parent.mkdir()
    link = parent / "config.toml"
    original = b'model = "mine"\n'
    old = tmp_path / "old.toml"
    new = tmp_path.parent / f"{tmp_path.name}-outside.toml"
    old.write_bytes(original)
    new.write_bytes(original)
    link.symlink_to(old)
    snippet = {"mcp_config": {"path": ".codex/config.toml", "content": {"mcp_servers": {"demo": {"command": "ok"}}}}}
    render = cmd_pull._toml_document

    def retarget(*args, **kwargs):
        result = render(*args, **kwargs)
        if swap_parent:
            parent.rename(tmp_path / "moved")
            parent.symlink_to(outside, target_is_directory=True)
        else:
            link.unlink()
            link.symlink_to(new)
        return result

    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    monkeypatch.setattr(cmd_pull, "_toml_document", retarget)
    with pytest.raises(CliError) as exc:
        cmd_pull.write_install_snippet(
            snippet, harness="codex", adapter=MagicMock(), target_dir=tmp_path, agent_id="dummy", is_user_scope=False
        )
    assert exc.value.category == ErrorCategory.CONFLICT
    assert exc.value.result["failed_path"] == str(old)
    assert old.read_bytes() == new.read_bytes() == original


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory descriptor semantics")
@pytest.mark.parametrize("after_replace", [False, True])
def test_snippet_codex_retarget_cannot_report_success(tmp_path, monkeypatch, after_replace):
    parent = tmp_path / ".codex"
    parent.mkdir()
    link = parent / "config.toml"
    old = tmp_path / "old.toml"
    new = tmp_path / "new.toml"
    original = b'[mcp_servers.demo]\ncommand = "ok"\n'
    old.write_bytes(original if not after_replace else b'model = "mine"\n')
    new.write_bytes(original)
    link.symlink_to(old)
    snippet = {"mcp_config": {"path": ".codex/config.toml", "content": {"mcp_servers": {"demo": {"command": "ok"}}}}}

    def retarget():
        link.unlink()
        link.symlink_to(new)

    if after_replace:
        replace = os.replace

        def swap_after_replace(*args, **kwargs):
            replace(*args, **kwargs)
            retarget()

        monkeypatch.setattr(os, "replace", swap_after_replace)
    else:
        render = cmd_pull._toml_document

        def swap_during_noop(*args, **kwargs):
            result = render(*args, **kwargs)
            retarget()
            return result

        monkeypatch.setattr(cmd_pull, "_toml_document", swap_during_noop)
    with pytest.raises(CliError) as exc:
        cmd_pull.write_install_snippet(
            snippet, harness="codex", adapter=MagicMock(), target_dir=tmp_path, agent_id="dummy", is_user_scope=False
        )
    assert exc.value.category == ErrorCategory.CONFLICT
    if after_replace:
        assert exc.value.result["partial"] is True
        identity = old.parent.stat()
        assert exc.value.result["files"] == [
            {
                "path": f"pinned directory (device={identity.st_dev}, inode={identity.st_ino}), filename='old.toml' (not a pathname)",
                "status": "merged",
            }
        ]
        assert tomllib.loads(old.read_text())["mcp_servers"]["demo"] == {"command": "ok"}
    else:
        assert exc.value.result["partial"] is False
        assert exc.value.result["files"] == []
    assert new.read_bytes() == original


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory descriptor semantics")
def test_snippet_codex_symlink_install_keeps_link_and_target(tmp_path):
    parent = tmp_path / ".codex"
    parent.mkdir()
    link = parent / "config.toml"
    actual = tmp_path / "actual.toml"
    actual.write_text('model = "mine"\n', encoding="utf-8")
    link.symlink_to("../actual.toml")
    snippet = {"mcp_config": {"path": ".codex/config.toml", "content": {"mcp_servers": {"demo": {"command": "ok"}}}}}
    written, failed = cmd_pull.write_install_snippet(
        snippet, harness="codex", adapter=MagicMock(), target_dir=tmp_path, agent_id="dummy", is_user_scope=False
    )
    assert written == [(str(actual), "merged")]
    assert failed == []
    assert link.is_symlink()
    assert tomllib.loads(actual.read_text(encoding="utf-8")) == {
        "model": "mine",
        "mcp_servers": {"demo": {"command": "ok"}},
    }


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


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory descriptor semantics")
def test_swapped_parent_during_render_does_not_write_outside_original_directory(tmp_path, monkeypatch):
    parent = tmp_path / "codex"
    parent.mkdir()
    path = parent / "config.toml"
    original = b'[mcp_servers.demo]\ncommand = "old"\n'
    path.write_bytes(original)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "config.toml").write_bytes(original)
    render = cmd_pull._toml_document

    def swap(*args, **kwargs):
        result = render(*args, **kwargs)
        parent.rename(tmp_path / "moved")
        parent.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(cmd_pull, "_toml_document", swap)
    with pytest.raises(CliError) as exc:
        cmd_pull._write_file_checked(path, {"mcp_servers": {"demo": {"command": "new"}}}, merge_mcp=True)
    assert exc.value.category == ErrorCategory.CONFLICT
    assert (outside / "config.toml").read_bytes() == original
    assert (tmp_path / "moved" / "config.toml").read_bytes() == original
    assert not [p for p in (tmp_path / "moved").glob(".config.toml.*") if p.name != ".config.toml.observal.lock"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory descriptor semantics")
def test_config_symlink_retarget_during_render_fails_without_writing_either_target(tmp_path, monkeypatch):
    parent = tmp_path / ".codex"
    parent.mkdir()
    path = parent / "config.toml"
    original = b'model = "mine"\n'
    old_target = tmp_path / "old.toml"
    new_target = tmp_path / "new.toml"
    old_target.write_bytes(original)
    new_target.write_bytes(original)
    path.symlink_to(old_target)
    render = cmd_pull._toml_document

    def swap(*args, **kwargs):
        result = render(*args, **kwargs)
        path.unlink()
        path.symlink_to(new_target)
        return result

    monkeypatch.setattr(cmd_pull, "_toml_document", swap)
    with pytest.raises(CliError) as exc:
        cmd_pull._write_file_checked(path, {"mcp_servers": {"demo": {"command": "new"}}}, merge_mcp=True)
    assert exc.value.category == ErrorCategory.CONFLICT
    assert path.resolve() == new_target
    assert old_target.read_bytes() == new_target.read_bytes() == original
    assert not [p for p in tmp_path.glob(".old.toml.*") if p.name != ".old.toml.observal.lock"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory descriptor semantics")
def test_noop_merge_retarget_during_render_does_not_report_success(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    original = b'[mcp_servers.demo]\ncommand = "new"\n'
    old_target = tmp_path / "old.toml"
    new_target = tmp_path / "new.toml"
    old_target.write_bytes(original)
    new_target.write_bytes(original)
    path.symlink_to(old_target)
    render = cmd_pull._toml_document

    def swap(*args, **kwargs):
        result = render(*args, **kwargs)
        path.unlink()
        path.symlink_to(new_target)
        return result

    monkeypatch.setattr(cmd_pull, "_toml_document", swap)
    with pytest.raises(CliError) as exc:
        cmd_pull._write_file_checked(path, {"mcp_servers": {"demo": {"command": "new"}}}, merge_mcp=True)
    assert exc.value.category == ErrorCategory.CONFLICT
    assert old_target.read_bytes() == new_target.read_bytes() == original


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory descriptor semantics")
def test_config_symlink_retarget_before_replace_cleans_temp(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    original = b'model = "mine"\n'
    old_target = tmp_path / "old.toml"
    new_target = tmp_path / "new.toml"
    old_target.write_bytes(original)
    new_target.write_bytes(original)
    path.symlink_to(old_target)
    read = cmd_pull._read_toml_bytes
    calls = 0

    def swap_after_cas(*args, **kwargs):
        nonlocal calls
        result = read(*args, **kwargs)
        calls += 1
        if calls == 2:
            path.unlink()
            path.symlink_to(new_target)
        return result

    monkeypatch.setattr(cmd_pull, "_read_toml_bytes", swap_after_cas)
    with pytest.raises(CliError) as exc:
        cmd_pull._write_file_checked(path, {"mcp_servers": {"demo": {"command": "new"}}}, merge_mcp=True)
    assert exc.value.category == ErrorCategory.CONFLICT
    assert old_target.read_bytes() == new_target.read_bytes() == original
    assert not [p for p in tmp_path.glob(".old.toml.*") if p.name != ".old.toml.observal.lock"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory descriptor semantics")
def test_symlinked_codex_config_preserves_link_and_target(tmp_path):
    actual = tmp_path / "actual.toml"
    actual.write_bytes(b'model = "mine"\n')
    parent = tmp_path / ".codex"
    parent.mkdir()
    link = parent / "config.toml"
    link.symlink_to("../actual.toml")
    assert cmd_pull._write_file(link, {"mcp_servers": {"demo": {"command": "echo"}}}, merge_mcp=True) == "merged"
    assert link.is_symlink()
    assert tomllib.loads(actual.read_text(encoding="utf-8")) == {
        "model": "mine",
        "mcp_servers": {"demo": {"command": "echo"}},
    }


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory descriptor semantics")
def test_swapped_symlinked_parent_does_not_write_new_target(tmp_path, monkeypatch):
    parent = tmp_path / ".codex"
    parent.mkdir()
    original = b'model = "mine"\n'
    real = tmp_path / "real.toml"
    real.write_bytes(original)
    (parent / "config.toml").symlink_to(real)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "config.toml").write_bytes(original)
    render = cmd_pull._toml_document

    def swap(*args, **kwargs):
        result = render(*args, **kwargs)
        parent.rename(tmp_path / "moved")
        parent.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(cmd_pull, "_toml_document", swap)
    with pytest.raises(CliError) as exc:
        cmd_pull._write_file_checked(
            parent / "config.toml", {"mcp_servers": {"demo": {"command": "new"}}}, merge_mcp=True
        )
    assert exc.value.category == ErrorCategory.CONFLICT
    assert (outside / "config.toml").read_bytes() == original
    assert (tmp_path / "moved" / "config.toml").is_symlink()
    assert real.read_bytes() == original


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory descriptor semantics")
def test_parent_swap_before_replace_keeps_destination_anchored(tmp_path, monkeypatch):
    parent = tmp_path / "codex"
    parent.mkdir()
    path = parent / "config.toml"
    original = b'model = "mine"\n'
    path.write_bytes(original)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "config.toml").write_bytes(original)
    replace = os.replace

    def swap(src, dst, **kwargs):
        parent.rename(tmp_path / "moved")
        parent.symlink_to(outside, target_is_directory=True)
        return replace(src, dst, **kwargs)

    monkeypatch.setattr(os, "replace", swap)
    with pytest.raises(CliError) as exc:
        cmd_pull._write_file_checked(path, {"mcp_servers": {"demo": {"command": "new"}}}, merge_mcp=True)
    assert exc.value.category == ErrorCategory.CONFLICT
    assert (outside / "config.toml").read_bytes() == original
    moved = tmp_path / "moved" / "config.toml"
    assert tomllib.loads(moved.read_text(encoding="utf-8"))["mcp_servers"] == {"demo": {"command": "new"}}
    assert exc.value.result["partial"] is True
    identity = moved.parent.stat()
    assert exc.value.result["files"] == [
        {
            "path": f"pinned directory (device={identity.st_dev}, inode={identity.st_ino}), filename='config.toml' (not a pathname)",
            "status": "merged",
        }
    ]


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory descriptor semantics")
@pytest.mark.parametrize("existed", [False, True])
def test_post_write_second_rename_and_path_reuse_does_not_misreport_destination(tmp_path, monkeypatch, existed):
    parent = tmp_path / "codex"
    parent.mkdir()
    requested = parent / "config.toml"
    if existed:
        requested.write_text('model = "mine"\n')
    unrelated = tmp_path / "outside"
    unrelated.mkdir()
    (unrelated / "config.toml").write_text('model = "outside"\n')
    moved = tmp_path / "moved"
    replace = os.replace

    def swap_after_replace(*args, **kwargs):
        replace(*args, **kwargs)
        parent.rename(moved)
        parent.symlink_to(unrelated, target_is_directory=True)

    monkeypatch.setattr(os, "replace", swap_after_replace)
    with pytest.raises(CliError) as exc:
        cmd_pull._write_file_checked(requested, {"mcp_servers": {"demo": {"command": "new"}}}, merge_mcp=True)
    assert exc.value.category == ErrorCategory.CONFLICT
    assert exc.value.result["failed_path"] == str(requested)
    assert exc.value.result["partial"] is True
    # Reuse the path that /proc/self/fd or F_GETPATH could have observed.
    written = tmp_path / "written"
    moved.rename(written)
    moved.mkdir()
    (moved / "config.toml").write_text('model = "unrelated"\n')
    identity = written.stat()
    assert exc.value.result["files"] == [
        {
            "path": f"pinned directory (device={identity.st_dev}, inode={identity.st_ino}), filename='config.toml' (not a pathname)",
            "status": "merged" if existed else "created",
        }
    ]
    assert tomllib.loads((written / "config.toml").read_text())["mcp_servers"] == {"demo": {"command": "new"}}
    assert (moved / "config.toml").read_text() == 'model = "unrelated"\n'
    assert (unrelated / "config.toml").read_text() == 'model = "outside"\n'


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory descriptor semantics")
def test_darwin_parent_swap_reports_pinned_destination_not_outside(tmp_path, monkeypatch):
    import fcntl

    parent = tmp_path / "codex"
    parent.mkdir()
    requested = parent / "config.toml"
    requested.write_bytes(b'model = "mine"\n')
    outside = tmp_path / "outside"
    outside.mkdir()
    unrelated = outside / "config.toml"
    original = b'model = "outside"\n'
    unrelated.write_bytes(original)
    moved = tmp_path / "moved" / "config.toml"
    replace = os.replace

    def swap(src, dst, **kwargs):
        replace(src, dst, **kwargs)
        parent.rename(moved.parent)
        parent.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(os, "replace", swap)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(fcntl, "F_GETPATH", 50, raising=False)

    def no_path_lookup(*args, **kwargs):
        raise AssertionError("F_GETPATH must not be used for reporting")

    monkeypatch.setattr(fcntl, "fcntl", no_path_lookup)

    with pytest.raises(CliError) as exc:
        cmd_pull._write_file_checked(requested, {"mcp_servers": {"demo": {"command": "new"}}}, merge_mcp=True)
    assert exc.value.category == ErrorCategory.CONFLICT
    assert exc.value.result["failed_path"] == str(requested)
    assert exc.value.result["partial"] is True
    destination = exc.value.result["files"][0]
    assert destination["status"] == "merged"
    assert str(unrelated) not in destination["path"]
    identity = moved.parent.stat()
    assert destination["path"] == (
        f"pinned directory (device={identity.st_dev}, inode={identity.st_ino}), filename='config.toml' (not a pathname)"
    )
    assert unrelated.read_bytes() == original
    assert tomllib.loads(moved.read_text())["mcp_servers"] == {"demo": {"command": "new"}}


def test_atomic_writer_keeps_validated_crlf_bytes(tmp_path):
    path = tmp_path / "config.toml"
    source = b'model = "snowman \xe2\x98\x83"\r\n'
    path.write_bytes(source)
    assert cmd_pull._write_file(path, {"mcp_servers": {"demo": {"command": "ok"}}}, merge_mcp=True) == "merged"
    result = path.read_bytes()
    assert b"\r\r\n" not in result
    assert b"\r\n" in result
    assert tomllib.loads(result.decode("utf-8"))["model"] == "snowman ☃"


def test_crlf_merge_preserves_newline_convention_and_utf8(tmp_path):
    path = tmp_path / "config.toml"
    source = b'# snowman \xe2\x98\x83\r\nmodel = "gpt"\r\n[mcp_servers.other]\r\ncommand = "old"\r\n'
    path.write_bytes(source)
    incoming = {"mcp_servers": {"demo": {"command": "ok", "env": {"TOKEN": "dummy"}}}}
    assert cmd_pull._write_file(path, incoming, merge_mcp=True) == "merged"
    result = path.read_bytes()
    assert result.startswith(b'# snowman \xe2\x98\x83\r\nmodel = "gpt"\r\n')
    assert b"\n" not in result.replace(b"\r\n", b"")
    assert b"\r\r\n" not in result
    assert tomllib.loads(result.decode("utf-8"))["mcp_servers"] == {
        "other": {"command": "old"},
        **incoming["mcp_servers"],
    }
    assert cmd_pull._write_file(path, incoming, merge_mcp=True) == "merged"
    assert path.read_bytes() == result


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
    if os.name == "posix":
        replace = os.replace

        def unavailable(src, dst, **kwargs):
            if dst == path.name:
                raise OSError("dummy-sentinel")
            return replace(src, dst, **kwargs)

        monkeypatch.setattr(os, "replace", unavailable)
    else:
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
