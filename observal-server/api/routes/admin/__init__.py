# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Admin routes package. Sub-modules register routes on the shared router."""

# Import sub-modules so they register their routes on the shared router.
from . import (  # noqa: F401
    enterprise_settings,
    insights_models,
    migrate,
    otlp_forwarding,
    policy,
    recommended,
    retention,
    system,
    usage_ping,
    users,
)
from ._router import router  # noqa: F401
