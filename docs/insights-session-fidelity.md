<!-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Shared Insights excerpt and cache fidelity

This change is incremental shared work, not another Kiro/Codex parser implementation. Canonical Kiro #2032 is on main; canonical Codex #2035 remains a prerequisite. Keep this PR draft until #2035 merges, then rebase/retest.

- Preserve both ends of bounded answers and the actual task inside delegation envelopes; explicitly label omitted text.
- Include a raw-source digest in agent facet eligibility and upsert the exact scoped cache row.
- Promote shared excerpt semantics to facet schema 3 after the canonical harness-specific v2 fixes; retain both harness dispatchers when integrating.
- Expose per-session processing coverage rather than treating missing facets as no findings.
- Do not rewrite historical transcripts/reports or change agent profiles.

The guarded `tests/runtime/verify_insights_fidelity.py` defaults to existing Kiro/Pi support. Set `INSIGHTS_WITH_CODEX=1` only when canonical #2035 is present. All execution must use disposable databases; no paid provider is required. Runtime histories/results are recorded outside the repository and in the PR description.
