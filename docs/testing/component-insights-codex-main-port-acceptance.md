<!-- SPDX-FileCopyrightText: 2026 Observal Contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Codex MCP: main-based, isolated acceptance ledger

This is **not** release approval. The tested implementation began at upstream `main` `b3835ea1f` and the bounded port savepoint is `e531ba674`. The follow-up fixes to stdio-env pinning, native-table verification, and ordinary CLI scanning must be reviewed on their own commit. Controls used fresh owner-only Registry drafts, temporary Codex homes/workspaces, inert local MCP servers and a loopback Responses provider. The dedicated `observal_main_port_01` stack had separate images/volumes and loopback-only API/storage ports; neither the original checkout nor a production instance was used. Credentials, session files, provider output, prompts, IDs, and screenshots stay in mode-private local controls, **not** in Git.

## Ten-session registry-to-report stdio baseline

Native **Codex CLI 0.164.0**, scoped project/user `mcp_servers` installs and 0.1.0 scratch Registry versions. A, B and D below are distinct owner-only draft listings. Each of the ten native session files was **automatically delivered before reconcile**, with source records present and current v2 layer hashes. Nine sessions had supported complete MCP publications; the denied/null-result session was unsupported. Forced in-container replay preserved the same classifications and call counts. The first two-server attempt produced a genuine no-tool session; a separate retry exercised a successful two-pinned-server call to **B only**. Do not call the initial no-tool attempt a collision success.

| Session control | Verified-present listing(s) | Measured? | Observed attributed MCP call |
| --- | --- | --- | --- |
| User stdio success | A | yes | A success, 1 |
| User stdio executed error | A | yes | A error, 1 |
| User stdio no tool | A | yes | none, measured 0 |
| Project stdio success | A | yes | A success, 1 |
| Project stdio executed error | A | yes | A error, 1 |
| Project stdio no tool | A | yes | none, measured 0 |
| Project denial/null result | D | **no** (unsupported source) | **unknown**, not zero or executed error |
| Project no tool after denial | D | yes | none, measured 0 |
| Project two-server no-tool attempt | A and B | yes | none, measured 0 |
| Project two-server retry | A and B | yes | **B success, 1**; A 0 |

Version-scoped owner API, CLI `activity`/`sessions`/`show`, worker reports and sanitized HTML agreed: **A: 8 present / 8 measured / 4 calls (2 successes, 2 errors); B: 2 / 2 / 1 success; D: 2 present / 1 measured / 0 calls**. All three worker reports completed. Anonymous summary access returned 401; an unrelated reviewer received 403 for activity and reports. Synthetic prompt/control markers did not appear in the three report responses or exports. A session-level complete zero is not proof that *every* installed component was active or used. Report limitations concerning sender-cached hashes and unproven historical final pushes remain in force.

## Follow-up: pin drift and inactive legacy table

Two **additional** fresh owner Registry-backed 0.1.0 stdio installs supplied a synthetic, non-secret environment value. One used user scope; one used trusted project scope. Each emitted one native no-tool baseline session with a verified pin. The user config was then changed **only in the env value**; the project config moved the identical alias, command, args and env from native `[mcp_servers]` to `[mcp.servers]`. Each changed case emitted one more native no-tool session. The source files and credentials were not copied into this ledger.

| Control | Native state after change | Receipt verification | Automatically delivered source / mapping | Component-specific denominator |
| --- | --- | --- | --- | --- |
| User env baseline | active | verified | 14 source records, current v2, complete; verified identity | 1 measured no-tool |
| User env-only edit | still listed by native CLI | **drifted** | 14 source records, current v2, complete; drifted identity | **excluded, not zero** |
| Project native-table baseline | active | verified | 14 source records, current v2, complete; verified identity | 1 measured no-tool |
| Project legacy-only move | alias **absent** from native `codex mcp list --json` | **unverified** despite matching old entry fingerprint | 14 source records, current v2, complete; unverified identity | **excluded, not zero** |

The session-wide projector published **zero candidate calls for each of these four**; that does **not** make the drifted/unverified sessions measured zeros for this listing. The version-scoped owner summary and session list included **2 verified-present / 2 measured / 0 calls**, with the two changed sessions excluded and reported as **one drifted and one unverified presence gap**. Forced replay left that denominator unchanged. Ordinary `observal scan --harness codex --output json` discovered the canonical install; scan also inventories the legacy config, which is **not** native execution or verified installation. The private controls and two failed preliminary setup attempts were not counted as native sessions.

## Compatibility and remaining gates

- Codex receipts without an explicit non-empty stdio env retain their historical fingerprint; new non-empty env receipts bind the env by a tagged local digest. Previously issued non-empty-env receipts omitted this binding and **must not be silently accepted or repinned**. Until explicitly reviewed and repinned, they fail verification. Other harnesses' historical fingerprints are unchanged.
- Unit tests cover Codex 0.163.0/0.164.0 extraction and stdio/HTTP configuration guards. This fresh native follow-up is **0.164.0 stdio only**. Registry-backed HTTP denial, two-active-server collision and broader HTTPS/auth modes are not established by the stdio matrix.
- Focused Python checks on the amended main-based worktree: **406 passed** across the Codex install/extractor/scan matrix, and **98 passed** across component API/SQL/report/CLI coverage. One pre-existing SQL parameter assertion was updated to include the newly registered, version-gated Codex extractor. Ruff and formatting passed on changed Python files. The earlier full serial suite timed out at roughly 19%; no green full suite or pinned Linux CI is claimed. The main-based web build also timed out; no new browser acceptance or screenshots are claimed here.
- Obtain a green applicable Linux CI run, independent code/privacy review, and accountable human authorization before a new upstream-main PR or release claim. Codex skills and registry hooks are **not** part of this MCP port. Never treat configuration, `scan`, or a hook-owned receipt as verified component use.
