<!-- SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Minimal example

End-to-end working call of the Agentic Resource Registry AWS module with sensible defaults.

```bash
cd infra/terraform/aws/examples/minimal
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars

terraform init
terraform plan -out tf.plan
terraform apply tf.plan
```

A clean apply takes ~12–15 minutes (RDS provisioning dominates).

When it finishes:

```bash
terraform output app_url
$(terraform output -raw data_host_ssm_session_command)
```

## What gets provisioned

Everything the parent module provisions. See [`../../README.md`](../../README.md). In its default form (no `domain_name`), this example brings up:

- VPC with 2-AZ public/private subnets, NAT gateway
- ALB on HTTP only (no HTTPS - supply `domain_name` + `route53_zone_id` for ACM)
- ECS Fargate: 2× api, 2× web, 1× worker
- RDS Postgres 16 (Multi-AZ on `prod`)
- ElastiCache Redis 7 (2-node failover on `prod`)
- One EC2 (t3.large) hosting ClickHouse, 100 GB EBS. Set `observability_stack = "grafana"` for bundled dashboards.
- S3 backups bucket with lifecycle to Glacier
- CloudWatch log groups per service

## Tearing it down

```bash
terraform destroy
```

If the apply hit `deletion_protection = true` on RDS (the `prod` default), set `environment = "staging"` first or remove the protection in the AWS console.
