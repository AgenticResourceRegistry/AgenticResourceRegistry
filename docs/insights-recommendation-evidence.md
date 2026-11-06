<!-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Auditable recommendation evidence

## Problem

Section models received unidentified brief summaries and could attribute generic MCP calls to whichever MCP was configured. Textual mentions/configuration could be mislabeled as successful skill/sandbox use. Recommendations could redefine a narrowly scoped agent based on unrelated sessions, with no per-claim source join or explicit uncertainty.

## Required evidence contract

Recommendations require the verified instruction/parent cohort contract. Without it, the pipeline returns an explicit blocked result rather than inferring verification from bindings. This is a consumer of recorded evidence, not a replacement for instruction loading, harness fidelity or historical repair.

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

Instruction/source qualification and task-relevance cache markers are independent checks; neither can substitute for the other. Scope hashes use the original role excerpt consistently, and any truncation before/after redaction makes relevance incomplete.

AI assistance: Pi under human direction; human diff/screenshot review is required before PR publication.


The runtime verifier receives its ephemeral `INSIGHTS_RUNTIME_PASSWORD` from the caller environment and reports only non-secret fixture metadata. It never writes that password to a temporary file. Wrong-shaped actionable sections and legacy fallback items are normalized away, not rendered as ungrounded advice.

The model receives a bounded index of opaque source references and relevance flags, not the stored project/user/session identities. The full audit map remains in authorized API/report data. This separation also applies to synthesis inputs.
