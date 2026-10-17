"""Deterministic demo dataset.

Each campaign is generated with a weekly delivery shape and then given one
specific failure, so the detector has something real to find:

* a healthy control,
* a tracking blackout (zero impressions),
* creative fatigue (CTR halves),
* a conversion collapse (clicks, no conversions),
* a spend spike,
* a brand-new campaign with no baseline yet,
* a click-tracker outage.

Log events tell the same story from the observability side, which is what makes
the log search worth having next to the incident feed.
"""

from __future__ import annotations

import json
import math
import random
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from adsentry.models import Account, Campaign, LogEvent, MetricDay
from adsentry.services.incidents import evaluate_account
from adsentry.services.logsearch import index_event
from adsentry.tenant import hash_api_key

DEMO_ACCOUNTS = [
    ("northwind-retail", "Northwind Retail", "as_demo_northwind_key", "scale"),
    ("skyline-travel", "Skyline Travel", "as_demo_skyline_key", "growth"),
]

HISTORY_DAYS = 40
_EVENT_NAMESPACE = uuid.UUID("8c4d2e61-5b7a-4f90-a3d1-7e2b9c0f4a55")


@dataclass(frozen=True)
class CampaignSpec:
    external_id: str
    name: str
    channel: str
    base_impressions: int
    ctr: float
    cvr: float
    cpc_cents: int
    failure: str = "none"


NORTHWIND = [
    CampaignSpec("NW-SRCH-01", "Always-On Brand Search", "google", 42_000, 0.052, 0.061, 1_450),
    CampaignSpec("NW-PMAX-02", "Marketplace Push", "google", 61_000, 0.031, 0.044, 1_100, "tracking_dark"),
    CampaignSpec("NW-META-03", "Prospecting Carousel", "meta", 88_000, 0.014, 0.028, 620, "creative_fatigue"),
    CampaignSpec("NW-DSP-04", "Retargeting Display", "display", 54_000, 0.009, 0.052, 480, "conversion_collapse"),
    CampaignSpec("NW-EML-05", "Winback Email", "email", 12_000, 0.041, 0.083, 210, "spend_spike"),
    CampaignSpec("NW-YT-06", "Video Launch", "youtube", 26_000, 0.006, 0.017, 340, "new_campaign"),
]

SKYLINE = [
    CampaignSpec("SK-SRCH-01", "Destination Search", "google", 33_000, 0.048, 0.057, 1_620),
    CampaignSpec("SK-META-02", "Seasonal Getaways", "meta", 47_000, 0.019, 0.034, 700, "click_loss"),
]

LOG_TEMPLATES = [
    ("info", "tracking", "Pixel postback received for conversion {id}", {"latency_ms": 84}),
    ("info", "platform-sync", "Platform sync completed in {latency}ms", {"rows": 1280}),
    ("info", "auth", "API token refreshed for scope ads.read", {}),
    ("info", "spend-reconcile", "Spend reconciliation drift {drift} paise corrected", {}),
    ("warning", "api", "Rate limit 429 received from platform API, backing off", {"attempt": 2}),
    ("warning", "tracking", "Postback queue backlog {drift} events", {"queue": "conversions"}),
    ("warning", "creative", "Creative CTR below threshold on ad set {id}", {}),
    ("error", "tracking", "Postback rejected: signature mismatch for conversion {id}", {}),
    ("error", "tracking", "Landing page 504 timeout after 3000ms", {"url": "/checkout"}),
    ("critical", "platform-sync", "Ad server returned zero impressions for {campaign}", {}),
    ("critical", "tracking", "Click tracker unreachable: connection refused", {"host": "track.example.com"}),
]


def _weekly_factor(day: date) -> float:
    # A gentle weekly shape: weekends dip, midweek peaks.
    return 1.0 + 0.12 * math.sin((day.weekday() / 7) * 2 * math.pi)


