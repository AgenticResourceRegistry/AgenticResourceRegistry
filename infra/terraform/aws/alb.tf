# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# tfsec:ignore:aws-elb-alb-not-public Public-facing load balancer is the entrypoint for end users; restrict reachability via var.alb_ingress_cidrs and (optionally) a WAF.
resource "aws_lb" "app" {
  name               = "${local.name}-alb"
  internal           = var.alb_scheme == "internal"
  load_balancer_type = "application"
  security_groups    = [local.alb_sg_id]
  subnets            = var.alb_scheme == "internal" ? local.private_subnet_ids : local.public_subnet_ids

  drop_invalid_header_fields = true
  tags                       = { Name = "${local.name}-alb" }
}

# ── Target groups ─────────────────────────────────────────────────────────

resource "aws_lb_target_group" "web" {
  name        = "${local.name}-web-tg"
  port        = 3000
  protocol    = "HTTP"
  vpc_id      = local.vpc_id
  target_type = "ip"

  deregistration_delay = 30

  health_check {
    path                = "/"
    matcher             = "200-399"
    interval            = 30
    timeout             = 10
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = { Name = "${local.name}-web-tg" }
}

resource "aws_lb_target_group" "api" {
  name        = "${local.name}-api-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = local.vpc_id
  target_type = "ip"

  deregistration_delay = 30

  health_check {
    path                = "/readyz"
    matcher             = "200-399"
    interval            = 30
    timeout             = 10
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = { Name = "${local.name}-api-tg" }
}

resource "aws_lb_target_group" "grafana" {
  count       = local.bundled_grafana_available ? 1 : 0
  name        = "${local.name}-grafana-tg"
  port        = 3001
  protocol    = "HTTP"
  vpc_id      = local.vpc_id
  target_type = "ip"

  deregistration_delay = 30

  health_check {
    path                = "/api/health"
    matcher             = "200"
    interval            = 30
    timeout             = 10
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = { Name = "${local.name}-grafana-tg" }
}

resource "aws_lb_target_group_attachment" "grafana" {
  count            = local.bundled_grafana_available ? 1 : 0
  target_group_arn = aws_lb_target_group.grafana[0].arn
  target_id        = aws_network_interface.data_host.private_ip
  port             = 3001
}

# ── HTTP listener ─────────────────────────────────────────────────────────
# When TLS is enabled, redirect to HTTPS. Otherwise terminate HTTP and
# forward to the web target group (eval / no-domain mode).
# tfsec:ignore:aws-elb-http-not-used HTTP listener is a 301 to HTTPS when TLS is enabled; otherwise eval-mode HTTP-only.
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = local.enable_tls ? "redirect" : "forward"

    dynamic "redirect" {
      for_each = local.enable_tls ? [1] : []
      content {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }

    target_group_arn = local.enable_tls ? null : aws_lb_target_group.web.arn
  }
}

# Path-based rules on the HTTP listener (no-TLS mode).
# Split across two rules — AWS allows max 5 path patterns per path_pattern condition.
resource "aws_lb_listener_rule" "http_api" {
  count        = local.enable_tls ? 0 : 1
  listener_arn = aws_lb_listener.http.arn
  priority     = 100

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  condition {
    path_pattern {
      values = ["/api/*", "/auth/*", "/health", "/metrics"]
    }
  }
}

# Operational metadata paths (/docs, /redoc, /openapi.json).
# Blocked by default (SEC-018); set enable_public_ops_paths=true to expose them.
resource "aws_lb_listener_rule" "http_ops_block" {
  count        = (local.enable_tls ? 0 : 1) * (var.enable_public_ops_paths ? 0 : 1)
  listener_arn = aws_lb_listener.http.arn
  priority     = 90

  action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Not found"
      status_code  = "403"
    }
  }

  condition {
    path_pattern {
      values = ["/openapi.json", "/docs", "/docs/*", "/redoc"]
    }
  }
}

resource "aws_lb_listener_rule" "http_api_docs" {
  count        = (local.enable_tls ? 0 : 1) * (var.enable_public_ops_paths ? 1 : 0)
  listener_arn = aws_lb_listener.http.arn
  priority     = 110

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  condition {
    path_pattern {
      values = ["/openapi.json", "/docs", "/docs/*", "/redoc"]
    }
  }
}

resource "aws_lb_listener_rule" "http_grafana" {
  count        = (local.enable_tls ? 0 : 1) * (local.bundled_grafana_available ? 1 : 0)
  listener_arn = aws_lb_listener.http.arn
  priority     = 200

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.grafana[0].arn
  }

  condition {
    path_pattern {
      values = ["/grafana", "/grafana/*"]
    }
  }
}

# ── ACM certificate (only when domain + zone are provided) ────────────────

resource "aws_acm_certificate" "app" {
  count             = local.enable_tls ? 1 : 0
  domain_name       = var.domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "cert_validation" {
  for_each = local.enable_tls ? {
    for dvo in aws_acm_certificate.app[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  zone_id         = var.route53_zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "app" {
  count                   = local.enable_tls ? 1 : 0
  certificate_arn         = aws_acm_certificate.app[0].arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

# ── HTTPS listener + path-based rules ─────────────────────────────────────

resource "aws_lb_listener" "https" {
  count             = local.enable_tls ? 1 : 0
  load_balancer_arn = aws_lb.app.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.app[0].certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

resource "aws_lb_listener_rule" "https_api" {
  count        = local.enable_tls ? 1 : 0
  listener_arn = aws_lb_listener.https[0].arn
  priority     = 100

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  condition {
    path_pattern {
      values = ["/api/*", "/auth/*", "/health", "/metrics"]
    }
  }
}

resource "aws_lb_listener_rule" "https_ops_block" {
  count        = (local.enable_tls ? 1 : 0) * (var.enable_public_ops_paths ? 0 : 1)
  listener_arn = aws_lb_listener.https[0].arn
  priority     = 90

  action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Not found"
      status_code  = "403"
    }
  }

  condition {
    path_pattern {
      values = ["/openapi.json", "/docs", "/docs/*", "/redoc"]
    }
  }
}

resource "aws_lb_listener_rule" "https_api_docs" {
  count        = (local.enable_tls ? 1 : 0) * (var.enable_public_ops_paths ? 1 : 0)
  listener_arn = aws_lb_listener.https[0].arn
  priority     = 110

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  condition {
    path_pattern {
      values = ["/openapi.json", "/docs", "/docs/*", "/redoc"]
    }
  }
}

resource "aws_lb_listener_rule" "https_grafana" {
  count        = (local.enable_tls ? 1 : 0) * (local.bundled_grafana_available ? 1 : 0)
  listener_arn = aws_lb_listener.https[0].arn
  priority     = 200

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.grafana[0].arn
  }

  condition {
    path_pattern {
      values = ["/grafana", "/grafana/*"]
    }
  }
}
