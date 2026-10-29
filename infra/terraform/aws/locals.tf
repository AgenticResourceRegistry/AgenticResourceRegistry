# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

locals {
  name = "${var.name_prefix}-${var.environment}"
  azs  = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  # BYO-VPC: when vpc_id is provided, use existing resources; otherwise create new ones.
  should_create_vpc = var.vpc_id == null

  vpc_id             = local.should_create_vpc ? module.vpc[0].vpc_id : var.vpc_id
  vpc_cidr           = data.aws_vpc.vpc.cidr_block
  private_subnet_ids = local.should_create_vpc ? module.vpc[0].private_subnet_ids : var.private_subnet_ids
  public_subnet_ids  = local.should_create_vpc ? module.vpc[0].public_subnet_ids : coalesce(var.public_subnet_ids, var.private_subnet_ids)

  # BYO Security Groups
  create_alb_sg = var.alb_security_group_id == null
  create_ecs_sg = var.ecs_security_group_id == null
  alb_sg_id     = local.create_alb_sg ? aws_security_group.alb[0].id : var.alb_security_group_id
  ecs_sg_id     = local.create_ecs_sg ? aws_security_group.ecs_tasks[0].id : var.ecs_security_group_id

  # ── Sizing presets ──────────────────────────────────────────────────────
  presets = {
    small = {
      api_cpu              = 256
      api_memory           = 512
      api_desired_count    = 1
      api_autoscale_min    = 1
      api_autoscale_max    = 3
      web_cpu              = 256
      web_memory           = 512
      web_desired_count    = 1
      web_autoscale_min    = 1
      web_autoscale_max    = 2
      worker_cpu           = 256
      worker_memory        = 512
      worker_desired_count = 1
      worker_autoscale_min = 1
      worker_autoscale_max = 2
      db_instance_class    = "db.t4g.micro"
      redis_node_type      = "cache.t4g.micro"
      data_instance_type   = "t3.medium"
      data_volume_size_gb  = 50
    }
    medium = {
      api_cpu              = 512
      api_memory           = 1024
      api_desired_count    = 2
      api_autoscale_min    = 2
      api_autoscale_max    = 10
      web_cpu              = 256
      web_memory           = 512
      web_desired_count    = 2
      web_autoscale_min    = 2
      web_autoscale_max    = 6
      worker_cpu           = 512
      worker_memory        = 1024
      worker_desired_count = 1
      worker_autoscale_min = 1
      worker_autoscale_max = 5
      db_instance_class    = "db.t4g.small"
      redis_node_type      = "cache.t4g.micro"
      data_instance_type   = "t3.large"
      data_volume_size_gb  = 100
    }
    large = {
      api_cpu              = 1024
      api_memory           = 2048
      api_desired_count    = 3
      api_autoscale_min    = 3
      api_autoscale_max    = 20
      web_cpu              = 512
      web_memory           = 1024
      web_desired_count    = 3
      web_autoscale_min    = 3
      web_autoscale_max    = 10
      worker_cpu           = 1024
      worker_memory        = 2048
      worker_desired_count = 2
      worker_autoscale_min = 2
      worker_autoscale_max = 10
      db_instance_class    = "db.r6g.large"
      redis_node_type      = "cache.r6g.large"
      data_instance_type   = "r6i.xlarge"
      data_volume_size_gb  = 500
    }
  }

  use_preset = var.sizing != "custom"

  effective_api_cpu              = local.use_preset ? local.presets[var.sizing].api_cpu : var.api_cpu
  effective_api_memory           = local.use_preset ? local.presets[var.sizing].api_memory : var.api_memory
  effective_api_desired_count    = local.use_preset ? local.presets[var.sizing].api_desired_count : var.api_desired_count
  effective_api_autoscale_min    = local.use_preset ? local.presets[var.sizing].api_autoscale_min : var.api_autoscale_min
  effective_api_autoscale_max    = local.use_preset ? local.presets[var.sizing].api_autoscale_max : var.api_autoscale_max
  effective_web_cpu              = local.use_preset ? local.presets[var.sizing].web_cpu : var.web_cpu
  effective_web_memory           = local.use_preset ? local.presets[var.sizing].web_memory : var.web_memory
  effective_web_desired_count    = local.use_preset ? local.presets[var.sizing].web_desired_count : var.web_desired_count
  effective_web_autoscale_min    = local.use_preset ? local.presets[var.sizing].web_autoscale_min : var.web_autoscale_min
  effective_web_autoscale_max    = local.use_preset ? local.presets[var.sizing].web_autoscale_max : var.web_autoscale_max
  effective_worker_cpu           = local.use_preset ? local.presets[var.sizing].worker_cpu : var.worker_cpu
  effective_worker_memory        = local.use_preset ? local.presets[var.sizing].worker_memory : var.worker_memory
  effective_worker_desired_count = local.use_preset ? local.presets[var.sizing].worker_desired_count : var.worker_desired_count
  effective_worker_autoscale_min = local.use_preset ? local.presets[var.sizing].worker_autoscale_min : var.worker_autoscale_min
  effective_worker_autoscale_max = local.use_preset ? local.presets[var.sizing].worker_autoscale_max : var.worker_autoscale_max
  effective_db_instance_class    = local.use_preset ? local.presets[var.sizing].db_instance_class : var.db_instance_class
  effective_redis_node_type      = local.use_preset ? local.presets[var.sizing].redis_node_type : var.redis_node_type
  effective_data_instance_type   = local.use_preset ? local.presets[var.sizing].data_instance_type : var.data_instance_type
  effective_data_volume_size_gb  = local.use_preset ? local.presets[var.sizing].data_volume_size_gb : var.data_volume_size_gb

  enable_tls = var.enable_tls && var.domain_name != "" && var.route53_zone_id != ""
  app_url    = var.domain_name != "" ? "${local.enable_tls ? "https" : "http"}://${var.domain_name}" : "http://${aws_lb.app.dns_name}"

  observability_prometheus_enabled = contains(["prometheus", "grafana"], var.observability_stack)
  observability_grafana_enabled    = var.observability_stack == "grafana"
  bundled_grafana_available        = local.observability_grafana_enabled

  # Internal DNS name for ClickHouse
  clickhouse_host_internal = "clickhouse.${var.internal_dns_zone}"

  ssm_prefix = "/${local.name}"
}

# Always fetch VPC metadata (works in both create and BYO modes).
data "aws_vpc" "vpc" {
  id = local.vpc_id
}

# Cross-variable validation: subnets required when using BYO-VPC.
resource "terraform_data" "byovpc_validation" {
  count = local.should_create_vpc ? 0 : 1

  lifecycle {
    precondition {
      condition     = var.private_subnet_ids != null && length(var.private_subnet_ids) >= 2
      error_message = "private_subnet_ids must be provided (at least 2) when vpc_id is set."
    }
    precondition {
      condition     = var.alb_scheme == "internal" || (var.public_subnet_ids != null && length(coalesce(var.public_subnet_ids, [])) >= 2)
      error_message = "public_subnet_ids must be provided (at least 2) when vpc_id is set and alb_scheme is 'internet-facing'. For private-only VPCs (e.g. TGW-based), set alb_scheme = 'internal'."
    }
    precondition {
      condition     = data.aws_vpc.vpc.enable_dns_hostnames && data.aws_vpc.vpc.enable_dns_support
      error_message = "BYO VPC must have enable_dns_hostnames and enable_dns_support enabled for private Route53 zone resolution."
    }
  }
}
