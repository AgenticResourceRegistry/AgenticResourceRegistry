# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Enforce release PR direction and backport provenance without GitHub credentials."""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.release import DEV_TITLE, RELEASE_TITLE, ReleaseError, commit_log, release_series, run, validate_target


def check(base: str, head: str, target: str) -> None:
    base = base.removeprefix("refs/heads/")
    if base == "main":
        if head.startswith(("release/", "prepare/", "backport/")):
            raise ReleaseError("Release and backport branches must never merge into main")
        if any(RELEASE_TITLE.fullmatch(commit.title) for commit in commit_log(f"origin/main..{target}")):
            raise ReleaseError("Release metadata commits must never merge into main")
        return
    release_series(base)
    changes = commit_log(f"origin/{base}..{target}")
    if not changes:
        return
    if run("git", "rev-list", "--merges", f"origin/{base}..{target}"):
        raise ReleaseError("Release branches require linear history")
    if any(RELEASE_TITLE.fullmatch(commit.title) for commit in changes):
        if len(changes) != 1:
            raise ReleaseError("Release preparation must contain exactly one metadata commit")
        validate_target(target, base, candidate=True)
        return
    for commit in changes:
        source = re.search(r"^\(cherry picked from commit ([0-9a-f]{40})\)$", commit.message, re.MULTILINE)
        if not source:
            raise ReleaseError("Release fixes must record cherry-pick -x provenance from main")
        run("git", "merge-base", "--is-ancestor", source[1], "origin/main")
        subject = run("git", "show", "-s", "--format=%s", source[1])
        if RELEASE_TITLE.fullmatch(subject) or DEV_TITLE.fullmatch(subject):
            raise ReleaseError("Do not backport release metadata")
        if ".release.toml" in run("git", "diff-tree", "--no-commit-id", "--name-only", "-r", commit.sha).splitlines():
            raise ReleaseError("Backports cannot modify the release manifest")


if __name__ == "__main__":
    try:
        check(os.environ["BASE_REF"], os.environ.get("HEAD_REF", ""), os.environ.get("PR_HEAD_SHA") or "HEAD")
    except (ReleaseError, KeyError) as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
