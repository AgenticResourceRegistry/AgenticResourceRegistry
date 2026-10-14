# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for git mirror service — real git operations, no mocks.

These tests create real git repos in tmpdir and exercise the full clone/discover/validate pipeline.
"""

import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import services.git_mirror_service as gms
from services.git_mirror_service import (
    _mirror_path,
    _parse_manifest,
    _safe_path,
    clone_or_update,
    discover_components,
    get_commit_sha,
    sync_source,
    validate_mcp_component,
)

# ── Helpers ─────────────────────────────────────────────────────────


def _create_git_repo(path: Path, files: dict[str, str] | None = None) -> Path:
    """Create a real git repo at `path` with optional files. Returns repo path."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--initial-branch", "main", str(path)], capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), capture_output=True, check=True)

    if files:
        for rel_path, content in files.items():
            fpath = path / rel_path
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content)
        subprocess.run(["git", "add", "-A"], cwd=str(path), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), capture_output=True, check=True)
    else:
        # Need at least one commit for HEAD to exist
        (path / ".gitkeep").touch()
        subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), capture_output=True, check=True)

    return path


@pytest.fixture
def tmp_base(tmp_path):
    """Provide a temp mirror base directory."""
    return tmp_path / "mirrors"


@pytest.fixture
def simple_mcp_repo(tmp_path):
    """A git repo with a single FastMCP server at src/my-mcp/."""
    return _create_git_repo(
        tmp_path / "mcp-repo",
        {
            "src/my-mcp/server.py": (
                "from mcp.server.fastmcp import FastMCP\n"
                'mcp = FastMCP("my-mcp")\n'
                "\n"
                "@mcp.tool()\n"
                "def hello(name: str) -> str:\n"
                '    """Say hello."""\n'
                '    return f"Hello {name}"\n'
            ),
        },
    )


@pytest.fixture
def manifest_repo(tmp_path):
    """A git repo with an .observal.json manifest listing multiple components."""
    manifest = {
        "version": "1.0",
        "mcps": [
            {"path": "src/filesystem", "name": "filesystem-mcp", "description": "FS ops"},
            {"path": "src/git-ops", "name": "git-mcp", "description": "Git ops"},
        ],
        "skills": [
            {"path": "skills/tdd", "name": "tdd-skill", "description": "TDD workflow"},
        ],
    }
    return _create_git_repo(
        tmp_path / "manifest-repo",
        {
            ".observal.json": json.dumps(manifest, indent=2),
            "src/filesystem/server.py": 'from mcp.server.fastmcp import FastMCP\nmcp = FastMCP("filesystem")\n',
            "src/git-ops/server.py": 'from mcp.server.fastmcp import FastMCP\nmcp = FastMCP("git-ops")\n',
            "skills/tdd/SKILL.md": "# TDD Skill\nTest-driven development.",
        },
    )


@pytest.fixture
def monorepo_convention(tmp_path):
    """A git repo with multiple components using convention layout (no manifest)."""
    return _create_git_repo(
        tmp_path / "mono-repo",
        {
            "src/server-a/main.py": 'from fastmcp import FastMCP\napp = FastMCP("a")\n',
            "src/server-b/main.py": 'from mcp.server.fastmcp import FastMCP\napp = FastMCP("b")\n',
            "src/not-mcp/README.md": "This has no Python files.",
            "skills/debugging/SKILL.md": "# Debugging skill",
            "hooks/pre-commit/hook.json": json.dumps({"event": "PreCommit"}),
            "prompts/code-review/review.md": "# Code Review Prompt",
            "sandboxes/python/Dockerfile": "FROM python:3.12\nRUN pip install pytest\n",
        },
    )


@pytest.fixture
def non_fastmcp_repo(tmp_path):
    """A git repo with a non-FastMCP MCP server (should fail validation)."""
    return _create_git_repo(
        tmp_path / "bad-mcp",
        {
            "src/old-server/server.py": (
                'import flask\napp = flask.Flask(__name__)\n@app.route("/tool")\ndef tool(): return "hi"\n'
            ),
        },
    )


# ── Clone & Update ──────────────────────────────────────────────────


