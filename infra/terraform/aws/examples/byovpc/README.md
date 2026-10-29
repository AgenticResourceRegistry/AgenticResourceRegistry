<!-- SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Bring Your Own VPC Example

Deploy Agentic Resource Registry into an existing VPC instead of having Terraform create one.

## Prerequisites

Your existing VPC must have:

- **DNS support enabled** (`enableDnsSupport = true`, `enableDnsHostnames = true`)
- **At least 2 private subnets** in different AZs with outbound internet access (via NAT Gateway or Transit Gateway) for container image pulls and AWS API calls
- **At least 2 public subnets** in different AZs (only required if `alb_scheme = "internet-facing"`)

## Usage

```bash
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your VPC and subnet IDs

terraform init
terraform apply
```

## Optional: Bring Your Own Security Groups

For environments with strict firewall policies, you can supply pre-created security groups:

- **ALB SG** must allow inbound TCP 80 and 443 from your desired client CIDRs
- **ECS SG** must allow inbound TCP 8000 and 3000 from the ALB security group, and outbound to all (for image pulls and AWS APIs)

If not provided, the module creates these security groups automatically.
