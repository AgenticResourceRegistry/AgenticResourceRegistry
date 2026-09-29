<!-- SPDX-FileCopyrightText: 2026 Observal Contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Database integration tests

`tests/integration_clickhouse/` holds tests that run against **real** ClickHouse and PostgreSQL instead of mocks. They cover behaviour a mock cannot show: ClickHouse deduplication and `FINAL` reads, migrations applying through the real runner, deletes completing, row locks, and foreign-key failures.

They are **opt-in**. `make test` and CI exclude the directory, and every file also skips itself unless its own environment variables are set.

> [!WARNING]
> Run these only against throwaway databases you created for the purpose, except for the Phase 0 suite described below. Several suites insert rows, run migrations, or delete data. Each file checks the host and database name it is given and refuses anything unexpected, but that check is a safety net, not permission to point it elsewhere.

## The suites

| File | What it checks | Database it needs |
|---|---|---|
| `test_phase0_publication.py` | Publication and deduplication rules, on test-only `phase0_ci_*` tables | Your populated local development ClickHouse database `observal` |
| `test_phase14_presence.py` | Component snapshot indexing and presence | ClickHouse `observal_phase14_ci` + PostgreSQL `observal_phase14_ci` |
| `test_phase22_migration.py` | ClickHouse migration 007 through the migration runner | ClickHouse `observal_phase22_ci` on port 18123 |
| `test_phase25_activity.py` | Session records become exact, deduplicated activity rows | ClickHouse `observal_phase22_ci` on 18123 + PostgreSQL `observal_phase14_ci` on 15432 |
| `test_phase3_activity_api.py` | Activity summary and sessions; isolation between users and projects | Same pair as above |
| `test_phase4_insights.py` | Component reports end to end | ClickHouse `observal_phase4_ci` on 18124 + PostgreSQL `observal_phase4_ci` on 15433 |
| `test_phase4_report_persistence.py` | Component reports are removed with their listing, including on rollback | PostgreSQL `observal_phase4_ci` on 15433 |
| `test_retention_component_cleanup.py` | Retention removes session-derived data per user and session, and keeps shared snapshots | ClickHouse `retention_proof` + PostgreSQL `retention_proof_pg` |
| `test_user_deletion_postgres.py` | Deleted accounts become locked-out shells; concurrent deletes cannot remove the last admin | PostgreSQL `user_deletion_proof` |

The Phase 2.2 to Phase 4 suites require the exact ports shown. The others accept any local port.

## What they do not prove

- **Not production.** Passing here means the behaviour holds on a freshly created schema at the current migration head. It says nothing about an existing deployment's data or an older schema.
- **Synthetic data only.** No suite uses real transcripts, so none checks whether Insights findings are useful or accurate.
- **Most rows are kept.** Suites use unique IDs and leave their rows behind, which is why the databases must be disposable.

## Phase 0: your development database

`test_phase0_publication.py` is the one suite that expects an existing database, not a new one. It checks that `observal` is already migrated and contains session data, then writes only to its own `phase0_ci_*` tables. It never runs migrations or changes existing tables.

With the development stack running (`make up`) and some sessions recorded:

```bash
make test-ch
```

Override the target with `OBSERVAL_CH_TEST_URL`, `OBSERVAL_CH_TEST_USER`, and `OBSERVAL_CH_TEST_PASSWORD`. Only `localhost` or `127.0.0.1` and the database name `observal` are accepted. The test tables are left in place for you to drop.

## Everything else: throwaway databases

### 1. Start the containers

The ports below are the ones the fixed-port suites require. Check they are free first.

```bash
docker run -d --rm --name observal-it-ch  --memory 3g -p 127.0.0.1:18123:8123 \
  -e CLICKHOUSE_USER=proof -e CLICKHOUSE_PASSWORD=proof -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 \
  clickhouse/clickhouse-server:26.6.4.55
docker run -d --rm --name observal-it-ch4 --memory 2g -p 127.0.0.1:18124:8123 \
  -e CLICKHOUSE_USER=proof -e CLICKHOUSE_PASSWORD=proof -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 \
  clickhouse/clickhouse-server:26.6.4.55
docker run -d --rm --name observal-it-pg  -p 127.0.0.1:15432:5432 \
  -e POSTGRES_USER=proof -e POSTGRES_PASSWORD=proof -e POSTGRES_DB=observal_phase14_ci postgres:16
docker run -d --rm --name observal-it-pg4 -p 127.0.0.1:15433:5432 \
  -e POSTGRES_USER=proof -e POSTGRES_PASSWORD=proof -e POSTGRES_DB=observal_phase4_ci postgres:16

# Wait until all four accept connections. PostgreSQL is checked over TCP because
# its first-run initialisation server listens only on a local socket, then restarts.
until docker exec observal-it-ch  clickhouse-client -u proof --password proof -q 'SELECT 1' >/dev/null 2>&1 &&
      docker exec observal-it-ch4 clickhouse-client -u proof --password proof -q 'SELECT 1' >/dev/null 2>&1 &&
      docker exec observal-it-pg  psql -h 127.0.0.1 -U proof -d observal_phase14_ci -qc 'SELECT 1' >/dev/null 2>&1 &&
      docker exec observal-it-pg4 psql -h 127.0.0.1 -U proof -d observal_phase4_ci  -qc 'SELECT 1' >/dev/null 2>&1; do
  sleep 1
done

docker exec observal-it-pg psql -h 127.0.0.1 -U proof -d observal_phase14_ci \
  -c "CREATE DATABASE retention_proof_pg" -c "CREATE DATABASE user_deletion_proof"
```

