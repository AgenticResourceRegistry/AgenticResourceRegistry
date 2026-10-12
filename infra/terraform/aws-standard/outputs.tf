# SPDX-FileCopyrightText: 2026 Observal
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

output "data_host_instance_id" {
  description = "EC2 instance ID for the data tier host (Postgres + Redis + ClickHouse + Grafana)."
  value       = aws_instance.data_host.id
}

output "data_host_ssm_session_command" {
  description = "Open a shell on the data tier host (no SSH key needed)."
  value       = "aws ssm start-session --region ${var.region} --target ${aws_instance.data_host.id}"
}

output "init_run_task_command" {
  description = "Manual command to re-run the migrations/seed task."
  value       = "aws ecs run-task --region ${var.region} --cluster ${aws_ecs_cluster.main.name} --capacity-provider-strategy capacityProvider=${aws_ecs_capacity_provider.ec2.name},weight=1,base=1 --task-definition ${aws_ecs_task_definition.init.family} --network-configuration 'awsvpcConfiguration={subnets=[${join(",", local.private_subnet_ids)}],securityGroups=[${aws_security_group.ecs_instances.id}],assignPublicIp=DISABLED}'"
}

output "backups_bucket" {
  description = "S3 bucket for backups."
  value       = aws_s3_bucket.backups.bucket
}

output "log_group_names" {
  description = "CloudWatch log groups for application and infrastructure."
  value = {
    api       = aws_cloudwatch_log_group.ecs_api.name
    web       = aws_cloudwatch_log_group.ecs_web.name
    worker    = aws_cloudwatch_log_group.ecs_worker.name
    init      = aws_cloudwatch_log_group.ecs_init.name
    data_host = aws_cloudwatch_log_group.data_host.name
  }
}
