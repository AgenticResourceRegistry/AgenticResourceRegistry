# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Lifecycle migration's ancestry, repair and safe downgrade contract."""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy import inspect

from models.skill import SkillVersion


def _migration(monkeypatch, bind=None):
    op = Mock()
    op.get_bind.return_value = bind or Mock()
    monkeypatch.setitem(sys.modules, "alembic", types.SimpleNamespace(op=op))
    path = Path(__file__).resolve().parents[1] / "observal-server/alembic/versions/030_skill_release_lifecycle.py"
    spec = importlib.util.spec_from_file_location("skill_release_migration_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, op


def test_additive_columns_and_deterministic_approved_pointer_repair(monkeypatch):
    migration, op = _migration(monkeypatch)
    assert migration.down_revision == "029_skill_version_extra_files"
    migration.upgrade()
    columns = {call.args[1].name: call.args[1] for call in op.add_column.call_args_list}
    assert set(columns) == {
        "base_version_id",
        "base_revision",
        "content_revision",
        "requires_global_review",
        "pre_public_status",
    }
    assert not columns["requires_global_review"].nullable
    assert columns["requires_global_review"].server_default is not None
    op.create_foreign_key.assert_called_once()
    query = op.execute.call_args.args[0]
    assert "ORDER BY (approved.status = 'approved') DESC" in query
    assert "approved.released_at DESC, approved.id DESC" in query
    assert "approved.status IN ('approved', 'archived')" in query
    assert "stale.status IN ('pending', 'draft', 'rejected')" in query
    assert all(field in inspect(SkillVersion).columns for field in columns)


def test_downgrade_refuses_saved_draft_or_global_review_data(monkeypatch):
    bind = Mock()
    bind.execute.return_value.first.return_value = (1,)
    migration, op = _migration(monkeypatch, bind)
    with pytest.raises(RuntimeError, match="Cannot downgrade"):
        migration.downgrade()
    assert str(bind.execute.call_args_list[0].args[0]) == "LOCK TABLE skill_versions IN ACCESS EXCLUSIVE MODE"
    query = str(bind.execute.call_args_list[1].args[0])
    for field in ("requires_global_review", "pre_public_status", "base_version_id", "content_revision"):
        assert field in query
    op.drop_column.assert_not_called()
    bind.execute.return_value.first.return_value = None
    migration.downgrade()
    assert [call.args[1] for call in op.drop_column.call_args_list] == [
        "pre_public_status",
        "requires_global_review",
        "content_revision",
        "base_revision",
        "base_version_id",
    ]
