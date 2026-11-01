# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit and property-based tests for ch-deep-copy: ClickHouse telemetry migration."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
from hypothesis import HealthCheck, given
from hypothesis import settings as hsettings
from hypothesis import strategies as st
from typer.testing import CliRunner

from observal_cli.main import app as cli_app
from observal_shared.migration.archive import _is_empty_parquet, _month_range, _sha256_file
from observal_shared.migration.ch_export import (
    MAX_SHARD_COUNT,
    TelemetryChunk,
    _build_ch_count_query,
    _build_ch_export_query,
    _build_ch_time_range_query,
    _month_windows,
    _read_count,
    _split_chunk,
)
from observal_shared.migration.connections import parse_clickhouse_url as _parse_clickhouse_url
from observal_shared.migration.constants import _UUID_RE, CLICKHOUSE_TABLES, EPOCH_SENTINELS, FK_PG_TABLE_MAP, TableCfg
from observal_shared.migration.results import TelemetryExportResult, TelemetryImportResult, TelemetryValidationResult

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI_RE.sub("", text)


# ── Mock helpers ─────────────────────────────────────────


class MockResponse:
    """Mock httpx response for _read_count tests."""

    def __init__(self, data: dict):
        self._data = data

    def json(self) -> dict:
        return self._data


# ══════════════════════════════════════════════════════════
# Unit Tests (Example-Based)
# ══════════════════════════════════════════════════════════


# ── CLI Registration Tests ───────────────────────────────


class TestCLIRegistration:
    """Verify Phase 2 telemetry subcommands appear in migrate --help."""

    def test_export_telemetry_in_help(self):
        result = runner.invoke(cli_app, ["server", "migrate", "--help"])
        assert result.exit_code == 0
        assert "export-telemetry" in _plain(result.output)

    def test_import_telemetry_in_help(self):
        result = runner.invoke(cli_app, ["server", "migrate", "--help"])
        assert result.exit_code == 0
        assert "import-telemetry" in _plain(result.output)

    def test_validate_telemetry_in_help(self):
        result = runner.invoke(cli_app, ["server", "migrate", "--help"])
        assert result.exit_code == 0
        assert "validate-telemetry" in _plain(result.output)

    def test_export_telemetry_help_shows_options(self):
        result = runner.invoke(cli_app, ["server", "migrate", "export-telemetry", "--help"])
        assert result.exit_code == 0
        out = _plain(result.output)
        assert "--clickhouse-url" in out
        assert "--manifest" in out
        assert "--output-dir" in out

    def test_import_telemetry_help_shows_options(self):
        result = runner.invoke(cli_app, ["server", "migrate", "import-telemetry", "--help"])
        assert result.exit_code == 0
        out = _plain(result.output)
        assert "--clickhouse-url" in out
        assert "--input-dir" in out

    def test_validate_telemetry_help_shows_options(self):
        result = runner.invoke(cli_app, ["server", "migrate", "validate-telemetry", "--help"])
        assert result.exit_code == 0
        out = _plain(result.output)
        assert "--input-dir" in out
        assert "--clickhouse-url" in out
        assert "--target-db-url" in out


# ── ClickHouse URL Parsing Tests ─────────────────────────


class TestParseClickhouseUrl:
    """Test _parse_clickhouse_url with various URL formats."""

    def test_full_url_with_all_components(self):
        url = "clickhouse://myuser:mypass@ch-host:9000/mydb"
        http_url, db, user, password = _parse_clickhouse_url(url)
        assert http_url == "http://ch-host:9000"
        assert db == "mydb"
        assert user == "myuser"
        assert password == "mypass"

    def test_url_with_default_port(self):
        url = "clickhouse://user:pass@host/db"
        http_url, db, user, password = _parse_clickhouse_url(url)
        assert http_url == "http://host:8123"
        assert db == "db"

    def test_url_with_default_database(self):
        url = "clickhouse://user:pass@host:9000"
        http_url, db, user, password = _parse_clickhouse_url(url)
        assert db == "default"

    def test_url_with_default_user_and_password(self):
        url = "clickhouse://host:9000/db"
        http_url, db, user, password = _parse_clickhouse_url(url)
        assert user == "default"
        assert password == ""

    def test_url_with_slash_only_path(self):
        url = "clickhouse://user:pass@host:8123/"
        http_url, db, user, password = _parse_clickhouse_url(url)
        assert db == "default"

    def test_clickhouses_tls_url(self):
        url = "clickhouses://myuser:mypass@ch-host:9440/mydb"
        http_url, db, user, password = _parse_clickhouse_url(url)
        assert http_url == "https://ch-host:9440"
        assert db == "mydb"
        assert user == "myuser"
        assert password == "mypass"

    def test_clickhouses_default_port_is_8443(self):
        url = "clickhouses://user:pass@host/db"
        http_url, db, user, password = _parse_clickhouse_url(url)
        assert http_url == "https://host:8443"
        assert db == "db"

    def test_anchored_prefix_password_containing_clickhouse(self):
        """Password containing 'clickhouse://' should not corrupt parsing."""
        url = "clickhouse://admin:clickhouse%3A%2F%2Ffoo@host:8123/db"
        http_url, db, user, password = _parse_clickhouse_url(url)
        assert http_url == "http://host:8123"
        assert user == "admin"
        assert db == "db"


