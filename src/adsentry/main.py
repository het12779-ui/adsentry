"""Application factory and wiring."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from adsentry.api import campaigns, incidents, logs, metrics, system
from adsentry.config import Settings, get_settings
from adsentry.db import SessionLocal, init_db
from adsentry.logging_setup import configure_logging, request_logging_middleware
from adsentry.metrics import METRICS
from adsentry.seed import ensure_seeded

logger = logging.getLogger("adsentry")
WEB_DIR = Path(__file__).resolve().parents[2] / "web"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        init_db()
        METRICS.inc("app_starts_total")
        if settings.seed_demo_data:
            with SessionLocal() as session:
                if ensure_seeded(session):
                    logger.info("demo_data_seeded")
        logger.info("app_started", extra={"version": settings.version})
        yield
        logger.info("app_stopped")

    app = FastAPI(
        title="AdSentry",
        version=settings.version,
        description=(
            "Anomaly detection and log search for ad operations. "
            "Metrics are daily facts; incidents are deduplicated state."
        ),
        lifespan=lifespan,
    )

    app.dependency_overrides[get_settings] = lambda: settings
    app.middleware("http")(request_logging_middleware)

    app.include_router(system.router)
    app.include_router(campaigns.router, prefix="/api/v1")
    app.include_router(metrics.router, prefix="/api/v1")
    app.include_router(incidents.router, prefix="/api/v1")
    app.include_router(logs.router, prefix="/api/v1")

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    else:  # pragma: no cover
        logger.warning("web_dir_missing", extra={"path": str(WEB_DIR)})

    return app


app = create_app()
