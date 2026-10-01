# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""The Postgres migration graph must have exactly one head.

Two heads make ``alembic upgrade head`` fail in observal-init (exit 255),
which only the Docker stack would otherwise show.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_has_exactly_one_head():
    server = Path(__file__).resolve().parents[1] / "observal-server"
    config = Config()
    config.set_main_option("script_location", str(server / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1, f"multiple Alembic heads: {sorted(heads)}"
