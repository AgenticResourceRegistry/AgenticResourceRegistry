# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Migration DDL/guard checks without a running PostgreSQL server."""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy.schema import CreateColumn


def _migration(monkeypatch, bind=None):
    op = Mock()
    op.get_bind.return_value = bind or Mock()
    monkeypatch.setitem(sys.modules, "alembic", types.SimpleNamespace(op=op))
    path = Path(__file__).resolve().parents[1] / "observal-server/alembic/versions/029_skill_version_extra_files.py"
    spec = importlib.util.spec_from_file_location("skill_bundle_migration_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, op


def test_upgrade_backfills_old_rows_and_new_legacy_writers(monkeypatch):
    migration, op = _migration(monkeypatch)
    assert migration.down_revision == "030_agent_share_manifests"
    migration.upgrade()
    table, column = op.add_column.call_args.args
    assert table == "skill_versions" and column.name == "extra_files"
    assert not column.nullable
    assert str(column.server_default.arg) == "'[]'::json"
    assert "JSON" in str(CreateColumn(column).compile(dialect=migration.postgresql.dialect()))


def test_downgrade_only_if_no_resource_bearing_rows(monkeypatch):
    bind = Mock()
    bind.execute.return_value.first.return_value = None
    migration, op = _migration(monkeypatch, bind)
    migration.downgrade()
    assert [str(call.args[0]) for call in bind.execute.call_args_list] == [
        "LOCK TABLE skill_versions IN ACCESS EXCLUSIVE MODE",
        "SELECT 1 FROM skill_versions WHERE extra_files::jsonb <> '[]'::jsonb LIMIT 1",
    ]
    op.drop_column.assert_called_once_with("skill_versions", "extra_files")

    bind.execute.return_value.first.return_value = (1,)
    op.reset_mock()
    bind.execute.reset_mock()
    with pytest.raises(RuntimeError, match="contains resources"):
        migration.downgrade()
    assert str(bind.execute.call_args_list[0].args[0]).startswith("LOCK TABLE")
    op.drop_column.assert_not_called()
