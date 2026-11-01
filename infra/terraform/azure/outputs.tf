# SPDX-FileCopyrightText: 2026 Tanvi Reddy <reddyplayer22@gmail.com>
# SPDX-License-Identifier: Apache-2.0

output "api_url" {
  description = "Public URL of the API service."
  value       = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "web_url" {
  description = "Public URL of the web frontend."
  value       = "https://${azurerm_container_app.web.ingress[0].fqdn}"
}

output "grafana_url" {
  description = "URL of the Azure Managed Grafana instance."
  value       = local.observability_grafana_enabled ? azurerm_dashboard_grafana.main[0].endpoint : "disabled"
}

output "postgresql_fqdn" {
  description = "PostgreSQL server FQDN (private, VNet only)."
  value       = azurerm_postgresql_flexible_server.main.fqdn
}

output "redis_hostname" {
  description = "Redis endpoint."
  value       = var.redis_mode == "enterprise" ? azurerm_redis_enterprise_cluster.main[0].hostname : "${azurerm_network_interface.clickhouse.private_ip_address}:6379"
}

output "clickhouse_private_ip" {
  description = "ClickHouse VM private IP (VNet only)."
  value       = azurerm_network_interface.clickhouse.private_ip_address
}

output "acr_login_server" {
  description = "ACR login server for pushing images."
  value       = azurerm_container_registry.main.login_server
}

output "resource_group_name" {
  description = "Resource group containing all resources."
  value       = azurerm_resource_group.main.name
}
