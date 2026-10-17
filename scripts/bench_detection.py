"""Benchmark detection evaluation and log search end to end.

Usage:
    python scripts/bench_detection.py
    python scripts/bench_detection.py --campaigns 50 500 --logs 20000
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import tempfile
import time
import uuid
from datetime import date, datetime, timedelta

_WORKDIR = tempfile.mkdtemp(prefix="adsentry-bench-")
os.environ["DATABASE_URL"] = f"sqlite:///{_WORKDIR}/bench.db"
os.environ["ADSENTRY_SEED"] = "0"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import insert, select  # noqa: E402

from adsentry.config import Settings  # noqa: E402
from adsentry.db import Base, SessionLocal, engine  # noqa: E402
from adsentry.main import create_app  # noqa: E402
from adsentry.models import Account, Campaign, LogEvent, LogToken, MetricDay  # noqa: E402
from adsentry.services.logsearch import tokenize  # noqa: E402
from adsentry.tenant import hash_api_key  # noqa: E402

API_KEY = "bench-key"
TODAY = date.today()
HISTORY_DAYS = 25


def seed_campaigns(count: int) -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    with SessionLocal() as session:
        account = Account(
            name="Bench", slug="bench", api_key_hash=hash_api_key(API_KEY), plan="scale"
        )
        session.add(account)
        session.flush()

        campaigns = [
            {
                "account_id": account.id,
                "external_id": f"BENCH-{index:05d}",
                "name": f"Benchmark {index}",
                "channel": "google",
                "status": "active",
                "created_at": datetime.now(),
            }
            for index in range(count)
        ]
        session.execute(insert(Campaign), campaigns)

        ids = session.execute(Campaign.__table__.select().with_only_columns(Campaign.id)).scalars().all()

        metrics = []
        for campaign_id in ids:
            for offset in range(HISTORY_DAYS, -1, -1):
                wobble = (offset % 3) - 1
                metrics.append(
                    {
                        "account_id": account.id,
                        "campaign_id": campaign_id,
                        "day": TODAY - timedelta(days=offset),
                        "impressions": 20_000 + wobble * 200,
                        "clicks": 600 + wobble * 6,
                        "conversions": 24 + wobble,
                        "spend_cents": 90_000 + wobble * 900,
                    }
                )
        session.execute(insert(MetricDay), metrics)
        session.commit()


def seed_logs(count: int) -> None:
    with SessionLocal() as session:
        account_id = session.scalar(select(Account.id))

        events = []
        for index in range(count):
            message = (
                "Postback rejected: signature mismatch for conversion"
                if index % 5 == 0
                else "Platform sync completed without errors"
            )
            events.append(
                {
                    "account_id": account_id,
                    "event_uid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"log-{index}")),
                    "ts": datetime.combine(TODAY, datetime.min.time()) + timedelta(seconds=index),
                    "level": "error" if index % 5 == 0 else "info",
                    "source": "tracking",
                    "campaign_external_id": f"BENCH-{index % 10:05d}",
                    "message": message,
                    "fields_json": json.dumps({"campaign": "bench"}),
                }
            )
        session.execute(insert(LogEvent), events)

        ids = session.execute(LogEvent.__table__.select().with_only_columns(LogEvent.id)).scalars().all()
        tokens = []
        for event_id in ids:
            for token in tokenize("Postback rejected signature mismatch conversion tracking"):
                tokens.append({"account_id": account_id, "event_id": event_id, "token": token})
        session.execute(insert(LogToken), tokens)
        session.commit()


def time_calls(client, url: str, method: str, iterations: int) -> tuple[float, float, float]:
    for _ in range(2):
        assert client.request(method, url).status_code == 200

    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        response = client.request(method, url)
        samples.append((time.perf_counter() - started) * 1000)
        assert response.status_code == 200

    samples.sort()
    return (
        statistics.median(samples),
        samples[max(0, int(len(samples) * 0.95) - 1)],
        max(samples),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaigns", type=int, nargs="+", default=[25, 250, 1000])
    parser.add_argument("--logs", type=int, default=20_000)
    parser.add_argument("--iterations", type=int, default=10)
    args = parser.parse_args()

    app = create_app(Settings(demo_mode=False, seed_demo_data=False))

    print(f"AdSentry benchmark · {args.iterations} iterations")
    print()
    print("| campaigns | metrics rows | evaluate p50 | p95 |")
    print("|---:|---:|---:|---:|")
    for count in args.campaigns:
        seed_campaigns(count)
        with TestClient(app) as client:
            client.headers.update({"X-API-Key": API_KEY})
            p50, p95, _ = time_calls(
                client,
                f"/api/v1/incidents/evaluate?as_of={TODAY.isoformat()}",
                "POST",
                args.iterations,
            )
        print(f"| {count:,} | {count * (HISTORY_DAYS + 1):,} | {p50:.1f} ms | {p95:.1f} ms |")

    print()
    seed_campaigns(10)
    seed_logs(args.logs)
    with TestClient(app) as client:
        client.headers.update({"X-API-Key": API_KEY})
        p50, p95, _ = time_calls(
            client,
            "/api/v1/logs?q=postback%20signature&limit=50",
            "GET",
            args.iterations,
        )
        recent_p50, _, _ = time_calls(
            client, "/api/v1/logs?limit=50", "GET", args.iterations
        )

    print(f"| log events | tokens | AND search p50 | p95 |")
    print("|---:|---:|---:|---:|")
    print(
        f"| {args.logs:,} | {args.logs * 6:,} | {p50:.1f} ms | {p95:.1f} ms |"
    )
    print()
    print(f"Unfiltered newest-first query: {recent_p50:.1f} ms")


if __name__ == "__main__":
    main()