### 2. Create the schemas

Use the same commands production uses: the ClickHouse migration runner, and for PostgreSQL the fresh-install bootstrap followed by an Alembic stamp.

```bash
cd observal-server
export PYTHONPATH=.:../packages/observal-shared

for target in 18123/observal_phase14_ci 18123/observal_phase22_ci 18124/observal_phase4_ci 18123/retention_proof; do
  CLICKHOUSE_URL="clickhouse://proof:proof@127.0.0.1:${target}" uv run python -m services.clickhouse.migrations
done

for target in 15432/observal_phase14_ci 15433/observal_phase4_ci; do
  export DATABASE_URL="postgresql+asyncpg://proof:proof@127.0.0.1:${target}"
  uv run python -m services.schema_bootstrap init && uv run alembic stamp head
done
cd ..
```

`retention_proof_pg` and `user_deletion_proof` need no preparation; those suites create what they use.

### 3. Run the suites

From the repository root:

```bash
export PYTHONPATH=observal-server:packages/observal-shared:.
CH=127.0.0.1:18123 CH4=127.0.0.1:18124
PG=postgresql+asyncpg://proof:proof@127.0.0.1:15432/observal_phase14_ci
PG4=postgresql+asyncpg://proof:proof@127.0.0.1:15433/observal_phase4_ci
t() { uv run --project observal-server python -m pytest -q "$@"; }

OBSERVAL_CH_PHASE14_URL=http://$CH/observal_phase14_ci CLICKHOUSE_URL=clickhouse://proof:proof@$CH/observal_phase14_ci \
  OBSERVAL_CH_PHASE14_USER=proof OBSERVAL_CH_PHASE14_PASSWORD=proof DATABASE_URL=$PG \
  t tests/integration_clickhouse/test_phase14_presence.py

OBSERVAL_CH_PHASE22_URL=http://$CH/observal_phase22_ci CLICKHOUSE_URL=clickhouse://proof:proof@$CH/observal_phase22_ci \
  t tests/integration_clickhouse/test_phase22_migration.py

OBSERVAL_CH_PHASE25_URL=http://$CH/observal_phase22_ci CLICKHOUSE_URL=clickhouse://proof:proof@$CH/observal_phase22_ci \
  DATABASE_URL=$PG t tests/integration_clickhouse/test_phase25_activity.py tests/integration_clickhouse/test_phase3_activity_api.py

OBSERVAL_CH_PHASE4_URL=http://$CH4/observal_phase4_ci CLICKHOUSE_URL=clickhouse://proof:proof@$CH4/observal_phase4_ci \
  DATABASE_URL=$PG4 t tests/integration_clickhouse/test_phase4_insights.py

OBSERVAL_PG_PHASE4_URL=$PG4 DATABASE_URL=$PG4 t tests/integration_clickhouse/test_phase4_report_persistence.py

R=clickhouse://proof:proof@$CH/retention_proof RP=postgresql+asyncpg://proof:proof@127.0.0.1:15432/retention_proof_pg
CLICKHOUSE_URL=$R OBSERVAL_CH_RETENTION_URL=$R DATABASE_URL=$RP OBSERVAL_PG_RETENTION_URL=$RP \
  t tests/integration_clickhouse/test_retention_component_cleanup.py

U=postgresql+asyncpg://proof:proof@127.0.0.1:15432/user_deletion_proof
DATABASE_URL=$U OBSERVAL_PG_USER_DELETION_URL=$U t tests/integration_clickhouse/test_user_deletion_postgres.py
```

Suites that share a database can run in any order and be rerun.

### 4. Remove the containers

```bash
docker stop observal-it-ch observal-it-ch4 observal-it-pg observal-it-pg4
```

The containers were started with `--rm`, so stopping them deletes all their data.

## Adding a suite

- Skip unless a suite-specific environment variable is set, and exclude nothing from `make test` beyond this directory.
- Check that the target host is local and that the database name is the one you expect, before writing anything.
- Prefer accepting any local port over a fixed one.
- Use unique IDs per run so reruns and shared databases do not collide.
- Add the suite to the table above, with what it does and does not prove.
