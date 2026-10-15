# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# ── ALB ────────────────────────────────────────────────────────────────────
resource "aws_security_group" "alb" {
  count       = local.create_alb_sg ? 1 : 0
  name        = "${local.name}-alb"
  description = "Public ingress to the load balancer."
  vpc_id      = local.vpc_id

  # tfsec:ignore:aws-ec2-no-public-ingress-sgr ALB is internet-facing by design; restrict via var.alb_ingress_cidrs.
  ingress {
    description = "HTTP from approved CIDRs"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.alb_ingress_cidrs
  }

  # tfsec:ignore:aws-ec2-no-public-ingress-sgr ALB is internet-facing by design; restrict via var.alb_ingress_cidrs.
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

  # tfsec:ignore:aws-ec2-no-public-egress-sgr ALB egress is constrained by routing to in-VPC targets.
  egress {
    description = "All egress (load balancer to targets)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-alb-sg" }
}

# ── ECS task ENIs (api/web/worker) ─────────────────────────────────────────
resource "aws_security_group" "ecs_tasks" {
  count       = local.create_ecs_sg ? 1 : 0
  name        = "${local.name}-ecs-tasks"
  description = "Fargate task ENIs for api/web/worker. Inbound only from ALB."
  vpc_id      = local.vpc_id

  ingress {
    description     = "API HTTP from ALB"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [local.alb_sg_id]
  }

  ingress {
    description     = "Web HTTP from ALB"
    from_port       = 3000
    to_port         = 3000
    protocol        = "tcp"
    security_groups = [local.alb_sg_id]
  }

  # tfsec:ignore:aws-ec2-no-public-egress-sgr Required: image pulls (ghcr.io), AWS API endpoints (SSM, CloudWatch, ECR), Postgres/Redis/ClickHouse in-VPC.
  egress {
    description = "All egress"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-ecs-tasks-sg" }
}

# ── Data tier EC2 (ClickHouse plus optional observability) ─────────────────
resource "aws_security_group" "data_host" {
  name        = "${local.name}-data-host"
  description = "ClickHouse EC2 with optional observability. Inbound from ALB and ECS tasks."
  vpc_id      = local.vpc_id

  dynamic "ingress" {
    for_each = local.bundled_grafana_available ? [1] : []
    content {
      description     = "Grafana UI from ALB"
      from_port       = 3001
      to_port         = 3001
      protocol        = "tcp"
      security_groups = [local.alb_sg_id]
    }
  }

  ingress {
    description     = "ClickHouse HTTP from ECS tasks"
    from_port       = 8123
    to_port         = 8123
    protocol        = "tcp"
    security_groups = [local.ecs_sg_id]
  }

  ingress {
    description     = "ClickHouse native protocol from ECS tasks"
    from_port       = 9000
    to_port         = 9000
    protocol        = "tcp"
    security_groups = [local.ecs_sg_id]
  }

  # tfsec:ignore:aws-ec2-no-public-egress-sgr Required: image pulls (ghcr.io), GitHub release tarball, SSM/EC2/CloudWatch endpoints, OS package mirrors.
  egress {
    description = "All egress (image pulls, OS updates, AWS APIs)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-data-host-sg" }
}

# ── RDS Postgres ───────────────────────────────────────────────────────────
resource "aws_security_group" "db" {
  name        = "${local.name}-db"
  description = "Postgres reachable only from ECS tasks."
  vpc_id      = local.vpc_id

  ingress {
    description     = "Postgres from ECS tasks"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [local.ecs_sg_id]
  }

  egress {
    description = "Egress within VPC only"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [local.vpc_cidr]
  }

  tags = { Name = "${local.name}-db-sg" }
}

# ── ElastiCache Redis ──────────────────────────────────────────────────────
resource "aws_security_group" "redis" {
  name        = "${local.name}-redis"
  description = "Redis reachable from ECS tasks (api + worker)."
  vpc_id      = local.vpc_id

  ingress {
    description     = "Redis from ECS tasks"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [local.ecs_sg_id]
  }

  egress {
    description = "Egress within VPC only"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [local.vpc_cidr]
  }

  tags = { Name = "${local.name}-redis-sg" }
}
