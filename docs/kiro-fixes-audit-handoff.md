<!-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Kiro session delivery review guide

Use this guide when validating Kiro engine routing, capture, reconciliation and retry
behavior. Environment setup is documented in `SETUP.md`; harness integration contracts
are documented in `docs/adding-a-harness.md`.

## Completion workflow

V3/IDE hooks capture records and persist explicit Stop authority. Finalization is
**not automatic**: run reconciliation from an independent terminal after Kiro exits.
V2 retains background finalization, with reconciliation available for recovery.

```bash
observal reconcile --harness kiro --since 24 --output json

# Use the same relocated runtime root that the Kiro chat used, if applicable:
KIRO_HOME=/absolute/path/used/by/kiro observal reconcile --harness kiro --since 24 --output json
```

Use the **same Observal authentication home** as the Kiro chat. An independent terminal
does not inherit that chat's environment. Quiet or permission-paused Kiro sessions
without matching Stop intent remain non-final.

See `observal_cli/skills/observal-advanced/references/recovery-workflows.md` for the
recovery workflow. A capture ACK, scheduled worker or process exit is not completion.
Verify the finalized cursor, cleared Stop intent, and server byte/line checkpoint.

## Regression suite

After installing development dependencies, run the project suite with `make test`.
For focused review, run the following from the repository root using the development
Python environment:

```bash
PYTHONPATH="$PWD:$PWD/packages/observal-shared:$PWD/observal-server" \
  python -m pytest tests/test_kiro*.py tests/test_launcher*.py tests/test_session*.py \
  tests/test_hooks_session_push.py tests/test_cmd_doctor*.py tests/test_cmd_pull_coverage.py -q
```

Process tests must pass explicit source paths to their child interpreters rather than
relying on the caller's working directory or an inherited `PYTHONPATH`.

Transport holds a scoped session/group claim through send and ACK, not the outbox-wide
lock. Competing drains retry busy groups without resending their rows. Kiro lifecycle
claims preserve Stop intent; they are not skipped merely because unrelated transport
is busy. Candidates are refreshed after claiming so accepted rows cannot revoke a
completed cursor. Refresh the authority source too, and reacquire the parent claim if
the refreshed envelope changes group identity. Capture-only credit updates must retain
a queued final payload and its Stop-generation envelope until authority is validated. Verify these properties with the concurrency regressions in
`tests/test_kiro_coderabbit_review.py`.

## Live validation matrix

Rebuild the local stack with `make rebuild` before exercising runtime changes.
Preserve host credentials, unrelated profiles and user hooks. Use isolated homes,
synthetic registry agents and unique session IDs; remove only owned test data afterward.

On macOS, changing native Kiro's `HOME` does not safely isolate Keychain access. Stop
if missing-keychain dialogs appear; never reset the host keychain for a test. Keep the
native OS home intact and separate the hook interpreter's test authentication home in
the validation harness. Document and remove any temporary interpreter bootstrap; it
is test infrastructure, not a supported product authentication override. Do not infer
forensic host-auth-state preservation from an isolated SQLite copy.

Validate native Kiro V2 and V3 in both project and user scopes with unchanged generated
launchers. Keep native engine evidence separate from constructed transcript or fault
controls. In particular, constructed V3 layouts are not native IDE GUI evidence.

- Verify engine selection, standalone per-event coverage and V2 inline compatibility.
- Check custom `KIRO_HOME` consistently in discovery, doctor, patch and cleanup.
  Missing or empty profile directories must not hide incomplete coverage or prevent
  owned standalone-hook cleanup.
- Verify attribution, late records and credits, final cursors, checkpoints and replay.
- Resume while final batches are offline. A newer Stop must not reauthorize old batches.
- Withhold the ACK after actual server acceptance, then reconnect. Retry must preserve
  source evidence without duplicating server source identities.
- Kill or resume a waiting V2 worker. Capture receipts must not suppress retry, and an
  old worker must not adopt a newer Stop generation.
- Exercise partial parent/child failures, quiet sessions and source-before-hook resume.
- Verify dry-run leaves target files and delivery state unchanged; preserve user-owned
  hooks through repeated patch and cleanup.
- Exercise normal and permanent-gap ACKs, unknown-harness quarantine, legacy SQLite
  migration, UTF-8 prompt boundaries and queued delivery after source deletion.

Transport is at least once and depends on server identity deduplication. A coherent
single ACK authority sample does not provide atomicity against external transcript
writers. Record failures and environment limits separately from passing controls;
do not infer production readiness or independent review from test execution alone.
