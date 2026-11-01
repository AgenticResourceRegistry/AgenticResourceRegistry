# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Disk-backed temporary storage for large multipart migration uploads."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from loguru import logger as optic


def configure_migration_upload_tempdir(artifact_root: str | Path) -> Path | None:
    """Spool multipart migration uploads beside the effective artifact root.

    Read-only root filesystems (or a missing home directory) leave the system
    temp directory in place instead of failing API startup.
    """
    upload_tempdir = Path(artifact_root).expanduser().parent / "migration_upload_tmp"
    try:
        upload_tempdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(upload_tempdir, 0o700)
    except OSError as error:
        optic.warning(
            "migration upload spool unavailable path={} error={}; using the system temp dir",
            upload_tempdir,
            error.strerror,
        )
        return None
    tempfile.tempdir = str(upload_tempdir)
    return upload_tempdir
