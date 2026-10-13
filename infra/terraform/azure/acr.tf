# SPDX-FileCopyrightText: 2026 Tanvi Reddy <reddyplayer22@gmail.com>
# SPDX-License-Identifier: Apache-2.0

resource "azurerm_container_registry" "main" {
  name                = replace("${var.name_prefix}${var.environment}acr", "-", "")
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = local.is_prod ? "Premium" : "Basic"
  admin_enabled       = true

  tags = local.tags
}
