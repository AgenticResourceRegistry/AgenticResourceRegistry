# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

variable "region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name (e.g. prod, staging). Drives prod-only toggles (RDS Multi-AZ, ElastiCache replication, deletion protection)."
  type        = string
  default     = "prod"
}

variable "name_prefix" {
  description = "Prefix applied to all resource names."
  type        = string
  default     = "observal"
}

# ── Network ────────────────────────────────────────────────────────────────

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.42.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "CIDRs for public subnets (one per AZ). Must match az_count."
  type        = list(string)
  default     = ["10.42.0.0/24", "10.42.1.0/24"]
}

variable "private_subnet_cidrs" {
  description = "CIDRs for private subnets (one per AZ). Must match az_count."
  type        = list(string)
  default     = ["10.42.10.0/24", "10.42.11.0/24"]
}

variable "az_count" {
  description = "Number of availability zones to span. ALB and RDS subnet groups need >= 2."
  type        = number
  default     = 2
}

variable "internal_dns_zone" {
  description = "Private Route 53 zone for VPC-internal DNS (e.g. clickhouse.observal.internal)."
  type        = string
  default     = "observal.internal"
}

# ── DNS / TLS ──────────────────────────────────────────────────────────────

variable "domain_name" {
  description = "Public hostname for the install (e.g. observal.example.com). Leave empty to expose only the ALB DNS name over HTTP."
  type        = string
  default     = ""
}

variable "route53_zone_id" {
  description = "Route 53 hosted zone ID for domain_name. Required if domain_name is set."
  type        = string
  default     = ""
}

variable "enable_tls" {
  description = "Enable TLS via ACM. Requires a publicly-resolvable Route53 zone for DNS validation. Set false for private zones."
  type        = bool
  default     = true
}

# ── Sizing preset ─────────────────────────────────────────────────────────
# Pick a preset to configure all resource sizes at once. Individual resource
# variables (api_cpu, db_instance_class, etc.) are IGNORED when sizing != "custom".
#
# IMPORTANT: Presets and individual vars are mutually exclusive.
# When sizing = "small|medium|large", individual resource variables have no effect.
# Set sizing = "custom" to control each resource variable independently.
#
# Presets:
#   small  (~$150/mo) — 1× api, 1× web, 1× worker, t3.medium data, db.t4g.micro
#   medium (~$255/mo) — 2× api, 2× web, 1× worker, t3.large data, db.t4g.small
#   large  (~$600/mo) — 3× api, 3× web, 2× worker, r6i.xlarge data, db.r6g.large

variable "sizing" {
  description = "Resource sizing preset. Set 'custom' to use individual resource variables instead."
  type        = string
  default     = "medium"

  validation {
    condition     = contains(["small", "medium", "large", "custom"], var.sizing)
    error_message = "sizing must be 'small', 'medium', 'large', or 'custom'."
  }
}

# ── ECS Fargate (api / web / worker / init) ────────────────────────────────

variable "image_repo_api" {
  description = "Container image repository for api + worker + init (they share an image)."
  type        = string
  default     = "ghcr.io/observal/observal-api"
}

variable "image_repo_web" {
  description = "Container image repository for the Next.js web frontend."
  type        = string
  default     = "ghcr.io/observal/observal-web"
}

variable "image_tag" {
  description = "Tag of the Observal images to deploy. Bump and re-apply to roll out a new release."
  type        = string
  default     = "latest"
}

variable "api_cpu" {
  description = "Fargate CPU units for the api task (1024 = 1 vCPU)."
  type        = number
  default     = 512
}

variable "api_memory" {
  description = "Fargate memory (MB) for the api task."
  type        = number
  default     = 1024
}

variable "api_desired_count" {
  description = "Baseline number of api tasks. Autoscaling overrides between min/max."
  type        = number
  default     = 2
}

variable "api_autoscale_min" {
  description = "Minimum number of api tasks under autoscaling."
  type        = number
  default     = 2
}

variable "api_autoscale_max" {
  description = "Maximum number of api tasks under autoscaling."
  type        = number
  default     = 10
}

variable "web_cpu" {
  description = "Fargate CPU units for the web task."
  type        = number
  default     = 256
}

variable "web_memory" {
  description = "Fargate memory (MB) for the web task."
  type        = number
  default     = 512
}

variable "web_desired_count" {
  description = "Baseline number of web tasks."
  type        = number
  default     = 2
}

variable "web_autoscale_min" {
  description = "Minimum number of web tasks under autoscaling."
  type        = number
  default     = 2
}

variable "web_autoscale_max" {
  description = "Maximum number of web tasks under autoscaling."
  type        = number
  default     = 6
}