class TestCloneOrUpdate:
    def test_fresh_clone(self, simple_mcp_repo, tmp_base):
        mirror = clone_or_update(str(simple_mcp_repo), branch="main", base=tmp_base)
        assert mirror.exists()
        assert (mirror / ".git").exists()
        assert (mirror / "src" / "my-mcp" / "server.py").exists()

    def test_returns_correct_path(self, simple_mcp_repo, tmp_base):
        mirror = clone_or_update(str(simple_mcp_repo), branch="main", base=tmp_base)
        expected = _mirror_path(str(simple_mcp_repo), tmp_base)
        assert mirror == expected

    def test_update_picks_up_changes(self, simple_mcp_repo, tmp_base):
        # Initial clone
        clone_or_update(str(simple_mcp_repo), branch="main", base=tmp_base)

        # Add a new file to the source repo
        new_file = simple_mcp_repo / "NEW_FILE.txt"
        new_file.write_text("new content")
        subprocess.run(["git", "add", "-A"], cwd=str(simple_mcp_repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "add file"], cwd=str(simple_mcp_repo), capture_output=True, check=True)

        # Update mirror
        mirror = clone_or_update(str(simple_mcp_repo), branch="main", base=tmp_base)
        assert (mirror / "NEW_FILE.txt").exists()
        assert (mirror / "NEW_FILE.txt").read_text() == "new content"

    def test_clone_invalid_url_raises(self, tmp_base):
        with pytest.raises(RuntimeError, match="clone failed"):
            clone_or_update("https://github.com/nonexistent/repo-that-does-not-exist-12345.git", base=tmp_base)

    def test_idempotent_clone(self, simple_mcp_repo, tmp_base):
        """Cloning the same repo twice should work (update path)."""
        m1 = clone_or_update(str(simple_mcp_repo), branch="main", base=tmp_base)
        sha1 = get_commit_sha(m1)
        m2 = clone_or_update(str(simple_mcp_repo), branch="main", base=tmp_base)
        sha2 = get_commit_sha(m2)
        assert m1 == m2
        assert sha1 == sha2


class TestGetCommitSha:
    def test_returns_40_char_hex(self, simple_mcp_repo, tmp_base):
        mirror = clone_or_update(str(simple_mcp_repo), branch="main", base=tmp_base)
        sha = get_commit_sha(mirror)
        assert len(sha) == 40
        assert all(c in "0123456789abcdef" for c in sha)

    def test_matches_source_repo_head(self, simple_mcp_repo, tmp_base):
        mirror = clone_or_update(str(simple_mcp_repo), branch="main", base=tmp_base)
        mirror_sha = get_commit_sha(mirror)
        source_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(simple_mcp_repo),
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert mirror_sha == source_sha


# ── Discovery: Manifest ─────────────────────────────────────────────


class TestManifestDiscovery:
    def test_discovers_from_manifest(self, manifest_repo, tmp_base):
        mirror = clone_or_update(str(manifest_repo), branch="main", base=tmp_base)
        components = discover_components(mirror)
        names = {c.name for c in components}
        assert "filesystem-mcp" in names
        assert "git-mcp" in names
        assert "tdd-skill" in names
        assert len(components) == 3

    def test_filter_by_type(self, manifest_repo, tmp_base):
        mirror = clone_or_update(str(manifest_repo), branch="main", base=tmp_base)
        mcps = discover_components(mirror, component_type="mcp")
        assert all(c.component_type == "mcp" for c in mcps)
        assert len(mcps) == 2

        skills = discover_components(mirror, component_type="skill")
        assert all(c.component_type == "skill" for c in skills)
        assert len(skills) == 1

    def test_manifest_preserves_description(self, manifest_repo, tmp_base):
        mirror = clone_or_update(str(manifest_repo), branch="main", base=tmp_base)
        components = discover_components(mirror, component_type="mcp")
        fs_mcp = next(c for c in components if c.name == "filesystem-mcp")
        assert fs_mcp.description == "FS ops"

    def test_manifest_preserves_path(self, manifest_repo, tmp_base):
        mirror = clone_or_update(str(manifest_repo), branch="main", base=tmp_base)
        components = discover_components(mirror, component_type="mcp")
        paths = {c.path for c in components}
        assert "src/filesystem" in paths
        assert "src/git-ops" in paths


