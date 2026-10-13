# SPDX-FileCopyrightText: 2026 Tanvi Reddy <reddyplayer22@gmail.com>
# SPDX-License-Identifier: Apache-2.0

resource "azurerm_postgresql_flexible_server" "main" {
  name                          = "${local.name}-pg"
  resource_group_name           = azurerm_resource_group.main.name
  location                      = azurerm_resource_group.main.location
  version                       = "16"
  delegated_subnet_id           = azurerm_subnet.data.id
  private_dns_zone_id           = azurerm_private_dns_zone.postgresql.id
  public_network_access_enabled = false

  administrator_login    = "observal"
  administrator_password = random_password.db.result

  sku_name   = var.postgresql_sku
  storage_mb = var.postgresql_storage_gb * 1024

  zone = local.is_prod ? "1" : null

  dynamic "high_availability" {
    for_each = local.is_prod ? [1] : []
    content {
      mode                      = "ZoneRedundant"
      standby_availability_zone = "2"
    }
  }

  backup_retention_days        = local.is_prod ? 35 : 7
  geo_redundant_backup_enabled = local.is_prod

  tags = local.tags

  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgresql]

  lifecycle {
    ignore_changes = [zone]
  }
}

resource "azurerm_postgresql_flexible_server_database" "observal" {
  name      = "observal"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

resource "azurerm_postgresql_flexible_server_configuration" "max_connections" {
  name      = "max_connections"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "300"
}
