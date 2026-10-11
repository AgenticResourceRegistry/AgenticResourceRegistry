# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import json

from middleware import _cli_version_response


def test_prerelease_version_response_does_not_suggest_unavailable_package():
    response = _cli_version_response("1.15.0-dev.0", "1.13.1")
    body = json.loads(response.body)

    assert response.status_code == 426
    assert "install_command" not in body
    assert "same source revision" in body["detail"]
    assert "self upgrade" not in body["detail"]
    assert response.headers["X-Observal-Required-CLI"] == "1.15.0-dev.0"


def test_release_version_response_keeps_upgrade_command():
    response = _cli_version_response("1.15.0", "1.13.1")
    body = json.loads(response.body)

    assert body["install_command"] == "observal self upgrade --version 1.15.0"
    assert body["install_command"] in body["detail"]