class TestParseManifest:
    def test_basic_manifest(self):
        manifest = {
            "mcps": [{"name": "a", "path": "src/a"}],
            "skills": [{"name": "b", "path": "skills/b"}],
        }
        result = _parse_manifest(manifest)
        assert len(result) == 2
        assert result[0].component_type == "mcp"
        assert result[1].component_type == "skill"

    def test_empty_manifest(self):
        result = _parse_manifest({})
        assert result == []

    def test_filter_by_type(self):
        manifest = {
            "mcps": [{"name": "a", "path": "src/a"}],
            "skills": [{"name": "b", "path": "skills/b"}],
        }
        result = _parse_manifest(manifest, component_type="mcp")
        assert len(result) == 1
        assert result[0].name == "a"


# ── Discovery: Convention Scan ───────────────────────────────────────


class TestConventionScan:
    def test_discovers_all_types(self, monorepo_convention, tmp_base):
        mirror = clone_or_update(str(monorepo_convention), branch="main", base=tmp_base)
        components = discover_components(mirror)
        types = {c.component_type for c in components}
        assert "mcp" in types
        assert "skill" in types
        assert "hook" in types
        assert "prompt" in types
        assert "sandbox" in types

    def test_mcp_requires_python_files(self, monorepo_convention, tmp_base):
        """The not-mcp dir (only has README.md) should NOT be discovered as an MCP."""
        mirror = clone_or_update(str(monorepo_convention), branch="main", base=tmp_base)
        mcps = discover_components(mirror, component_type="mcp")
        names = {c.name for c in mcps}
        assert "server-a" in names
        assert "server-b" in names
        assert "not-mcp" not in names

    def test_skill_requires_skill_md(self, monorepo_convention, tmp_base):
        mirror = clone_or_update(str(monorepo_convention), branch="main", base=tmp_base)
        skills = discover_components(mirror, component_type="skill")
        assert len(skills) == 1
        assert skills[0].name == "debugging"

    def test_hook_requires_hook_json(self, monorepo_convention, tmp_base):
        mirror = clone_or_update(str(monorepo_convention), branch="main", base=tmp_base)
        hooks = discover_components(mirror, component_type="hook")
        assert len(hooks) == 1
        assert hooks[0].name == "pre-commit"

    def test_sandbox_requires_dockerfile(self, monorepo_convention, tmp_base):
        mirror = clone_or_update(str(monorepo_convention), branch="main", base=tmp_base)
        sandboxes = discover_components(mirror, component_type="sandbox")
        assert len(sandboxes) == 1
        assert sandboxes[0].name == "python"

    def test_empty_repo_returns_nothing(self, tmp_path, tmp_base):
        repo = _create_git_repo(tmp_path / "empty-repo")
        mirror = clone_or_update(str(repo), branch="main", base=tmp_base)
        components = discover_components(mirror)
        assert components == []


# ── FastMCP Validation ───────────────────────────────────────────────


