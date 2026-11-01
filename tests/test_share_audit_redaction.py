# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import re


def test_share_token_redacted_from_audit_path():
    path = "/api/v1/agent-shares/SECRET_TOKEN_123"
    assert re.sub(r"(/agent-shares/)[^/]+", r"\1{token}", path) == "/api/v1/agent-shares/{token}"
