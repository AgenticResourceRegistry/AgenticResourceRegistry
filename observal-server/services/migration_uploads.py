# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Disk-backed temporary storage for large multipart migration uploads."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def configure_migration_upload_tempdir(artifact_root: str | Path) -> Path:
    """Spool multipart migration uploads beside the effective artifact root."""
    upload_tempdir = Path(artifact_root).expanduser().parent / "migration_upload_tmp"
    upload_tempdir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(upload_tempdir, 0o700)
    tempfile.tempdir = str(upload_tempdir)
    return upload_tempdir
