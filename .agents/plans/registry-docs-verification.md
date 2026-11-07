<!-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Core documentation batch: verification and handoff

The original-batch record below is retained as history. The [review follow-up](#review-follow-up) records the new clean-deployment, reviewer-UI, model-task, and accessibility checks and supersedes the corresponding original blockers. Full-suite, release/publication, and human visual-review requirements remain open.

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

**Not verified in the original batch:** successful model task completion, session delivery, insight generation, remote A2A execution, a fresh Docker deployment, published installer execution, Windows runtime, every harness, or every permission configuration. The local health response was HTTP 200 with `status: degraded` and unreachable ClickHouse. Registry/install success is not evidence that telemetry is healthy.

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

## Review follow-up

### Corrections made

- The first-agent safety checkpoint now uses full JSON, selects an approved release, reads its exact-version detail and lock, and links instructions for inspecting each pinned component payload. Preview and pull use the same explicit version with strict pin/integrity checks.
- The Use Cases overview is registry-first. Immediate team-registry guidance no longer promises identical discovery scope, unconditional pending-author installs, or default instrumentation as onboarding.
- Claude Code documentation removes a copied “current” hook-spec number and describes localized isolated launchers. Opt-in project command hooks use `settings.local.json`. Cursor skill claims and the stale `admin review` command table are corrected.
- Task guides now cover inspection, authoring, review/publication, ARD discovery, A2A delegation, and interpreting insights. Their references remain supporting detail. Missing Codex, Copilot CLI, Antigravity, and version-specific Cursor activation walkthroughs are explicitly labeled documentation gaps, not missing adapters.
- Reviewer instructions explicitly return from Files changed to Conversation to reach the publication gate. Two captures of a synthetic local agent review illustrate inspection and published state.
- Version guidance explicitly identifies this revision as 1.17 development documentation, not a stable-release certification. Maintainer selection and verification of a stable publication target remain required.
- Full-site anchor checking found six existing broken references. They were repaired against actual headings, or removed where the claimed IAM-policy section did not exist.
- Automated accessibility checks found contrast failures in the inherited light accent and platform attribution. The teal primary was darkened from `#018399` to `#017184`, and a small token-based attribution CSS rule keeps the platform footer readable without hiding it.

### Fresh deployment and source installation

A clean archive of commit `af631690` was extracted into a separate temporary directory. New Docker images and the isolated `arr-docs-eval` Compose project were built from that source, with new database/application volumes. Host bindings were overridden for the local test (load balancer 18080, web 18081, no database/ClickHouse host ports), not copied into the public guide or production configuration.

Initialization completed, including PostgreSQL and ClickHouse migrations. The API returned `status: ok`, with PostgreSQL, ClickHouse, and Redis all healthy. This closes the original fresh-deployment gap for this source revision; it is not verification of downloadable release artifacts or a production deployment.

`uv tool install --editable` from the same clean archive succeeded with isolated tool/bin directories, preserving the contributor's normal CLI installation. Its version was `1.17.0-dev.0`, and it authenticated against the fresh API using the temporary CLI account state.

### First-agent and reviewer UI path

- Ordinary demo user login succeeded; the initial approved-agent list was empty.
- A synthetic agent was created. Full listing JSON contained its actual prompt and component links.
- Exact agent-version detail returned the prompt and version-specific components.
- The separate demo reviewer signed in through the web UI, opened the actual review, inspected Files changed, submitted approval, returned to Conversation, and published through the confirmation dialog. The published-state confirmation appeared.
- The approved release was visible to the ordinary user. Exact-version detail and release lock were read before strict preview and project-scope pull.
- Preview left project files unchanged. Strict pull installed the selected `1.0.0` release, and read-only inventory found its local profile.
- A small real Claude Code task used that generated profile and returned its instruction-specific `REGISTRY_GUIDE_CHECK` marker plus three README-summary bullets. The README remained unchanged. Reported model cost: **$0.006387**, two turns, successful exit.
- Model execution allowed only the built-in Read tool, disabled all hooks and skills/slash commands, used an empty strict MCP configuration, disabled session persistence, and capped spend at $0.25. The first attempt with project-only setting sources could not use model authentication; the successful attempt retained user authentication/settings while overriding hooks and MCP connections. This proves constrained activation and task execution, **not** session delivery, automatic capture, delegation, or general component execution.
- The disposable listing was archived. Only synthetic evaluation resources were changed; the isolated Compose project is cleaned up after validation.

The sample had no linked third-party components. Exact component-version endpoint/schema coverage comes from code and focused tests, not an end-to-end installation of every component kind. ARD/A2A/insight commands were checked against CLI help and source contracts; remote delegation and report generation were not run.

### Revised validation

- **411 focused tests passed** across two runs: 324 tests in the original five workflow suites plus agent-version routes, localized install launchers, and safe-path launchers; another 87 tests in component-version routes, component install versions, and agent component pins.
- Full Mintlify strict build passed with zero errors/warnings.
- Full-site internal-link and anchor check passed, including legacy pages.
- Browser smoke checks passed for **20 product routes at desktop and mobile widths** (40 route/viewport cases), with no document-level horizontal overflow.
- Axe WCAG 2 A/AA and 2.1 AA checks reported zero violations for overview, first-agent, and review/publication pages in both light and dark themes. Some checks remain incomplete and require manual review; zero automated findings are not accessibility certification.
- Keyboard activation of a visible onboarding link reached the intended page.
- Documentation-preview screenshots were refreshed. Product-review screenshots are clearly synthetic examples, not private runtime evidence. Image inspection remains disabled for this assistant, so human visual/editorial sign-off is still required.

### Still required before publication

1. Choose and verify the stable release and Mintlify publishing target; this PR currently declares a development documentation scope.
2. Resolve full-suite failure attribution/baseline reproduction. The original interrupted full runs are not passing results, and docs-only CI skipping Python is not a test pass.
3. Complete human visual, editorial, and manual accessibility review, including automated incomplete checks and Windows execution.
4. Complete the explicitly missing integration activation guides. Continue deeper ARD, A2A, insights, and component workflows with appropriate runtime evidence.
5. Verify capture/delivery and report behavior separately when required; the model task intentionally disabled those integrations.
