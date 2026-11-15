# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Server version detection utility.

Shared between the version middleware and the /config/version endpoint.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from importlib.metadata import version as pkg_version
from pathlib import Path


@lru_cache(maxsize=1)
def get_server_version() -> str:
    """Get the server version string.

    Resolution order:
    1. importlib.metadata (installed package)
    2. pyproject.toml (running from source in Docker)
    3. "dev" fallback
    """
    try:
        return pkg_version("observal-server")
    except Exception:
        pass
    try:
        pyproject = Path(__file__).resolve().parent / "pyproject.toml"
        if pyproject.exists():
            m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(), re.M)
            if m:
                return m.group(1)
    except Exception:
        pass
    return "dev"


_COMMIT_RE = re.compile(r"[0-9a-f]{40}")


@lru_cache(maxsize=1)
def get_build_commit() -> str | None:
    """Git commit this server image was built from, when the build recorded one.

    Development builds share a version across many commits, so the CLI needs the
    commit to build a matching copy of itself. Set through the
    ``OBSERVAL_BUILD_COMMIT`` build argument in ``docker/Dockerfile.api``.
    """
    value = os.environ.get("OBSERVAL_BUILD_COMMIT", "").strip().lower()
    return value if _COMMIT_RE.fullmatch(value) else None
