<!-- SPDX-FileCopyrightText: 2026 Naraen Rammoorthi -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Phase 0 ClickHouse publication proofs

Run `make test-ch` against the existing **local, already-migrated sample** database (`http://127.0.0.1:8123/observal` by default). Set `OBSERVAL_CH_TEST_URL` to another *local* `.../observal` endpoint and, if needed, `OBSERVAL_CH_TEST_USER` / `OBSERVAL_CH_TEST_PASSWORD`; the suite rejects remote hosts or another database name. Default `make test` excludes this directory, and a direct pytest invocation without `OBSERVAL_CH_TEST_URL` skips it. The Phase 0 suite checks that migrations through 005 were already applied and that baseline tables plus sample source rows exist; it **does not** run any new migrations against the sample. It creates and writes only the reserved `phase0_ci_` tables from `tests/fixtures/component_insights/clickhouse/projection_tables.sql`. Test runs use unique generated project keys. There is no `DROP`, `TRUNCATE`, `ALTER`, or write to pre-existing application tables, and test tables/rows are intentionally left for owner-approved cleanup later. Passing here proves publication queries against the pinned **test-only final logical DDL contract**, **not** that future production migrations have been applied or match it; Phases 1–2 must compare and re-test their shipped schemas. Concurrent Phase 0 tests assume unique generations allocated before each attempt. The production `006_layer_components.sql` was separately applied by the real runner against a disposable isolated ClickHouse 26.6 database (`observal_phase14_ci`); it was never applied to the sample database.

## Phase 1.4–1.5 isolated proof

Use a **dedicated isolated** local ClickHouse instance/database named `observal_phase14_ci` with production migrations through 006 applied by `python -m services.clickhouse.migrations`. Never point this proof at the existing `observal` sample. With an isolated PostgreSQL database migrated to Alembic `029_projection_generation`, run:

```bash
cd observal-server
OBSERVAL_CH_PHASE14_URL=http://127.0.0.1:18123/observal_phase14_ci \
CLICKHOUSE_URL=clickhouse://proof:proof@127.0.0.1:18123/observal_phase14_ci \
DATABASE_URL=postgresql+asyncpg://proof:proof@127.0.0.1:15432/observal_phase14_ci \
uv run --with pytest --with pytest-asyncio --with pyyaml --with typer --with rich pytest ../tests/integration_clickhouse/test_phase14_presence.py -q
```

The test rejects any URL targeting the sample database and reads `006_layer_components` in the isolated migration ledger. It inserts only synthetic uniquely keyed projection/session rows there. The separate PostgreSQL proof ran the target `028_` Alembic upgrade after stamping 027 in the isolated database. A full empty-database Alembic upgrade remains blocked by the pre-existing `003_skill_registry_direct` migration (`DuplicateColumnError` after `Base.metadata.create_all`), so that is **not** claimed green.

## Phase 2.2 isolated production migration proof

The `007_component_activity.sql` migration is compared byte-for-byte after SQL splitting to the last two pinned Phase 0 DDL statements by `tests/test_clickhouse_migrations.py`. To prove that the **real migration runner** creates those production tables, opt in with the separate `observal_phase22_ci` database on the isolated proof ClickHouse instance (never the existing sample):

```bash
cd observal-server
OBSERVAL_CH_PHASE22_URL=http://127.0.0.1:18123/observal_phase22_ci \
CLICKHOUSE_URL=clickhouse://proof:proof@127.0.0.1:18123/observal_phase22_ci \
uv run --with pytest --with pytest-asyncio pytest ../tests/integration_clickhouse/test_phase22_migration.py -q
```

The test hard-rejects the sample database/port, runs all pending migrations in that dedicated database, checks the migration ledger, table engines, complete safe-field sets and generation keys, inserts a complete zero-call marker with zero activity rows, and reruns the runner to prove idempotence. It does not delete its proof tables/database/container. Use test-only credentials, not production credentials.

## Phase 2.5 isolated activity replay proof

The fixture-derived real parallel records and explicitly constructed single-record multi-block case are projected against the already-migrated **isolated** `observal_phase22_ci` ClickHouse database. Activity generations are allocated from the separately isolated PostgreSQL `028` sequence. This suite never runs migrations, never touches the sample, and retains all uniquely keyed synthetic rows after testing:

```bash
cd observal-server
PYTHONPATH=.:../packages/observal-shared \
OBSERVAL_CH_PHASE25_URL=http://127.0.0.1:18123/observal_phase22_ci \
CLICKHOUSE_URL=clickhouse://proof:proof@127.0.0.1:18123/observal_phase22_ci \
DATABASE_URL=postgresql+asyncpg://proof:proof@127.0.0.1:15432/observal_phase14_ci \
uv run --with pytest --with pytest-asyncio pytest ../tests/integration_clickhouse/test_phase25_activity.py -q
```

