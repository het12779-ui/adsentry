"""Incident lifecycle.

An incident is *state*, not a log line. That distinction is the difference
between an alerting system people use and one they mute:

* one open incident per ``(campaign, kind)``;
* repeat detections increment ``occurrences`` once per day, not once per run;
* when the condition stops being detected, the incident auto-resolves;
* a later recurrence opens a **new** incident, preserving the history.

Evaluation is idempotent: running it twice for the same ``as_of`` produces the
same open set and does not inflate occurrence counts.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from adsentry.models import Campaign, Incident, MetricDay
from adsentry.services.detection import DayPoint, Thresholds, evaluate_series


@dataclass(frozen=True)
class EvaluateSummary:
    as_of: date
    campaigns_evaluated: int
    findings: int
    opened: int
    updated: int
    resolved: int


def load_points(
    session: Session, account_id: int, since: date, until: date
) -> dict[int, list[DayPoint]]:
    rows = session.execute(
        select(
            MetricDay.campaign_id,
            MetricDay.day,
            MetricDay.impressions,
            MetricDay.clicks,
            MetricDay.conversions,
            MetricDay.spend_cents,
        ).where(
            MetricDay.account_id == account_id,
            MetricDay.day >= since,
            MetricDay.day <= until,
        )
    ).all()

    grouped: dict[int, list[DayPoint]] = defaultdict(list)
    for campaign_id, day, impressions, clicks, conversions, spend_cents in rows:
        grouped[campaign_id].append(
            DayPoint(
                day=day,
                impressions=impressions,
                clicks=clicks,
                conversions=conversions,
                spend_cents=spend_cents,
            )
        )
    return grouped


def evaluate_account(
    session: Session,
    account_id: int,
    as_of: date,
    thresholds: Thresholds | None = None,
    auto_resolve: bool = True,
) -> EvaluateSummary:
    thresholds = thresholds or Thresholds()

    campaigns = list(
        session.scalars(
            select(Campaign).where(Campaign.account_id == account_id, Campaign.status == "active")
        ).all()
    )

    window_start = as_of - timedelta(days=max(thresholds.baseline_days + 7, 21))
    points_by_campaign = load_points(session, account_id, window_start, as_of)

    open_incidents = {
        (incident.campaign_id, incident.kind): incident
        for incident in session.scalars(
            select(Incident).where(
                Incident.account_id == account_id, Incident.resolved_at.is_(None)
            )
        ).all()
    }

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    findings_total = 0
    opened = 0
    updated = 0
    resolved = 0

    for campaign in campaigns:
        findings = evaluate_series(
            points_by_campaign.get(campaign.id, []),
            as_of,
            thresholds,
            campaign_name=campaign.name,
        )
        findings_total += len(findings)
        seen_kinds: set[str] = set()

        for finding in findings:
            seen_kinds.add(finding.kind)
            existing = open_incidents.get((campaign.id, finding.kind))

            if existing is not None:
                if existing.last_seen_day < as_of:
                    existing.occurrences += 1
                    existing.last_seen_day = as_of
                    updated += 1
                # Refresh copy: thresholds and messages may have changed.
                existing.severity = finding.severity
                existing.message = finding.message
                existing.detail = finding.detail
                continue

            session.add(
                Incident(
                    account_id=account_id,
                    campaign_id=campaign.id,
                    kind=finding.kind,
                    severity=finding.severity,
                    metric=finding.metric,
                    first_seen_day=as_of,
                    last_seen_day=as_of,
                    occurrences=1,
                    message=finding.message,
                    detail=finding.detail,
                )
            )
            opened += 1

        if auto_resolve:
            for (campaign_id, kind), incident in open_incidents.items():
                if campaign_id != campaign.id:
                    continue
                if kind in seen_kinds:
                    continue
                # Resolve when the condition no longer reproduces. The
                # `<=` guard means a same-day restatement can clear an
                # incident, while evaluating an older date cannot close a
                # newer one.
                if incident.resolved_at is None and incident.last_seen_day <= as_of:
                    incident.resolved_at = now
                    resolved += 1

    session.commit()

    return EvaluateSummary(
        as_of=as_of,
        campaigns_evaluated=len(campaigns),
        findings=findings_total,
        opened=opened,
        updated=updated,
        resolved=resolved,
    )


def campaign_series(
    session: Session, account_id: int, campaign_id: int, days: int
) -> list[dict]:
    rows = session.execute(
        select(
            MetricDay.day,
            MetricDay.impressions,
            MetricDay.clicks,
            MetricDay.conversions,
            MetricDay.spend_cents,
        )
        .where(MetricDay.account_id == account_id, MetricDay.campaign_id == campaign_id)
        .order_by(MetricDay.day.desc())
        .limit(days)
    ).all()

    series = [
        {
            "day": day,
            "impressions": impressions,
            "clicks": clicks,
            "conversions": conversions,
            "spend_cents": spend_cents,
            "ctr": round(clicks / impressions, 5) if impressions else 0.0,
            "cvr": round(conversions / clicks, 5) if clicks else 0.0,
        }
        for day, impressions, clicks, conversions, spend_cents in rows
    ]
    series.reverse()
    return series