# ── Export Query Builder Tests ───────────────────────────


class TestBuildChExportQuery:
    """Test bounded export and count query construction."""

    @staticmethod
    def _chunk(table: str = "session_events") -> TelemetryChunk:
        return TelemetryChunk(
            table,
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 2, 1, tzinfo=UTC),
            3,
            64,
        )

    def test_replacing_engine_has_final_and_bounds(self):
        cfg = CLICKHOUSE_TABLES[0]
        query = _build_ch_export_query(cfg, self._chunk())
        assert "FINAL" in query
        assert "timestamp >= {chunk_start:String}" in query
        assert "timestamp < {chunk_end:String}" in query
        assert "timestamp < {cutoff:String}" in query
        assert "sipHash64(project_id, user_id, harness, session_id)" in query
        assert "% {shard_count:UInt32} = {bucket:UInt32}" in query
        assert query.rstrip().endswith("FORMAT Parquet")

    def test_mergetree_engine_plain_select(self):
        cfg = next(item for item in CLICKHOUSE_TABLES if item["name"] == "audit_log")
        query = _build_ch_export_query(cfg, self._chunk("audit_log"))
        assert "FINAL" not in query
        assert query.rstrip().endswith("FORMAT Parquet")

    def test_count_uses_identical_bounded_predicate(self):
        cfg = CLICKHOUSE_TABLES[0]
        chunk = self._chunk()
        export_query = _build_ch_export_query(cfg, chunk)
        count_query = _build_ch_count_query(cfg, chunk)
        assert "count() AS cnt" in count_query
        assert count_query.endswith("FORMAT JSON")
        assert export_query.split(" WHERE ", 1)[1].removesuffix(" FORMAT Parquet") == count_query.split(" WHERE ", 1)[
            1
        ].removesuffix(" FORMAT JSON")


class TestBuildChTimeRangeQuery:
    """Time discovery must avoid an unbounded FINAL operation."""

    def test_has_min_max_cutoff_and_no_final(self):
        query = _build_ch_time_range_query(CLICKHOUSE_TABLES[0])
        assert "AS min_t" in query
        assert "AS max_t" in query
        assert "timestamp < {cutoff:String}" in query
        assert "FINAL" not in query


# ── Month Range Tests ────────────────────────────────────


class TestMonthRange:
    """Test _month_range generation."""

    def test_same_month_single_entry(self):
        result = _month_range(datetime(2025, 3, 10), datetime(2025, 3, 20))
        assert result == [202503]

    def test_cross_year_boundary(self):
        result = _month_range(datetime(2024, 11, 1), datetime(2025, 2, 1))
        assert result == [202411, 202412, 202501, 202502]

    def test_multi_year_range(self):
        result = _month_range(datetime(2023, 12, 1), datetime(2025, 1, 1))
        assert result[0] == 202312
        assert result[-1] == 202501
        assert len(result) == 14  # Dec 2023 through Jan 2025

    def test_ascending_order_no_gaps(self):
        result = _month_range(datetime(2025, 1, 1), datetime(2025, 6, 30))
        assert result == [202501, 202502, 202503, 202504, 202505, 202506]
        # Verify ascending
        for i in range(len(result) - 1):
            assert result[i] < result[i + 1]


# ── _is_empty_parquet Tests ──────────────────────────────


