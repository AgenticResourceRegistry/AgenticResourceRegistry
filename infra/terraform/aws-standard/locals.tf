# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

locals {
  name = "${var.name_prefix}-${var.environment}"
  azs  = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  # BYO-VPC
  should_create_vpc  = var.vpc_id == null
  vpc_id             = local.should_create_vpc ? aws_vpc.main[0].id : var.vpc_id
  vpc_cidr           = data.aws_vpc.vpc.cidr_block
  private_subnet_ids = local.should_create_vpc ? aws_subnet.private[*].id : var.private_subnet_ids
  public_subnet_ids  = local.should_create_vpc ? aws_subnet.public[*].id : coalesce(var.public_subnet_ids, var.private_subnet_ids)

  enable_tls = var.enable_tls && var.domain_name != "" && var.route53_zone_id != ""
  app_url    = var.domain_name != "" ? "${local.enable_tls ? "https" : "http"}://${var.domain_name}" : "http://${aws_lb.app.dns_name}"
  ssm_prefix = "/${local.name}"

  observability_prometheus_enabled = contains(["prometheus", "grafana"], var.observability_stack)
  observability_grafana_enabled    = var.observability_stack == "grafana"

  api_image = "${var.image_repo_api}:${var.image_tag}"
  web_image = "${var.image_repo_web}:${var.image_tag}"

  # ── Sizing presets ──────────────────────────────────────────────────────
  presets = {
    small = {
      ecs_instance_type    = "t3.large"
      api_cpu              = 512
      api_memory           = 1024
      api_desired_count    = 1
      web_cpu              = 256
      web_memory           = 512
      web_desired_count    = 1
      worker_cpu           = 256
      worker_memory        = 512
      worker_desired_count = 1
      data_instance_type   = "t3.medium"
      data_volume_size_gb  = 50
    }
    medium = {
      ecs_instance_type    = "t3.large"
      api_cpu              = 512
      api_memory           = 1024
      api_desired_count    = 1
      web_cpu              = 256
      web_memory           = 512
      web_desired_count    = 1
      worker_cpu           = 512
      worker_memory        = 1024
      worker_desired_count = 1
      data_instance_type   = "t3.medium"
      data_volume_size_gb  = 100
    }
  }

  use_preset = var.sizing != "custom"

  effective_ecs_instance_type    = local.use_preset ? local.presets[var.sizing].ecs_instance_type : var.ecs_instance_type
  effective_api_cpu              = local.use_preset ? local.presets[var.sizing].api_cpu : var.api_cpu
  effective_api_memory           = local.use_preset ? local.presets[var.sizing].api_memory : var.api_memory
  effective_api_desired_count    = local.use_preset ? local.presets[var.sizing].api_desired_count : var.api_desired_count
  effective_web_cpu              = local.use_preset ? local.presets[var.sizing].web_cpu : var.web_cpu
  effective_web_memory           = local.use_preset ? local.presets[var.sizing].web_memory : var.web_memory
  effective_web_desired_count    = local.use_preset ? local.presets[var.sizing].web_desired_count : var.web_desired_count
  effective_worker_cpu           = local.use_preset ? local.presets[var.sizing].worker_cpu : var.worker_cpu
  effective_worker_memory        = local.use_preset ? local.presets[var.sizing].worker_memory : var.worker_memory
  effective_worker_desired_count = local.use_preset ? local.presets[var.sizing].worker_desired_count : var.worker_desired_count
  effective_data_instance_type   = local.use_preset ? local.presets[var.sizing].data_instance_type : var.data_instance_type
  effective_data_volume_size_gb  = local.use_preset ? local.presets[var.sizing].data_volume_size_gb : var.data_volume_size_gb

  is_windows = length(regexall("^[a-zA-Z]:", abspath(path.root))) > 0

  windows_command = <<-EOT
    Write-Host "Waiting 180s for data-tier bootstrap to complete..."
    Start-Sleep -Seconds 180

    $task_arn = (aws ecs run-task `
      --region ${var.region} `
      --cluster ${aws_ecs_cluster.main.name} `
      --capacity-provider-strategy capacityProvider=${aws_ecs_capacity_provider.ec2.name},weight=1,base=1 `
      --task-definition ${aws_ecs_task_definition.init.arn} `
      --network-configuration "awsvpcConfiguration={subnets=[${join(",", local.private_subnet_ids)}],securityGroups=[${aws_security_group.ecs_instances.id}],assignPublicIp=DISABLED}" `
      --query "tasks[0].taskArn" --output text)
    Write-Host "Init task started: $task_arn"
    aws ecs wait tasks-stopped --region ${var.region} --cluster ${aws_ecs_cluster.main.name} --tasks $task_arn
    $exit_code = (aws ecs describe-tasks --region ${var.region} --cluster ${aws_ecs_cluster.main.name} --tasks $task_arn --query "tasks[0].containers[0].exitCode" --output text)
    Write-Host "Init task exit code: $exit_code"
    if ($exit_code -ne "0") {
      Write-Error "Init task failed. See log group ${aws_cloudwatch_log_group.ecs_init.name}."
      exit 1
    }
  EOT

  unix_command = <<-EOT
    set -euo pipefail

    # Give the data-host EC2 instance time to bootstrap services.
    echo "Waiting 180s for data-tier bootstrap to complete..."
    sleep 180

    task_arn=$(aws ecs run-task \
      --region ${var.region} \
      --cluster ${aws_ecs_cluster.main.name} \
      --capacity-provider-strategy capacityProvider=${aws_ecs_capacity_provider.ec2.name},weight=1,base=1 \
      --task-definition ${aws_ecs_task_definition.init.arn} \
      --network-configuration "awsvpcConfiguration={subnets=[${join(",", local.private_subnet_ids)}],securityGroups=[${aws_security_group.ecs_instances.id}],assignPublicIp=DISABLED}" \
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

data "aws_vpc" "vpc" {
  id = local.vpc_id
}

resource "terraform_data" "byovpc_dns_validation" {
  count = local.should_create_vpc ? 0 : 1

  lifecycle {
    precondition {
      condition     = var.private_subnet_ids != null && length(coalesce(var.private_subnet_ids, [])) >= 2
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
