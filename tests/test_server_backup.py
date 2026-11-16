# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for observal_cli.server.backup."""

from __future__ import annotations

import pytest

from observal_cli.server import backup


@pytest.fixture(autouse=True)
def isolated_backups(tmp_path, monkeypatch):
    """Redirect backup directory to tmp."""
    monkeypatch.setattr(backup, "BACKUPS_DIR", tmp_path / "backups")
    return tmp_path / "backups"


class TestListBackups:
    def test_empty_dir(self, isolated_backups):
        assert backup.list_backups() == []

    def test_lists_existing_backups(self, isolated_backups):
        # Create fake backup dirs
        b1 = isolated_backups / "v0.7.0-20260521T120000"
        b1.mkdir(parents=True)
        (b1 / "pg.dump").write_bytes(b"fake pg dump data" * 100)
        (b1 / "clickhouse_schema.sql").write_text("CREATE TABLE...")

        b2 = isolated_backups / "v0.6.0-20260501T100000"
        b2.mkdir(parents=True)
        (b2 / "pg.dump").write_bytes(b"older dump")

        results = backup.list_backups()
        assert len(results) == 2
        assert results[0]["name"] == "v0.7.0-20260521T120000"  # Most recent first
        assert results[0]["has_pg"] is True
        assert results[0]["has_ch"] is True
        assert results[1]["has_ch"] is False

    def test_orders_by_creation_timestamp_across_version_changes(self, isolated_backups):
        older = isolated_backups / "v1.9.0-20261001T120000"
        newer = isolated_backups / "v1.10.0-20261002T120000"
        for path in (older, newer):
            path.mkdir(parents=True)

        assert [item["name"] for item in backup.list_backups()] == [newer.name, older.name]


class TestPruneBackups:
    def test_prune_beyond_retention(self, isolated_backups):
        # Create 5 backups
        for i in range(5):
            d = isolated_backups / f"v0.{i}.0-2026050{i}T100000"
            d.mkdir(parents=True)
            (d / "pg.dump").write_bytes(b"data")

        pruned = backup.prune_backups(retention=3)
        assert len(pruned) == 2
        remaining = backup.list_backups()
        assert len(remaining) == 3

    def test_retains_newest_by_timestamp_across_version_changes(self, isolated_backups):
        older = isolated_backups / "v1.10.0-20261001T120000"
        newer = isolated_backups / "v1.9.0-20261002T120000"
        for path in (older, newer):
            path.mkdir(parents=True)

        pruned = backup.prune_backups(retention=1)

        assert pruned == [older]
        assert [item["name"] for item in backup.list_backups()] == [newer.name]

    def test_no_prune_under_retention(self, isolated_backups):
        for i in range(2):
            d = isolated_backups / f"v0.{i}.0-2026050{i}T100000"
            d.mkdir(parents=True)
            (d / "pg.dump").write_bytes(b"data")

        pruned = backup.prune_backups(retention=3)
        assert pruned == []


class TestEstimateBackupSize:
    def test_fallback_on_failure(self, tmp_path):
        """If docker exec fails, returns 100MB fallback."""
        size = backup.estimate_backup_size(tmp_path)
        # Docker isn't running in tests, so it should return fallback
        assert size == 100 * 1024 * 1024
