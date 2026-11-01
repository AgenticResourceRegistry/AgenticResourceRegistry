# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for sandbox runner and config generators."""

import uuid
from unittest.mock import MagicMock, patch

import pytest

# ── Sandbox Runner ──────────────────────────────────────────────────


class TestSandboxRunner:
    def test_now_iso_format(self):
        from observal_cli.sandbox_runner import _now_iso

        ts = _now_iso()
        assert len(ts) == 23  # YYYY-MM-DD HH:MM:SS.mmm
        assert "-" in ts and ":" in ts

    def test_max_log_bytes(self):
        from observal_cli.sandbox_runner import MAX_LOG_BYTES

        assert MAX_LOG_BYTES == 64 * 1024

    def test_send_span_no_creds(self):
        """send_span should silently return when no server_url or api_key."""
        from observal_cli.sandbox_runner import _send_span

        _send_span("", "", {"test": True})  # should not raise
        _send_span("http://localhost", "", {"test": True})
        _send_span("", "key", {"test": True})

    def test_send_span_noop_after_structured_telemetry_removal(self):
        from observal_cli.sandbox_runner import _send_span

        _send_span("http://localhost:8000", "test-key", {"span_id": "test"})

    def _run_with_mock_docker(
        self, mock_container, sandbox_id="test-id", image="alpine:latest", command=None, timeout=300
    ):
        """Helper: run sandbox with mocked Docker SDK, return (exit_code, span_sent)."""
        mock_client = MagicMock()
        mock_client.containers.run.return_value = mock_container

        mock_docker = MagicMock()
        mock_docker.from_env.return_value = mock_client

        captured_spans = []
        original_send = None

        with patch.dict("sys.modules", {"docker": mock_docker}):
            import importlib

            import observal_cli.sandbox_runner as sr

            importlib.reload(sr)

            original_send = sr._send_span
            sr._send_span = lambda url, key, span: captured_spans.append(span)

            try:
                sr.run_sandbox(sandbox_id, image, command, timeout)
            except SystemExit as e:
                return e.code, captured_spans[0] if captured_spans else None

        return None, None

    def test_run_sandbox_captures_logs(self):
        """Test that run_sandbox captures container logs via Docker SDK."""
        mock_container = MagicMock()
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.return_value = b"hello from container\n"
        mock_container.short_id = "abc123"
        mock_container.attrs = {"State": {"OOMKilled": False}}
        mock_container.reload.return_value = None

        exit_code, span = self._run_with_mock_docker(mock_container, command="echo hello", timeout=30)

        assert exit_code == 0
        mock_container.logs.assert_called_once_with(stdout=True, stderr=True)
        mock_container.wait.assert_called_once_with(timeout=30)
        mock_container.remove.assert_called_once_with(force=True)

        assert span is not None
        assert span["type"] == "sandbox_exec"
        assert span["output"] == "hello from container\n"
        assert span["exit_code"] == 0
        assert span["container_id"] == "abc123"
        assert span["oom_killed"] is False

    def test_run_sandbox_error_exit_code(self):
        mock_container = MagicMock()
        mock_container.wait.return_value = {"StatusCode": 1}
        mock_container.logs.return_value = b"error occurred\n"
        mock_container.short_id = "def456"
        mock_container.attrs = {"State": {"OOMKilled": False}}
        mock_container.reload.return_value = None

        exit_code, span = self._run_with_mock_docker(mock_container, command="false")

        assert exit_code == 1
        assert span["status"] == "error"
        assert span["exit_code"] == 1
        assert "exit_code=1" in span["error"]

    def test_run_sandbox_oom_detected(self):
        mock_container = MagicMock()
        mock_container.wait.return_value = {"StatusCode": 137}
        mock_container.logs.return_value = b"killed\n"
        mock_container.short_id = "oom789"
        mock_container.attrs = {"State": {"OOMKilled": True}}
        mock_container.reload.return_value = None

        exit_code, span = self._run_with_mock_docker(mock_container)

        assert span["oom_killed"] is True

    def test_run_sandbox_truncates_large_logs(self):
        from observal_cli.sandbox_runner import MAX_LOG_BYTES

        mock_container = MagicMock()
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.return_value = b"x" * (MAX_LOG_BYTES + 1000)
        mock_container.short_id = "trunc"
        mock_container.attrs = {"State": {"OOMKilled": False}}
        mock_container.reload.return_value = None

        exit_code, span = self._run_with_mock_docker(mock_container)

        assert "[truncated at 64KB]" in span["output"]
        assert len(span["output"]) < MAX_LOG_BYTES + 100


