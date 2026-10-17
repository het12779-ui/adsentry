"""Request/response contracts."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

LogLevel = Literal["debug", "info", "warning", "error", "critical"]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc)


class CampaignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    channel: str = Field(min_length=1, max_length=40)
    status: Literal["active", "paused"] = "active"


class CampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_id: str
    name: str
    channel: str
    status: str


class CampaignHealthOut(CampaignOut):
    last_day: date | None
    impressions: int
    clicks: int
    conversions: int
    spend_cents: int
    ctr: float
    cvr: float
    open_incidents: int
    critical_incidents: int
    sparkline: list[int]


class MetricIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_external_id: str = Field(min_length=1, max_length=80)
    day: date
    impressions: int = Field(ge=0, le=100_000_000)
    clicks: int = Field(ge=0, le=100_000_000)
    conversions: int = Field(ge=0, le=100_000_000)
    spend_cents: int = Field(ge=0, le=10_000_000_000)


class MetricBatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metrics: list[MetricIn] = Field(min_length=1, max_length=1000)


class MetricIngestResult(BaseModel):
    accepted: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: int = 0
    errors: list[str] = Field(default_factory=list)


class IncidentOut(BaseModel):
    id: int
    campaign_id: int
    campaign_name: str
    kind: str
    severity: str
    metric: str
    first_seen_day: date
    last_seen_day: date
    occurrences: int
    message: str
    detail: str | None
    created_at: datetime
    resolved_at: datetime | None

    @field_serializer("created_at", "resolved_at")
    def serialize_time(self, value: datetime | None) -> datetime | None:
        return _utc(value) if value else None


class EvaluateOut(BaseModel):
    as_of: date
    campaigns_evaluated: int
    findings: int
    opened: int
    updated: int
    resolved: int


class LogEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_uid: str = Field(min_length=1, max_length=90)
    ts: datetime
    level: LogLevel = "info"
    source: str = Field(min_length=1, max_length=40)
    campaign_external_id: str | None = Field(default=None, max_length=80)
    message: str = Field(min_length=1, max_length=4000)
    fields: dict = Field(default_factory=dict)

    @field_validator("ts")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("ts must include a timezone offset")
        return value


class LogBatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[LogEventIn] = Field(min_length=1, max_length=500)


class LogIngestResult(BaseModel):
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    tokens_indexed: int = 0
    errors: list[str] = Field(default_factory=list)


class LogEventOut(BaseModel):
    id: int
    event_uid: str
    ts: datetime
    level: str
    source: str
    campaign_external_id: str | None
    message: str
    fields: dict

    @field_serializer("ts")
    def serialize_time(self, value: datetime) -> datetime:
        return _utc(value)


class LogSearchOut(BaseModel):
    total: int
    took_ms: float
    tokens: list[str]
    events: list[LogEventOut]


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    plan: str
