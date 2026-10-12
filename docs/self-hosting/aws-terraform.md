<!-- SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# AWS deployment with Terraform

End state: an Observal install running in your own AWS account, fronted by an Application Load Balancer with HTTPS, with managed Postgres + Redis, ECS Fargate for the stateless app tier, and a single EC2 host for ClickHouse. Prometheus and Grafana are optional through `observability_stack`.

This is the recommended path for enterprise self-hosting on AWS. If you only want to evaluate Observal, use [Docker Compose setup](docker-compose.md) instead.

## What gets provisioned

A single `terraform apply` creates:

* **VPC** with public + private subnets across two availability zones, NAT gateway, VPC flow logs
* **Application Load Balancer** with HTTPS (ACM certificate, DNS-validated) when you supply a domain; HTTP-only otherwise. Path-based rules: `/api/*` → api service, optional `/grafana/*` → Grafana, default → web
* **ECS Fargate cluster** running:
  * `api` (FastAPI): 2 tasks by default, autoscales 2–10 on CPU
  * `web` (Next.js): 2 tasks by default, autoscales 2–6 on CPU
  * `worker` (arq background jobs): 1 task by default, autoscales 1–5 on CPU
  * `init` (one-shot migrations + seeds): runs as a Fargate `RunTask` whenever `image_tag` changes
* **RDS Postgres 16**: Multi-AZ on `prod`, encrypted, automated daily backups, Performance Insights, Enhanced Monitoring, log exports
* **ElastiCache Redis 7**: 2-node replication group with automatic failover on `prod`, slow-log to CloudWatch
* **Data tier EC2** (Amazon Linux 2023): single host running ClickHouse on EBS gp3, optional Prometheus and Grafana, ENI with static private IP, internal Route 53 zone for DNS, daily ClickHouse → S3 snapshot via systemd timer
* **S3 backups bucket**: versioned, AES256, lifecycle to STANDARD\_IA → GLACIER\_IR → expire, TLS-only
* **CloudWatch log groups**: per ECS service, data host, RDS, Redis slow log, VPC flow logs
* **SSM Parameter Store**: generated DB / ClickHouse / SECRET\_KEY / optional Grafana passwords, plus pre-built connection URLs injected into ECS tasks
* **SSM Session Manager**: shell access to the data host, no SSH

ClickHouse runs on EC2. The data volume keeps it durable across instance replacements; ClickHouse HA is out of scope for this module.

## Prerequisites

