from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from adsentry.db import get_session
from adsentry.metrics import METRICS
from adsentry.models import Account, LogEvent, LogToken
from adsentry.schemas import (
    LogBatchIn,
    LogEventIn,
    LogEventOut,
    LogIngestResult,
    LogSearchOut,
)
from adsentry.services.logsearch import index_event, parse_fields, search_events, tokenize
from adsentry.tenant import get_tenant

router = APIRouter(prefix="/logs", tags=["logs"])


def _naive_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def ingest_event(
    session: Session, account: Account, payload: LogEventIn, result: LogIngestResult
) -> None:
    exists = session.scalar(
        select(LogEvent.id).where(
            LogEvent.account_id == account.id, LogEvent.event_uid == payload.event_uid
        )
    )
    if exists is not None:
        result.duplicates += 1
        return

    event = LogEvent(
        account_id=account.id,
        event_uid=payload.event_uid,
        ts=_naive_utc(payload.ts),
        level=payload.level,
        source=payload.source,
        campaign_external_id=payload.campaign_external_id,
        message=payload.message,
        fields_json=json.dumps(payload.fields, sort_keys=True) if payload.fields else None,
    )

    savepoint = session.begin_nested()
    try:
        session.add(event)
        session.flush()  # assign event.id, needed by the index rows
        tokens = index_event(session, account.id, event)
        savepoint.commit()
        result.accepted += 1
        result.tokens_indexed += len(tokens)
        METRICS.inc("log_events_ingested_total", level=payload.level)
    except IntegrityError:
        # Concurrent duplicate writer: the event still exists exactly once.
        savepoint.rollback()
        result.duplicates += 1


@router.post("", response_model=LogIngestResult, status_code=status.HTTP_201_CREATED)
def post_event(
    payload: LogEventIn,
    account: Account = Depends(get_tenant),
    session: Session = Depends(get_session),
) -> LogIngestResult:
    result = LogIngestResult()
    ingest_event(session, account, payload, result)
    session.commit()
    return result


@router.post("/batch", response_model=LogIngestResult, status_code=status.HTTP_201_CREATED)
def post_events(
    payload: LogBatchIn,
    account: Account = Depends(get_tenant),
    session: Session = Depends(get_session),
) -> LogIngestResult:
    result = LogIngestResult()
    for event in payload.events:
        ingest_event(session, account, event, result)
    session.commit()
    return result


@router.get("/stats")
def log_stats(
    account: Account = Depends(get_tenant),
    session: Session = Depends(get_session),
) -> dict:
    events = session.scalar(
        select(func.count(LogEvent.id)).where(LogEvent.account_id == account.id)
    )
    tokens = session.scalar(
        select(func.count(LogToken.id)).where(LogToken.account_id == account.id)
    )
    by_level = session.execute(
        select(LogEvent.level, func.count(LogEvent.id))
        .where(LogEvent.account_id == account.id)
        .group_by(LogEvent.level)
    ).all()

    return {
        "events": int(events or 0),
        "tokens": int(tokens or 0),
        "by_level": {level: int(count) for level, count in by_level},
    }


@router.get("", response_model=LogSearchOut)
def search(
    account: Account = Depends(get_tenant),
    session: Session = Depends(get_session),
    q: str | None = Query(default=None, max_length=200),
    level: str | None = None,
    source: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> LogSearchOut:
    started = time.perf_counter()
    events, tokens, total = search_events(
        session,
        account.id,
        query=q,
        level=level,
        source=source,
        limit=limit,
    )
    took_ms = (time.perf_counter() - started) * 1000

    return LogSearchOut(
        total=total,
        took_ms=round(took_ms, 2),
        tokens=tokens,
        events=[
            LogEventOut(
                id=event.id,
                event_uid=event.event_uid,
                ts=event.ts,
                level=event.level,
                source=event.source,
                campaign_external_id=event.campaign_external_id,
                message=event.message,
                fields=parse_fields(event.fields_json),
            )
            for event in events
        ],
    )
