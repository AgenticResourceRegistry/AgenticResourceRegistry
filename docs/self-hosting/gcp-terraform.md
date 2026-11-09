---
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 VishnuM049 <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "GCP deployment with Terraform"
---

End state: Agentic Resource Registry running in your GCP project on Cloud Run, backed by Cloud SQL Postgres, Memorystore Redis, and a GCE instance for ClickHouse — with a Global HTTPS Load Balancer, managed SSL certificate, Secret Manager for credentials, and GCS for backups.

For the overall deployment strategy and comparison with AWS, see [Production deployment](/self-hosting/production-deploy). If you want a simpler single-VM setup, see [Single-node deployment](/self-hosting/single-node-deploy).

## What gets provisioned

A single `terraform apply` creates:

- **VPC** with a private subnet, Cloud NAT, and a Serverless VPC Access Connector
- **Cloud Run v2 services**: `api`, `web`, `worker` with autoscaling (min/max instance counts)
- **Cloud Run v2 job**: `init` (one-shot migrations + seeds)
- **Cloud SQL Postgres** (Open-source distribution): optional HA, encrypted, automated backups
- **Memorystore Redis**: BASIC or STANDARD_HA tier
- **GCE instance** (data host): ClickHouse on a persistent disk, with optional Prometheus and Grafana, accessible via IAP SSH tunnel
- **Global HTTPS Load Balancer** with managed SSL certificate (when domain is supplied)
- **Cloud DNS** A record pointing to the load balancer
- **Secret Manager**: generated DB / ClickHouse / SECRET_KEY passwords, plus connection URLs injected into Cloud Run services
- **GCS backups bucket**: versioned, lifecycle to Nearline → delete
- **Artifact Registry**: for storing container images (optional)

## Prerequisites

| Requirement | Why |
|---|---|
| GCP project with billing enabled | Resources cost money |
| `gcloud` CLI authenticated | `gcloud auth application-default login` |
| Terraform ≥ 1.5 | `brew install terraform` |
| (Optional) Cloud DNS managed zone | Required for HTTPS on a custom domain |

Enable required APIs:

```bash
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  redis.googleapis.com \
  compute.googleapis.com \
  secretmanager.googleapis.com \
  dns.googleapis.com \
  vpcaccess.googleapis.com \
  servicenetworking.googleapis.com \
  iam.googleapis.com
```

Use the selected application image tag `1.16.0`, not a moving `latest` alias, and verify any Artifact Registry mirror contains those exact images. The source checkout tag does not pin a separately configured image tag by itself.

## Quickstart

<Warning>
  **v1.16.0 GCP signing-key limitation:** the stock Cloud Run API sets `JWT_KEY_DIR=/tmp/keys`, with no shared durable signing-key mount. Workers within one instance can share its directory, but separate API instances can generate different keys, and replacement loses them. Before an authenticated team deployment, require a reviewed, tested shared-and-durable signing-key design for all API instances. Limiting instance count alone does not fix replacement/persistence. This guide does not supply or certify that infrastructure change.
</Warning>

```bash
git clone --branch v1.16.0 --depth 1 https://github.com/AgenticResourceRegistry/AgenticResourceRegistry.git
cd AgenticResourceRegistry/infra/terraform/gcp

# 1. Authenticate
gcloud auth application-default login

# 2. Configure
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars
# REQUIRED: set project_id and change image_tag = "latest" to image_tag = "1.16.0".

# 3. Plan and review before applying
terraform init
terraform plan -out tf.plan
# Review API/web/worker/init application image references for :1.16.0 before applying.
terraform apply tf.plan

# 4. Run migrations
$(terraform output -raw init_job_run_command)

# 5. Verify
terraform output app_url
```

## Configuration

All inputs live in `terraform.tfvars`.

### Minimal (no custom domain)

```hcl
project_id  = "my-gcp-project"
region      = "us-central1"
environment = "prod"
image_tag   = "1.16.0"
```

The install comes up on the Cloud Run default URL (e.g. `https://observal-prod-api-xxxxx-uc.a.run.app`).

### Recommended (HTTPS on your domain)

```hcl
project_id  = "my-gcp-project"
region      = "us-central1"
environment = "prod"
image_tag   = "1.16.0"

domain_name           = "observal.example.com"
dns_managed_zone_name = "example-com"
```

Terraform provisions a Global HTTPS Load Balancer with a Google-managed SSL certificate and creates the DNS A record.

### Sizing

```hcl
# Cloud Run
api_cpu            = "1"
api_memory         = "1Gi"
api_min_instances   = 1
api_max_instances   = 10

web_min_instances   = 1
worker_min_instances = 1

# Data tier
data_machine_type  = "e2-standard-2"   # 2 vCPU / 8 GB
data_disk_size_gb  = 100

# Cloud SQL
db_tier            = "db-g1-small"
db_disk_size_gb    = 50
db_ha_enabled      = false             # set true for production HA

# Redis
redis_memory_size_gb = 1
redis_tier           = "BASIC"         # or STANDARD_HA
```

For high-throughput installs, bump `data_machine_type` to `e2-standard-4`, `db_tier` to `db-custom-4-16384`, and `redis_tier` to `STANDARD_HA`.

## Operating the install

