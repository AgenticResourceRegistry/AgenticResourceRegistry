# SPDX-FileCopyrightText: 2026 Tanvi Reddy <reddyplayer22@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# Azure Monitor Workspace (required for Managed Grafana integration)
resource "azurerm_monitor_workspace" "main" {
  count               = local.observability_grafana_enabled ? 1 : 0
  name                = "${local.name}-monitor"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  tags = local.tags
}

# Azure Managed Grafana - enterprise-ready observability dashboard.
resource "azurerm_dashboard_grafana" "main" {
  count               = local.observability_grafana_enabled ? 1 : 0
  name                = "${var.name_prefix}-${var.environment}-gf"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Standard"

  grafana_major_version = "11"

  identity {
    type = "SystemAssigned"
  }

  azure_monitor_workspace_integrations {
    resource_id = azurerm_monitor_workspace.main[0].id
  }

  tags = local.tags
}

# Grant the current user Grafana Admin role
resource "azurerm_role_assignment" "grafana_admin" {
  count                = local.observability_grafana_enabled ? 1 : 0
  scope                = azurerm_dashboard_grafana.main[0].id
  role_definition_name = "Grafana Admin"
  principal_id         = data.azurerm_client_config.current.object_id
}
