# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

locals {
  name = "${var.name_prefix}-${var.environment}"

  enable_custom_domain = var.domain_name != "" && var.dns_managed_zone_name != ""
  app_url              = local.enable_custom_domain ? "https://${var.domain_name}" : google_cloud_run_v2_service.api.uri

  observability_prometheus_enabled = contains(["prometheus", "grafana"], var.observability_stack)
  observability_grafana_enabled    = var.observability_stack == "grafana"

  ar_prefix = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.ghcr_proxy.repository_id}"

  image_repo_api_effective = trimprefix(var.image_repo_api, "ghcr.io/")
  image_repo_web_effective = trimprefix(var.image_repo_web, "ghcr.io/")

  image_api = "${local.ar_prefix}/${local.image_repo_api_effective}:${var.image_tag}"
  image_web = "${local.ar_prefix}/${local.image_repo_web_effective}:${var.image_tag}"

  database_url   = "postgresql+asyncpg://${google_sql_user.app.name}:${random_password.db.result}@${google_sql_database_instance.postgres.private_ip_address}:5432/${google_sql_database.app.name}"
  redis_url      = "redis://${google_redis_instance.main.host}:${google_redis_instance.main.port}"
  clickhouse_url = "clickhouse://default:${random_password.clickhouse.result}@${google_compute_instance.data_host.network_interface[0].network_ip}:8123/observal"
}
