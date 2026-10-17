from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from adsentry.config import Settings, get_settings
from adsentry.db import get_session
from adsentry.metrics import METRICS
from adsentry.models import Account

router = APIRouter(tags=["system"])
logger = logging.getLogger("adsentry.system")


@router.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
def readyz(session: Session = Depends(get_session)) -> Response:
    try:
        session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - any failure means "not ready"
        logger.exception("readiness_check_failed")
        return Response(
            content='{"status":"degraded","database":"unreachable"}',
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json",
        )
    return Response(
        content='{"status":"ready","database":"reachable"}',
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


@router.get("/metrics")
def metrics() -> Response:
    return Response(content=METRICS.render(), media_type="text/plain; version=0.0.4")


@router.get("/api/v1/demo/accounts")
def demo_accounts(
    session: Session = Depends(get_session), settings: Settings = Depends(get_settings)
) -> list[dict]:
    if not settings.demo_mode:
        return []
    rows = session.scalars(select(Account).order_by(Account.id)).all()
    return [{"id": row.id, "name": row.name, "slug": row.slug, "plan": row.plan} for row in rows]