# ── Config Generators ───────────────────────────────────────────────


class _MockListing:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestSandboxMcpEntryBuilder:
    def test_basic(self):
        from services.harness.helpers import _build_sandbox_mcp_entry

        listing = _MockListing(id="s-123", name="sandbox-a", image="python:3.12", entrypoint=None, resource_limits={})
        config = _build_sandbox_mcp_entry({"s-123": listing}, "cursor")
        assert "observal-sandbox" in config
        assert config["observal-sandbox"]["command"] == "python3"
        assert "observal_cli.sandbox_mcp" in " ".join(config["observal-sandbox"]["args"])

    def test_empty(self):
        from services.harness.helpers import _build_sandbox_mcp_entry

        config = _build_sandbox_mcp_entry({}, "kiro")
        assert config == {}


class TestSkillConfigGenerator:
    def test_basic(self):
        from services.skill_config_generator import generate_skill_config

        listing = _MockListing(id="sk-123", name="python-expert", git_url=None, skill_path=None)
        config = generate_skill_config(listing, "kiro")
        assert "SessionStart" in config["hooks"]
        assert "SessionEnd" in config["hooks"]
        assert config["skill"]["name"] == "python-expert"

    def test_with_git_url(self):
        from services.skill_config_generator import generate_skill_config

        listing = _MockListing(
            id="sk-456", name="test-skill", git_url="https://github.com/example/skill.git", skill_path="skills/test"
        )
        config = generate_skill_config(listing, "claude-code")
        assert config["skill"]["git_url"] == "https://github.com/example/skill.git"
        assert config["skill"]["skill_path"] == "skills/test"
        # Claude Code should have allowedEnvVars
        hook = config["hooks"]["SessionStart"][0]["hooks"][0]
        assert "allowedEnvVars" in hook


# ── Install Route Wiring ────────────────────────────────────────────


class TestInstallRouteWiring:
    """Verify install routes call config generators instead of returning stubs."""

    @pytest.mark.asyncio
    async def test_skill_install_uses_config_generator(self):
        from unittest.mock import AsyncMock, patch

        from api.routes.skill import install_skill
        from models.mcp import ListingStatus
        from schemas.skill import SkillInstallRequest

        version = _MockListing(
            id=uuid.uuid4(),
            version="1.0.0",
            status=ListingStatus.approved,
            requires_global_review=False,
            delivery_mode="git_fetch",
            download_count=0,
        )
        listing = _MockListing(
            id=uuid.uuid4(),
            name="test-skill",
            git_url=None,
            skill_path=None,
            status=ListingStatus.approved,
            latest_version=version,
        )

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = listing
        mock_result.scalars.return_value.first.return_value = listing
        mock_db.execute.return_value = mock_result

        mock_user = MagicMock()
        mock_user.id = uuid.uuid4()

        req = SkillInstallRequest(harness="claude-code")
        with (
            patch("api.routes.skill._selected_skill_release", new_callable=AsyncMock, return_value=(listing, version)),
            patch(
                "api.routes.config.derive_endpoints",
                return_value={
                    "api": "http://localhost:8000",
                    "otlp_http": "http://localhost:8000",
                    "web": "http://localhost:3000",
                },
            ),
        ):
            resp = await install_skill(listing.id, req, MagicMock(), mock_db, mock_user)
        config = resp.config_snippet
        assert "hooks" in config
        assert "SessionStart" in config["hooks"]
