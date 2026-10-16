# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""The Postgres migration graph must have exactly one head.

Two heads make ``alembic upgrade head`` fail in observal-init (exit 255),
which only the Docker stack would otherwise show.
"""

import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_has_exactly_one_head():
    server = Path(__file__).resolve().parents[1] / "observal-server"
    config = Config()
    config.set_main_option("script_location", str(server / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1, f"multiple Alembic heads: {sorted(heads)}"


def test_repository_migration_checker_accepts_the_joined_graph():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "check_migrations.py")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "046_component_insights_merge" in result.stdout


def test_component_insights_head_preserves_both_migration_histories():
    server = Path(__file__).resolve().parents[1] / "observal-server"
    config = Config()
    config.set_main_option("script_location", str(server / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_current_head() == "046_component_insights_merge"
    merge = scripts.get_revision(scripts.get_current_head())
    assert set(merge.down_revision) == {"034_hook_component_reports", "045_usage_ping_default_on"}
    history = {revision.revision for revision in scripts.iterate_revisions(merge.revision, "030_agent_share_manifests")}
    assert {
        "029_projection_generation",
        "034_hook_component_reports",
        "029_skill_version_extra_files",
        "044_review_orphan_authors",
    } <= history
