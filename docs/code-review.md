---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Code Review Standard"
---

Code review protects Observal users, contributors, and maintainers. Its purpose is not merely to make a pull request pass CI. Reviewers are responsible for deciding whether a change is correct, secure, understandable, appropriately tested, and safe to maintain.

This standard applies to every pull request merged into a protected branch. The depth of review should match the risk of the change, but the approval and merge requirements apply even to small changes.

## Required reviewers

Every pull request requires:

* Approval from at least two eligible reviewers. An eligible reviewer is a project collaborator whose approval GitHub counts toward the protected branch.
* Approval from a code owner when the changed paths have an applicable entry in `CODEOWNERS`. A code-owner approval may count as one of the two required approvals.
* Additional review from someone qualified in any area that the assigned reviewers cannot confidently assess, such as authentication, authorization, cryptography, privacy, database migrations, release infrastructure, accessibility, or concurrency.

Anyone may comment and provide useful review feedback. Reviewers who are not eligible approvers should state that their review is advisory. A reviewer covering only part of a pull request must identify the files or concerns they reviewed and must not imply approval of the remainder.

Author self-approval is prohibited. An approval from a co-author or anyone who contributed commits to the pull request does not count toward the two independent approvals.

## Reviewer procedure

### 1. Understand the change

Before reviewing individual lines:

1. Read the pull request description, linked issue, prior design discussion, and testing evidence.
2. Confirm that the proposed behavior is wanted and that the pull request addresses the stated problem.
3. Check that the change is focused. Unrelated refactors, formatting, generated files, or dependency updates should be separated unless they are necessary for the change.
4. Identify high-risk areas and request a qualified reviewer early when needed.

If the purpose, expected behavior, or risk is unclear, ask for clarification before approving.

### 2. Review the complete change

Review every human-written line in the diff and enough surrounding code to understand its effect. Do not rely only on the changed lines. Trace callers, data flow, failure paths, configuration, migrations, and user-visible behavior where relevant.

Generated files may be sampled when their source and generation command are clear. Lockfiles still require review for unexpected packages, sources, versions, and integrity data.

Automated checks support human review but do not replace it. A green pipeline cannot detect every authorization error, privacy leak, race, incompatible interface, or incorrect product decision.

### 3. Validate the evidence

Confirm that the pull request explains how the change was tested. Run focused tests or reproduce the behavior when the risk, novelty, or user impact warrants independent validation. Ask for screenshots, recordings, logs, benchmarks, migration output, or a demonstration when code inspection alone is insufficient.

A reviewer must not approve code they do not understand. Ask questions or bring in another reviewer instead.

### 4. Give actionable feedback

Use **Request changes** for blocking findings. Explain the risk and the condition that must be satisfied. Mark optional improvements clearly as non-blocking or as a nit.

Prefer comments about correctness, risk, maintainability, and documented project conventions over personal style preferences. Suggest a follow-up issue only when the current change is safe and complete without the follow-up.

The author must acknowledge every blocking thread. A thread may be resolved when the change is made or when the author and reviewer agree, with the reasoning recorded in the pull request.

### 5. Review the latest revision

Review the final diff, not only the first version submitted. After a material push, prior approvals are stale. All required approvers must review the delta and approve or explicitly reaffirm approval on the latest material revision.

A push is material when it changes behavior, interfaces, security or privacy properties, data handling, dependencies, migrations, build or release logic, test strategy, or the agreed scope. Typographical and other clearly non-behavioral corrections are not normally material, but a reviewer may request another review whenever the impact is uncertain.

## What every review must cover

### Correctness and design

Confirm that:

* The implementation satisfies the documented requirements and preserves relevant invariants.
* Error handling is explicit and does not hide failed operations, partial writes, or data loss.
* Edge cases, empty and malformed inputs, retries, concurrency, idempotency, and resource cleanup are handled where relevant.
* The design follows existing project architecture and uses an existing helper or platform feature where appropriate.
* The change does not introduce unnecessary abstraction, configuration, dependencies, dead code, or speculative flexibility.
* Performance, availability, and operational costs are reasonable for expected production use.

### Security and privacy

Treat all external input and cross-tenant data as untrusted. Confirm that:

* Authentication and authorization are enforced at the server boundary, not only in the user interface.
* Access checks cover object ownership, team boundaries, administrative roles, and indirect lookup paths.
* Inputs are validated and are safe from injection, path traversal, request forgery, unsafe redirects, and insecure deserialization.
* Secrets, tokens, credentials, personal data, and telemetry are not exposed through logs, errors, URLs, browser storage, analytics, or generated artifacts.
* Data collection, retention, export, deletion, and redaction remain consistent with the documented privacy model.
* Cryptography and security controls use established project mechanisms and secure defaults.
* New dependencies, actions, container images, and downloaded tools are necessary, integrity-pinned where supported, and from trustworthy sources.

