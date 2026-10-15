# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# Public webhook entry point for private installs (opt-in).
#
# When the main ALB is internal (alb_scheme = "internal"), github.com and
# gitlab.com cannot deliver webhooks, so MCP repository sync has nothing to
# react to. This adds an API Gateway HTTP API that:
#   - has one route per provider, POST /api/v1/webhooks/<provider>/mcp/{sync_id},
#     and answers 404 for everything else, so the UI, API and login stay private,
#   - reaches the existing ALB privately through a VPC link (no public subnets,
#     internet gateway or second load balancer needed),
#   - throttles deliveries before they reach the VPC.
# The receiver authenticates each delivery (GitHub HMAC signature, GitLab secret
# token) before doing anything, so there is no source IP allowlist to keep current.
#
# The ALB must be internal: API Gateway connects to the addresses the ALB's DNS
# name resolves to, and an internet-facing ALB resolves to public addresses the
# VPC link cannot reach.

locals {
  webhook_ingress_enabled = var.enable_webhook_ingress
  webhook_custom_domain   = local.webhook_ingress_enabled && var.webhook_domain_name != ""
  webhook_zone_id         = var.webhook_route53_zone_id != "" ? var.webhook_route53_zone_id : var.route53_zone_id
  webhook_public_url = !local.webhook_ingress_enabled ? "" : (
    local.webhook_custom_domain ? "https://${var.webhook_domain_name}" : aws_apigatewayv2_api.webhook[0].api_endpoint
  )
  # The VPC link connects to the listener app traffic already uses.
  webhook_alb_port = local.enable_tls ? 443 : 80
}

resource "terraform_data" "webhook_ingress_validation" {
  count = local.webhook_ingress_enabled ? 1 : 0

  lifecycle {
    precondition {
      condition     = var.alb_scheme == "internal"
      error_message = "enable_webhook_ingress needs alb_scheme = \"internal\": the API Gateway VPC link cannot reach an internet-facing ALB. An internet-facing ALB open to the internet does not need this endpoint."
    }
    precondition {
      condition     = !local.webhook_custom_domain || local.webhook_zone_id != ""
      error_message = "webhook_domain_name needs a public Route 53 zone: set webhook_route53_zone_id (or route53_zone_id)."
    }
  }
}

# ── VPC link to the existing ALB ───────────────────────────────────────────

resource "aws_security_group" "webhook_vpc_link" {
  count       = local.webhook_ingress_enabled ? 1 : 0
  name        = "${local.name}-webhook-vpc-link"
  description = "API Gateway VPC link for git provider webhooks. Egress to the ALB port only."
  vpc_id      = local.vpc_id

  # The ALB's security group admits this group, so this side uses the VPC CIDR
  # rather than the ALB group (two groups referencing each other form a cycle).
  egress {
    description = "Webhook deliveries to the ALB"
    from_port   = local.webhook_alb_port
    to_port     = local.webhook_alb_port
    protocol    = "tcp"
    cidr_blocks = [local.vpc_cidr]
  }

  tags = { Name = "${local.name}-webhook-vpc-link-sg" }
}

resource "aws_apigatewayv2_vpc_link" "webhook" {
  count              = local.webhook_ingress_enabled ? 1 : 0
  name               = "${local.name}-webhooks"
  subnet_ids         = local.private_subnet_ids
  security_group_ids = [aws_security_group.webhook_vpc_link[0].id]
  tags               = { Name = "${local.name}-webhooks" }
}

# ── HTTP API ───────────────────────────────────────────────────────────────

resource "aws_apigatewayv2_api" "webhook" {
  count         = local.webhook_ingress_enabled ? 1 : 0
  name          = "${local.name}-webhooks"
  description   = "Git provider webhook deliveries for MCP repository sync."
  protocol_type = "HTTP"
  # With a custom domain, that is the only way in.
  disable_execute_api_endpoint = local.webhook_custom_domain
  tags                         = { Name = "${local.name}-webhooks" }
}

