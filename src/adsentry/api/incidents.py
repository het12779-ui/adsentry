from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from adsentry.config import Settings, get_settings
from adsentry.db import get_session
from adsentry.models import Account, Campaign, Incident
from adsentry.schemas import EvaluateOut, IncidentOut
from adsentry.services.detection import Thresholds
from adsentry.services.incidents import evaluate_account
from adsentry.tenant import get_tenant

router = APIRouter(prefix="/incidents", tags=["incidents"])


def _thresholds(settings: Settings) -> Thresholds:
    return Thresholds(
        baseline_days=settings.baseline_days,
        z_threshold=settings.z_threshold,
        min_impressions=settings.min_impressions,
        min_clicks=settings.min_clicks,
    )


@router.post("/evaluate", response_model=EvaluateOut)
def evaluate(
    account: Account = Depends(get_tenant),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    as_of: date | None = Query(default=None),
) -> EvaluateOut:
    as_of = as_of or datetime.now(timezone.utc).date()
    summary = evaluate_account(
        session,
        account.id,
        as_of,
        thresholds=_thresholds(settings),
        auto_resolve=settings.auto_resolve,
    )
    return EvaluateOut(**asdict(summary))


@router.get("", response_model=list[IncidentOut])
def list_incidents(
    account: Account = Depends(get_tenant),
    session: Session = Depends(get_session),
    open_only: bool = Query(default=True, alias="open"),
    severity: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[IncidentOut]:
    stmt = (
        select(Incident, Campaign.name)
        .join(Campaign, Campaign.id == Incident.campaign_id)
        .where(Incident.account_id == account.id)
    )
    if open_only:
        stmt = stmt.where(Incident.resolved_at.is_(None))
    if severity:
        stmt = stmt.where(Incident.severity == severity)

    order = (
        Incident.severity.asc(),
        Incident.last_seen_day.desc(),
        Incident.id.desc(),
    )
    rows = session.execute(stmt.order_by(*order).limit(limit)).all()

    return [
        IncidentOut(
            id=incident.id,
            campaign_id=incident.campaign_id,
            campaign_name=campaign_name,
            kind=incident.kind,
            severity=incident.severity,
            metric=incident.metric,
            first_seen_day=incident.first_seen_day,
            last_seen_day=incident.last_seen_day,
            occurrences=incident.occurrences,
            message=incident.message,
            detail=incident.detail,
            created_at=incident.created_at,
            resolved_at=incident.resolved_at,
        )
        for incident, campaign_name in rows
    ]


@router.post("/{incident_id}/resolve", response_model=IncidentOut)
def resolve(
    incident_id: int,
    account: Account = Depends(get_tenant),
    session: Session = Depends(get_session),
) -> IncidentOut:
    row = session.execute(
        select(Incident, Campaign.name)
        .join(Campaign, Campaign.id == Incident.campaign_id)
        .where(Incident.id == incident_id, Incident.account_id == account.id)
    ).first()

    if row is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    incident, campaign_name = row
    if incident.resolved_at is None:
        incident.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.commit()
        session.refresh(incident)

    return IncidentOut(
        id=incident.id,
        campaign_id=incident.campaign_id,
        campaign_name=campaign_name,
        kind=incident.kind,
        severity=incident.severity,
        metric=incident.metric,
        first_seen_day=incident.first_seen_day,
        last_seen_day=incident.last_seen_day,
        occurrences=incident.occurrences,
        message=incident.message,
        detail=incident.detail,
        created_at=incident.created_at,
        resolved_at=incident.resolved_at,
    )
