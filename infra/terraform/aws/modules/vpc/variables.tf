# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

variable "name" {
  description = "Name prefix for all VPC resources."
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.42.0.0/16"
}

variable "azs" {
  description = "List of availability zones to use."
  type        = list(string)

  validation {
    condition     = length(var.azs) >= 1
    error_message = "At least one availability zone must be specified."
  }
}

variable "public_subnet_cidrs" {
  description = "CIDRs for public subnets (one per AZ). Empty list derives from vpc_cidr via cidrsubnet; non-empty list must match length(azs)."
  type        = list(string)
  default     = []
}

variable "private_subnet_cidrs" {
  description = "CIDRs for private subnets (one per AZ). Empty list derives from vpc_cidr via cidrsubnet; non-empty list must match length(azs)."
  type        = list(string)
  default     = []
}

variable "log_retention_days" {
  description = "CloudWatch log retention for flow logs."
  type        = number
  default     = 30
}

variable "enable_flow_logs" {
  description = "Enable VPC flow logs."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags to apply to all resources."
  type        = map(string)
  default     = {}
}
