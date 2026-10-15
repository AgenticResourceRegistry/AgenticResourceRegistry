# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
#
# Source before running the API, migrations or the seed against the demo stack.
# HOME and JWT_KEY_DIR point at a throwaway directory so nothing reads or
# writes your real ~/.observal (including your real JWT signing keys).
DEMO_DIR="${TMPDIR:-/tmp}/observal-cost-demo"
mkdir -p "$DEMO_DIR/home" "$DEMO_DIR/keys"
export HOME="$DEMO_DIR/home"
export JWT_KEY_DIR="$DEMO_DIR/keys"
export DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:15432/observal
export CLICKHOUSE_URL=clickhouse://default:clickhouse@127.0.0.1:18123/observal
export REDIS_URL=redis://127.0.0.1:16379
export SECRET_KEY=cost-demo-local-only
export USAGE_PING_DEPLOYMENT_TYPE=development
