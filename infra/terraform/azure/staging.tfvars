# SPDX-FileCopyrightText: 2026 Tanvi Reddy <reddyplayer22@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# Staging environment - cost-optimized for development/testing.

subscription_id = "6a0284fc-791a-4d77-9520-69cdaa79ba44"
environment     = "staging"
location        = "centralus"
name_prefix     = "observal"

# Smaller PostgreSQL for staging
postgresql_sku        = "B_Standard_B2s"
postgresql_storage_gb = 32

# Redis on ClickHouse VM (Enterprise not available on this subscription)
redis_mode = "self_hosted"

# Minimal replicas
api_min_replicas    = 1
api_max_replicas    = 3
web_min_replicas    = 1
web_max_replicas    = 2
worker_min_replicas = 1
worker_max_replicas = 2

# Smaller ClickHouse VM
clickhouse_vm_size      = "Standard_D2ads_v7"
clickhouse_disk_size_gb = 50

# Bundled observability
observability_stack = "grafana"

# Minimum allowed by Azure is 30
log_retention_days = 30
