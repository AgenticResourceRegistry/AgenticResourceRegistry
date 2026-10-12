<!-- SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Observal on AWS - Terraform

Production-shaped self-hosted Observal in your own AWS account. One `terraform apply` provisions:

- **VPC**: public + private subnets across 2 AZs, NAT gateway, VPC flow logs
- **Application Load Balancer**: HTTPS via DNS-validated ACM (when domain supplied), path-based routing
- **ECS Fargate cluster** - `api`, `web`, `worker` as separate services with target-tracking CPU autoscaling. `init` runs as a one-shot `RunTask` on every image bump
- **RDS Postgres 16** - Multi-AZ on `prod`, encrypted, automated backups, Performance Insights, Enhanced Monitoring, log exports
- **ElastiCache Redis 7** - 2-node replication group with automatic failover on `prod`, slow-log to CloudWatch
- **Data tier EC2**: single host running ClickHouse on EBS gp3, optional Prometheus and Grafana, ENI with static private IP, internal Route 53 zone for DNS, daily ClickHouse → S3 snapshot via systemd timer
- **S3 backups bucket**: versioned, AES256, lifecycle to STANDARD_IA → GLACIER_IR → expire, TLS-only access
- **CloudWatch log groups**: per-service for ECS tasks, data host, RDS, Redis, VPC flow logs
- **SSM Parameter Store**: generated DB / ClickHouse / SECRET_KEY / optional Grafana passwords, plus pre-built connection URLs injected into ECS tasks
- **SSM Session Manager**: shell access to the data host, no SSH

## Architecture

```
                      ┌──────────────────────────┐
                Internet → │   ALB (443)   │
                      └──┬─────┬─────┬──────────┘
              path /api/*│     │/grafana/* optional
                         ▼     ▼     ▼ default
              ┌──────────────┐  ┌────────────────┐  ┌──────────┐
              │ ECS Fargate  │  │  EC2 data host │  │   ECS    │
              │ api service  │  │  (single AZ)   │  │   web    │
              │  2..10×      │  │  ClickHouse    │  │ 2..6×    │
              └─────┬────────┘  │  optional      │  └──────────┘
                    │           │  observability │
              ┌─────┴────────┐  └────────┬───────┘
              │ ECS Fargate  │           │
              │   worker     │           │
              │   1..5×      │           ▼
              └────┬─────────┘  EBS gp3 100GB at /data
                   │
                   ▼
         ┌──────────────────────────────┐
         │  RDS Postgres 16 (Multi-AZ)  │
         │  ElastiCache Redis (2-node)  │
         └──────────────────────────────┘
```

The stateless app tier (api/web/worker) lives on Fargate across both AZs with autoscaling and rolling deploys. The stateful data tier lives on a single EC2 with EBS so ClickHouse keeps its disk across instance replacements. Prometheus and Grafana are enabled with `observability_stack`. ClickHouse HA is out of scope.

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Terraform | ≥ 1.6 | `brew install terraform` |
| AWS CLI | ≥ 2.x | `brew install awscli` |
| Session Manager plugin | latest | [docs](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) |

You also need:

- An AWS account with billing enabled.
- An IAM principal with permission to manage VPC, EC2, RDS, ElastiCache, ECS, IAM, ALB, ACM, Route 53, SSM, CloudWatch, S3, DynamoDB.
- (Optional, for HTTPS) a Route 53 public hosted zone for your domain.

## Production setup (recommended)

Five steps from a fresh AWS account to a running install. Skip step 2 only if you're evaluating and OK with throwing away local state.

### 1. Authenticate

Any of these is fine. Terraform reads the standard AWS SDK credential chain:

```bash
# Long-lived IAM user
export AWS_PROFILE=observal-prod

# Or AWS SSO / IAM Identity Center
aws sso login --profile observal-prod
export AWS_PROFILE=observal-prod

# Or short-lived session creds
eval "$(aws configure export-credentials --profile observal-prod --format env)"
```

Verify with `aws sts get-caller-identity`.

### 2. Bootstrap remote state - run once per AWS account

This creates the S3 bucket + DynamoDB lock table that hold Terraform state. Without it, state lives only on your laptop. Losing the file orphans live AWS resources.

