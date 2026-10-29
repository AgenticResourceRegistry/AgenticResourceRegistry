# VPC Module

Creates a complete VPC with public and private subnets, an internet gateway, a NAT gateway, public and private route tables, and optional VPC flow logs with CloudWatch log group and IAM role.

## Usage

```hcl
module "vpc" {
  source = "./modules/vpc"

  name                 = "observal-prod"
  vpc_cidr             = "10.42.0.0/16"
  azs                  = ["us-east-1a", "us-east-1b"]
  public_subnet_cidrs  = ["10.42.0.0/24", "10.42.1.0/24"]
  private_subnet_cidrs = ["10.42.10.0/24", "10.42.11.0/24"]
  log_retention_days   = 30
  enable_flow_logs     = true

  tags = {
    Environment = "prod"
    Project     = "observal"
  }
}
```

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| `name` | Name prefix for all VPC resources. | `string` | n/a | yes |
| `vpc_cidr` | CIDR block for the VPC. | `string` | `"10.42.0.0/16"` | no |
| `azs` | List of availability zones to use. | `list(string)` | n/a | yes |
| `public_subnet_cidrs` | CIDRs for public subnets (one per AZ). Empty list derives from vpc_cidr via cidrsubnet; non-empty list must match length(azs). | `list(string)` | `[]` | no |
| `private_subnet_cidrs` | CIDRs for private subnets (one per AZ). Empty list derives from vpc_cidr via cidrsubnet; non-empty list must match length(azs). | `list(string)` | `[]` | no |
| `log_retention_days` | CloudWatch log retention for flow logs. | `number` | `30` | no |
| `enable_flow_logs` | Enable VPC flow logs. | `bool` | `true` | no |
| `tags` | Tags to apply to all resources. | `map(string)` | `{}` | no |

## Outputs

| Name | Description |
|------|-------------|
| `vpc_id` | ID of the created VPC. |
| `vpc_cidr` | CIDR block of the created VPC. |
| `public_subnet_ids` | IDs of the public subnets. |
| `private_subnet_ids` | IDs of the private subnets. |
| `nat_gateway_ips` | Elastic IPs of the NAT gateway. |
| `internet_gateway_id` | ID of the internet gateway. |
| `flow_logs_log_group_name` | Name of the CloudWatch log group for VPC flow logs. |
