<!-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.work@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Agentic Resource Registry on AWS EC2 — Single Instance

Deploy Agentic Resource Registry on a single EC2 instance with everything running in Docker. Simple, self-contained, no managed services required.

## What it creates

- **EC2 instance** (Ubuntu 24.04) with Docker, Docker Compose, certbot, git
- **Elastic IP** (static public IP that survives instance stop/start)
- **Security group** (ports 80 + 443 open)
- **IAM instance profile** (SSM access for remote management — no SSH keys needed)
- **Route53 DNS record** (optional)

All Agentic Resource Registry components (API, web frontend, worker, PostgreSQL, Redis, ClickHouse, Grafana, nginx) run as Docker containers on the single instance.

## Architecture

```
┌─────────────────────────────────────────┐
│  EC2 Instance (t3.large)                │
│                                         │
│  ┌─────────┐  ┌─────────┐  ┌────────┐  │
│  │  nginx   │  │   API   │  │  Web   │  │
│  │  (lb)    │  │  :8000  │  │ :3000  │  │
│  └────┬─────┘  └────┬────┘  └────────┘  │
│       │              │                   │
│  ┌────┴────┐  ┌──────┴──┐  ┌─────────┐  │
│  │ Worker  │  │Postgres │  │  Redis  │  │
│  └─────────┘  └─────────┘  └─────────┘  │
│                                         │
│  ┌───────────┐  ┌──────────┐            │
│  │ClickHouse │  │ Grafana  │            │
│  └───────────┘  └──────────┘            │
└─────────────────────────────────────────┘
```

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Terraform | >= 1.5 | Infrastructure provisioning |
| AWS CLI | v2 | SSM commands for deployment |
| AWS credentials | — | Account with EC2, IAM, VPC, EIP permissions |

## Quick Start

```bash
# 1. Configure
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values

# 2. Provision infrastructure
terraform init
terraform apply

# 3. Deploy Agentic Resource Registry
./deploy.sh
```

That's it. The deploy script clones Agentic Resource Registry, configures it, builds Docker images, and starts everything. It takes 8-12 minutes on first run (Docker build from scratch).

## Configuration

### Minimal (IP-only, no domain)

```hcl
name          = "mycompany"
region        = "us-east-1"
instance_type = "t3.large"
```

Access via `http://<elastic-ip>` after deploy.

### With custom domain + HTTPS

```hcl
name            = "mycompany"
region          = "us-east-1"
instance_type   = "t3.large"
domain          = "observal.mycompany.io"
route53_zone_id = "Z1234567890ABC"
```

The deploy script automatically obtains a Let's Encrypt TLS certificate and configures HTTPS.

### Enabling Google OAuth (and other env overrides)

Any value in `.env.example` can be overridden via the `env_overrides` map. To turn on Google sign-in:

```hcl
env_overrides = {
  GOOGLE_OAUTH_CLIENT_ID       = "1234567890-abc...apps.googleusercontent.com"
  GOOGLE_OAUTH_CLIENT_SECRET   = "GOCSPX-..."
  GOOGLE_OAUTH_ALLOWED_DOMAINS = "acme.com,acme.io"  # optional
}
```

After `terraform apply` re-runs the user-data and the API container restarts, the login page shows a "Sign in with Google" button. The same mechanism configures the generic OIDC slot (`OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET`, `OAUTH_SERVER_METADATA_URL`). See [Authentication and SSO](../../../docs/self-hosting/authentication.md) for the full setup walkthrough.

### Instance sizes

| Type | vCPU | RAM | Recommended for |
|------|------|-----|-----------------|
| t3.medium | 2 | 4 GB | Dev/testing (< 5 users) |
| t3.large | 2 | 8 GB | Small teams (5-20 users) |
| t3.xlarge | 4 | 16 GB | Medium teams (20-50 users) |
| t3.2xlarge | 8 | 32 GB | Large teams (50+ users) |

## Accessing the Instance

No SSH keys needed. Connect via AWS Systems Manager:

```bash
aws ssm start-session --target $(terraform output -raw instance_id) --region $(terraform output -raw region)
```

Once connected:

```bash
sudo -i
cd /opt/observal
docker compose --env-file .env -f docker/docker-compose.yml -f docker/docker-compose.production.yml ps
docker compose --env-file .env -f docker/docker-compose.yml -f docker/docker-compose.production.yml logs -f observal-api
```

## Updating / Redeploying

To deploy a new version:

```bash
# Update the ref in terraform.tfvars, then:
./deploy.sh
```

Or manually on the instance:

```bash
sudo -i
cd /opt/observal
git fetch origin && git checkout <new-tag-or-branch>
docker compose --env-file .env -f docker/docker-compose.yml -f docker/docker-compose.production.yml build
docker compose --env-file .env -f docker/docker-compose.yml -f docker/docker-compose.production.yml up -d
```

## Stopping / Starting (cost saving)

Stop the instance (no compute charges while stopped, data preserved):

```bash
aws ec2 stop-instances --instance-ids $(terraform output -raw instance_id) --region $(terraform output -raw region)
```

Start it back:

```bash
aws ec2 start-instances --instance-ids $(terraform output -raw instance_id) --region $(terraform output -raw region)
# Wait ~60s, then access normally. Docker containers auto-start.
```

## Destroying

```bash
terraform destroy
```

This removes all AWS resources. Data is not recoverable after destroy.

## Default Credentials

After first deploy (if `SEED_DEMO_ACCOUNTS=true` in .env):

| Role | Email | Password |
|------|-------|----------|
| Super Admin | super@demo.example | super-changeme |
| Admin | admin@demo.example | admin-changeme |
| Reviewer | reviewer@demo.example | reviewer-changeme |
| User | user@demo.example | user-changeme |

**Change these immediately** after first login via the Settings page.

## Comparison with ECS Fargate deployment

For production deployments with autoscaling, managed databases (RDS + ElastiCache), and high availability, see [`../aws/`](../aws/) (ECS Fargate).

| Feature | This (EC2) | ECS Fargate (`../aws/`) |
|---------|-----------|------------------------|
| Setup complexity | Low | High |
| Cost (small team) | ~$60/month | ~$300/month |
| Autoscaling | Manual | Automatic |
| Database | Docker (on-instance) | RDS (managed, Multi-AZ) |
| HA / Failover | None | Built-in |
| Best for | Dev, small teams, demos | Production, large orgs |