```bash
cd infra/terraform/aws/bootstrap
terraform init
terraform apply
terraform output -raw backend_config   # copy this
```

Paste the output into the `backend "s3" {}` block in `infra/terraform/aws/versions.tf` (it ships commented out). Details: [`bootstrap/README.md`](bootstrap/README.md).

### 3. Configure inputs

```bash
cd infra/terraform/aws
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars
```

For a real production install you almost certainly want:

```hcl
region          = "us-east-1"
environment     = "prod"
name_prefix     = "observal"

# HTTPS on a custom domain (Route 53 zone must already exist in this account)
domain_name     = "observal.example.com"
route53_zone_id = "Z0123456789ABCDEFGHIJ"

# Lock the ALB to your office / VPN / corporate egress
alb_ingress_cidrs = ["203.0.113.0/24", "198.51.100.42/32"]

# Pin to a specific release instead of latest
image_tag = "v1.42.0"
```

The complete input list is in [`variables.tf`](variables.tf).

### 4. Apply

```bash
terraform init        # picks up the S3 backend from step 2
terraform plan -out tf.plan
terraform apply tf.plan
```

A clean apply takes **~20–30 minutes** (RDS Multi-AZ provisioning dominates). What gets created: ~100 AWS resources end-to-end.

If you skipped step 2, run `terraform init -migrate-state` after configuring the backend and Terraform will move local state into S3.

#### Watching the apply in the AWS Console

While Terraform runs, you can watch resources come up live. Replace `<REGION>` in the URLs below with the region from your `terraform.tfvars`. The names below assume `name_prefix = "observal"` and `environment = "prod"` - if you changed those, substitute accordingly. The pattern is `<name_prefix>-<environment>-<resource>`).

| Resource | Console link (replace `<REGION>`) | What to look for |
|---|---|---|
| RDS Postgres | `https://<REGION>.console.aws.amazon.com/rds/home?region=<REGION>#databases:` | `observal-prod-pg` → Status: `Available` (passes through `Creating` → `Backing-up` → `Modifying`) |
| ElastiCache Redis | `https://<REGION>.console.aws.amazon.com/elasticache/home?region=<REGION>#/redis` | `observal-prod-redis` → Status: `available` |
| ECS Fargate | `https://<REGION>.console.aws.amazon.com/ecs/v2/clusters?region=<REGION>` | Cluster `observal-prod-cluster` → Services tab: `api`/`web`/`worker` showing `Running 2/2` (or 1/1 for worker) |
| ALB | `https://<REGION>.console.aws.amazon.com/ec2/home?region=<REGION>#LoadBalancers:` | `observal-prod-alb` → State: `Active` |
| EC2 data host | `https://<REGION>.console.aws.amazon.com/ec2/home?region=<REGION>#Instances:` | `observal-prod-data-host` → Status check: `2/2 checks passed` |
| VPC | `https://<REGION>.console.aws.amazon.com/vpc/home?region=<REGION>#vpcs:` | `observal-prod-vpc` |
| S3 (state + backups) | `https://s3.console.aws.amazon.com/s3/buckets` | `observal-tf-state-<account_id>` and `observal-prod-backups-<account_id>` (S3 console is global) |
| CloudWatch logs | `https://<REGION>.console.aws.amazon.com/cloudwatch/home?region=<REGION>#logsV2:log-groups` | `/aws/ecs/observal-prod/api`, `/aws/ecs/observal-prod/web`, `/aws/ecs/observal-prod/worker`, `/aws/ec2/observal-prod/data-host`, `/aws/elasticache/observal-prod/redis/slow`, `/aws/vpc/observal-prod/flow-logs` |

> **Region gotcha**: the AWS Console only shows resources in the region selected in the top-right dropdown. If a resource looks missing, you're probably looking at the wrong region.

Typical timing during apply:

| Resource | Appears | Becomes available |
|---|---|---|
| VPC, subnets, IGW, NAT | ~1 min | ~3 min |
| ALB | ~2 min | ~3 min |
| ElastiCache Redis | ~3 min | ~7 min |
| RDS Postgres (Multi-AZ) | ~3 min | **~15–20 min** ← long pole |
| EC2 data host | ~5 min | ~6 min, then ~3 more min for docker-compose |
| ECS services | last | tasks healthy ~2 min after RDS+Redis are ready |