class TestFastMcpValidation:
    def test_valid_fastmcp(self, simple_mcp_repo, tmp_base):
        mirror = clone_or_update(str(simple_mcp_repo), branch="main", base=tmp_base)
        passed, detail = validate_mcp_component(mirror / "src" / "my-mcp")
        assert passed is True
        assert "FastMCP found" in detail

    def test_non_fastmcp_rejected(self, non_fastmcp_repo, tmp_base):
        mirror = clone_or_update(str(non_fastmcp_repo), branch="main", base=tmp_base)
        passed, detail = validate_mcp_component(mirror / "src" / "old-server")
        assert passed is False
        assert "must use FastMCP" in detail

    def test_empty_dir_rejected(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        passed, detail = validate_mcp_component(empty)
        assert passed is False

    def test_alternative_import_style(self, tmp_path, tmp_base):
        """Test 'from fastmcp import FastMCP' (alternative import) is accepted."""
        repo = _create_git_repo(
            tmp_path / "alt-import-repo",
            {
                "src/alt/server.py": 'from fastmcp import FastMCP\napp = FastMCP("alt")\n',
            },
        )
        mirror = clone_or_update(str(repo), branch="main", base=tmp_base)
        passed, detail = validate_mcp_component(mirror / "src" / "alt")
        assert passed is True


# ── Full Sync Pipeline ───────────────────────────────────────────────


class TestSyncSource:
    def test_sync_manifest_repo(self, manifest_repo, tmp_base):
        result = sync_source(str(manifest_repo), component_type="mcp", base=tmp_base)
        assert result.success is True
        assert len(result.commit_sha) == 40
        assert len(result.components) == 2
        names = {c.name for c in result.components}
        assert "filesystem-mcp" in names
        assert "git-mcp" in names

    def test_sync_convention_repo(self, monorepo_convention, tmp_base):
        result = sync_source(str(monorepo_convention), component_type="mcp", base=tmp_base)
        assert result.success is True
        # Both server-a and server-b use FastMCP
        assert len(result.components) == 2

    def test_sync_filters_non_fastmcp(self, non_fastmcp_repo, tmp_base):
        """Non-FastMCP MCPs should be filtered out during sync."""
        result = sync_source(str(non_fastmcp_repo), component_type="mcp", base=tmp_base)
        assert result.success is True
        assert len(result.components) == 0  # Filtered out

    def test_sync_non_mcp_skips_validation(self, monorepo_convention, tmp_base):
        """Skills, hooks, etc. should not go through FastMCP validation."""
        result = sync_source(str(monorepo_convention), component_type="skill", base=tmp_base)
        assert result.success is True
        assert len(result.components) == 1
        assert result.components[0].name == "debugging"

    def test_sync_invalid_url_returns_error(self, tmp_base):
        result = sync_source(
            "https://github.com/nonexistent/no-such-repo-99999.git", component_type="mcp", base=tmp_base
        )
        assert result.success is False
        assert result.error != ""
        assert result.components == []

    def test_sync_updates_on_second_run(self, simple_mcp_repo, tmp_base):
        """Second sync should update, not re-clone."""
        r1 = sync_source(str(simple_mcp_repo), component_type="mcp", base=tmp_base)
        assert r1.success is True

        # Add another MCP to the source repo
        new_mcp = simple_mcp_repo / "src" / "new-mcp" / "server.py"
        new_mcp.parent.mkdir(parents=True, exist_ok=True)
        new_mcp.write_text('from mcp.server.fastmcp import FastMCP\nmcp = FastMCP("new")\n')
        subprocess.run(["git", "add", "-A"], cwd=str(simple_mcp_repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "add mcp"], cwd=str(simple_mcp_repo), capture_output=True, check=True)

        r2 = sync_source(str(simple_mcp_repo), component_type="mcp", base=tmp_base)
        assert r2.success is True
        assert len(r2.components) == 2  # Original + new
        assert r2.commit_sha != r1.commit_sha


# ── Mirror Lock ──────────────────────────────────────────────────────


@pytest.fixture
def overlap_tracker(monkeypatch):
    """Count syncs that are between clone start and discovery end, and keep the peak.

    Discovery sleeps so that an unserialized second sync would start its clone
    while the first sync is still scanning the checkout.
    """
    state = {"active": 0, "peak": 0}
    guard = threading.Lock()
    real_clone, real_discover = gms.clone_or_update, gms.discover_components

    def clone(*args, **kwargs):
        with guard:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        return real_clone(*args, **kwargs)

    def discover(*args, **kwargs):
        time.sleep(0.3)
        try:
            return real_discover(*args, **kwargs)
        finally:
            with guard:
                state["active"] -= 1

    monkeypatch.setattr(gms, "clone_or_update", clone)
    monkeypatch.setattr(gms, "discover_components", discover)
    return state


def _sync_in_parallel(*calls: tuple[str, str], base: Path):
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [pool.submit(sync_source, url, component_type, base=base) for url, component_type in calls]
        return [f.result() for f in futures]


class TestMirrorLock:
    def test_same_url_syncs_run_one_at_a_time(self, monorepo_convention, tmp_base, overlap_tracker):
        """One URL registered as both mcp and skill shares a checkout, so its syncs must not overlap."""
        url = str(monorepo_convention)
        mcp, skill = _sync_in_parallel((url, "mcp"), (url, "skill"), base=tmp_base)

        assert overlap_tracker["peak"] == 1
        assert mcp.success is True, mcp.error
        assert skill.success is True, skill.error
        assert len(mcp.components) == 2
        assert [c.name for c in skill.components] == ["debugging"]

    def test_different_urls_sync_in_parallel(self, simple_mcp_repo, manifest_repo, tmp_base, overlap_tracker):
        """The lock is per mirror, so unrelated sources do not wait on each other."""
        results = _sync_in_parallel((str(simple_mcp_repo), "mcp"), (str(manifest_repo), "mcp"), base=tmp_base)

        assert overlap_tracker["peak"] == 2
        assert all(r.success for r in results)

    def test_lock_file_sits_beside_the_mirror(self, simple_mcp_repo, tmp_base):
        """A fresh clone replaces the mirror directory, so the lock file cannot live inside it."""
        result = sync_source(str(simple_mcp_repo), component_type="mcp", base=tmp_base)

        mirror = _mirror_path(str(simple_mcp_repo), tmp_base)
        assert result.success is True
        assert (mirror / ".git").is_dir()
        assert mirror.with_name(f"{mirror.name}.lock").is_file()


# ── Mirror Path ──────────────────────────────────────────────────────


class TestMirrorPath:
    def test_deterministic(self, tmp_base):
        p1 = _mirror_path("https://github.com/org/repo.git", tmp_base)
        p2 = _mirror_path("https://github.com/org/repo.git", tmp_base)
        assert p1 == p2

    def test_different_urls_different_paths(self, tmp_base):
        p1 = _mirror_path("https://github.com/org/repo1.git", tmp_base)
        p2 = _mirror_path("https://github.com/org/repo2.git", tmp_base)
        assert p1 != p2

    def test_path_under_base(self, tmp_base):
        p = _mirror_path("https://github.com/org/repo.git", tmp_base)
        assert str(p).startswith(str(tmp_base))


# ── Security ─────────────────────────────────────────────────────────


class TestPathTraversalPrevention:
    def test_safe_path_allows_normal(self, tmp_path):
        assert _safe_path(tmp_path, "src/my-mcp") is True
        assert _safe_path(tmp_path, "skills/tdd") is True

    def test_safe_path_blocks_traversal(self, tmp_path):
        assert _safe_path(tmp_path, "../../etc/passwd") is False
        assert _safe_path(tmp_path, "../../../sensitive") is False

    def test_safe_path_blocks_absolute(self, tmp_path):
        assert _safe_path(tmp_path, "/etc/passwd") is False

    def test_manifest_rejects_traversal_paths(self, tmp_path):
        """Manifest entries with path traversal should be skipped."""
        manifest = {
            "mcps": [
                {"name": "legit", "path": "src/legit"},
                {"name": "evil", "path": "../../etc/passwd"},
                {"name": "also-evil", "path": "../../../sensitive"},
            ],
        }
        components = _parse_manifest(manifest, mirror_dir=tmp_path)
        names = {c.name for c in components}
        assert "legit" in names
        assert "evil" not in names
        assert "also-evil" not in names

    def test_convention_scan_skips_symlinks(self, tmp_path, tmp_base):
        """Symlinks in convention directories should be skipped."""
        repo_dir = tmp_path / "symlink-repo"
        external = tmp_path / "external_secret"
        external.mkdir()
        (external / "server.py").write_text("from mcp.server.fastmcp import FastMCP\n")

        files = {
            "src/legit/server.py": "from mcp.server.fastmcp import FastMCP\nmcp = FastMCP('legit')\n",
        }
        repo = _create_git_repo(repo_dir, files)

        # Create symlink after repo init (git may not track it, but the mirror dir will have it)
        mirror = clone_or_update(str(repo), branch="main", base=tmp_base)
        symlink_target = mirror / "src" / "evil-link"
        symlink_target.symlink_to(external)

        mcps = discover_components(mirror, component_type="mcp")
        names = {c.name for c in mcps}
        assert "legit" in names
        assert "evil-link" not in names
