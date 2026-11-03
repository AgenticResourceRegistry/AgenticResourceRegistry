<!-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Kiro fixes: auditor handoff

## Status and scope

This is a technical checkpoint, **not a production-readiness or human-review sign-off**.
Work was directed by the user and assisted by Pi 1.4.0. Human review remains pending.
No PR was merged. The two new #2021 implementation commits are local, not pushed.

Branch: `fix/kiro-engine-delivery`. Dependency/base: #2029 head `1626b67a`.
The reviewed #2029 branch was not modified by this work.

| Checkpoint | Purpose |
| --- | --- |
| `04356285` | Engine detection and doctor health: workspace/global settings, explicit `OBSERVAL_KIRO_ENGINE` override, native CLI settings probe, version fallback, no IDE-launcher fallback. Preserve inline compatibility for V2 launches despite a saved V3 default. Includes four new engine/doctor regressions. |
| `080e0556` | Delivery coordination: placement-tagged hook commands, runtime selection from observed V3/IDE transcript layout, per-session capture/recovery/finalization locks and success receipts, cross-process serialization of the SQLite outbox send/acknowledge loop. Includes retry, interruption, related-update, scope and concurrency regressions. Finalization remains unresolved. |

Review the changes with:

```bash
git show 04356285
git show 080e0556
git diff 1626b67a..080e0556
```

Existing fix-series context, as previously published; PR status was not rechecked for this handoff:

| Issues | PR | Latest recorded commit | Change |
| --- | --- | --- | --- |
| #2015, #2016, #2024 | [#2029](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/pull/2029) | `1626b67a` | Universal/legacy hook readers, local-profile preservation on re-pull, partial-install recovery and doctor audit/scope corrections. Supersedes #2016. |
| #2020 | [#2030](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/pull/2030) | `3ae29a88` | Registry hook array merging, owned-entry refresh/deduplication, malformed-config refusal before writes. |
| #2023 | [#2031](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/pull/2031) | `c5381f03` | V3 model evidence transport/precedence and exclusion of synthetic model/credit offsets from cursor progress. |
| #2022 | [#2032](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/pull/2032) | `dfc8167b` | Kiro metrics/transcript normalization, scoped facet-cache upgrade, bounded database lookups and injected-prompt ordering. |

## #2021 validation evidence

- The isolated engine/doctor/pull tree committed as `04356285`: **460 tests passed**.
- Earlier full working-tree targeted run: **650 passed, 4 skipped**.
- Latest broad run after splitting tests for the commits: **648 passed, 4 skipped,
  2 failed**. Both failures are generated-launcher tests reporting
  `No module named observal_cli.hook_gate`; an isolated launcher rerun reproduced
  them (**31 passed, 2 failed**). Cause and baseline comparison remain outstanding.
- Earlier full suite, before final placement changes: **11,562 passed, 120 skipped,
  6 failed**. Two new fixture failures were fixed and passed isolated reruns.
  Three previously known delegation/package-setup failures remained; the fourth
  remaining failure, leaderboard route lookup, also reproduced in isolation.
  This is not a passing final full-suite result.
- Ruff, formatting and whitespace checks passed for the implementation checkpoint.
- Native Kiro CLI **2.35.0** confirmed two inline invocations on V2 versus four
  inline/standalone invocations on V3 for two lifecycle events. Native agent
  upgrade was used, not a hand-written approximation of the universal format.
- Actual synthetic registry agents were created and pulled into temporary projects.
  With runtime placement selection, native V3 reached the OrbStack API with
  **four raw invocations, two capture HTTP requests, one recovery worker and one
  scheduled final worker**. This proves capture placement, not final completion.
- **Detached finalization did not produce its completion marker or a finalized
  cursor.** The cause could be implementation, worker lifecycle or instrumentation;
  it has not been established. Later isolated native `whoami` probes intermittently
  timed out. No exactly-once delivery claim is made, including ambiguous HTTP ACKs.
- No new `make rebuild` was completed for #2021. Tests used the existing OrbStack
  services with the changed local CLI; rebuilt-stack verification remains required.

## Environment changes and cleanup

Tests used throwaway homes/projects and private read-only SQLite backups of Kiro's
local database for authentication. Credential values were not printed. The original
Kiro database/settings were not deliberately edited; this is not a forensic integrity
attestation. Temporary CLI configs and copied authentication databases were removed.
Owned synthetic agents and exact session IDs were cleaned up.

`security.allow_draft_install` was temporarily enabled and restored. API restarts
were used because updating the setting through one worker did not reliably refresh
another worker's process-local cache. That observation is not part of this fix.
The API was HTTP 200 at handoff; ClickHouse retained its original **2 GiB** limit.
Temporary runtime scripts and logs remain outside Git and require privacy review
before sharing. Local evidence paths are not durable CI artifacts.

## Outstanding review gates

1. Diagnose detached finalization and verify actual completion, delayed records and credits.
2. Add stronger concurrent capture/final-worker, hard-crash and ambiguous-ACK controls;
   examine standalone-hook validity and `KIRO_HOME` handling.
3. Resolve or establish baselines for launcher and leaderboard failures; rerun the final full suite.
4. Rebuild OrbStack and repeat V2/V3 placement, retry and attribution controls.
5. Obtain human review. Rebase #2021 onto `main` after #2029 merges before publishing
   a clean, separate PR. The project-only doctor cleanup gap remains separately tracked.

Detailed local chronology: `../KIRO-FIXES.md`. Evidence logs:
`/tmp/kiro-2021-atomic-{engine,delivery,launcher}.log`,
`/tmp/kiro-2021-{full,final-targeted,isolated,native-gated}.log`.
Native capture evidence: temporary run `kiro-2021-native-live-foc2n_xw`.
