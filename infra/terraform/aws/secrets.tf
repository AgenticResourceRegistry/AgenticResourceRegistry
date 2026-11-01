# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# ── Generated secrets ─────────────────────────────────────────────────────

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "random_password" "clickhouse" {
  length  = 32
  special = false
}

resource "random_password" "secret_key" {
  length  = 48
  special = false
}

resource "random_password" "grafana_admin" {
  length  = 24
  special = false
}

# ── SSM Parameter Store ───────────────────────────────────────────────────
# Two flavors:
#   1. Raw secrets (passwords) — referenced by user-data.sh on the data-tier EC2.
#   2. Pre-built connection URLs — injected into ECS tasks via the task
#      definition's `secrets` block. Pre-building avoids brittle entrypoint
#      logic that splices passwords into URLs at container start.

locals {
  raw_secrets = merge(
    {
      "DB_PASSWORD"         = random_password.db.result
      "CLICKHOUSE_PASSWORD" = random_password.clickhouse.result
      "SECRET_KEY"          = random_password.secret_key.result
    },
    local.observability_grafana_enabled ? { "GRAFANA_ADMIN_PASSWORD" = random_password.grafana_admin.result } : {}
  )

  derived_urls = {
    "DATABASE_URL"   = "postgresql+asyncpg://observal:${random_password.db.result}@${aws_db_instance.postgres.address}:${aws_db_instance.postgres.port}/observal"
    "REDIS_URL"      = "redis://${aws_elasticache_replication_group.redis.primary_endpoint_address}:${aws_elasticache_replication_group.redis.port}"
    "CLICKHOUSE_URL" = "clickhouse://default:${random_password.clickhouse.result}@${local.clickhouse_host_internal}:8123/observal"
  }
}

resource "aws_ssm_parameter" "app" {
  for_each = local.raw_secrets

  name  = "${local.ssm_prefix}/${each.key}"
  type  = "SecureString"
  value = each.value

  tags = { Name = "${local.name}-${lower(each.key)}" }
}

resource "aws_ssm_parameter" "urls" {
  for_each = local.derived_urls

  name  = "${local.ssm_prefix}/${each.key}"
  type  = "SecureString"
  value = each.value

  tags = { Name = "${local.name}-${lower(each.key)}" }
}
