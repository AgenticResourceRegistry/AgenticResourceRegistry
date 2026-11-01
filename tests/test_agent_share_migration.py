# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""030_agent_share_manifests must not fail when its tables already exist.

docker/entrypoint.sh runs Base.metadata.create_all before `alembic upgrade head`,
so an upgraded install already has the share tables when 030 runs. A plain
create_table then raises DuplicateTableError and the init container exits.
"""

import importlib.util
import types
from pathlib import Path

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[1] / "observal-server" / "alembic" / "versions" / "030_agent_share_manifests.py"
)
TABLES = {"agent_share_manifests", "agent_share_items"}
INDEXES = {
    "ix_agent_share_manifests_created_by",
    "ix_agent_share_manifests_expires_at",
    "ix_agent_share_items_agent_id",
}


class _RecordingOp:
    def __init__(self) -> None:
        self.tables: list[str] = []
        self.indexes: list[str] = []

    def create_table(self, name, *args, **kwargs):
        self.tables.append(name)

    def create_index(self, name, *args, **kwargs):
        self.indexes.append(name)


def _load(
    monkeypatch, *, existing_tables: set[str], existing_indexes: set[str]
) -> tuple[types.ModuleType, _RecordingOp]:
    spec = importlib.util.spec_from_file_location("_migration_030", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    recorder = _RecordingOp()
    monkeypatch.setattr(module, "op", recorder)
    monkeypatch.setattr(module, "_has_table", lambda name: name in existing_tables)
    monkeypatch.setattr(module, "_has_index", lambda table, name: name in existing_indexes)
    return module, recorder


def test_upgrade_creates_everything_on_a_database_without_the_tables(monkeypatch):
    module, recorder = _load(monkeypatch, existing_tables=set(), existing_indexes=set())
    module.upgrade()
    assert recorder.tables == ["agent_share_manifests", "agent_share_items"]
    assert set(recorder.indexes) == INDEXES


@pytest.mark.parametrize("existing_indexes", [INDEXES, set()])
def test_upgrade_skips_tables_created_by_create_all(monkeypatch, existing_indexes):
    module, recorder = _load(monkeypatch, existing_tables=TABLES, existing_indexes=existing_indexes)
    module.upgrade()
    assert recorder.tables == []
    assert set(recorder.indexes) == INDEXES - existing_indexes