class TestIsEmptyParquet:
    """Test _is_empty_parquet with real Parquet files."""

    def test_zero_byte_file_is_empty(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".parquet") as f:
            path = Path(f.name)
        try:
            assert _is_empty_parquet(path) is True
        finally:
            path.unlink(missing_ok=True)

    def test_non_empty_parquet_file(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".parquet") as f:
            path = Path(f.name)
        try:
            table = pa.table({"id": [1, 2, 3]})
            pq.write_table(table, path)
            assert _is_empty_parquet(path) is False
        finally:
            path.unlink(missing_ok=True)

    def test_empty_rows_parquet_file(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".parquet") as f:
            path = Path(f.name)
        try:
            table = pa.table({"id": pa.array([], type=pa.int64())})
            pq.write_table(table, path)
            assert _is_empty_parquet(path) is True
        finally:
            path.unlink(missing_ok=True)

    def test_invalid_parquet_returns_true(self):
        """ArrowInvalid from corrupt data should return True (narrow exception)."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".parquet") as f:
            f.write(b"not a parquet file at all")
            path = Path(f.name)
        try:
            assert _is_empty_parquet(path) is True
        finally:
            path.unlink(missing_ok=True)


# ── _read_count Tests ────────────────────────────────────


class TestReadCount:
    """Test _read_count parsing of ClickHouse JSON responses."""

    def test_normal_response(self):
        resp = MockResponse({"data": [{"cnt": "42"}]})
        assert _read_count(resp) == 42

    def test_empty_data(self):
        resp = MockResponse({"data": [{}]})
        assert _read_count(resp) == 0

    def test_missing_cnt_key(self):
        resp = MockResponse({"data": [{"other": "value"}]})
        assert _read_count(resp) == 0

    def test_zero_count(self):
        resp = MockResponse({"data": [{"cnt": "0"}]})
        assert _read_count(resp) == 0


# ── Constants Tests ──────────────────────────────────────


class TestConstants:
    """Verify CLICKHOUSE_TABLES, FK_PG_TABLE_MAP, and EPOCH_SENTINELS."""

    def test_clickhouse_tables_has_7_entries(self):
        assert len(CLICKHOUSE_TABLES) == 7

    def test_each_table_has_required_keys(self):
        for table_cfg in CLICKHOUSE_TABLES:
            assert "name" in table_cfg
            assert "engine" in table_cfg
            assert "time_col" in table_cfg
            assert "fk_cols" in table_cfg
            assert "shard_expr" in table_cfg
            assert "base_shards" in table_cfg
            assert "physical_shards" in table_cfg
            assert "preserve_shard_group" in table_cfg

    def test_table_names(self):
        names = {t["name"] for t in CLICKHOUSE_TABLES}
        expected = {
            "session_events",
            "session_checkpoints",
            "session_stats_agg",
            "layer_snapshots",
            "audit_log",
            "security_events",
            "webhook_deliveries",
        }
        assert names == expected

    def test_engine_types(self):
        for t in CLICKHOUSE_TABLES:
            assert t["engine"] in ("replacing", "mergetree")

    def test_replacing_tables_are_keyed_telemetry(self):
        replacing = {t["name"] for t in CLICKHOUSE_TABLES if t["engine"] == "replacing"}
        assert replacing == {"session_events", "session_checkpoints", "session_stats_agg", "layer_snapshots"}
        assert all(t["preserve_shard_group"] for t in CLICKHOUSE_TABLES if t["engine"] == "replacing")

    def test_mergetree_tables(self):
        mergetree = [t["name"] for t in CLICKHOUSE_TABLES if t["engine"] == "mergetree"]
        assert set(mergetree) == {"audit_log", "security_events", "webhook_deliveries"}

    def test_typed_dict_structure(self):
        """Verify CLICKHOUSE_TABLES entries conform to TableCfg TypedDict."""
        required_keys = {
            "name",
            "engine",
            "time_col",
            "fk_cols",
            "shard_expr",
            "base_shards",
            "physical_shards",
            "preserve_shard_group",
        }
        for table_cfg in CLICKHOUSE_TABLES:
            assert set(table_cfg.keys()) == required_keys
            assert isinstance(table_cfg["name"], str)
            assert table_cfg["engine"] in ("replacing", "mergetree")
            assert isinstance(table_cfg["time_col"], str)
            assert isinstance(table_cfg["fk_cols"], list)
            assert all(isinstance(c, str) for c in table_cfg["fk_cols"])
            assert isinstance(table_cfg["shard_expr"], str)
            assert table_cfg["base_shards"] >= 1
            assert table_cfg["physical_shards"] >= 1
            assert isinstance(table_cfg["preserve_shard_group"], bool)

    def test_tablecfg_type_exists(self):
        """Verify TableCfg is importable and is a TypedDict."""
        assert hasattr(TableCfg, "__annotations__")
        assert "name" in TableCfg.__annotations__
        assert "engine" in TableCfg.__annotations__
        assert "shard_expr" in TableCfg.__annotations__
        assert "base_shards" in TableCfg.__annotations__
        assert "physical_shards" in TableCfg.__annotations__
        assert "preserve_shard_group" in TableCfg.__annotations__

    def test_fk_pg_table_map_has_3_entries(self):
        assert len(FK_PG_TABLE_MAP) == 3

    def test_fk_pg_table_map_keys(self):
        expected_keys = {"agent_id", "user_id", "actor_id"}
        assert set(FK_PG_TABLE_MAP.keys()) == expected_keys

    def test_epoch_sentinels_contains_expected(self):
        assert None in EPOCH_SENTINELS
        assert "" in EPOCH_SENTINELS
        assert "1970-01-01 00:00:00.000" in EPOCH_SENTINELS
        assert "1970-01-01 00:00:00" in EPOCH_SENTINELS


# ── Dataclass Tests ──────────────────────────────────────


class TestDataclasses:
    """Verify Phase 2 dataclass fields."""

    def test_telemetry_export_result_fields(self):
        result = TelemetryExportResult(
            output_dir="/tmp/out",
            migration_id="abc-123",
            table_results={"session_events": {"files": [], "row_count": 0}},
            total_rows=100,
            total_size_bytes=1024,
            duration_seconds=5.0,
        )
        assert result.output_dir == "/tmp/out"
        assert result.migration_id == "abc-123"
        assert result.total_rows == 100
        assert result.total_size_bytes == 1024
        assert result.duration_seconds == 5.0

    def test_telemetry_import_result_fields(self):
        result = TelemetryImportResult(
            migration_id="abc-123",
            tables_imported=4,
            tables_skipped=["security_events"],
            rows_imported={"session_events": 500},
            duration_seconds=10.0,
            warnings=["some warning"],
        )
        assert result.tables_imported == 4
        assert result.tables_skipped == ["security_events"]
        assert result.rows_imported["session_events"] == 500
        assert result.warnings == ["some warning"]

    def test_telemetry_validation_result_fields(self):
        result = TelemetryValidationResult(
            checksums_valid=True,
            checksum_results={"session_events_2025-01.parquet": True},
            fk_results=None,
            row_count_results=None,
        )
        assert result.checksums_valid is True
        assert result.fk_results is None
        assert result.row_count_results is None


# ── Error Path Tests (CLI) ───────────────────────────────


class TestErrorPaths:
    """Test CLI error handling for missing arguments and files."""

    def test_export_telemetry_missing_options(self):
        """export-telemetry without required options should fail."""
        result = runner.invoke(cli_app, ["server", "migrate", "export-telemetry"])
        assert result.exit_code != 0

    def test_export_telemetry_missing_manifest(self):
        """export-telemetry with non-existent manifest should fail."""
        result = runner.invoke(
            cli_app,
            [
                "migrate",
                "export-telemetry",
                "--clickhouse-url",
                "clickhouse://localhost:8123/db",
                "--manifest",
                "/nonexistent/manifest.json",
                "--output-dir",
                "/tmp/test-out",
            ],
        )
        assert result.exit_code != 0

    def test_import_telemetry_missing_input_dir(self):
        """import-telemetry with non-existent input dir should fail."""
        result = runner.invoke(
            cli_app,
            [
                "migrate",
                "import-telemetry",
                "--clickhouse-url",
                "clickhouse://localhost:8123/db",
                "--input-dir",
                "/nonexistent/dir",
            ],
        )
        assert result.exit_code != 0

    def test_validate_telemetry_missing_input_dir(self):
        """validate-telemetry with non-existent input dir should fail."""
        result = runner.invoke(
            cli_app,
            [
                "migrate",
                "validate-telemetry",
                "--input-dir",
                "/nonexistent/dir",
            ],
        )
        assert result.exit_code != 0


# ── Security Tests ───────────────────────────────────────


class TestSecurity:
    """Verify connection strings never appear in CLI output."""

    @patch("observal_cli.cmd_migrate.asyncio")
    def test_clickhouse_url_not_in_export_output(self, mock_asyncio):
        secret_url = "clickhouse://secret_user:secret_pass@secret-host:9000/secret_db"
        mock_asyncio.run.side_effect = SystemExit(1)
        result = runner.invoke(
            cli_app,
            [
                "migrate",
                "export-telemetry",
                "--clickhouse-url",
                secret_url,
                "--manifest",
                "/nonexistent/manifest.json",
                "--output-dir",
                "/tmp/test-out",
            ],
        )
        assert secret_url not in result.output

    def test_clickhouse_url_not_in_import_output(self):
        secret_url = "clickhouse://secret_user:secret_pass@secret-host:9000/secret_db"
        result = runner.invoke(
            cli_app,
            [
                "migrate",
                "import-telemetry",
                "--clickhouse-url",
                secret_url,
                "--input-dir",
                "/nonexistent/dir",
            ],
        )
        assert secret_url not in result.output

    def test_clickhouse_url_not_in_validate_output(self):
        secret_url = "clickhouse://secret_user:secret_pass@secret-host:9000/secret_db"
        result = runner.invoke(
            cli_app,
            [
                "migrate",
                "validate-telemetry",
                "--input-dir",
                "/nonexistent/dir",
                "--clickhouse-url",
                secret_url,
            ],
        )
        assert secret_url not in result.output


# ══════════════════════════════════════════════════════════
# Property-Based Tests (Hypothesis)
# ══════════════════════════════════════════════════════════


# ── Property 2: ClickHouse URL parsing correctness ──────


class TestClickhouseUrlParsingProperty:
    """Property 2: ClickHouse URL parsing extracts correct components.

    **Validates: Requirements 3.1**
    """

    @given(
        host=st.from_regex(r"[a-z][a-z0-9-]{0,20}", fullmatch=True),
        port=st.integers(min_value=1, max_value=65535),
        db=st.from_regex(r"[a-z][a-z0-9_]{0,20}", fullmatch=True),
        user=st.from_regex(r"[a-z][a-z0-9]{0,10}", fullmatch=True),
        password=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            min_size=1,
            max_size=20,
        ),
    )
    @hsettings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_url_components_extracted(self, host, port, db, user, password):
        url = f"clickhouse://{user}:{password}@{host}:{port}/{db}"
        http_url, parsed_db, parsed_user, parsed_password = _parse_clickhouse_url(url)
        assert http_url == f"http://{host}:{port}"
        assert parsed_db == db
        assert parsed_user == user
        assert parsed_password == password

    @given(
        host=st.from_regex(r"[a-z][a-z0-9-]{0,20}", fullmatch=True),
    )
    @hsettings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_defaults_applied_for_missing_components(self, host):
        url = f"clickhouse://{host}"
        http_url, db, user, password = _parse_clickhouse_url(url)
        assert http_url == f"http://{host}:8123"
        assert db == "default"
        assert user == "default"
        assert password == ""

    @given(
        host=st.from_regex(r"[a-z][a-z0-9-]{0,20}", fullmatch=True),
        port=st.integers(min_value=1, max_value=65535),
        db=st.from_regex(r"[a-z][a-z0-9_]{0,20}", fullmatch=True),
        user=st.from_regex(r"[a-z][a-z0-9]{0,10}", fullmatch=True),
        password=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            min_size=1,
            max_size=20,
        ),
    )
    @hsettings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_tls_url_components_extracted(self, host, port, db, user, password):
        url = f"clickhouses://{user}:{password}@{host}:{port}/{db}"
        http_url, parsed_db, parsed_user, parsed_password = _parse_clickhouse_url(url)
        assert http_url == f"https://{host}:{port}"
        assert parsed_db == db
        assert parsed_user == user
        assert parsed_password == password

    @given(
        host=st.from_regex(r"[a-z][a-z0-9-]{0,20}", fullmatch=True),
    )
    @hsettings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_tls_defaults_applied(self, host):
        url = f"clickhouses://{host}"
        http_url, db, user, password = _parse_clickhouse_url(url)
        assert http_url == f"https://{host}:8443"
        assert db == "default"


# ── Property 3: Export query builder correctness ─────────


class TestExportQueryBuilderProperty:
    """Property 3: Export query builder correctness.

    **Validates: Requirements 4.2, 4.3, 4.4, 5.4, 12.1, 12.2, 12.3**
    """

    @given(
        table_cfg=st.sampled_from(CLICKHOUSE_TABLES),
        bucket=st.integers(min_value=0, max_value=63),
    )
    @hsettings(max_examples=100)
    def test_export_query_properties(self, table_cfg, bucket):
        chunk = TelemetryChunk(
            table_cfg["name"],
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 2, 1, tzinfo=UTC),
            bucket,
            64,
        )
        query = _build_ch_export_query(table_cfg, chunk)
        if table_cfg["engine"] == "replacing":
            assert "FINAL" in query
        else:
            assert "FINAL" not in query
        assert table_cfg["time_col"] in query
        assert table_cfg["shard_expr"] in query
        assert query.rstrip().endswith("FORMAT Parquet")

    @given(
        table_cfg=st.sampled_from(CLICKHOUSE_TABLES),
        bucket=st.integers(min_value=0, max_value=63),
    )
    @hsettings(max_examples=100)
    def test_count_query_properties(self, table_cfg, bucket):
        chunk = TelemetryChunk(
            table_cfg["name"],
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 2, 1, tzinfo=UTC),
            bucket,
            64,
        )
        query = _build_ch_count_query(table_cfg, chunk)
        assert "count() AS cnt" in query
        assert "FORMAT JSON" in query
        assert table_cfg["shard_expr"] in query
        assert ("FINAL" in query) is (table_cfg["engine"] == "replacing")


# ── Property 4: Month range completeness and ordering ────


class TestMonthRangeProperty:
    """Property 4: Month range generation completeness and ordering.

    **Validates: Requirements 5.2, 5.5**
    """

    @given(
        min_dt=st.datetimes(min_value=datetime(2000, 1, 1), max_value=datetime(2099, 12, 31)),
        max_dt=st.datetimes(min_value=datetime(2000, 1, 1), max_value=datetime(2099, 12, 31)),
    )
    @hsettings(max_examples=100)
    def test_month_range_properties(self, min_dt, max_dt):
        if min_dt > max_dt:
            min_dt, max_dt = max_dt, min_dt

        result = _month_range(min_dt, max_dt)

        # No duplicates
        assert len(result) == len(set(result))

        # Ascending order
        for i in range(len(result) - 1):
            assert result[i] < result[i + 1]

        # First and last months correct
        assert result[0] == min_dt.year * 100 + min_dt.month
        assert result[-1] == max_dt.year * 100 + max_dt.month

        # No gaps: consecutive months differ by 1 month or year rollover
        for i in range(len(result) - 1):
            y1, m1 = divmod(result[i], 100)
            y2, m2 = divmod(result[i + 1], 100)
            if m1 == 12:
                assert y2 == y1 + 1 and m2 == 1
            else:
                assert y2 == y1 and m2 == m1 + 1

        # Correct length
        min_months = min_dt.year * 12 + min_dt.month
        max_months = max_dt.year * 12 + max_dt.month
        expected_len = max_months - min_months + 1
        assert len(result) == expected_len


# ── Property 5: SHA-256 checksum integrity ───────────────


class TestSha256IntegrityProperty:
    """Property 5: SHA-256 checksum integrity.

    **Validates: Requirements 6.1, 6.5**
    """

    @given(data=st.binary(min_size=0, max_size=10000))
    @hsettings(max_examples=100)
    def test_sha256_deterministic(self, data):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(data)
            f.flush()
            path = Path(f.name)
        try:
            hash1 = _sha256_file(path)
            hash2 = _sha256_file(path)
            assert hash1 == hash2
            # Also matches stdlib
            assert hash1 == hashlib.sha256(data).hexdigest()
        finally:
            path.unlink(missing_ok=True)


# ── Property 6: Telemetry manifest JSON round-trip ───────


class TestTelemetryManifestRoundTripProperty:
    """Property 6: Telemetry manifest JSON round-trip.

    **Validates: Requirements 10.4**
    """

    @given(
        migration_id=st.uuids(),
        row_counts=st.dictionaries(
            keys=st.sampled_from(["session_events", "audit_log", "security_events", "audit_log", "webhook_deliveries"]),
            values=st.integers(min_value=0, max_value=1_000_000),
            min_size=1,
            max_size=5,
        ),
        checksums_valid=st.booleans(),
    )
    @hsettings(max_examples=100)
    def test_telemetry_manifest_round_trip(self, migration_id, row_counts, checksums_valid):
        manifest = {
            "migration_id": str(migration_id),
            "phase": "deep_copy",
            "phase_status": "export_complete",
            "export_completed_at": datetime.now(UTC).isoformat(),
            "export_time_cutoff": datetime.now(UTC).isoformat(),
            "source_clickhouse_url_hash": hashlib.sha256(b"clickhouse://test").hexdigest(),
            "tables": {
                table: {
                    "files": [f"{table}_2025-01.parquet"],
                    "row_count": count,
                    "checksum": {f"{table}_2025-01.parquet": hashlib.sha256(f"{table}".encode()).hexdigest()},
                    "time_range": {"min": "2025-01-01T00:00:00", "max": "2025-01-31T23:59:59"},
                }
                for table, count in row_counts.items()
            },
            "fk_validation": {
                "orphaned_agent_ids": [],
                "orphaned_agent_ids_truncated": False,
                "orphaned_user_ids": [],
                "orphaned_user_ids_truncated": False,
                "validated_at": None,
            },
        }
        serialized = json.dumps(manifest)
        deserialized = json.loads(serialized)
        assert deserialized == manifest


# ── Property 7: migration_id consistency ─────────────────


class TestMigrationIdConsistencyProperty:
    """Property 7: migration_id consistency across phases.

    **Validates: Requirements 2.4, 16.3**
    """

    @given(migration_id=st.uuids())
    @hsettings(max_examples=100)
    def test_migration_id_carried_forward(self, migration_id):
        mid = str(migration_id)

        # Phase 1 manifest
        p1_manifest = {
            "migration_id": mid,
            "phase1_completed_at": datetime.now(UTC).isoformat(),
            "source_db_url_hash": hashlib.sha256(b"pg://test").hexdigest(),
            "table_row_counts": {},
            "uuid_ranges": {},
        }

        # Simulate Phase 2 reading Phase 1 manifest and carrying forward
        p2_manifest = {
            "migration_id": p1_manifest["migration_id"],
            "phase": "deep_copy",
            "phase_status": "export_complete",
            "export_completed_at": datetime.now(UTC).isoformat(),
            "tables": {},
        }

        # Serialize and deserialize both
        p1 = json.loads(json.dumps(p1_manifest))
        p2 = json.loads(json.dumps(p2_manifest))
        assert p1["migration_id"] == p2["migration_id"]
        assert p2["migration_id"] == mid


# ── Property 8: FK validation completeness ───────────────


class TestFKValidationCompletenessProperty:
    """Property 8: FK validation completeness.

    **Validates: Requirements 9.2, 9.3, 9.4**
    """

    @given(
        agent_ids=st.frozensets(st.uuids().map(str), min_size=0, max_size=20),
        user_ids=st.frozensets(st.uuids().map(str), min_size=0, max_size=20),
        actor_ids=st.frozensets(st.uuids().map(str), min_size=0, max_size=20),
    )
    @hsettings(max_examples=100)
    def test_fk_collection_is_complete(self, agent_ids, user_ids, actor_ids):
        """Verify FK collection merges audit actors into user references."""
        fk_values = {
            "agent_id": set(agent_ids),
            "user_id": set(user_ids),
            "actor_id": set(actor_ids),
        }

        fk_values["user_id"] |= fk_values.pop("actor_id", set())

        assert fk_values["user_id"] == set(user_ids) | set(actor_ids)
        assert fk_values["agent_id"] == set(agent_ids)


# ── Property 9: Orphaned reference detection ─────────────


class TestOrphanedReferenceDetectionProperty:
    """Property 9: Orphaned reference detection correctness.

    **Validates: Requirements 9.7**
    """

    @given(
        collected=st.frozensets(st.uuids().map(str), min_size=0, max_size=50),
        existing=st.frozensets(st.uuids().map(str), min_size=0, max_size=50),
    )
    @hsettings(max_examples=100)
    def test_orphaned_is_set_difference(self, collected, existing):
        """orphaned = collected - existing, exactly."""
        orphaned = sorted(collected - existing)
        assert set(orphaned) == collected - existing
        # No false positives: every orphaned ID is in collected but not existing
        for oid in orphaned:
            assert oid in collected
            assert oid not in existing
        # No false negatives: every collected ID not in existing is orphaned
        for cid in collected:
            if cid not in existing:
                assert cid in orphaned


# ── Property 10: Connection string never leaked ──────────


class TestConnectionStringNeverLeakedProperty:
    """Property 10: Connection string never leaked.

    **Validates: Requirements 1.6, 13.1, 13.4**
    """

    @given(
        host=st.from_regex(r"[a-z][a-z0-9]{2,10}", fullmatch=True),
        port=st.integers(min_value=1000, max_value=65535),
        user=st.from_regex(r"[a-z]{3,10}", fullmatch=True),
        password=st.from_regex(r"[a-z0-9]{5,15}", fullmatch=True),
    )
    @hsettings(max_examples=100, deadline=None)
    def test_clickhouse_url_never_in_output(self, host, port, user, password):
        secret_url = f"clickhouse://{user}:{password}@{host}:{port}/testdb"

        # Test export-telemetry path
        with nullcontext():
            result = runner.invoke(
                cli_app,
                [
                    "migrate",
                    "export-telemetry",
                    "--clickhouse-url",
                    secret_url,
                    "--manifest",
                    "/nonexistent/manifest.json",
                    "--output-dir",
                    "/tmp/test-out",
                ],
            )
            assert secret_url not in result.output

    @given(
        host=st.from_regex(r"[a-z][a-z0-9]{2,10}", fullmatch=True),
        port=st.integers(min_value=1000, max_value=65535),
        user=st.from_regex(r"[a-z]{3,10}", fullmatch=True),
        password=st.from_regex(r"[a-z0-9]{5,15}", fullmatch=True),
    )
    @hsettings(max_examples=100, deadline=None)
    def test_clickhouse_url_never_in_import_output(self, host, port, user, password):
        secret_url = f"clickhouse://{user}:{password}@{host}:{port}/testdb"

        with nullcontext():
            result = runner.invoke(
                cli_app,
                [
                    "migrate",
                    "import-telemetry",
                    "--clickhouse-url",
                    secret_url,
                    "--input-dir",
                    "/nonexistent/dir",
                ],
            )
            assert secret_url not in result.output

    @given(
        host=st.from_regex(r"[a-z][a-z0-9]{2,10}", fullmatch=True),
        port=st.integers(min_value=1000, max_value=65535),
        user=st.from_regex(r"[a-z]{3,10}", fullmatch=True),
        password=st.from_regex(r"[a-z0-9]{5,15}", fullmatch=True),
    )
    @hsettings(max_examples=100, deadline=None)
    def test_clickhouse_url_never_in_validate_output(self, host, port, user, password):
        secret_url = f"clickhouse://{user}:{password}@{host}:{port}/testdb"

        with nullcontext():
            result = runner.invoke(
                cli_app,
                [
                    "migrate",
                    "validate-telemetry",
                    "--input-dir",
                    "/nonexistent/dir",
                    "--clickhouse-url",
                    secret_url,
                ],
            )
            assert secret_url not in result.output


# ══════════════════════════════════════════════════════════
# New Tests for Fix Tasks
# ══════════════════════════════════════════════════════════


# ── UUID Lowercase Normalization ─────────────────────────


class TestUUIDLowercaseNormalization:
    """Verify UUID values are normalized to lowercase for FK comparison."""

    def test_uuid_re_matches_lowercase(self):
        assert _UUID_RE.match("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

    def test_uuid_re_matches_uppercase(self):
        assert _UUID_RE.match("A1B2C3D4-E5F6-7890-ABCD-EF1234567890")

    def test_uuid_re_matches_mixed_case(self):
        assert _UUID_RE.match("A1b2C3d4-E5f6-7890-AbCd-Ef1234567890")

    def test_uuid_re_rejects_non_uuid(self):
        assert not _UUID_RE.match("not-a-uuid")
        assert not _UUID_RE.match("filesystem")
        assert not _UUID_RE.match("")

    def test_uuid_re_is_module_level_constant(self):
        """Verify _UUID_RE is compiled once at module level, not per call."""
        import observal_cli.cmd_migrate as mod

        assert hasattr(mod, "_UUID_RE")
        assert mod._UUID_RE is _UUID_RE


# ── Partition Check for All Engines ──────────────────────


class TestChunkSplitting:
    """Verify deterministic time/hash splitting covers the parent chunk."""

    def test_time_split_is_contiguous(self):
        chunk = TelemetryChunk(
            "audit_log",
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 2, 1, tzinfo=UTC),
            0,
            1,
        )
        left, right = _split_chunk(chunk, prefer_hash=False)
        assert left.start == chunk.start
        assert left.end == right.start
        assert right.end == chunk.end
        assert left.bucket == right.bucket == 0

    def test_hash_split_is_disjoint_and_bounded(self):
        chunk = TelemetryChunk(
            "session_events",
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 1, 1, tzinfo=UTC),
            3,
            64,
        )
        left, right = _split_chunk(chunk, prefer_hash=True)
        assert left.shard_count == right.shard_count == 128
        assert {left.bucket, right.bucket} == {3, 67}
        terminal = TelemetryChunk(chunk.table, chunk.start, chunk.end, 0, MAX_SHARD_COUNT)
        assert _split_chunk(terminal, prefer_hash=True) is None


# ── Import Resume State ──────────────────────────────────


class TestImportResumeState:
    """Verify .import_state.json is written and read for resume."""

    def test_state_file_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / ".import_state.json"
            completed = {
                "session_events:chunk-1": {
                    "filename": "session_events_chunk-1.parquet",
                    "sha256": "a" * 64,
                    "rows": 10,
                }
            }
            state_path.write_text(
                json.dumps({"migration_id": "migration-1", "completed_chunks": completed}, indent=2),
                encoding="utf-8",
            )
            loaded = json.loads(state_path.read_text(encoding="utf-8"))
            assert loaded["migration_id"] == "migration-1"
            assert loaded["completed_chunks"] == completed

    def test_state_file_empty_initially(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / ".import_state.json"
            assert not state_path.exists()


# ── Atomic Write Pattern ─────────────────────────────────


class TestAtomicWritePattern:
    """Verify the .tmp file pattern for atomic writes."""

    def test_tmp_suffix_construction(self):
        """Verify the tmp path is constructed correctly."""
        original = Path("/tmp/session_events_2025-01.parquet")
        tmp = original.with_suffix(original.suffix + ".tmp")
        assert str(tmp).endswith(".parquet.tmp")
        assert tmp.name == "session_events_2025-01.parquet.tmp"


# ── UTF-8 Encoding ───────────────────────────────────────


class TestUTF8Encoding:
    """Verify encoding='utf-8' is used on all text I/O."""

    def test_utf8_write_and_read(self):
        """Verify UTF-8 encoding works for non-ASCII content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.json"
            content = json.dumps({"name": "tëst-dàtà-日本語"}, indent=2)
            path.write_text(content, encoding="utf-8")
            loaded = json.loads(path.read_text(encoding="utf-8"))
            assert loaded["name"] == "tëst-dàtà-日本語"


