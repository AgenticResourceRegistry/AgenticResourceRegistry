<!-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Cost Intelligence demo stack

> **Temporary: remove `scripts/cost-demo/` before this PR is merged.** It exists so reviewers can see the Cost Intelligence tab with realistic data while the work is in progress; it is not meant to ship in `main`.

The demo kit in `scripts/cost-demo/` runs a throwaway stack (separate compose project and ports 15432 / 18123 / 16379 / 18000 / 13000) with synthetic data. It cannot touch your main stack, and its env file redirects `HOME` and `JWT_KEY_DIR` so your real `~/.observal` keys are never read.

```bash
# 1. databases
docker compose -f scripts/cost-demo/compose.yml up -d

# 2. schema (the alembic chain does not start from an empty DB, so mirror the entrypoint:
#    create tables at revision 041, then run the 042 migration for real)
cd observal-server
source ../scripts/cost-demo/env.sh
.venv/bin/python -c "
import asyncio
from sqlalchemy import text
from database import engine
from models import Base
async def init():
    async with engine.begin() as conn:
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS pg_trgm'))
        tables = [t for n, t in Base.metadata.tables.items() if n != 'cost_opportunities']
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    await engine.dispose()
asyncio.run(init())"
.venv/bin/python -m alembic stamp 041_registry_forks
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m services.clickhouse.migrations

# 3. API
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 18000 &

# 4. seed (DESTRUCTIVE to the demo DBs only; refuses any other ports)
PYTHONPATH=. .venv/bin/python ../scripts/cost-demo/seed.py

# 5. web
cd ../web && pnpm install && pnpm exec vite --config ../scripts/cost-demo/vite.demo.config.ts
# open http://localhost:13000/login  ->  admin@verify.local / costdemo-2026
# Administration -> Dashboard -> Cost Intelligence

# teardown
docker compose -f ../scripts/cost-demo/compose.yml down -v
git checkout -- src/routeTree.gen.ts   # the dev server regenerates it
```

The seed creates a staged month: billing configured (Claude Code 3 seats at $100, Kiro 2 seats at $20), detection run, the `ultra-pi` routing opportunity approved and "shipped" in 1.2.0, so identified, actioned and measured savings all show.

Tests: `make test` (unit, mocks ClickHouse), `tests/test_cost_intelligence.py`, `tests/test_cost_billing_and_dedup.py`, and `tests/e2e/cost-intelligence.spec.ts` (needs the full stack; not yet run).
