# SPDX-FileCopyrightText: 2026 Tanvi Reddy <reddyplayer22@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# Redis runs on the ClickHouse VM via Docker Compose (self_hosted mode).
# When redis_mode = "enterprise", an Azure Managed Redis cluster is provisioned instead.
#
# Enterprise mode requires a subscription with Redis Enterprise quota.
# Most Azure for Students / Sponsorship subscriptions don't have this.

resource "azurerm_redis_enterprise_cluster" "main" {
  count               = var.redis_mode == "enterprise" ? 1 : 0
  name                = "${local.name}-redis"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku_name            = var.redis_enterprise_sku

  minimum_tls_version = "1.2"

  tags = local.tags
}

resource "azurerm_redis_enterprise_database" "main" {
  count             = var.redis_mode == "enterprise" ? 1 : 0
  name              = "default"
  cluster_id        = azurerm_redis_enterprise_cluster.main[0].id
  client_protocol   = "Encrypted"
  clustering_policy = "EnterpriseCluster"
  eviction_policy   = "VolatileLRU"
}
