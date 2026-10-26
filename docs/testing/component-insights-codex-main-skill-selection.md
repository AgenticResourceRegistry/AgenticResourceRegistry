<!-- SPDX-FileCopyrightText: 2026 Observal Contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Codex project-skill selection: main-based acceptance ledger

**Draft PR #1972 evidence, not release approval.** This is a separate cell from the MCP controls in [the main-port ledger](component-insights-codex-main-port-acceptance.md). Tests used a fresh owner-only Registry draft, a single registry-direct 0.1.0 project-skill install, a disposable Codex home/workspace, an inert loopback Responses provider and the isolated main-based API/worker. Neither ordinary harness profiles nor the original checkout were changed. Tokens, prompts, instructions, transcripts, provider output, draft IDs and desktop/mobile screenshots remain in private local controls **outside Git**.

## Metric and gate

Only native **Codex CLI 0.163.0** is opted in. The first-party project skill inventory must identify one unambiguous project root and exact `SKILL.md` path. An attributed selection requires the native `skills.selected_skill_instructions` content kind, a preceding typed user turn with the same turn ID, a rooted exact-path expansion, a complete supported source and a unique contemporaneous **resolved, fingerprint-verified** project install in the same v2 layer. Published facts contain only alias, scope, time, location digest and registry IDs: no path, body, prompt or raw transcript. This is **skill selected**, not a separately confirmed load, proof of model reliance or effectiveness. Availability is not selection. A file read, pasted expansion or name mention is not selection.

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

## Unclosed boundaries

- User-scope Codex skill presence/selection, mixed-scope attribution, CLI 0.164.0 skill selection, separately confirmed loads, instructions-supplied inference, direct file-access counts and registry-hook execution are **not enabled by this slice**. These require their own native and product gates.
- Final local cross-slice regression: **446 passed** (parser, pinning, scope SQL, API, CLI, reports and existing MCP checks). Ruff lint/format and frontend typecheck passed; frontend lint passed with 96 nonfatal warnings. One deterministic Playwright Codex report test passed in isolated loopback Chrome. The previous green Linux CI run was for the **MCP-only** head; rerun CI after these changes. A full local test-suite pass and accountable human code/privacy/UI approval are not claimed.
- This bounded result does not change the Cursor or Kiro CLI V3 evidence gaps. Kiro V2 and IDE stay excluded.
