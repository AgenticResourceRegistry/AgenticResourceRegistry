<!-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Insights session fidelity and disposable cache identity

## Problem (OBS-1544)

Insights formatted raw records separately from the harness adapters. Kiro CLI/IDE and Codex assistant replies, tool arguments and failures could disappear from model input despite being stored in ClickHouse. The model could then claim that a delivered answer was missing. Pi/Codex delegation puts the actual task after a long agent-instruction preamble, beyond the old 500-character excerpt. Assistant answers were reduced to their first 300 characters, hiding commands and verification boundaries. Updating extraction alone also left stale cached facets reusable; the cache's unique scoped key excludes `facet_version`, so inserting another version could conflict with an existing row.

## Fix

- Kiro and Codex adapters provide Insights normalization beside their trace parsing. Kiro CLI and IDE envelopes measure native messages, tools, errors and edits; standalone IDE tool calls do not manufacture assistant replies. Codex normalization preserves replies and tool outputs and correlates nearby event echoes without discarding repeated prompts across explicit turns.
- Transcript building dispatches through the registry's parser ID, not a harness condition chain. Pi/Claude native nested error records are retained. Existing generic formatting is preserved for other/historical harness names; this is not a ten-harness fidelity certification.
- Per-event budgets: user/assistant text 4,000 characters; tool arguments/errors 1,000. Over-budget text preserves both ends and includes an explicit middle-omission marker. The CLI's exact delegation envelope is unwrapped to task-first text; arbitrary user XML is not treated as delegation. These are bounded excerpts, **not** proof that every command in the omitted middle reaches the model.
- The whole-transcript threshold remains 30,000 characters, with the existing bounded chunk summarizer. This change does not eliminate model context/cost limits or add a secret-redaction guarantee.
- Facet version 3 invalidates old excerpt/normalization results across harnesses. Agent report cache lookup additionally compares a digest of the currently read raw source lines; a growing session misses the old cache. Source identity is digest-only, not another raw trace upload.
- Cache writes upsert the existing scoped row on PostgreSQL/SQLite rather than racing a select/insert. Version and source digest are stored with the disposable derived result; historical session events and old report snapshots are not rewritten.
- Report `facets_summary.processing_coverage` identifies processed sessions, transcript-unavailable sessions, extraction failure/insufficient-input sessions and sessions skipped by the facet budget. Cached versus new successful facets both count as processed. Failed/insufficient is deliberately not asserted as an agent failure.

## Validation

Targeted tests cover CLI/IDE shapes, Codex echo/turn boundaries, safe malformed-record parsing, scoped identity, source cache invalidation, long delegation preambles, bounded final-answer tails and nested Pi/Claude tool errors. `tests/runtime/verify_insights_fidelity.py` runs the actual ingest -> ClickHouse -> transcript path and PostgreSQL cache path in a **disposable** API container. It creates unique synthetic records, makes no model call, performs no historical repair and requires `INSIGHTS_DISPOSABLE_VERIFY=1`.

Example (only after confirming the Compose project is disposable):

```bash
docker exec -i -e INSIGHTS_DISPOSABLE_VERIFY=1 insights-fidelity-observal-api-1 \
  /app/.venv/bin/python < tests/runtime/verify_insights_fidelity.py
```

This verifies source/format/cache behavior independently of model randomness. It does not certify an external provider's narrative quality, unavailable harness formats, parent-child cohort semantics or actual loaded agent attribution; those are separate changes. See the private suite ledger for the executed build/test results.

AI assistance: implemented under human direction using Pi; human review of the complete diff is required before PR publication.

## Executed verification (2026-11-06)

- Isolated OrbStack Compose project `insights-fidelity`: API and web built via `make rebuild`; API `/health` reports PostgreSQL, ClickHouse and Redis healthy; LB/web returns HTTP 200. An initial port-binding attempt failed; explicit per-service port overrides resolved it without stopping existing containers.
- Live runtime script passed for Kiro CLI, Kiro IDE and Codex: four source rows each ingested, actual ClickHouse-backed transcript preserved the task/reply/error, deterministic metadata counted the error, and PostgreSQL cache reuse/miss/upsert assertions passed.
- Container `facets.py` and `transcript.py` SHA-256 values matched the worktree files being tested.
- Broad Insights/metadata regression selection: **381 passed**.
- Full Python suite: **11,518 passed, 71 skipped, 4 failed**. Three failures reproduced unchanged on `main` (dead-worker cleanup and two macOS Bash setup tests). The fourth was a Hypothesis timing/deadline flaky failure during the concurrent build; it passed on both isolated baseline and targeted candidate reruns. These unrelated failures were not hidden or fixed in this PR.
- No hosted provider was called; live verification proves extraction/cache behavior, not narrative quality. Historical data and hosted reports remain unchanged.
