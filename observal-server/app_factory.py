# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from fastapi import FastAPI

import services.dynamic_settings as ds
from health import configure_health_and_metrics
from logging_config import setup_logging
from middleware import configure_middleware
from routes import configure_routes
from services.optic import setup_optic
from startup import lifespan

setup_logging()
setup_optic(mode="dev")


def create_app() -> FastAPI:
    expose_openapi = ds.get_sync_bool("observability.enable_openapi", True)
    app = FastAPI(
        title="Observal API",
        description="API for Observal Agents & Capabilities Hub",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if expose_openapi else None,
        redoc_url="/redoc" if expose_openapi else None,
        openapi_url="/openapi.json" if expose_openapi else None,
    )
    configure_middleware(app)
    configure_routes(app)
    configure_health_and_metrics(app)
    return app
