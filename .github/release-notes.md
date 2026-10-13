<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

This release includes 124 change groups through `78a0c25`.

## Breaking changes

- add help and error contracts ([#1690](https://github.com/Observal/Observal/pull/1690))
- make Observal agent-ready ([#1691](https://github.com/Observal/Observal/pull/1691))

## Features

- add teamspace membership and handle reservation ([#1619](https://github.com/Observal/Observal/pull/1619))
- add team publishing and team-private visibility ([#1640](https://github.com/Observal/Observal/pull/1640))
- add success criteria fields to agent versions ([#1623](https://github.com/Observal/Observal/pull/1623))
- add curated release pipeline ([#1642](https://github.com/Observal/Observal/pull/1642))
- add Goose as a first-class harness ([#1667](https://github.com/Observal/Observal/pull/1667))
- continuous fuzzing via OSS-Fuzz (Scorecard Fuzzing 0/10 → 10/10) ([#1668](https://github.com/Observal/Observal/pull/1668))
- add the cross-product actionable inbox ([#1669](https://github.com/Observal/Observal/pull/1669))
- meet OpenSSF security criteria ([#1674](https://github.com/Observal/Observal/pull/1674))
- add shareable registry and team links ([#1673](https://github.com/Observal/Observal/pull/1673))
- revamp registry home ([#1692](https://github.com/Observal/Observal/pull/1692))
- add configurable usage reporting ([#1701](https://github.com/Observal/Observal/pull/1701))
- establish shared design-language foundations for UI revamp ([#1731](https://github.com/Observal/Observal/pull/1731))
- allow anonymous public registry access ([#1725](https://github.com/Observal/Observal/pull/1725))
- revamp review and work views ([#1740](https://github.com/Observal/Observal/pull/1740))
- revamp administration views ([#1742](https://github.com/Observal/Observal/pull/1742))
- batch ClickHouse telemetry exports ([#1745](https://github.com/Observal/Observal/pull/1745))
- ARD search, observal discover, and usage attribution ([#1744](https://github.com/Observal/Observal/pull/1744))
- pin component versions and lock agent installs ([#1762](https://github.com/Observal/Observal/pull/1762))
- auto-install the Pi telemetry extension with latest version ([#1722](https://github.com/Observal/Observal/pull/1722))
- delegate tasks to agents via A2A ([#1765](https://github.com/Observal/Observal/pull/1765))
- add admin-controlled recommended badges ([#1760](https://github.com/Observal/Observal/pull/1760))
- add scoped agent share links ([#1710](https://github.com/Observal/Observal/pull/1710))
- add OpenGraph and Twitter meta tags to index.html ([#1749](https://github.com/Observal/Observal/pull/1749))
- add governance and maintainers documentation ([#1718](https://github.com/Observal/Observal/pull/1718))
- add opt-in local harness inventory ([#1770](https://github.com/Observal/Observal/pull/1770))
- publish from maintained branches ([#1801](https://github.com/Observal/Observal/pull/1801))
- flags for non-interactive release notes ([#1805](https://github.com/Observal/Observal/pull/1805))
- support complete registry-direct skill folders ([#1781](https://github.com/Observal/Observal/pull/1781))
- sync MCP servers from GitHub and GitLab webhooks ([#1793](https://github.com/Observal/Observal/pull/1793))
- agent and component forking ([#1799](https://github.com/Observal/Observal/pull/1799))
- replace legacy approvals with PR-style reviews ([#1813](https://github.com/Observal/Observal/pull/1813))
- deprecate the Leaderboard, off by default ([#1847](https://github.com/Observal/Observal/pull/1847))
- open main dev-version bump PR after cut ([#1849](https://github.com/Observal/Observal/pull/1849))
- merge agents and components into Agentic Resources ([#1842](https://github.com/Observal/Observal/pull/1842))

## Fixes

- resolve release tags to SHAs to survive background tag pruning ([#1651](https://github.com/Observal/Observal/pull/1651))
- attestation verify identity and resume-safe publish jobs ([#1655](https://github.com/Observal/Observal/pull/1655))
- make pypi, npm, and helm jobs resume-safe ([#1656](https://github.com/Observal/Observal/pull/1656))
- remove duplicate title and contributors section from release notes ([#1658](https://github.com/Observal/Observal/pull/1658))
- only stamp alembic HEAD on a genuinely fresh database ([#1662](https://github.com/Observal/Observal/pull/1662))
- pre-create ~/.observal before Docker makes it root-owned ([#1664](https://github.com/Observal/Observal/pull/1664))
- red parallel test run, dropped log records, and stale AGENTS.md ([#1666](https://github.com/Observal/Observal/pull/1666))
- preserve intentional downgrades ([#1672](https://github.com/Observal/Observal/pull/1672))
- correct agent trace attribution ([#1671](https://github.com/Observal/Observal/pull/1671))
- support headless setup ([#1676](https://github.com/Observal/Observal/pull/1676))
- preserve debug symbols ([#1679](https://github.com/Observal/Observal/pull/1679))
- require explicit session identity ([#1675](https://github.com/Observal/Observal/pull/1675))
- validate current notes format ([#1681](https://github.com/Observal/Observal/pull/1681))
- update gitsign verification ([#1682](https://github.com/Observal/Observal/pull/1682))
- correct teamspace lifecycle access ([#1683](https://github.com/Observal/Observal/pull/1683))
- harden diagnostics and CodeQL ([#1684](https://github.com/Observal/Observal/pull/1684))
- repair the OSS-Fuzz build before submitting upstream to google ([#1689](https://github.com/Observal/Observal/pull/1689))
- avoid duplicate Observal skills in Pi ([#1663](https://github.com/Observal/Observal/pull/1663))
- keep UUIDs as text in SQLite tests ([#1704](https://github.com/Observal/Observal/pull/1704))
- use npm trusted publishing ([#1715](https://github.com/Observal/Observal/pull/1715))
- bundle harness model data ([#1716](https://github.com/Observal/Observal/pull/1716))
- bump gitpython to 3.1.59 ([#1724](https://github.com/Observal/Observal/pull/1724))
- remove mock fallbacks and polish UI ([#1735](https://github.com/Observal/Observal/pull/1735))
- import scoped and legacy artifacts ([#1746](https://github.com/Observal/Observal/pull/1746))
- eliminate vendor egress paths ([#1750](https://github.com/Observal/Observal/pull/1750))
- lead the observal skill with discovery triggers ([#1752](https://github.com/Observal/Observal/pull/1752))
- remove misleading UI patterns ([#1751](https://github.com/Observal/Observal/pull/1751))
- general ui fixes ([#1756](https://github.com/Observal/Observal/pull/1756))
- use ARD error envelope on 400s ([#1758](https://github.com/Observal/Observal/pull/1758))
- harden machine-readable workflows ([#1697](https://github.com/Observal/Observal/pull/1697))
- separate source and user metadata ([#1764](https://github.com/Observal/Observal/pull/1764))
- correct cache and list filters ([#1778](https://github.com/Observal/Observal/pull/1778))
- use newline framing for MCP stdio ([#1766](https://github.com/Observal/Observal/pull/1766))
- exclude cached tokens from input ([#1768](https://github.com/Observal/Observal/pull/1768))
- make pulled agents visible in Kiro IDE and deliver IDE session telemetry ([#1761](https://github.com/Observal/Observal/pull/1761))
- keep the SPDX hook inside the file header ([#1747](https://github.com/Observal/Observal/pull/1747))
- skip existing tables in migration 030 ([#1788](https://github.com/Observal/Observal/pull/1788))
- add Google and GitHub to CLI SSO login ([#1774](https://github.com/Observal/Observal/pull/1774))
- run pulled agent hooks with CLI python ([#1775](https://github.com/Observal/Observal/pull/1775))
- say when a pulled agent reports sessions ([#1776](https://github.com/Observal/Observal/pull/1776))
- scope ARD to joined teamspaces ([#1803](https://github.com/Observal/Observal/pull/1803))
- retry and parallelize GitHub PR lookups ([#1804](https://github.com/Observal/Observal/pull/1804))
- register remote MCP servers with Claude Code ([#1812](https://github.com/Observal/Observal/pull/1812))
- validate rules and prevent repeated firing ([#1839](https://github.com/Observal/Observal/pull/1839))
- keep authenticated cached responses private ([#1840](https://github.com/Observal/Observal/pull/1840))
- block private hosts in SKILL.md fetch ([#1833](https://github.com/Observal/Observal/pull/1833))
- make user-wide token revocation stick ([#1834](https://github.com/Observal/Observal/pull/1834))
- only let agent owners watch a target ([#1835](https://github.com/Observal/Observal/pull/1835))
- resolve agent names only when unambiguous ([#1836](https://github.com/Observal/Observal/pull/1836))
- show resolved unread items the badge counts ([#1850](https://github.com/Observal/Observal/pull/1850))
- guide prerelease CLI mismatches ([#1838](https://github.com/Observal/Observal/pull/1838))

## Documentation

- remove repetition between How It Works and feature sections ([#1657](https://github.com/Observal/Observal/pull/1657))
- add optional Discord field to PR template ([#1703](https://github.com/Observal/Observal/pull/1703))
- add Pi integration guide ([#1713](https://github.com/Observal/Observal/pull/1713))
- add two-command setup guide ([#1757](https://github.com/Observal/Observal/pull/1757))
- add Claude Code integration guide ([#1714](https://github.com/Observal/Observal/pull/1714))
- add contributor payments policy ([#1797](https://github.com/Observal/Observal/pull/1797))
- correct the live log viewer command in README ([#1808](https://github.com/Observal/Observal/pull/1808))
- refresh screenshots and captions ([#1848](https://github.com/Observal/Observal/pull/1848))
- correct stale Next.js references ([#1831](https://github.com/Observal/Observal/pull/1831))

## Maintenance

- remove legacy tenant scaffold ([#1641](https://github.com/Observal/Observal/pull/1641))
- Feat/registry recommendations ([#1638](https://github.com/Observal/Observal/pull/1638))
- feat(builder): recommend the Observal skill ([#1832](https://github.com/Observal/Observal/pull/1832))

## Verify this release

Verify checksums, artifact provenance, and the signed release tag using the [release verification guide](https://github.com/Observal/Observal/blob/main/docs/security/release-verification.md).

## Full comparison

[v1.10.7...v1.15.0](https://github.com/Observal/Observal/compare/v1.10.7...v1.15.0)
