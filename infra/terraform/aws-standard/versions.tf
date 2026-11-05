# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.70" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
    null   = { source = "hashicorp/null", version = "~> 3.2" }
  }

  # Uncomment and configure for remote state.
  # backend "s3" {
  #   bucket         = "your-tf-state-bucket"
  #   key            = "observal/standard/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "your-tf-lock-table"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "Observal"
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}