variable "worker_cpu" {
  description = "Fargate CPU units for the worker task."
  type        = number
  default     = 512
}

variable "worker_memory" {
  description = "Fargate memory (MB) for the worker task."
  type        = number
  default     = 1024
}

variable "worker_desired_count" {
  description = "Baseline number of worker tasks."
  type        = number
  default     = 1
}

variable "worker_autoscale_min" {
  description = "Minimum number of worker tasks under autoscaling."
  type        = number
  default     = 1
}

variable "worker_autoscale_max" {
  description = "Maximum number of worker tasks under autoscaling."
  type        = number
  default     = 5
}

variable "service_autoscale_cpu_target" {
  description = "Target CPU utilization (%) for ECS service autoscaling."
  type        = number
  default     = 65
}

variable "run_init_on_apply" {
  description = "Run the migrations/seed task as a one-shot Fargate task whenever image_tag changes."
  type        = bool
  default     = true
}

# ── Data tier (ClickHouse plus optional observability on EC2) ───────────────

variable "data_instance_type" {
  description = "EC2 instance type for the ClickHouse data host. 8 GB RAM is the floor for light use."
  type        = string
  default     = "t3.large"
}

variable "data_volume_size_gb" {
  description = "Size of the EBS volume mounted at /data on the data tier host."
  type        = number
  default     = 100
}

# ── Managed data services ──────────────────────────────────────────────────

variable "db_instance_class" {
  description = "RDS instance class for Postgres."
  type        = string
  default     = "db.t4g.small"
}

variable "db_allocated_storage_gb" {
  description = "Allocated storage for RDS (GB). Auto-scales up to db_max_allocated_storage_gb."
  type        = number
  default     = 50
}

variable "db_max_allocated_storage_gb" {
  description = "Storage autoscaling ceiling (GB)."
  type        = number
  default     = 500
}

variable "redis_node_type" {
  description = "ElastiCache node type for Redis."
  type        = string
  default     = "cache.t4g.micro"
}

# ── Access controls ────────────────────────────────────────────────────────

