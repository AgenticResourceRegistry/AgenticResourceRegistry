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

## Fresh main-based HTTP controls (subsequent to the stdio checks)

Three **new**, distinct owner-only 0.1.0 draft listings were installed from the Registry into fresh disposable Codex homes and workspaces with an inert required `X-Control` header and loopback streamable-HTTP endpoints. The A and B endpoints expose identically named tools but separate request paths; D exposes a tool that requires approval and is denied in the tested read-only policy. Native **Codex CLI 0.163.0 project** and **0.164.0 user/project** runs produced ten automatically delivered sessions **before reconcile**. Each source was current v2 and each A/B/D install fingerprint was verified. No real service or credential was used.

| HTTP control | Verified-present listing(s) | Eligible publication | Independent endpoint observation |
| --- | --- | --- | --- |
| 0.163.0 project success, executed error, no-tool | A (3 sessions) | 3 measured; 1 success, 1 error, 1 zero | A endpoint reached twice, neither on no-tool |
| 0.164.0 user success, executed error, no-tool | A (3 sessions) | 3 measured; 1 success, 1 error, 1 zero | A endpoint reached twice, neither on no-tool |
| 0.164.0 user approval/null-result denial and no-tool | D (2 sessions) | **denial unsupported/unknown**; no-tool measured zero | **No tool call** reached D in either session |
| 0.164.0 project two-active-server no-tool, then B-selected call | A and B (2 sessions) | both measured; **B success only** on the second session | B endpoint reached once; A never reached |

The first-party native record alone classified the denied/null-result attempt as **unsupported**, not an executed error or a no-call measurement. In the two-server run the linked native call matched **B's verified pin** and the independent HTTP endpoint marker was B only. Both servers' installed presence is not proof that both were used. Initial worker publications were complete for nine sessions and unsupported for the denial. On the initial publication version, owner version-scoped API, CLI, worker reports and sanitized HTML agreed on **A: 8 present / 8 measured / 4 calls (2 errors); B: 2 / 2 / 1 success; D: 2 present / 1 measured / 0 calls**. Anonymous activity and report access returned 401; an unrelated reviewer received 403. Prompts, control markers, headers and tool results were absent from reports and HTML. The exact main-source Vite UI (pinned lockfile dependencies, with a loopback API and third-party requests blocked) displayed these counts and the D **1 of 2** coverage on desktop and mobile without horizontal overflow. Screenshots remain outside Git.

The later Codex skill matcher revision invalidated the shared publication version. A **bounded forced replay** of all ten HTTP sessions on the rebuilt main-based API/worker again yielded nine complete sessions and the one unsupported denial. A second forced replay after the final isolated rebuild retained those results. Fresh owner API, CLI, three worker reports/HTML exports and desktop/mobile pages retained the same totals and privacy/access controls. This is separate from the stdio cohort; identical A/B/D labels do not mean shared listings or merged transport counts.

## Compatibility and remaining gates

- Codex receipts without an explicit non-empty stdio env retain their historical fingerprint; new non-empty env receipts bind the env by a tagged local digest. Previously issued non-empty-env receipts omitted this binding and **must not be silently accepted or repinned**. Until explicitly reviewed and repinned, they fail verification. Other harnesses' historical fingerprints are unchanged.
- This HTTP run proves only the tested loopback streamable-HTTP header/policy configurations on the native CLI versions/scopes named above. **HTTPS, OAuth and broader authentication modes, real remote endpoints and additional Codex versions remain untested**. The earlier stdio controls do not extend HTTP claims, or vice versa.
- Earlier main-port test checkpoints were **406 Codex tests plus 98 component tests**. The upstream-main draft PR #1972 CI rerun at `94fb259dd` passed its full Linux matrix, lint, REUSE and Docker build, but **does not cover the later skill changes**. The earlier local serial suite timed out; do not claim a local full-suite pass. The prior web-image build timed out, but the main-based HTTP browser check described above did pass via Vite dev mode; these are not the same gate.
- PR #1972 remains **draft**. Rerun applicable CI for any new commits, request independent code/privacy/UI review, and obtain accountable human authorization before a release or merge decision. Codex skill selection is documented separately; registry-hook execution is **not** established by this MCP port. Never treat installation, `scan`, a session-wide zero or a hook-owned receipt as verified component use.
