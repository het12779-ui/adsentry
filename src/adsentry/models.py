"""ORM models.

Three ideas shape this schema:

1. **Metrics are a daily fact per campaign.** Restatements happen (platforms
   re-sync late data), so the row upserts on ``(account, campaign, day)`` rather
   than duplicating.
2. **Incidents are state, not log lines.** One open incident per
   ``(campaign, kind)`` with ``first_seen_day``/``last_seen_day`` and an
   occurrence count, so an ongoing problem does not page someone every morning.
3. **Log search uses an inverted index in SQL.** ``log_tokens`` holds one row per
   (event, token); a search is an indexed join + HAVING count, not a
   ``LIKE '%text%'`` scan. That is the ELK idea reduced to plain SQL.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from adsentry.db import Base


def utcnow() -> datetime:
    """Naive UTC, converted at the edges."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    plan: Mapped[str] = mapped_column(String(30), default="growth", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        UniqueConstraint("account_id", "external_id", name="uq_campaign_account_external"),
        Index("ix_campaign_account_status", "account_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    metrics: Mapped[list["MetricDay"]] = relationship(back_populates="campaign")


class MetricDay(Base):
    __tablename__ = "metric_days"
    __table_args__ = (
        Index("ix_metric_account_campaign_day", "account_id", "campaign_id", "day", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    day: Mapped[date] = mapped_column(Date, nullable=False)
    impressions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conversions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    spend_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    campaign: Mapped[Campaign] = relationship(back_populates="metrics")


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        Index("ix_incident_account_open", "account_id", "resolved_at"),
        Index("ix_incident_campaign_kind", "campaign_id", "kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    metric: Mapped[str] = mapped_column(String(20), nullable=False)
    first_seen_day: Mapped[date] = mapped_column(Date, nullable=False)
    last_seen_day: Mapped[date] = mapped_column(Date, nullable=False)
    occurrences: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LogEvent(Base):
    __tablename__ = "log_events"
    __table_args__ = (
        UniqueConstraint("account_id", "event_uid", name="uq_log_account_event"),
        Index("ix_log_account_time", "account_id", "ts"),
        Index("ix_log_account_level", "account_id", "level"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_uid: Mapped[str] = mapped_column(String(90), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    campaign_external_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    fields_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class LogToken(Base):
    """Inverted index: one row per (event, token)."""

    __tablename__ = "log_tokens"
    __table_args__ = (
        Index("ix_token_account_token", "account_id", "token", "event_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("log_events.id", ondelete="CASCADE"), nullable=False
    )
    token: Mapped[str] = mapped_column(String(48), nullable=False)


__all__ = [
    "Account",
    "Campaign",
    "Incident",
    "LogEvent",
    "LogToken",
    "MetricDay",
    "utcnow",
]
