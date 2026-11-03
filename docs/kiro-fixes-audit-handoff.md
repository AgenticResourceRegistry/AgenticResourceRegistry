<!-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Kiro fixes: auditor handoff

## Status and scope

This is a technical checkpoint, **not a production-readiness or human-review sign-off**.
Work was directed by the user and assisted by Pi 1.4.0. Human review remains pending.
No PR was merged. All #2021 commits remain local, not pushed.
The original checkpoints below are superseded by the auditor corrections recorded next.

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

## Auditor corrections and current evidence

Both reported correctness findings reproduced: **9 new regressions failed before fixes**.
The namespace-only launcher probe also failed its new regression before correction.

| Local commit | Correction |
| --- | --- |
| `da5fd74d` | Require v1 schema, enabled Python session-push actions and coverage of the current event before suppressing inline delivery. Doctor requires both Prompt and Stop for installed standalone health. 177 focused tests passed. |
| `bbdf4510` | Capture receipts no longer suppress final-worker retries. Persist explicit Stop intent separately; aged recovery may finalize that intent, but not merely quiet Kiro sessions. Clear intent only after in-scope final cursors complete. 172 delivery/cross-harness tests passed. |
| `c5b862db` | Probe hook/worker module resolution, not namespace-root importability. All 35 launcher tests passed, including the two auditor failures. |
| `7858cc00` | Real concurrent capture/final interpreters and an HTTP peer accepting a record before losing its ACK. Both regressions passed. This is at-least-once network delivery requiring server deduplication. |
| `06ca5a90` | A previously final cursor cannot hide a new pending Stop carrying changed companion metadata. Six finalization regressions passed. |

Current-tree targeted validation: **823 passed**, including Kiro, doctor/pull, launchers,
shared sessions/outbox/reconciliation and cross-harness delivery. A real killed interpreter
releases its lock without committing a success receipt. Ruff/format/whitespace checks passed.
The leaderboard route-lookup failure reproduced on unchanged #2029 base `1626b67a`
using the same environment; it is not attributable to these changes.

`make rebuild` completed with external source-overlay Dockerfiles/Compose configuration.
The API source was rebuilt from this branch; the cached web base was reused. Initial startup
failed because this worktree lacked `.env`; a temporary symlink to the original environment
was used and removed. ClickHouse remained at 2 GiB. The dedicated test venv required a normal
shared-package install: its editable hidden .pth was also skipped, so source fallback alone
could not import `observal_shared`. This environment correction did not modify host Kiro.

Unmodified generated hook commands and detached worker launchers passed rebuilt-API controls
for V2 and V3 transcript layouts: final receipts, finalized cursors, delayed source records
and credits, contiguous server checkpoints, source deduplication and completed Stop replay.
These were constructed transcript controls, not native engine-execution proof.

Native CLI 2.35.0 **V2 passed** with a real created/pulled agent, unchanged generated launchers,
detached final cursor, credits and agent attribution. Native **V3 still failed completion**:
its cursor stayed at 10 source lines without `finalized`; durable Stop intent remained.
A foreground invocation of the real worker subsequently finalized that saved V3 source at
16 lines. The detached cause is not established. Another V3 control reproduced the failure;
`whoami` intermittently timed out, and the last fresh attempt failed the login precondition.
PID monitoring was inconclusive. No native V3 completion or approval claim is made.

A broad full-suite run started at `7858cc00` is being recorded separately; the final metadata
edge correction landed during that run. It cannot be advertised as a final committed-tree
full-suite pass. Native V3 completion and a clean final full-suite run remain approval gates.

Evidence: `/tmp/kiro-2021-auditor-{before,final-targeted,full}.log`,
`/tmp/kiro-2021-launcher-probe-{before,after}.log`,
`/tmp/kiro-2021-generated-worker-live.log`, `/tmp/kiro-2021-native-unmodified.log`,
`/tmp/kiro-2021-existing-source-diagnostic.log`, `/tmp/kiro-2021-native-pid-monitor.log`.
The temporary scripts/private homes are not durable CI artifacts or share-ready logs.

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

1. Diagnose native V3 detached finalization and verify actual completion. Constructed delayed-record/credit controls passed, not native V3 completion.
2. Review new concurrent process, hard-kill and lost-ACK tests and standalone validation.
   Examine explicit-intent cancellation across resumed turns and `KIRO_HOME` handling.
3. Launcher failures are fixed; leaderboard failed on unchanged base. Record remaining
   full-suite failures and rerun the final committed tree.
4. Rebuilt API and native V2 controls passed. Complete native V3 placement/finalization
   validation while preserving credentials and host configuration.
5. Obtain human review. Rebase #2021 onto `main` after #2029 merges before publishing
   a clean, separate PR. The project-only doctor cleanup gap remains separately tracked.

Detailed local chronology: `../KIRO-FIXES.md`. Evidence logs:
`/tmp/kiro-2021-atomic-{engine,delivery,launcher}.log`,
`/tmp/kiro-2021-{full,final-targeted,isolated,native-gated}.log`.
Native capture evidence: temporary run `kiro-2021-native-live-foc2n_xw`.
