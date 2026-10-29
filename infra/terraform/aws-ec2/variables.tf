# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

variable "name" {
  description = "Deployment name (used for resource naming)"
  type        = string
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = "EC2 instance type (t3.large recommended minimum)"
  type        = string
  default     = "t3.large"
}

variable "domain" {
  description = "Domain for this instance (e.g. observal.company.io). Leave empty for IP-only access over HTTP."
  type        = string
  default     = ""
}

variable "route53_zone_id" {
  description = "Route53 hosted zone ID (required if domain is set)"
  type        = string
  default     = ""
}

variable "vpc_id" {
  description = "VPC ID (leave empty for default VPC)"
  type        = string
  default     = ""
}

variable "subnet_id" {
  description = "Subnet ID (leave empty for default)"
  type        = string
  default     = ""
}

variable "env_overrides" {
  description = "Map of environment variables to override in .env (empty values are ignored, defaults from .env.example are kept)"
  type        = map(string)
  default     = {}
}

variable "observability_stack" {
  description = "Bundled observability stack to deploy: none, prometheus, or grafana. grafana includes prometheus."
  type        = string
  default     = "none"

  validation {
    condition     = contains(["none", "prometheus", "grafana"], var.observability_stack)
    error_message = "observability_stack must be one of: none, prometheus, grafana."
  }
}

variable "image_tag" {
  description = "Observal image tag to deploy from GHCR (e.g. 'latest', 'v1.5.0')"
  type        = string
  default     = "latest"
}

variable "observal_ref" {
  description = "Git branch, tag, or commit SHA (used only for config files, not image builds)"
  type        = string
  default     = "main"
}

variable "observal_repo" {
  description = "Git repository URL"
  type        = string
  default     = "https://github.com/AgenticResourceRegistry/AgenticResourceRegistry.git"
}
