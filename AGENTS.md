<!-- SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# AGENTS.md

Internal context for contributors and AI coding agents. Use `README.md` for the public API reference, `SETUP.md` for environment setup, and `docs/adding-a-harness.md` for harness integration.

## What Observal is

Observal is an agent-centric registry and observability platform for AI coding agents. Users interact with it three ways:

1. **CLI** (`observal`): pull agents, sca harnesses, submit components, manage the server
2. **Web UI** (`web/`): browse the registry, view traces, manage users, admin dashboard
3. **Observal skill** (bundled, auto-installed on login): lets the LLM inside any harness drive Observal commands directly (e.g. "create an agent that uses the github MCP")

Agents are the primary entity. Each agent bundles 5 component types: MCP servers, skills, hooks, prompts, and sandboxes. When a user runs `observal agent pull <agent>`, the platform resolves all components and writes harness-specific config files.

## harness capability support

Ten harnesses are registered in `packages/observal-shared/observal_shared/harness_registry.py`. Support is per-capability, not a single tier. Verify against the registry before relying on this table.

| Harness | Hook spec | Session parser | Capabilities | Harness-specific e2e |
|---|---|---|---|---|
| Claude Code | yes | `claude-code` | hooks, mcp_servers, skills | no |
| Kiro | yes | `kiro` | hooks, mcp_servers | yes (9 specs) |
| Cursor | no | `cursor` | hooks, mcp_servers | no |
| Pi | no | `pi` | hooks, mcp_servers, skills | no |
| Codex CLI | yes | `codex` | hooks, mcp_servers, skills | no |
| Copilot | yes | `copilot-cli` (shared) | hooks, mcp_servers, skills, prompts | no |
| Copilot CLI | yes | `copilot-cli` | hooks, mcp_servers, skills, prompts | no |
| OpenCode | yes | `opencode` | hooks, mcp_servers, skills | no |
| Antigravity | yes | `antigravity` | hooks, mcp_servers, skills | no |
| Goose | yes | `goose` | hooks, mcp_servers, skills | no |

Every harness now resolves a session parser, so `observal reconcile` works across all ten. Hook specs in `observal_cli/harness_specs/` exist for eight; Cursor and Pi have none. Only Kiro has harness-specific Playwright coverage.

See `docs/adding-a-harness.md` for the complete guide to adding or promoting a harness.

## Architecture at a glance

```
observal_cli/          Python CLI (Typer)
  harness/             CLI-side harness adapters (protocol.py, base.py, 10 adapters)
  harness_specs/       Hook specs (8: claude_code, kiro, codex, copilot, copilot_cli, opencode, antigravity, goose)
  skills/              Bundled skills installed on login (observal, observal-admin, etc.)
  delegation/          Agent-to-agent delegation: A2A tasks, workspace isolation, headless runs,
                       A2A client, observal-agents MCP server (docs/adr/0002-a2a-delegation.md)

observal-server/       FastAPI server
  api/routes/          REST endpoints (agent/, admin/ are sub-packages)
  api/middleware/      Audit, request-id, content-type
  models/              SQLAlchemy models (PostgreSQL)
  schemas/             Pydantic request/response schemas
  services/            Business logic
    clickhouse/        ClickHouse subpackage (client, schema, insert, query)
    harness/           Server-side harness adapters (config generation)
    session_parsers/   Per-harness JSONL parsers (9 modules covering all 10 harnesses)
    audit/             Compliance audit system (loguru-based)
    config/            Config generation helpers (mcp_builder, skill_builder)
    insights/          Insight engine (report generation, facets, sections, HTML export)
    shared/            Cross-service utilities
  jobs/                Background job definitions (catalog, maintenance, migration)


web/                   Vite 8 SPA / React 19 / TanStack Router (see web/AGENTS.md)
packages/pi-extension/ Pi telemetry extension (npm: observal-pi)
docker/                Docker Compose stack (10 services)
fuzz/                  Atheris fuzz targets + OSS-Fuzz project config mirror
tests/                 pytest (174 files)
tests/e2e/             Playwright (20 specs)
```

