# SPDX-FileCopyrightText: 2026 Tanvi Reddy <reddyplayer22@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# Production environment - HA, zone-redundant, deletion-protected.

subscription_id = "6a0284fc-791a-4d77-9520-69cdaa79ba44"
environment     = "prod"
location        = "eastus"
name_prefix     = "observal"

# Production-grade PostgreSQL
postgresql_sku        = "GP_Standard_D2ds_v5"
postgresql_storage_gb = 128

# Azure Managed Redis (switch to "enterprise" when subscription has quota)
redis_mode           = "self_hosted"
redis_enterprise_sku = "Enterprise_E10-2"

# Production replicas with autoscaling
api_min_replicas    = 2
api_max_replicas    = 10
web_min_replicas    = 2
web_max_replicas    = 6
worker_min_replicas = 1
worker_max_replicas = 5

# Larger ClickHouse VM for production workloads
clickhouse_vm_size      = "Standard_D4ads_v7"
clickhouse_disk_size_gb = 200

# Bundled observability
observability_stack = "grafana"

# 30-day log retention
log_retention_days = 30
