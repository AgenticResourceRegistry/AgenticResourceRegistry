# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# ── ECS task execution role (pulls images, writes logs, reads secrets) ─────

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_execution" {
  name               = "${local.name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "ecs_execution_managed" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Allow the execution role to inject SSM SecureString parameters into the task at start.
data "aws_iam_policy_document" "ecs_execution_secrets" {
  # tfsec:ignore:aws-iam-no-policy-wildcards Resource wildcard is bounded to /${local.name}/* — i.e. only this install's parameters.
  statement {
    actions   = ["ssm:GetParameters", "ssm:GetParameter"]
    resources = ["arn:${data.aws_partition.current.partition}:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${local.ssm_prefix}/*"]
  }
  # tfsec:ignore:aws-iam-no-policy-wildcards kms:Decrypt is gated by the kms:ViaService condition restricting it to SSM-initiated decrypts.
  statement {
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_policy" "ecs_execution_secrets" {
  name   = "${local.name}-ecs-execution-secrets"
  policy = data.aws_iam_policy_document.ecs_execution_secrets.json
}

resource "aws_iam_role_policy_attachment" "ecs_execution_secrets" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = aws_iam_policy.ecs_execution_secrets.arn
}

# ── ECS task role (runtime permissions for the running container) ──────────

resource "aws_iam_role" "ecs_task" {
  name               = "${local.name}-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

# Read-only access to backups bucket (api can list/restore on demand).
data "aws_iam_policy_document" "ecs_task_backups" {
  statement {
    actions = ["s3:GetObject", "s3:ListBucket"]
    resources = [
      aws_s3_bucket.backups.arn,
      "${aws_s3_bucket.backups.arn}/*",
    ]
  }
}

resource "aws_iam_policy" "ecs_task_backups" {
  name   = "${local.name}-ecs-task-backups"
  policy = data.aws_iam_policy_document.ecs_task_backups.json
}

resource "aws_iam_role_policy_attachment" "ecs_task_backups" {
  role       = aws_iam_role.ecs_task.name
  policy_arn = aws_iam_policy.ecs_task_backups.arn
}

# ── EC2 instance role for the data tier host ──────────────────────────────

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "data_host" {
  name               = "${local.name}-data-host"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "data_host_ssm_core" {
  role       = aws_iam_role.data_host.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "data_host_cw_agent" {
  role       = aws_iam_role.data_host.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/CloudWatchAgentServerPolicy"
}

# Pull SSM parameters at boot (CH password, Grafana admin password).
data "aws_iam_policy_document" "data_host_ssm_read" {
  # tfsec:ignore:aws-iam-no-policy-wildcards Resource wildcard is bounded to /${local.name}/* — i.e. only this install's parameters.
  statement {
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:GetParametersByPath",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${local.ssm_prefix}/*"]
  }
  # tfsec:ignore:aws-iam-no-policy-wildcards kms:Decrypt is gated by the kms:ViaService condition restricting it to SSM-initiated decrypts.
  statement {
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_policy" "data_host_ssm_read" {
  name   = "${local.name}-data-host-ssm-read"
  policy = data.aws_iam_policy_document.data_host_ssm_read.json
}

resource "aws_iam_role_policy_attachment" "data_host_ssm_read" {
  role       = aws_iam_role.data_host.name
  policy_arn = aws_iam_policy.data_host_ssm_read.arn
}

# Push ClickHouse snapshots to the backups bucket.
data "aws_iam_policy_document" "data_host_backups" {
  statement {
    actions = ["s3:PutObject", "s3:GetObject", "s3:ListBucket", "s3:AbortMultipartUpload"]
    resources = [
      aws_s3_bucket.backups.arn,
      "${aws_s3_bucket.backups.arn}/*",
    ]
  }
}

resource "aws_iam_policy" "data_host_backups" {
  name   = "${local.name}-data-host-backups"
  policy = data.aws_iam_policy_document.data_host_backups.json
}

resource "aws_iam_role_policy_attachment" "data_host_backups" {
  role       = aws_iam_role.data_host.name
  policy_arn = aws_iam_policy.data_host_backups.arn
}

resource "aws_iam_instance_profile" "data_host" {
  name = "${local.name}-data-host"
  role = aws_iam_role.data_host.name
}
