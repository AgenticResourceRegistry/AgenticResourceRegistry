<!-- SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Observal AWS Standard Module

Single-account, cost-optimized Terraform deployment for Observal on AWS. Runs the full stack (API, web frontend, background workers, Postgres, Redis, ClickHouse, Grafana, Prometheus) on two EC2 instances — one ECS EC2 cluster node for containers and one data-tier host for stateful services.

## Architecture

```
Internet
    |
   ALB (public subnets)
    |
    +-- /api/*, /auth/* --> ECS EC2: api container (port 8000)
    +-- /grafana/*      --> Data host: Grafana (port 3001)
    +-- /* (default)    --> ECS EC2: web container (port 3000)
    |
Private subnets:
    +-- ECS EC2 instance (t3.large) running api, web, worker tasks
    +-- Data host EC2 (t3.medium) running:
        - Postgres 18 (port 5432)
        - Redis 8 (port 6379)
        - ClickHouse 26.5 (ports 8123, 9000)
        - Grafana (port 3001)
        - Prometheus (port 9090)
```

Internal DNS (`observal.internal` private Route53 zone) connects ECS tasks to the data host via stable names: `postgres.observal.internal`, `redis.observal.internal`, `clickhouse.observal.internal`.

## Estimated Monthly Cost

| Preset | ECS Instance | Data Host | EBS | NAT Gateway | ALB | Total |
|--------|-------------|-----------|-----|-------------|-----|-------|
| small  | t3.large (~$60) | t3.medium (~$30) | 50 GB ($4) | ~$32 | ~$16 | ~$120/mo |
| medium | t3.large (~$60) | t3.medium (~$30) | 100 GB ($8) | ~$32 | ~$16 | ~$155/mo |

## Usage

```bash
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values

terraform init
terraform plan
terraform apply
```

## BYO-VPC

To deploy into an existing VPC, set `vpc_id`, `public_subnet_ids`, and `private_subnet_ids` in your tfvars. The module will skip VPC/subnet/IGW/NAT creation.

## TLS

Set `domain_name`, `route53_zone_id`, and `enable_tls = true` to provision an ACM certificate with DNS validation and enable HTTPS on the ALB.

## Git webhooks on a private install

With `alb_scheme = "internal"`, github.com and gitlab.com cannot deliver the webhooks that MCP repository sync relies on. Set `enable_webhook_ingress = true` and `webhook_providers` (`github`, `gitlab`, or both) to add an API Gateway HTTP API with one route per provider, `POST /api/v1/webhooks/<provider>/mcp/{sync_id}`, that reaches the ALB privately through a VPC link and answers `404` to everything else. See [Git webhooks on a private install](../../../docs/self-hosting/aws-terraform.md#git-webhooks-on-a-private-install).

## Differences from Enterprise Module

| Feature | Standard | Enterprise (`infra/terraform/aws/`) |
|---------|----------|--------------------------------------|
| Compute | ECS on EC2 (1 instance) | ECS Fargate (auto-scaling) |
| Database | Postgres on EC2 | RDS Postgres (managed) |
| Cache | Redis on EC2 | ElastiCache Redis (managed) |
| ClickHouse | EC2 (same host) | Dedicated EC2 host |
| HA | Single-AZ data tier | Multi-AZ managed services |
| Cost | ~$120-155/mo | ~$300-800/mo |
| BYO Security Groups | No | Yes |
| VPC Flow Logs | No | Yes |
| Auto-scaling | ASG for ECS instances | Fargate + Application Auto Scaling |

## SPDX

SPDX-FileCopyrightText: 2026 Observal
SPDX-License-Identifier: Apache-2.0
