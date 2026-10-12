<!-- SPDX-FileCopyrightText: 2026 Ai-chan-0411 <aoikabu12@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 DoomsCoder <vedantkakade05@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

<pre>
 ██████╗ ██████╗ ███████╗███████╗██████╗ ██╗   ██╗ █████╗ ██╗
██╔═══██╗██╔══██╗██╔════╝██╔════╝██╔══██╗██║   ██║██╔══██╗██║
██║   ██║██████╔╝███████╗█████╗  ██████╔╝██║   ██║███████║██║
██║   ██║██╔══██╗╚════██║██╔══╝  ██╔══██╗╚██╗ ██╔╝██╔══██║██║
╚██████╔╝██████╔╝███████║███████╗██║  ██║ ╚████╔╝ ██║  ██║███████╗
 ╚═════╝ ╚═════╝ ╚══════╝╚══════╝╚═╝  ╚═╝  ╚═══╝  ╚═╝  ╚═╝╚══════╝
</pre>

**Observal is the control plane and system of record for internal coding agent resources**
  ---
  
  _Set up once, and let your coding agent use Observal through observal-cli._
  
  ---

<p>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue?style=flat-square" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.11+-3776ab?style=flat-square&logo=python&logoColor=white" alt="Python">
  <a href="https://pypi.org/project/observal-cli/"><img src="https://img.shields.io/pypi/v/observal-cli?style=flat-square&logo=pypi&logoColor=white&label=pypi" alt="PyPI version"></a>
  <a href="https://github.com/Observal/Observal/graphs/contributors"><img src="https://img.shields.io/github/contributors/Observal/Observal?style=flat-square&logo=github" alt="Contributors"></a>
  <a href="https://discord.gg/SFPjnTWddk"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fdiscord.com%2Fapi%2Fv10%2Finvites%2FSFPjnTWddk%3Fwith_counts%3Dtrue&query=%24.approximate_member_count&label=Discord&logo=discord&color=5865F2&style=flat-square" alt="Discord Server"></a>
  <a href="https://github.com/orgs/Observal/packages?repo_name=Observal"><img src="https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Haz3-jolt/b28aba6d0efebb0b430d43c8068feb91/raw/ghcr-pulls.json&style=flat-square" alt="GHCR pulls"></a>
  <a href="https://artifacthub.io/packages/search?repo=observal"><img src="https://img.shields.io/endpoint?url=https://artifacthub.io/badge/repository/observal" alt="Artifact Hub"></a>
  <a href="https://cla-assistant.io/Observal/Observal"><img src="https://cla-assistant.io/readme/badge/Observal/Observal" alt="CLA assistant" /></a>
  <a href="https://scorecard.dev/viewer/?uri=github.com/Observal/Observal"><img src="https://api.scorecard.dev/projects/github.com/Observal/Observal/badge" alt="OpenSSF Scorecard"></a>
  <a href="https://www.bestpractices.dev/projects/13472"><img src="https://www.bestpractices.dev/projects/13472/badge" alt="OpenSSF Best Practices"></a>
  <a href="https://codecov.io/gh/Observal/Observal"><img src="https://codecov.io/gh/Observal/Observal/graph/badge.svg?branch=main" alt="Codecov"></a>
</p>

> If you find Observal useful, please consider giving it a star. It helps others discover the project and keeps development going.

---

## What is Observal and what does it solve?

Observal is the control plane and system of record for internal internal coding agent resources. Every tech-forward organization today creates internal Skills, Agents files, MCP clients. Though the creation of these coding agent resources has been prolific, peer adoption and usage is sparse. Coding agent users today end up creating their own version of such resouces without reusing existing packages.

The cause is largely due to two problems:

1. **Lack of a discoverability layer**

   Organizations store their coding agent resources in siloed git repositories with little to no documentation. Users are not able to locate existing coding agent resources and this results in multiple developers creating the same/similar resources again.

2. **Missing feedback loop**

   Any software where usage patterns are not understood and the principle of user-centric development is violated tends to fade out. Such is the problem with development of MCP clients, Skills and Agent files. Authors publish and maintain these resources with little visibility into how they're actually used. Additionally, Coding agent failures don't trigger static error codes: they hallucinate or provide subtly incorrect answers. This leaves users clueless about what went wrong compounding the feedback problem.

Observal solves this by providing a centralized discovery layer for coding agent resources alongside useful insights into usage patterns. It turns silent failures into actionable feedback, ensuring internal resources are continuously optimized for the people using them.

Observal supports Claude Code, Cursor, Kiro, Pi, Copilot, Codex, OpenCode, and other tools.

