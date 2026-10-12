# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for env var detection (analyzer + validator), config generation with docker,
and MCP submit auto-replace logic."""

import json
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.deps import get_current_user, get_db
from models.mcp import ListingStatus
from models.user import User, UserRole

# ── Helpers ──────────────────────────────────────────────


def _user(**kw):
    u = MagicMock(spec=User)
    u.id = kw.get("id", uuid.uuid4())
    u.role = kw.get("role", UserRole.user)
    u.username = kw.get("username", "testuser")
    u.email = kw.get("email", "test@example.com")
    return u


def _mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    # A publish that stays pending delivers inbox items in the same
    # transaction, wrapping each insert in a SAVEPOINT. A bare AsyncMock
    # returns a coroutine from begin_nested(), not an async context manager.
    nested = MagicMock()
    nested.__aenter__ = AsyncMock(return_value=None)
    nested.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested = MagicMock(return_value=nested)
    return db


def _scalar_result(val):
    r = MagicMock()
    r.scalar_one_or_none.return_value = val
    r.scalars.return_value.all.return_value = [val] if val else []
    r.scalars.return_value.first.return_value = val
    return r


def _make_tmpdir_with_files(file_map: dict[str, str]) -> str:
    """Create a temp directory with the given file tree. Returns path."""
    tmp = tempfile.mkdtemp(prefix="observal_test_")
    for relpath, content in file_map.items():
        full = Path(tmp) / relpath
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    return tmp


# ═══════════════════════════════════════════════════════════
# 1. Env Var Filtering
# ═══════════════════════════════════════════════════════════


class TestEnvVarFiltering:
    """Test _is_filtered_env_var for both CLI analyzer and server validator."""

    def test_internal_vars_filtered(self):
        from observal_cli.analyzer import _is_filtered_env_var

        for var in ("PATH", "HOME", "NODE_ENV", "PORT", "APP", "DEBUG"):
            assert _is_filtered_env_var(var), f"{var} should be filtered"

    def test_ci_prefix_filtered(self):
        from observal_cli.analyzer import _is_filtered_env_var

        for var in ("CI_PIPELINE_ID", "GITHUB_SHA", "GITLAB_CI", "DOCKER_BUILDKIT"):
            assert _is_filtered_env_var(var), f"{var} should be filtered"

    def test_allowed_vars_bypass_prefix_filter(self):
        from observal_cli.analyzer import _is_filtered_env_var

        for var in ("GITHUB_TOKEN", "GITHUB_PERSONAL_ACCESS_TOKEN", "DOCKER_HOST"):
            assert not _is_filtered_env_var(var), f"{var} should NOT be filtered"

    def test_user_facing_vars_pass(self):
        from observal_cli.analyzer import _is_filtered_env_var

        for var in ("OPENAI_API_KEY", "SLACK_TOKEN", "DATABASE_URL"):
            assert not _is_filtered_env_var(var), f"{var} should pass"

    def test_server_validator_matches_cli(self):
        """Server-side filtering must match CLI-side."""
        from observal_cli.analyzer import _is_filtered_env_var as cli_filter
        from services.mcp_validator import _is_filtered_env_var as server_filter

        test_vars = [
            "PATH",
            "GITHUB_SHA",
            "GITHUB_TOKEN",
            "OPENAI_API_KEY",
            "CI_PIPELINE_ID",
            "DOCKER_HOST",
            "APP",
            "SLACK_TOKEN",
        ]
        for var in test_vars:
            assert cli_filter(var) == server_filter(var), f"Mismatch on {var}"


# ═══════════════════════════════════════════════════════════
# 2. Test File Filtering
# ═══════════════════════════════════════════════════════════


class TestTestFileFiltering:
    def test_skip_test_dirs(self):
        from observal_cli.analyzer import _is_test_file

        assert _is_test_file(Path("tests/test_foo.py"))
        assert _is_test_file(Path("test/main_test.go"))
        assert _is_test_file(Path("e2e/integration.ts"))
        assert _is_test_file(Path("vendor/lib.go"))
        assert _is_test_file(Path("node_modules/pkg/index.js"))

    def test_skip_test_files(self):
        from observal_cli.analyzer import _is_test_file

        assert _is_test_file(Path("cmd/server_test.go"))
        assert _is_test_file(Path("test_config.py"))

    def test_pass_normal_files(self):
        from observal_cli.analyzer import _is_test_file

        assert not _is_test_file(Path("cmd/server.go"))
        assert not _is_test_file(Path("src/main.py"))
        assert not _is_test_file(Path("lib/index.ts"))


# ═══════════════════════════════════════════════════════════
# 3. Tiered Env Var Detection (CLI analyzer)
# ═══════════════════════════════════════════════════════════


class TestTier1ServerJson:
    """Tier 1: server.json manifest is authoritative."""

    def test_packages_runtime_arguments(self):
        from observal_cli.analyzer import _detect_env_vars

        manifest = {
            "packages": [
                {
                    "runtimeArguments": [
                        {"value": "GITHUB_PERSONAL_ACCESS_TOKEN={token}", "description": "GitHub PAT"},
                    ]
                }
            ]
        }
        tmp = _make_tmpdir_with_files({"server.json": json.dumps(manifest)})
        result = _detect_env_vars(tmp)
        names = [r["name"] for r in result]
        assert "GITHUB_PERSONAL_ACCESS_TOKEN" in names

    def test_remotes_variables(self):
        from observal_cli.analyzer import _detect_env_vars

        manifest = {
            "remotes": [
                {
                    "variables": {
                        "API_KEY": {"description": "The API key"},
                        "SECRET": {"description": "A secret"},
                    }
                }
            ]
        }
        tmp = _make_tmpdir_with_files({"server.json": json.dumps(manifest)})
        result = _detect_env_vars(tmp)
        names = [r["name"] for r in result]
        assert "API_KEY" in names
        assert "SECRET" in names

    def test_manifest_stops_further_scanning(self):
        """If server.json exists (even with 0 env vars), skip all other tiers."""
        from observal_cli.analyzer import _detect_env_vars

        tmp = _make_tmpdir_with_files(
            {
                "server.json": json.dumps({"packages": []}),
                "README.md": "export MY_VAR=something",
                "src/main.py": 'os.environ["SOME_KEY"]',
            }
        )
        result = _detect_env_vars(tmp)
        assert result == []

    def test_invalid_json_falls_through(self):
        """Malformed server.json should fall through to next tier."""
        from observal_cli.analyzer import _detect_env_vars

        tmp = _make_tmpdir_with_files(
            {
                "server.json": "not valid json{{{",
                "README.md": "export MY_TOKEN=xyz",
            }
        )
        result = _detect_env_vars(tmp)
        names = [r["name"] for r in result]
        assert "MY_TOKEN" in names


class TestTier2Readme:
    """Tier 2: README env var extraction."""

    def test_docker_e_flag(self):
        from observal_cli.analyzer import _detect_env_vars

        tmp = _make_tmpdir_with_files({"README.md": "docker run -e MY_API_KEY -e MY_SECRET image:latest"})
        result = _detect_env_vars(tmp)
        names = [r["name"] for r in result]
        assert "MY_API_KEY" in names
        assert "MY_SECRET" in names

    def test_export_statement(self):
        from observal_cli.analyzer import _detect_env_vars

        tmp = _make_tmpdir_with_files({"README.md": "export OPENAI_API_KEY=sk-..."})
        result = _detect_env_vars(tmp)
        names = [r["name"] for r in result]
        assert "OPENAI_API_KEY" in names

    def test_json_config_key(self):
        from observal_cli.analyzer import _detect_env_vars

        tmp = _make_tmpdir_with_files({"README.md": '{\n  "SLACK_TOKEN": "xoxb-..."\n}'})
        result = _detect_env_vars(tmp)
        names = [r["name"] for r in result]
        assert "SLACK_TOKEN" in names

    def test_filters_internal_vars_from_readme(self):
        from observal_cli.analyzer import _detect_env_vars

        tmp = _make_tmpdir_with_files(
            {"README.md": "export PATH=/usr/bin\nexport NODE_ENV=production\nexport MY_TOKEN=abc"}
        )
        result = _detect_env_vars(tmp)
        names = [r["name"] for r in result]
        assert "MY_TOKEN" in names
        assert "PATH" not in names
        assert "NODE_ENV" not in names

    def test_readme_stops_further_scanning(self):
        from observal_cli.analyzer import _detect_env_vars

        tmp = _make_tmpdir_with_files(
            {
                "README.md": "export MY_TOKEN=abc",
                ".env.example": "EXTRA_VAR=foo",
                "src/main.py": 'os.getenv("CODE_VAR")',
            }
        )
        result = _detect_env_vars(tmp)
        names = [r["name"] for r in result]
        assert "MY_TOKEN" in names
        assert "EXTRA_VAR" not in names
        assert "CODE_VAR" not in names


class TestTier3EnvExample:
    """Tier 3: .env.example file."""

    def test_env_example_detected(self):
        from observal_cli.analyzer import _detect_env_vars

        tmp = _make_tmpdir_with_files({".env.example": "API_KEY=\nDATABASE_URL=postgres://localhost/db\n"})
        result = _detect_env_vars(tmp)
        names = [r["name"] for r in result]
        assert "API_KEY" in names
        assert "DATABASE_URL" in names

    def test_skips_comments_and_blanks(self):
        from observal_cli.analyzer import _detect_env_vars

        tmp = _make_tmpdir_with_files({".env.example": "# This is a comment\n\nSECRET_KEY=mysecret\n"})
        result = _detect_env_vars(tmp)
        names = [r["name"] for r in result]
        assert "SECRET_KEY" in names
        assert len(result) == 1

    def test_skips_env_and_env_local(self):
        """Should not scan .env or .env.local (actual secrets)."""
        from observal_cli.analyzer import _detect_env_vars

        tmp = _make_tmpdir_with_files(
            {
                ".env": "REAL_SECRET=abc123",
                ".env.local": "LOCAL_SECRET=def456",
            }
        )
        result = _detect_env_vars(tmp)
        assert result == []


class TestTier4SourceCode:
    """Tier 4: Source code scanning (last resort)."""

    def test_python_os_environ(self):
        from observal_cli.analyzer import _detect_env_vars

        tmp = _make_tmpdir_with_files(
            {"src/main.py": 'import os\ntoken = os.environ["MY_TOKEN"]\nkey = os.getenv("MY_KEY")\n'}
        )
        result = _detect_env_vars(tmp)
        names = [r["name"] for r in result]
        assert "MY_TOKEN" in names
        assert "MY_KEY" in names

    def test_go_os_getenv(self):
        from observal_cli.analyzer import _detect_env_vars

        tmp = _make_tmpdir_with_files(
            {"cmd/main.go": 'package main\nimport "os"\nfunc main() { os.Getenv("API_TOKEN") }\n'}
        )
        result = _detect_env_vars(tmp)
        names = [r["name"] for r in result]
        assert "API_TOKEN" in names

    def test_typescript_process_env(self):
        from observal_cli.analyzer import _detect_env_vars

        tmp = _make_tmpdir_with_files(
            {"src/index.ts": 'const key = process.env.OPENAI_KEY;\nconst s = process.env["MY_SECRET"];\n'}
        )
        result = _detect_env_vars(tmp)
        names = [r["name"] for r in result]
        assert "OPENAI_KEY" in names
        assert "MY_SECRET" in names

    def test_skips_test_directories(self):
        from observal_cli.analyzer import _detect_env_vars

        tmp = _make_tmpdir_with_files(
            {
                "tests/test_config.py": 'os.environ["TEST_ONLY_VAR"]',
                "src/main.py": 'os.getenv("REAL_VAR")',
            }
        )
        result = _detect_env_vars(tmp)
        names = [r["name"] for r in result]
        assert "REAL_VAR" in names
        assert "TEST_ONLY_VAR" not in names

    def test_filters_internal_vars_from_code(self):
        from observal_cli.analyzer import _detect_env_vars

        tmp = _make_tmpdir_with_files(
            {"src/app.py": 'os.environ["PATH"]\nos.getenv("HOME")\nos.getenv("MY_CUSTOM_VAR")\n'}
        )
        result = _detect_env_vars(tmp)
        names = [r["name"] for r in result]
        assert "MY_CUSTOM_VAR" in names
        assert "PATH" not in names
        assert "HOME" not in names


# ═══════════════════════════════════════════════════════════
# 4. Server-side _detect_env_vars (mirrors CLI)
# ═══════════════════════════════════════════════════════════


class TestServerEnvDetection:
    """Verify server-side mcp_validator._detect_env_vars produces same results."""

    def test_server_json_manifest(self):
        from services.mcp_validator import _detect_env_vars

        manifest = {
            "packages": [
                {
                    "runtimeArguments": [
                        {"value": "MY_TOKEN={val}", "description": "desc"},
                    ]
                }
            ]
        }
        tmp = _make_tmpdir_with_files({"server.json": json.dumps(manifest)})
        result = _detect_env_vars(tmp)
        names = [r["name"] for r in result]
        assert "MY_TOKEN" in names

    def test_readme_tier(self):
        from services.mcp_validator import _detect_env_vars

        tmp = _make_tmpdir_with_files({"README.md": "export CUSTOM_KEY=value"})
        result = _detect_env_vars(tmp)
        names = [r["name"] for r in result]
        assert "CUSTOM_KEY" in names

    def test_source_scanning(self):
        from services.mcp_validator import _detect_env_vars

        tmp = _make_tmpdir_with_files({"main.go": 'package main\nimport "os"\nfunc f() { os.Getenv("GO_TOKEN") }\n'})
        result = _detect_env_vars(tmp)
        names = [r["name"] for r in result]
        assert "GO_TOKEN" in names


# ═══════════════════════════════════════════════════════════
# 5. Config Generation: _build_run_command
# ═══════════════════════════════════════════════════════════


class TestBuildRunCommand:
    def test_docker_image_generates_docker_run(self):
        from services.config_generator import _build_run_command

        cmd = _build_run_command("my-mcp", "go", docker_image="ghcr.io/org/server:latest")
        assert cmd[0] == "docker"
        assert cmd[1] == "run"
        assert "ghcr.io/org/server:latest" in cmd

    def test_docker_image_with_env_vars(self):
        from services.config_generator import _build_run_command

        cmd = _build_run_command(
            "my-mcp",
            "go",
            docker_image="ghcr.io/org/server:latest",
            server_env={"GITHUB_PERSONAL_ACCESS_TOKEN": "tok123"},
        )
        assert "-e" in cmd
        assert "GITHUB_PERSONAL_ACCESS_TOKEN=tok123" in cmd
        # -e flags must come before the image
        image_idx = cmd.index("ghcr.io/org/server:latest")
        e_idx = cmd.index("-e")
        assert e_idx < image_idx

    def test_no_docker_image_typescript(self):
        from services.config_generator import _build_run_command

        cmd = _build_run_command("my-mcp", "typescript-mcp-sdk")
        assert cmd == ["npx", "-y", "my-mcp"]

    def test_no_docker_image_go(self):
        from services.config_generator import _build_run_command

        cmd = _build_run_command("my-mcp", "go-mcp-sdk")
        assert cmd == ["my-mcp"]

    def test_no_docker_image_python(self):
        from services.config_generator import _build_run_command

        cmd = _build_run_command("my-mcp", "python-mcp")
        assert cmd == ["python", "-m", "my-mcp"]

    def test_no_docker_image_none_framework(self):
        from services.config_generator import _build_run_command

        cmd = _build_run_command("my-mcp", None)
        assert cmd == ["python", "-m", "my-mcp"]

    def test_docker_image_overrides_framework(self):
        """Any framework with a docker_image should use docker run."""
        from services.config_generator import _build_run_command

        for fw in ("python-mcp", "typescript-mcp-sdk", "go-mcp-sdk", None):
            cmd = _build_run_command("my-mcp", fw, docker_image="img:latest")
            assert cmd[0] == "docker", f"Framework {fw} with docker_image should use docker run"


# ═══════════════════════════════════════════════════════════
# 6. Config Generation: generate_config with docker listing
# ═══════════════════════════════════════════════════════════


class TestGenerateConfigDocker:
    def _make_listing(self, **kw):
        listing = MagicMock()
        listing.name = kw.get("name", "github-mcp-server")
        listing.id = kw.get("listing_id", "abc-123")
        listing.docker_image = kw.get("docker_image", "ghcr.io/github/github-mcp-server")
        listing.framework = kw.get("framework", "go")
        listing.command = kw.get("command")
        listing.args = kw.get("args")
        listing.url = kw.get("url")
        listing.transport = kw.get("transport")
        listing.auto_approve = kw.get("auto_approve")
        listing.environment_variables = kw.get(
            "environment_variables",
            [{"name": "GITHUB_PERSONAL_ACCESS_TOKEN", "description": "", "required": True}],
        )
        return listing

    def test_cursor_docker_config(self):
        from services.config_generator import generate_config

        listing = self._make_listing()
        cfg = generate_config(listing, "cursor", env_values={"GITHUB_PERSONAL_ACCESS_TOKEN": "tok"})
        server = cfg["mcpServers"]["github-mcp-server"]
        assert server["command"] == "docker"
        assert "ghcr.io/github/github-mcp-server" in server["args"]

    def test_claude_code_docker_config(self):
        from services.config_generator import generate_config

        listing = self._make_listing()
        cfg = generate_config(listing, "claude-code", env_values={"GITHUB_PERSONAL_ACCESS_TOKEN": "tok"})
        assert cfg["type"] == "shell_command"
        # The command should contain docker run
        cmd = cfg["command"]
        assert "docker" in cmd
        assert "ghcr.io/github/github-mcp-server" in cmd


# ═══════════════════════════════════════════════════════════
# 7. Agent Config Generator: Claude Code with docker MCP
# ═══════════════════════════════════════════════════════════


class TestAgentConfigDockerMcp:
    def _make_agent(self, mcp_component_id=None):
        agent = MagicMock()
        agent.name = "test-agent"
        agent.id = "agent-123"
        agent.prompt = "You are a test agent."
        agent.description = "A test agent"
        agent.model_name = None
        comp = MagicMock()
        comp.component_type = "mcp"
        comp.component_id = mcp_component_id or uuid.uuid4()
        agent.components = [comp]
        agent.external_mcps = []
        return agent

    def _make_listing(self):
        listing = MagicMock()
        listing.name = "github-mcp-server"
        listing.id = uuid.uuid4()
        listing.docker_image = "ghcr.io/github/github-mcp-server"
        listing.framework = "go"
        listing.command = None
        listing.args = None
        listing.url = None
        listing.transport = None
        listing.auto_approve = None
        listing.environment_variables = [{"name": "GITHUB_PERSONAL_ACCESS_TOKEN", "description": "", "required": True}]
        return listing

    def test_claude_code_passes_docker_image(self):
        from services.harness import generate_agent_config

        listing = self._make_listing()
        comp_id = uuid.uuid4()
        agent = self._make_agent(mcp_component_id=comp_id)

        cfg = generate_agent_config(
            agent,
            "claude-code",
            mcp_listings={comp_id: listing},
            env_values={str(listing.id): {"GITHUB_PERSONAL_ACCESS_TOKEN": "tok"}},
        )
        mcp_config = cfg["mcp_config"]
        assert "github-mcp-server" in mcp_config
        mcp_entry = mcp_config["github-mcp-server"]
        assert mcp_entry["command"] == "docker"
        args_str = " ".join(mcp_entry["args"])
        assert "ghcr.io/github/github-mcp-server" in args_str

    def test_claude_code_passes_env_vars(self):
        from services.harness import generate_agent_config

        listing = self._make_listing()
        comp_id = uuid.uuid4()
        agent = self._make_agent(mcp_component_id=comp_id)

        cfg = generate_agent_config(
            agent,
            "claude-code",
            mcp_listings={comp_id: listing},
            env_values={str(listing.id): {"GITHUB_PERSONAL_ACCESS_TOKEN": "tok"}},
        )
        mcp_entry = cfg["mcp_config"]["github-mcp-server"]
        assert mcp_entry["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "tok"


# ═══════════════════════════════════════════════════════════
# 8. MCP Submit: Auto-replace pending/rejected listings
# ═══════════════════════════════════════════════════════════


class TestMcpSubmitAutoReplace:
    @pytest.mark.asyncio
    async def test_replace_pending_on_resubmit(self):
        from api.routes.mcp import router

        user = _user()
        db = _mock_db()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = lambda: db

        # Existing pending listing with same name
        existing = MagicMock()
        existing.id = uuid.uuid4()
        existing.name = "github-mcp-server"
        existing.status = ListingStatus.pending
        existing.submitted_by = user.id

        # First execute returns existing, second returns results for any other queries
        db.execute = AsyncMock(return_value=_scalar_result(existing))

        def _refresh(obj):
            obj.id = uuid.uuid4()
            obj.created_at = datetime.now(UTC)
            obj.updated_at = datetime.now(UTC)
            # Simulate relationship loading after refresh
            version_mock = MagicMock()
            version_mock.version = "1.0.0"
            version_mock.description = "GitHub MCP server"
            version_mock.status = ListingStatus.pending
            version_mock.mcp_validated = False
            version_mock.framework = None
            version_mock.rejection_reason = None
            version_mock.supported_harnesses = []
            version_mock.environment_variables = []
            version_mock.setup_instructions = None
            version_mock.changelog = None
            version_mock.source_url = "https://github.com/github/github-mcp-server"
            version_mock.docker_image = None
            version_mock.command = None
            version_mock.args = None
            version_mock.url = None
            version_mock.headers = None
            version_mock.auto_approve = None
            version_mock.download_count = 0
            version_mock.transport = None
            version_mock.tools_schema = None
            obj.latest_version = version_mock

        db.refresh = AsyncMock(side_effect=_refresh)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/mcps/submit",
                json={
                    "name": "github-mcp-server",
                    "version": "1.0.0",
                    "git_url": "https://github.com/github/github-mcp-server",
                    "description": "GitHub MCP server",
                    "category": "version-control",
                    "owner": "github",
                    "client_analysis": {"tools": [], "issues": []},
                },
            )

        assert r.status_code == 200
        # The old listing should have been deleted
        db.delete.assert_called_once_with(existing)
        assert db.flush.call_count >= 1  # flush for delete + listing + version

    @pytest.mark.asyncio
    async def test_reject_resubmit_of_approved(self):
        from api.routes.mcp import router

        user = _user()
        db = _mock_db()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = lambda: db

        existing = MagicMock()
        existing.id = uuid.uuid4()
        existing.name = "github-mcp-server"
        existing.status = ListingStatus.approved
        existing.submitted_by = user.id

        db.execute = AsyncMock(return_value=_scalar_result(existing))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/mcps/submit",
                json={
                    "name": "github-mcp-server",
                    "version": "1.0.0",
                    "git_url": "https://github.com/github/github-mcp-server",
                    "description": "GitHub MCP server",
                    "category": "version-control",
                    "owner": "github",
                    "client_analysis": {"tools": [], "issues": []},
                },
            )

        assert r.status_code == 409
        assert "approved" in r.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_no_existing_submits_normally(self):
        from api.routes.mcp import router

        user = _user()
        db = _mock_db()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = lambda: db

        db.execute = AsyncMock(return_value=_scalar_result(None))

        def _refresh(obj):
            obj.id = uuid.uuid4()
            obj.created_at = datetime.now(UTC)
            obj.updated_at = datetime.now(UTC)
            # Simulate relationship loading after refresh
            version_mock = MagicMock()
            version_mock.version = "1.0.0"
            version_mock.description = "GitHub MCP server"
            version_mock.status = ListingStatus.pending
            version_mock.mcp_validated = False
            version_mock.framework = None
            version_mock.rejection_reason = None
            version_mock.supported_harnesses = []
            version_mock.environment_variables = []
            version_mock.setup_instructions = None
            version_mock.changelog = None
            version_mock.source_url = "https://github.com/github/github-mcp-server"
            version_mock.docker_image = None
            version_mock.command = None
            version_mock.args = None
            version_mock.url = None
            version_mock.headers = None
            version_mock.auto_approve = None
            version_mock.download_count = 0
            version_mock.transport = None
            version_mock.tools_schema = None
            obj.latest_version = version_mock

        db.refresh = AsyncMock(side_effect=_refresh)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/mcps/submit",
                json={
                    "name": "github-mcp-server",
                    "version": "1.0.0",
                    "git_url": "https://github.com/github/github-mcp-server",
                    "description": "GitHub MCP server",
                    "category": "version-control",
                    "owner": "github",
                    "client_analysis": {"tools": [], "issues": []},
                },
            )

        assert r.status_code == 200
        # No delete should have been called
        db.delete.assert_not_called()


# ═══════════════════════════════════════════════════════════
# 9. _build_run_command with stored command/args
# ═══════════════════════════════════════════════════════════


class TestBuildRunCommandWithStoredArgs:
    def test_stored_command_args_used_directly(self):
        from services.config_generator import _build_run_command

        cmd = _build_run_command("my-mcp", "python", stored_command="docker", stored_args=["run", "img"])
        assert cmd == ["docker", "run", "img"]

    def test_stored_command_without_args(self):
        from services.config_generator import _build_run_command

        cmd = _build_run_command("my-mcp", "python", stored_command="my-binary")
        assert cmd == ["my-binary"]

    def test_stored_command_overrides_framework(self):
        from services.config_generator import _build_run_command

        cmd = _build_run_command("my-mcp", "python", stored_command="npx", stored_args=["-y", "pkg"])
        assert cmd[0] == "npx"
        assert cmd == ["npx", "-y", "pkg"]

    def test_no_stored_falls_back_to_framework(self):
        from services.config_generator import _build_run_command

        cmd = _build_run_command("my-mcp", "python-mcp")
        assert cmd == ["python", "-m", "my-mcp"]

    def test_no_stored_falls_back_to_docker(self):
        from services.config_generator import _build_run_command

        cmd = _build_run_command("my-mcp", None, docker_image="ghcr.io/org/img:latest")
        assert cmd[0] == "docker"
        assert "ghcr.io/org/img:latest" in cmd


# ═══════════════════════════════════════════════════════════
# 10. Config Generation: SSE transport
# ═══════════════════════════════════════════════════════════


class TestGenerateConfigSSE:
    def _make_listing(self, **kw):
        listing = MagicMock()
        listing.name = kw.get("name", "my-sse-server")
        listing.id = kw.get("listing_id", "sse-123")
        listing.url = kw.get("url", "https://example.com/mcp")
        listing.transport = kw.get("transport", "sse")
        listing.command = kw.get("command")
        listing.args = kw.get("args")
        listing.docker_image = kw.get("docker_image")
        listing.framework = kw.get("framework")
        listing.auto_approve = kw.get("auto_approve", ["tool_name"])
        listing.headers = kw.get(
            "headers",
            [{"name": "Authorization", "description": "Bearer token", "required": True}],
        )
        listing.environment_variables = kw.get("environment_variables", [])
        return listing

    def test_cursor_sse_config(self):
        from services.config_generator import generate_config

        listing = self._make_listing()
        cfg = generate_config(
            listing,
            "cursor",
            header_values={"Authorization": "Bearer tok123"},
        )
        server = cfg["mcpServers"]["my-sse-server"]
        assert server["type"] == "sse"
        assert server["url"] == "https://example.com/mcp"
        assert server["headers"] == {"Authorization": "Bearer tok123"}
        assert server["autoApprove"] == ["tool_name"]
        assert server["disabled"] is False

    def test_claude_code_sse_config(self):
        from services.config_generator import generate_config

        listing = self._make_listing()
        cfg = generate_config(listing, "claude-code", header_values={"Authorization": "Bearer tok123"})
        assert cfg["type"] == "shell_command"
        # `claude mcp add` has no --url option: transport flag, then name and URL
        assert cfg["command"] == [
            "claude",
            "mcp",
            "add",
            "--transport",
            "sse",
            "my-sse-server",
            "https://example.com/mcp",
            "-H",
            "Authorization: Bearer tok123",
        ]
        # mcpServers should also be present
        server = cfg["mcpServers"]["my-sse-server"]
        assert server["type"] == "sse"

    def test_sse_without_headers(self):
        from services.config_generator import generate_config

        listing = self._make_listing(headers=[])
        cfg = generate_config(listing, "cursor")
        server = cfg["mcpServers"]["my-sse-server"]
        assert "headers" not in server


# ═══════════════════════════════════════════════════════════
# 11. Dollar-sign variable detection (CLI)
# ═══════════════════════════════════════════════════════════


class TestDollarVarDetection:
    def test_extract_from_args(self):
        from observal_cli.cmd_mcp import _extract_dollar_vars

        result = _extract_dollar_vars(["-v", "$USER_VOLUME_PATH:/data", "--host", "$SERVER_HOST"], {})
        assert "USER_VOLUME_PATH" in result
        assert "SERVER_HOST" in result

    def test_extract_from_env_values(self):
        from observal_cli.cmd_mcp import _extract_dollar_vars

        result = _extract_dollar_vars([], {"JIRA_URL": "$JIRA_BASE_URL", "TOKEN": "$JIRA_TOKEN"})
        assert "JIRA_BASE_URL" in result
        assert "JIRA_TOKEN" in result

    def test_extract_braces_form(self):
        from observal_cli.cmd_mcp import _extract_dollar_vars

        result = _extract_dollar_vars(["${MY_VAR}"], {})
        assert "MY_VAR" in result

    def test_filters_system_vars(self):
        from observal_cli.cmd_mcp import _extract_dollar_vars

        result = _extract_dollar_vars(["$HOME/path", "$MY_CUSTOM"], {})
        assert "MY_CUSTOM" in result
        assert "HOME" not in result

    def test_dedup_across_args_and_env(self):
        from observal_cli.cmd_mcp import _extract_dollar_vars

        result = _extract_dollar_vars(["$JIRA_URL"], {"URL": "$JIRA_URL"})
        assert result.count("JIRA_URL") == 1

    def test_ignores_lowercase(self):
        from observal_cli.cmd_mcp import _extract_dollar_vars

        result = _extract_dollar_vars(["$lowercase_var"], {})
        assert result == []

    def test_multiple_vars_in_one_arg(self):
        from observal_cli.cmd_mcp import _extract_dollar_vars

        result = _extract_dollar_vars(["$USER_PATH:/data/$SUBDIR"], {})
        assert "USER_PATH" in result
        assert "SUBDIR" in result


# ═══════════════════════════════════════════════════════════
# 12. Dollar-sign variable substitution (server)
# ═══════════════════════════════════════════════════════════


class TestDollarVarSubstitution:
    def test_substitute_in_stored_args(self):
        from services.config_generator import _build_run_command

        cmd = _build_run_command(
            "my-mcp",
            "docker",
            server_env={"VOL": "/tmp/vol"},
            stored_command="docker",
            stored_args=["run", "-v", "$VOL:/data", "image"],
        )
        assert cmd == ["docker", "run", "-v", "/tmp/vol:/data", "image"]

    def test_preserves_unmatched(self):
        from services.config_generator import _substitute_dollar_vars

        result = _substitute_dollar_vars(["$UNKNOWN_VAR"], {"OTHER": "val"})
        assert result == ["$UNKNOWN_VAR"]

    def test_substitute_braces_form(self):
        from services.config_generator import _substitute_dollar_vars

        result = _substitute_dollar_vars(["${MY_VAR}"], {"MY_VAR": "replaced"})
        assert result == ["replaced"]

    def test_no_substitution_without_env(self):
        from services.config_generator import _substitute_dollar_vars

        result = _substitute_dollar_vars(["$VAR"], None)
        assert result == ["$VAR"]

    def test_multiple_vars_in_one_arg(self):
        from services.config_generator import _substitute_dollar_vars

        result = _substitute_dollar_vars(["$USER_PATH:/data/$SUBDIR"], {"USER_PATH": "/home/u", "SUBDIR": "out"})
        assert result == ["/home/u:/data/out"]


# ═══════════════════════════════════════════════════════════
# 13. _parse_direct_config with dollar-sign vars
# ═══════════════════════════════════════════════════════════


class TestParseDirectConfigDollarVars:
    def test_detects_dollar_vars_in_args(self):
        from observal_cli.cmd_mcp import _parse_direct_config

        cfg = {
            "command": "docker",
            "args": ["run", "-v", "$USER_PATH:/data", "myimage"],
        }
        parsed = _parse_direct_config(cfg)
        names = {ev["name"] for ev in parsed.get("environment_variables", [])}
        assert "USER_PATH" in names
        assert parsed.get("_dollar_vars_detected")
        assert "USER_PATH" in parsed["_dollar_vars_detected"]

    def test_merges_env_keys_and_dollar_vars(self):
        from observal_cli.cmd_mcp import _parse_direct_config

        cfg = {
            "command": "docker",
            "args": ["run", "myimage"],
            "env": {"API_KEY": "sk-xxx", "URL": "$BASE_URL"},
        }
        parsed = _parse_direct_config(cfg)
        names = [ev["name"] for ev in parsed.get("environment_variables", [])]
        # API_KEY from env key, URL from env key, BASE_URL from dollar-sign in value
        assert "API_KEY" in names
        assert "URL" in names
        assert "BASE_URL" in names
        # No duplicates
        assert len(names) == len(set(names))

    def test_no_flag_when_no_dollar_vars(self):
        from observal_cli.cmd_mcp import _parse_direct_config

        cfg = {
            "command": "python",
            "args": ["-m", "my_server"],
            "env": {"API_KEY": "sk-xxx"},
        }
        parsed = _parse_direct_config(cfg)
        assert "_dollar_vars_detected" not in parsed


# ═══════════════════════════════════════════════════════════
# 14. _dollar_to_placeholder
# ═══════════════════════════════════════════════════════════


class TestDollarToPlaceholder:
    def test_bearer_single_token(self):
        from observal_cli.cmd_mcp import _dollar_to_placeholder

        assert _dollar_to_placeholder("Bearer $TOKEN") == "Bearer <TOKEN>"

    def test_bearer_braces_form(self):
        from observal_cli.cmd_mcp import _dollar_to_placeholder

        assert _dollar_to_placeholder("Bearer ${TOKEN}") == "Bearer <TOKEN>"

    def test_bearer_multiple_tokens(self):
        from observal_cli.cmd_mcp import _dollar_to_placeholder

        assert _dollar_to_placeholder("Bearer $TOKEN1 $TOKEN2") == "Bearer <TOKEN1> <TOKEN2>"

    def test_bearer_mixed_literal(self):
        from observal_cli.cmd_mcp import _dollar_to_placeholder

        result = _dollar_to_placeholder("Bearer $TOKEN1, token2")
        assert result == "Bearer <TOKEN1>, token2"

    def test_bare_variable(self):
        from observal_cli.cmd_mcp import _dollar_to_placeholder

        assert _dollar_to_placeholder("$API_KEY") == "<API_KEY>"

    def test_no_variables(self):
        from observal_cli.cmd_mcp import _dollar_to_placeholder

        assert _dollar_to_placeholder("static-value") == "static-value"

    def test_empty_string(self):
        from observal_cli.cmd_mcp import _dollar_to_placeholder

        assert _dollar_to_placeholder("") == ""

    def test_multiple_vars_in_path(self):
        from observal_cli.cmd_mcp import _dollar_to_placeholder

        assert _dollar_to_placeholder("$HOST:$PORT/api") == "<HOST>:<PORT>/api"


# ═══════════════════════════════════════════════════════════
# 15. _build_config_preview dollar-var placeholders
# ═══════════════════════════════════════════════════════════


class TestBuildConfigPreviewPlaceholders:
    def test_sse_header_bearer_token_placeholder(self):
        from observal_cli.cmd_mcp import _build_config_preview

        parsed = {
            "transport": "sse",
            "url": "https://example.com/sse",
            "headers": [
                {"name": "Authorization", "value": "Bearer $TOKEN", "description": "", "required": True},
            ],
            "environment_variables": [{"name": "TOKEN", "description": "", "required": True}],
        }
        result = _build_config_preview("test-server", parsed)
        headers = result["test-server"]["headers"]
        assert headers["Authorization"] == "Bearer <TOKEN>"

    def test_sse_header_multiple_dollar_vars(self):
        from observal_cli.cmd_mcp import _build_config_preview

        parsed = {
            "transport": "sse",
            "url": "https://example.com/sse",
            "headers": [
                {"name": "Authorization", "value": "Bearer $TOKEN1 $TOKEN2", "description": "", "required": True},
            ],
            "environment_variables": [],
        }
        result = _build_config_preview("test-server", parsed)
        headers = result["test-server"]["headers"]
        assert headers["Authorization"] == "Bearer <TOKEN1> <TOKEN2>"

    def test_sse_header_no_dollar_uses_name_fallback(self):
        from observal_cli.cmd_mcp import _build_config_preview

        parsed = {
            "transport": "sse",
            "url": "https://example.com/sse",
            "headers": [
                {"name": "X-Custom", "description": "", "required": True},
            ],
            "environment_variables": [],
        }
        result = _build_config_preview("test-server", parsed)
        headers = result["test-server"]["headers"]
        assert headers["X-Custom"] == "<X-Custom>"

    def test_stdio_args_dollar_vars_replaced(self):
        from observal_cli.cmd_mcp import _build_config_preview

        parsed = {
            "transport": "stdio",
            "command": "node",
            "args": ["server.js", "--token=$API_KEY", "--host", "$HOST"],
            "environment_variables": [
                {"name": "API_KEY", "description": "", "required": True},
                {"name": "HOST", "description": "", "required": True},
            ],
        }
        result = _build_config_preview("test-server", parsed)
        args = result["test-server"]["args"]
        assert "--token=<API_KEY>" in args
        assert "<HOST>" in args

    def test_stdio_args_no_dollar_unchanged(self):
        from observal_cli.cmd_mcp import _build_config_preview

        parsed = {
            "transport": "stdio",
            "command": "python",
            "args": ["-m", "my_server"],
            "environment_variables": [],
        }
        result = _build_config_preview("test-server", parsed)
        args = result["test-server"]["args"]
        assert args == ["-m", "my_server"]
