# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

from observal_cli.install_detector import (
    SOURCE_REPO,
    SOURCE_SPEC,
    InstallInfo,
    InstallMethod,
    install_spec,
    upgrade_command,
)


def _info(method: InstallMethod) -> InstallInfo:
    return InstallInfo(method, Path("/x/observal"), True, None)


def test_dev_versions_build_from_source_and_releases_pin_pypi():
    assert install_spec("1.15.0-dev.0") == SOURCE_SPEC
    assert install_spec("1.15.0.dev0") == SOURCE_SPEC
    assert install_spec("1.14.0") == "observal-cli==1.14.0"
    assert install_spec("1.15.0-rc.1") == "observal-cli==1.15.0-rc.1"


def test_dev_upgrade_command_never_pins_unpublished_package():
    for method in InstallMethod:
        assert "observal-cli==" not in upgrade_command("1.15.0-dev.0", _info(method))
        assert SOURCE_SPEC in upgrade_command("1.15.0-dev.0", _info(method))


def test_executor_installs_dev_build_from_source(monkeypatch):
    from unittest.mock import MagicMock

    from observal_cli import upgrade_executor, version_check

    run = MagicMock(return_value=MagicMock(returncode=0, stderr=""))
    monkeypatch.setattr(upgrade_executor.subprocess, "run", run)
    commit = "a" * 40
    monkeypatch.setattr(version_check, "dev_source_refs", lambda *_args: [commit])
    from contextlib import nullcontext

    upgrade_executor._install_via_uv("1.15.0.dev0", "upgrade", lambda _m: nullcontext())

    # The published commit that declares the version, never the moving `main` branch.
    assert run.call_args.args[0] == ["uv", "tool", "install", f"{SOURCE_REPO}@{commit}", "--force"]


def test_426_remediation_is_the_clis_own_not_the_servers_install_command():
    """A 426 body's install_command can name any commit, including an unmerged one the CLI refuses to build."""
    import httpx
    import pytest

    from observal_cli.client import _handle_error
    from observal_cli.errors import CliError

    unmerged = (
        "uv tool install --force git+https://github.com/AgenticResourceRegistry/AgenticResourceRegistry@" + "c" * 40
    )
    resp = httpx.Response(
        426,
        json={"detail": "d", "server_version": "1.15.0-dev.0", "install_command": unmerged},
        request=httpx.Request("GET", "http://x/api"),
    )
    with pytest.raises(CliError) as exc:
        _handle_error(httpx.HTTPStatusError("e", request=resp.request, response=resp), "/api")
    assert unmerged not in exc.value.remediation
    assert "observal self" in exc.value.remediation and "1.15.0-dev.0" in exc.value.remediation
