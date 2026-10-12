# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# ECS Fargate stack: api, web, worker, init.
#
# api + worker share the same image (different commands). web has its own image.
# init runs migrations + seeds and is invoked as a one-shot RunTask whenever
# image_tag changes (or on first apply when run_init_on_apply is true).

resource "aws_ecs_cluster" "main" {
  name = "${local.name}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = { Name = "${local.name}-cluster" }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 1
  }
}

# ── Common config injected into every Observal task ───────────────────────

locals {
  api_image = "${var.image_repo_api}:${var.image_tag}"
  web_image = "${var.image_repo_web}:${var.image_tag}"

  # Non-secret env vars passed to api/worker/init.
  app_environment = concat([
    { name = "NEXT_PUBLIC_API_URL", value = local.app_url },
    { name = "JWT_KEY_DIR", value = "/tmp/keys" },
    ], var.demo_super_admin_email != "" ? [
    { name = "DEMO_SUPER_ADMIN_EMAIL", value = var.demo_super_admin_email },
    { name = "DEMO_SUPER_ADMIN_PASSWORD", value = var.demo_super_admin_password },
    { name = "DEMO_ADMIN_EMAIL", value = var.demo_admin_email },
    { name = "DEMO_ADMIN_PASSWORD", value = var.demo_admin_password },
    { name = "DEMO_REVIEWER_EMAIL", value = var.demo_reviewer_email },
    { name = "DEMO_REVIEWER_PASSWORD", value = var.demo_reviewer_password },
    { name = "DEMO_USER_EMAIL", value = var.demo_user_email },
    { name = "DEMO_USER_PASSWORD", value = var.demo_user_password },
    ] : [], local.webhook_ingress_enabled ? [
    { name = "WEBHOOK_PUBLIC_URL", value = local.webhook_public_url },
  ] : [])

  # Secrets injected by ECS at task start. Reference SSM Parameter Store ARNs.
  app_secrets = [
    { name = "DATABASE_URL", valueFrom = aws_ssm_parameter.urls["DATABASE_URL"].arn },
    { name = "REDIS_URL", valueFrom = aws_ssm_parameter.urls["REDIS_URL"].arn },
    { name = "CLICKHOUSE_URL", valueFrom = aws_ssm_parameter.urls["CLICKHOUSE_URL"].arn },
    { name = "SECRET_KEY", valueFrom = aws_ssm_parameter.app["SECRET_KEY"].arn },
  ]
}

# ── Task: init (one-shot, runs entrypoint.sh) ─────────────────────────────

resource "aws_ecs_task_definition" "init" {
  family                   = "${local.name}-init"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"

  execution_role_arn = aws_iam_role.ecs_execution.arn
  task_role_arn      = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name      = "init"
    image     = local.api_image
    essential = true
    command   = ["/app/entrypoint.sh"]
    environment = concat(local.app_environment, [
      { name = "SKIP_DDL_ON_STARTUP", value = "false" },
    ])
    secrets = local.app_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.ecs_init.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "init"
      }
    }
    readonlyRootFilesystem = true
    linuxParameters = {
      initProcessEnabled = true
      tmpfs              = [{ containerPath = "/tmp", size = 128, mountOptions = ["rw"] }]
    }
  }])

  tags = { Name = "${local.name}-init" }
}

# ── Task: api ─────────────────────────────────────────────────────────────

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(local.effective_api_cpu)
  memory                   = tostring(local.effective_api_memory)

  execution_role_arn = aws_iam_role.ecs_execution.arn
  task_role_arn      = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name      = "api"
    image     = local.api_image
    essential = true
    command = [
      "/app/.venv/bin/python", "-m", "uvicorn", "main:app",
      "--host", "0.0.0.0", "--port", "8000",
      "--workers", "2",
      "--proxy-headers", "--forwarded-allow-ips", "*",
    ]
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    environment = concat(local.app_environment, [
      { name = "SKIP_DDL_ON_STARTUP", value = "true" },
    ])
    secrets = local.app_secrets
    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/readyz')\" || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.ecs_api.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "api"
      }
    }
    readonlyRootFilesystem = true
    linuxParameters = {
      initProcessEnabled = true
      tmpfs              = [{ containerPath = "/tmp", size = 256, mountOptions = ["rw"] }]
    }
  }])

  tags = { Name = "${local.name}-api" }
}

