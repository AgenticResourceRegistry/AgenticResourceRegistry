# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import json

from middleware import _cli_version_response


def test_dev_version_response_gives_source_install_command():
    response = _cli_version_response("1.15.0-dev.0", "1.13.1")
    body = json.loads(response.body)

    assert response.status_code == 426
    assert body["install_command"] == 'uv tool install --force "git+https://github.com/Observal/Observal@main"'
    assert body["install_command"] in body["detail"]


def test_rc_version_response_keeps_pypi_command():
    body = json.loads(_cli_version_response("1.15.0-rc.1", "1.13.1").body)

    assert body["install_command"] == "observal self upgrade --version 1.15.0-rc.1"


def test_release_version_response_keeps_upgrade_command():
    response = _cli_version_response("1.15.0", "1.13.1")
    body = json.loads(response.body)

    assert body["install_command"] == "observal self upgrade --version 1.15.0"
    assert body["install_command"] in body["detail"]
