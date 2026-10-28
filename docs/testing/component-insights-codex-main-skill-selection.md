<!-- SPDX-FileCopyrightText: 2026 Observal Contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Codex project-skill selection: main-based acceptance ledger

**Draft PR #1972 evidence, not release approval.** This is a separate cell from the MCP controls in [the main-port ledger](component-insights-codex-main-port-acceptance.md). A later, separate [user-skill selection cell](component-insights-codex-user-skill-selection.md) has its own gates and does not retroactively change this project-scope result. Tests used a fresh owner-only Registry draft, a single registry-direct 0.1.0 project-skill install, a disposable Codex home/workspace, an inert loopback Responses provider and the isolated main-based API/worker. Neither ordinary harness profiles nor the original checkout were changed. Tokens, prompts, instructions, transcripts, provider output, draft IDs and desktop/mobile screenshots remain in private local controls **outside Git**.

## Metric and gate

Only native **Codex CLI 0.163.0** is opted in. The first-party project skill inventory must identify one unambiguous project root and exact `SKILL.md` path. A native AGENTS.md project may emit a two-kind `['agents_md.instructions', 'environments.environment_context']` context record. The extractor now accepts **only this exact ordered pair**, once per turn, with two nonempty input-text blocks preceding its typed user turn. It is context, not selection. Other multi-kind records, malformed blocks, duplicates and missing or misordered turn links are unsupported, not measured zeros. An attributed selection still requires the separate native `skills.selected_skill_instructions` content kind, a preceding typed user turn with the same turn ID, a rooted exact-path expansion, a complete supported source and a unique contemporaneous **resolved, fingerprint-verified** project install in the same v2 layer. Published facts contain only alias, scope, time, location digest and registry IDs: no path, body, prompt or raw transcript. This is **skill selected**, not a separately confirmed load, proof of model reliance or effectiveness. Availability is not selection. A file read, pasted expansion or name mention is not selection.

Only project-scope verified installs can have a selection/no-selection denominator. A user-scope or ambiguous mixed-scope install remains unknown even when the session-wide projector completes. Missing or changed native metadata, incomplete source, other Codex versions, a shadowed pin and a drifted skill are not measured zeros. A null confirmed-load count means **not recorded**, never zero. The SQL reader, API, CLI, deterministic narrative and UI retain those boundaries. A load-specific attention rule skips cohorts with unmeasured or mixed load coverage, and the model-written interpretation rejects load claims when loads were not measured.

## Fresh native controls and publication

All **seven** Codex 0.163.0 sessions below were automatically delivered with current v2 source records and a skill publication **before reconcile**. A native positive selection can still fail install attribution; the rightmost column is the verified Registry metric, not a guess from the prompt.

| Control | Native selection tag | Published pin | Attributed selection |
| --- | --- | --- | --- |
| Project skill selected | yes | verified | **1** (and one separate offered fact) |
| No selection | no | verified | **0**, measured |
| Name-only reference | no | verified | **0**, measured |
| Pasted imitation | no | verified | **0**, measured |
| Successful manual read | no | verified | **0**, measured; a read does not become selection |
| Skill-file bytes changed after pin | yes | **drifted** | **unknown**, excluded from present/denominator |
| Same-named home skill shadow | yes | **unverified** | **unknown**, excluded from present/denominator |

Source records and provider control markers were checked privately but are not report data. Warehouse mappings confirmed the first five pinned sessions as verified (the selected session published `skill_available` plus `skill_invoked` only). The last two native selected records produced zero *attributed* facts because their mapping was drifted/unverified; that is **not** an observed no-selection result. A bounded forced replay after the matcher-version bump preserved these results. All ten HTTP MCP sessions were also replayed on the new shared publication version: nine stayed complete, the approval/null-result denial stayed unsupported. After the final isolated API/worker rebuild, a second forced replay of all seven skill and ten HTTP sessions retained the same classifications. Fresh warehouse, owner API/CLI/report/HTML export, and main-source desktop/mobile browser checks passed for both slices. New reports were generated after replay; older completed reports were not reused as new-version evidence.

The version-scoped owner API and per-session list, CLI `activity`/`sessions`/`show`, a fresh worker report, HTML export and **main-source desktop/mobile browsers** agreed on **5 verified-present / 5 measured / 5 offered / 1 selected / confirmed loads null (not recorded)**. The browser labels the signal “Codex selections,” displays “Confirmed loads: Not recorded,” explains that selection is not a separately confirmed load and has no horizontal overflow. Prompts, instruction markers and manual-read markers were absent from report/API/export/browser surfaces. Anonymous activity, reports and export returned 401; an unrelated reviewer received 403. Private UI captures were reviewed but not committed.

