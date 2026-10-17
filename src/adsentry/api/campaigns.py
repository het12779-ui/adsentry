"""Campaign registry and health view."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from adsentry.db import get_session
from adsentry.models import Account, Campaign, Incident, MetricDay
from adsentry.schemas import CampaignHealthOut, CampaignIn, CampaignOut
from adsentry.services.incidents import campaign_series
from adsentry.tenant import get_tenant

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

SPARKLINE_DAYS = 14


def _today() -> date:
    return datetime.now(timezone.utc).date()


@router.post("", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
def create_campaign(
    payload: CampaignIn,
    account: Account = Depends(get_tenant),
    session: Session = Depends(get_session),
) -> Campaign:
    campaign = Campaign(account_id=account.id, **payload.model_dump())
    session.add(campaign)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"external_id '{payload.external_id}' already exists for this account",
        ) from exc
    session.refresh(campaign)
    return campaign


@router.get("", response_model=list[CampaignHealthOut])
def list_campaigns(
    account: Account = Depends(get_tenant),
    session: Session = Depends(get_session),
    as_of: date | None = Query(default=None),
) -> list[CampaignHealthOut]:
    as_of = as_of or _today()
    window_start = as_of - timedelta(days=SPARKLINE_DAYS - 1)

    campaigns = list(
        session.scalars(
            select(Campaign)
            .where(Campaign.account_id == account.id)
            .order_by(Campaign.name)
        ).all()
    )

    metric_rows = session.execute(
        select(
            MetricDay.campaign_id,
            MetricDay.day,
            MetricDay.impressions,
            MetricDay.clicks,
            MetricDay.conversions,
            MetricDay.spend_cents,
        ).where(
            MetricDay.account_id == account.id,
            MetricDay.day >= window_start,
            MetricDay.day <= as_of,
        )
    ).all()

    by_campaign: dict[int, list] = {}
    for row in metric_rows:
        by_campaign.setdefault(row[0], []).append(row)

    incident_rows = session.execute(
        select(Incident.campaign_id, Incident.severity, func.count(Incident.id))
        .where(Incident.account_id == account.id, Incident.resolved_at.is_(None))
        .group_by(Incident.campaign_id, Incident.severity)
    ).all()

    incident_counts: dict[int, dict[str, int]] = {}
    for campaign_id, severity, count in incident_rows:
        incident_counts.setdefault(campaign_id, {})[severity] = int(count)

    health: list[CampaignHealthOut] = []
    for campaign in campaigns:
        rows = sorted(by_campaign.get(campaign.id, []), key=lambda row: row[1])
        latest = rows[-1] if rows else None
        per_day = {row[1]: int(row[2]) for row in rows}
        sparkline = [
            per_day.get(window_start + timedelta(days=offset), 0)
            for offset in range(SPARKLINE_DAYS)
        ]

        counts = incident_counts.get(campaign.id, {})
        impressions = int(latest[2]) if latest else 0
        clicks = int(latest[3]) if latest else 0
        conversions = int(latest[4]) if latest else 0
        spend = int(latest[5]) if latest else 0

        health.append(
            CampaignHealthOut(
                id=campaign.id,
                external_id=campaign.external_id,
                name=campaign.name,
                channel=campaign.channel,
                status=campaign.status,
                last_day=latest[1] if latest else None,
                impressions=impressions,
                clicks=clicks,
                conversions=conversions,
                spend_cents=spend,
                ctr=round(clicks / impressions, 5) if impressions else 0.0,
                cvr=round(conversions / clicks, 5) if clicks else 0.0,
                open_incidents=sum(counts.values()),
                critical_incidents=counts.get("critical", 0),
                sparkline=sparkline,
            )
        )

    return health


@router.get("/{campaign_id}/series")
def get_series(
    campaign_id: int,
    account: Account = Depends(get_tenant),
    session: Session = Depends(get_session),
    days: int = Query(default=14, ge=1, le=90),
) -> dict:
    campaign = session.scalar(
        select(Campaign).where(
            Campaign.id == campaign_id, Campaign.account_id == account.id
        )
    )
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return {
        "campaign_id": campaign.id,
        "name": campaign.name,
        "series": campaign_series(session, account.id, campaign.id, days),
    }
