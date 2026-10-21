# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Agent-scoped insight report routes."""

import uuid

from fastapi import Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db, require_role
from models.user import User, UserRole
from schemas.insights import (
    ApplySuggestionsRequest,
    GenerateInsightRequest,
    InsightReportListItem,
    InsightReportResponse,
)

from ._router import router


@router.get("/{agent_id}/insights/session-count")
async def agent_insight_session_count(
    agent_id: str,
    agent_version: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Return the number of sessions available for insight generation."""
    from api.routes.insights import agent_session_count

    return await agent_session_count(agent_id, agent_version, db, current_user)


@router.get("/{agent_id}/insights/reports", response_model=list[InsightReportListItem])
async def list_agent_insight_reports(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """List insight reports for an agent, newest first."""
    from api.routes.insights import list_reports

    return await list_reports(agent_id, db, current_user)


@router.post("/{agent_id}/insights/reports", response_model=InsightReportListItem)
async def create_agent_insight_report(
    agent_id: str,
    req: GenerateInsightRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Trigger generation of an insight report for an agent."""
    from api.routes.insights import generate_insight

    return await generate_insight(agent_id, req, db, current_user)


@router.get("/{agent_id}/insights/reports/{report_id}", response_model=InsightReportResponse)
async def get_agent_insight_report(
    agent_id: str,
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Get one insight report that belongs to an agent."""
    from api.routes.insights import _resolve_insights_agent, get_report

    agent = await _resolve_insights_agent(agent_id, db, current_user)
    report = await get_report(report_id, db, current_user)
    if report.agent_id != agent.id:
        raise HTTPException(status_code=404, detail="Report not found for agent")
    return report


@router.post("/{agent_id}/insights/reports/{report_id}/apply")
async def apply_agent_insight_report_suggestions(
    agent_id: str,
    report_id: uuid.UUID,
    body: ApplySuggestionsRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Apply selected suggestions from an agent insight report."""
    from api.routes.insights import apply_report_suggestions

    await get_agent_insight_report(agent_id, report_id, db, current_user)
    return await apply_report_suggestions(report_id, body, db, current_user)


@router.get("/{agent_id}/insights/reports/{report_id}/export/html", response_class=HTMLResponse)
async def export_agent_insight_report_html(
    agent_id: str,
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Export an agent insight report as a self-contained HTML document."""
    from api.routes.insights import export_report_html

    await get_agent_insight_report(agent_id, report_id, db, current_user)
    return await export_report_html(report_id, db, current_user)


@router.delete("/{agent_id}/insights/reports")
async def delete_agent_insight_reports(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Delete all insight reports and cached data for an agent."""
    from api.routes.insights import clear_agent_reports

    return await clear_agent_reports(agent_id, db, current_user)
