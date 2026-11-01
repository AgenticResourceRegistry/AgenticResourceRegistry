# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

# error_rate and latency_p99 are unimplemented stubs that always return 0, so
# token_usage is the only metric the evaluator can actually act on.
AlertMetric = Literal["token_usage"]
AlertCondition = Literal["above", "below"]
AlertTargetType = Literal["mcp", "agent", "all"]


class AlertRuleCreate(BaseModel):
    name: str
    metric: AlertMetric
    threshold: float
    condition: AlertCondition
    target_type: AlertTargetType = "all"
    target_id: str = ""
    webhook_url: str = ""


class AlertRuleUpdate(BaseModel):
    status: str | None = None
    webhook_url: str | None = None


class AlertRuleResponse(BaseModel):
    id: UUID
    name: str
    metric: str
    threshold: float
    condition: str
    target_type: str
    target_id: str
    webhook_url: str
    webhook_secret_last4: str = ""
    status: str
    last_triggered: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_rule(cls, rule) -> "AlertRuleResponse":
        """Create response from AlertRule, computing webhook_secret_last4."""
        return cls(
            id=rule.id,
            name=rule.name,
            metric=rule.metric,
            threshold=rule.threshold,
            condition=rule.condition,
            target_type=rule.target_type,
            target_id=rule.target_id,
            webhook_url=rule.webhook_url,
            webhook_secret_last4=rule.webhook_secret[-4:] if rule.webhook_secret else "",
            status=rule.status,
            last_triggered=rule.last_triggered,
            created_at=rule.created_at,
        )


class WebhookSecretResponse(BaseModel):
    webhook_secret: str


class WebhookSecretRotateResponse(BaseModel):
    webhook_secret_last4: str
    rotated_at: datetime


class WebhookTestResponse(BaseModel):
    success: bool
    status_code: int | None
    attempts: int
    duration_ms: float


class AlertHistoryResponse(BaseModel):
    id: UUID
    alert_rule_id: UUID
    metric_value: float
    threshold: float
    condition: str
    fired_at: datetime
    delivery_status: str
    response_code: int | None
    error: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