If you prefer the terminal:

```bash
# RDS
watch -n 10 "aws rds describe-db-instances \
  --query 'DBInstances[?DBInstanceIdentifier==\`observal-prod-pg\`].[DBInstanceStatus,Endpoint.Address]' \
  --output table"

# Redis
watch -n 10 "aws elasticache describe-replication-groups \
  --replication-group-id observal-prod-redis \
  --query 'ReplicationGroups[0].Status' --output text"

# ECS services
watch -n 10 "aws ecs describe-services \
  --cluster observal-prod-cluster \
  --services observal-prod-api observal-prod-web observal-prod-worker \
  --query 'services[*].[serviceName,status,runningCount,desiredCount]' \
  --output table"
```

### 5. Verify

```bash
terraform output app_url                        # https://observal.example.com (or ALB DNS in HTTP mode)
terraform output ecs_cluster_name               # observal-prod-cluster
terraform output data_host_ssm_session_command  # SSM session into the data host
```

The api takes ~2–3 minutes to be reachable after `apply` finishes (ECS task startup + ALB health checks). `curl -fsS $(terraform output -raw app_url)/readyz` should return 200.

A working module-call example lives at [`examples/minimal`](examples/minimal/).

## What credentials and inputs are required?

The module generates all application secrets (Postgres password, ClickHouse password, `SECRET_KEY`, Grafana admin) and stores them in SSM Parameter Store as `SecureString`s. ECS task definitions reference them via the `secrets` block, so they're injected as environment variables at task start, never written to disk.

You only need to supply:

1. **AWS credentials**: `AWS_PROFILE`, `aws configure sso`, or `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`.
2. **`region`** (default `us-east-1`).
3. **(Optional) `domain_name` + `route53_zone_id`** for HTTPS on a real domain.
4. **(Optional) `alb_ingress_cidrs`** to lock the ALB to specific IP ranges.

## Operating the install

**Open a shell on the data host (no SSH key needed):**
```bash
$(terraform output -raw data_host_ssm_session_command)
```

**Tail an ECS service:**
```bash
aws logs tail /aws/ecs/observal-prod/api --follow
```

**Force a rolling deploy of the api (e.g. after pushing a new image with the same tag):**
```bash
aws ecs update-service \
  --cluster $(terraform output -raw ecs_cluster_name) \
  --service observal-prod-api \
  --force-new-deployment
```

**Re-run migrations:**
```bash
$(terraform output -raw init_run_task_command)
```

**Read a generated secret:**
```bash
aws ssm get-parameter --with-decryption \
  --name /observal-prod/SECRET_KEY \
  --query Parameter.Value --output text
```

