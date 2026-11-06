<!-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Verified instruction cohorts and session accounting

## Problem

Insights selected sessions by agent/version binding, even when a persistent default had never loaded the profile. Children counted as independent sessions, message totals could become prompt counts, and cached facets or layer-group correlations could reintroduce evidence outside the claimed cohort. An installed profile or layer pin is not proof that its instructions reached the model.

## Read-time policy

`metrics.cohort` separates bound records, recorded-instruction-matched top-level sessions, mismatches/unknowns, failed runs and child records. No historical session binding, report snapshot or personal default setting is rewritten. The selected report's approved version supplies a private full-prompt verification contract; version comparisons load their own contracts rather than using today's latest prompt.

Pi verifies recorded system sections (exact preamble or full, line-bounded unquoted prompt within the active default Pi AGENTS instruction-file block) and the CLI's exact named delegation envelope. It checks every response on its own tree ancestry, including abandoned branches, patches and compaction checkpoints. A missing legacy checkpoint, malformed/incomplete/cyclic tree or edited/dropped delegation envelope is unknown. User mentions, tools, summaries and skill descriptions are not profile evidence. Codex supports explicit delegation envelopes through the existing converter; this is not a duplicate of pending Kiro/Codex fidelity work. Custom instruction directories without this recorded contract do not qualify; contributor docs and inactive profiles are never accepted just because they quote the prompt. Other formats without a sufficient recorded instruction contract remain explicitly unknown, not assumed unused.

A recorded match proves prompt presence only under the same local-transcript trust boundary as existing telemetry. It does **not** cryptographically attest a loader, uniquely distinguish versions with identical prompts, prove compliance/isolation, or classify task relevance. Appended or conflicting instructions can still affect behavior. Recommendations need the separate scope/evidence change.

Only matched, substantive top-level sessions qualify for agent-effect metrics/facets. Bound operational counts and reported top-level costs remain available in coverage. Children are counted separately and matched to parents only with the same project/user/harness identity; unresolved children never become independent tasks. Child costs are reported separately, not summed into parent spend (a parent provider may already include them). Failed runs are retained as coverage rather than silently erased. Missing parent metadata is unknown and excluded.

`metrics.rich.user_turns` and `assistant_messages` are distinct from all `total_messages` records. Current qualified facets carry an instruction/source/version policy marker; old or stale facets cannot qualify simply because the agent/session ID matches. The pending shared cache/fidelity PR remains separate. Existing binding-scoped layer-impact correlations and legacy regression baselines are withheld until they can honor verified session scope, rather than mixed into this cohort.

## Presentation and privacy

The web/CLI display included/excluded counts and a legacy-report warning. An empty verified cohort returns a deterministic insufficient-evidence report without model calls, not an assertion that the agent was unused. Full private prompt contracts are never serialized into the section-model data block; only the existing redacted role excerpt is available there. Evidence responses contain decisions/digests, never copied instruction bodies or raw traces.

## Verification

Fixtures cover prompt-only mentions, exact system/role envelopes, patches, missing/changed checkpoints, branches, malformed trees, version/source cache markers, unsupported formats, cross-user parent collisions and child cost accounting. Disposable runtime checks ingest synthetic sessions through the actual API, read actual ClickHouse records, run the real pipeline with process-local model stubs, persist/read the coverage report through PostgreSQL/authenticated APIs and inspect desktop/mobile UI. No hosted data or paid provider is used. Unknown-format coverage is an explicit limit, not ten-harness certification.

AI assistance: Pi under human direction; full human diff/screenshot review is required before PR publication.

## Executed verification (2026-11-06)

- Current broad targeted suite: **357 passed**; instruction matcher selection: **27 passed**. No full-suite green claim: unrelated baseline failures from the earlier suite are still outside this change.
- Final production-source API replay passed: four bound records, one qualified top-level, one mismatch, one unknown, one child; one user turn in the qualified metric; only qualified facet extraction; source/contract cache reuse; PostgreSQL persistence and authenticated API reads.
- Production API/web Dockerfiles built, frontend typecheck passed, lint had 0 errors / 94 existing warnings, scoped design detector found no issues. Final desktop/mobile Playwright run with retries disabled: **1 passed**; screenshots retained privately.
- The first final verification attempt was blocked by local Redis/ClickHouse timeouts and a zombie API process. The user explicitly authorized an OrbStack restart; the existing stack recovered healthy without volume resets. Test ClickHouse at 512 MB could not execute health queries; the local test override was raised to 1 GB. This is not a production setting change.
- `make rebuild` passed using an uncommitted cached-image verification override after both production images had been built: API source was recopied and precompiled; the already source-built/tested web bundle was retained. The override is not shipped. Container matcher checksum matched current source.
- No paid provider was called; model stubs were local to the verification process. No historical telemetry/report mutation, personal profile/default change or unrelated workload shutdown was performed. Human diff/screenshot review remains required before PR publication. AI assistance: Pi 1.5.0.
