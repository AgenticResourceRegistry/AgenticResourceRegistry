<!-- SPDX-FileCopyrightText: 2026 Naraen Rammoorthi -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Phase 0 ClickHouse publication proofs

Run `make test-ch` against the existing **local, already-migrated sample** database (`http://127.0.0.1:8123/observal` by default). Set `OBSERVAL_CH_TEST_URL` to another *local* `.../observal` endpoint and, if needed, `OBSERVAL_CH_TEST_USER` / `OBSERVAL_CH_TEST_PASSWORD`; the suite rejects remote hosts or another database name. Default `make test` excludes this directory, and a direct pytest invocation without `OBSERVAL_CH_TEST_URL` skips it. The Phase 0 suite checks that migrations through 005 were already applied and that baseline tables plus sample source rows exist; it **does not** run any new migrations against the sample. It creates and writes only the reserved `phase0_ci_` tables from `tests/fixtures/component_insights/clickhouse/projection_tables.sql`. Test runs use unique generated project keys. There is no `DROP`, `TRUNCATE`, `ALTER`, or write to pre-existing application tables, and test tables/rows are intentionally left for owner-approved cleanup later. Passing here proves publication queries against the pinned **test-only final logical DDL contract**, **not** that future production migrations have been applied or match it; Phases 1–2 must compare and re-test their shipped schemas. Concurrent Phase 0 tests assume unique generations allocated before each attempt. The production `006_layer_components.sql` was separately applied by the real runner against a disposable isolated ClickHouse 26.6 database (`observal_phase14_ci`); it was never applied to the sample database.

## Phase 1.4–1.5 isolated proof

Use a **dedicated isolated** local ClickHouse instance/database named `observal_phase14_ci` with production migrations through 006 applied by `python -m services.clickhouse.migrations`. Never point this proof at the existing `observal` sample. With an isolated PostgreSQL database migrated to Alembic `028_projection_generation`, run:

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