### Why teams use Observal

- **Centralize resources into one platform:** Store Skills, MCP clients, hooks, prompts, agent files and sandboxes in one registry.
- **Run a governed registry:** Review submissions, approve internal agents, inspect version diffs, and give developers one trusted place to install from.
- **Render across multiple Coding IDE/CLI:** Generate the correct config for each supported harness instead of maintaining separate setup instructions for every harness.
- **Let agents use each other:** Every approved resource is discoverable over the open [ARD](https://github.com/ards-project/ard-spec) standard, and a running agent can hand part of its task to another approved agent, whether it lives in the registry or is a remote [A2A](https://a2a-protocol.org) service.
- **Learn what works:** Use real adoption and session data to find which agents, tools, prompts, and workflows are helping teams.
- **Replay sessions when needed:** Use traces as evidence for debugging, review, audits, and deeper analysis.

---

## Supported harnesses

[![Claude Code](https://img.shields.io/badge/Claude%20Code-D97757?style=flat-square&logo=anthropic&logoColor=white)](https://github.com/anthropics/claude-code)
[![Codex](https://img.shields.io/badge/Codex-412991?style=flat-square&logo=openai&logoColor=white)](https://github.com/openai/codex)
[![Cursor](https://img.shields.io/badge/Cursor-000000?style=flat-square&logo=cursor&logoColor=white)](https://cursor.com)
[![Copilot](https://img.shields.io/badge/GitHub%20Copilot-000000?style=flat-square&logo=githubcopilot&logoColor=white)](https://github.com/features/copilot)
![Kiro](https://img.shields.io/badge/Kiro-5A3FFF?style=flat-square)
![Pi](https://img.shields.io/badge/Pi-00897B?style=flat-square)
![OpenCode](https://img.shields.io/badge/OpenCode-333333?style=flat-square)
![Antigravity](https://img.shields.io/badge/Antigravity%20CLI-4285F4?style=flat-square)
![Goose](https://img.shields.io/badge/Goose-6E56CF?style=flat-square)

One command to install any agent into any supported harness. The config files are generated per-harness automatically.

---
### Where does observal fit in your modern enterprise infrastructure?

<img width="1920" height="1080" alt="Observal (3)" src="https://github.com/user-attachments/assets/008f8a68-1269-46de-a9cf-ddfad37b0caa" />


---
## Quick Start

Observal has two parts: a **server** (API + web UI + databases) you self-host, and a **CLI** you install on each developer machine.

### 1. Deploy the server

**One-line install** (requires Docker Engine ≥ 24.0 with Compose v2):

```bash
curl -fsSL https://raw.githubusercontent.com/Observal/Observal/main/install-server.sh | bash
```

This downloads a Docker Compose package, generates operator-owned secret files with restricted container-group access, binds published ports to loopback by default, pulls container images from GHCR, and starts the stack. With a terminal it runs guided setup; without a terminal the same command applies safe defaults automatically.

Deployment docs are linked directly from this README:

- [Setup guide](SETUP.md): fastest path from zero to a working stack
- [Self-hosting overview](docs/self-hosting/README.md): deployment models and operator docs
- [Production deployment](docs/self-hosting/production-deploy.md): hardened production topology
- [Databases](docs/self-hosting/databases.md): Postgres, ClickHouse, migrations, retention
- [Upgrades](docs/self-hosting/upgrades.md): safe upgrade and rollback flow
- [Release verification](docs/security/release-verification.md): verify checksums, provenance, and signed tags
- [Backup and restore](docs/self-hosting/backup-and-restore.md): backup plan before upgrades

**From source** (for contributors):

```bash
git clone https://github.com/Observal/Observal.git && cd Observal
cp .env.example .env
make up
```

### 2. Install the CLI

**Standalone binary** (no Python required):

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/Observal/Observal/main/install.sh | bash
```

```powershell
# Windows (PowerShell)
irm https://raw.githubusercontent.com/Observal/Observal/main/install.ps1 | iex
```

Connecting to an existing server? See the [Setup guide](docs/getting-started/onboarding.md).

**Python** (3.11+):

```bash
uv tool install observal-cli
# or: pipx install observal-cli
```

### 3. Connect your harness

```bash
observal auth login
observal doctor --patch
```

This authenticates with your server, detects your harness, installs telemetry hooks, starts capturing sessions automatically, and prepares it for agent installs and registry commands.

Once logged in, run `/observal` inside your harness and it takes the wheel. Pull agents, submit resources, browse the registry, run diagnostics:

```
/observal pull security-auditor
/observal scan
/observal doctor
```

Or just tell your agent what you want and it figures out the right commands.

---

## How Observal works

### The registry is the distribution layer

The registry is where agents live. Admins review submissions, version diffs keep changes auditable, and one command installs an agent into any supported harness.

### Insights close the loop

Real usage data flows back as reports: what's helping, what's getting in the way, and where to improve. Session traces provide the underlying evidence for debugging and auditing.

---

## Coding agent resource registry

**Browse, search, and install resources with harness compatibility badges:**

![Agent registry with grid view](docs/img/registry.png)

**Build agent files visually with live config preview for every harness:**

![Agent Builder with preview panel](docs/img/builder.png)

**Supported resources: Agent files, MCP clients, Skills, Hooks, Prompts, Sandboxes:**

![Component registry showing MCP servers](docs/img/component_registry.png)

### Fork an approved release

Fork an agent or component into an independent draft in your personal namespace or an eligible teamspace. The source stays unchanged; agent forks retain pinned component references rather than copying components. Reviewers can compare the draft with its approved upstream using **Diff vs upstream**. Public approved direct forks appear on the source's **Forks** tab; private forks and unpublished drafts do not. [Fork and customize guide](docs/use-cases/fork-and-customize.md).

| Registry fork API | Action |
| --- | --- |
| `POST /api/v1/agents/{id}/fork` | Create an agent draft from an approved release |
| `POST /api/v1/{type}/{id}/fork` | Create a component draft (`type`: `mcps`, `skills`, `hooks`, `prompts`, `sandboxes`) |
| `GET /api/v1/agents/{id}/forks`, `GET /api/v1/{type}/{id}/forks` | List public approved direct forks |
| `GET /api/v1/agents/{id}/fork-diff?version=…`, `GET /api/v1/{type}/{id}/fork-diff?version=…` | Compare a visible fork version to its approved, accessible base |

---

## Discovery and Delegation (ARD + A2A)

**ARD: one search across everything approved.** Observal implements [Agentic Resource Discovery](https://github.com/ards-project/ard-spec) (v0.91). Every approved agent, MCP server, skill, hook, prompt and sandbox is published as an ARD entry with a permanent `urn:air:` identifier, a public manifest at `/.well-known/ard.json`, and the spec's search API at `/api/v1/ard/search`. Results are ranked by relevance only; approval, visibility, harness support and whether the resource can be used right now are separate fields. The same visibility rules as the registry apply, so a team-private agent is only found by that team.

**A2A: agents hand tasks to agents.** Observal speaks the [Agent2Agent protocol](https://a2a-protocol.org) (v1.0, with v0.3 compatibility). Every delegation is an A2A Task, whoever runs it:

- **Registry agents** run headless in a harness that supports it (Claude Code, Kiro, Cursor, Codex, OpenCode, Copilot CLI, Antigravity, Pi) inside a throwaway copy of your repository. Their answer comes back as a result, and any file changes come back as a patch that is never applied for you.
- **Remote A2A agents** (a service another team runs, built with any framework) are registered by their Agent Card URL, reviewed like any submission, and then called directly with the card the reviewer approved. Observal never proxies the traffic or stores their credentials.

```mermaid
flowchart LR
    A["Agent in your harness"] -- "find_agents" --> R["Observal registry<br/>(ARD search)"]
    A -- "delegate" --> T{"A2A Task"}
    T -- "registry agent" --> H["Headless harness<br/>in a throwaway copy"]
    T -- "remote agent" --> S["A2A service<br/>(approved Agent Card)"]
    H -- "answer + patch" --> A
    S -- "artifacts" --> A
```

Every agent you pull gets an `observal-agents` MCP server with four tools, `find_agents`, `delegate`, `get_task` and `cancel_task`, so it can do this without being told how. Delegation is limited to approved agents, stops after two levels, lets a delegated agent start at most three tasks of its own, and refuses loops. Each delegation is recorded against the calling session, so traces show which agents a session relied on.

**Discover shows registry agents that can take a task and remote A2A agents side by side:**

![Discover page showing a delegable registry agent and a remote A2A agent](docs/img/discover-a2a.png)

**Delegate from any terminal or script.** Here a remote A2A agent answers directly; a registry agent also hands back its file changes as a patch for you to review:

![observal delegate find and run returning a remote A2A agent's answer](docs/img/delegate-cli.svg)

```bash
observal delegate find "review this branch for auth bugs"
observal delegate run acme/security-reviewer "Review src/auth on this branch for token leaks"
git apply ~/.observal/delegations/<task-id>/changes.patch     # only if you agree with it

observal registry a2a submit https://agents.acme.com --visibility team --team platform
observal discover inspect urn:air:agents.acme.com:a2a:incident-triage     # shows the card's Digest
observal registry a2a review urn:air:agents.acme.com:a2a:incident-triage --approve --digest sha256:...
```

Design decisions: [ADR 0001 (ARD)](docs/adr/0001-agentic-resource-discovery.md), [ADR 0002 (A2A delegation)](docs/adr/0002-a2a-delegation.md). API: [Discovery endpoints](docs/reference/api-endpoints.md#discovery-ard).

---

## Coding Agent Insights

**AI-powered insight reports** analyze usage patterns across all sessions, what's working, what's hindering, and quick wins. Powered by [LiteLLM](https://docs.litellm.ai/docs/providers), works with any provider (Anthropic, OpenAI, Bedrock, Gemini, Azure, Ollama).

![Insight report with What's Working, What's Hindering, Quick Wins](docs/img/insights.png)

See [Insights LLM Setup](docs/insights-setup.md) for configuration.

---

## Session Replay

**Full session overview with token counts, models, tools, and turn-by-turn timeline:**

![Session detail showing tokens, tools, models, and turns](docs/img/ses1.png)

**Every turn captured: user prompt, tool calls, thinking block, assistant response:**

![Turn expanded showing user prompt, thinking, and response](docs/img/complete_capture_thinking_response.png)

**Drill into any span to see exact tool inputs and outputs:**

![Span detail showing bash command input and full output](docs/img/span.png)

---

## Review and Governance

**Admin review queue with full prompt inspection and approve/reject:**

![Review queue with agent detail](docs/img/review.png)

**Side-by-side version diffs before approving a new release:**

![Side-by-side diff of v1.0.0 vs v2.0.0](docs/img/review-diff.png)

---

## Open-source features

Audit logs, SAML SSO, SCIM provisioning, and the executive dashboard are included in the Apache-2.0 distribution.

**Audit log with parameterized search:**

![Audit log with PHI sensitivity badges and chain hashes](docs/img/audit_logging.png)

---

## Documentation

Full docs at **[docs.observal.io](https://docs.observal.io/)**.

Start here for deployment and operations:

| Need | Link |
|------|------|
| Fast local or source setup | [SETUP.md](SETUP.md) |
| Self-hosting overview | [docs/self-hosting/README.md](docs/self-hosting/README.md) |
| Production deployment | [docs/self-hosting/production-deploy.md](docs/self-hosting/production-deploy.md) |
| Single-node deployment | [docs/self-hosting/single-node-deploy.md](docs/self-hosting/single-node-deploy.md) |
| Docker Compose setup | [docs/self-hosting/docker-compose.md](docs/self-hosting/docker-compose.md) |
| Databases and migrations | [docs/self-hosting/databases.md](docs/self-hosting/databases.md) |
| Upgrades | [docs/self-hosting/upgrades.md](docs/self-hosting/upgrades.md) |
| Backup and restore | [docs/self-hosting/backup-and-restore.md](docs/self-hosting/backup-and-restore.md) |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Vite 8, React 19, TanStack Router, Tailwind CSS 4, shadcn/ui |
| Backend | Python 3.11+, FastAPI, Strawberry GraphQL |
| Databases | PostgreSQL 16 (registry), ClickHouse (telemetry) |
| Queue | Redis + arq |
| CLI | Python, Typer, Rich |
| Telemetry | Session hooks, local transcript reconciliation, push-based ingest |
| Deployment | Docker Compose (10 services), Kubernetes (Helm) |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version:

1. Fork and clone
2. `make hooks` to install pre-commit hooks
3. Create a feature branch
4. Run `make lint` and `make test`
5. Open a PR

See [AGENTS.md](AGENTS.md) for internal codebase context.

## Community

[GitHub Discussions](https://github.com/Observal/Observal/discussions) for questions and ideas. [Discord](https://discord.observal.io) for chat. Open Issues for confirmed bugs.

## Reporting Issues

```bash
observal doctor support bundle
```

Produces a redacted diagnostic archive. Review before sharing: `observal doctor support inspect observal-support-*.tar.gz`

For live debugging, Observal uses loguru-based dev logging (internally called "optic"). Stream logs with:

```bash
observal ops logs
```

Logs are written to `~/.observal/logs/dev.log` and include structured context for every request, background job, and telemetry event.

## Security

Report vulnerabilities via [GitHub Private Vulnerability Reporting](https://github.com/Observal/Observal/security/advisories) or email contact@observal.io. Do not open a public issue. See [SECURITY.md](SECURITY.md).

## License

Observal is licensed under the Apache License 2.0. See [LICENSE](LICENSE).
