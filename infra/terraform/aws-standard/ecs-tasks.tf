# SPDX-FileCopyrightText: 2026 Observal
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# ECS task definitions: api, web, worker, init.
# All run on EC2 capacity provider with awsvpc networking.

# ── Common config injected into every Observal task ───────────────────────

locals {
  # Non-secret env vars passed to api/worker/init.
  app_environment = concat([
    { name = "NEXT_PUBLIC_API_URL", value = local.app_url },
    { name = "JWT_KEY_DIR", value = "/tmp/keys" },
    { name = "DEMO_SUPER_ADMIN_EMAIL", value = "super@demo.example" },
    { name = "DEMO_SUPER_ADMIN_PASSWORD", value = "super-changeme" },
    { name = "DEMO_ADMIN_EMAIL", value = "admin@demo.example" },
    { name = "DEMO_ADMIN_PASSWORD", value = "admin-changeme" },
    { name = "DEMO_REVIEWER_EMAIL", value = "reviewer@demo.example" },
    { name = "DEMO_REVIEWER_PASSWORD", value = "reviewer-changeme" },
    { name = "DEMO_USER_EMAIL", value = "user@demo.example" },
    { name = "DEMO_USER_PASSWORD", value = "user-changeme" },
    ], local.webhook_ingress_enabled ? [
    { name = "WEBHOOK_PUBLIC_URL", value = local.webhook_public_url },
  ] : [])

  # Secrets injected by ECS at task start via SSM Parameter Store ARNs.
  app_secrets = [
    { name = "DATABASE_URL", valueFrom = aws_ssm_parameter.urls["DATABASE_URL"].arn },
    { name = "REDIS_URL", valueFrom = aws_ssm_parameter.urls["REDIS_URL"].arn },
    { name = "CLICKHOUSE_URL", valueFrom = aws_ssm_parameter.urls["CLICKHOUSE_URL"].arn },
    { name = "SECRET_KEY", valueFrom = aws_ssm_parameter.secret_key.arn },
  ]
}

# ── Task: init (one-shot, runs entrypoint.sh) ─────────────────────────────

resource "aws_ecs_task_definition" "init" {
  family                   = "${local.name}-init"
  network_mode             = "awsvpc"
  requires_compatibilities = ["EC2"]
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
    linuxParameters = {
      initProcessEnabled = true
    }
  }])

  tags = { Name = "${local.name}-init" }
}

# ── Task: api ─────────────────────────────────────────────────────────────

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  network_mode             = "awsvpc"
  requires_compatibilities = ["EC2"]
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
    linuxParameters = {
      initProcessEnabled = true
    }
  }])

  tags = { Name = "${local.name}-api" }
}

# ── Task: web ─────────────────────────────────────────────────────────────

resource "aws_ecs_task_definition" "web" {
  family                   = "${local.name}-web"
  network_mode             = "awsvpc"
  requires_compatibilities = ["EC2"]
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
    # that don't exist in ECS). The ALB routes /api/* and /auth/* to the
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
    linuxParameters = {
      initProcessEnabled = true
    }
  }])

  tags = { Name = "${local.name}-web" }
}

# ── Task: worker ──────────────────────────────────────────────────────────

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name}-worker"
  network_mode             = "awsvpc"
  requires_compatibilities = ["EC2"]
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
    linuxParameters = {
      initProcessEnabled = true
    }
  }])

  tags = { Name = "${local.name}-worker" }
}
