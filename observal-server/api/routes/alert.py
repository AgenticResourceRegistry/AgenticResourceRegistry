# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import asyncio
import secrets
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger as optic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import ROLE_HIERARCHY, get_db, require_role
from models.alert import AlertRule
from models.alert_history import AlertHistory
from models.user import User, UserRole
from schemas.alert import (
    AlertHistoryResponse,
    AlertRuleCreate,
    AlertRuleResponse,
    AlertRuleUpdate,
    WebhookSecretResponse,
    WebhookSecretRotateResponse,
    WebhookTestResponse,
)
from services.alert_evaluator import watchable_target_id
from services.ssrf_guard import is_private_url

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


async def _validate_webhook_url(url: str) -> None:
    optic.trace("validating webhook URL: {}", url)
    if not url:
        return  # empty URL is OK (no webhook)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, "webhook_url must use http or https")
    if await asyncio.to_thread(is_private_url, url):
        raise HTTPException(400, "webhook_url must not point to private/internal networks")


@router.get("", response_model=list[AlertRuleResponse])
async def list_alerts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.debug("alert list")
    stmt = select(AlertRule).order_by(AlertRule.created_at.desc())
    if ROLE_HIERARCHY.get(current_user.role, 999) > ROLE_HIERARCHY[UserRole.admin]:
        stmt = stmt.where(AlertRule.created_by == current_user.id)
    result = await db.execute(stmt)
    alerts = result.scalars().all()
    return [AlertRuleResponse.from_rule(r) for r in alerts]


@router.post("", response_model=AlertRuleResponse, status_code=201)
async def create_alert(
    body: AlertRuleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.debug("alert create")
    await _validate_webhook_url(body.webhook_url)
    target_id = await watchable_target_id(db, current_user, body.target_type, body.target_id)
    if target_id is None:
        # Same answer whether or not the agent exists, so this does not reveal hidden agents.
        raise HTTPException(403, "You can only create alerts for agents you own or co-author")
    rule = AlertRule(
        name=body.name,
        metric=body.metric,
        threshold=body.threshold,
        condition=body.condition,
        target_type=body.target_type,
        target_id=target_id if body.target_type != "all" else "",
        webhook_url=body.webhook_url,
        webhook_secret=secrets.token_hex(32),
        created_by=current_user.id,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return AlertRuleResponse.from_rule(rule)


@router.patch("/{alert_id}", response_model=AlertRuleResponse)
async def update_alert(
    alert_id: uuid.UUID,
    body: AlertRuleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.debug("alert update")
    rule = await db.get(AlertRule, alert_id)
    if not rule:
        raise HTTPException(404, "Alert rule not found")
    is_admin_or_above = ROLE_HIERARCHY.get(current_user.role, 999) <= ROLE_HIERARCHY[UserRole.admin]
    if rule.created_by != current_user.id and not is_admin_or_above:
        raise HTTPException(403, "Not authorized to modify this alert rule")
    if body.status is not None:
        rule.status = body.status
    if body.webhook_url is not None:
        await _validate_webhook_url(body.webhook_url)
        rule.webhook_url = body.webhook_url
    await db.commit()
    await db.refresh(rule)
    return AlertRuleResponse.from_rule(rule)


@router.delete("/{alert_id}", status_code=204)
async def delete_alert(
    alert_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.debug("alert delete")
    rule = await db.get(AlertRule, alert_id)
    if not rule:
        raise HTTPException(404, "Alert rule not found")
    is_admin_or_above = ROLE_HIERARCHY.get(current_user.role, 999) <= ROLE_HIERARCHY[UserRole.admin]
    if rule.created_by != current_user.id and not is_admin_or_above:
        raise HTTPException(403, "Not authorized to delete this alert rule")
    str(rule.id)
    await db.delete(rule)
    await db.commit()


@router.get("/{alert_id}/history", response_model=list[AlertHistoryResponse])
async def get_alert_history(
    alert_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.debug("fetching alert history for {} (limit={})", alert_id, limit)
    rule = await db.get(AlertRule, alert_id)
    if not rule:
        raise HTTPException(404, "Alert rule not found")
    is_admin = ROLE_HIERARCHY.get(current_user.role, 999) <= ROLE_HIERARCHY[UserRole.admin]
    if rule.created_by != current_user.id and not is_admin:
        raise HTTPException(403, "Not authorized")

    stmt = (
        select(AlertHistory)
        .where(AlertHistory.alert_rule_id == alert_id)
        .order_by(AlertHistory.fired_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    history = result.scalars().all()
    return history


@router.post("/{alert_id}/webhook-secret/rotate", response_model=WebhookSecretRotateResponse)
async def rotate_webhook_secret(
    alert_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Rotate the webhook signing secret for an alert rule. Admin only."""
    optic.debug("rotating webhook secret for alert {}", alert_id)
    rule = await db.get(AlertRule, alert_id)
    if not rule:
        raise HTTPException(404, "Alert rule not found")

    from datetime import UTC, datetime

    rule.webhook_secret = secrets.token_hex(32)
    await db.commit()
    await db.refresh(rule)
    return WebhookSecretRotateResponse(
        webhook_secret_last4=rule.webhook_secret[-4:],
        rotated_at=datetime.now(UTC),
    )


@router.get("/{alert_id}/webhook-secret", response_model=WebhookSecretResponse)
async def reveal_webhook_secret(
    alert_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Reveal the full webhook secret. Admin only, audit-logged."""
    optic.debug("revealing webhook secret for alert {}", alert_id)
    import logging

    logger = logging.getLogger(__name__)

    rule = await db.get(AlertRule, alert_id)
    if not rule:
        raise HTTPException(404, "Alert rule not found")

    logger.info(
        "Webhook secret revealed: alert_rule_id=%s by user_id=%s",
        alert_id,
        current_user.id,
    )
    return WebhookSecretResponse(webhook_secret=rule.webhook_secret)


@router.post("/{alert_id}/webhook/test", response_model=WebhookTestResponse)
async def test_webhook(
    alert_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Send a test webhook to the configured URL. Owner or admin."""
    optic.debug("sending test webhook for alert {}", alert_id)
    rule = await db.get(AlertRule, alert_id)
    if not rule:
        raise HTTPException(404, "Alert rule not found")

    is_admin = ROLE_HIERARCHY.get(current_user.role, 999) <= ROLE_HIERARCHY[UserRole.admin]
    if rule.created_by != current_user.id and not is_admin:
        raise HTTPException(403, "Not authorized to test this alert rule")

    if not rule.webhook_url:
        raise HTTPException(400, "No webhook URL configured for this alert rule")

    from services.webhook_delivery import deliver_webhook

    payload = {
        "test": True,
        "alert_rule_id": str(rule.id),
        "alert_name": rule.name,
        "metric": rule.metric,
        "threshold": rule.threshold,
        "condition": rule.condition,
        "target_type": rule.target_type,
        "target_id": rule.target_id,
        "message": "This is a test webhook from Observal",
    }

    result = await deliver_webhook(
        webhook_url=rule.webhook_url,
        webhook_secret=rule.webhook_secret,
        payload=payload,
        alert_rule_id=rule.id,
    )
    return WebhookTestResponse(
        success=result.success,
        status_code=result.status_code,
        attempts=result.attempts,
        duration_ms=result.duration_ms,
    )