| Requirement                                                                                                                             | Why                                                                        |
| --------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| AWS account with billing enabled                                                                                                        | obvious                                                                    |
| Terraform ≥ 1.6                                                                                                                         | `brew install terraform` or use [tenv](https://tofuutils.github.io/tenv/)  |
| AWS CLI v2                                                                                                                              | `brew install awscli` - also used by the one-shot init task runner         |
| [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) | shell access into the data host                                            |
| IAM principal with sufficient rights                                                                                                    | see [Required IAM permissions](aws-terraform.md#required-iam-permissions)  |
| (Optional) Route 53 hosted zone                                                                                                         | required for HTTPS on a custom domain                                      |
| (Recommended) S3 bucket + DynamoDB table                                                                                                | remote Terraform state - see [Remote state](aws-terraform.md#remote-state) |

## Quickstart

```bash
git clone https://github.com/Observal/Observal.git
cd Observal/infra/terraform/aws

# 1. Authenticate to AWS (any of these works)
export AWS_PROFILE=observal-prod
# or: aws configure sso
# or: export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...

# 2. Configure
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars

# 3. Apply
terraform init
terraform plan -out tf.plan
terraform apply tf.plan
```

First-time apply takes 12–15 minutes. RDS provisioning dominates. When it finishes:

```bash
terraform output app_url
```

Open that URL in your browser. The API and web tasks start a couple of minutes after the ALB targets register; refresh until you see the Observal login page.

A ready-to-apply call of the module lives at [`infra/terraform/aws/examples/minimal`](https://github.com/Observal/Observal/blob/main/infra/terraform/aws/examples/minimal/README.md).

## Configuration

All inputs live in `terraform.tfvars`. The defaults are production-shaped; you can apply with very little changed.

### Minimal configuration (HTTP, no custom domain)

```hcl
region      = "us-east-1"
environment = "prod"
```

The install comes up on the ALB's AWS-assigned hostname (e.g. `observal-prod-alb-1234.us-east-1.elb.amazonaws.com`) over plain HTTP. Useful for evaluation; not for production.

### Recommended configuration (HTTPS on your domain)

```hcl
region      = "us-east-1"
environment = "prod"
sizing      = "medium"

domain_name     = "observal.example.com"
route53_zone_id = "Z0123456789ABCDEFGHIJ"

# Lock down ingress to your corporate egress / VPN
alb_ingress_cidrs = ["203.0.113.0/24", "198.51.100.0/24"]
```

Terraform requests an ACM certificate, validates it via DNS records in your hosted zone, attaches it to the ALB, and creates the alias A record pointing the domain at the ALB. There is no manual DNS step.

### Sizing presets

Instead of tuning 12+ resource variables, pick a preset:

```hcl
sizing = "medium"   # small | medium | large | custom
```

| Preset   | \~$/mo | API tasks      | Web tasks      | Worker tasks   | Data host           | RDS          | Redis           |
| -------- | ------ | -------------- | -------------- | -------------- | ------------------- | ------------ | --------------- |
| `small`  | $150   | 1× (0.25 vCPU) | 1× (0.25 vCPU) | 1× (0.25 vCPU) | t3.medium (50 GB)   | db.t4g.micro | cache.t4g.micro |
| `medium` | $255   | 2× (0.5 vCPU)  | 2× (0.25 vCPU) | 1× (0.5 vCPU)  | t3.large (100 GB)   | db.t4g.small | cache.t4g.micro |
| `large`  | $600   | 3× (1 vCPU)    | 3× (0.5 vCPU)  | 2× (1 vCPU)    | r6i.xlarge (500 GB) | db.r6g.large | cache.r6g.large |

**Important: Presets vs. individual variables**

Presets and individual resource variables (`api_cpu`, `db_instance_class`, etc.) are **mutually exclusive**:

* When `sizing = "small|medium|large"`: all individual resource variables are **ignored**. The preset values are used unconditionally.
* When `sizing = "custom"`: individual variables take effect normally.

If you set `sizing = "medium"` and also `api_cpu = 1024`, the medium preset wins — `api_cpu` has no effect. To override individual values, set `sizing = "custom"` first.

### Custom sizing

Set `sizing = "custom"` to control each resource independently:

```hcl
sizing = "custom"

api_cpu              = 512   # 0.5 vCPU; raise to 1024+ for sustained >100 req/s
api_memory           = 1024
api_desired_count    = 2
api_autoscale_max    = 10

web_desired_count    = 2
worker_desired_count = 1

# Data tier (ClickHouse)
data_instance_type   = "t3.large"   # 8 GB - minimum viable for ClickHouse
data_volume_size_gb  = 100
observability_stack  = "none"       # none | prometheus | grafana

db_instance_class    = "db.t4g.small"
redis_node_type      = "cache.t4g.micro"
```

For high-throughput installs (>100 trace events/sec sustained), bump `data_instance_type` to `m6i.xlarge` and `db_instance_class` to `db.m6g.large`.

### Application options

```hcl
deployment_mode     = "enterprise"   # SSO-only login
data_retention_days = 90             # ClickHouse TTL; 0 to disable
log_retention_days  = 30             # CloudWatch log group retention
image_tag           = "v1.4.0"       # specific Observal release; "latest" pulls main
```

See [Configuration](configuration.md) for the meaning of each application setting.

### Bring Your Own VPC

Deploy Observal into an existing VPC rather than having Terraform create a new one. Use this when you have Transit Gateway, shared-services VPCs, compliance requirements, or peered networks.

```hcl
# Use an existing VPC
vpc_id             = "vpc-0abc1234def56789a"
private_subnet_ids = ["subnet-aaa11111", "subnet-bbb22222"]
public_subnet_ids  = ["subnet-xxx11111", "subnet-yyy22222"]

# Control ALB placement independently
alb_scheme = "internet-facing"   # or "internal"
```

When `vpc_id` is set, Terraform skips creating VPC, subnets, IGW, NAT gateway, route tables, and VPC flow logs. All other resources (ALB, ECS, RDS, Redis, data host) are created inside your existing VPC.

**VPC requirements:**

* DNS support enabled (`enableDnsSupport = true`, `enableDnsHostnames = true`)
* Private subnets must have outbound internet access (via NAT Gateway or Transit Gateway) for container image pulls (`ghcr.io`) and AWS API endpoints (SSM, CloudWatch, ECR)
* Public subnets must have an Internet Gateway route (only needed if `alb_scheme = "internet-facing"`)
* Subnets should span at least 2 AZs for RDS Multi-AZ and ECS placement

**Optional: Bring your own security groups:**

For environments with strict firewall policies, supply pre-created SG IDs:

```hcl
alb_security_group_id = "sg-0abc1234def56789a"
ecs_security_group_id = "sg-0abc1234def56789b"
```

The ALB SG must allow inbound TCP 80/443 from your desired CIDRs. The ECS SG must allow inbound TCP 8000 and 3000 from the ALB SG, with outbound to all.

A full working example lives at [`infra/terraform/aws/examples/byovpc`](https://github.com/Observal/Observal/blob/main/infra/terraform/aws/examples/byovpc/README.md).

### Git webhooks on a private install

[MCP repository sync](../use-cases/mcp-repo-sync.md) publishes a new MCP version as soon as GitHub or GitLab sends a webhook. With `alb_scheme = "internal"`, github.com and gitlab.com cannot reach Observal, so nothing syncs. Turn on the webhook endpoint to fix that without opening the rest of the install:

```hcl
alb_scheme             = "internal"
enable_webhook_ingress = true
webhook_providers      = ["github", "gitlab"]   # default ["github"]
```

Terraform then adds an API Gateway HTTP API in front of the existing ALB:

* One route per provider, `POST /api/v1/webhooks/<provider>/mcp/{sync_id}`. Everything else, including other methods, other paths and `..` segments, gets `404` from API Gateway and never reaches the VPC, so the UI, API and login stay private.
* A VPC link in the private subnets that connects API Gateway to the ALB's listener, so there are no public subnets, internet gateway or second load balancer. With `enable_tls`, API Gateway connects over HTTPS and checks the ALB certificate against `domain_name`.
* A security group rule that lets the VPC link, and nothing else new, reach the ALB.
* Throttling (`webhook_throttle_rate_limit`, `webhook_throttle_burst_limit`), access logs in CloudWatch, and `WEBHOOK_PUBLIC_URL` on the API so the MCP **Sync** tab shows the public webhook URL.

The webhook URL is the API's `https://<id>.execute-api.<region>.amazonaws.com` address, shown by the `webhook_public_url` output. To use your own hostname instead, set `webhook_domain_name` (and `webhook_route53_zone_id` if the public zone differs from `route53_zone_id`); Terraform adds the certificate, custom domain and DNS record, and turns off the `execute-api` address.

The receiver rejects any delivery that is not authenticated for that listing (a GitHub HMAC signature or the GitLab secret token), and limits each webhook URL to 60 deliveries a minute.

Things to know:

* The ALB must be internal. API Gateway connects to the addresses the ALB's DNS name resolves to, and an internet-facing ALB resolves to public addresses the VPC link cannot reach; Terraform stops with an error in that case. An internet-facing ALB that is open to the internet does not need this.
* There is no source IP allowlist. GitHub and GitLab do not sign their source addresses, and the providers' ranges change, while every delivery is already authenticated. If you need one, put a REST API with a resource policy, or AWS WAF, in front instead.
* A VPC link with no traffic for 60 days goes inactive, and the next delivery fails while AWS reprovisions it, which takes a few minutes. GitHub does not retry failed deliveries automatically; use **Redeliver** on the hook, or run the sync by hand from the **Sync** tab.
* API Gateway throttling is approximate and meant as a backstop. Requests over the limit get `429` and show as failed in the provider's delivery log.
* VPC links are not available in every Availability Zone (for example `use1-az3`). If `terraform apply` reports an unsupported zone, use private subnets in other zones.
* Turning it on adds `WEBHOOK_PUBLIC_URL` to the API task definition, which rolls the API tasks once.
* Cost: about $1 per million deliveries; the VPC link itself is free.

## Required IAM permissions

The IAM principal running Terraform needs permission to manage resources across these services. The simplest path is to attach the AWS-managed policies below; for a tighter custom policy, see [Hardened IAM policy](aws-terraform.md#hardened-iam-policy).

| Service                             | Managed policy                    |
| ----------------------------------- | --------------------------------- |
| VPC, EC2                            | `AmazonEC2FullAccess`             |
| ECS                                 | `AmazonECS_FullAccess`            |
| RDS                                 | `AmazonRDSFullAccess`             |
| ElastiCache                         | `AmazonElastiCacheFullAccess`     |
| Load balancer                       | `ElasticLoadBalancingFullAccess`  |
| Certificates                        | `AWSCertificateManagerFullAccess` |
| DNS                                 | `AmazonRoute53FullAccess`         |
| IAM (creates ECS + EC2 + RDS roles) | `IAMFullAccess`                   |
| Parameter Store + Session Manager   | `AmazonSSMFullAccess`             |
| S3 (backups bucket)                 | `AmazonS3FullAccess`              |
| Logs                                | `CloudWatchLogsFullAccess`        |
| Application Auto Scaling            | `AutoScalingFullAccess`           |

## Operating the install

### Tail an ECS service

```bash
aws logs tail /aws/ecs/observal-prod/api --follow
```

Or get all log group names:

```bash
terraform output log_group_names
```

### Force a rolling deploy

```bash
aws ecs update-service \
  --cluster $(terraform output -raw ecs_cluster_name) \
  --service observal-prod-api \
  --force-new-deployment
```

### Re-run migrations / seeds

The init task runs automatically on every `image_tag` change. To run it manually:

```bash
$(terraform output -raw init_run_task_command)
```

### Shell into the data host

```bash
$(terraform output -raw data_host_ssm_session_command)
# inside the session:
sudo journalctl -u observal-bootstrap -f                                 # cloud-init / first-boot logs
sudo docker compose -f /opt/observal/docker-compose.data.yml ps          # CH/Grafana/Prom status
sudo docker compose -f /opt/observal/docker-compose.data.yml logs -f clickhouse
```

### Read a generated secret

```bash
aws ssm get-parameter --with-decryption \
  --name /observal-prod/SECRET_KEY \
  --query Parameter.Value --output text
```

The Terraform run generates and stores `DB_PASSWORD`, `CLICKHOUSE_PASSWORD`, `SECRET_KEY`, and `GRAFANA_ADMIN_PASSWORD`, plus pre-built `DATABASE_URL`, `REDIS_URL`, and `CLICKHOUSE_URL` connection strings. ECS injects these into tasks at start; you never paste them in.

### Upgrade to a new Observal release

```hcl
# terraform.tfvars
image_tag = "v1.5.0"
```

```bash
terraform apply
```

The init task re-runs migrations against the live RDS, then ECS rolls each service over with zero downtime.

### Resize the data volume

```hcl
# terraform.tfvars
data_volume_size_gb = 250
```

```bash
terraform apply
# then SSM into the data host:
sudo growpart /dev/nvme1n1 1 || true
sudo resize2fs /dev/nvme1n1
```

### Destroy

```bash
terraform destroy
```

In `prod`, RDS has `deletion_protection = true` and `skip_final_snapshot = false`. Disable both manually before destroy if that is really what you want. The S3 backups bucket also blocks destroy unless `backup_bucket_force_destroy = true`.

## Remote state

By default, Terraform writes state to your laptop (`terraform.tfstate`). For a real install, configure remote state so the install can be managed by anyone on your team and state isn't lost.

1. Create an S3 bucket (versioned, encrypted) and a DynamoDB table with hash key `LockID`.
2.  Uncomment and fill the backend block in `versions.tf`:

    ```hcl
    backend "s3" {
      bucket         = "your-tf-state-bucket"
      key            = "observal/prod/terraform.tfstate"
      region         = "us-east-1"
      dynamodb_table = "your-tf-lock-table"
      encrypt        = true
    }
    ```
3. Re-run `terraform init` and answer "yes" when prompted to migrate state.

## Cost expectations

Rough monthly baseline in `us-east-1` at on-demand rates (May 2026):

| Component                           | \~$/month    |
| ----------------------------------- | ------------ |
| Fargate api 2× (0.5 vCPU / 1 GB)    | $30          |
| Fargate web 2× (0.25 vCPU / 0.5 GB) | $15          |
| Fargate worker 1× (0.5 vCPU / 1 GB) | $15          |
| EC2 `t3.large` (data host)          | $60          |
| RDS `db.t4g.small` Multi-AZ         | $50          |
| ElastiCache (2× `cache.t4g.micro`)  | $25          |
| ALB                                 | $20          |
| Webhook API Gateway (only with `enable_webhook_ingress`) | \<$1 |
| NAT Gateway                         | $33 + egress |
| EBS gp3 100 GB                      | $8           |
| S3 backups (1 GB cold)              | $0.10        |
| **Baseline**                        | **\~$255**   |

Set `environment = "staging"` to drop RDS to single-AZ, run a single Redis node, and skip RDS deletion protection, typically halves the bill. Drop `worker_desired_count` and `web_desired_count` for further savings.

## Production hardening checklist

The defaults are safe but conservative. Before pointing real traffic at this:

* [ ] Switch to remote state (S3 + DynamoDB)
* [ ] Restrict `alb_ingress_cidrs` to known networks
* [ ] Enable AWS GuardDuty + Config in the account
* [ ] Add CloudWatch alarms on RDS `CPUUtilization`, `FreeableMemory`, ECS service CPU, ALB `HTTPCode_Target_5XX_Count`
* [ ] Attach AWS WAF to the ALB
* [ ] Set `transit_encryption_enabled = true` on the ElastiCache replication group and switch `REDIS_URL` to `rediss://...`
* [ ] Configure Observal SSO. See [Authentication and SSO](authentication.md)
* [ ] Test the [backup and restore](backup-and-restore.md) procedure end-to-end
* [ ] Replace the GitHub tarball download in `user-data.sh.tftpl` with an artifact URL you control

## Troubleshooting

**`terraform apply` succeeds but the URL returns 502.** The api/web tasks are still starting. Watch the service:

```bash
aws ecs describe-services \
  --cluster $(terraform output -raw ecs_cluster_name) \
  --services observal-prod-api \
  --query 'services[0].deployments'
aws logs tail /aws/ecs/observal-prod/api --follow
```

The `null_resource.run_init` step also runs migrations before the api service comes up. If the init task fails, the api service won't start.

**Init task fails.** Check the `/aws/ecs/observal-prod/init` log group. The most common failures are:

* RDS not yet reachable (transient on first apply; re-running fixes it)
* Migration error (look at the entrypoint output)

To re-run by hand: `$(terraform output -raw init_run_task_command)`.

**ALB target health is `unhealthy`.**

* For `api`: health check hits `/readyz` on port 8000. Tail the api log group for startup errors.
* For `web`: health check hits `/` on port 3000. Check the web log group.
* For Grafana: health check hits `/api/health` on port 3001. SSM into the data host and check `docker compose ps`.

**ACM certificate stuck in `Pending validation`.** The DNS validation records were not created in your hosted zone. Verify `route53_zone_id` is correct and that the IAM principal had `route53:ChangeResourceRecordSets` permission at apply time. Re-run `terraform apply` after fixing.

**RDS storage is full.** `max_allocated_storage` autoscales up to 500 GB by default. Raise `db_max_allocated_storage_gb` if you have heavier audit-log volume.

For application-level issues (login fails, traces missing) see [Troubleshooting](troubleshooting.md).
