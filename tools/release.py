# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Cut release lines, prepare channel releases, and backport fixes."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG_ANCHOR = "All notable changes to this project will be documented in this file.\n\n"
CATEGORIES = (
    "Security",
    "Features",
    "Fixes",
    "Performance",
    "Documentation",
    "Maintenance",
)
VERSION_FILES = (
    "pyproject.toml",
    "observal-server/pyproject.toml",
    "web/package.json",
    "packages/pi-extension/package.json",
)
RELEASE_FILES = (
    *VERSION_FILES,
    "uv.lock",
    "observal-server/uv.lock",
    "CHANGELOG.md",
    ".release.toml",
    ".github/release-notes.md",
)
RELEASE_TITLE = re.compile(r"chore\(release\): v\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?")
DEV_TITLE = re.compile(r"chore: start \d+\.\d+ development")


class ReleaseError(RuntimeError):
    pass


@dataclass
class Contributor:
    name: str
    login: str | None = None
    first_time: bool = False

    @property
    def label(self) -> str:
        return f"@{self.login}" if self.login else self.name


@dataclass
class Change:
    commits: list[str]
    title: str
    author_name: str
    author_email: str
    pr: int | None = None
    url: str | None = None
    body: str = ""
    labels: list[str] = field(default_factory=list)
    contributor: Contributor | None = None
    category: str = "Maintenance"
    include_in_notes: bool = True
    highlight: bool = False
    breaking: bool = False

    @property
    def reference(self) -> str:
        if self.pr and self.url:
            return f"[#{self.pr}]({self.url})"
        sha = self.commits[-1]
        return f"[{sha[:7]}](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/commit/{sha})"


@dataclass(frozen=True)
class Commit:
    sha: str
    author_name: str
    author_email: str
    title: str
    message: str


def run(*args: str, cwd: Path | None = None, capture: bool = True) -> str:
    result = subprocess.run(args, cwd=cwd or ROOT, check=False, text=True, capture_output=capture)
    if result.returncode:
        detail = (result.stderr or "").strip() or (result.stdout or "").strip() or f"exit code {result.returncode}"
        raise ReleaseError(f"{' '.join(args)} failed: {detail}")
    return result.stdout.strip() if capture else ""


def require(command: str) -> None:
    try:
        run(command, "--version")
    except (FileNotFoundError, ReleaseError) as exc:
        raise ReleaseError(f"Required command not available: {command}") from exc


def repository(remote: str) -> tuple[str, str]:
    url = run("git", "remote", "get-url", remote)
    match = re.search(r"github\.com(?::|/)([^/]+)/([^/]+?)(?:\.git)?$", url)
    if not match:
        raise ReleaseError(f"Cannot determine GitHub repository from remote {remote}: {url}")
    return match.group(1), match.group(2)


GH_ATTEMPTS = 4


def gh_json(repo: str, endpoint: str) -> object:
    for attempt in range(1, GH_ATTEMPTS + 1):
        try:
            return json.loads(run("gh", "api", f"repos/{repo}/{endpoint}"))
        except ReleaseError as exc:
            if attempt == GH_ATTEMPTS:
                raise
            print(f"gh api {endpoint} failed (attempt {attempt}/{GH_ATTEMPTS}), retrying: {exc}", file=sys.stderr)
            time.sleep(2**attempt)