A first live worker report failed: its older load-specific attention rule tried to convert a null Codex load to zero. The rule and the model's load-claim guard were amended, targeted tests passed, the isolated API/worker were rebuilt, and a **new** report completed and passed every surface check. The failed draft remains failed, not retrospectively counted as a success.

## AGENTS.md combined-context follow-up

A separate read-only audit identified the exact two-kind context record in real 0.163.0 AGENTS.md project sessions. Private native source inspection confirmed two input-text blocks before the typed user turn, including sessions with and without a separate selection tag. Tests inject a sanitized structural context into the existing fixtures: the pair preserves selection when present and does not invent it when absent. Spoofed ordering, extra/mixed kinds, bad block shapes, duplicate contexts, missing typed turns and re-tagged selection records stay unsupported; skill markup pasted into the context alone is not a selection. Raw AGENTS.md instructions and source files are not committed. This is a narrow parser accommodation, **not** proof that all 0.163.0 project layouts or all such private sessions have verified Registry mappings. Other source or inventory gates can still exclude a session.

The extractor change bumped the shared skill matcher revision from 3 to 4 (publication version **13382**). After an isolated API/worker rebuild, forced replay of the **same seven skill and ten HTTP** sessions preserved seven skill completions, nine HTTP completions and the one unsupported HTTP denial; warehouse, fresh owner API/CLI/report/export and desktop/mobile checks passed again. The seven originally accepted project sessions did not contain this AGENTS.md combination. Earlier completed reports are not substitutes for reports generated after replay.

## Fresh AGENTS.md Registry acceptance (separate cohort)

A **new** owner-only Registry 0.1.0 skill was installed once with a versioned, fingerprint-verified **project** pin in a fresh disposable workspace containing an inert `AGENTS.md`. One disposable Codex 0.163.0 home produced **two** new native sessions against that same install. Both persisted source files contained **one** exact `['agents_md.instructions', 'environments.environment_context']` record with two nonempty input-text blocks before a linked typed user turn. Only the positive session had the separate native `skills.selected_skill_instructions` tag and exact rooted expansion. The no-selection session contained no selected-skill tag, tool call or skill-file read. A failed preliminary setup attempt produced no accepted native session and was excluded.

| New native control | Automatic source before reconcile | Verified project mapping | Skill publication at version 13382 | Selection denominator |
| --- | --- | --- | --- | --- |
| AGENTS.md + selected | 15 complete canonical records; current v2 | resolved and fingerprint-verified for this exact version | one `skill_available` **and one `skill_invoked`** | 1 measured, 1 selected |
| AGENTS.md + no selection/read | 14 complete canonical records; current v2 | same verified project install | one `skill_available`, **no `skill_invoked`** | 1 measured, **0 selected** |

Both arrived and published **automatically before any reconcile**. Private native rows were checked against the delivered canonical source; the current skill publication and mapping were complete, not merely the session-wide ingest. A bounded forced replay of **both** created new publication generations with identical available/selected counts. Version-scoped warehouse, owner API and CLI before and after replay showed **2 verified-present / 2 measured / 2 offered / 1 selected**, with an explicit per-session zero only for the no-selection control. **Confirmed loads and load attempts remained null/not recorded**, not zero. A **new** worker report generated after replay, CLI `show`, sanitized HTML and main-source desktop/mobile browser views preserved those figures and called the positive signal **Codex selections, not a separately confirmed load**. The report/export privacy check found no raw typed prompt, AGENTS.md instruction fragment, skill-body fragment, credential or private provider control marker; screenshots and private source stay outside Git.

This proves the combined-context path for **these two** automatically delivered, pinned 0.163.0 project sessions; it does not opt in other combined-kind shapes, Codex versions, home/mixed scopes or a confirmed-load signal. This follow-up is local: PR #1972 still points to the earlier MCP-only head, and current-head CI and independent code/privacy/UI approval remain outstanding.

## Unclosed boundaries

- User-scope Codex skill presence/selection, mixed-scope attribution, CLI 0.164.0 skill selection, separately confirmed loads, instructions-supplied inference, direct file-access counts and registry-hook execution are **not enabled by this slice**. These require their own native and product gates.
- Final local cross-slice regression after the combined-context change: **456 passed** (parser, pinning, scope SQL, API, CLI, reports and existing MCP checks). Ruff lint/format and frontend typecheck passed; frontend lint passed with 96 nonfatal warnings. One deterministic Playwright Codex report test passed in isolated loopback Chrome before this parser-only follow-up; the main-source desktop/mobile live checks passed after the follow-up rebuild. The previous green Linux CI run was for the **MCP-only** head; rerun CI after these changes. A full local test-suite pass and accountable human code/privacy/UI approval are not claimed.
- This bounded result does not change the Cursor or Kiro CLI V3 evidence gaps. Kiro V2 and IDE stay excluded.
