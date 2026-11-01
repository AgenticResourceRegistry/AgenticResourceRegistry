# SPDX-FileCopyrightText: 2026 Tanvi Reddy <reddyplayer22@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# Container Apps Environment with VNet integration
resource "azurerm_container_app_environment" "main" {
  name                       = "${local.name}-env"
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  infrastructure_subnet_id   = azurerm_subnet.container_apps.id

  tags = local.tags

  depends_on = [
    azurerm_subnet.container_apps,
    azurerm_virtual_network.main,
  ]
}

# Persistent storage for JWT keys (survives container restarts)
resource "azurerm_storage_account" "keys" {
  name                     = replace("${var.name_prefix}${var.environment}keys", "-", "")
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "LRS"

  tags = local.tags
}

resource "azurerm_storage_share" "jwt_keys" {
  name               = "jwt-keys"
  storage_account_id = azurerm_storage_account.keys.id
  quota              = 1
}

resource "azurerm_container_app_environment_storage" "jwt_keys" {
  name                         = "jwtkeys"
  container_app_environment_id = azurerm_container_app_environment.main.id
  account_name                 = azurerm_storage_account.keys.name
  share_name                   = azurerm_storage_share.jwt_keys.name
  access_key                   = azurerm_storage_account.keys.primary_access_key
  access_mode                  = "ReadWrite"
}

# -- API service -------------------------------------------------------------

resource "azurerm_container_app" "api" {
  name                         = "${local.name}-api"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"

  registry {
    server               = azurerm_container_registry.main.login_server
    username             = azurerm_container_registry.main.admin_username
    password_secret_name = "acr-password"
  }

  secret {
    name  = "acr-password"
    value = azurerm_container_registry.main.admin_password
  }

  secret {
    name  = "database-url"
    value = local.database_url
  }

  secret {
    name  = "redis-url"
    value = local.redis_url
  }

  secret {
    name  = "clickhouse-url"
    value = local.clickhouse_url
  }

  secret {
    name  = "secret-key"
    value = random_password.secret_key.result
  }

  template {
    min_replicas = var.api_min_replicas
    max_replicas = var.api_max_replicas

    volume {
      name         = "jwt-keys"
      storage_name = azurerm_container_app_environment_storage.jwt_keys.name
      storage_type = "AzureFile"
    }

    container {
      name   = "api"
      image  = local.api_image
      cpu    = var.api_cpu
      memory = var.api_memory

      command = [
        "/bin/bash", "-c",
        "mkdir -p /tmp/keys && cp -a /mnt/jwt-keys/. /tmp/keys/ 2>/dev/null; trap 'cp -a /tmp/keys/. /mnt/jwt-keys/ 2>/dev/null' EXIT; exec /app/.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips '*'",
      ]

      volume_mounts {
        name = "jwt-keys"
        path = "/mnt/jwt-keys"
      }

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }

      env {
        name        = "REDIS_URL"
        secret_name = "redis-url"
      }

      env {
        name        = "CLICKHOUSE_URL"
        secret_name = "clickhouse-url"
      }

      env {
        name        = "SECRET_KEY"
        secret_name = "secret-key"
      }

      env {
        name  = "SKIP_DDL_ON_STARTUP"
        value = "true"
      }

      env {
        name  = "JWT_KEY_DIR"
        value = "/tmp/keys"
      }

      liveness_probe {
        transport = "HTTP"
        path      = "/readyz"
        port      = 8000
      }

      readiness_probe {
        transport = "HTTP"
        path      = "/readyz"
        port      = 8000
      }

      startup_probe {
        transport               = "HTTP"
        path                    = "/readyz"
        port                    = 8000
        failure_count_threshold = 10
      }
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "http"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  tags = local.tags
}

# -- Web service -------------------------------------------------------------

resource "azurerm_container_app" "web" {
  name                         = "${local.name}-web"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"

  registry {
    server               = azurerm_container_registry.main.login_server
    username             = azurerm_container_registry.main.admin_username
    password_secret_name = "acr-password"
  }

  secret {
    name  = "acr-password"
    value = azurerm_container_registry.main.admin_password
  }

  template {
    min_replicas = var.web_min_replicas
    max_replicas = var.web_max_replicas

    container {
      name   = "web"
      image  = local.web_image
      cpu    = var.web_cpu
      memory = var.web_memory

      liveness_probe {
        transport = "HTTP"
        path      = "/"
        port      = 3000
      }
    }
  }

  ingress {
    external_enabled = true
    target_port      = 3000
    transport        = "http"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  tags = local.tags
}

# -- Worker service ----------------------------------------------------------

resource "azurerm_container_app" "worker" {
  name                         = "${local.name}-worker"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"

  registry {
    server               = azurerm_container_registry.main.login_server
    username             = azurerm_container_registry.main.admin_username
    password_secret_name = "acr-password"
  }

  secret {
    name  = "acr-password"
    value = azurerm_container_registry.main.admin_password
  }

  secret {
    name  = "database-url"
    value = local.database_url
  }

  secret {
    name  = "redis-url"
    value = local.redis_url
  }

  secret {
    name  = "clickhouse-url"
    value = local.clickhouse_url
  }

  secret {
    name  = "secret-key"
    value = random_password.secret_key.result
  }

  template {
    min_replicas = var.worker_min_replicas
    max_replicas = var.worker_max_replicas

    container {
      name   = "worker"
      image  = local.api_image
      cpu    = var.worker_cpu
      memory = var.worker_memory

      command = [
        "/app/.venv/bin/python", "-c",
        "import asyncio; asyncio.set_event_loop(asyncio.new_event_loop()); from arq import run_worker; from worker import WorkerSettings; run_worker(WorkerSettings)",
      ]

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }

      env {
        name        = "REDIS_URL"
        secret_name = "redis-url"
      }

      env {
        name        = "CLICKHOUSE_URL"
        secret_name = "clickhouse-url"
      }

      env {
        name        = "SECRET_KEY"
        secret_name = "secret-key"
      }

      env {
        name  = "JWT_KEY_DIR"
        value = "/tmp/keys"
      }
    }
  }

  tags = local.tags
}

# -- Init job (migrations) ---------------------------------------------------

resource "azurerm_container_app_job" "init" {
  name                         = "${local.name}-init"
  location                     = azurerm_resource_group.main.location
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  replica_timeout_in_seconds   = 600
  replica_retry_limit          = 1

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  registry {
    server               = azurerm_container_registry.main.login_server
    username             = azurerm_container_registry.main.admin_username
    password_secret_name = "acr-password"
  }

  secret {
    name  = "acr-password"
    value = azurerm_container_registry.main.admin_password
  }

  secret {
    name  = "database-url"
    value = local.database_url
  }

  secret {
    name  = "redis-url"
    value = local.redis_url
  }

  secret {
    name  = "clickhouse-url"
    value = local.clickhouse_url
  }

  secret {
    name  = "secret-key"
    value = random_password.secret_key.result
  }

  template {
    container {
      name   = "init"
      image  = local.api_image
      cpu    = 0.5
      memory = "1Gi"

      command = ["/app/entrypoint.sh"]

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }

      env {
        name        = "REDIS_URL"
        secret_name = "redis-url"
      }

      env {
        name        = "CLICKHOUSE_URL"
        secret_name = "clickhouse-url"
      }

      env {
        name        = "SECRET_KEY"
        secret_name = "secret-key"
      }

      env {
        name  = "JWT_KEY_DIR"
        value = "/tmp/keys"
      }
    }
  }

  tags = local.tags
}
