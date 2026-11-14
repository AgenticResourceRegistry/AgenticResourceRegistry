# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# VPC networking is now managed by the modules/vpc submodule.
# When vpc_id is provided (BYO-VPC), the module is not invoked.

module "vpc" {
  count  = local.should_create_vpc ? 1 : 0
  source = "./modules/vpc"

  name                 = local.name
  vpc_cidr             = var.vpc_cidr
  azs                  = local.azs
  public_subnet_cidrs  = var.public_subnet_cidrs
  private_subnet_cidrs = var.private_subnet_cidrs
  log_retention_days   = var.log_retention_days
  enable_flow_logs     = var.enable_flow_logs
}

resource "aws_route53_zone" "internal" {
  name = var.internal_dns_zone

  vpc {
    vpc_id = local.vpc_id
  }

  tags = { Name = "${local.name}-internal-zone" }
}