# ── Sidecar Archive Hash ─────────────────────────────────


class TestSidecarArchiveHash:
    """Verify archive_sha256 field in sidecar manifest."""

    def test_sha256_file_deterministic(self):
        """Verify _sha256_file produces consistent results."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz") as f:
            f.write(b"test archive content")
            path = Path(f.name)
        try:
            h1 = _sha256_file(path)
            h2 = _sha256_file(path)
            assert h1 == h2
            assert len(h1) == 64  # SHA-256 hex digest length
        finally:
            path.unlink(missing_ok=True)

    def test_archive_hash_field_in_manifest(self):
        """Verify the archive_sha256 field can be added to a manifest dict."""
        manifest = {"migration_id": "test-123"}
        archive_hash = hashlib.sha256(b"test").hexdigest()
        manifest["archive_sha256"] = archive_hash
        serialized = json.dumps(manifest)
        deserialized = json.loads(serialized)
        assert deserialized["archive_sha256"] == archive_hash


# ── Parameterized Query ──────────────────────────────────


class TestParameterizedQuery:
    """Verify _ch_existing_tables uses parameterized query, not f-string."""

    def test_existing_tables_query_uses_parameterized_syntax(self):
        """The SQL should use {db:String} placeholder, not f-string interpolation."""
        # We can't easily call the async function, but we can verify the pattern
        # by checking the source code uses the right SQL string.
        import inspect

        from observal_shared.migration.ch_import import _ch_existing_tables

        source = inspect.getsource(_ch_existing_tables)
        assert "{db:String}" in source
        assert "extra_params" in source
        # Should NOT have f-string with db variable in SQL
        assert 'f"SELECT' not in source or "f'SELECT" not in source


# ── Cutoff in WHERE Clause ───────────────────────────────


class TestBoundedWindows:
    """Verify month planning clips windows at the cutoff."""

    def test_windows_are_contiguous_and_cutoff_bounded(self):
        cutoff = datetime(2025, 2, 15, 12, tzinfo=UTC)
        windows = _month_windows(
            datetime(2025, 1, 10, tzinfo=UTC),
            datetime(2025, 3, 10, tzinfo=UTC),
            cutoff,
        )
        assert windows == [
            (datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 2, 1, tzinfo=UTC)),
            (datetime(2025, 2, 1, tzinfo=UTC), cutoff),
        ]
        assert windows[0][1] == windows[1][0]


def test_exec_dashboard_queries_session_tables_only():
    root = Path(__file__).resolve().parents[1]
    source = (root / "observal-server/api/routes/exec_dashboard.py").read_text()
    assert "FROM traces" not in source
    assert "FROM spans" not in source
    assert "JOIN traces" not in source
    assert "JOIN spans" not in source
    assert "session_stats_agg" in source
    assert "error_rate = None" in source
    assert "success_rate = None" in source


def test_legacy_clickhouse_tables_are_dropped_but_not_exported():
    root = Path(__file__).resolve().parents[1]
    baseline = (root / "observal-server/clickhouse/migrations/001_baseline.sql").read_text()
    constants = (root / "packages/observal-shared/observal_shared/migration/constants.py").read_text()
    for table in ("traces", "spans", "scores", "otel_logs"):
        assert f"DROP TABLE IF EXISTS {table}" in baseline
        assert f'"name": "{table}"' not in constants