## How the modularisation works

The codebase follows a strict adapter pattern for harness-specific logic. This is the most important architectural decision:

**One adapter per harness, on both sides.** CLI adapters handle scanning and hook detection (`observal_cli/harness/<name>.py`). Server adapters handle config file generation (`observal-server/services/harness/<name>.py`). The shared harness registry (`packages/observal-shared/observal_shared/harness_registry.py`) defines paths, keys, features, and event maps for both sides.

**No if/elif chains for harness logic.** If you need harness-specific behavior, it goes in the adapter. The orchestrators (`cmd_scan.py`, `agent_builder.py`, `cmd_doctor.py`) call adapters via the registry, never with conditionals.

**Capability gating.** Each adapter method maps to a capability via `METHOD_FEATURE_MAP` in `observal_cli/harness/protocol.py`. The registry entry's `capabilities` set (`hooks`, `mcp_servers`, `skills`, `prompts`) decides what is allowed; `BaseAdapter` raises `NotSupportedError` when the capability is absent. This means stubs are safe: they exist but can't be called for unsupported operations.

**Headless runs are a verified fact.** The registry's `headless_run` runtime fact says a harness CLI can run one prompt non-interactively; the CLI adapter's `headless_command` builds the argv and `parse_headless_output` reads the answer. Delegation uses only harnesses with the fact set.

**Session parsers are separate from adapters.** They live in `services/session_parsers/` (server-side) and handle converting raw JSONL into normalized trace events. All nine harnesses resolve a parser; Copilot reuses the Copilot CLI parser.

### What full support means concretely

A fully supported harness has all of:
- A hook spec in `harness_specs/` (defines what `doctor patch` installs)
- A session parser resolved from the registry's `session_parser` key (enables `observal reconcile`)
- Full scanning implementation in its CLI adapter (discovers MCPs, skills, hooks, agents)
- E2E test coverage in `tests/e2e/`

Today only Kiro meets all four. A minimal harness has:
- A registry entry with correct paths
- A CLI adapter that handles basic MCP scanning
- A server adapter that generates config files
- No hook spec and no e2e tests

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
├── scan                     # read-only discovery of what's installed
├── outdated                 # installed components with newer versions available
├── reconcile                # backfill sessions missed by automatic delivery
├── auth                     # login, logout, whoami, status, change-password, set-username
├── config                   # show, set, path, alias, aliases
├── registry                 # component parent group
│   ├── mcp                  #   submit, list, show, fork, install, edit, delete, co-authors, sync
│   ├── skill                #   submit, list, show, fork, fork-listing, install, edit, delete, co-authors
│   ├── hook                 #   submit, list, show, fork, install, edit, delete, co-authors
│   ├── prompt               #   submit, list, show, fork, edit, render, delete, co-authors
│   ├── sandbox              #   submit, list, show, fork, edit, delete, co-authors
│   ├── models               #   inspect registry-backed harness model data
│   ├── version              #   component version commands
│   ├── recommend            #   components recommended from your own sessions
│   ├── bulk                 #   mixed component submission from one JSON file
│   └── a2a                  #   submit, list, review, remove remote A2A agents
├── agent                    # create, bulk-create, list, my, show, fork, install, archive,
│                            # unarchive, delete, init, add, build, publish, release,
│                            # versions, transfer-owner, co-authors
│   └── pull                 #   install agent into harness (primary workflow)
├── team                     # list, show, create, delete, leave, members (list/add/remove)
├── ops                      # top, rate, rate-update, rate-delete, feedback, traces
│   ├── telemetry            #   status
│   ├── logs                 #   live dev log viewer
│   └── insights             #   agent insight reports
├── admin                    # core administration + review (list/show/approve/reject)
├── self                     # upgrade, downgrade, rollback, status
├── doctor                   # diagnose + patch harness settings for all 10 harnesses
│   ├── patch / cleanup      #   install or remove telemetry hooks
│   └── support              #   diagnostic bundle with redaction
└── server                   # start, stop, restart, status, logs, install, reset, config
    └── migrate              #   PostgreSQL and ClickHouse migration tools
