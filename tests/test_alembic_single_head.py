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


def test_repository_migration_checker_accepts_the_chain():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "check_migrations.py")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