# ── Task: worker ──────────────────────────────────────────────────────────

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name}-worker"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(local.effective_worker_cpu)
  memory                   = tostring(local.effective_worker_memory)

  execution_role_arn = aws_iam_role.ecs_execution.arn
  task_role_arn      = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name      = "worker"
    image     = local.api_image
    essential = true
    command = [
      "/app/.venv/bin/python", "-c",
      "import asyncio; asyncio.set_event_loop(asyncio.new_event_loop()); from arq import run_worker; from worker import WorkerSettings; run_worker(WorkerSettings)",
    ]
    environment = local.app_environment
    secrets     = local.app_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.ecs_worker.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "worker"
      }
    }
    readonlyRootFilesystem = true
    linuxParameters = {
      initProcessEnabled = true
      tmpfs              = [{ containerPath = "/tmp", size = 256, mountOptions = ["rw"] }]
    }
  }])

  tags = { Name = "${local.name}-worker" }
}

# ── Task: web ─────────────────────────────────────────────────────────────

resource "aws_ecs_task_definition" "web" {
  family                   = "${local.name}-web"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(local.effective_web_cpu)
  memory                   = tostring(local.effective_web_memory)

  execution_role_arn = aws_iam_role.ecs_execution.arn
  task_role_arn      = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name         = "web"
    image        = local.web_image
    essential    = true
    portMappings = [{ containerPort = 3000, protocol = "tcp" }]
    # Override entrypoint to skip nginx's docker-entrypoint.sh which runs
    # envsubst on config files (breaks $uri/$uri/ in nginx directives).
    # Write ECS-safe nginx config (no proxy_pass to Docker Compose hostnames
    # that don't exist in Fargate). The ALB routes /api/* and /auth/* to the
    # API target group before reaching this container.
    entryPoint = ["/bin/sh", "-c"]
    command = [
      "printf 'server {\\n  listen 3000;\\n  root /usr/share/nginx/html;\\n  index index.html;\\n  location /assets/ {\\n    expires 1y;\\n    add_header Cache-Control \"public, immutable\" always;\\n  }\\n  location / {\\n    try_files $uri $uri/ /index.html;\\n  }\\n}\\n' > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"
    ]
    environment = [
      { name = "NEXT_PUBLIC_API_URL", value = local.app_url },
      { name = "PORT", value = "3000" },
    ]
    healthCheck = {
      command     = ["CMD-SHELL", "wget -q --spider http://localhost:3000/ || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 20
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.ecs_web.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "web"
      }
    }
    readonlyRootFilesystem = true
    linuxParameters = {
      initProcessEnabled = true
      tmpfs = [
        { containerPath = "/tmp", size = 64, mountOptions = ["rw"] },
        { containerPath = "/var/cache/nginx", size = 64, mountOptions = ["rw"] },
        { containerPath = "/var/run", size = 8, mountOptions = ["rw"] },
        { containerPath = "/etc/nginx/conf.d", size = 8, mountOptions = ["rw"] },
      ]
    }
  }])

  tags = { Name = "${local.name}-web" }
}

# ── Services ──────────────────────────────────────────────────────────────