def _seed_campaign(
    session: Session, account: Account, spec: CampaignSpec, today: date, rng: random.Random
) -> Campaign:
    campaign = Campaign(
        account_id=account.id,
        external_id=spec.external_id,
        name=spec.name,
        channel=spec.channel,
        status="active",
    )
    session.add(campaign)
    session.flush()

    start_offset = 0 if spec.failure == "new_campaign" else HISTORY_DAYS

    for offset in range(start_offset, -1, -1):
        day = today - timedelta(days=offset)
        days_ago = offset

        impressions_factor = _weekly_factor(day) * rng.uniform(0.9, 1.1)
        ctr = spec.ctr * rng.uniform(0.88, 1.12)
        cvr = spec.cvr * rng.uniform(0.8, 1.2)
        spend_factor = rng.uniform(0.92, 1.08)

        if spec.failure == "creative_fatigue" and days_ago <= 8:
            # Fatigue ramps: 100% -> ~35% of the original CTR over eight days.
            ctr *= 1 - 0.65 * ((9 - days_ago) / 8)

        if spec.failure == "tracking_dark" and days_ago <= 1:
            impressions = 0
            clicks = 0
            conversions = 0
            spend = 0
        elif spec.failure == "click_loss" and days_ago == 0:
            impressions = int(spec.base_impressions * impressions_factor)
            clicks = 0
            conversions = 0
            spend = 0
        else:
            impressions = int(spec.base_impressions * impressions_factor)
            clicks = max(0, int(impressions * ctr))
            conversions = max(0, int(clicks * cvr))
            spend = int(clicks * spec.cpc_cents * spend_factor)

        if spec.failure == "conversion_collapse" and days_ago <= 1:
            conversions = 0

        if spec.failure == "spend_spike" and days_ago == 0:
            spend = int(spend * 2.6)

        session.add(
            MetricDay(
                account_id=account.id,
                campaign_id=campaign.id,
                day=day,
                impressions=impressions,
                clicks=clicks,
                conversions=conversions,
                spend_cents=spend,
            )
        )

    return campaign


def _seed_logs(
    session: Session, account: Account, campaigns: list[Campaign], today: date, rng: random.Random
) -> None:
    campaign_ids = [campaign.external_id for campaign in campaigns]

    for index in range(260):
        level, source, template, fields = rng.choice(LOG_TEMPLATES)
        campaign = rng.choice(campaign_ids)
        message = template.format(
            id=rng.randint(10_000, 99_999),
            latency=rng.randint(120, 2_400),
            drift=rng.randint(1, 900),
            campaign=campaign,
        )

        days_ago = rng.randint(0, 6)
        ts = datetime.combine(today - timedelta(days=days_ago), time(hour=8)) + timedelta(
            minutes=rng.randint(0, 720)
        )

        # Tie the loud failures to the campaigns that actually failed, so the
        # incident feed and the log explorer tell the same story.
        if "zero impressions" in message or "signature mismatch" in message:
            campaign = "NW-PMAX-02"
        if "Click tracker unreachable" in message:
            campaign = "SK-META-02"

        event = LogEvent(
            account_id=account.id,
            event_uid=str(uuid.uuid5(_EVENT_NAMESPACE, f"{account.slug}:{index}")),
            ts=ts,
            level=level,
            source=source,
            campaign_external_id=campaign,
            message=message,
            fields_json=json.dumps(
                {"campaign": campaign, **fields}, sort_keys=True
            ),
        )
        session.add(event)
        session.flush()
        index_event(session, account.id, event)


def seed_demo(session: Session, today: date | None = None, force: bool = False) -> bool:
    if not force and session.scalar(select(func.count(Account.id))) > 0:
        return False

    today = today or datetime.now(timezone.utc).date()
    rng = random.Random(20261017)

    accounts: dict[str, Account] = {}
    for slug, name, api_key, plan in DEMO_ACCOUNTS:
        account = Account(name=name, slug=slug, api_key_hash=hash_api_key(api_key), plan=plan)
        session.add(account)
        session.flush()
        accounts[slug] = account

    northwind_campaigns = [
        _seed_campaign(session, accounts["northwind-retail"], spec, today, rng)
        for spec in NORTHWIND
    ]
    skyline_campaigns = [
        _seed_campaign(session, accounts["skyline-travel"], spec, today, rng)
        for spec in SKYLINE
    ]

    _seed_logs(session, accounts["northwind-retail"], northwind_campaigns, today, rng)
    _seed_logs(session, accounts["skyline-travel"], skyline_campaigns, today, rng)

    session.commit()

    for account in accounts.values():
        evaluate_account(session, account.id, today)

    return True


def ensure_seeded(session: Session) -> bool:
    return seed_demo(session)
