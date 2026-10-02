# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""The layer index keeps where each agent hook is placed, defaulting to the agent file."""

from __future__ import annotations

from pathlib import Path

from services.layer_components.normalizer import normalize_snapshot

AGENT = "00000000-0000-4000-8000-000000000001"
HOOK = "77777777-7777-4777-8777-777777777777"
PIN = {
    "type": "hook",
    "id": HOOK,
    "name": "probe-fail",
    "version": "1.0.0",
    "scope": "project",
    "local_name": "probe-fail",
}
PINS = {
    "schema_version": 2,
    "standalone": [],
    "agents": [
        {
            "id": AGENT,
            "name": "probe-agent",
            "version": "1.0.0",
            "harness": "claude-code",
            "scope": "project",
            "components": [PIN],
        }
    ],
}
RECORD = {
    "harness": "claude-code",
    "component_id": HOOK,
    "alias": "probe-fail",
    "scope": "project",
    "parent_agent_id": AGENT,
    "status": "verified",
    "location_sha256": "a" * 64,
    "hook_agent": "probe-agent",
}


def _placement(**record) -> tuple[str, str]:
    (occurrence,) = normalize_snapshot(PINS, {"hook_verifications": [RECORD | record]})
    return occurrence.binding_agent, occurrence.binding_placement


def test_a_gated_settings_placement_is_indexed():
    assert _placement(hook_placement="gated_settings") == ("probe-agent", "gated_settings")


def test_every_other_record_is_a_frontmatter_placement():
    assert _placement() == ("probe-agent", "frontmatter"), "records made before placement existed"
    assert _placement(hook_placement="frontmatter") == ("probe-agent", "frontmatter")
    assert _placement(hook_placement="anything-else") == ("probe-agent", "frontmatter")
    assert _placement(hook_placement=None) == ("probe-agent", "frontmatter")


def test_placement_needs_a_valid_binding_agent():
    assert _placement(hook_agent="", hook_placement="gated_settings") == ("", "frontmatter")
    assert _placement(hook_agent="bad name", hook_placement="gated_settings") == ("", "frontmatter")


def test_the_column_defaults_existing_rows_to_frontmatter():
    sql = (
        Path(__file__).resolve().parents[1] / "observal-server/clickhouse/migrations/010_hook_placement.sql"
    ).read_text()
    assert (
        "ALTER TABLE layer_components ADD COLUMN IF NOT EXISTS binding_placement LowCardinality(String) "
        "DEFAULT 'frontmatter';" in sql
    )
