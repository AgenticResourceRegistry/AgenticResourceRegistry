# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Migration constants: table ordering, column metadata, and ClickHouse config."""

from __future__ import annotations

import re
from typing import Literal, TypedDict

# ── PostgreSQL constants ─────────────────────────────────

CHUNK_SIZE = 500
DEFAULT_PROJECT_ID = "default"
TELEMETRY_MANIFEST_VERSION = "2.0"

INSERT_ORDER: list[str] = [
    # Tier 0 - no FK dependencies
    "enterprise_config",
    "component_sources",
    # Tier 1 - deployment-wide records
    "users",
    "exporter_configs",
    # Tier 1.5 - FK to users
    "component_bundles",
    # Tier 2 - FK to orgs + users + component_bundles
    # NOTE: listings/agents have a circular FK with their version tables:
    #   *_listings.latest_version_id → *_versions.id (nullable, use_alter)
    #   *_versions.listing_id → *_listings.id (NOT NULL)
    # The cycle is broken during import by disabling trigger-based FK enforcement
    # via session_replication_role = 'replica' (see pg_import).
    "mcp_listings",
    "skill_listings",
    "hook_listings",
    "prompt_listings",
    "sandbox_listings",
    "agents",
    # Tier 2.5 - FK to listings/agents + users (version tables)
    "mcp_versions",
    "skill_versions",
    "hook_versions",
    "prompt_versions",
    "sandbox_versions",
    "agent_versions",
    # Tier 3 - FK to listings/users
    "mcp_validation_results",
    "mcp_downloads",
    "skill_downloads",
    "hook_downloads",
    "prompt_downloads",
    "sandbox_downloads",
    "submissions",
    "alert_rules",
    # Tier 4 - FK to agents/agent_versions
    "agent_download_records",
    "component_download_records",
    # Tier 6 - FK to agent_versions (polymorphic component_id)
    "agent_components",
    # Tier 7 - FK to users (polymorphic listing_id)
    "feedback",
    # Tier 8 - FK to alert_rules
    "alert_history",
    # Tier 9 - FK to agents + users (insight tables)
    "insight_meta_cache",
    "insight_session_facets",
    "insight_session_meta",
    "insight_reports",
]

JSONB_COLUMNS: dict[str, list[str]] = {
    "agents": ["model_config_json", "external_mcps", "supported_harnesses"],
    "agent_versions": [
        "model_config_json",
        "external_mcps",
        "supported_harnesses",
        "required_capabilities",
        "inferred_supported_harnesses",
        "harness_configs",
        "gaming_flags",
        "models_by_harness",
    ],
    "mcp_listings": ["tools_schema", "environment_variables", "supported_harnesses"],
    "mcp_versions": ["tools_schema", "environment_variables", "supported_harnesses", "args", "headers", "auto_approve"],
    "skill_listings": ["supported_harnesses", "target_agents", "triggers", "mcp_server_config", "activation_keywords"],
    "skill_versions": ["supported_harnesses", "target_agents", "triggers", "mcp_server_config", "activation_keywords"],
    "hook_listings": ["supported_harnesses", "handler_config", "input_schema", "output_schema"],
    "hook_versions": ["supported_harnesses", "handler_config", "input_schema", "output_schema"],
    "prompt_listings": ["variables", "model_hints", "tags", "supported_harnesses"],
    "prompt_versions": ["variables", "model_hints", "tags", "supported_harnesses"],
    "sandbox_listings": ["resource_limits", "allowed_mounts", "env_vars", "supported_harnesses"],
    "sandbox_versions": ["resource_limits", "runtime_config", "allowed_mounts", "env_vars", "supported_harnesses"],
    "agent_components": ["config_override"],
    "exporter_configs": ["config"],
    "insight_reports": ["metrics", "narrative", "aggregated_data"],
    "insight_session_facets": ["facets"],
    "insight_session_meta": ["meta"],
    "insight_meta_cache": ["session_metas"],
}

# ── ClickHouse telemetry constants ───────────────────────

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


class TableCfg(TypedDict):
    name: str
    engine: Literal["replacing", "mergetree"]
    time_col: str
    fk_cols: list[str]
    shard_expr: str
    base_shards: int
    physical_shards: int
    preserve_shard_group: bool


# ReplacingMergeTree shard expressions use only ORDER BY identity columns so
# every physical version of one logical row is processed by the same chunk.
CLICKHOUSE_TABLES: list[TableCfg] = [
    {
        "name": "session_events",
        "engine": "replacing",
        "time_col": "timestamp",
        "fk_cols": ["agent_id", "user_id"],
        "shard_expr": "sipHash64(project_id, user_id, harness, session_id)",
        "base_shards": 64,
        "physical_shards": 64,
        "preserve_shard_group": True,
    },
    {
        "name": "session_checkpoints",
        "engine": "replacing",
        "time_col": "updated_at",
        "fk_cols": ["user_id"],
        "shard_expr": "sipHash64(project_id, user_id, harness, session_id)",
        "base_shards": 16,
        "physical_shards": 1,
        "preserve_shard_group": True,
    },
    {
        "name": "session_stats_agg",
        "engine": "replacing",
        "time_col": "first_event_time",
        "fk_cols": ["agent_id", "user_id"],
        "shard_expr": "sipHash64(project_id, user_id, harness, session_id)",
        "base_shards": 64,
        "physical_shards": 64,
        "preserve_shard_group": True,
    },
    {
        "name": "layer_snapshots",
        "engine": "replacing",
        "time_col": "uploaded_at",
        "fk_cols": ["user_id"],
        "shard_expr": "sipHash64(project_id, user_id, hash)",
        "base_shards": 16,
        "physical_shards": 1,
        "preserve_shard_group": True,
    },
    {
        "name": "audit_log",
        "engine": "mergetree",
        "time_col": "timestamp",
        "fk_cols": ["actor_id"],
        "shard_expr": "sipHash64(event_id)",
        "base_shards": 1,
        "physical_shards": 1,
        "preserve_shard_group": False,
    },
    {
        "name": "security_events",
        "engine": "mergetree",
        "time_col": "timestamp",
        "fk_cols": [],
        "shard_expr": "sipHash64(event_id)",
        "base_shards": 1,
        "physical_shards": 1,
        "preserve_shard_group": False,
    },
    {
        "name": "webhook_deliveries",
        "engine": "mergetree",
        "time_col": "timestamp",
        "fk_cols": [],
        "shard_expr": "sipHash64(delivery_id)",
        "base_shards": 1,
        "physical_shards": 1,
        "preserve_shard_group": False,
    },
]

FK_PG_TABLE_MAP: dict[str, str] = {
    "agent_id": "agents",
    "user_id": "users",
    "actor_id": "users",
}

EPOCH_SENTINELS: set[str | None] = {None, "", "1970-01-01 00:00:00.000", "1970-01-01 00:00:00"}