```

`pull` is a subcommand (`observal agent pull`), not a top-level command. Run `observal --help` to confirm before documenting a command path.

## Server routes

REST at `/api/v1/`. GraphQL at `/api/v1/graphql` (read-only telemetry layer with subscriptions).

Key route files: `ard.py`, `ard_imports.py` (remote A2A agents), `auth.py`, `mcp.py`, `skill.py`, `hook.py`, `prompt.py`, `sandbox.py`, `review.py`, `feedback.py`, `dashboard.py`, `insights.py`, `reconcile.py`, `ingest.py`, `telemetry.py`, `alert.py`, `config.py`, `sessions.py`, `device_auth.py`, `jwks.py`, `component_source.py`, `component_versions.py`, `agent_versions.py`, `bulk.py`, `support.py`, `preview.py`, `audit.py`, `registry_models.py`.

Sub-packages: `agent/` (crud, install, draft), `admin/` (enterprise_settings, users, org, retention).

## Database architecture

- **PostgreSQL**: relational data (users, agents, components, feedback, settings). SQLAlchemy async.
- **ClickHouse**: session events, session aggregates, audit events, security events, and webhook deliveries. HTTP interface, MergeTree-family tables, bloom filter indexes. Schema changes use versioned SQL migrations in `observal-server/clickhouse/migrations/`. Runtime helpers stay in `services/clickhouse/`.
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
# Docker stack (10 services: init, api, db, clickhouse, redis, worker, web, lb, prometheus, grafana)
make up                  # start
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
make test                # runs tests/ only (174 files), parallel via pytest-xdist
make test-v              # verbose
# observal-server/tests/ (21 files) and observal_cli/tests/ (11 files) are not run
# by `make test` or CI; invoke pytest on those paths directly.
make test-fuzz           # smoke-test the OSS-Fuzz targets in fuzz/ (needs atheris)
# E2E (requires running stack):
cd tests/e2e && pnpm test   # 20 Playwright specs
```

## Optic (dev logging)

Loguru-based. `observal ops logs` streams `~/.observal/logs/dev.log`.

- Import: `from loguru import logger as optic`
- Format: `optic.debug("msg: x={}", x)` (positional only, never f-strings)
- **Never pass `exc_info=`.** Loguru ignores it, so the traceback is silently dropped and `exc_info` lands in `extra` as a literal. Use `optic.exception(...)` or `optic.opt(exception=True).error(...)`.
- **Avoid structlog-style keyword args** (`optic.info("event_name", key=value)`). Loguru does not interpolate them into the message; they land in `record["extra"]`. The stderr and file sinks render `{message}` only, so those values are invisible in `docker logs` and `dev.log` — only the ring-buffer sink (SSE `/admin/logs/stream`, support bundle) retains them. About 30 legacy call sites still do this (`jobs/catalog.py`, `services/dynamic_settings.py`, `services/insights/self_learn.py`, `services/strategic_insights.py`, `api/routes/{dashboard,insights}.py`); convert them when you touch them, don't add new ones.
- Never log secrets, tokens, keys, JWT payloads. Log IDs and counts only.
- Log format (console/json) configured via `observability.log_format` dynamic setting.
- `logging_config.py` configures a separate structlog pipeline used by parts of the insights engine. Loguru (`optic`) is the default for new code.

## AI contribution policy

See `AI_POLICY.md`. Key rules: no autonomous PRs without human authorship, every change must be explainable, label AI tool usage, frontend changes need screenshots, no slop.

## Paths to never commit

`.claude/`, `CLAUDE.md`, `.kiro/`, `.cursor/`, `.gemini/`, `GEMINI.md`, `.opencode/`, `.github/copilot-instructions.md`, `.copilot/`, `.vscode/`, `.worktrees/`
