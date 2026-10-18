# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""The default Agent model shared by the server, CLI and web builder.

Every Agent has a model. ``auto`` means "let each harness use its own default
model"; it is the default when an author does not pick one, so a missing model
is resolved when the Agent is created instead of blocking publication later.
"""

from __future__ import annotations

AUTO_MODEL = "auto"


def normalize_model_name(value: object) -> str:
    """Return the stored model name, using ``auto`` for a missing or blank value."""
    if not isinstance(value, str) or not value.strip():
        return AUTO_MODEL
    return value.strip()


def is_auto_model(value: object) -> bool:
    """True when the model leaves the choice to each harness."""
    return normalize_model_name(value).lower() == AUTO_MODEL
