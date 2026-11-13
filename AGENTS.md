<!-- SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# AGENTS.md

Internal context for contributors and AI coding agents. Use `README.md` for the public API reference, `SETUP.md` for environment setup, and `docs/adding-a-harness.md` for harness integration.

## What Agentic Resource Registry is

Agentic Resource Registry is a self-hosted, Apache 2.0 registry for sharing coding agent resources. Users interact with it three ways:

1. **CLI** (`observal`): pull agents, scan harnesses, submit components, manage the server
2. **Web UI** (`web/`): browse the registry, view traces, manage users, admin dashboard
3. **Agentic Resource Registry skill** (bundled, auto-installed on login): lets the LLM inside any harness drive Agentic Resource Registry commands directly (e.g. "create an agent that uses the github MCP")

Agents are the primary entity. Each agent bundles 5 component types: MCP servers, skills, hooks, prompts, and sandboxes. When a user runs `observal agent pull <agent>`, the platform resolves all components and writes harness-specific config files.

## harness capability support

Ten harnesses are registered in `packages/observal-shared/observal_shared/harness_registry.py`. Each has CLI and server adapters and resolves a session parser. The table lists registered capabilities, delegation facts and component-evidence coverage; native activation and tool-version requirements are documented in the integration guides. Antigravity is on the compatibility hold described in `docs/integrations/antigravity.md`; its hook cleanup currently uses the base no-op. Verify registry declarations and adapter implementations when changing support.

| Harness | Capabilities | Session parser | Hook install | Headless delegation | Component insights |
|---|---|---|---|---|---|
| Claude Code | hooks, mcp_servers, skills | `claude-code` | `harness_specs/` | yes | yes (MCP, skill, hook) |
| Kiro | hooks, mcp_servers, skills | `kiro` | `harness_specs/` | yes | no |
| Cursor | hooks, mcp_servers | `cursor` | adapter (`~/.cursor/hooks.json`) | yes | no |
| Pi | hooks, mcp_servers, skills | `pi` | `packages/pi-extension` | yes | yes (MCP, skill, hook) |
| Codex CLI | hooks, mcp_servers, skills | `codex` | `harness_specs/` | yes | no |
| Copilot | hooks, mcp_servers, skills, prompts | `copilot-cli` (shared) | `harness_specs/` | no (no CLI entry point) | no |
| Copilot CLI | hooks, mcp_servers, skills, prompts | `copilot-cli` | `harness_specs/` | yes | no |
| OpenCode | hooks, mcp_servers, skills | `opencode` | `harness_specs/` | yes | no |
| Antigravity | hooks, mcp_servers, skills | `antigravity` | `harness_specs/` | yes | no |
| Goose | hooks, mcp_servers, skills | `goose` | `harness_specs/` | no (agent profiles are not recipes) | no |

Known gaps, not tiers:
- **Component insights** count MCP invocations, skill evidence and hook evidence only where an extractor is proven for the harness (`invocation_extractor`, `skill_evidence_extractor`, `hook_evidence_extractor` in the registry). Today that is Claude Code and Pi. Everywhere else coverage reports unsupported, never zero use. See "Component Insights" in `docs/adding-a-harness.md`.
- **Delegation runs** need the `headless_run` fact. Copilot (VS Code) and Goose do not have it, so they cannot run a delegated agent as a child process.
- **Cursor has no `skills` capability.**
- **Legacy scan coverage is scope-specific.** OpenCode scans MCPs, skills, hooks and agents in home and project scope. Claude Code and Kiro home scans cover those four categories; their project scans cover MCPs. Antigravity home scans cover all four, and project scans cover MCPs and skills. Copilot CLI home scans cover MCPs, skills and hooks, and project scans also cover agents. Goose home scans cover all four; project scans cover skills, hooks and agents (MCP config is user-level). Pi scans MCPs and skills in both scopes; Codex, Copilot and Cursor legacy scans cover MCPs. Bounded `discover_home()` / `discover_project()` power the separate read-only `scan --inventory` path.
- **Playwright coverage** is harness-specific only for Kiro (9 specs). The other harnesses are covered by pytest in `tests/`.
- Cursor and Pi do not have a `harness_specs/` module: Cursor writes its hooks from the adapter, and Pi runs registry hooks and telemetry through the bundled extension (see `docs/integrations/pi.md`).

