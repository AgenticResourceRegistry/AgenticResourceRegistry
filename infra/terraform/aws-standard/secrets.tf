# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
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

# ── SSM Parameter Store ───────────────────────────────────────────────────
# Connection URLs reference internal DNS names resolved via the private Route53 zone.

locals {
  connection_urls = {
    "DATABASE_URL"   = "postgresql+asyncpg://observal:${random_password.db.result}@postgres.${var.internal_dns_zone}:5432/observal"
    "REDIS_URL"      = "redis://redis.${var.internal_dns_zone}:6379"
    "CLICKHOUSE_URL" = "clickhouse://default:${random_password.clickhouse.result}@clickhouse.${var.internal_dns_zone}:8123/observal"
  }
}

resource "aws_ssm_parameter" "urls" {
  for_each = local.connection_urls

  name  = "${local.ssm_prefix}/${each.key}"
  type  = "SecureString"
  value = each.value

  tags = { Name = "${local.name}-${lower(each.key)}" }
}

resource "aws_ssm_parameter" "secret_key" {
  name  = "${local.ssm_prefix}/SECRET_KEY"
  type  = "SecureString"
  value = random_password.secret_key.result

  tags = { Name = "${local.name}-secret-key" }
}

resource "aws_ssm_parameter" "db_password" {
  name  = "${local.ssm_prefix}/DB_PASSWORD"
  type  = "SecureString"
  value = random_password.db.result

  tags = { Name = "${local.name}-db-password" }
}

resource "aws_ssm_parameter" "clickhouse_password" {
  name  = "${local.ssm_prefix}/CLICKHOUSE_PASSWORD"
  type  = "SecureString"
  value = random_password.clickhouse.result

  tags = { Name = "${local.name}-clickhouse-password" }
}
