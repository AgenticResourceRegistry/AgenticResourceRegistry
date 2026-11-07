<!-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Agentic Resource Registry documentation renewal

## Status and authority

The product positioning, audience separation, writing principles, and Mintlify platform below were approved by the user in this session. Detailed page mappings are proposals, not publication approval. No public documentation has been changed by this planning batch.

The companion `registry-docs-inventory.csv` records every Markdown page at the pre-rewrite baseline and its proposed disposition. The repository's copyright hook adds an SPDX comment preamble; skip leading `#` comment lines and blank lines when importing the CSV. An inventory classification is not a completed factual review. Verify content against the selected code revision and release before rewriting or publishing.

The first core-page drafts and navigation changes are now prepared on `docs/registry-first-core-guides`. See `registry-docs-verification.md` for completed checks, observed policy behavior, and unresolved validation. These drafts are not authorization to merge or deploy.

## Approved product brief

- Public product name: **Agentic Resource Registry**.
- Keep **Mintlify** and the existing docs domain unless separately instructed otherwise.
- Keep implemented identifiers such as `observal`, `~/.observal/`, package names, and API paths. Branding changes must not invent command or identifier changes.
- Lead with the registry: finding, pulling, activating, authoring, versioning, and sharing agents and their resources.
- Give insights, Agentic Resource Discovery (ARD), and agent-to-agent delegation (A2A) practical product guides, not only reference tables or architecture decisions.
- Sessions and observability provide evidence for insights and debugging; they are not the default onboarding goal.
- Primary audience: developers and technically comfortable users. Beginners should have linked explanations without making experts read introductory computing lessons.
- Separate existing-registry users, local evaluators, resource authors, administrators/operators, API consumers, and contributors.

## Editorial standard

Task guides must answer:

1. What will I accomplish?
2. What do I need beforehand?
3. What exactly do I do?
4. What should I see afterward?
5. What if it fails?
6. What should I do next?

These are content requirements, not six mandatory headings on every page. Reference pages need a different structure: purpose, syntax/schema, options, examples, errors, and related guides.

- Use direct language, short paragraphs, specific headings, and copyable tested examples.
- Assume familiarity with tools such as Claude Code, Git, and Docker; link setup help where relevant.
- Briefly explain product-specific concepts where they affect a decision: registry agent, component, namespace, pinned version, activation, approval.
- Introduce ARD and A2A through what the reader can do with them; link protocol details.
- Put the glossary and technical explanations one click away, not in the middle of every task.
- Recommend one path. Put OS alternatives in tabs and advanced configuration on separate pages.
- Avoid unsupported timing promises and promotional claims.
- Distinguish local evaluation, connecting to an existing server, and production deployment.
- Explain changes to local files, scope, permissions, secrets, overwrite behavior, unsupported capabilities, and activation requirements.
- Never imply every harness supports every component equally.
- Distinguish installed, active, observed, and verified. Do not imply insights prove effectiveness or can attribute every component on every harness.
- Preserve security and evidence caveats; simplify their language, not their truth.
- Prefer canonical `namespace/slug` identities in examples. Show UUIDs where the actual operation requires them.
- Use one coherent example through onboarding. Clearly distinguish placeholders from resources that actually exist.

## Navigation proposal

Four reader-facing tabs, with contributor material linked separately:

### Guides

- Start here: overview, join an existing registry, try locally, first agent quickstart.
- Agents: inspect, pull, activate, create, publish, version, fork, share.
- Components: MCP servers, skills, hooks, prompts, sandboxes; submit, install, version, and sync.
- Team workflows: teamspaces, permissions, submissions, review, sharing, Inbox.
- Discovery: find resources; ARD purpose and supported cross-registry behavior.
- Delegation: A2A purpose, local versus remote agents, prerequisites, tasks, status, cancellation, and safety.
- Insights: agent reports, component evidence, limits, and actionable next steps.
- Sessions: view evidence, debug failures, understand capture limits and reconciliation.

