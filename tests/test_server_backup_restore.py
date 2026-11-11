# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Focused regression tests for PostgreSQL backup restore failures."""

from __future__ import annotations

import subprocess
from unittest.mock import Mock

import pytest

from observal_cli.server import backup


@pytest.fixture
def restore_context(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    (backup_dir / "pg.dump").write_bytes(b"custom-format-backup")
    compose_dir = tmp_path / "compose"
    compose_dir.mkdir()

    run = Mock()

    def verify_run(command, **kwargs):
        assert command == [
            "docker",
            "compose",
            "exec",
            "-T",
            "observal-db",
            "pg_restore",
            "-U",
            "postgres",
            "-d",
            "observal",
            "--clean",
            "--if-exists",
            "--exit-on-error",
        ]
        assert kwargs["stdin"].name == str(backup_dir / "pg.dump")
        assert kwargs["stdin"].read() == b"custom-format-backup"
        assert kwargs["capture_output"] is True
        assert kwargs["cwd"] == compose_dir
        assert kwargs["timeout"] == 300
        return run(command, **kwargs)

    monkeypatch.setattr(backup.subprocess, "run", verify_run)
    return backup_dir, compose_dir, run


def test_restore_success_on_zero_exit(restore_context, capsys):
    backup_dir, compose_dir, run = restore_context
    run.return_value = subprocess.CompletedProcess([], 0, b"", b"")

    backup.restore_backup(backup_dir, compose_dir)

    run.assert_called_once()
    assert "PostgreSQL restored." in capsys.readouterr().out


def test_restore_rejects_exit_one_even_with_clean(restore_context, capsys):
    backup_dir, compose_dir, run = restore_context
    run.return_value = subprocess.CompletedProcess([], 1, b"", b"pg_restore: error: unsupported version")

    with pytest.raises(RuntimeError, match="pg_restore failed: pg_restore: error: unsupported version"):
        backup.restore_backup(backup_dir, compose_dir)

    run.assert_called_once()
    assert "PostgreSQL restored." not in capsys.readouterr().out