See `docs/adding-a-harness.md` for the complete guide to adding or promoting a harness.

## Governance and collaboration

Beyond the registry itself, these systems exist. Know where they live before touching them.

- **Review gate.** Component and agent versions go through reviews: requests, threads, decisions, automated checks, and per-team policy. Server code in `services/review/` and `api/routes/reviews/`, CLI in `cmd_review.py`, web in `web/src/pages/review/`.
- **Teamspaces.** Teams own resources, have invites, visibility and membership requests (`api/routes/teams.py`, `services/teamspace.py`, `cmd_team.py`).
- **Inbox.** Per-user feed of reviews, decisions and update notices (`services/inbox/`, `cmd_inbox.py`).
- **Agent sharing.** Version-pinned share links for installed agents (`api/routes/agent_shares.py`, `cmd_share.py`).
- **Discovery.** ARD search lets an assistant find approved resources mid-task (`services/discovery/`, `observal discover`, ADR 0001). The web Discover page was removed; ARD search lives in the Agents view.
- **Component activity evidence.** Session parsers emit skill and hook evidence that `services/component_activity/` projects onto registry components for insights.
- **Enterprise auth.** SAML SSO, SCIM provisioning, and an audit log (`sso_saml.py`, `scim.py`, `audit_log.py`).

## Architecture at a glance

```
observal_cli/          Python CLI (Typer)
  harness/             CLI-side harness adapters (protocol.py, base.py, 10 adapters)
  harness_specs/       Hook specs (8: claude_code, kiro, codex, copilot, copilot_cli, opencode, antigravity, goose)
  hooks/               Session push hooks (generic, Antigravity, Copilot CLI, Copilot VS Code)
  sessions/            Local session readers used by push hooks and `observal reconcile`
  discovery/           Read-only local inventory collector behind `observal scan --inventory`
  skills/              Bundled skills installed on login (observal, observal-admin, observal-advanced,
                       observal-agents, observal-ops, observal-registry)
  delegation/          Agent-to-agent delegation: A2A tasks, workspace isolation, headless runs,
                       A2A client, observal-agents MCP server (docs/adr/0002-a2a-delegation.md)

observal-server/       FastAPI server
  api/routes/          REST endpoints (agent/, admin/ are sub-packages)
  api/middleware/      Audit, request-id, content-type, trusted-proxy, SSO config guard
  models/              SQLAlchemy models (PostgreSQL)
  schemas/             Pydantic request/response schemas
  services/            Business logic
    clickhouse/        ClickHouse subpackage (client, schema, insert, query)
    harness/           Server-side harness adapters (config generation)
    session_parsers/   Per-harness JSONL parsers (9 parsers covering all 10 harnesses) plus skill and hook evidence
    audit/             Compliance audit system (loguru-based)
    config/            Config generation helpers (skill_builder)
    discovery/         ARD search, projection and visibility (docs/adr/0001-agentic-resource-discovery.md)
    review/            Review gate: requests, decisions, checks, policy, notifications
    inbox/             Inbox events, recipients and delivery
    component_activity/ Projects session evidence onto skills, hooks and MCPs
    layer_components/  Layer snapshot extraction and resolution
    insights/          Insight engine (report generation, facets, sections, HTML export, component reports)
    shared/            Cross-service utilities
  jobs/                Background jobs (catalog, activity, maintenance, mcp_sync, migration, review_backfill,
                       review_checks, usage_ping)


web/                   Vite 8 SPA / React 19 / TanStack Router (see web/AGENTS.md)
packages/observal-shared/ Shared harness registry and types used by CLI and server
packages/pi-extension/ Pi telemetry and hook extension (bundled into the CLI)
docker/                Docker Compose stack (8 core services, optional Prometheus and Grafana)
infra/                 Helm chart and Terraform
tools/                 Release tooling (release.py, check_release_policy.py)
fuzz/                  Atheris fuzz targets + OSS-Fuzz project config mirror
docs/                  User and contributor docs; docs/adr/ holds architecture decision records
tests/                 pytest (430 files)
tests/e2e/             Playwright (32 specs)
```

