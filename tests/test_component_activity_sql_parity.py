# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Pre-consolidation SQL fingerprints for the three scoped session reads.

These hash the entire query (including cohort and joins), not synthetic
execution results. If query semantics deliberately change, update the
fingerprints only after checking all three evidence types and running the
opt-in ClickHouse integration suites against disposable databases.
"""

import hashlib

import pytest

from services.component_activity import hook_queries, queries, skill_queries


@pytest.mark.parametrize(
    ("module", "attribute", "expected"),
    [
        (queries, "_SESSIONS", "d9e7a04796de15d69f2bae6b0964361ba5a4ea3abd7b52953f09184b187daf4c"),
        (skill_queries, "_SKILL_SESSIONS", "ba98982852aff0e016328f9aebc944c5727f27e3df95d204f9525922b66a2911"),
        (hook_queries, "_HOOK_SESSIONS", "c39efbf248d5604c646ad363aa1266076510743823b43fffbd7d168f0cbfb4f6"),
    ],
)
def test_session_cohort_sql_unchanged(module, attribute, expected):
    assert hashlib.sha256(getattr(module, attribute).encode()).hexdigest() == expected


def test_evidence_kind_is_scoped_per_publication():
    for evidence_type in ("mcp", "skill", "hook"):
        publication = queries.latest_publication_sql(evidence_type)
        markers = queries.marker_states_sql(evidence_type)
        assert f"evidence_type = '{evidence_type}'" in publication
        assert f"evidence_type = '{evidence_type}'" in markers
    with pytest.raises(ValueError):
        queries.latest_publication_sql("mcp' OR 1=1")
    with pytest.raises(ValueError):
        queries.cohort_session_joins_sql("", "", "", "other")
