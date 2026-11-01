# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""ClickHouse subpackage.

Re-exports every public symbol that was previously available from
``services.clickhouse`` (the flat module).  All existing callers continue to
work without import changes.
"""

from services.clickhouse._settings import _resource_overrides
from services.clickhouse.client import (
    CLICKHOUSE_DB,
    CLICKHOUSE_HTTP,
    CLICKHOUSE_PASSWORD,
    CLICKHOUSE_USER,
    _get_client,
    _invalidate_cache,
    _normalize_ts,
    _now_ms,
    _query,
    clickhouse_health,
)
from services.clickhouse.insert import (
    _insert_webhook_deliveries,
    insert_audit_log,
    insert_session_capabilities,
    insert_session_checkpoint,
    insert_session_events,
    refresh_session_summary,
)
from services.clickhouse.migrations import run_clickhouse_migrations
from services.clickhouse.query import (
    query_existing_for_dedup,
    query_recent_events,
    query_session_checkpoint,
    query_session_source_manifest,
    query_source_records_after,
)
from services.clickhouse.schema import (
    DEFAULT_QUERY_SETTINGS,
    RESOURCE_SETTINGS_MAP,
    _materialize_if_needed,
    apply_resource_settings,
    init_clickhouse,
)

__all__ = [
    "CLICKHOUSE_DB",
    "CLICKHOUSE_HTTP",
    "CLICKHOUSE_PASSWORD",
    "CLICKHOUSE_USER",
    "DEFAULT_QUERY_SETTINGS",
    "RESOURCE_SETTINGS_MAP",
    "_get_client",
    "_insert_webhook_deliveries",
    "_invalidate_cache",
    "_materialize_if_needed",
    "_normalize_ts",
    "_now_ms",
    "_query",
    "_resource_overrides",
    "apply_resource_settings",
    "clickhouse_health",
    "init_clickhouse",
    "insert_audit_log",
    "insert_session_capabilities",
    "insert_session_checkpoint",
    "insert_session_events",
    "query_existing_for_dedup",
    "query_recent_events",
    "query_session_checkpoint",
    "query_session_source_manifest",
    "query_source_records_after",
    "refresh_session_summary",
    "run_clickhouse_migrations",
]
