# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# Deploy Observal into an existing VPC.
#
# Prerequisites:
#   - VPC with DNS hostnames + DNS support enabled
#   - At least 2 private subnets (with NAT/TGW route for outbound)
#   - At least 2 public subnets (if ALB is internet-facing)

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.70" }
  }
}

module "observal" {
  source = "../.."

  region      = var.region
  environment = var.environment
  name_prefix = var.name_prefix

  # BYO-VPC
  vpc_id             = var.vpc_id
  private_subnet_ids = var.private_subnet_ids
  public_subnet_ids  = var.public_subnet_ids

  # Optional: BYO security groups
  alb_security_group_id = var.alb_security_group_id
  ecs_security_group_id = var.ecs_security_group_id

  # ALB scheme — use "internal" if your VPC has no public subnets
  alb_scheme = var.alb_scheme

  # Restrict ALB ingress to your corporate CIDR
  alb_ingress_cidrs = var.alb_ingress_cidrs
}
