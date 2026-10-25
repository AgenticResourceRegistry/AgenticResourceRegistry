# SPDX-FileCopyrightText: 2026 SrihariLegend <sriharilegend23@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Helpers for multiprocessing tests that use package-qualified test targets."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from multiprocessing.process import BaseProcess

_REPO_ROOT = str(Path(__file__).resolve().parents[1])


def start_spawned_test_process(process: BaseProcess) -> None:
    """Prioritize the root tests package while spawn imports the target module."""
    original_path = sys.path.copy()
    sys.path[:] = [_REPO_ROOT, *(entry for entry in original_path if entry != _REPO_ROOT)]
    try:
        process.start()
    finally:
        sys.path[:] = original_path
