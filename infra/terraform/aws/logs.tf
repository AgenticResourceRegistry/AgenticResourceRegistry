# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# Centralized CloudWatch log groups. Retention is governed by var.log_retention_days.
# CMKs can be supplied per-group for stricter compliance; defaults use AWS-managed encryption.

# tfsec:ignore:aws-cloudwatch-log-group-customer-key AWS-managed key by default.
resource "aws_cloudwatch_log_group" "ecs_api" {
  name              = "/aws/ecs/${local.name}/api"
  retention_in_days = var.log_retention_days

  tags = { Name = "${local.name}-ecs-api" }
}

# tfsec:ignore:aws-cloudwatch-log-group-customer-key AWS-managed key by default.
resource "aws_cloudwatch_log_group" "ecs_web" {
  name              = "/aws/ecs/${local.name}/web"
  retention_in_days = var.log_retention_days

  tags = { Name = "${local.name}-ecs-web" }
}

# tfsec:ignore:aws-cloudwatch-log-group-customer-key AWS-managed key by default.
resource "aws_cloudwatch_log_group" "ecs_worker" {
  name              = "/aws/ecs/${local.name}/worker"
  retention_in_days = var.log_retention_days

  tags = { Name = "${local.name}-ecs-worker" }
}

# tfsec:ignore:aws-cloudwatch-log-group-customer-key AWS-managed key by default.
resource "aws_cloudwatch_log_group" "ecs_init" {
  name              = "/aws/ecs/${local.name}/init"
  retention_in_days = var.log_retention_days

  tags = { Name = "${local.name}-ecs-init" }
}

# tfsec:ignore:aws-cloudwatch-log-group-customer-key AWS-managed key by default.
resource "aws_cloudwatch_log_group" "data_host" {
  name              = "/aws/ec2/${local.name}/data-host"
  retention_in_days = var.log_retention_days

  tags = { Name = "${local.name}-data-host" }
}

# tfsec:ignore:aws-cloudwatch-log-group-customer-key AWS-managed key by default.
resource "aws_cloudwatch_log_group" "redis_slow" {
  name              = "/aws/elasticache/${local.name}/redis/slow"
  retention_in_days = var.log_retention_days

  tags = { Name = "${local.name}-redis-slow" }
}
