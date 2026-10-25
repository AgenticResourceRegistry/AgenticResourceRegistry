# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

output "app_url" {
  description = "Observal application URL."
  value       = module.observal.app_url
}

output "alb_dns_name" {
  description = "ALB DNS name."
  value       = module.observal.alb_dns_name
}

output "ecs_cluster_name" {
  description = "ECS cluster name."
  value       = module.observal.ecs_cluster_name
}