resource "aws_apigatewayv2_integration" "webhook" {
  count              = local.webhook_ingress_enabled ? 1 : 0
  api_id             = aws_apigatewayv2_api.webhook[0].id
  integration_type   = "HTTP_PROXY"
  integration_method = "POST"
  integration_uri    = local.enable_tls ? aws_lb_listener.https[0].arn : aws_lb_listener.http.arn
  connection_type    = "VPC_LINK"
  connection_id      = aws_apigatewayv2_vpc_link.webhook[0].id
  # GitHub and GitLab give up after 10 seconds; the receiver answers well within that.
  timeout_milliseconds = 10000

  request_parameters = {
    "overwrite:path" = "$request.path"
  }

  dynamic "tls_config" {
    for_each = local.enable_tls ? [1] : []
    content {
      server_name_to_verify = var.domain_name
    }
  }
}

# Path parameters cannot contain "/", so nothing but these exact paths matches.
resource "aws_apigatewayv2_route" "webhook" {
  for_each  = local.webhook_ingress_enabled ? toset(var.webhook_providers) : toset([])
  api_id    = aws_apigatewayv2_api.webhook[0].id
  route_key = "POST /api/v1/webhooks/${each.value}/mcp/{sync_id}"
  target    = "integrations/${aws_apigatewayv2_integration.webhook[0].id}"
}

resource "aws_cloudwatch_log_group" "webhook" {
  count             = local.webhook_ingress_enabled ? 1 : 0
  name              = "/aws/apigateway/${local.name}-webhooks"
  retention_in_days = var.log_retention_days
}

resource "aws_apigatewayv2_stage" "webhook" {
  count       = local.webhook_ingress_enabled ? 1 : 0
  api_id      = aws_apigatewayv2_api.webhook[0].id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_rate_limit  = var.webhook_throttle_rate_limit
    throttling_burst_limit = var.webhook_throttle_burst_limit
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.webhook[0].arn
    format = jsonencode({
      requestId          = "$context.requestId"
      requestTime        = "$context.requestTime"
      sourceIp           = "$context.identity.sourceIp"
      routeKey           = "$context.routeKey"
      path               = "$context.path"
      status             = "$context.status"
      integrationStatus  = "$context.integrationStatus"
      integrationError   = "$context.integrationErrorMessage"
      integrationLatency = "$context.integrationLatency"
      responseLength     = "$context.responseLength"
    })
  }

  depends_on = [aws_apigatewayv2_route.webhook]
}

# ── Optional custom domain ─────────────────────────────────────────────────
# The execute-api URL already has a valid certificate. A custom domain keeps
# the webhook URL stable if the API is ever recreated.

resource "aws_acm_certificate" "webhook" {
  count             = local.webhook_custom_domain ? 1 : 0
  domain_name       = var.webhook_domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [terraform_data.webhook_ingress_validation]
}

resource "aws_route53_record" "webhook_cert_validation" {
  for_each = local.webhook_custom_domain ? {
    for dvo in aws_acm_certificate.webhook[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  zone_id         = local.webhook_zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "webhook" {
  count                   = local.webhook_custom_domain ? 1 : 0
  certificate_arn         = aws_acm_certificate.webhook[0].arn
  validation_record_fqdns = [for r in aws_route53_record.webhook_cert_validation : r.fqdn]
}

resource "aws_apigatewayv2_domain_name" "webhook" {
  count       = local.webhook_custom_domain ? 1 : 0
  domain_name = var.webhook_domain_name

  domain_name_configuration {
    certificate_arn = aws_acm_certificate_validation.webhook[0].certificate_arn
    endpoint_type   = "REGIONAL"
    security_policy = "TLS_1_2"
  }
}

resource "aws_apigatewayv2_api_mapping" "webhook" {
  count       = local.webhook_custom_domain ? 1 : 0
  api_id      = aws_apigatewayv2_api.webhook[0].id
  domain_name = aws_apigatewayv2_domain_name.webhook[0].id
  stage       = aws_apigatewayv2_stage.webhook[0].id
}

resource "aws_route53_record" "webhook" {
  count   = local.webhook_custom_domain ? 1 : 0
  zone_id = local.webhook_zone_id
  name    = var.webhook_domain_name
  type    = "A"

  alias {
    name                   = aws_apigatewayv2_domain_name.webhook[0].domain_name_configuration[0].target_domain_name
    zone_id                = aws_apigatewayv2_domain_name.webhook[0].domain_name_configuration[0].hosted_zone_id
    evaluate_target_health = false
  }
}
