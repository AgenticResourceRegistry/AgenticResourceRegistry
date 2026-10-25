# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Migration 031 supports both Alembic-first and create_all-first deployments."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from models.agent import Agent
from models.hook import HookListing
from models.mcp import McpListing
from models.prompt import PromptListing
from models.sandbox import SandboxListing
from models.skill import SkillListing


@pytest.fixture
def migration():
    path = Path(__file__).parents[1] / "observal-server/alembic/versions/041_registry_forks.py"
    spec = importlib.util.spec_from_file_location("registry_fork_migration", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_model_columns_fks_and_indexes_match_all_six_tables(migration):
    models = (Agent, McpListing, SkillListing, HookListing, PromptListing, SandboxListing)
    assert {model.__tablename__ for model in models} == set(migration.TABLES)
    for model in models:
        table = model.__table__
        assert {"forked_from_id", "forked_from_version_id", "forked_from_ref", "forked_at"} <= set(table.c.keys())
        assert any(index.name == f"ix_{model.__tablename__}_forked_from_id" for index in table.indexes)
        for col, target in (
            ("forked_from_id", model.__tablename__),
            ("forked_from_version_id", migration.TABLES[model.__tablename__]),
        ):
            fk = next(iter(table.c[col].foreign_keys))
            assert fk.target_fullname == f"{target}.id"
            assert fk.ondelete == "SET NULL"


def test_upgrade_skips_existing_create_all_columns_indexes_and_fks(migration, monkeypatch):
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)
    monkeypatch.setattr(
        migration,
        "_columns",
        lambda *_: {
            "forked_from_id",
            "forked_from_version_id",
            "forked_from_ref",
            "forked_at",
        },
    )
    monkeypatch.setattr(migration, "_indexes", lambda table: {f"ix_{table}_forked_from_id"})
    monkeypatch.setattr(
        migration,
        "_fork_fks",
        lambda *_: {
            "forked_from_id": "existing_parent_fk",
            "forked_from_version_id": "existing_version_fk",
        },
    )
    migration.upgrade()
    op.add_column.assert_not_called()
    op.create_index.assert_not_called()
    op.create_foreign_key.assert_not_called()


def test_upgrade_creates_four_columns_two_fks_and_index_per_table(migration, monkeypatch):
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)
    monkeypatch.setattr(migration, "_columns", lambda *_: set())
    monkeypatch.setattr(migration, "_indexes", lambda *_: set())
    monkeypatch.setattr(migration, "_fork_fks", lambda *_: {})
    migration.upgrade()
    assert op.add_column.call_count == 6 * 4
    assert op.create_foreign_key.call_count == 6 * 2
    assert op.create_index.call_count == 6
    assert all(call.kwargs["ondelete"] == "SET NULL" for call in op.create_foreign_key.call_args_list)


def test_downgrade_removes_only_existing_fork_columns_constraints_and_indexes(migration, monkeypatch):
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)
    monkeypatch.setattr(
        migration,
        "_columns",
        lambda *_: {
            "forked_from_id",
            "forked_from_version_id",
            "forked_from_ref",
            "forked_at",
        },
    )
    monkeypatch.setattr(migration, "_indexes", lambda table: {f"ix_{table}_forked_from_id"})
    monkeypatch.setattr(
        migration,
        "_fork_fks",
        lambda *_: {
            "forked_from_id": "existing_parent_fk",
            "forked_from_version_id": "existing_version_fk",
        },
    )
    migration.downgrade()
    assert op.drop_index.call_count == 6
    assert op.drop_constraint.call_count == 12
    assert op.drop_column.call_count == 24