Do not make all these groups equally prominent in the initial sidebar. Start with the shortest useful path and progressively disclose advanced workflows.

### Integrations

- Supported coding tools and per-capability matrix.
- Task-oriented setup and activation pages for supported harnesses.
- Preserve limitations, version requirements, and project/user scope differences.
- Review missing Codex, Copilot CLI, and Antigravity guides against the registry; do not create unsupported examples merely to fill a matrix.

### Self-hosting & Administration

- Deployment chooser, requirements, local versus production setup, Docker, supported cloud/Helm paths.
- Identity, authentication, SSO, provisioning, tokens, trusted proxies, security.
- Team administration, permissions, review and approval.
- Configuration, insights configuration, retention, upgrades, backup, migration, tuning, operational troubleshooting.

### Reference

- CLI reference.
- API and protocol reference.
- Configuration, component formats, hooks, glossary, architecture/evidence details.
- Architecture decisions remain accessible as technical background, not onboarding prerequisites.

## First-success journey

1. Choose: join an existing registry or run a local evaluation instance.
2. Install the CLI and authenticate using the actual supported flow.
3. Find an agent available to this account and inspect its components and harness support.
4. Preview the install, then pull into the chosen scope.
5. Activate using the harness-specific mechanism.
6. Verify the generated configuration and run a small real task.
7. Choose a relevant next step: customize, share, discover, delegate, or inspect insights when evidence exists.

An empty registry must have an explicit branch: use a verified sample/resource seed if one exists, or create and approve an example through documented supported operations. Do not promise a pre-existing agent on every fresh server. An onboarding sample must not require unnecessary third-party credentials or silently execute sensitive hooks.

## First rewrite batch

| Page | Reader outcome | Source material | Acceptance conditions |
| --- | --- | --- | --- |
| Overview (`docs/index.md`) | Understand the registry and choose a path | Current overview/concepts/use cases and implemented product | Registry-first introduction, one mental-model diagram, clear audience entry points, links to ARD/A2A/insights; no installation wall |
| Join an existing registry (`getting-started/onboarding`) | Connect a developer machine to their team's registry | Current onboarding, auth CLI and login behavior | No hardcoded internal server/VPN assumptions; explicit server URL placeholder; OS tabs; success and auth recovery |
| CLI installation (`getting-started/installation`) | Install and verify the CLI | Both current installation pages, installers, supported artifacts | One canonical installation guide; accurate platform alternatives; server deployment linked separately; safe upgrade/uninstall instructions |
| First agent (`getting-started/quickstart`) | Find, preview, pull, activate, and verify an agent | Pull/discover/list commands, adapters, integration guides | Tested existing-registry path; explicit empty-registry handling; supported harness example; expected results; no first-trace dependency |
| Try locally (new guide) | Run a disposable local registry and reach the agent workflow | Compose, setup, installers, requirements | Tested local defaults and health checks; demo credentials explicitly local-only; production warning; link to canonical first-agent path |
| Core concepts (`core-concepts/index`) | Understand agents, components, identity, versions, approval, and activation | Registry models, install contracts, lockfiles | Brief product model before implementation details; bearer-token correction; no unsupported portability claims |
| Integration overview (new guide) | Choose a supported tool and find activation instructions | Shared harness registry and adapters | Capability matrix matches code; supported does not mean equal capabilities or equal evidence coverage |
| Common setup problems (new or focused existing guide) | Recover from login, missing agent, unsupported harness, and activation failures | Real CLI/API errors and existing troubleshooting | Actionable fixes verified against behavior; no advice to bypass verification or overwrite unrelated configuration |

Alongside this batch, correct confirmed inaccurate claims in API reference and cross-harness sharing. Keep the broader reference rewrite separate from the onboarding rewrite.

## Confirmed corrections and duplication backlog

