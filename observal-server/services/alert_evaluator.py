# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Alert evaluation engine: periodic metric checks and webhook delivery."""

import uuid
from datetime import UTC, datetime, timedelta

from loguru import logger as optic
from sqlalchemy import select

from database import async_session
from models.alert import AlertRule
from models.alert_history import AlertHistory
from services.clickhouse import _query
from services.ssrf_guard import is_private_url  # noqa: F401 -- re-exported for callers

LOOKBACK_MINUTES = 5
WEBHOOK_TIMEOUT = 5


async def _query_error_rate(target_type: str, target_id: str, lookback_minutes: int) -> float | None:
    """Query JSONL alert error rate.

    Session JSONL does not maintain a normalized error counter yet, so this is
    intentionally zero until an event-type based error metric is added.
    """
    optic.trace("querying error rate for {} {}", target_type, target_id)
    sql = "SELECT 0 AS error_rate"
    params: dict[str, str] = {"param_lookback": str(lookback_minutes)}
    try:
        r = await _query(sql, params)
        r.raise_for_status()
        text = r.text.strip()
        if not text:
            return None
        return float(text)
    except Exception as e:
        optic.error("ClickHouse error_rate query failed: {}", e)
        return None


async def _query_latency_p99(target_type: str, target_id: str, lookback_minutes: int) -> float | None:
    """Query JSONL alert p99 latency.

    Session JSONL no longer stores span latency, so this is intentionally zero.
    """
    optic.trace("querying p99 latency for {} {}", target_type, target_id)
    sql = "SELECT 0 AS latency_p99"
    params: dict[str, str] = {"param_lookback": str(lookback_minutes)}
    try:
        r = await _query(sql, params)
        r.raise_for_status()
        text = r.text.strip()
        if not text:
            return None
        return float(text)
    except Exception as e:
        optic.error("ClickHouse latency_p99 query failed: {}", e)
        return None


async def _query_token_usage(target_type: str, target_id: str, lookback_minutes: int) -> float | None:
    """Query total token usage from JSONL session aggregates."""
    optic.trace("querying token usage for {} {}", target_type, target_id)
    sql = (
        "SELECT sum(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens) AS token_usage "
        "FROM session_stats_agg FINAL "
        "WHERE last_event_time > now() - INTERVAL {lookback:UInt32} MINUTE"
    )
    params: dict[str, str] = {"param_lookback": str(lookback_minutes)}
    if target_type == "agent":
        sql += " AND agent_id = {target_id:String}"
        params["param_target_id"] = target_id
    try:
        r = await _query(sql, params)
        r.raise_for_status()
        text = r.text.strip()
        if not text:
            return None
        return float(text)
    except Exception as e:
        optic.error("ClickHouse token_usage query failed: {}", e)
        return None


async def _query_metric(metric: str, target_type: str, target_id: str, lookback_minutes: int) -> float | None:
    """Dispatch to the appropriate metric query helper."""
    optic.trace("dispatching metric {} query for {}", metric, target_type)
    if metric == "error_rate":
        return await _query_error_rate(target_type, target_id, lookback_minutes)
    elif metric == "latency_p99":
        return await _query_latency_p99(target_type, target_id, lookback_minutes)
    elif metric == "token_usage":
        return await _query_token_usage(target_type, target_id, lookback_minutes)
    else:
        optic.warning("unknown alert metric {} - cannot evaluate", metric)
        return None


async def _deliver_webhook_signed(
    url: str, secret: str, payload: dict, alert_rule_id: uuid.UUID
) -> tuple[int | None, str | None]:
    """Deliver a signed webhook using the webhook_delivery service.

    Returns (status_code, error) tuple for AlertHistory compatibility.
    If secret is empty (legacy rule), delivers without signature (AD-4).
    """
    optic.trace("validating webhook URL: {}", url)
    from services.webhook_delivery import deliver_webhook

    result = await deliver_webhook(
        webhook_url=url,
        webhook_secret=secret,
        payload=payload,
        alert_rule_id=alert_rule_id,
    )
    if result.success:
        return result.status_code, None
    return result.status_code, result.error


def _condition_met(condition: str, value: float, threshold: float) -> bool:
    """Check if the alert condition is met."""
    optic.trace("checking condition {} against value={}", condition, value)
    if condition == "above":
        return value > threshold
    elif condition == "below":
        return value < threshold
    return False


async def evaluate_alerts(ctx: dict) -> None:
    """Main arq cron job: evaluate all active alert rules and fire webhooks."""
    optic.debug("alert evaluation cycle starting")
    from services.webhook_delivery import flush_delivery_records

    optic.info("evaluating alert rules")
    async with async_session() as db:
        stmt = select(AlertRule).where(AlertRule.status == "active")
        result = await db.execute(stmt)
        rules = result.scalars().all()

        for rule in rules:
            try:
                value = await _query_metric(
                    rule.metric,
                    rule.target_type,
                    rule.target_id,
                    LOOKBACK_MINUTES,
                )
                if value is None:
                    continue
                if not _condition_met(rule.condition, value, rule.threshold):
                    continue

                now = datetime.now(UTC)
                last_triggered = rule.last_triggered
                if last_triggered is not None:
                    if last_triggered.tzinfo is None:
                        last_triggered = last_triggered.replace(tzinfo=UTC)
                    if now - last_triggered < timedelta(minutes=LOOKBACK_MINUTES):
                        continue

                status_code: int | None = None
                error: str | None = None
                delivery_status = "pending"

                if rule.webhook_url:
                    payload = {
                        "alert_rule_id": str(rule.id),
                        "alert_name": rule.name,
                        "metric": rule.metric,
                        "metric_value": value,
                        "threshold": rule.threshold,
                        "condition": rule.condition,
                        "target_type": rule.target_type,
                        "target_id": rule.target_id,
                        "fired_at": now.isoformat(),
                    }
                    # AD-3: secret resolved here at evaluation time, not later
                    status_code, error = await _deliver_webhook_signed(
                        rule.webhook_url, rule.webhook_secret, payload, rule.id
                    )
                    delivery_status = "delivered" if error is None else "failed"
                else:
                    delivery_status = "delivered"

                history = AlertHistory(
                    id=uuid.uuid4(),
                    alert_rule_id=rule.id,
                    metric_value=value,
                    threshold=rule.threshold,
                    condition=rule.condition,
                    fired_at=now,
                    delivery_status=delivery_status,
                    response_code=status_code,
                    error=error,
                )
                db.add(history)
                rule.last_triggered = now
                await db.commit()

                optic.info(
                    "Alert '{}' fired: {}={} (threshold={}, condition={}, delivery={})",
                    rule.name,
                    rule.metric,
                    value,
                    rule.threshold,
                    rule.condition,
                    delivery_status,
                )
            except Exception as e:
                optic.error("alert rule {} evaluation crashed: {}", rule.id, e)

    # AD-1: Batch-flush delivery records to ClickHouse at end of cycle
    await flush_delivery_records()
    optic.info("alert evaluation cycle complete")
