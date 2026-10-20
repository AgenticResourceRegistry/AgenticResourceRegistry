# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Protect pre-push from Docker requirements and working-tree rewrites."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_mutating_and_docker_hooks_are_commit_only():
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    hooks = {hook["id"]: hook for repo in config["repos"] for hook in repo["hooks"]}
    for name in ("ruff", "ruff-format", "trailing-whitespace", "end-of-file-fixer", "hadolint-docker"):
        assert hooks[name]["stages"] == ["pre-commit"], name


def test_readme_has_no_trailing_whitespace():
    lines = (ROOT / "README.md").read_text().splitlines()
    assert all(line == line.rstrip() for line in lines)