- `reference/api-endpoints.md`: advertises `X-API-Key` and API-key login; current auth dependency and login schema use JWT bearer tokens and email/password respectively. Verify all documented auth routes and device flow while revising.
- `core-concepts/index.md`: deployment-mode table advertises API-key authentication. Correct against the implemented auth model.
- `use-cases/share-agent-configs.md`: `vscode` harness identifier is not in the current registry; Claude Code profiles are Markdown with YAML frontmatter, not JSON. Review other paths and flags, not just these examples.
- `index.md` and `getting-started/installation.md`: overlapping installation content already differs. Repurpose the homepage and retain one installation source.
- `getting-started/onboarding.md`: replace internal deployment assumptions with a public existing-registry guide; retain genuinely internal instructions outside public onboarding if needed.
- Insights setup/configuration pages overlap; compare settings and runtime behavior before merging.
- SAML setup/settings pages overlap; preserve unique instructions and consolidate after checking settings sources.
- CLI SSO/API reference filenames are confusing; inspect content before moving or merging. Do not assume `cli-sso.md` and `sso-cli.md` are equivalent.
- Useful unlisted pages need deliberate navigation or contextual links; not all 39 unlisted Markdown files belong in the sidebar.
- GitBook artifacts are legacy alongside Mintlify. Confirm no active consumer before removing `.gitbook.yaml` or `SUMMARY.md`.

## Source-of-truth and publication gates

Before public changes:

- Identify the exact docs/code revision and intended release. Local main and the live site differed during the audit.
- Confirm Mintlify repository, branch, content directory, preview capability, and dashboard access. Live metadata during the audit indicated `Observal/Observal`, branch `main`, directory `docs`; recheck before publication.
- Decide stable-release versus development documentation policy. Do not quietly describe unreleased behavior as universally available.
- Confirm canonical public repository URLs after the product rename; do not mass-replace implemented identifiers.
- Confirm a named human documentation owner and relevant technical reviewers. This plan does not change CODEOWNERS or assign people.
- Obtain human review and explicit publication authorization per `AI_POLICY.md`; disclose nontrivial AI assistance.

## Implementation and review workflow

Page brief → source/behavior verification → draft → technical review → editorial review → Mintlify preview → newcomer task test → approved publication → live verification.

Use small coherent PRs. Do not rename every URL in one pass. Keep current slugs where useful; record source-to-destination redirects before moving pages. Update inbound links, search navigation, and contextual application help in the same migration.

## Verification checklist

- Validate `docs.json`, MD/MDX rendering, navigation targets, links, anchors, and redirects.
- Check CLI syntax against this checkout, not only an independently installed CLI version.
- Check API schemas/routes against code and OpenAPI; manually explain behavior and authentication even when schemas are generated.
- Exercise the first-agent journey on a running instance and with the chosen coding tool; mocked tests are insufficient.
- Verify both an available-agent path and an empty/unavailable-agent path.
- Verify activation and installed-file paths, scope, safe previews, and lock behavior.
- Verify permission/review behavior for ordinary users, not only super-admin demo accounts.
- Inspect desktop/mobile, light/dark, keyboard navigation, code overflow, image alt text, and diagrams in preview.
- Capture screenshots from a known current build with sanitized data; existing assets need freshness review before reuse.
- Keep credentials, personal session content, internal URLs, and unsupported security claims out of public screenshots/examples.
- Verify deployed pages and redirects after publication.

## Follow-up batches

1. Authoring, components, versions, team sharing, and review workflows.
2. Practical ARD and A2A guides, backed by implementation and live verification.
3. Insights guides separating user interpretation from operator setup and evidence limitations.
4. Self-hosting/admin consolidation and verified integration expansion.
5. Reference generation/checks, glossary, legacy artifact cleanup, and ongoing docs ownership.

## Scope of the initial audit

The inventory covers the full current Markdown tree. Representative user/operator/reference pages and selected implementation claims were reviewed. It is not a line-by-line correctness certification, a full accessibility audit, or an end-to-end test of every documented workflow. Proposed merges and moves require content review before execution.
