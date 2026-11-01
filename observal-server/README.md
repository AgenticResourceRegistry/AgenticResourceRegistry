<!-- SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Agentic Resource Registry Server

FastAPI backend that powers the Agentic Resource Registry platform. Provides REST and GraphQL APIs for managing agents, components, telemetry, and user access.

## Stack

- **Framework**: FastAPI with Uvicorn
- **Database**: PostgreSQL (async via SQLAlchemy + asyncpg)
- **Analytics**: ClickHouse for telemetry, traces, and scoring data
- **Cache / Jobs**: Redis with arq for background workers
- **GraphQL**: Strawberry (dashboard queries with DataLoaders)
- **Auth**: JWT (asymmetric signing), OAuth/OIDC via Authlib

## Directory Layout

```
observal-server/
├── main.py              # App entrypoint, middleware stack, lifespan
├── config.py            # Settings from env vars / .env
├── database.py          # Async SQLAlchemy engine and session factory
├── worker.py            # arq background worker (syncs, alerts)
├── api/
│   ├── deps.py          # Dependency injection (auth, DB sessions, role checks)
│   ├── graphql.py       # Strawberry schema and DataLoaders
│   ├── ratelimit.py     # slowapi rate limiting
│   ├── middleware/       # Request ID tracking, content-type validation
│   └── routes/          # REST routers (19 modules)
├── models/              # SQLAlchemy ORM models (22 tables)
├── schemas/             # Pydantic request/response schemas
├── services/            # Business logic (32 modules)
└── alembic/             # Database migrations
```

## API Surface

### REST Endpoints

| Area | Routes | Purpose |
|------|--------|---------|
| Auth | `/api/v1/auth/*` | Login, OAuth/OIDC, token refresh, bootstrap |
| Agents | `/api/v1/agents/*` | CRUD, validation, config generation, install |
| Components | `/api/v1/mcps/*`, `skills/*`, `hooks/*`, `prompts/*`, `sandboxes/*` | Registry for each component type |
| Telemetry | `/api/v1/telemetry/*` | Telemetry data ingestion |
| Admin | `/api/v1/admin/*` | User management, settings, system config |
| Review | `/api/v1/review/*` | Approve/reject submissions |
| Dashboard | `/api/v1/graphql` | GraphQL for dashboard and trace queries |
| Health | `/healthz`, `/health` | Liveness and readiness checks |

### RBAC

Four-tier role hierarchy: `super_admin > admin > reviewer > user`. Enforced via dependency injection in `api/deps.py`.

## Middleware

| Layer | What it does |
|-------|-------------|
| SecurityHeaders | HSTS, CSP, X-Frame-Options, X-Content-Type-Options |
| RequestSizeLimit | Rejects bodies over 10 MB |
| ContentType | Validates Content-Type, blocks JSON nesting beyond 20 levels |
| RequestID | Assigns a UUID `X-Request-ID` to every request |
| CORS | Configurable allowed origins |
| RateLimit | 10 req/min on auth endpoints, 5 req/min on strict paths |

## Background Workers

The arq worker (`worker.py`) runs two recurring jobs:

- **sync_component_sources**: Pull latest state from Git mirrors (every 6 hours)
- **evaluate_alerts**: Check alert rule conditions (every minute)

## Database Migrations

Managed with Alembic. Migrations live in `alembic/versions/`.

```bash
cd observal-server
alembic upgrade head
alembic revision --autogenerate -m "description"
```

## Running Locally

```bash
cd observal-server
uv run uvicorn main:app --reload --port 8000
```

Requires PostgreSQL, ClickHouse, and Redis. See `config.py` for all environment variables and defaults.