def parse_version(version: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        raise ReleaseError(f"Latest stable tag is not semantic: v{version}")
    return tuple(map(int, match.groups()))


def bump_version(version: str, bump: str) -> str:
    major, minor, patch = parse_version(version)
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "feature":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def validate_version_channel(version: str, channel: str) -> None:
    version_key(version)
    if channel not in {"alpha", "beta", "rc", "stable"}:
        raise ReleaseError(f"Invalid release channel: {channel}")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?", version):
        raise ReleaseError(f"Invalid cross-registry version: {version}")
    if (channel == "stable") != ("-" not in version) or (channel != "stable" and f"-{channel}." not in version):
        raise ReleaseError(f"Version {version} does not match the {channel} channel")


def infer_category(title: str, labels: list[str]) -> str:
    normalized = {label.lower() for label in labels}
    if normalized & {"security", "area: security", "type: security"}:
        return "Security"
    if normalized & {"performance", "area: performance", "type: performance"}:
        return "Performance"
    if normalized & {"documentation", "docs", "type: docs"}:
        return "Documentation"
    commit_type = re.match(r"([a-z]+)(?:\([^)]*\))?!?:", title.lower())
    kind = commit_type.group(1) if commit_type else ""
    return {
        "feat": "Features",
        "fix": "Fixes",
        "perf": "Performance",
        "docs": "Documentation",
    }.get(kind, "Maintenance")


def clean_title(title: str) -> str:
    return re.sub(r"^[a-z]+(?:\([^)]*\))?!?:\s*", "", title, flags=re.IGNORECASE).strip().rstrip(".")


def is_breaking(title: str, labels: list[str], body: str = "") -> bool:
    return bool(
        re.match(r"^[a-z]+(?:\([^)]*\))?!:", title, flags=re.IGNORECASE)
        or any("breaking" in label.lower() for label in labels)
        or "BREAKING CHANGE:" in body
    )


def commit_log(revision_range: str) -> list[Commit]:
    raw = run("git", "log", "--reverse", "--format=%H%x1f%an%x1f%ae%x1f%s%x1f%B%x1e", revision_range)
    commits = []
    for record in raw.split("\x1e"):
        fields = record.strip().split("\x1f", 4)
        if len(fields) == 5:
            commits.append(Commit(*fields))
    return commits


def discover_changes(repo: str, previous_ref: str, branch: str, base: str = "main") -> list[Change]:
    changes: list[Change] = []
    seen_prs: set[int] = set()
    commits = commit_log(f"{previous_ref}..{branch}")
    with ThreadPoolExecutor(max_workers=8) as pool:
        all_pulls = list(pool.map(lambda c: gh_json(repo, f"commits/{c.sha}/pulls"), commits))
    for commit, pulls in zip(commits, all_pulls, strict=True):
        matching = [pr for pr in pulls if pr.get("merged_at") and pr.get("base", {}).get("ref") in {"main", base}]
        pr = max(matching, key=lambda item: item["merged_at"]) if matching else None
        pr_number = pr["number"] if pr else None
        if changes and pr_number is not None and changes[-1].pr == pr_number:
            changes[-1].commits.append(commit.sha)
            continue
        if pr_number in seen_prs:
            raise ReleaseError(f"PR #{pr_number} is not contiguous in git history")
        if pr_number:
            seen_prs.add(pr_number)
        labels = [label["name"] for label in pr.get("labels", [])] if pr else []
        title = pr["title"] if pr else commit.title
        if RELEASE_TITLE.fullmatch(title):
            continue
        user = pr.get("user") if pr else None
        login = user.get("login") if user else None
        association = pr.get("author_association", "") if pr else ""
        change = Change(
            commits=[commit.sha],
            title=title,
            author_name=commit.author_name,
            author_email=commit.author_email,
            pr=pr_number,
            url=pr.get("html_url") if pr else None,
            body=pr.get("body") or "" if pr else commit.message,
            labels=labels,
            contributor=Contributor(
                name=user.get("login", commit.author_name) if user else commit.author_name,
                login=login,
                first_time=association == "FIRST_TIME_CONTRIBUTOR",
            ),
        )
        change.category = infer_category(title, labels)
        change.include_in_notes = change.category != "Maintenance"
        change.breaking = is_breaking(title, labels, change.body)
        changes.append(change)
    return changes


def apply_note_overrides(
    changes: list[Change],
    include: tuple[int, ...] = (),
    exclude: tuple[int, ...] = (),
    highlight: tuple[int, ...] = (),
    breaking: tuple[int, ...] = (),
    titles: dict[int, str] | None = None,
    categories: dict[int, str] | None = None,
) -> None:
    titles, categories = titles or {}, categories or {}
    by_pr = {change.pr: change for change in changes if change.pr}
    for numbers in (include, exclude, highlight, breaking, titles, categories):
        unknown = sorted(set(numbers) - by_pr.keys())
        if unknown:
            raise ReleaseError(f"PRs not in this release: {', '.join(f'#{n}' for n in unknown)}")
    if set(include) & set(exclude):
        raise ReleaseError("--include-pr and --exclude-pr overlap")
    bad = sorted(set(categories.values()) - set(CATEGORIES))
    if bad:
        raise ReleaseError(f"Unknown category {', '.join(bad)}; choose from {', '.join(CATEGORIES)}")
    for number, title in titles.items():
        by_pr[number].title = title
    for number, category in categories.items():
        by_pr[number].category = category
        by_pr[number].include_in_notes = by_pr[number].include_in_notes or category != "Maintenance"
    for number in include:
        by_pr[number].include_in_notes = True
    for number in exclude:
        by_pr[number].include_in_notes = False
    for number in highlight:
        by_pr[number].include_in_notes = True
        by_pr[number].highlight = True
    for number in breaking:
        by_pr[number].breaking = True


def coauthors(commits: list[Commit]) -> list[Contributor]:
    contributors: list[Contributor] = []
    pattern = re.compile(r"^Co-authored-by:\s*(.+?)\s*<([^>]+)>$", re.IGNORECASE | re.MULTILINE)
    for commit in commits:
        for name, email in pattern.findall(commit.message):
            login_match = re.search(r"(?:\d+\+)?([^@+]+)@users\.noreply\.github\.com$", email)
            contributors.append(Contributor(name=name.strip(), login=login_match.group(1) if login_match else None))
    return contributors


def all_contributors(changes: list[Change], commits: list[Commit]) -> list[Contributor]:
    result: dict[str, Contributor] = {}
    commit_authors = []
    for commit in commits:
        login_match = re.search(r"(?:\d+\+)?([^@+]+)@users\.noreply\.github\.com$", commit.author_email)
        commit_authors.append(Contributor(name=commit.author_name, login=login_match.group(1) if login_match else None))
    candidates = [*(change.contributor for change in changes), *commit_authors, *coauthors(commits)]
    for contributor in candidates:
        if not contributor:
            continue
        raw = contributor.login or contributor.name
        key = re.sub(r"[^a-z0-9]", "", raw, flags=re.IGNORECASE).lower()
        if raw.lower().endswith("[bot]") or key in {"dependabot", "githubactions"}:
            continue
        existing = result.get(key)
        if existing:
            existing.first_time |= contributor.first_time
            if contributor.login:
                existing.login = contributor.login
        else:
            result[key] = contributor
    return sorted(result.values(), key=lambda item: item.label.lower())


def migration_changes(changes: list[Change]) -> list[Change]:
    result = []
    for change in changes:
        paths = set()
        for sha in change.commits:
            paths.update(run("git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha).splitlines())
        if any(
            path.startswith("observal-server/alembic/versions/")
            or path.startswith("observal-server/clickhouse/migrations/")
            for path in paths
        ):
            result.append(change)
    return result


def grouped(changes: list[Change]) -> dict[str, list[Change]]:
    return {
        category: [change for change in changes if change.include_in_notes and change.category == category]
        for category in CATEGORIES
    }


def render_entries(changes: list[Change]) -> str:
    return "\n".join(f"- {clean_title(change.title)} ({change.reference})" for change in changes)


def render_changelog_section(version: str, date: str, changes: list[Change]) -> str:
    lines = [f"## [{version}] - {date}"]
    selected = [change for change in changes if change.include_in_notes]
    if not selected:
        return "\n".join([*lines, "", "No user-facing changes."])
    for category, items in grouped(changes).items():
        if items:
            lines.extend(("", f"### {category}", "", render_entries(items)))
    return "\n".join(lines)


def render_release_notes(
    version: str,
    previous_tag: str,
    source_sha: str,
    changes: list[Change],
    contributors: list[Contributor],
) -> str:
    selected = [change for change in changes if change.include_in_notes]
    highlights = [change for change in selected if change.highlight]
    breaking = [change for change in selected if change.breaking]
    lines = [
        "<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->",
        # REUSE-IgnoreStart
        "<!-- SPDX-License-Identifier: Apache-2.0 -->",
        # REUSE-IgnoreEnd
        "",
        f"This release includes {len(changes)} change groups through `{source_sha[:7]}`.",
    ]
    if highlights:
        lines.extend(("", "## Highlights", "", render_entries(highlights)))
    if breaking:
        lines.extend(("", "## Breaking changes", "", render_entries(breaking)))
    for category, items in grouped(changes).items():
        regular = [item for item in items if item not in highlights and item not in breaking]
        if regular:
            lines.extend(("", f"## {category}", "", render_entries(regular)))
    lines.extend(
        (
            "",
            "## Verify this release",
            "",
            "Verify checksums, artifact provenance, and the signed release tag using the "
            "[release verification guide](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/blob/main/"
            "docs/security/release-verification.md).",
            "",
            "## Full comparison",
            "",
            f"[{previous_tag}...v{version}](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/compare/{previous_tag}...v{version})",
            "",
        )
    )
    return "\n".join(lines)


def prepend_changelog(existing: str, section: str, version: str) -> str:
    if re.search(rf"^## \[{re.escape(version)}\]", existing, re.MULTILINE):
        raise ReleaseError(f"CHANGELOG.md already contains version {version}")
    position = existing.find(CHANGELOG_ANCHOR)
    if position < 0:
        raise ReleaseError("CHANGELOG.md introduction was not found")
    position += len(CHANGELOG_ANCHOR)
    return existing[:position] + section.rstrip() + "\n\n" + existing[position:]


def set_version(path: Path, version: str) -> None:
    text = path.read_text()
    if path.suffix == ".toml":
        project = re.search(r"(?ms)^\[project\]\s*$.*?(?=^\[|\Z)", text)
        if not project:
            raise ReleaseError(f"Could not find [project] in {path}")
        block, count = re.subn(
            r'^(version\s*=\s*")[^"]+("\s*)$', rf"\g<1>{version}\2", project.group(), flags=re.MULTILINE
        )
        updated = text[: project.start()] + block + text[project.end() :]
    else:
        data = json.loads(text)
        if not isinstance(data, dict) or "version" not in data:
            raise ReleaseError(f"Could not find a top-level version in {path}")
        updated, count = re.subn(
            r'^( {2}"version"\s*:\s*")[^"]+("\s*,?)$', rf"\g<1>{version}\2", text, flags=re.MULTILINE
        )
    if count != 1:
        raise ReleaseError(f"Could not update exactly one version in {path}")
    path.write_text(updated)


def write_manifest(
    path: Path,
    version: str,
    channel: str,
    previous_tag: str,
    source_sha: str,
    changes: list[Change],
) -> None:
    validate_version_channel(version, channel)
    branch = "release/" + ".".join(version.split(".")[:2])
    prs = ", ".join(str(change.pr) for change in changes if change.pr)
    path.write_text(
        "# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>\n"
        # REUSE-IgnoreStart
        "# SPDX-License-Identifier: Apache-2.0\n\n"
        # REUSE-IgnoreEnd
        f'version = "{version}"\n'
        f'channel = "{channel}"\n'
        f'previous_tag = "{previous_tag}"\n'
        f'source_sha = "{source_sha}"\n'
        f'branch = "{branch}"\n'
        f'created_at = "{datetime.now(UTC).isoformat()}"\n'
        f"commit_count = {sum(len(change.commits) for change in changes)}\n"
        f"included_prs = [{prs}]\n"
    )


def pr_body(version: str, previous_tag: str, source_sha: str, changes: list[Change], preview: str) -> str:
    branch = "release/" + ".".join(version.split(".")[:2])
    return f"""## Purpose / Description
Prepare v{version} on `{branch}` from `{previous_tag}..{source_sha}`.

## Approach
Includes all {len(changes)} change groups on the release branch. Only release metadata changes.
Rebase-merge into `{branch}`, never into main. If the base advances, regenerate this PR.

## How Has This Been Tested?
Local version and ancestry checks; required branch CI and release preflight validate the merged commit.
Publication completes only after artifact verification.

## Release preview
{preview}
"""


def release_series(branch: str) -> str:
    if not re.fullmatch(r"release/(0|[1-9]\d*)\.(0|[1-9]\d*)", branch):
        raise ReleaseError("Expected release/X.Y, never main or a patch/channel branch")
    return branch.removeprefix("release/")


def version_key(version: str) -> tuple[int, ...]:
    match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(alpha|beta|rc)\.([1-9]\d*))?", version)
    if not match:
        raise ReleaseError(f"Invalid release version: {version}")
    major, minor, patch, channel, serial = match.groups()
    return int(major), int(minor), int(patch), {"alpha": 0, "beta": 1, "rc": 2, None: 3}[channel], int(serial or 0)


def version_tags(ref: str | None = None) -> list[str]:
    args = ["git", "tag", "--list", "v[0-9]*"]
    if ref:
        args.extend(("--merged", ref))
    tags = run(*args).splitlines()
    return [tag for tag in tags if re.fullmatch(r"v\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?", tag)]


def latest_tag(ref: str | None = None) -> str:
    tags = version_tags(ref)
    if not ref:
        tags = [tag for tag in tags if "-" not in tag]
    if not tags:
        raise ReleaseError("No release tag exists in the requested history")
    return max(tags, key=lambda tag: version_key(tag[1:]))


def validate_progression(version: str, channel: str, branch: str, tags: list[str]) -> None:
    validate_version_channel(version, channel)
    series = release_series(branch)
    key = version_key(version)
    if version.split("-")[0].rsplit(".", 1)[0] != series:
        raise ReleaseError(f"Version {version} does not belong to {branch}")
    line = [tag for tag in tags if tag[1:].split("-")[0].rsplit(".", 1)[0] == series]
    if any(version_key(tag[1:]) >= key for tag in line):
        raise ReleaseError("Version must advance beyond every existing release on this line")


def distribution_tag(version: str, channel: str, tags: list[str]) -> str:
    validate_version_channel(version, channel)
    if channel != "stable":
        alias = {"rc": "next", "beta": "beta", "alpha": "alpha"}[channel]
        newer = any(f"-{channel}." in tag and version_key(tag[1:]) > version_key(version) for tag in tags)
        return f"{alias}-{version.split('-')[0].rsplit('.', 1)[0]}" if newer else alias
    newer = any("-" not in tag and version_key(tag[1:]) > version_key(version) for tag in tags)
    return "lts-" + version.rsplit(".", 1)[0] if newer else "latest"


def next_version(branch: str, channel: str, tags: list[str]) -> str:
    series = release_series(branch)
    line = [tag[1:] for tag in tags if tag[1:].split("-")[0].rsplit(".", 1)[0] == series]
    previous = max(line, key=version_key) if line else None
    core = previous.split("-")[0] if previous else f"{series}.0"
    if previous and "-" not in previous:
        core = bump_version(core, "patch")
    serial = 1
    while f"v{core}-{channel}.{serial}" in tags:
        serial += 1
    version = core if channel == "stable" else f"{core}-{channel}.{serial}"
    validate_progression(version, channel, branch, tags)
    return version


def ensure_preflight(upstream: str, expected: str | None = None) -> str:
    for command in ("git", "gh"):
        require(command)
    if run("git", "status", "--porcelain"):
        raise ReleaseError("Working tree is dirty. Commit or stash changes first.")
    branch = run("git", "branch", "--show-current")
    if expected and branch != expected:
        raise ReleaseError(f"Must run from {expected}")
    if not expected:
        release_series(branch)
    run("gh", "auth", "status")
    run("git", "fetch", upstream, branch, "--tags", "--no-prune-tags")
    if run("git", "rev-parse", "HEAD") != run("git", "rev-parse", f"{upstream}/{branch}"):
        raise ReleaseError(f"Local {branch} must exactly match {upstream}/{branch}")
    return branch


def next_dev_version(series: str) -> str:
    major, minor, _ = parse_version(f"{series}.0")
    return f"{major}.{minor + 1}.0-dev.0"


def bump_main(series: str, upstream: str, fork: str) -> None:
    """Open a PR advancing main to the next minor dev version, as AnkiDroid does after branching."""
    version = next_dev_version(series)
    branch = f"chore/start-{version}"
    worktree = ROOT / ".worktrees" / f"main-{version}"
    if worktree.exists() or run("git", "branch", "--list", branch):
        raise ReleaseError(f"Main bump branch or worktree already exists: {branch}")
    repo = "/".join(repository(upstream))
    owner, _ = repository(fork)
    title = f"chore: start {version.rsplit('.', 2)[0]} development"
    run("git", "worktree", "add", "-b", branch, str(worktree), f"{upstream}/main", capture=False)
    try:
        for relative in VERSION_FILES:
            set_version(worktree / relative, version)
        run("uv", "lock", cwd=worktree, capture=False)
        run("uv", "lock", cwd=worktree / "observal-server", capture=False)
        run("git", "add", *VERSION_FILES, "uv.lock", "observal-server/uv.lock", cwd=worktree)
        run("git", "commit", "-s", "-m", title, "-m", f"Advance main to {version}.", cwd=worktree, capture=False)
        run("git", "push", fork, branch, cwd=worktree, capture=False)
        print(
            run(
                "gh", "pr", "create", "--repo", repo, "--head", f"{owner}:{branch}", "--base", "main",
                "--title", title, "--body", f"Advance main to {version} after cutting release/{series}.",
                cwd=worktree,
            )
        )  # fmt: skip
    except Exception:
        print(f"Main bump worktree preserved for recovery: {worktree}", file=sys.stderr)
        raise
    else:
        run("git", "worktree", "remove", str(worktree), capture=False)


def cut(series: str, upstream: str, fork: str) -> None:
    branch = f"release/{series}"
    release_series(branch)
    ensure_preflight(upstream, "main")
    if any(version_key(tag[1:])[:2] >= version_key(f"{series}.0")[:2] for tag in version_tags()):
        raise ReleaseError("Cut a new minor line above all existing release tags; maintain existing lines in place")
    if run("git", "ls-remote", "--heads", upstream, f"refs/heads/{branch}"):
        raise ReleaseError(f"Release line already exists: {branch}")
    # An empty expected ref prevents a racing cut from updating another line.
    run("git", "push", f"--force-with-lease=refs/heads/{branch}:", upstream, f"HEAD:refs/heads/{branch}", capture=False)
    print(f"Created {branch} at {run('git', 'rev-parse', 'HEAD')}; nothing published")
    bump_main(series, upstream, fork)


def status(upstream: str) -> None:
    run("git", "fetch", upstream, "--tags", "--no-prune-tags")
    branch = run("git", "branch", "--show-current")
    if branch == "main":
        print(run("git", "ls-remote", "--heads", upstream, "refs/heads/release/*"))
        return
    release_series(branch)
    tag = latest_tag("HEAD")
    print(f"{branch}: latest reachable tag {tag}")
    print(run("git", "log", "--oneline", f"{tag}..HEAD"))


def backport(number: int, target: str, upstream: str, fork: str) -> None:
    series = release_series(target)
    ensure_preflight(upstream, "main")
    repo = "/".join(repository(upstream))
    pull = gh_json(repo, f"pulls/{number}")
    if not isinstance(pull, dict) or not pull.get("merged_at") or pull.get("base", {}).get("ref") != "main":
        raise ReleaseError("Backports require a merged main PR")
    if RELEASE_TITLE.fullmatch(pull["title"]) or DEV_TITLE.fullmatch(pull["title"]):
        raise ReleaseError("Do not backport release metadata")
    # GitHub reports original PR hashes even after a rebase merge. Read the
    # equivalent commits from main, ending at its recorded merge commit.
    count = pull["commits"]
    if not isinstance(count, int) or count < 1:
        raise ReleaseError("PR has no commits")
    tip = pull["merge_commit_sha"]
    if not isinstance(tip, str) or not re.fullmatch(r"[0-9a-f]{40}", tip):
        raise ReleaseError("PR has invalid merge commit")
    run("git", "merge-base", "--is-ancestor", tip, f"{upstream}/main")
    commits = run("git", "rev-list", "--reverse", "--first-parent", f"{tip}~{count}..{tip}").splitlines()
    pages = json.loads(run("gh", "api", "--paginate", "--slurp", f"repos/{repo}/pulls/{number}/commits"))
    original = [commit["commit"]["message"].strip() for page in pages for commit in page]
    messages = [run("git", "show", "-s", "--format=%B", sha) for sha in commits]
    if original != messages:
        raise ReleaseError("Cannot identify rebase-merged PR commits exactly; inspect the merge manually")
    if run("git", "rev-list", "--merges", f"{tip}~{count}..{tip}"):
        raise ReleaseError("Backports require linear rebase-merged PRs")
    run("git", "fetch", upstream, target)
    branch = f"backport/{series}/{number}"
    worktree = ROOT / ".worktrees" / f"backport-{series}-{number}"
    run("git", "worktree", "add", "-b", branch, str(worktree), f"{upstream}/{target}", capture=False)
    try:
        run("git", "cherry-pick", "-x", *commits, cwd=worktree, capture=False)
        run("git", "push", fork, branch, cwd=worktree, capture=False)
        owner, _ = repository(fork)
        body = f"Backport-of: #{number}\nTarget: {target}\nOriginal-commits:\n" + "\n".join(
            f"- {sha}" for sha in commits
        )
        print(
            run(
                "gh",
                "pr",
                "create",
                "--repo",
                repo,
                "--head",
                f"{owner}:{branch}",
                "--base",
                target,
                "--title",
                f"[{series}] {pull['title']}",
                "--body",
                body,
                cwd=worktree,
            )
        )
    except Exception:
        print(f"Backport worktree preserved for recovery: {worktree}", file=sys.stderr)
        raise
    else:
        run("git", "worktree", "remove", str(worktree), capture=False)


def resolve_release_push(before: str, after: str, branch: str) -> str | None:
    release_series(branch)
    if before == "0" * 40:
        return None  # Cutting a line is not a release.
    for sha in (before, after):
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ReleaseError("Push commits must be full SHAs")
    run("git", "merge-base", "--is-ancestor", before, after)
    commits = commit_log(f"{before}..{after}")
    releases = [commit for commit in commits if RELEASE_TITLE.fullmatch(commit.title)]
    manifests = set(run("git", "log", "--format=%H", f"{before}..{after}", "--", ".release.toml").splitlines())
    if not releases and not manifests:
        return None
    if len(releases) != 1 or manifests != {releases[0].sha}:
        raise ReleaseError("push contains an ambiguous or malformed release change")
    return releases[0].sha


def validate_target(target: str, branch: str, upstream: str = "origin", *, candidate: bool = False) -> dict[str, str]:
    release_series(branch)
    if not re.fullmatch(r"[0-9a-f]{40}", target):
        raise ReleaseError("Target must be a full commit SHA")
    if not candidate:
        run("git", "merge-base", "--is-ancestor", target, f"{upstream}/{branch}")
    manifest = tomllib.loads(run("git", "show", f"{target}:.release.toml"))
    version, channel = manifest["version"], manifest["channel"]
    if manifest["branch"] != branch:
        raise ReleaseError("Release manifest branch does not match workflow branch")
    tags = version_tags()
    if f"v{version}" in tags:
        if run("git", "rev-parse", f"v{version}^{{commit}}") != target:
            raise ReleaseError("Existing tag points to a different commit")
        tags.remove(f"v{version}")
    validate_progression(version, channel, branch, tags)
    if run("git", "show", "-s", "--format=%s", target) != f"chore(release): v{version}":
        raise ReleaseError("Invalid release commit subject")
    source = manifest["source_sha"]
    if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ReleaseError("Invalid source SHA")
    if candidate and source != run("git", "rev-parse", f"{upstream}/{branch}"):
        raise ReleaseError("Release PR is stale; regenerate it from the latest release branch")
    if run("git", "show", "-s", "--format=%P", target).split() != [source]:
        raise ReleaseError("Release source must be its only parent; regenerate stale release PRs")
    previous = manifest["previous_tag"]
    if previous not in tags:
        raise ReleaseError("Previous release tag is missing or invalid")
    run("git", "merge-base", "--is-ancestor", previous, source)
    if latest_tag(source) != previous:
        raise ReleaseError("Previous tag is not the latest reachable release")
    changed = set(run("git", "diff", "--name-only", source, target).splitlines())
    if changed - set(RELEASE_FILES):
        raise ReleaseError(f"Release commit changed forbidden files: {sorted(changed - set(RELEASE_FILES))}")
    for relative in VERSION_FILES:
        text = run("git", "show", f"{target}:{relative}")
        actual = (
            tomllib.loads(text)["project"]["version"] if relative.endswith(".toml") else json.loads(text)["version"]
        )
        if actual != version:
            raise ReleaseError(f"Release versions disagree: {relative}")
    if f"## [{version}]" not in run("git", "show", f"{target}:CHANGELOG.md"):
        raise ReleaseError("Release version missing from changelog")
    if f"{previous}...v{version}" not in run("git", "show", f"{target}:.github/release-notes.md"):
        raise ReleaseError("Release notes incomplete")
    key, old = version_key(version), version_key(previous[1:])
    bump = "major" if key[0] != old[0] else "feature" if key[1] != old[1] else "patch"
    python_version = re.sub(r"-(alpha|beta|rc)\.", lambda m: {"alpha": "a", "beta": "b", "rc": "rc"}[m[1]], version)
    dist_tag = distribution_tag(version, channel, tags)
    return dict(
        version=version,
        channel=channel,
        python_version=python_version,
        bump_type=bump,
        dist_tag=dist_tag,
        promote_latest=str(dist_tag == "latest").lower(),
    )


def _ask(prompt):
    answer = prompt.ask()
    if answer is None:
        raise ReleaseError("Release cancelled")
    return answer


def choose_release(
    changes: list[Change], branch: str, tags: set[str], channel: str | None = None, version: str | None = None
):
    import questionary
    from questionary import Choice

    included = changes
    selected_notes = _ask(
        questionary.checkbox(
            "Which included changes belong in public release notes?",
            choices=[
                Choice(
                    f"{change.category}: {clean_title(change.title)}",
                    value=index,
                    checked=change.include_in_notes,
                )
                for index, change in enumerate(included)
            ],
        )
    )
    selected_set = set(selected_notes)
    for index, change in enumerate(included):
        change.include_in_notes = index in selected_set
    if selected_notes and _ask(questionary.confirm("Edit selected note titles and categories?", default=False)):
        for index in selected_notes:
            change = included[index]
            change.title = _ask(
                questionary.text("Release-note title:", default=clean_title(change.title))
            ) or clean_title(change.title)
            change.category = _ask(questionary.select("Category:", choices=CATEGORIES, default=change.category))
            change.highlight = _ask(questionary.confirm("Highlight this change?", default=False))
            change.breaking = _ask(questionary.confirm("Breaking change?", default=change.breaking))
    channel = channel or _ask(
        questionary.select("Release channel:", choices=("alpha", "beta", "rc", "stable"), default="rc")
    )
    version = version or next_version(branch, channel, list(tags))
    validate_progression(version, channel, branch, list(tags))
    return included, version, channel


def prepare(
    preview_only: bool,
    upstream: str = "upstream",
    fork: str = "origin",
    channel: str | None = None,
    version: str | None = None,
    yes: bool = False,
    overrides: dict | None = None,
) -> None:
    import questionary

    base = ensure_preflight(upstream)
    require("uv")
    owner, name = repository(upstream)
    repo = f"{owner}/{name}"
    fork_owner, _ = repository(fork)
    branch = f"{upstream}/{base}"
    previous_tag = latest_tag(branch)
    changes = discover_changes(repo, previous_tag, branch, base)
    apply_note_overrides(changes, **(overrides or {}))
    if yes:
        if not channel:
            raise ReleaseError("Non-interactive preparation requires --channel")
        included = changes
        version = version or next_version(base, channel, version_tags())
        validate_progression(version, channel, base, version_tags())
    else:
        included, version, channel = choose_release(changes, base, set(version_tags()), channel, version)
    undocumented_migrations = [change for change in migration_changes(included) if not change.include_in_notes]
    if undocumented_migrations:
        names = ", ".join(
            f"#{change.pr}" if change.pr else change.commits[-1][:7] for change in undocumented_migrations
        )
        raise ReleaseError(f"Database migrations must be included in release notes (use --include-pr): {names}")
    source_sha = run("git", "rev-parse", branch)
    commits = [
        commit for commit in commit_log(f"{previous_tag}..{source_sha}") if not RELEASE_TITLE.fullmatch(commit.title)
    ]
    contributors = all_contributors(included, commits)
    date = datetime.now(UTC).date().isoformat()
    changelog_section = render_changelog_section(version, date, included)
    notes = render_release_notes(version, previous_tag, source_sha, included, contributors)
    print("\nIncluded:")
    print(f"  {len(included)} change groups, {len(commits)} commits, {len(contributors)} contributors")
    print(f"Version:  {version} ({channel})")
    print("\nRelease notes preview:\n")
    print(notes)
    if preview_only:
        return
    if not yes and not _ask(questionary.confirm("Create and push this release PR?", default=False)):
        raise ReleaseError("Release cancelled")

    release_branch = f"prepare/v{version}"
    worktree = ROOT / ".worktrees" / f"release-v{version}"
    if worktree.exists() or run("git", "branch", "--list", release_branch):
        raise ReleaseError(f"Release branch or worktree already exists: {release_branch}")
    run("git", "worktree", "add", "-b", release_branch, str(worktree), source_sha, capture=False)
    try:
        for relative in VERSION_FILES:
            set_version(worktree / relative, version)
        run("uv", "lock", cwd=worktree, capture=False)
        run("uv", "lock", cwd=worktree / "observal-server", capture=False)
        changelog = worktree / "CHANGELOG.md"
        changelog.write_text(prepend_changelog(changelog.read_text(), changelog_section, version))
        notes_path = worktree / ".github" / "release-notes.md"
        notes_path.write_text(notes)
        write_manifest(worktree / ".release.toml", version, channel, previous_tag, source_sha, included)
        run("git", "add", *RELEASE_FILES, cwd=worktree)
        changed = set(run("git", "diff", "--cached", "--name-only", cwd=worktree).splitlines())
        unexpected = changed - set(RELEASE_FILES)
        unexpected.update(run("git", "diff", "--name-only", cwd=worktree).splitlines())
        unexpected.update(run("git", "ls-files", "--others", "--exclude-standard", cwd=worktree).splitlines())
        if unexpected:
            raise ReleaseError(f"Release preparation changed unexpected files: {sorted(unexpected)}")
        run("git", "diff", "--cached", "--check", cwd=worktree)
        run("git", "commit", "-s", "-m", f"chore(release): v{version}", cwd=worktree, capture=False)
        run("git", "push", fork, release_branch, cwd=worktree, capture=False)
        body = pr_body(version, previous_tag, source_sha, included, changelog_section)
        with tempfile.TemporaryDirectory() as tmpdir:
            body_path = Path(tmpdir) / "release-pr-body.md"
            body_path.write_text(body)
            url = run(
                "gh",
                "pr",
                "create",
                "--repo",
                repo,
                "--head",
                f"{fork_owner}:{release_branch}",
                "--base",
                base,
                "--title",
                f"chore(release): v{version}",
                "--body-file",
                str(body_path),
                cwd=worktree,
            )
        print(f"\nRelease PR created: {url}")
        print(f"Rebase-merge into {base}. If its base advances, regenerate this PR.")
    except Exception:
        print(f"Release worktree preserved for recovery: {worktree}", file=sys.stderr)
        raise
    else:
        run("git", "worktree", "remove", str(worktree), capture=False)


def _pr_pair(value: str) -> tuple[int, str]:
    number, sep, text = value.partition("=")
    if not sep or not number.isdigit() or not text:
        raise argparse.ArgumentTypeError("expected PR=VALUE")
    return int(number), text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--cut", metavar="X.Y", help="cut a release line from the current canonical main")
    action.add_argument("--backport", type=int, metavar="PR", help="backport a rebase-merged main PR")
    action.add_argument("--status", action="store_true", help="show release lines or unreleased commits")
    action.add_argument("--resolve-push", action="store_true", help=argparse.SUPPRESS)
    action.add_argument("--validate-target", help=argparse.SUPPRESS)
    parser.add_argument("--to", help="backport destination release/X.Y")
    parser.add_argument("--channel", choices=("alpha", "beta", "rc", "stable"))
    parser.add_argument("--version", help="explicit version within the current release line")
    parser.add_argument("--yes", action="store_true", help="prepare without prompts, requires --channel")
    for flag, text in (("include", "include in"), ("exclude", "exclude from"), ("highlight", "highlight in")):
        parser.add_argument(
            f"--{flag}-pr", type=int, action="append", default=[], metavar="PR", help=f"{text} public release notes"
        )
    parser.add_argument("--breaking-pr", type=int, action="append", default=[], metavar="PR", help="mark as breaking")
    parser.add_argument(
        "--title-pr", type=_pr_pair, action="append", default=[], metavar="PR=TITLE", help="set release-note title"
    )
    parser.add_argument(
        "--category-pr", type=_pr_pair, action="append", default=[], metavar="PR=CATEGORY", help="set category"
    )
    parser.add_argument("--preview", action="store_true", help="render release notes without writing or publishing")
    parser.add_argument("--upstream", default="upstream", help="canonical repository remote")
    parser.add_argument("--fork", default="origin", help="remote receiving preparation and backport PRs")
    parser.add_argument("--before", help=argparse.SUPPRESS)
    parser.add_argument("--after", help=argparse.SUPPRESS)
    parser.add_argument("--branch", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if (
            args.preview
            or args.yes
            or args.channel
            or args.version
            or args.include_pr
            or args.exclude_pr
            or args.highlight_pr
            or args.breaking_pr
            or args.title_pr
            or args.category_pr
        ) and (args.cut or args.backport or args.status or args.resolve_push or args.validate_target):
            raise ReleaseError("Preparation flags cannot be combined with another action")
        if args.cut:
            cut(args.cut, args.upstream, args.fork)
        elif args.backport:
            if not args.to:
                raise ReleaseError("--backport requires --to release/X.Y")
            backport(args.backport, args.to, args.upstream, args.fork)
        elif args.status:
            status(args.upstream)
        elif args.resolve_push:
            if not all((args.before, args.after, args.branch)):
                raise ReleaseError("--resolve-push requires --before, --after, --branch")
            target = resolve_release_push(args.before, args.after, args.branch)
            if target:
                print(target)
        elif args.validate_target:
            if not args.branch:
                raise ReleaseError("--validate-target requires --branch")
            for key, value in validate_target(args.validate_target, args.branch, args.upstream).items():
                print(f"{key}={value}")
        else:
            prepare(
                args.preview,
                args.upstream,
                args.fork,
                args.channel,
                args.version,
                args.yes,
                dict(
                    include=tuple(args.include_pr),
                    exclude=tuple(args.exclude_pr),
                    highlight=tuple(args.highlight_pr),
                    breaking=tuple(args.breaking_pr),
                    titles=dict(args.title_pr),
                    categories=dict(args.category_pr),
                ),
            )
    except (ReleaseError, KeyboardInterrupt, KeyError, tomllib.TOMLDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
