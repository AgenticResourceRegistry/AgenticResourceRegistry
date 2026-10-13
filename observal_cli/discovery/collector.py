# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Bounded, read-only harness inventory; never consults the Registry."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from observal_cli.discovery.bounded_walk import AggregateDiscoveryBudget, discovery_budget
from observal_cli.discovery.models import AdapterDiscoveryResult, DiagnosticCode, DiagnosticSeverity
from observal_cli.discovery.redact import make_diagnostic

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from observal_cli.harness import HarnessAdapter


def collect_local_inventory(
    adapters: Mapping[str, HarnessAdapter],
    *,
    home: Path,
    project_dir: Path,
    budget: AggregateDiscoveryBudget | None = None,
    deadline_seconds: float = 30.0,
) -> AdapterDiscoveryResult:
    result = AdapterDiscoveryResult()
    budget = budget or AggregateDiscoveryBudget()
    deadline = time.monotonic() + deadline_seconds
    for name in sorted(adapters):
        if time.monotonic() >= deadline:
            if budget.diagnostics < budget.max_diagnostics:
                budget.diagnostics += 1
                result.diagnostics.append(
                    make_diagnostic(
                        DiagnosticCode.ADAPTER_DEADLINE_EXCEEDED,
                        DiagnosticSeverity.WARNING,
                        "harness",
                        "total local inventory deadline exceeded",
                    )
                )
            break
        adapter = adapters[name]
        with discovery_budget(budget, deadline=min(deadline, time.monotonic() + 10)):
            for discover, root in ((adapter.discover_home, home), (adapter.discover_project, project_dir)):
                found = discover(root)
                result.evidence.extend(found.evidence)
                result.diagnostics.extend(found.diagnostics)
                limits = (
                    (budget.roots >= budget.max_roots, DiagnosticCode.APPROVED_ROOT_LIMIT_REACHED),
                    (budget.files >= budget.max_files, DiagnosticCode.COLLECTION_FILE_LIMIT_REACHED),
                    (budget.entries >= budget.max_entries, DiagnosticCode.COLLECTION_ENTRY_LIMIT_REACHED),
                    (budget.evidence >= budget.max_evidence, DiagnosticCode.EVIDENCE_LIMIT_REACHED),
                )
                for reached, code in limits:
                    if reached:
                        if code not in budget.emitted_limits and budget.diagnostics < budget.max_diagnostics:
                            budget.emitted_limits.add(code)
                            budget.diagnostics += 1
                            result.diagnostics.append(
                                make_diagnostic(
                                    code, DiagnosticSeverity.WARNING, "harness", "local inventory limit reached"
                                )
                            )
                        return result
    return result
