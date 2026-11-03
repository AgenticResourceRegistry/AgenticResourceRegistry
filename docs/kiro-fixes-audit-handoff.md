<!-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Kiro fixes: auditor handoff

## Current status

User-directed, Pi-assisted work. **No human-review or production-readiness sign-off.**
Branch `fix/kiro-engine-delivery` remains stacked on #2029 head `1626b67a`.
Nothing pushed, published or merged; the reviewed #2029 branch was not changed.

**Native V3 completion is now proven through independent foreground reconciliation,
not an automatic hook-spawned finalizer.** This deliberately changes the workflow:

```bash
# Run from an independent terminal after the Kiro chat exits.
observal reconcile --harness kiro --since 24 --output json
```

For a relocated runtime root, the independent terminal must use **the same `KIRO_HOME`
value and Observal authentication home** used by Kiro. Another terminal does not inherit
that environment; the bare command may scan the wrong root:

```bash
KIRO_HOME=/absolute/path/used/by/kiro observal reconcile --harness kiro --since 24 --output json
```

The auditor's two resume-safety findings against `56b4c24f` were reproduced and fixed
locally. **Independent re-review/clearance and approval of the manual workflow remain pending.**

V3/IDE hooks capture records and persist explicit Stop authority. They do not spawn
finalizers that native shutdown kills. V2 retains its existing background path.
Quiet/permission-paused Kiro sessions without a matching Stop remain non-final.

## Reviewable local checkpoints

| Commit | Change |
| --- | --- |
| `04356285` | Configured engine detection and V2 compatibility. |
| `080e0556` | Placement tagging, session guards/receipts and serialized outbox delivery. |
| `da5fd74d` | Validate standalone schema, enabled executable actions and current-event coverage. |
| `bbdf4510`, `06ca5a90` | Separate capture ACKs from retryable Stop intent; recover changed pending metadata. |
| `c5b862db` | Reject namespace-only isolated launcher installations. |
| `7858cc00`, `3ea34d80` | Real process concurrency, lost HTTP ACK and killed-finalizer retry regressions. |
| `46b2cc7b`, `e2ac9f94` | SIGTERM-immunity experiment, then removal after native SIGKILL escalation. No immunity remains. |
| `13dd1960` | Independent reconciliation: guarded parent/child groups, late credits/records, prompt-bound Stop authority, resumed-prompt cancellation and cursor reset. Recovery skill instructions updated. |
| `ec9e748b` | Custom `KIRO_HOME` runtime discovery/settings/credits/coverage/scanning; Observal auth/outbox home unchanged. Explicit caller home retains isolation. |
| `c700133c` | Antigravity test fixture inherits production adapter defaults. |
| `119cb3a1` | Generation-bound queued final authority and V2 workers; record-preserving durable downgrade, send/ACK validation, consistent outbox-before-session lock ordering, capture-only metadata kept non-final. |
| `b54ae938` | Worker-routing test verifies the actual scheduled Stop generation. |
| `1b39dc76` | Superseded ACKs cannot create completion receipts or report finalized; fresh Stop remains eligible. |

Inspect `git diff 56b4c24f..1b39dc76` for the resume-safety fixes,
`git diff 715d9a64..ec9e748b` for the redesign, or
`git diff 1626b67a..HEAD` for the complete stacked #2021 change.

## Why the original worker direction was rejected

Private stderr/start-exit diagnostics preserved generated hook commands and worker argv.
The native V3 worker entered, then died before its 2-second settling delay, without Python
traceback. Independent macOS kqueue observation measured SIGTERM/exit **-15** at about
**0.36 seconds**. Ignoring SIGTERM led to SIGKILL/exit **-9** at about one second.
The worker already had its own process group and parent PID 1; removing Kiro environment
variables did not help. Exact signal sender/callsite was not established. Temporary
instrumentation and ineffective signal changes were removed.

## Current validation

- Five reconciliation regressions failed before the redesign; two custom-home regressions
  failed before that correction. Added dry-run, late-credit, source-before-hook resume,
  completed-session resume and failed-child intent-retention controls.