## How the modularisation works

The codebase follows a strict adapter pattern for harness-specific logic. This is the most important architectural decision:

**One adapter per harness, on both sides.** CLI adapters handle scanning and hook detection (`observal_cli/harness/<name>.py`). Server adapters handle config file generation (`observal-server/services/harness/<name>.py`). The shared harness registry (`packages/observal-shared/observal_shared/harness_registry.py`) defines paths, keys, features, and event maps for both sides.

**No if/elif chains for harness logic.** If you need harness-specific behavior, it goes in the adapter. The orchestrators (`cmd_scan.py`, `agent_builder.py`, `cmd_doctor.py`) call adapters via the registry, never with conditionals.

**Capability gating.** `METHOD_FEATURE_MAP` in `observal_cli/harness/protocol.py` maps hook generation/detection/spec methods to `hooks` and legacy scan methods to `mcp_servers`. `BaseAdapter` checks these mapped methods before invoking overrides. Other operations enforce their own capability or runtime-fact checks; inherited no-op methods are not evidence that an operation is implemented. The registry capability vocabulary is `hooks`, `mcp_servers`, `skills`, `prompts`.

**Headless runs are a verified fact.** The registry's `headless_run` runtime fact says a harness CLI can run one prompt non-interactively; the CLI adapter's `headless_command` builds the argv and `parse_headless_output` reads the answer. Delegation uses only harnesses with the fact set.

**Session parsers are separate from adapters.** They live in `services/session_parsers/` (server-side) and handle converting raw JSONL into normalized trace events. All ten harnesses resolve one of nine parsers; Copilot reuses the Copilot CLI parser.

### What a harness needs

A complete harness integration includes:
- A registry entry with correct paths, capabilities and runtime facts
- A CLI adapter with scope-aware scanning/inventory and the native hook installation and cleanup workflow. Coverage is listed above; implement and test each operation explicitly.
- A server adapter that generates config files
- A session parser resolved from the registry's `session_parser` key (enables `observal reconcile`)
- pytest coverage in `tests/`

Optional, added per harness once proven: a `harness_specs/` hook spec, component insight extractors, and the `headless_run` fact with a `headless_command`.

## Coding patterns we prefer

### Python (server + CLI)

- **Ruff** for lint and format. Line length 120. Pre-commit enforces it.
- **Loguru for dev logging** (`from loguru import logger as optic`). Positional args only: `optic.debug("x={}", x)`. Never f-strings. Never `exc_info=` (loguru ignores it). See the Optic section below for the full rule and known exceptions.
- **Typer for CLI.** `B008` suppressed because Typer requires function calls in argument defaults.
- **Skill files track CLI changes.** When any CLI command is added, removed, renamed, or has its flags changed, update the corresponding skill files in `observal_cli/skills/`. These are the agent's source of truth for command syntax.
- **Dynamic settings** for runtime config: `from services.dynamic_settings import get, get_int, get_bool`. Non-boot settings live in the DB, not env vars.
- **ClickHouse migrations** live in `observal-server/clickhouse/migrations/*.sql` and run through `services.clickhouse.migrations`. Keep Alembic for Postgres only. Never add ClickHouse DDL to startup code. The init container runs ClickHouse migrations after Alembic and before API startup.
- **SSRF guard** for all outbound network: `from services.ssrf_guard import is_private_url`. Used in webhooks, git clone, MCP analysis.
- **Conventional Commits**: `feat`, `fix`, `docs`, `refactor`, `test`, `build`, `ci`, `chore`. Scope in parens. No fixup commits (amend instead).

### TypeScript (web)

Vite 8 SPA with TanStack Router, not Next.js. `web/AGENTS.md` is the authoritative frontend reference; the rules below are the short form.

