# SPDX-FileCopyrightText: 2026 Observal
# SPDX-License-Identifier: Apache-2.0

# ── ALB ────────────────────────────────────────────────────────────────────
resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Public ingress to the load balancer."
  vpc_id      = local.vpc_id

  ingress {
    description = "HTTP from approved CIDRs"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.alb_ingress_cidrs
  }

  ingress {
    description = "HTTPS from approved CIDRs"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.alb_ingress_cidrs
  }

  dynamic "ingress" {
    for_each = local.webhook_ingress_enabled ? [1] : []
    content {
      description     = "Git provider webhooks from the API Gateway VPC link"
      from_port       = local.webhook_alb_port
      to_port         = local.webhook_alb_port
      protocol        = "tcp"
      security_groups = [aws_security_group.webhook_vpc_link[0].id]
    }
  }

  egress {
    description = "All egress (load balancer to targets)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-alb-sg" }
}

# ── ECS instances (api/web/worker) ────────────────────────────────────────
resource "aws_security_group" "ecs_instances" {
  name        = "${local.name}-ecs-instances"
  description = "ECS EC2 instances running api/web/worker. Inbound from ALB only."
  vpc_id      = local.vpc_id

  ingress {
    description     = "API HTTP from ALB"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description     = "Web HTTP from ALB"
    from_port       = 3000
    to_port         = 3000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    description = "All egress"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-ecs-instances-sg" }
}

# ── Data host (Postgres, Redis, ClickHouse, optional observability) ───────
resource "aws_security_group" "data_host" {
  name        = "${local.name}-data-host"
  description = "Data tier EC2: Postgres, Redis, ClickHouse, Grafana, Prometheus."
  vpc_id      = local.vpc_id

  ingress {
    description     = "Postgres from ECS instances"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_instances.id]
  }

  ingress {
    description     = "Redis from ECS instances"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_instances.id]
  }

  ingress {
    description     = "ClickHouse HTTP from ECS instances"
    from_port       = 8123
    to_port         = 8123
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_instances.id]
  }

  ingress {
    description     = "ClickHouse native protocol from ECS instances"
    from_port       = 9000
    to_port         = 9000
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_instances.id]
  }

  dynamic "ingress" {
    for_each = local.observability_grafana_enabled ? [1] : []
    content {
      description     = "Grafana UI from ALB"
      from_port       = 3001
      to_port         = 3001
      protocol        = "tcp"
      security_groups = [aws_security_group.alb.id]
    }
  }

  egress {
    description = "All egress (image pulls, OS updates, AWS APIs)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-data-host-sg" }
}
