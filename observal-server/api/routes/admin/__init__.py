# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Admin routes package. Sub-modules register routes on the shared router."""

# Import sub-modules so they register their routes on the shared router.
from . import (  # noqa: F401
    enterprise_settings,
    insights_models,
    migrate,
    policy,
    recommended,
    retention,
    system,
    usage_ping,
    users,
)
from ._router import router  # noqa: F401
