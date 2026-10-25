# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Agent routes package. Sub-modules register routes on the shared router."""

# Import sub-modules so they register their routes on the shared router.
from . import crud, draft, fork, insights, install  # noqa: F401
from ._router import router  # noqa: F401
from .helpers import _load_agent, _resolve_component_names, _resolve_component_statuses  # noqa: F401
from .install import install_agent  # noqa: F401
