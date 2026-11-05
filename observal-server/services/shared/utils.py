# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared utility functions used across multiple server-side services.

Single source of truth - any helper that was duplicated across
agent_builder.py, config_generator.py, agent_config_generator.py,
or skill_config_generator.py lives here instead.
"""

from __future__ import annotations

import re

_SAFE_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")


def registry_item_slug(item) -> str:
    """Return a listing's stable slug, falling back for transient/test objects."""
    slug = getattr(item, "slug", None)
    return slug if isinstance(slug, str) and slug else item.name


def sanitize_name(name: str) -> str:
    """Normalise an arbitrary string to a safe identifier (alphanumeric, hyphens, underscores).

    Returns *name* unchanged when it already consists entirely of safe characters.
    Otherwise replaces every unsafe character with a hyphen.

    Raises TypeError if *name* is not a str.
    """
    if not isinstance(name, str):
        raise TypeError(f"sanitize_name expects str, got {type(name).__name__!r}")
    if _SAFE_NAME.fullmatch(name):
        return name
    return re.sub(r"[^a-zA-Z0-9_-]", "-", name)