For upgrades, rollbacks, teardown, and DR see [Day-2 operations](#day-2-operations) below.

## Costs (rough, us-east-1, on-demand)

| Resource | Default | ~$/month |
|----------|---------|----------|
| Fargate api 2× (0.5 vCPU / 1 GB) | always-on | $30 |
| Fargate web 2× (0.25 vCPU / 0.5 GB) | always-on | $15 |
| Fargate worker 1× (0.5 vCPU / 1 GB) | always-on | $15 |
| EC2 t3.large (data host) | 1× | $60 |
| RDS db.t4g.small Multi-AZ | 1 | $50 |
| ElastiCache cache.t4g.micro × 2 | 1 | $25 |
| ALB | 1 | $20 |
| NAT Gateway | 1 | $33 + egress |
| EBS gp3 100 GB | 1 | $8 |
| S3 backups (1 GB cold) | - | $0.10 |
| **Total baseline** | | **~$255/month** |

Drop to single-AZ RDS, single Redis node, and `worker_desired_count = 0` for staging by setting `environment != "prod"`.

## Day-2 operations

### Upgrade to a new release
Bump `image_tag` in `terraform.tfvars` and re-apply. The `null_resource.run_init` rerun handles migrations; ECS handles the rolling deploy.

### Roll back
Set `image_tag` to the previous version and re-apply. RDS / ClickHouse data are not affected.

### Tear down
```bash
terraform destroy
```
The bootstrap module's bucket + table have `prevent_destroy = true`. Destroying the main module won't touch them. To remove them too, edit `bootstrap/main.tf` to drop the lifecycle blocks, then `terraform destroy` in `bootstrap/`. Don't do this until you're sure no Observal install in the account still needs the state.

### Disaster recovery
- **Postgres**: automated daily snapshots (7-day retention on prod). Restore via `aws rds restore-db-instance-from-db-snapshot`.
- **ClickHouse**: daily snapshot to the S3 backups bucket via systemd timer (see `user-data.sh.tftpl`). Restore with `clickhouse-client RESTORE`.
- **Terraform state**: S3 versioning is on - recover prior state with `aws s3api list-object-versions` + `cp --version-id`.

## Production hardening checklist

Before using in front of customers:

- [x] Switch the Terraform backend to S3 + DynamoDB - done by the `bootstrap/` module
- [ ] Restrict `alb_ingress_cidrs` to known CIDRs
- [ ] Enable AWS Config + GuardDuty in the account
- [ ] Wire CloudWatch alarms on RDS CPU / freeable memory, ECS service CPU, ALB 5xx
- [ ] Add a WAF in front of the ALB (`aws_wafv2_web_acl_association`)
- [ ] Set `transit_encryption_enabled = true` on the ElastiCache replication group and switch `REDIS_URL` to `rediss://...`
- [ ] Replace the GitHub-tarball pull in `user-data.sh.tftpl` with your own signed artifact location

## Layout

```
bootstrap/                 # one-time S3 state bucket + DynamoDB lock table
versions.tf                # provider versions + (commented) S3 backend
variables.tf               # all inputs
locals.tf                  # AZ lookup, derived names, internal DNS
vpc.tf                     # VPC, subnets, NAT, route tables, flow logs, private zone
security.tf                # security groups (alb, ecs_tasks, data_host, db, redis)
iam.tf                     # ECS execution + task roles, EC2 data-host role
secrets.tf                 # random_password + SSM Parameter Store (raw + URLs)
postgresql.tf              # RDS Postgres + Enhanced Monitoring role
redis.tf                   # ElastiCache replication group
clickhouse.tf              # data-tier EC2 (CH plus optional observability), private DNS
ecs.tf                     # Fargate cluster, task defs, services, autoscaling, init
alb.tf                     # ALB, target groups, listeners, listener rules, ACM
s3.tf                      # backups bucket with lifecycle and TLS-only policy
logs.tf                    # CloudWatch log groups
dns.tf                     # public Route 53 record (alias to ALB)
webhook-ingress.tf         # optional API Gateway endpoint for GitHub/GitLab webhooks on private installs
outputs.tf                 # app_url, ecs cluster/services, log groups, ...
user-data.sh.tftpl         # cloud-init for the data host
terraform.tfvars.example
examples/minimal/          # ready-to-apply module-call example
```

## Git webhooks on a private install

With `alb_scheme = "internal"`, github.com and gitlab.com cannot deliver the webhooks that MCP repository sync relies on. Set `enable_webhook_ingress = true` and `webhook_providers` (`github`, `gitlab`, or both) to add an API Gateway HTTP API with one route per provider, `POST /api/v1/webhooks/<provider>/mcp/{sync_id}`, that reaches the ALB privately through a VPC link and answers `404` to everything else. See [Git webhooks on a private install](../../../docs/self-hosting/aws-terraform.md#git-webhooks-on-a-private-install).

## Troubleshooting

**`No valid credential sources found` on `terraform plan`** - your shell auth (e.g. AWS CLI v2 `aws login` profile) isn't visible to the AWS SDK that Terraform uses. Export the resolved creds:

```bash
eval "$(aws configure export-credentials --profile <name> --format env)"
```

**`The bucket you tried to create already exists`** when running the bootstrap module. S3 bucket names are globally unique. Change `name_prefix` in `bootstrap/terraform.tfvars` and re-apply, or import the existing bucket.

**`Error: getting Application Load Balancer Listener Rule ... too many path patterns`**: fixed; max is 5 per `path_pattern` condition.

**`apply` hangs on RDS**: first-time `db.t4g.small` Multi-AZ provisioning can take 15+ minutes. Check the AWS console; it's almost always the RDS resource still in `creating`.

**App returns 502 right after apply**: ECS tasks need ~2 min to pass ALB health checks. `aws logs tail /aws/ecs/observal-prod/api --follow` shows what the api is doing.
