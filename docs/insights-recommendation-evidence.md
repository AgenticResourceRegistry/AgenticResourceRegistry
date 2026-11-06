<!-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Auditable recommendation evidence

## Problem

Section models received unidentified brief summaries and could attribute generic MCP calls to whichever MCP was configured. Textual mentions/configuration could be mislabeled as successful skill/sandbox use. Recommendations could redefine a narrowly scoped agent based on unrelated sessions, with no per-claim source join or explicit uncertainty.

## Dependency and scope

This branch is independently based on main but **requires the verified-cohort contract for substantive recommendations**. It consumes that contract, never copies its instruction verification implementation. On old main, absent verification/parent evidence fails closed with an explicit blocked result. Runtime integration must combine the cohort change first; this is not a second copy of Kiro/Codex fidelity work or a historical repair.

## Behavior

- Each qualifying session gets a deterministic reference from its full project/user/harness/session identity. Section inputs label briefs, friction and instructions with these references; `narrative.source_index` supplies the authorized session join. References are not reconstructed from a bare session ID.
- Facets assess `agent_task_relevance` against the existing redacted role excerpt. The assessment is **model interpretation**, not fact. Missing, malformed or truncated role context stays unknown. Relevance/schema/scope cache markers invalidate incompatible cached assessments; combined cohort markers additionally bind instruction/version/source identity.
- Suggestions and future workflows must cite existing references that are eligible for changes (recorded instruction match, top-level, complete role and model-assessed in-scope task). Missing/invented/off-task references cause withholding, with per-record reasons/counts. Valid off-task sources can remain diagnostic strengths/friction, not a justification to redefine the agent.
- Unsupported actionable records are removed **before synthesis**. Unreferenced or off-task quick wins cannot reintroduce discarded changes afterward. Source validation and incomplete coverage are visible, not silently treated as no findings. Source refs support auditability only: they do not prove every generated sentence true or establish causality.
- MCP target counters read explicit gateway target/tool selectors or namespaced calls. Discovery/unknown gateways stay unidentified; configured MCP names are never substituted for observed targets. These are **attempts**, not success or registry component identity. Valid Registry reuse still requires the existing `component_ref` authorization/version validation.
- Configured/mentioned skills and sandboxes are unassessed unless separately evidenced. The former mention-based used/unused heuristic is gone. This change does not pretend to implement every harness' sandbox/load receipts.
- Web shows evidence eligibility/withholding, model-assessed relevance and source details; actionable records display refs. Section-specific CLI JSON includes source/validation context. Legacy reports are labeled unverified and remain unchanged.

## Verification boundary

Unit tests cover scoped refs, missing/invented/off-task/unknown rejection, source-backed diagnostic versus change eligibility, synthesis gating, role truncation, MCP gateway/proxy attempts and removal of false capability-use claims. Disposable integration replay runs actual ingest/ClickHouse/pipeline/cache/PostgreSQL/authenticated reads with deterministic process-local model responses. No paid provider or deployed model setting is changed. Browser checks use synthetic data, no network traces or public credentials. Actual model truthfulness and task-relevance accuracy remain outside deterministic proof.

The cohort and recommendation branches touch the same cache call site: integration must preserve **both** qualification-marker and relevance-marker checks. Do not drop one while resolving a rebase. The detached integration worktree is testing scaffolding only and is not pushed.

AI assistance: Pi under human direction; human diff/screenshot review is required before PR publication.

## Executed verification (2026-11-06)

- Standalone main-based targeted suite: **344 passed**. Combined cohort + recommendation targeted suite in a detached worktree: **371 passed**. No full-suite green claim; known unrelated baseline failures were not fixed here.
- Real disposable integration API replay passed: four source records/sessions yielded two instruction-matched top-level sources, exactly one model-assessed change-eligible source, and withheld off-task/invented/unreferenced changes. Invalid advice was absent from synthesis input, and off-task quick wins were blocked after synthesis. Explicit Linear MCP attempt + one unidentified attempt were reported despite GitHub being configured. PostgreSQL persistence and authenticated source-map reads passed.
- Integration API was source-recopied/precompiled on the already production-built cohort dependency image using an uncommitted cached-image Dockerfile. No dependencies changed. The integration web production Dockerfile/typecheck/bundle built; `make rebuild` passed with cached-image overrides after those builds. Evidence/facet/section module checksums matched source.
- Final desktop/mobile Playwright run with retries disabled: **1 passed** (eligibility/withholding, actual MCP target, grounded change/ref labels, source details, no unsupported advice and no overflow). Typecheck passed; lint 0 errors / 94 existing warnings; design detector empty. Screenshots retained privately; credentials removed; tracing disabled.
- Model responses were deterministic and process-local, not deployed model configuration. No paid provider, historical repair, profile/default setting, or autonomous PR publication.
- The integration combines both cache gates; two import/cache lines and test/doc append conflicts were resolved in the detached test tree. This branch still needs rebasing on the cohort merge before publication; do not blindly choose one cache gate while resolving it. The source branch contains no instruction-verification implementation.
- AI assistance: Pi 1.5.0, under human direction. Human review of full diffs and screenshots is required before PR publication.