resource "aws_ecs_service" "api" {
  name            = "${local.name}-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  launch_type     = "FARGATE"
  desired_count   = local.effective_api_desired_count

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 60

  network_configuration {
    subnets          = local.private_subnet_ids
    security_groups  = [local.ecs_sg_id]
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
  launch_type     = "FARGATE"
  desired_count   = local.effective_web_desired_count

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 30

  network_configuration {
    subnets          = local.private_subnet_ids
    security_groups  = [local.ecs_sg_id]
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
  launch_type     = "FARGATE"
  desired_count   = local.effective_worker_desired_count

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  network_configuration {
    subnets          = local.private_subnet_ids
    security_groups  = [local.ecs_sg_id]
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
# the AWS CLI configured — same prerequisite as `terraform apply` itself.

resource "null_resource" "run_init" {
  count = var.run_init_on_apply ? 1 : 0

  triggers = {
    image_tag      = var.image_tag
    task_def       = aws_ecs_task_definition.init.arn
    cluster        = aws_ecs_cluster.main.name
    db_endpoint    = aws_db_instance.postgres.address
    redis_endpoint = aws_elasticache_replication_group.redis.primary_endpoint_address
  }

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail

      # Give the data-host EC2 instance time to bootstrap ClickHouse.
      # user-data takes 2-4 minutes (package install + docker pull + start).
      # The init task's entrypoint.sh also retries 15 times with backoff,
      # so this is a best-effort head start, not a hard gate.
      echo "Waiting 120s for data-tier bootstrap to complete..."
      sleep 120

      task_arn=$(aws ecs run-task \
        --region ${var.region} \
        --cluster ${aws_ecs_cluster.main.name} \
        --launch-type FARGATE \
        --task-definition ${aws_ecs_task_definition.init.arn} \
        --network-configuration "awsvpcConfiguration={subnets=[${join(",", local.private_subnet_ids)}],securityGroups=[${local.ecs_sg_id}],assignPublicIp=DISABLED}" \
        --query 'tasks[0].taskArn' --output text)
      echo "Init task started: $task_arn"
      aws ecs wait tasks-stopped --region ${var.region} --cluster ${aws_ecs_cluster.main.name} --tasks "$task_arn"
      exit_code=$(aws ecs describe-tasks --region ${var.region} --cluster ${aws_ecs_cluster.main.name} --tasks "$task_arn" --query 'tasks[0].containers[0].exitCode' --output text)
      echo "Init task exit code: $exit_code"
      if [ "$exit_code" != "0" ]; then
        echo "Init task failed. See log group ${aws_cloudwatch_log_group.ecs_init.name}." >&2
        exit 1
      fi
    EOT
  }

  depends_on = [
    aws_db_instance.postgres,
    aws_elasticache_replication_group.redis,
    aws_ecs_cluster.main,
    aws_iam_role_policy_attachment.ecs_execution_managed,
    aws_iam_role_policy_attachment.ecs_execution_secrets,
    aws_instance.data_host,
    aws_route53_record.clickhouse_internal,
  ]
}

# ── Service autoscaling ────────────────────────────────────────────────────

resource "aws_appautoscaling_target" "api" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.api.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = local.effective_api_autoscale_min
  max_capacity       = local.effective_api_autoscale_max
}

resource "aws_appautoscaling_policy" "api_cpu" {
  name               = "${local.name}-api-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.api.service_namespace
  resource_id        = aws_appautoscaling_target.api.resource_id
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value = var.service_autoscale_cpu_target
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    scale_in_cooldown  = 120
    scale_out_cooldown = 60
  }
}

resource "aws_appautoscaling_target" "web" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.web.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = local.effective_web_autoscale_min
  max_capacity       = local.effective_web_autoscale_max
}

resource "aws_appautoscaling_policy" "web_cpu" {
  name               = "${local.name}-web-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.web.service_namespace
  resource_id        = aws_appautoscaling_target.web.resource_id
  scalable_dimension = aws_appautoscaling_target.web.scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value = var.service_autoscale_cpu_target
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    scale_in_cooldown  = 120
    scale_out_cooldown = 60
  }
}

resource "aws_appautoscaling_target" "worker" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.worker.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = local.effective_worker_autoscale_min
  max_capacity       = local.effective_worker_autoscale_max
}

resource "aws_appautoscaling_policy" "worker_cpu" {
  name               = "${local.name}-worker-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value = var.service_autoscale_cpu_target
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    scale_in_cooldown  = 120
    scale_out_cooldown = 60
  }
}
