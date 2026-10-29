<!-- SPDX-FileCopyrightText: 2026 Ryan Madhuwala <rawx18.dev@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Governance

This document explains who makes decisions in Agentic Resource Registry, how people take on more responsibility, and how we handle disagreements. The current Maintainers are listed in [MAINTAINERS.md](MAINTAINERS.md).

## Principles

- **Merit-based.** Responsibility follows demonstrated skill, sound judgment, and sustained contribution. Affiliation and tenure alone do not count.
- **Transparent.** Decisions happen in public issues and pull requests wherever possible.
- **Accountable.** Every important area of the project has someone responsible for it.

## Roles

### Contributor

Anyone who improves the project: code, docs, tests, bug reports, triage, examples, or design discussion. Contributors do not own any area and do not make project-wide decisions. They are expected to learn the codebase and follow the [contribution guidelines](CONTRIBUTING.md).

### Core Contributor

A Contributor who has shown sustained involvement and understands how the major parts of the project fit together. Core Contributors:

- work independently in one or more areas of the codebase;
- triage issues and review pull requests;
- spot regressions and technical risks;
- help other contributors find their way;
- escalate decisions that affect architecture or direction.

Core Contributors may be the point of contact for a component. This status does not by itself give ownership of a subsystem.

### Maintainer

A trusted person who owns and looks after a subsystem or other significant area. Maintainers:

- guide and review significant changes in their area and keep quality standards up;
- set technical direction, and flag architectural and operational risks;
- make sure important issues do not go unanswered;
- coordinate with the owners of related components;
- mentor contributors, improve docs and engineering practices, and help grow the community.

Maintainership is a position of trust. It is not granted for technical skill or repository tenure alone. It also requires reliability, good judgment, and collaborative behavior.

## Advancement

There is no fixed number of commits, pull requests, or months required. These numbers are useful evidence, but we weigh the quality, consistency, and significance of the work.

- **Contributor to Core Contributor:** sustained, meaningful contributions, effective participation in issues and reviews, and the ability to work independently in part of the codebase.
- **Core Contributor to Maintainer:** all of the above, plus subsystem-level ownership, sound decision-making, and a commitment to the project's health and growth.

The process is:

1. **Nomination.** Any Maintainer may nominate a Core Contributor or Contributor. Candidates may also ask a Maintainer to nominate them.
2. **Evidence.** The nominator opens a discussion that links the candidate's work: merged pull requests, reviews, triage, and mentoring.
3. **Approval.** A Maintainer promotion needs approval from a majority of active Maintainers, with no unresolved objection. A Core Contributor promotion needs approval from at least two Maintainers.
4. **Record.** The decision is recorded in the discussion. For new Maintainers, the approval is a pull request that updates [MAINTAINERS.md](MAINTAINERS.md).

## Decision making

- **Routine decisions** (implementation details within the project's architecture) are made by the Maintainer or Core Contributor responsible for the area.
- **Broader decisions** (architecture, compatibility, security, public APIs, or anything that spans subsystems) need open discussion with the relevant Maintainers and affected contributors before implementation.
- **Disagreements** are settled by technical discussion based on evidence, requirements, and maintainability.
- **When consensus cannot be reached,** the Maintainer for that area makes the final call and documents it. Decisions that affect the whole project are decided by the Maintainers together.

Maintainers decide on technical merit, security, reliability, and user impact, and not on personal, organizational, or commercial interests.

## Ownership

Ownership means responsibility, not exclusivity. A Maintainer is the primary owner of a subsystem, but anyone may investigate, propose changes, and review code there. Ownership exists so every important area has clear accountability and continuity. It must not become a barrier to contribution.

Maintainers should reduce single-person dependencies by documenting systems, sharing knowledge, and growing additional owners.

## Community and conduct

Everyone is expected to act professionally and respectfully. Technical disagreement is welcome when it is constructive. Personal attacks, harassment, discrimination, and intimidation are not acceptable. Governance authority must not be used to suppress legitimate disagreement or unfairly exclude contributors.

The [Code of Conduct](CODE_OF_CONDUCT.md), the contribution guidelines, and other project policies are part of this governance framework.

## Inactive and former Maintainers

Maintainers are expected to stay involved. A Maintainer may step down at any time by telling the other Maintainers.

If a Maintainer has had no meaningful activity (commits, reviews, issue responses, or discussion) for **six months**:

1. Another Maintainer opens an issue and contacts them directly.
2. If there is no reply within **two weeks**, a majority of the other active Maintainers may move them to Emeritus status.
3. Their areas are reassigned to active Maintainers, and [MAINTAINERS.md](MAINTAINERS.md) is updated in a pull request.

Emeritus Maintainers keep their thanks and are welcome to keep contributing, but they cannot approve changes or vote. If they return to sustained participation, a majority of active Maintainers can reinstate them without a full nomination.

Changing employer or organization does not by itself change anyone's role.

## Changing this document

Changes are proposed in a pull request and discussed openly. They should preserve merit-based advancement, transparent decisions, accountability, and long-term sustainability. As the project grows, we may add more roles, formal voting, or a technical steering group where they provide clear value.