- **Auth storage is split.** `observal_access_token` lives in sessionStorage; `observal_refresh_token` and cached profile fields (role, name, email, username, avatar) live in localStorage so refresh survives reloads and new tabs. Do not widen localStorage use without changing the auth model deliberately.
- **TanStack Query hooks** from `use-api.ts` for all data fetching. Raw `fetch` in components is a known exception, not a pattern: a handful of call sites (co-authors, edit-lock release via `keepalive`, logout, SAML exchange) still use it. Do not add more.
- **Types centralized** in `src/lib/types.ts` (a barrel over `src/lib/types/`). No inline API response types.
- **harness list from server** (`/api/v1/config/harnesses`), never hardcoded in frontend.
- **OKLCH color tokens** in `src/app.css`. No raw hex/rgb in components.

### General

- **No telemetry wrappers or OTLP env vars.** MCP commands and remote URLs remain direct. Telemetry flows through session push hooks and reconciliation. Never generate `OTEL_*` or `CLAUDE_CODE_ENABLE_TELEMETRY` vars.
- **Owner fallback on install.** Submitters can install their own items without admin approval. Approved items are preferred, but pending/rejected items are accessible to the submitter.
- **Canonical registry identity is `namespace/slug`.** UUIDs remain accepted; legacy bare names resolve only when unambiguous. CLI slash-qualified references resolve to UUIDs before using existing action routes.
- **Hard rewrite policy.** No deprecation wrappers. When code moves, callers update in the same PR. Dead code is deleted immediately.
- **Tests mock externals.** No Docker needed to run the test suite. E2E specs in `tests/e2e/` are the exception (require running stack).
- **Verify against a live instance, not only tests.** When a change affects runtime behavior (API routes, worker, migrations, web UI, Dockerfiles, compose), run `make rebuild` and exercise it on the running stack (curl the endpoint, click through the page, run the relevant Playwright spec) before calling it done. Mocked tests passing is not proof it works in the container. Skip this for docs-only, test-only, or pure refactors with no runtime effect.
- **Docker image facts.** The API container root filesystem is read-only, so Python bytecode is precompiled in `Dockerfile.api` (otherwise every start recompiles). The web image runs `tsc` and the Vite bundle as parallel BuildKit stages, so a type error still fails the build via the `typecheck` stage marker.

## CLI structure

