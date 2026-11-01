# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

output "app_url" {
  description = "Public URL for the Observal install."
  value       = local.app_url
}

output "alb_dns_name" {
  description = "ALB DNS name (use this to set your CNAME if you skipped Route53 here)."
  value       = aws_lb.app.dns_name
}

output "webhook_public_url" {
  description = "Public base URL GitHub and GitLab use for webhook deliveries: the custom domain or the API Gateway execute-api URL (empty unless enable_webhook_ingress is true). The MCP Sync tab shows full webhook URLs under it."
  value       = local.webhook_public_url
}

output "ecs_cluster_name" {
  description = "ECS cluster running api/web/worker."
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_names" {
  description = "ECS service names — use with `aws ecs update-service` for manual scaling or force-deploy."
  value = {
    api    = aws_ecs_service.api.name
    web    = aws_ecs_service.web.name
    worker = aws_ecs_service.worker.name
  }
}

output "init_run_task_command" {
  description = "Manual command to re-run the migrations/seed task."
  value       = "aws ecs run-task --region ${var.region} --cluster ${aws_ecs_cluster.main.name} --launch-type FARGATE --task-definition ${aws_ecs_task_definition.init.family} --network-configuration 'awsvpcConfiguration={subnets=[${join(",", local.private_subnet_ids)}],securityGroups=[${local.ecs_sg_id}],assignPublicIp=DISABLED}'"
}

output "rds_endpoint" {
  description = "RDS Postgres endpoint."
  value       = aws_db_instance.postgres.address
  sensitive   = true
}

output "redis_endpoint" {
  description = "ElastiCache Redis primary endpoint."
  value       = aws_elasticache_replication_group.redis.primary_endpoint_address
  sensitive   = true
}

output "data_host_instance_id" {
  description = "EC2 instance id for the ClickHouse data host."
  value       = aws_instance.data_host.id
}

output "data_host_ssm_session_command" {
  description = "Open a shell on the data tier host (no SSH key needed)."
  value       = "aws ssm start-session --region ${var.region} --target ${aws_instance.data_host.id}"
}

output "clickhouse_endpoint" {
  description = "Internal ClickHouse endpoint (DNS within the VPC)."
  value       = local.clickhouse_host_internal
  sensitive   = true
}

output "backups_bucket" {
  description = "S3 bucket for ClickHouse + RDS backups."
  value       = aws_s3_bucket.backups.bucket
}

output "ssm_parameter_paths" {
  description = "SSM parameter names holding generated secrets and connection URLs."
  value = sort(concat(
    [for p in aws_ssm_parameter.app : p.name],
    [for p in aws_ssm_parameter.urls : p.name],
  ))
}

output "log_group_names" {
  description = "CloudWatch log groups for application and infrastructure."
  value = {
    api       = aws_cloudwatch_log_group.ecs_api.name
    web       = aws_cloudwatch_log_group.ecs_web.name
    worker    = aws_cloudwatch_log_group.ecs_worker.name
    init      = aws_cloudwatch_log_group.ecs_init.name
    data_host = aws_cloudwatch_log_group.data_host.name
    flow_logs = aws_cloudwatch_log_group.flow_logs.name
  }
}
