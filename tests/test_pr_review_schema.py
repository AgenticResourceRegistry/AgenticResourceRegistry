# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Migration and metadata smoke tests without Docker."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

from models import Base, Review, ReviewRevision, ReviewThread


def test_review_schema_upgrades_twice_from_legacy_sqlite():
    engine = create_engine("sqlite://")
    legacy = [
        table
        for table in Base.metadata.sorted_tables
        if not table.name.startswith("review_") and table.name != "reviews"
    ]
    Base.metadata.create_all(engine, tables=legacy)
    path = Path(__file__).resolve().parent.parent / "observal-server/alembic/versions/042_pr_reviews.py"
    spec = importlib.util.spec_from_file_location("review_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cutover_spec = importlib.util.spec_from_file_location("cutover_migration", path.with_name("043_review_cutover.py"))
    cutover = importlib.util.module_from_spec(cutover_spec)
    cutover_spec.loader.exec_module(cutover)
    orphan_spec = importlib.util.spec_from_file_location(
        "orphan_migration", path.with_name("044_review_orphan_authors.py")
    )
    orphan = importlib.util.module_from_spec(orphan_spec)
    orphan_spec.loader.exec_module(orphan)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        module.upgrade()
        module.upgrade()
        cutover.upgrade()
        orphan.upgrade()
        tables = set(inspect(conn).get_table_names())
        assert set(Base.metadata.tables) <= tables
        assert {"uq_reviews_subject_version"} <= {c["name"] for c in inspect(conn).get_unique_constraints("reviews")}
        assert {"uq_review_submission_draft"} <= {i["name"] for i in inspect(conn).get_indexes("review_submissions")}
        migrated = inspect(conn)
        for table in Base.metadata.sorted_tables:
            if table.name != "reviews" and not table.name.startswith("review_"):
                continue
            # This SQLite smoke stops at 044; 046's PostgreSQL constraint
            # migration is exercised on PostgreSQL, not via SQLite table rebuild.
            expected = {
                (tuple(fk.parent.name for fk in c.elements), c.elements[0].column.table.name, c.ondelete)
                for c in table.foreign_key_constraints
                if table.name != "review_threads" or c.name != "fk_review_threads_change_request"
            }
            actual = {
                (tuple(fk["constrained_columns"]), fk["referred_table"], fk.get("options", {}).get("ondelete"))
                for fk in migrated.get_foreign_keys(table.name)
            }
            assert actual == expected, table.name
            assert {
                c.name: c.nullable
                for c in table.columns
                if table.name != "review_threads" or c.name != "change_request_id"
            } == {c["name"]: c["nullable"] for c in migrated.get_columns(table.name)}, table.name
        orphan.downgrade()
        cutover.downgrade()
        module.downgrade()
        assert "reviews" not in inspect(conn).get_table_names()
    engine.dispose()


def test_number_sequence_and_unique_revision():
    assert Review.__table__.c.number.default.name == "review_number_seq"
    assert any(c.name == "uq_review_revision_number" for c in ReviewRevision.__table__.constraints)
    assert any(c.name == "uq_review_threads_change_request" for c in ReviewThread.__table__.constraints)
    assert any(c.name == "fk_review_threads_change_request" for c in ReviewThread.__table__.foreign_key_constraints)
