from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from adsentry.config import Settings
from adsentry.db import Base
from adsentry.main import create_app
from tests.conftest import make_campaign

TODAY = datetime.now(timezone.utc).date()


def _log(client, uid, message):
    return client.post(
        "/api/v1/logs",
        json={
            "event_uid": uid,
            "ts": f"{TODAY}T09:00:00Z",
            "level": "info",
            "source": "tracking",
            "message": message,
        },
    )


def test_campaign_lists_are_scoped(northwind_client, skyline_client):
    make_campaign(northwind_client, "NW-1")
    assert [row["external_id"] for row in northwind_client.get("/api/v1/campaigns").json()] == ["NW-1"]
    assert skyline_client.get("/api/v1/campaigns").json() == []


def test_metrics_cannot_target_another_tenants_campaign(northwind_client, skyline_client):
    make_campaign(northwind_client, "NW-2")
    response = skyline_client.post(
        "/api/v1/metrics",
        json={
            "campaign_external_id": "NW-2",
            "day": TODAY.isoformat(),
            "impressions": 100,
            "clicks": 1,
            "conversions": 0,
            "spend_cents": 10,
        },
    )
    assert response.status_code == 201
    assert response.json()["rejected"] == 1


def test_incidents_are_scoped(northwind_client, skyline_client):
    make_campaign(northwind_client, "NW-3")
    for offset in range(10, 0, -1):
        northwind_client.post(
            "/api/v1/metrics",
            json={
                "campaign_external_id": "NW-3",
                "day": (TODAY - timedelta(days=offset)).isoformat(),
                "impressions": 10_000,
                "clicks": 300,
                "conversions": 12,
                "spend_cents": 40_000,
            },
        )
    northwind_client.post(
        "/api/v1/metrics",
        json={
            "campaign_external_id": "NW-3",
            "day": TODAY.isoformat(),
            "impressions": 0,
            "clicks": 0,
            "conversions": 0,
            "spend_cents": 0,
        },
    )
    northwind_client.post("/api/v1/incidents/evaluate")

    incident = northwind_client.get("/api/v1/incidents").json()[0]
    assert skyline_client.get("/api/v1/incidents").json() == []
    assert skyline_client.post(f"/api/v1/incidents/{incident['id']}/resolve").status_code == 404


def test_log_search_is_scoped_even_for_identical_messages(northwind_client, skyline_client):
    _log(northwind_client, "nw-log", "Postback rejected: signature mismatch")
    _log(skyline_client, "sk-log", "Postback rejected: signature mismatch")

    northwind = northwind_client.get("/api/v1/logs", params={"q": "postback"}).json()
    skyline = skyline_client.get("/api/v1/logs", params={"q": "postback"}).json()

    assert [event["event_uid"] for event in northwind["events"]] == ["nw-log"]
    assert [event["event_uid"] for event in skyline["events"]] == ["sk-log"]

    assert northwind_client.get("/api/v1/logs/stats").json()["events"] == 1


def test_unknown_api_key_is_rejected(client):
    assert client.get("/api/v1/campaigns", headers={"X-API-Key": "bad"}).status_code == 401


def test_missing_credentials_are_rejected(client):
    assert client.get("/api/v1/campaigns").status_code == 401


def test_demo_header_fails_when_demo_mode_is_off(tenants):
    strict_app = create_app(Settings(demo_mode=False, seed_demo_data=False))
    with TestClient(strict_app) as strict_client:
        response = strict_client.get(
            "/api/v1/campaigns", headers={"X-Account-Id": str(tenants[0].id)}
        )
    assert response.status_code == 401


def test_every_business_table_has_an_account_column():
    exempt = {"accounts"}
    missing = [
        table.name
        for table in Base.metadata.sorted_tables
        if table.name not in exempt and "account_id" not in table.c
    ]
    assert missing == [], f"tables missing account_id: {missing}"