### Shell into the data host

```bash
$(terraform output -raw data_host_ssh_command)
# Inside:
sudo docker compose -f /opt/observal/docker-compose.data.yml ps
sudo docker compose -f /opt/observal/docker-compose.data.yml logs -f clickhouse
```

No public SSH. Access is through IAP (Identity-Aware Proxy) — authenticated, audited, no SSH keys to manage.

### View logs

Cloud Run logs go to Cloud Logging automatically:

```bash
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=observal-prod-api" \
  --limit 50 --format json
```

Or use the Cloud Console: **Logging → Logs Explorer**, filter by `resource.type="cloud_run_revision"`.

### Re-run migrations

Only after the [canonical migration/recovery gates](/self-hosting/upgrades) are satisfied: preserve backups, drain incompatible writers, review the target and exported command, and control traffic. A launch response is not completion; wait for the intended task/job and inspect its exit/result before resuming access. Do not re-run migrations against a serving deployment as an improvised repair.

```bash
$(terraform output -raw init_job_run_command)
```

### Verify generated secret configuration

Review Secret Manager versions, service-account permissions, and the intended container injection without printing decrypted credentials in a shared transcript. Coordinated database URLs, native credentials, and application encryption configuration must be preserved for [recovery](/self-hosting/backup-and-restore).

### Upgrade to a new release

Use [the canonical upgrade gates](/self-hosting/upgrades), choose the actual target image/revision, and preserve full backups before a change. Cloud Run revision rollout is not a migration or zero-downtime guarantee.

The Terraform init-job resource defines the job; it does not by itself prove the job executed successfully. The exported `init_job_run_command` launches it, but an operator must wait for completion and inspect its result. For a migration requiring a homogeneous fleet, drain incompatible old API/worker revisions before initialization and control traffic until the new schema and application are verified.

Do not apply a new image and then assume existing writers were safely stopped while the job ran. Review the planned dependency/execution graph and use the approved Cloud Run traffic/writer procedure for that release. If a change fails, determine schema/data state before selecting prior revisions; an image rollback does not restore Cloud SQL, ClickHouse evidence, or matching application/encryption artifacts.

### Resize the data disk

```hcl
data_disk_size_gb = 250
```

```bash
terraform apply
# SSH into the data host and resize the filesystem:
$(terraform output -raw data_host_ssh_command)
sudo growpart /dev/sdb 1 || true
sudo resize2fs /dev/sdb1
```

### Destroy

```bash
terraform destroy
```

Cloud SQL deletion protection is enabled on prod. Disable manually before destroy if you mean it.

## Cost estimate (us-central1, on-demand)

| Component | Spec | ~$/month |
|---|---|---|
| Cloud Run api | 1 vCPU / 1 GB, min 1 instance | $25 |
| Cloud Run web | 1 vCPU / 512 MB, min 1 instance | $15 |
| Cloud Run worker | 1 vCPU / 1 GB, min 1 instance | $25 |
| Cloud SQL Postgres | db-g1-small (shared) | $25 |
| Memorystore Redis | 1 GB BASIC | $35 |
| GCE data host | e2-standard-2 | $50 |
| Global HTTPS LB | — | $18 |
| Persistent Disk 100 GB | — | $4 |
| GCS backups | ~1 GB | $0.02 |
| **Total** | | **~$200** |

Cloud Run scales to zero when idle (if `min_instances = 0`), which can significantly reduce costs for low-traffic deployments.

## Production hardening checklist

- Set `db_ha_enabled = true` for Cloud SQL high availability
- Set `redis_tier = "STANDARD_HA"` for Redis failover
- Restrict Cloud Run ingress (use `ingress = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"` if behind GCLB)
- Enable Cloud Armor (WAF) on the Global HTTPS Load Balancer
- Enable Security Command Center in the project
- Set up alerting on Cloud SQL CPU, memory, and Cloud Run error rates
- Configure [SSO](/self-hosting/authentication)
- Test [backup and restore](/self-hosting/backup-and-restore) end-to-end
- Set up Terraform remote state in GCS

## Troubleshooting

**Cloud Run service stuck in `Revision is not ready`.**
Check revision logs in Cloud Logging. Common causes: Secret Manager permissions missing, VPC connector not ready, or image pull failure.

**Init job fails.**
```bash
gcloud run jobs executions list --job=observal-prod-init --region=us-central1
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=observal-prod-init" --limit 20
```

Usually: Cloud SQL not yet reachable (transient on first apply), or missing secret access.

**502 from the load balancer.**
The managed SSL certificate takes 10–30 minutes to provision. Check: **Network Services → Load Balancing → (your LB) → Backend services → Health**.

**Cloud SQL connection refused from Cloud Run.**
The VPC Access Connector must be in the same region as Cloud Run. Verify `connector_cidr` doesn't overlap with your VPC subnet.

For application-level issues, see [Troubleshooting](/self-hosting/troubleshooting).

## Next

- [Production deployment](/self-hosting/production-deploy) — overview of both clouds
- [Configuration](/self-hosting/configuration) — environment variables
- [Upgrades](/self-hosting/upgrades) — upgrade and rollback procedures
- [Backup and restore](/self-hosting/backup-and-restore) — restore procedures
