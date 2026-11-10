# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Test container cleanup error logging in sandbox runner."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from observal_cli.sandbox_runner import _docker_run


class MockDockerError(Exception):
    """Mock DockerException deriving from Exception for testing."""
    pass


@pytest.fixture
def mock_docker_module():
    """Mock docker SDK module with DockerException."""
    mock_docker = types.ModuleType("docker")
    mock_errors = types.ModuleType("docker.errors")
    mock_errors.DockerException = MockDockerError
    mock_errors.APIError = MockDockerError
    mock_docker.errors = mock_errors

    client = MagicMock()
    mock_docker.from_env = MagicMock(return_value=client)

    container = MagicMock()
    container.wait.return_value = {"StatusCode": 0}
    container.logs.return_value = b"test log output"
    container.attrs = {"State": {"OOMKilled": False}}
    container.short_id = "test-cont-123"
    client.containers.run.return_value = container

    with patch.dict(sys.modules, {"docker": mock_docker, "docker.errors": mock_errors}):
        yield client, container


def test_docker_run_cleanup_failure_logged_and_exit_code_preserved(mock_docker_module, monkeypatch):
    """Assert failing container.remove logs a warning and preserves run exit code."""
    client, container = mock_docker_module
    remove_exc = MockDockerError("daemon connection lost")
    container.remove.side_effect = remove_exc

    monkeypatch.setattr("observal_cli.sandbox_runner._send_span", MagicMock())

    with patch("observal_cli.sandbox_runner.optic.warning") as mock_warning:
        with pytest.raises(SystemExit) as exit_info:
            _docker_run(
                sandbox_id="sb-test",
                image="alpine:latest",
                command="echo hello",
                timeout=10,
                env={},
                network_policy="none",
                resource_limits={},
            )

        assert exit_info.value.code == 0
        container.remove.assert_called_once_with(force=True)
        mock_warning.assert_called_once_with("Failed to remove container: error={}", remove_exc)


def test_docker_run_cleanup_success(mock_docker_module, monkeypatch):
    """Assert successful container.remove does not trigger warning log."""
    client, container = mock_docker_module
    container.remove.side_effect = None

    monkeypatch.setattr("observal_cli.sandbox_runner._send_span", MagicMock())

    with patch("observal_cli.sandbox_runner.optic.warning") as mock_warning:
        with pytest.raises(SystemExit) as exit_info:
            _docker_run(
                sandbox_id="sb-test",
                image="alpine:latest",
                command="echo hello",
                timeout=10,
                env={},
                network_policy="none",
                resource_limits={},
            )

        assert exit_info.value.code == 0
        container.remove.assert_called_once_with(force=True)
        mock_warning.assert_not_called()
