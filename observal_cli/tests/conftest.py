# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import os
import tempfile

# Same guard as tests/conftest.py: never touch the developer's real home.
os.environ["HOME"] = os.environ["USERPROFILE"] = tempfile.mkdtemp(prefix="observal-test-home-")
