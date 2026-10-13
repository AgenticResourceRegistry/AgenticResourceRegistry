# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# ECS services using EC2 capacity provider strategy.

resource "aws_ecs_service" "api" {
  name            = "${local.name}-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = local.effective_api_desired_count

  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.ec2.name
    weight            = 1
    base              = 1
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 60

  network_configuration {
    subnets          = local.private_subnet_ids
    security_groups  = [aws_security_group.ecs_instances.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  lifecycle {
    ignore_changes = [desired_count]
  }

  depends_on = [
    aws_lb_listener.http,
    null_resource.run_init,
  ]

  tags = { Name = "${local.name}-api" }
}

resource "aws_ecs_service" "web" {
  name            = "${local.name}-web"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = local.effective_web_desired_count

  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.ec2.name
    weight            = 1
    base              = 0
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 30

  network_configuration {
    subnets          = local.private_subnet_ids
    security_groups  = [aws_security_group.ecs_instances.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 3000
  }

  lifecycle {
    ignore_changes = [desired_count]
  }

  depends_on = [aws_lb_listener.http]

  tags = { Name = "${local.name}-web" }
}

resource "aws_ecs_service" "worker" {
  name            = "${local.name}-worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = local.effective_worker_desired_count

  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.ec2.name
    weight            = 1
    base              = 0
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  network_configuration {
    subnets          = local.private_subnet_ids
    security_groups  = [aws_security_group.ecs_instances.id]
    assign_public_ip = false
  }

  lifecycle {
    ignore_changes = [desired_count]
  }

  depends_on = [null_resource.run_init]

  tags = { Name = "${local.name}-worker" }
}

# ── One-shot init task (migrations + seeds) ───────────────────────────────
# Triggers on every image_tag change. Uses local-exec so the user must have
# the AWS CLI configured.

resource "null_resource" "run_init" {
  count = var.run_init_on_apply ? 1 : 0

  triggers = {
    image_tag = var.image_tag
    task_def  = aws_ecs_task_definition.init.arn
    cluster   = aws_ecs_cluster.main.name
  }

  provisioner "local-exec" {
    interpreter = local.is_windows ? ["powershell", "-Command"] : ["/bin/bash", "-c"]
    command     = local.is_windows ? local.windows_command : local.unix_command
  }

  depends_on = [
    aws_ecs_cluster.main,
    aws_ecs_cluster_capacity_providers.main,
    aws_autoscaling_group.ecs,
    aws_iam_role_policy_attachment.ecs_execution_managed,
    aws_iam_role_policy_attachment.ecs_execution_secrets,
    aws_instance.data_host,
    aws_route53_record.postgres_internal,
    aws_route53_record.redis_internal,
    aws_route53_record.clickhouse_internal,
  ]
}
