<!-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Core documentation batch: verification and handoff

## Scope

This is the first registry-first documentation batch, not the complete documentation renewal. It includes the overview, CLI installation, joining a registry, first-agent walkthrough, local source evaluation, core concepts, coding-tool overview, cross-harness sharing, and selected correctness fixes. The four audience tabs retain existing content while establishing the new entry paths. Comprehensive ARD, A2A, insights, administration, and integration rewrites remain follow-up work.

The companion inventory is a **pre-rewrite baseline** of 112 Markdown files, not a live generated inventory or a correctness certification. New guides and moved navigation entries in this PR deliberately change its counts. Page dispositions and later batches remain proposals.

## Source and environment

- Live workflow checks used source baseline `8d12d9cf1d684f0a9445808ea142164d8110a885`. The branch was subsequently fast-forwarded to `a64d4380` (AGENTS.md guidance only; no implementation changes), and the read-only inventory variant was verified against that revision.
- Source CLI: `1.17.0-dev.0`.
- Running local API advertised `1.17.0-dev.0`; image/source byte-for-byte parity was not independently established.
- Local API reached through `http://localhost:3000`; the default load-balancer port 80 was not running in this environment.
- Claude Code executable: `2.1.286`.
- Mintlify CLI: `4.3.3`; preview client: `0.0.4083`.
- CLI verification used a temporary HOME and project, not the contributor's normal registry credentials or active harness configuration.
- Live verification used known local demo user and reviewer accounts. Self-registration was disabled. No real account was created and no existing password or deployment policy was changed.

## Live registry/install verification

| Check | Result |
| --- | --- |
| Credential login with `--no-setup --output json` | Passed with ordinary demo user |
| `auth whoami` | Passed |
| Approved agent list before test resource creation | Passed; empty list, not an authentication failure |
| Create a minimal Claude Code agent without third-party components | Passed; initially pending |
| Show through its actual `namespace/slug` | Passed |
| Pull pending agent as its author | Refused with not-found/approval error under current policy, as documented |
| Find the existing submission | A repeated submit reported it was already under review; no duplicate or policy override was used |
| Approve and publish the test submission as a separate reviewer | Passed; both CLI operations completed |
| Dry-run the approved install | Passed; planned profile returned and project files unchanged; no installed-agent tracking recorded |
| Project-scope pull | Passed; Markdown profile with YAML frontmatter and project lock written |
| Re-pull without upgrade | Passed; same version, resolved from `project-lock` |
| Scan Claude Code configuration | Passed; read-only `scan --inventory --harness claude-code --output json` also returned the generated agent profile |
| Launch `claude --agent` with the generated local name | Command attempted, but model execution blocked by missing Claude authentication in isolated HOME; response reported zero API duration and zero cost |
| Archive disposable test agent | Passed; test listing removed from approved browsing |

Pull wrote registry session hooks and one registry delegation MCP entry even though the test agent had no third-party components. The guides now explain those configuration and data-reporting implications before installation.

**Not verified:** successful model task completion, session delivery, insight generation, remote A2A execution, a fresh Docker deployment, published installer execution, Windows runtime, every harness, or every permission configuration. The local health response was HTTP 200 with `status: degraded` and unreachable ClickHouse. Registry/install success is not evidence that telemetry is healthy.

## Documentation validation

- Full `mint validate`: strict build passed with zero errors and zero warnings after fixing two existing contributor pages' HTML-comment/frontmatter parse errors.
- `mint broken-links --check-anchors --files ...`: passed for the ten affected product pages.
- Full file inventory: every original Markdown file classified once; all proposed current navigation targets resolve locally.
- Browser smoke checks: ten affected product routes returned HTTP 200 with the expected headings at 1440px and 390px. No document-level horizontal overflow or raw component markup was detected.
- Installation OS tabs: browser interaction showed the Windows instructions. The first-agent guide includes PowerShell variable assignment and line-continuation variants; Windows execution itself remains unverified.
- Dark desktop homepage: audience tabs present. Screenshots cover overview and first-agent walkthrough on desktop/mobile plus dark overview.
- Screenshots are captured browser evidence, not proof of a human visual review. Image inspection is disabled in this assistant session; human visual/editorial review remains required.
- Search in the local Mintlify preview requires dashboard authentication and was not exercised.

## Python checks

Focused workflow tests passed: **253 tests** across:

- `test_pull_and_agent_cli.py`
- `test_agent_owner_draft_install.py`
- `test_cmd_auth.py`
- `test_agent_harness_narrowing.py`
- `test_pull_user_only_mcp.py`

`make test` was attempted, reached approximately 99%, logged failures, and exceeded its execution window before producing a summary. A second full-suite attempt with the pytest-timeout plugin was interrupted after logging failures and fixture errors. Neither run is a pass. Exact failure attribution and baseline reproduction were not completed; do not claim these are established pre-existing failures.

The PR must remain a draft while full-suite/CI status and human review are unresolved. No Python, API, worker, infrastructure, or application frontend implementation was changed in this batch.

## Screenshot provenance

Files under `docs/img/docs-renewal/` are full-page captures of the local Mintlify preview of this documentation branch, using Chromium headless shell and Playwright. They contain documentation text, not private runtime sessions or account credentials. Each raster has an SPDX sidecar. They are review artifacts, not illustrative screenshots of a running registry application.

## Review priorities

1. Confirm the registry-first overview and audience structure reflect the public product.
2. Walk the first-agent path with a real model-authenticated coding tool.
3. Exercise a fresh local source setup separately; do not infer it from the already-running local API.
4. Review the side-effect/privacy wording for login, pull, hooks, and delegation.
5. Confirm version policy before merging development-verified behavior into the live docs site.
6. Inspect the screenshots and preview; verify the PowerShell variants on Windows.
7. Resolve full-suite/CI outcomes before considering this PR merge-ready.

The publishing workflow is unchanged: this branch and draft PR are for review, not authorization to merge or deploy.
