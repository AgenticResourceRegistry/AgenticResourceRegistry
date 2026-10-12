<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Observal Product Roadmap

Observal is the control plane and system of record for portable AI components. The registry and its governance workflows are the product center; session evidence, insights, installation, and platform operations make those governed components useful and trustworthy in practice.

This roadmap covers the public planning horizon from August 2026 through August 2027. It documents what the project intends to do and not do during at least the next year, satisfying the [OpenSSF Best Practices roadmap criterion](https://www.bestpractices.dev/en/criteria/1#1.documentation_roadmap) required on the path to Gold.

This roadmap advances three product lanes in parallel:

1. **Observability**: collect the correct session data, attribute it to the exact agent and components in use, and turn it into evidence people can trust.
2. **AI Components SDLC**: create, validate, publish, review, version, distribute, and improve agents and components with less friction and clear provenance.
3. **Platform**: make Observal easy and safe to operate through its CLI and skills, teamspaces, hosting paths, administration, CI/CD, and project security controls.

## Status legend

Status is determined by each initiative's outcome-based completion signal, not by whether implementation has started.

- **Planned**: no qualifying implementation has merged, or prerequisite work is still pending.
- **In progress**: relevant work has merged or is under review, but the completion signal is not yet satisfied.
- **Completed**: the complete outcome has shipped. The status includes the completing pull request.

The 20 most recently merged pull requests were reviewed when this roadmap was created on 2026-08-10. None satisfied an entire initiative's completion signal. Relevant partial work is linked as progress rather than marked complete.

## Priority legend

Priority is explicit and is not inferred from list position.

- **P0, higher priority:** advance as soon as its stated dependencies hold.
- **P1, standard priority:** sequence by dependency and product value.
- **P2, lower priority:** retain as planned work, but do not let it block higher-priority outcomes.

## Dependency stages

- **Stage 1, trustworthy foundations**
- **Stage 2, governed workflows**
- **Stage 3, distribution and verified trust**

## How to use this roadmap

Stages express dependency order, not release commitments. The three lanes can advance in parallel once their dependencies hold. Before implementation, define the initiative's scope and dependencies, reuse the linked contracts, preserve the stated boundaries, and close the initiative only when its outcome-based completion signal is true. Keep tightly coupled changes together when splitting them would leave incomplete workflows or unstable intermediate contracts.

## Lane 1: Observability

### Stage 1: Trustworthy foundations

#### Trustworthy session and component attribution, P1

**Status:** In progress. [PR #1671](https://github.com/Observal/Observal/pull/1671) corrected Kiro agent attribution, but the completion signal covers every supported harness and component.

Resolve each attributable session to the exact installed agent version and component IDs and versions, including standalone components. Reuse the lockfile and layer snapshot as installation evidence, preserve explicit `unknown` and dirty-install states, and never infer attribution from a display name alone.

**Complete when:** every supported harness fixture produces consistent session → agent version → component version evidence, component attribution survives reconciliation and replay, and existing sessions that can be resolved safely are backfilled without guessing.

#### Harness and session-data correctness gate, P1

**Status:** In progress. [PR #1667](https://github.com/Observal/Observal/pull/1667) added Goose coverage and [PR #1671](https://github.com/Observal/Observal/pull/1671) strengthened Kiro attribution, but the cross-harness interruption and repair gate is not complete.

Exercise source discovery, complete-record handling, durable outbox delivery, contiguous checkpoints, finalization repair, parsing, and reconciliation for every supported harness. Recovery and normal hooks must continue to use the same ingest contract.

**Complete when:** each harness passes interruption, duplicate delivery, stale local state, partial-record, finalization-repair, and parser regression scenarios, and dashboards no longer silently turn ingest failures into zeroes.

#### Management-dashboard long-horizon pilot, P0

**Status:** Planned.

Test the current management dashboard with real leadership users and realistic multi-month, high-agent-count data. Establish the questions leaders need answered, the source-of-truth formula for each KPI, a recurring feedback cadence, and whether users trust each result. Use the evidence to iterate toward [issue #1592](https://github.com/Observal/Observal/issues/1592): one clearer effectiveness view, per-agent drilldowns, and contextual URL-backed filters. Treat its proposed layouts and charts as hypotheses until testing supports them.

**Complete when:** adoption, effectiveness, cost, success, and per-agent questions have validated formulas and source attribution; feedback rounds produce tracked decisions; and users can explain both a metric and the evidence behind it. This pilot continues across all three stages.

#### Insights and dashboard feedback loop, P0

**Status:** In progress. [PR #1638](https://github.com/Observal/Observal/pull/1638) added evidence-backed registry recommendations, and [PR #1669](https://github.com/Observal/Observal/pull/1669) is adding completed insight reports to Inbox. Component and success-criteria findings remain.

Start with current agent reports and dashboard data, then extend the same evidence model to component and success-criteria findings. Route completed reports and actionable findings through Inbox rather than building another notification or analytics system.

**Complete when:** report findings can lead a user from Inbox or dashboard summary to the relevant insight and supporting sessions, and dashboard changes are traceable to observed usage or explicit feedback.

### Stage 2: Governed workflows

#### Component Insights, P1

**Status:** Planned.

Provide component-level analysis comparable to agent insights: attributed sessions and active users, the agents and versions that included the component, usage trends, quality and friction findings, and supporting trace drilldowns.

**Complete when:** an owner can explain where a component is used, how its versions perform, and which sessions support each conclusion; unattributed sessions are reported separately instead of being assigned heuristically.

#### Success-criteria evaluation in Insights, P1

**Status:** Planned. [PR #1623](https://github.com/Observal/Observal/pull/1623) added agent success-criteria metadata, but version-correct evaluation and component criteria are not implemented.

Load server-side criteria for the session-attributed agent and component versions. Evaluate each metric as `met`, `not_met`, or `insufficient_evidence`, with trace-backed reasoning, and use those results throughout the report without fabricating measurements.

**Complete when:** criteria evaluations are version-correct, distinguish missing evidence from failure, link to supporting sessions, and produce the same result when regenerated from the same evidence.

### Stage 3: Distribution and verified trust

The long-horizon dashboard pilot and Insights feedback loop continue here using the component and success-criteria evidence delivered in Stage 2. No separate dashboard data model is introduced.

## Lane 2: AI Components SDLC

### Stage 1: Trustworthy foundations

#### Agent Skills specification alignment, P1

**Status:** Planned.

Align skill creation, editing, display, and installation with the Agent Skills specification. A standard skill with only required `name` and `description` must be accepted; standard optional fields and resources must survive the full lifecycle; Observal-only registry metadata must be optional or defaulted.

**Complete when:** a conforming external skill can enter through CLI, web, or Git source and install without Observal-specific edits, while malformed standard fields receive the same actionable errors in every path.

#### Vendored Agent Skills reference validation, P1

**Status:** Planned.

Vendor a pinned and attributed copy of the official `skills-ref` validation and property-reading implementation. Remove Observal's competing frontmatter parser; retain repository fetch and clone and skill-path discovery only as source acquisition before invoking the vendored implementation.

**Complete when:** submission, marketplace import, Git sync, and pre-install checks use one conformance implementation, upstream provenance is recorded, and one regression corpus protects Observal-specific integration behavior.

#### Configurable pull-request-style review, P1

**Status:** In progress. Existing version and review routes provide the base, and [PR #1669](https://github.com/Observal/Observal/pull/1669) adds review assignments and decisions to Inbox. Conversations, approval counts, and policy presets remain.

Replace binary review friction with a GitHub/GitLab-style, version-scoped workflow: full diffs, line- and field-anchored threads, general discussion, blocking and non-blocking feedback, change requests, resolved conversations, activity history, reviewer state, and configurable approval counts.

Offer three understandable policy presets instead of a bespoke rules engine:

- **Strict:** review every release.
- **Balanced, recommended:** review initial, major, and high-risk releases; auto-approve validated low-risk patch and minor releases from trusted owners.
- **Fast:** auto-approve after required validation.

Ownership, visibility, source, failed validation, and new or expanded executable permissions, such as hooks, scripts, or MCP commands, are always high risk. Global admins control public-registry policy. Team owners may select a team-private preset or auto-approve toggle only within the global safety ceiling. Every automatic decision records its checks and rationale in review history and Inbox.

**Complete when:** submitters and reviewers can conduct the full review from one workspace, configured approval counts are enforced, low-risk updates can avoid manual review under policy, and no high-risk or failed-validation release can bypass review.

#### Server-side success criteria for agents and components, P1

**Status:** In progress. [PR #1623](https://github.com/Observal/Observal/pull/1623) added agent success criteria, but it stores them in local manifests and snapshots and does not cover every component type, so the completion signal is not met.

Add first-class CLI and web workflows to define, inspect, update, and clear optional criteria for agents and every component type. Criteria are server-side version metadata only: exclude them from local agent manifests, lockfiles, generated harness configuration, component packages, and install responses. Any criteria change requires an explicit semantic-version bump and follows the configured review policy.

**Complete when:** criteria are independently versioned and reviewable, installed artifacts contain none of the criteria metadata, and Insights can load the exact criteria attached to an attributed version.

### Stage 2: Governed workflows

#### Git webhook version sync, P1

**Status:** Planned.

Let an owner link a GitHub or GitLab repository to an external skill or MCP. A verified push, tag, or release event resolves the source commit and creates a new immutable Observal version in the normal review flow; an approved version is never silently mutated.

**Complete when:** duplicate delivery is idempotent, source commits are immutable and visible in review, failed validation blocks release, and webhook-created versions obey the selected review policy.

#### Independent fork drafts, P1

**Status:** In progress. Implemented in [PR #1799](https://github.com/Observal/Observal/pull/1799) (Phases 0–5), pending review and merge.

Fork a selected agent or component version into a provenance-linked draft in a personal or team namespace. Agent forks retain existing component references and never recursively fork components; each component fork is a separate explicit action.

**Complete when:** the fork can evolve and submit independently while source provenance remains visible and ownership, reviews, adoption, and insights begin separately.

#### Transfer ownership to a teamspace, P1

**Status:** In progress. [PR #1619](https://github.com/Observal/Observal/pull/1619) added teamspace identity and membership, and [PR #1640](https://github.com/Observal/Observal/pull/1640) added team publishing and visibility. Accepted, audited transfer into a teamspace remains.

Extend ownership transfer so an agent or component can move from a user namespace into a teamspace. Require acceptance by a team owner, preserve version, review, and provenance history, update its canonical namespace route, and apply the destination team's visibility and review policy.

**Complete when:** the transfer is atomic, audited, accepted by the destination team, and old canonical routes redirect without exposing private content.

#### Organization recommendation badge, P1

**Status:** Planned. [PR #1638](https://github.com/Observal/Observal/pull/1638) added personalized recommendations, not organization endorsements.

Allow a global admin to endorse an agent or component on behalf of the organization, for example, “Recommended by Appian,” with organization branding on cards and detail pages and a deliberate recommendation and search boost.

**Complete when:** admins can add and remove the endorsement, the ranking effect is testable and disclosed, and a badge cannot be self-awarded by a publisher.

### Stage 3: Distribution and verified trust

#### Federated external skill discovery and import, P1

**Status:** Planned.

Browse skills from Claude Code marketplaces, skills.sh, and AWS, Azure, and GCP catalogs inside Observal. Import a selected skill as an external or unclaimed, provenance-linked draft that passes standard validation and normal review.

**Complete when:** source identity and maintenance status are clear, imports cannot grant false publisher ownership or leaderboard credit, duplicate imports can be recognized, and a future verified claim can be represented separately from the importer.

#### SDLC refinement from usage, P1

**Status:** In progress. [PR #1638](https://github.com/Observal/Observal/pull/1638) added recommendations based on session evidence and registry reuse, but criteria evaluation and review-history signals remain.

Use component insights, criteria evaluations, review history, and source provenance to improve search, ranking, and owner workflows without creating a second analytics system.

**Complete when:** every automated recommendation or ranking signal names its source evidence, and owners can act on findings through existing version and review workflows.

## Lane 3: Platform

### Stage 1: Trustworthy foundations

#### Agent-ready CLI contract, P1

**Status:** In progress. [PR #1687](https://github.com/Observal/Observal/pull/1687) established shared table and JSON output modes. [PR #1690](https://github.com/Observal/Observal/pull/1690) adds validated examples to every help screen, removes duplicate example options, and adds the categorized error contract across the command tree. The help and error slices are complete; universal command-by-command output, empty-state, prompt and progress behavior, and bundled-skill JSON use still require verification before the completion signal is met.

Treat the CLI as an agent-facing API while preserving a clear human default:

- Add copy-pasteable examples to root and every command and subcommand help screen; remove duplicate example options.
- Make errors identify the failed operation and resource, the precise safe cause, a remediation, and a request ID; expose internal detail only under debug mode.
- Keep human-readable tables as the default and add universal explicit JSON mode. Finite JSON uses natural payloads: paginated lists return `{items, total, page, page_size}` and detail and mutation commands return direct result objects. JSON errors go to stderr with categorized non-zero exit codes; streams use JSON Lines.
- Keep formatting separate from prompting, dry-run behavior, file writes, and output destinations. Retire inconsistent format options through a compatibility window. Use external `jq` rather than embedding another query language.

**Complete when:** every command has deterministic table and JSON behavior; empty and error paths remain parseable; JSON mode never emits prompts, spinners, banners, or Rich text; and the bundled skills use JSON explicitly.

#### Agent-readiness audit for every CLI command, P1

**Status:** In progress in [PR #1690](https://github.com/Observal/Observal/pull/1690), which adds the CLI authoring contract and whole-tree help and error-boundary checks. The command-by-command bundled-skill matrix and supported-harness workflow trials remain.

Exercise each command through its relevant bundled skill. Verify stable schemas and exit codes, complete non-interactive inputs, confirmation and dry-run safety, idempotence, bounded pagination and output, secret safety, realistic help examples, and exact next actions. Maintain a runnable command-by-command contract and smoke matrix and trial workflows through supported harness agents.

**Complete when:** every supported command is represented in the matrix, critical workflows pass from each applicable harness, and stale skill instructions are corrected or removed.

#### Cross-product actionable Inbox, P1

**Status:** In progress in [PR #1669](https://github.com/Observal/Observal/pull/1669). That pull request adds the durable inbox foundation, review assignments and decisions, requested insight completion, and manually reported update notices. Review conversations, teamspace request events, and relevant system actions remain.

Create a durable per-user work and event feed, not only a notification bell. Cover installed-item updates, review assignments and conversations, change requests and decisions, teamspace requests, completed insights and reports, and relevant admin and system actions. Web and `observal inbox` support filters, unread and action-required state, history, and exact links or commands for taking action.

**Complete when:** each supported event is delivered idempotently to the intended users, can be queried through CLI and web, retains action history, and does not disappear merely because it was read.

#### Canonical shareable product URLs, P1

**Status:** Planned.

Replace user-facing UUID links with `/agents/{namespace}/{slug}`, `/components/{type}/{namespace}/{slug}`, and `/teamspaces/{handle}`. APIs may retain internal UUIDs, but cards, Inbox actions, share controls, ownership transfers, and redirects use canonical routes. Anonymous access to protected routes receives HTTP 401 and a sign-in challenge carrying the return URL. Signed-in non-members requesting team-private agents and components also receive HTTP 401 with no resource payload.

**Complete when:** all product objects have stable copyable URLs, renamed and transferred objects redirect safely, sign-in returns to the requested route, and unauthorized private responses reveal no object metadata.

#### Installed-item update notices, P0

**Status:** In progress in [PR #1669](https://github.com/Observal/Observal/pull/1669), which persists explicit `observal outdated` findings in Inbox. Periodic non-blocking checks and verified exact upgrade commands remain.

Reuse the installed lockfile inventory and outdated comparison to show a periodic, non-blocking “update available” notice for installed agents and components in the terminal and create the corresponding Inbox item. Do not update automatically.

**Complete when:** notices compare exact installed and latest approved versions, are cached to avoid noisy network checks, remain queryable in Inbox, and include the exact explicit upgrade command.

#### Teamspace creation and membership approvals, P0

**Status:** In progress. [PR #1619](https://github.com/Observal/Observal/pull/1619) added teamspaces and direct membership management, and [PR #1640](https://github.com/Observal/Observal/pull/1640) added team publishing. Membership request and approval workflows remain.

Any signed-in user can create a teamspace and becomes its initial owner. Teamspace creation is immediate and audited; it does not require admin approval. Team owners approve or reject membership requests, with requester, reviewer, decision, reason, and timestamps persisted in Inbox and history. Keep owner-driven direct member addition.

**Complete when:** immediate teamspace creation works in web and CLI and is audited, membership approval works in web and CLI, rejected requests cannot create membership, and the last-owner protections remain intact.

#### Teamspace share-to-join, P0

**Status:** Planned. Teamspace pages and membership exist from [PR #1619](https://github.com/Observal/Observal/pull/1619), but shared request-to-join links do not.

Add a Share button that copies the canonical `/teamspaces/{handle}` page. An anonymous recipient is prompted to sign in and redirected back to that page, where they explicitly select **Request to join**. The request asks only for member access and enters the owner approval flow; the shared link never grants access directly.

**Complete when:** the return path survives sign-in, expired sessions do not lose the destination, duplicate requests are handled clearly, and approval is required before the roster changes.

#### Audited logging for agent-led debugging, P1

**Status:** In progress. [PR #1666](https://github.com/Observal/Observal/pull/1666) fixed dropped security audit records and missing exception tracebacks. The end-to-end seeded failure audit remains.

Audit server and CLI logging for useful structured fields and levels, request and job correlation, retention and availability, failure-path coverage, and secret and PII redaction. Trial logging against seeded failures across deployment and harness paths before Debug-as-a-skill depends on it.

**Complete when:** trial failures can be followed across CLI and server by correlation ID, sensitive MCP and config values are not exposed, and missing or misleading log paths have regression checks.

#### Protected canonical release branches and tags, P1

**Status:** In progress. [PR #1642](https://github.com/Observal/Observal/pull/1642), [PR #1644](https://github.com/Observal/Observal/pull/1644), [PR #1648](https://github.com/Observal/Observal/pull/1648), [PR #1651](https://github.com/Observal/Observal/pull/1651), [PR #1655](https://github.com/Observal/Observal/pull/1655), and [PR #1656](https://github.com/Observal/Observal/pull/1656) hardened release generation, merge-queue operation, provenance, and recovery. Canonical release branches and every stated repository control are not yet complete.

Move `release/v*` branches from maintainer forks into `Observal/Observal`. Protect `main`, `release/v*`, and `v*` tags with pull requests, two approvals, CODEOWNERS, stale-review dismissal, last-push approval, required CI and security checks, merge queue, and no force-push, deletion, or admin bypass. Restrict release-tag creation and publishing to the release app without changing the current curated cutoff semantics.

**Complete when:** repository rulesets enforce these controls, release recovery still works, and the public Scorecard observes the protected branches and tags.

#### Official OpenSSF Best Practices Gold, P0

**Status:** In progress. Recent foundations include Scorecard in [PR #1643](https://github.com/Observal/Observal/pull/1643), dependency and permission hardening in [PR #1645](https://github.com/Observal/Observal/pull/1645), pinned dependencies and signed provenance in [PR #1648](https://github.com/Observal/Observal/pull/1648), parallel test execution in [PR #1652](https://github.com/Observal/Observal/pull/1652), and local OSS-Fuzz integration in [PR #1668](https://github.com/Observal/Observal/pull/1668). This document supplies the required public one-year roadmap. The official Gold record has not yet been awarded.

Drive the project from Passing through Silver to the official Gold badge. Required evidence includes reproducible builds, at least 90% statement and 80% branch coverage, two-person review, qualifying independent contributors, hardened project and download sites and modern TLS, and a documented security review.

**Complete when:** every applicable criterion has durable public evidence and the official project record awards Gold; displaying a badge image alone is not completion.

### Stage 2: Governed workflows

#### Setup-as-a-skill across all supported deployment paths, P1

**Status:** Planned.

Ship a guided setup skill for Docker Compose, Helm and Kubernetes, and existing cloud Terraform paths. Invoke the existing installers and IaC rather than reimplementing them; guide prerequisites, domain and TLS, secrets, databases, first-admin bootstrap, current backup readiness, health checks, local CLI login, harness instrumentation, and end-to-end session verification.

**Complete when:** clean-environment trials for each supported path reach a healthy server and a visible reconciled session, with failures pointing to the exact recovery step.

#### Debug-as-a-skill, P1

**Status:** Planned. It depends on the logging audit above.

Diagnose the complete stack across supported deployments and local harnesses. Classify the symptom; run existing read-only doctor, status, diagnostics, log, support, and reconcile checks; correlate audited logs; explain the root cause; propose the smallest safe fix; execute repairs only after confirmation; then rerun the failed check.

**Complete when:** seeded trials cover deployment, authentication, database, ingest, hook and extension, attribution, and registry failures, and the skill demonstrates root-cause recovery without unsafe guesses.

#### Post-upgrade changelog and frontend tour, P2

**Status:** Planned.

After an Observal release is installed, show the relevant changelog in both the web frontend and CLI. The CLI presents release notes after a successful update, while the web frontend detects a deployment version change and shows the notes once per user with a permanent way to reopen them. Both surfaces use the same versioned release-note source so their descriptions do not drift.

The web frontend also offers an optional guided tour that spotlights where new features are located. Tours are frontend-only, user-initiated or explicitly accepted, skippable, and role-aware so they never point users to controls they cannot access.

**Complete when:** a successful server or CLI update shows the correct version's changelog, dismissed notes remain available on demand, and users can launch or skip a role-appropriate frontend tour that identifies the released features without blocking normal navigation.

### Stage 3: Distribution and verified trust

#### Google OSS-Fuzz integration, P1

**Status:** In progress. [PR #1668](https://github.com/Observal/Observal/pull/1668) added local OSS-Fuzz configuration, session parser and redaction fuzzers, corpora, and regression tests. The project is not yet present in the upstream `google/oss-fuzz` repository, and Agent Skills and SCIM filter targets remain.

Register Observal with OSS-Fuzz and run continuous Atheris fuzzing against untrusted session parser dispatch for every harness, the vendored Agent Skills parser and validator, and the SCIM filter parser. Maintain seed corpora and turn each confirmed crash into a small repository regression test.

**Complete when:** upstream OSS-Fuzz accepts the project, fuzzers run continuously, findings have an owned response path, and regressions remain in normal CI.

#### Sustained OpenSSF Scorecard 9+, P1

**Status:** In progress. [PR #1643](https://github.com/Observal/Observal/pull/1643) added Scorecard, [PR #1645](https://github.com/Observal/Observal/pull/1645) tightened dependencies and permissions, [PR #1648](https://github.com/Observal/Observal/pull/1648) added pinned dependencies and signed provenance, and [PR #1668](https://github.com/Observal/Observal/pull/1668) added local fuzzing integration. A recurring public score of at least 9.0 has not yet been sustained.

Raise the public score from 7.9 at roadmap creation to at least 9.0 and keep it there across recurring scans. Maintain underlying controls: human review, branch protection, Gold evidence, continuous fuzzing, complete SAST, and consistently signed and provenance-bearing releases.

**Complete when:** recurring public scans remain at or above 9.0 and regressions create tracked work; a one-time score is not completion.

#### Transactional local server upgrade and rollback, P2

**Status:** In progress. [PR #1662](https://github.com/Observal/Observal/pull/1662) made migration failures fail safely instead of stamping a broken database as current. Coherent backup, health verification, and automatic full rollback remain.

Harden `observal server upgrade` and explicit rollback around a coherent backup of PostgreSQL, API and JWT keys, ClickHouse data, and image and config state. Pull and recreate target containers, apply migrations, run health and smoke checks, and restore all previous state automatically on any failure. Do not add a remote arbitrary-execution control plane.

**Complete when:** failure-injection tests at backup, pull, migration, startup, and health-check stages always end on a proven healthy target or previous stack, never a half-migrated deployment.

## Cross-lane dependencies

- Stable component attribution precedes Component Insights, evidence-based agent and component criteria evaluation, and trusted management-dashboard KPIs.
- The vendored Agent Skills implementation precedes Git webhook sync and external marketplace import.
- Review presets and Inbox precede scalable webhook and import review conversations, automatic review decisions, and durable decision history.
- Canonical namespace routes are reused by Inbox actions, teamspace sharing, ownership transfer, and external-source provenance.
- The universal CLI contract and command audit precede Setup-as-a-skill, Debug-as-a-skill, Inbox CLI workflows, and criteria commands.
- The logging audit precedes Debug-as-a-skill trials and release.
- Setup-as-a-skill verifies the backup capability available in the current release. The lower-priority transactional rollback work later strengthens that guarantee without blocking setup guidance.
- Canonical protected releases, OSS-Fuzz, signed provenance, complete SAST, and Gold evidence jointly drive a sustained Scorecard of 9+.
- Dashboard testing and feedback begin in Stage 1; major UI consolidation follows evidence rather than preceding it.

## Product boundaries

The following are explicitly outside this roadmap's intended implementation:

- Update notices never install an agent or component automatically.
- Notification channels mean terminal notices plus the Observal Inbox, not Slack or email delivery.
- Review customization uses Strict, Balanced, and Fast presets, an approval count, and scoped auto-approve toggles, not a general-purpose policy language.
- Success criteria are server-side evaluation metadata and never ship with installed agents or components.
- Forking an agent never recursively forks its components.
- Teamspace sharing uses the canonical teamspace page, sign-in return, and an explicit membership request; the link itself never grants access.
- Marketplace import never masquerades as local authorship or grants publisher leaderboard credit.
- Server lifecycle work does not introduce arbitrary remote shell execution.
- Frontend tours are optional guidance and never block normal navigation or run in the CLI.
- Stages communicate dependency order, not delivery dates.

## Existing implementation to build on

Future epics should extend these committed contracts instead of creating parallel systems:

- **Product and harness inventory:** [`README.md`](README.md)
- **Session delivery and repair contract:** [`docs/core-concepts/session-tracking.md`](docs/core-concepts/session-tracking.md), [`observal-server/api/routes/ingest.py`](observal-server/api/routes/ingest.py), and [`observal-server/services/session_ingest.py`](observal-server/services/session_ingest.py)
- **Installed state and layer evidence:** [`observal_cli/lockfile.py`](observal_cli/lockfile.py), [`observal_cli/layer.py`](observal_cli/layer.py), and [`observal-server/api/routes/layer_snapshot.py`](observal-server/api/routes/layer_snapshot.py)
- **Harness parsers and extension guide:** [`observal-server/services/session_parsers/`](observal-server/services/session_parsers/) and [`docs/adding-a-harness.md`](docs/adding-a-harness.md)
- **Management dashboard:** [`observal-server/api/routes/exec_dashboard.py`](observal-server/api/routes/exec_dashboard.py), [`web/src/pages/admin/dashboard/`](web/src/pages/admin/dashboard/), and [issue #1592](https://github.com/Observal/Observal/issues/1592)
- **Agent and component versions and review:** [`observal-server/api/routes/agent_versions.py`](observal-server/api/routes/agent_versions.py), [`observal-server/api/routes/component_versions.py`](observal-server/api/routes/component_versions.py), [`observal-server/api/routes/review.py`](observal-server/api/routes/review.py), and [`web/src/components/review/`](web/src/components/review/)
- **Skills:** [`observal-server/schemas/skill.py`](observal-server/schemas/skill.py), [`observal-server/services/skill_validator.py`](observal-server/services/skill_validator.py), and [`docs/cli/skill.md`](docs/cli/skill.md)
- **CLI and bundled skills:** [`observal_cli/main.py`](observal_cli/main.py), [`observal_cli/client.py`](observal_cli/client.py), [`observal_cli/render.py`](observal_cli/render.py), and [`observal_cli/skills/`](observal_cli/skills/)
- **Recommendations and dynamic policy settings:** [`observal-server/api/routes/recommendations.py`](observal-server/api/routes/recommendations.py), [`observal-server/services/user_recommendations.py`](observal-server/services/user_recommendations.py), and [`observal-server/services/dynamic_settings.py`](observal-server/services/dynamic_settings.py)
- **Teamspaces:** [`observal-server/api/routes/teams.py`](observal-server/api/routes/teams.py), [`observal-server/models/team.py`](observal-server/models/team.py), and [`docs/use-cases/teamspaces.md`](docs/use-cases/teamspaces.md)
- **Server setup and lifecycle:** [`install-server.sh`](install-server.sh), [`observal_cli/cmd_server.py`](observal_cli/cmd_server.py), [`observal_cli/server/backup.py`](observal_cli/server/backup.py), and [`docs/self-hosting/`](docs/self-hosting/)
- **Release and security automation:** [`tools/release.py`](tools/release.py), [`.github/workflows/release.yml`](.github/workflows/release.yml), [`.github/workflows/scorecard.yml`](.github/workflows/scorecard.yml), and [`.github/workflows/codeql.yml`](.github/workflows/codeql.yml)
