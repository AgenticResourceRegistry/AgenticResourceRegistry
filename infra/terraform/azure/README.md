<!-- SPDX-FileCopyrightText: 2026 Tanvi Reddy <reddyplayer22@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Agentic Resource Registry - Azure Terraform Module

Deploy Agentic Resource Registry to Azure using Container Apps, PostgreSQL Flexible Server, Azure Cache for Redis, and a self-hosted ClickHouse VM.

## Architecture

```
Internet
    |
    v
Azure Container Apps (VNet-integrated)
    ├── observal-api     (FastAPI, autoscale 2-10)
    ├── observal-web     (nginx SPA, autoscale 2-6)
    ├── observal-worker  (arq background jobs, autoscale 1-5)
    └── observal-init    (one-shot migration job)
    |
    v (private VNet)
    ├── Azure Database for PostgreSQL Flexible Server (zone-redundant in prod)
    ├── Azure Cache for Redis (Standard tier, TLS-only)
    └── Azure VM: ClickHouse + Prometheus (Premium SSD data disk)
    |
    v
Azure Managed Grafana (connected to Log Analytics + ClickHouse)
```

## Prerequisites

- Azure CLI (`az login`)
- Terraform >= 1.5
- Docker (for building/pushing images to ACR)

## Quick Start

```bash
# 1. Initialize
cd infra/terraform/azure
terraform init

# 2. Deploy staging
terraform apply -var-file=staging.tfvars

# 3. Push images to ACR (after first apply creates the registry)
ACR=$(terraform output -raw acr_login_server)
az acr login --name $ACR
docker tag ghcr.io/agenticresourceregistry/observal-api:latest $ACR/observal-api:latest
docker tag ghcr.io/agenticresourceregistry/observal-web:latest $ACR/observal-web:latest
docker push $ACR/observal-api:latest
docker push $ACR/observal-web:latest

# 4. Trigger the init job (migrations)
az containerapp job start -n observal-staging-init -g observal-staging-rg

# 5. Get URLs
terraform output api_url
terraform output web_url
```

## Environments

| File | Description |
|------|-------------|
| `staging.tfvars` | Cost-optimized, single replicas, smaller SKUs |
| `prod.tfvars` | Zone-redundant PostgreSQL, HA Redis, autoscaling, larger VMs |

## ClickHouse

The module deploys ClickHouse on an Azure VM with a managed disk.

## Estimated Monthly Cost (Staging)

| Resource | ~Cost |
|----------|-------|
| Container Apps (3 apps, min replicas) | $30 |
| PostgreSQL Flexible (B2s) | $25 |
| Redis (Standard C0) | $15 |
| ClickHouse VM (D2s_v5) | $70 |
| Managed Grafana | $10 |
| Log Analytics | $5 |
| ACR (Basic) | $5 |
| **Total** | **~$160/mo** |

## Destroying

```bash
terraform destroy -var-file=staging.tfvars
```
