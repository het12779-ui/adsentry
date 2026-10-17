"""Daily metric ingestion.

Platforms re-sync late data, so a metric row **upserts** on
``(account, campaign, day)``. An identical re-post is counted as ``unchanged``
rather than ``updated`` - otherwise every nightly re-sync looks like a
restatement and the number becomes meaningless.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from adsentry.db import get_session
from adsentry.metrics import METRICS
from adsentry.models import Account, Campaign, MetricDay
from adsentry.schemas import MetricBatchIn, MetricIngestResult, MetricIn
from adsentry.tenant import get_tenant

router = APIRouter(prefix="/metrics", tags=["metrics"])


def _campaign(session: Session, account_id: int, external_id: str) -> Campaign | None:
    return session.scalar(
        select(Campaign).where(
            Campaign.account_id == account_id, Campaign.external_id == external_id
        )
    )


def ingest_metric(
    session: Session, account: Account, payload: MetricIn, result: MetricIngestResult
) -> None:
    campaign = _campaign(session, account.id, payload.campaign_external_id)
    if campaign is None:
        result.rejected += 1
        result.errors.append(
            f"{payload.campaign_external_id}/{payload.day}: unknown campaign"
        )
        return

    existing = session.scalar(
        select(MetricDay).where(
            MetricDay.account_id == account.id,
            MetricDay.campaign_id == campaign.id,
            MetricDay.day == payload.day,
        )
    )

    if existing is not None:
        incoming = (
            payload.impressions,
            payload.clicks,
            payload.conversions,
            payload.spend_cents,
        )
        current = (
            existing.impressions,
            existing.clicks,
            existing.conversions,
            existing.spend_cents,
        )
        if incoming == current:
            result.unchanged += 1
            return

        (
            existing.impressions,
            existing.clicks,
            existing.conversions,
            existing.spend_cents,
        ) = incoming
        result.updated += 1
        METRICS.inc("metric_restatements_total")
        return

    session.add(
        MetricDay(
            account_id=account.id,
            campaign_id=campaign.id,
            day=payload.day,
            impressions=payload.impressions,
            clicks=payload.clicks,
            conversions=payload.conversions,
            spend_cents=payload.spend_cents,
        )
    )
    result.accepted += 1
    METRICS.inc("metric_rows_ingested_total")


@router.post("", response_model=MetricIngestResult, status_code=status.HTTP_201_CREATED)
def post_metric(
    payload: MetricIn,
    account: Account = Depends(get_tenant),
    session: Session = Depends(get_session),
) -> MetricIngestResult:
    result = MetricIngestResult()
    ingest_metric(session, account, payload, result)
    session.commit()
    return result


@router.post("/batch", response_model=MetricIngestResult, status_code=status.HTTP_201_CREATED)
def post_metrics(
    payload: MetricBatchIn,
    account: Account = Depends(get_tenant),
    session: Session = Depends(get_session),
) -> MetricIngestResult:
    result = MetricIngestResult()
    for metric in payload.metrics:
        ingest_metric(session, account, metric, result)
    session.commit()
    return result