```
observal
├── api                      # authenticated JSON escape hatch for /api/v1 endpoints
├── discover                 # search, inspect, use approved resources for the current task
├── delegate                 # find, run, status, reply, list, cancel, mcp: hand a task to another agent
├── scan                     # discovery of what's installed; only `--inventory` is strictly read-only,
│                            # plain `scan` also runs legacy MCP config and lockfile migrations
├── outdated                 # installed components with newer versions available
├── reconcile                # backfill sessions missed by automatic delivery
├── inbox                    # list, count, show, read, unread, done, dismiss, reopen, read-all
├── share                    # candidates, create, open, revoke: version-pinned agent sharing
├── auth                     # login, logout, whoami, status, change-password, set-username
├── config                   # show, set, path, alias, aliases
├── registry                 # component parent group
│   ├── mcp                  #   submit, list, my, show, install, edit, fork, archive, unarchive, sync,
│   │                        #   transfer-owner, co-authors
│   ├── skill                #   submit, list, my, show, install, edit, fork, fork-listing, import-folder,
│   │                        #   rebase, export, replace-files, withdraw, backups, archive, unarchive,
│   │                        #   transfer-owner, co-authors
│   ├── hook                 #   submit, list, show, install, edit, fork, archive, unarchive,
│   │                        #   transfer-owner, co-authors
│   ├── prompt               #   submit, list, my, show, render, edit, fork, archive, unarchive,
│   │                        #   transfer-owner, co-authors (`submit --draft` saves a draft)
│   ├── sandbox              #   submit, list, show, edit, fork, archive, unarchive,
│   │                        #   transfer-owner, co-authors (`submit --draft` saves a draft)
│   ├── models               #   inspect registry-backed harness model data
│   ├── version              #   publish, list
│   ├── recommend            #   components recommended from your own sessions
│   ├── bulk                 #   mixed component submission from one JSON file
│   └── a2a                  #   submit, list, review, remove remote A2A agents
├── agent                    # create, bulk-create, list, my, show, fork, install, archive,
│                            # unarchive, delete, init, add, build, edit, publish, release,
│                            # versions, outdated, transfer-owner, co-authors
│   └── pull                 #   install agent into harness (primary workflow)
├── team                     # list, show, create, claim-personal, delete, leave, members, invite,
│                            # visibility, request (teamspaces)
├── review                   # list, show, files, diff, checks, gate, threads, comment, reply, resolve,
│                            # approve, request-changes, request-reviewer, publish, withdraw, policy, watch
├── ops                      # top, rate, rate-update, rate-delete, feedback, traces
│   ├── telemetry            #   status
│   ├── logs                 #   live dev log viewer
│   └── insights             #   list, show, generate, sessions, activity
├── admin                    # settings, users, SAML and SCIM config, audit-log, security-events,
│                            # trace-privacy, diagnostics, cache-clear, set-role, recommend
├── self                     # upgrade, downgrade, rollback, status
├── doctor                   # diagnose + patch harness settings for all 10 harnesses
│   ├── patch / cleanup      #   install or remove telemetry hooks
│   └── support              #   bundle, inspect: diagnostic bundle with redaction
└── server                   # start, stop, restart, status, logs, install, reset, config,
    │                        # upgrade, rollback, versions
    └── migrate              #   export, import, validate (PostgreSQL archives only);
                             #   export-telemetry, import-telemetry, validate-telemetry (ClickHouse)
```

Command lists are a snapshot. Run `observal <group> --help` before documenting a command path.

`pull` is a subcommand (`observal agent pull`), not a top-level command.

## Server routes

REST at `/api/v1/`. GraphQL at `/api/v1/graphql` (read-only telemetry layer with subscriptions).

Key route files: `ard.py`, `ard_imports.py` (remote A2A agents), `auth.py`, `mcp.py`, `skill.py`, `skill_files.py`, `hook.py`, `prompt.py`, `sandbox.py`, `agent_shares.py`, `agent_versions.py`, `inbox.py`, `teams.py`, `feedback.py`, `dashboard.py`, `exec_dashboard.py`, `insights.py`, `component_activity.py`, `ingest.py`, `telemetry.py`, `alert.py`, `config.py`, `sessions.py`, `device_auth.py`, `jwks.py`, `sso_saml.py`, `admin_sso.py`, `scim.py`, `component_source.py`, `component_versions.py`, `component_forks.py`, `co_authors.py`, `layer_snapshot.py`, `recommendations.py`, `bulk.py`, `support.py`, `preview.py`, `audit.py`, `audit_log.py`, `registry.py`.

Sub-packages: `agent/` (crud, draft, fork, install, insights), `admin/` (enterprise_settings, users, retention, policy, recommended, migrate, system, usage_ping), `reviews/` (queue, detail, threads, submissions, actions, policy, check_settings).

## Database architecture

- **PostgreSQL**: relational data (users, teams, agents, components, reviews, inbox, feedback, settings). SQLAlchemy async.
- **ClickHouse**: session events, session aggregates and checkpoints, session capabilities, component activity, layer snapshots and components, audit events, security events, and webhook deliveries. HTTP interface, MergeTree-family tables, bloom filter indexes. Schema changes use versioned SQL migrations in `observal-server/clickhouse/migrations/`. Runtime helpers stay in `services/clickhouse/`.
- **Redis**: pub/sub for GraphQL subscriptions, arq job queue, dynamic settings cache, auth token revocation.

## Telemetry pipeline

```
harness ──→ session push hooks ──→ POST /api/v1/ingest/session ──→ ClickHouse
CLI ──→ observal reconcile ──→ POST /api/v1/ingest/session ──→ ClickHouse
```

