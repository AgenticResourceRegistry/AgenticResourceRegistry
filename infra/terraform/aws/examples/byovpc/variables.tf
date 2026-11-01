# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "name_prefix" {
  type    = string
  default = "observal"
}

variable "vpc_id" {
  description = "Existing VPC ID (e.g. vpc-0abc1234def56789a)"
  type        = string
}

variable "private_subnet_ids" {
  description = "At least 2 private subnet IDs in different AZs"
  type        = list(string)
}

variable "public_subnet_ids" {
  description = "At least 2 public subnet IDs in different AZs"
  type        = list(string)
}

variable "alb_security_group_id" {
  description = "Optional: existing ALB security group ID"
  type        = string
  default     = null
}

variable "ecs_security_group_id" {
  description = "Optional: existing ECS tasks security group ID"
  type        = string
  default     = null
}

variable "alb_scheme" {
  description = "ALB scheme: 'internet-facing' or 'internal'"
  type        = string
  default     = "internet-facing"
}

variable "alb_ingress_cidrs" {
  description = "CIDRs allowed to reach the ALB"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}