variable "alb_ingress_cidrs" {
  description = "CIDR blocks allowed to reach the ALB. Default is open; restrict for private installs."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

# ── Git webhook ingress (optional) ─────────────────────────────────────────
# For private installs (alb_scheme = "internal") that use MCP repository sync.
# Adds an API Gateway HTTP API that forwards only GitHub and/or GitLab webhook
# deliveries to the ALB through a VPC link. See webhook-ingress.tf.

variable "enable_webhook_ingress" {
  description = "Create a public API Gateway endpoint that forwards only git provider webhook deliveries (POST /api/v1/webhooks/<provider>/mcp/{sync_id}) to the ALB through a VPC link, so MCP repository sync works on a private install. Requires alb_scheme = \"internal\"."
  type        = bool
  default     = false
}

variable "webhook_providers" {
  description = "Providers whose webhooks the endpoint accepts: github, gitlab, or both. Each adds one route."
  type        = list(string)
  default     = ["github"]

  validation {
    condition     = length(var.webhook_providers) > 0 && alltrue([for p in var.webhook_providers : contains(["github", "gitlab"], p)])
    error_message = "webhook_providers must list github, gitlab, or both."
  }
}

variable "webhook_domain_name" {
  description = "Optional custom hostname for the webhook endpoint (e.g. hooks.observal.example.com). Empty uses the API Gateway execute-api URL, which already has a valid certificate."
  type        = string
  default     = ""
}

variable "webhook_route53_zone_id" {
  description = "Public Route 53 hosted zone for webhook_domain_name (DNS record and certificate validation). Defaults to route53_zone_id."
  type        = string
  default     = ""
}

variable "webhook_throttle_rate_limit" {
  description = "Steady-state webhook requests per second API Gateway lets through to the VPC (best effort, a coarse backstop; the API also limits each webhook URL to 60 deliveries a minute). Excess deliveries get 429 and show as failed in the provider's delivery log."
  type        = number
  default     = 10
}

variable "webhook_throttle_burst_limit" {
  description = "Webhook request burst API Gateway allows above webhook_throttle_rate_limit."
  type        = number
  default     = 50
}

# ── Application config ─────────────────────────────────────────────────────

variable "observability_stack" {
  description = "Bundled observability stack to deploy: none, prometheus, or grafana. grafana includes prometheus."
  type        = string
  default     = "none"

  validation {
    condition     = contains(["none", "prometheus", "grafana"], var.observability_stack)
    error_message = "observability_stack must be one of: none, prometheus, grafana."
  }
}

variable "log_retention_days" {
  description = "CloudWatch log retention for application + infrastructure log groups."
  type        = number
  default     = 30
}

# ── Backups ────────────────────────────────────────────────────────────────

variable "backup_bucket_force_destroy" {
  description = "Allow Terraform to destroy the backups bucket even if it contains objects. Set true only for non-prod."
  type        = bool
  default     = false
}

variable "backup_lifecycle_ia_days" {
  description = "Days after which backup objects transition to STANDARD_IA."
  type        = number
  default     = 30
}

variable "backup_lifecycle_glacier_days" {
  description = "Days after which backup objects transition to GLACIER_IR."
  type        = number
  default     = 90
}

variable "backup_lifecycle_expire_days" {
  description = "Days after which backup objects expire. Set to 0 to disable expiration."
  type        = number
  default     = 365
}

variable "enable_public_ops_paths" {
  description = "Expose /docs, /redoc, /openapi.json publicly via the ALB. Set true only for internal or dev deployments. Default false blocks these paths with a 403."
  type        = bool
  default     = false
}

# ── License / Edition ──────────────────────────────────────────────────────


# ── Demo account seeding (optional, first-deploy only) ────────────────────────

variable "demo_super_admin_email" {
  description = "Email for the demo super-admin account. Leave empty to skip all demo seeding."
  type        = string
  default     = ""
}

variable "demo_super_admin_password" {
  description = "Password for the demo super-admin account."
  type        = string
  default     = ""
  sensitive   = true
}

variable "demo_admin_email" {
  description = "Email for the demo admin account."
  type        = string
  default     = ""
}

variable "demo_admin_password" {
  description = "Password for the demo admin account."
  type        = string
  default     = ""
  sensitive   = true
}

variable "demo_reviewer_email" {
  description = "Email for the demo reviewer account."
  type        = string
  default     = ""
}

variable "demo_reviewer_password" {
  description = "Password for the demo reviewer account."
  type        = string
  default     = ""
  sensitive   = true
}

variable "demo_user_email" {
  description = "Email for the demo user account."
  type        = string
  default     = ""
}

variable "demo_user_password" {
  description = "Password for the demo user account."
  type        = string
  default     = ""
  sensitive   = true
}

# ── Bring-your-own VPC (optional) ─────────────────────────────────────────────
# Set these to deploy into an existing VPC instead of creating a new one.
# When vpc_id is set, Terraform skips creating VPC, subnets, IGW, NAT, and
# route tables. You must provide at least 2 public and 2 private subnet IDs.

variable "vpc_id" {
  description = "ID of an existing VPC to reuse. Leave empty to create a new VPC."
  type        = string
  default     = null

  validation {
    condition     = var.vpc_id == null || can(regex("^vpc-", var.vpc_id))
    error_message = "VPC ID must start with 'vpc-' if provided."
  }
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs (required when using existing VPC). At least 2, in different AZs."
  type        = list(string)
  default     = null

  validation {
    condition     = var.private_subnet_ids == null || length(coalesce(var.private_subnet_ids, [])) >= 2
    error_message = "At least 2 private_subnet_ids are required when using an existing VPC."
  }
}

variable "public_subnet_ids" {
  description = "List of public subnet IDs (required when using existing VPC with internet-facing ALB). Not required when alb_scheme = 'internal' (e.g. TGW-based VPCs with no public subnets)."
  type        = list(string)
  default     = null

  validation {
    condition     = var.public_subnet_ids == null || length(coalesce(var.public_subnet_ids, [])) >= 2
    error_message = "At least 2 public_subnet_ids are required when provided."
  }
}

# ── Bring-your-own Security Groups (optional) ────────────────────────────────
# Advanced: supply pre-created SG IDs to skip security group creation.
# The ALB SG must allow inbound 80/443 from your desired CIDRs.
# The ECS SG must allow inbound 8000/3000 from the ALB SG.

variable "alb_security_group_id" {
  description = "Existing ALB security group ID. Leave empty to create one."
  type        = string
  default     = null

  validation {
    condition     = var.alb_security_group_id == null || can(regex("^sg-", var.alb_security_group_id))
    error_message = "alb_security_group_id must start with 'sg-' if provided."
  }
}

variable "ecs_security_group_id" {
  description = "Existing ECS tasks security group ID. Leave empty to create one."
  type        = string
  default     = null

  validation {
    condition     = var.ecs_security_group_id == null || can(regex("^sg-", var.ecs_security_group_id))
    error_message = "ecs_security_group_id must start with 'sg-' if provided."
  }
}

variable "alb_scheme" {
  description = "Scheme for the ALB: 'internet-facing' or 'internal'."
  type        = string
  default     = "internet-facing"

  validation {
    condition     = contains(["internet-facing", "internal"], var.alb_scheme)
    error_message = "alb_scheme must be 'internet-facing' or 'internal'."
  }
}