Session delivery uses a local outbox and resumes after transient network failures.

## Auth model

- JWT bearer tokens. `Authorization: Bearer <token>` on every authenticated request. There is no `X-API-Key` path.
- JWT signing uses ES256 (not HS256). JWKS endpoint for public key distribution.
- Device authorization flow for CLI login via browser confirmation.
- Redis fail-closed: if Redis is down, auth fails (prevents stale token usage).
- Fresh servers auto-bootstrap admin on first `observal auth login` (localhost-only).

## Release branches

For maintainer release tasks, use the repository-local [release skill](.agents/skills/release/SKILL.md). It is not part of the bundled end-user skills.

Forward development targets `main`; publishing runs only from protected `release/X.Y` branches. Alpha, beta, RC, stable, and patches share one minor-line branch. Use `tools/release.py` (or `make release ARGS="..."`) for cutting lines, preparation, backports, and status. Never merge a release branch back into `main`. Only the newest verified stable release triggers hosted deployment. Terraform validation remains independent and never applies infrastructure during release. See `docs/self-hosting/releasing.md` for the workflow and production rollout gates.

## Commands

```bash
# Docker stack (8 core services: init, api, db, clickhouse, redis, worker, web, lb)
make up                  # start
make up-prometheus       # core stack plus Prometheus
make up-observability    # core stack plus Prometheus and Grafana
make down                # stop
make rebuild             # rebuild api/web images once and restart (alias: make rebuild-fast, same target)
make logs                # tail logs

# CLI (installed via uv)
uv tool install --editable .
observal auth login      # auto-creates admin on fresh server, or login
observal auth whoami     # check auth

# Linting
make lint                # ruff check
make format              # ruff format + ruff fix
make check               # pre-commit on all files
make hooks               # install pre-commit hooks

# Tests (all mock externals, no Docker needed)
make test                # runs tests/ only (430 files), parallel via pytest-xdist
make test-v              # verbose
make test-ch             # ClickHouse integration proofs against a local sample DB (tests/integration_clickhouse/)
# observal-server/tests/ (19 files) and observal_cli/tests/ (13 files) are not run
# by `make test` or CI; invoke pytest on those paths directly.
make test-fuzz           # smoke-test the OSS-Fuzz targets in fuzz/ (needs atheris)
# E2E (requires running stack):
cd tests/e2e && pnpm test   # 32 Playwright specs
```

## Optic (dev logging)

Loguru-based. `observal ops logs` streams `~/.observal/logs/dev.log`.

- Import: `from loguru import logger as optic`
- Format: `optic.debug("msg: x={}", x)` (positional only, never f-strings)
- **Never pass `exc_info=`.** Loguru ignores it, so the traceback is silently dropped and `exc_info` lands in `extra` as a literal. Use `optic.exception(...)` or `optic.opt(exception=True).error(...)`.
- **Avoid structlog-style keyword args** (`optic.info("event_name", key=value)`). Loguru does not interpolate them into the message; they land in `record["extra"]`. The stderr and file sinks render `{message}` only, so those values are invisible in `docker logs` and `dev.log`, only the ring-buffer sink (SSE `/admin/logs/stream`, support bundle) retains them. The legacy call sites have been converted to positional formatting; format arguments positionally and never add new keyword-arg calls.
- Never log secrets, tokens, keys, JWT payloads. Log IDs and counts only.
- Log format (console/json) configured via `observability.log_format` dynamic setting.
- `logging_config.py` configures a separate structlog pipeline used by parts of the insights engine. Loguru (`optic`) is the default for new code.

## AI contribution policy

See `AI_POLICY.md`. Key rules: no autonomous PRs without human authorship, every change must be explainable, label AI tool usage, frontend changes need screenshots, no slop.

## Paths to never commit

`.claude/`, `CLAUDE.md`, `.kiro/`, `.cursor/`, `.gemini/`, `GEMINI.md`, `.opencode/`, `.github/copilot-instructions.md`, `.copilot/`, `.vscode/`, `.worktrees/`
