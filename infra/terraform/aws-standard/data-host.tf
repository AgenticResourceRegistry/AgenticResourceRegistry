# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# Data tier: a single EC2 host running Postgres, Redis, ClickHouse, and optional observability.
# All services run via Docker Compose, bootstrapped from user-data.

data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

# Static private IP via ENI — gives the host a stable address that
# survives instance replacement and enables predictable DNS records.
resource "aws_network_interface" "data_host" {
  subnet_id       = local.private_subnet_ids[0]
  security_groups = [aws_security_group.data_host.id]

  tags = { Name = "${local.name}-data-host-eni" }
}

resource "aws_ebs_volume" "data" {
  availability_zone = local.azs[0]
  size              = local.effective_data_volume_size_gb
  type              = "gp3"
  encrypted         = true

  tags = { Name = "${local.name}-data" }
}

locals {
  data_host_user_data = templatefile("${path.module}/data-user-data.sh.tftpl", {
    region                           = var.region
    ssm_prefix                       = local.ssm_prefix
    db_password                      = random_password.db.result
    clickhouse_password              = random_password.clickhouse.result
    data_volume_size_gb              = local.effective_data_volume_size_gb
    log_group                        = aws_cloudwatch_log_group.data_host.name
    grafana_root_url                 = local.app_url
    observability_prometheus_enabled = local.observability_prometheus_enabled
    observability_grafana_enabled    = local.observability_grafana_enabled
  })
}

resource "aws_instance" "data_host" {
  ami                  = data.aws_ami.al2023.id
  instance_type        = local.effective_data_instance_type
  iam_instance_profile = aws_iam_instance_profile.data_host.name

  network_interface {
    device_index         = 0
    network_interface_id = aws_network_interface.data_host.id
  }

  user_data                   = local.data_host_user_data
  user_data_replace_on_change = true

  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
    encrypted   = true
  }

  tags = { Name = "${local.name}-data-host" }

  depends_on = [
    aws_nat_gateway.main,
    aws_ssm_parameter.db_password,
    aws_ssm_parameter.clickhouse_password,
  ]
}

resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.data_host.id
}

# ── Internal DNS records ──────────────────────────────────────────────────

resource "aws_route53_record" "postgres_internal" {
  zone_id = aws_route53_zone.internal.zone_id
  name    = "postgres.${var.internal_dns_zone}"
  type    = "A"
  ttl     = 60
  records = [aws_network_interface.data_host.private_ip]
}

resource "aws_route53_record" "redis_internal" {
  zone_id = aws_route53_zone.internal.zone_id
  name    = "redis.${var.internal_dns_zone}"
  type    = "A"
  ttl     = 60
  records = [aws_network_interface.data_host.private_ip]
}

resource "aws_route53_record" "clickhouse_internal" {
  zone_id = aws_route53_zone.internal.zone_id
  name    = "clickhouse.${var.internal_dns_zone}"
  type    = "A"
  ttl     = 60
  records = [aws_network_interface.data_host.private_ip]
}

resource "aws_route53_record" "grafana_internal" {
  count   = local.observability_grafana_enabled ? 1 : 0
  zone_id = aws_route53_zone.internal.zone_id
  name    = "grafana.${var.internal_dns_zone}"
  type    = "A"
  ttl     = 60
  records = [aws_network_interface.data_host.private_ip]
}
