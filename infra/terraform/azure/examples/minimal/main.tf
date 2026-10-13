# SPDX-FileCopyrightText: 2026 Tanvi Reddy <reddyplayer22@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# Minimal example: deploy Observal to Azure with defaults.

module "observal" {
  source = "../../"

  subscription_id = "6a0284fc-791a-4d77-9520-69cdaa79ba44"
  environment     = "staging"
  location        = "eastus"
}

output "api_url" {
  value = module.observal.api_url
}

output "web_url" {
  value = module.observal.web_url
}
