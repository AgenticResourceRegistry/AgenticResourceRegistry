<!-- SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Terraform state bootstrap

One-time setup that creates the S3 bucket and DynamoDB lock table the main Agentic Resource Registry module uses for remote state. Run this **once per AWS account**, before the first `terraform init` in `infra/terraform/aws/`.

## What it creates

| Resource | Why |
|---|---|
| `aws_s3_bucket` (versioned, AES256, public-access-blocked, TLS-only) | Holds `terraform.tfstate`; versioning lets you recover from a corrupted state |
| `aws_dynamodb_table` (PAY_PER_REQUEST, PITR enabled) | State locking - prevents two `apply`s from corrupting state |

Both have `prevent_destroy = true`. Losing them strands your live infra from Terraform.

## Usage

```bash
cd infra/terraform/aws/bootstrap

terraform init
terraform apply
```

Then paste the output into the main module's `versions.tf`:

```bash
terraform output -raw backend_config
```

```hcl
# infra/terraform/aws/versions.tf
terraform {
  # ...
  backend "s3" {
    bucket         = "observal-tf-state-012432678098"
    key            = "observal/observal/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "observal-tf-locks"
    encrypt        = true
  }
}
```

Now `terraform init` (in the main module) will prompt to migrate any existing local state into S3:

```bash
cd ../   # back to infra/terraform/aws
terraform init -migrate-state
```

## State of this module's own state

Local. That's intentional. Bootstrapping the state backend can't itself live in the state backend. Commit the resulting `terraform.tfstate` for the bootstrap module to a private location, or treat the resources as one-shot infrastructure you'll never re-apply.

## Variables

| Name | Default | Notes |
|---|---|---|
| `region` | `us-east-1` | Pick once; moving the bucket later is painful |
| `name_prefix` | `observal` | Bucket → `<prefix>-tf-state-<account-id>`; table → `<prefix>-tf-locks` |