The source checkout path for the shared harness registry is explicit: the server's installed `observal_shared` may be older than the new extractor registration. The test requires the exact isolated host, port and database names, including for PostgreSQL, and skips unless opted in.

## Phase 4a isolated report and migration proof

Use **new disposable** databases only. The proof refuses any other host, ports,
users or database names; never point it at the sample or production stack. The
PostgreSQL target migration was run against a minimal pre-030 table shape after
stamping 029; this validates the target upgrade/rollback, **not** a historical
fresh install (the existing 003 bootstrap problem remains).

```bash
docker run --rm -d --name observal_phase4_ci_pg -e POSTGRES_USER=proof \
  -e POSTGRES_PASSWORD=proof -e POSTGRES_DB=observal_phase4_ci \
  -p 127.0.0.1:15433:5432 postgres:16-alpine
# Wait for: docker exec observal_phase4_ci_pg pg_isready -U proof -d observal_phase4_ci
docker exec -i observal_phase4_ci_pg psql -v ON_ERROR_STOP=1 -U proof -d observal_phase4_ci <<'SQL'
CREATE TABLE insight_reports (id uuid PRIMARY KEY, agent_id uuid NOT NULL);
CREATE TABLE insight_session_facets (id uuid PRIMARY KEY, agent_id uuid NOT NULL,
  session_id text NOT NULL, CONSTRAINT uq_session_facets_agent_session UNIQUE (agent_id, session_id));
CREATE TABLE mcp_listings (id uuid PRIMARY KEY);
CREATE TABLE skill_listings (id uuid PRIMARY KEY);
CREATE TABLE hook_listings (id uuid PRIMARY KEY);
CREATE SEQUENCE projection_generation_seq;
SQL
export DATABASE_URL=postgresql+asyncpg://proof:proof@127.0.0.1:15433/observal_phase4_ci
export PYTHONPATH=.:observal-server:packages/observal-shared
observal-server/.venv/bin/alembic -c observal-server/alembic.ini stamp 029_projection_generation
observal-server/.venv/bin/alembic -c observal-server/alembic.ini upgrade head
observal-server/.venv/bin/alembic -c observal-server/alembic.ini downgrade 029_projection_generation
observal-server/.venv/bin/alembic -c observal-server/alembic.ini upgrade head

docker run --rm -d --name observal_phase4_ci_ch -p 127.0.0.1:18124:8123 \
  -e CLICKHOUSE_DB=observal_phase4_ci -e CLICKHOUSE_USER=proof \
  -e CLICKHOUSE_PASSWORD=proof -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 \
  clickhouse/clickhouse-server:26.6.4.55
export CLICKHOUSE_URL=clickhouse://proof:proof@127.0.0.1:18124/observal_phase4_ci
# Wait for: curl -fsS http://127.0.0.1:18124/ping
observal-server/.venv/bin/python -m services.clickhouse.migrations
OBSERVAL_CH_PHASE4_URL=http://127.0.0.1:18124/observal_phase4_ci \
OBSERVAL_PG_PHASE4_URL="$DATABASE_URL" \
observal-server/.venv/bin/python -m pytest \
  tests/integration_clickhouse/test_phase4_insights.py \
  tests/integration_clickhouse/test_phase4_report_persistence.py -q
# After proof: docker stop observal_phase4_ci_pg observal_phase4_ci_ch
```

The fixture gives two users the same session ID and Agent ID, but only one
verified component mapping. It checks that source reads/facets stay separate,
that the generated Agent report reaches the HTML renderer, and that component
report counts, collision coverage and version distribution match the activity
API on exactly the same interval. The Phase 4b portion additionally selects a
published call, verifies its source hash against
canonical JSONL, sends bounded scoped excerpts to a mocked model and checks that
Bob's identically named session never reaches Alice's prompt or cited evidence.
A separate mock regression checks that a late published call is paired only
with a nearby preceding user request, not the first unrelated request or a
tool-result body.
The model is mocked; this is not proof of qualitative model accuracy. An isolated PG transaction checks that a
listing DELETE removes its reports and rollback restores both, via the shared
DB trigger installed by 030. The trigger also covers future deletion paths;
currently the MCP/skill/hook API routes **archive**, not hard-delete, listings.
Screenshots require a separately running web app and are not part of this DB
proof.
