# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from fastapi import FastAPI
from strawberry.fastapi import GraphQLRouter

from api.graphql import get_context_dep, schema
from api.routes.admin import router as admin_router
from api.routes.admin_sso import router as admin_sso_router
from api.routes.agent import router as agent_router
from api.routes.agent_shares import router as agent_shares_router
from api.routes.alert import router as alert_router
from api.routes.ard import document_ard_validation_errors
from api.routes.ard import router as ard_router
from api.routes.ard_imports import router as ard_imports_router
from api.routes.artifacts import router as artifacts_router
from api.routes.audit import router as audit_router
from api.routes.audit_log import router as audit_log_router
from api.routes.auth import router as auth_router
from api.routes.bulk import router as bulk_router
from api.routes.co_authors import router as co_authors_router
from api.routes.component_source import router as component_source_router
from api.routes.config import router as config_router
from api.routes.dashboard import router as dashboard_router
from api.routes.device_auth import router as device_auth_router
from api.routes.exec_dashboard import router as exec_dashboard_router
from api.routes.feedback import router as feedback_router
from api.routes.hook import router as hook_router
from api.routes.inbox import router as inbox_router
from api.routes.ingest import router as ingest_router
from api.routes.insights import router as insights_router
from api.routes.jwks import router as jwks_router
from api.routes.layer_snapshot import router as layer_snapshot_router
from api.routes.logs_stream import router as logs_stream_router
from api.routes.mcp import router as mcp_router
from api.routes.mcp_webhook_sync import router as mcp_webhook_sync_router
from api.routes.mcp_webhook_sync import webhook_router as mcp_webhook_receiver_router
from api.routes.preview import router as preview_router
from api.routes.prompt import router as prompt_router
from api.routes.recommendations import router as recommendations_router
from api.routes.registry import router as registry_router
from api.routes.review import router as review_router
from api.routes.sandbox import router as sandbox_router
from api.routes.scim import router as scim_router
from api.routes.sessions import router as sessions_router
from api.routes.skill import router as skill_router
from api.routes.sso_saml import router as sso_saml_router
from api.routes.sso_saml import saml_health_probe
from api.routes.support import router as support_router
from api.routes.teams import router as teams_router
from api.routes.telemetry import router as telemetry_router
from api.routes.users import router as users_router
from services.saml_health import register_saml_health_probe

REST_ROUTERS = (
    auth_router,
    device_auth_router,
    jwks_router,
    mcp_router,
    mcp_webhook_sync_router,
    mcp_webhook_receiver_router,
    registry_router,
    review_router,
    agent_router,
    agent_shares_router,
    preview_router,
    skill_router,
    hook_router,
    prompt_router,
    sandbox_router,
    telemetry_router,
    dashboard_router,
    feedback_router,
    insights_router,
    ingest_router,
    admin_router,
    alert_router,
    sessions_router,
    component_source_router,
    bulk_router,
    co_authors_router,
    config_router,
    support_router,
    teams_router,
    inbox_router,
    layer_snapshot_router,
    logs_stream_router,
    audit_router,
    users_router,
    audit_log_router,
    admin_sso_router,
    sso_saml_router,
    scim_router,
    exec_dashboard_router,
    recommendations_router,
    ard_router,
    ard_imports_router,
    artifacts_router,
)


def include_graphql_routes(app: FastAPI) -> None:
    graphql_app = GraphQLRouter(schema, context_getter=get_context_dep)
    app.include_router(graphql_app, prefix="/api/v1/graphql")


def include_rest_routes(app: FastAPI) -> None:
    for router in REST_ROUTERS:
        app.include_router(router)


def configure_routes(app: FastAPI) -> None:
    register_saml_health_probe(saml_health_probe)
    include_graphql_routes(app)
    include_rest_routes(app)
    document_ard_validation_errors(app)