Do not discuss an undisclosed vulnerability in a public pull request. Follow the private process in the [Security Policy](https://github.com/Observal/Observal/blob/main/SECURITY.md).

### Tests and CI

Confirm that:

* New behavior has tests at the smallest effective level, with integration or end-to-end coverage where boundaries or user workflows require it.
* A bug fix includes a regression test that fails without the fix whenever practical.
* Tests assert observable behavior, exercise failure paths, and would detect a real regression.
* Tests are deterministic, isolated from real user data and external services, and no more complex than necessary.
* Required CI checks pass for the final material revision. Flaky, skipped, cancelled, or unrelated failures must be investigated and documented, not silently ignored.

### Compatibility and data safety

Confirm that:

* Public APIs, CLI commands, configuration, stored data, deployment files, supported platforms, and harness integrations remain compatible unless an intentional change is approved and documented.
* Breaking changes include a clear rationale, user migration path, release note, and coordinated updates to every in-repository caller.
* Database migrations use the repository migration tooling, have been inspected, preserve existing data, and have a safe upgrade and recovery plan.
* Changes that cross versions, workers, caches, queues, or rolling deployments account for mixed-version operation where applicable.

### Documentation and user experience

Confirm that:

* User-visible behavior, APIs, commands, configuration, setup, and operational procedures are documented in the same pull request.
* CLI syntax changes are reflected in bundled skill documentation.
* User-facing changes include the required changelog entry and accurate screenshots or other evidence when applicable.
* Interfaces remain accessible, understandable, responsive, and consistent with established design patterns.
* Comments explain non-obvious intent and trade-offs rather than restating the code.

### Licensing and provenance

Confirm that:

* New files have the correct SPDX copyright and license headers.
* The contributor has satisfied the CLA and followed the [AI Policy](https://github.com/Observal/Observal/blob/main/AI_POLICY.md).
* Copied or adapted material is compatible with Apache-2.0, properly attributed, and accompanied by any required notices.
* New dependencies, assets, generated code, and vendored files have acceptable licenses and documented origins.

### Scope and maintainability

Confirm that:

* The pull request contains one coherent change and no unrelated cleanup.
* The implementation is readable by maintainers who did not author it.
* Temporary code, debugging output, compatibility workarounds, and obsolete paths are removed unless explicitly justified.
* The pull request description, commit history, and documentation accurately describe the final implementation.

## Approval and merge requirements

A pull request is acceptable for merge only when all of the following are true:

* Two eligible reviewers have approved the latest material revision.
* A code owner has approved where `CODEOWNERS` applies.
* No reviewer has an outstanding **Request changes** review.
* Every review thread is resolved with its outcome recorded.
* All required CI checks pass on the mergeable revision.
* Required tests, documentation, changelog entries, screenshots, migration evidence, SPDX headers, and license information are present.
* The CLA and other repository policy checks pass.
* The pull request is focused, understandable, and ready to operate without relying on an unspecified follow-up.

The person merging the pull request performs a final gate check. Merge permission is not permission to bypass this standard.

## When to request changes or reject a pull request

A reviewer must withhold approval and request changes when any blocking issue remains, including:

* Incorrect behavior, an unhandled failure mode, data-loss risk, or an unresolved security or privacy concern.
* Missing or ineffective tests for behavior that can reasonably be tested.
* A breaking or operationally unsafe change without an approved migration and recovery plan.
* Missing required documentation, licensing information, attribution, or policy compliance.
* Scope or complexity that prevents a reliable review. The reviewer may require the pull request to be split or redesigned.
* A material revision that has not been reviewed by the required approvers.

A pull request should be rejected or closed when it:

* Conflicts with an established project decision or does not solve an accepted project need.
* Duplicates existing work without a reason to replace it.
* Introduces malicious, deceptive, obfuscated, or unverifiable behavior.
* Contains code or assets that the contributor cannot legally license to the project.
* Violates the Code of Conduct, CLA, AI Policy, responsible disclosure process, or other repository policy.
* Remains abandoned after maintainers have clearly requested the information or changes needed to continue.

Reviewers should explain rejection clearly and respectfully. Rejection is a decision about the proposed change, not the contributor.

## Review conduct and disagreements

Follow the [Code of Conduct](https://github.com/Observal/Observal/blob/main/CODE_OF_CONDUCT.md). Be specific, constructive, and proportionate to risk. Authors should not interpret questions as approval, and reviewers should not use approval as leverage for unrelated preferences.

Resolve disagreements using documented requirements, reproducible evidence, and project architecture. If consensus cannot be reached, ask another domain expert or maintainer to decide and record the decision in the pull request. Do not leave a pull request indefinitely blocked by an unstated concern.

Urgent and embargoed security changes may be reviewed privately by a restricted group, but independent review, testing, and approval are not waived.

## Further reading

This standard was informed by established review practices from:

* [Google Engineering Practices: What to look for in a code review](https://google.github.io/eng-practices/review/reviewer/looking-for.html)
* [GitLab Code Review Guidelines](https://docs.gitlab.com/development/code_review/)
* [LLVM Code-Review Policy and Practices](https://llvm.org/docs/CodeReview.html)
* [Django Contribution Checklist](https://docs.djangoproject.com/en/dev/internals/contributing/writing-code/submitting-patches/#contribution-checklist)
* [OpenSSF Source Code Management Platform Configuration Best Practices](https://best.openssf.org/SCM-BestPractices/)
