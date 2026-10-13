# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# Centralized CloudWatch log groups for ECS tasks and the data host.

resource "aws_cloudwatch_log_group" "ecs_api" {
  name              = "/aws/ecs/${local.name}/api"
  retention_in_days = var.log_retention_days

  tags = { Name = "${local.name}-ecs-api" }
}

resource "aws_cloudwatch_log_group" "ecs_web" {
  name              = "/aws/ecs/${local.name}/web"
  retention_in_days = var.log_retention_days

  tags = { Name = "${local.name}-ecs-web" }
}

resource "aws_cloudwatch_log_group" "ecs_worker" {
  name              = "/aws/ecs/${local.name}/worker"
  retention_in_days = var.log_retention_days

  tags = { Name = "${local.name}-ecs-worker" }
}

resource "aws_cloudwatch_log_group" "ecs_init" {
  name              = "/aws/ecs/${local.name}/init"
  retention_in_days = var.log_retention_days

  tags = { Name = "${local.name}-ecs-init" }
}

resource "aws_cloudwatch_log_group" "data_host" {
  name              = "/aws/ec2/${local.name}/data-host"
  retention_in_days = var.log_retention_days

  tags = { Name = "${local.name}-data-host" }
}