- Resume findings: **three regression assertions failed before fixes**. Current committed
  implementation `1b39dc76`: **845 targeted tests passed** in 154.78s. Ruff/format/whitespace
  checks passed. Offline final → prompt resume → pre-group outbox reconnect now retains
  records/credits without final semantics or a final cursor. Covered source-before-hook and
  source-at-ACK races, partial delegated-child ACKs, newer Stop generations, legacy SQLite
  migration and a real waiting V2 interpreter. The concurrent final-worker control now
  establishes explicit Stop authority, not a prompt-only capture.
- Initial parallel targeted run: 842 passed / 3 failed (two resource/timing failures and an
  outdated exact worker-argv assertion). Routing assertion corrected; the later complete
  sequential 845-test run passed, without timing-test changes or exclusions.
- Historical full implementation-tree run at `ec9e748b`: **11,594 passed, 71 skipped, 6 failed** in
  32m32s. Four failures were previously recorded/base-reproduced: leaderboard route lookup,
  two package defaults/credential tests and delegation dead-worker cleanup. An unrelated
  parser timing check passed in isolation. Antigravity's minimal fake lacked the new adapter
  contract; corrected in test-only `c700133c`, then its tests and the timing check passed
  (**4 passed**). The subsequent 835-test run passed. **No new full-suite run or all-green
  full-suite claim for the resume-safety commits.**
- `make rebuild` rerun completed using external source-overlay Dockerfiles/Compose configuration;
  API source rebuilt, cached web base reused. Initial startup exceeded the health window
  under test load; an intermediate retry lacked the root `.env` file. Correct root symlink
  restored for the successful run, then removed. ClickHouse container limit remained 2 GiB.
- Actual Kiro CLI **2.35.0**, both **V2 and V3**, created/pulled registry agents, native V3
  upgrade and unchanged generated launchers passed again against the rebuilt API, including
  a final run at `1b39dc76`. This is native happy-path evidence, not native resume/offline proof. For V3,
  `KIRO_HOME` was relocated. Before reconcile, Stop was pending and cursor non-final;
  no cleanup-owned finalizer was spawned. Independent public CLI dry-run/reconcile then
  produced the finalized cursor and cleared intent. API attribution, positive credits and
  contiguous byte/line checkpoints matched. A second reconcile was a no-op.
- Network delivery remains at least once; server identity deduplication is required.
  A capture ACK, scheduled child or process exit alone is not completion evidence.

## Environment and remaining gates

Owned synthetic agents/exact session rows and copied auth/config files were cleaned up.
Temporary draft-install settings were restored; API HTTP 200 and original ClickHouse 2 GiB
retained. Kiro authentication used private read-only SQLite backups in throwaway homes;
original host settings/database were not deliberately changed. No forensic attestation.
The dedicated venv uses a normal shared-package install because hidden editable .pth loading
was broken. Private scripts/logs are outside Git and require privacy review before sharing.

Human review and independent re-review must approve the **explicit V3/IDE reconciliation workflow**, adapter/group
coordination, source prompt authority and custom-runtime-home scope. This is not an automatic
background-runner solution. Global installer relocation is not newly attested. A clean
full-suite rerun may still be requested before publication. After #2029 merges, rebase onto
updated `main` and verify the #2021-only diff. No PR publication or merge without approval.
The pre-existing project-only doctor cleanup gap remains separate.

Prior published fixes, status not rechecked in this step: #2029 (#2015/#2016/#2024),
#2030 (#2020), #2031 (#2023), #2032 (#2022). Detailed chronology: `../KIRO-FIXES.md`.

New evidence: `/tmp/kiro-2021-resume-{before,focused,controls,completion-receipts,targeted,targeted-final}.log`,
`/tmp/kiro-2021-resume-native-final.log`, `/tmp/kiro-2021-resume-rebuild-final.log`.
Final native run: `kiro-2021-native-live-yfh8752j`; cleanup errors empty. Local authority
lives in a SQLite `delivery_context` column, not API payload fields. Captured records,
credits and source ranges survive cancellation; only superseded final metadata is removed.

Prior evidence: `/tmp/kiro-2021-reconcile-{before,focused,full,isolated,targeted-final}.log`,
`/tmp/kiro-2021-home-{before,after}.log`, `/tmp/kiro-2021-native-reconciliation.log`,
`/tmp/kiro-2021-reconcile-rebuild-retry.log`. Native private run:
`kiro-2021-native-live-mf_eexi4`. Earlier signal evidence:
`/tmp/kiro-2021-native-{worker-stderr,kqueue,kqueue-fixed,groups,env-experiment}.log`.
