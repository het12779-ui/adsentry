"""Token-based log search backed by a SQL inverted index.

A naive implementation is ``WHERE message LIKE '%timeout%'``, which cannot use an
index and degrades linearly with history. Instead, ingestion tokenises each event
into ``log_tokens`` (one row per token), and a search becomes an indexed join:

    SELECT event_id FROM log_tokens
    WHERE account_id = ? AND token IN (...)
    GROUP BY event_id
    HAVING COUNT(DISTINCT token) = <number of query tokens>

The ``HAVING`` is what gives AND semantics: an event must contain every token.
This is what Logstash/Elasticsearch does, reduced to plain SQL. It also keeps the
whole feature inside the same transactional store as the metrics, so there is no
second system to operate for a small deployment.
"""

from __future__ import annotations

import json
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from adsentry.models import LogEvent, LogToken

TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Words that carry no search signal and would match almost every event.
STOPWORDS = {
    "the", "and", "for", "was", "were", "with", "from", "that", "this", "not",
    "but", "are", "has", "had", "have", "its", "into", "our", "you", "your",
    "after", "before", "while", "when", "then", "than", "them", "they", "will",
}

MAX_TOKENS_PER_EVENT = 60


def tokenize(text: str | None) -> list[str]:
    if not text:
        return []
    tokens = []
    for token in TOKEN_RE.findall(text.lower()):
        if len(token) < 2 or len(token) > 40 or token in STOPWORDS:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens[:MAX_TOKENS_PER_EVENT]


def index_event(session: Session, account_id: int, event: LogEvent) -> list[str]:
    """Create the inverted-index rows for one event. Returns the tokens."""

    payload = " ".join(
        part
        for part in (event.message, event.source, event.campaign_external_id, event.fields_json)
        if part
    )
    tokens = tokenize(payload)
    for token in tokens:
        session.add(LogToken(account_id=account_id, event_id=event.id, token=token))
    return tokens


def search_events(
    session: Session,
    account_id: int,
    query: str | None,
    level: str | None,
    source: str | None,
    start=None,
    end=None,
    limit: int = 100,
) -> tuple[list[LogEvent], list[str], int]:
    tokens = tokenize(query)
    stmt = select(LogEvent).where(LogEvent.account_id == account_id)

    if tokens:
        matched_ids = (
            select(LogToken.event_id)
            .where(LogToken.account_id == account_id, LogToken.token.in_(tokens))
            .group_by(LogToken.event_id)
            .having(func.count(func.distinct(LogToken.token)) == len(tokens))
            .subquery()
        )
        stmt = stmt.where(LogEvent.id.in_(select(matched_ids.c.event_id)))

    if level:
        stmt = stmt.where(LogEvent.level == level)
    if source:
        stmt = stmt.where(LogEvent.source == source)
    if start is not None:
        stmt = stmt.where(LogEvent.ts >= start)
    if end is not None:
        stmt = stmt.where(LogEvent.ts < end)

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        session.scalars(
            stmt.order_by(LogEvent.ts.desc(), LogEvent.id.desc()).limit(limit)
        ).all()
    )
    return rows, tokens, int(total)


def parse_fields(fields_json: str | None) -> dict:
    if not fields_json:
        return {}
    try:
        payload = json.loads(fields_json)
    except (TypeError, ValueError):
        return {"_raw": fields_json}
    return payload if isinstance(payload, dict) else {"_value": payload}
