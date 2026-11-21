# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# Single-instance resources (root used count=1 → [0]; module has no count)
moved {
  from = aws_vpc.main[0]
  to   = module.vpc[0].aws_vpc.main
}
moved {
  from = aws_internet_gateway.main[0]
  to   = module.vpc[0].aws_internet_gateway.main
}
moved {
  from = aws_eip.nat[0]
  to   = module.vpc[0].aws_eip.nat
}
moved {
  from = aws_nat_gateway.main[0]
  to   = module.vpc[0].aws_nat_gateway.main
}
moved {
  from = aws_route_table.public[0]
  to   = module.vpc[0].aws_route_table.public
}
moved {
  from = aws_route_table.private[0]
  to   = module.vpc[0].aws_route_table.private
}

# Counted resources — move the WHOLE resource (handles any az_count)
moved {
  from = aws_subnet.public
  to   = module.vpc[0].aws_subnet.public
}
moved {
  from = aws_subnet.private
  to   = module.vpc[0].aws_subnet.private
}
moved {
  from = aws_route_table_association.public
  to   = module.vpc[0].aws_route_table_association.public
}
moved {
  from = aws_route_table_association.private
  to   = module.vpc[0].aws_route_table_association.private
}

# flow_logs log group had NO count on root; module uses count → [0]
moved {
  from = aws_cloudwatch_log_group.flow_logs
  to   = module.vpc[0].aws_cloudwatch_log_group.flow_logs[0]
}

# IAM role/policy + flow log resource (root had count → [0])
moved {
  from = aws_iam_role.flow_logs[0]
  to   = module.vpc[0].aws_iam_role.flow_logs[0]
}
moved {
  from = aws_iam_role_policy.flow_logs[0]
  to   = module.vpc[0].aws_iam_role_policy.flow_logs[0]
}
moved {
  from = aws_flow_log.main[0]
  to   = module.vpc[0].aws_flow_log.main[0]
}
