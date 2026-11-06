<!-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Explicit Insights section failures (OBS-1543)

## Problem

The model adapter returns an empty dictionary on an empty response, failed call or malformed JSON. Section generation previously silently substituted an empty section, accepted unrelated first dictionary values and marked the report completed. Readers could not distinguish a valid empty suggestions set from a generation failure; Apply had no explicit failed-section gate.

## Behavior

Each section and the at-a-glance synthesis get at most two section-level attempts. The second prompt restates the exact requested JSON envelope and required fields; it does not echo a rejected response or exception that might contain private data. The provider's existing transport retry policy remains separate.

`section_validation.py` checks the named envelope, required top-level field types, object-list elements and the essential suggestion content fields. Additional fields are allowed. This is structural validation, not factual verification of model advice or certification of executable hooks. A suggestions object with three valid empty arrays is a successful no-findings result. `{}`, a missing envelope, malformed fields and failed calls are not.

A report's existing JSON narrative now carries:

```json
{
  "generation_state": "partial",
  "section_errors": [
    {"section": "suggestions", "code": "invalid_or_empty_response", "attempts": 2}
  ]
}
```

Available sections remain readable. Failed sections remain empty but are explicitly accounted for. The report lifecycle remains `completed` (generation finished), while the progress message and web status show partial availability. No database status-enum migration is required. The API exposes the persisted narrative metadata; the CLI displays a partial-report warning, and a failed section-specific JSON response includes `section_errors`. The web page identifies failed sections and substitutes an unavailable state for failed suggestions instead of showing Apply controls.

Apply checks the persisted failure record before materializing suggestions and returns an actionable error. It does not automatically regenerate a report, modify old snapshots or approve artifacts. Missing new fields on legacy reports do not prove those sections succeeded; historical repair is deferred.

## Tests and live checks

- Model adapter fixture with malformed provider JSON reaches the bounded retry and explicit failure path.
- Missing/wrong envelopes and invalid field types fail; a valid empty suggestions result succeeds; a second valid response recovers without a failure record.
- An exception containing a synthetic secret never appears in stored diagnostics.
- Available sections survive another section's failure; Apply rejects failed suggestions before writes.
- `tests/runtime/verify_insights_sections.py` injects deterministic model failures only in its own process, persists the actual generated narrative in a disposable PostgreSQL database, reads it through the running authenticated API and checks HTTP Apply rejection. It does not change the running worker's model configuration or call a paid provider.
- Opt-in Playwright spec `insights-section-failures.spec.ts` checks the real built page at desktop/mobile sizes, unavailable suggestions, retained successful content, absent Apply button and screenshots. The spec requires a synthetic localhost report and explicit test credentials; it does not mutate hosted reports.

AI assistance: Pi under human direction. Human review of the complete diff and screenshot evidence is required before PR publication.

## Executed verification (2026-11-06)

- Isolated OrbStack project `insights-sections`, own images/volumes/network and nonconflicting ports: API/web rebuilt via `make rebuild`. API health checks passed. The verification API was subsequently set to one worker with additional memory headroom in the **local override only**, not the product Compose file.
- Real PostgreSQL persistence and authenticated HTTP reads preserved partial generation metadata; direct and HTTP Apply attempts rejected the failed suggestions without an applied timestamp. Model failures were injected only in the verification process; no running worker/provider settings changed.
- Container checksums for `sections.py`, `section_validation.py` and `self_learn.py` matched tested source files.
- Broad Insights/CLI/self-learn selection: **355 passed** before the final malformed-provider fixture; the subsequent section/CLI selection including that fixture: **60 passed**. Frontend typecheck passed; lint reported **0 errors, 94 existing warnings**; scoped design detector returned no findings.
- Final opt-in Playwright run with retries disabled: **1 passed**, checking desktop/mobile, retained available content, failure states and absent Apply controls. Earlier verification runs exposed an incomplete synthetic strength fixture and a redundant-navigation timing failure; the fixture was corrected and viewport changes now reuse the loaded report. Screenshots were captured for both the partial-report banner and unavailable suggestions at both sizes, and retained in the private suite evidence directory.
- No paid/hosted provider was called. This verifies failure handling and API/UI behavior with deterministic fault injection, not a hosted model's retry success rate or worker-queue scheduling under load.
