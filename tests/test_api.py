from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tests.conftest import make_campaign

TODAY = datetime.now(timezone.utc).date()


def post_metric(client, external_id, day, impressions, clicks, conversions, spend):
    return client.post(
        "/api/v1/metrics",
        json={
            "campaign_external_id": external_id,
            "day": day.isoformat(),
            "impressions": impressions,
            "clicks": clicks,
            "conversions": conversions,
            "spend_cents": spend,
        },
    )


def seed_history(client, external_id="CMP-001", days=15):
    """Steady history, ending yesterday, so today can be the interesting day."""
    for offset in range(days, 0, -1):
        day = TODAY - timedelta(days=offset)
        wobble = (offset % 3) - 1
        response = post_metric(
            client,
            external_id,
            day,
            20_000 + wobble * 200,
            600 + wobble * 5,
            24 + wobble,
            90_000 + wobble * 1_000,
        )
        assert response.status_code == 201, response.text


def test_create_campaign_and_reject_duplicate(northwind_client):
    created = make_campaign(northwind_client)
    assert created["external_id"] == "CMP-001"

    duplicate = northwind_client.post(
        "/api/v1/campaigns",
        json={"external_id": "CMP-001", "name": "Again", "channel": "meta"},
    )
    assert duplicate.status_code == 409


def test_metric_upsert_reports_new_unchanged_and_restated(northwind_client):
    make_campaign(northwind_client)

    first = post_metric(northwind_client, "CMP-001", TODAY, 1_000, 30, 2, 4_000).json()
    assert first == {"accepted": 1, "updated": 0, "unchanged": 0, "rejected": 0, "errors": []}

    same = post_metric(northwind_client, "CMP-001", TODAY, 1_000, 30, 2, 4_000).json()
    assert same["unchanged"] == 1

    corrected = post_metric(northwind_client, "CMP-001", TODAY, 1_000, 30, 3, 4_200).json()
    assert corrected["updated"] == 1


def test_metric_for_unknown_campaign_is_rejected(northwind_client):
    response = post_metric(northwind_client, "NOPE", TODAY, 1, 1, 1, 1)
    assert response.status_code == 201
    assert response.json()["rejected"] == 1


def test_tracking_blackout_opens_one_incident(northwind_client):
    make_campaign(northwind_client)
    seed_history(northwind_client)
    post_metric(northwind_client, "CMP-001", TODAY, 0, 0, 0, 0)

    first = northwind_client.post("/api/v1/incidents/evaluate").json()
    assert first["campaigns_evaluated"] == 1
    assert first["opened"] == 1

    incidents = northwind_client.get("/api/v1/incidents").json()
    assert len(incidents) == 1
    assert incidents[0]["kind"] == "TRACKING_DARK"
    assert incidents[0]["severity"] == "critical"
    assert incidents[0]["campaign_name"] == "Campaign CMP-001"

    # Re-running the same evaluation must not duplicate or inflate.
    second = northwind_client.post("/api/v1/incidents/evaluate").json()
    assert second["opened"] == 0
    assert second["updated"] == 0
    assert len(northwind_client.get("/api/v1/incidents").json()) == 1


def test_restating_metrics_auto_resolves_the_incident(northwind_client):
    make_campaign(northwind_client)
    seed_history(northwind_client)
    post_metric(northwind_client, "CMP-001", TODAY, 0, 0, 0, 0)
    northwind_client.post("/api/v1/incidents/evaluate")

    # The sync job catches up and sends the real numbers.
    post_metric(northwind_client, "CMP-001", TODAY, 21_000, 640, 26, 95_000)
    result = northwind_client.post("/api/v1/incidents/evaluate").json()

    assert result["resolved"] == 1
    assert northwind_client.get("/api/v1/incidents").json() == []

    history = northwind_client.get("/api/v1/incidents", params={"open": False}).json()
    assert history[0]["resolved_at"] is not None


def test_incident_can_be_resolved_manually(northwind_client):
    make_campaign(northwind_client)
    seed_history(northwind_client)
    post_metric(northwind_client, "CMP-001", TODAY, 0, 0, 0, 0)
    northwind_client.post("/api/v1/incidents/evaluate")
    incident = northwind_client.get("/api/v1/incidents").json()[0]

    response = northwind_client.post(f"/api/v1/incidents/{incident['id']}/resolve")
    assert response.status_code == 200
    assert response.json()["resolved_at"] is not None
    assert northwind_client.post("/api/v1/incidents/9999/resolve").status_code == 404


def test_campaign_health_includes_sparkline(northwind_client):
    make_campaign(northwind_client)
    seed_history(northwind_client, days=14)

    rows = northwind_client.get("/api/v1/campaigns").json()
    assert len(rows) == 1
    health = rows[0]
    assert health["last_day"] == (TODAY - timedelta(days=1)).isoformat()
    assert len(health["sparkline"]) == 14
    # 13 days of history in the window, then today's genuine zero.
    assert all(value > 0 for value in health["sparkline"][:-1])
    assert health["sparkline"][-1] == 0
    assert health["ctr"] > 0

    series = northwind_client.get(
        f"/api/v1/campaigns/{health['id']}/series", params={"days": 7}
    ).json()
    assert len(series["series"]) == 7


def test_log_ingest_indexes_tokens_and_dedupes(northwind_client):
    payload = {
        "event_uid": "log-1",
        "ts": f"{TODAY}T10:00:00Z",
        "level": "error",
        "source": "tracking",
        "campaign_external_id": "NW-PMAX-02",
        "message": "Postback rejected: signature mismatch for conversion 12345",
        "fields": {"latency_ms": 120},
    }
    first = northwind_client.post("/api/v1/logs", json=payload).json()
    assert first["accepted"] == 1
    assert first["tokens_indexed"] >= 5  # stopwords dropped

    second = northwind_client.post("/api/v1/logs", json=payload).json()
    assert second["duplicates"] == 1
    assert second["accepted"] == 0

    stats = northwind_client.get("/api/v1/logs/stats").json()
    assert stats["events"] == 1
    assert stats["by_level"]["error"] == 1


def test_log_search_requires_all_tokens(northwind_client):
    events = [
        ("log-a", "error", "tracking", "Postback rejected: signature mismatch"),
        ("log-b", "error", "tracking", "Postback received for conversion 99"),
        ("log-c", "warning", "api", "Rate limit 429 from platform"),
    ]
    for uid, level, source, message in events:
        response = northwind_client.post(
            "/api/v1/logs",
            json={
                "event_uid": uid,
                "ts": f"{TODAY}T09:00:00Z",
                "level": level,
                "source": source,
                "message": message,
            },
        )
        assert response.status_code == 201

    both = northwind_client.get("/api/v1/logs", params={"q": "postback signature"}).json()
    assert both["total"] == 1
    assert both["events"][0]["event_uid"] == "log-a"
    assert set(both["tokens"]) == {"postback", "signature"}

    postback = northwind_client.get("/api/v1/logs", params={"q": "postback"}).json()
    assert postback["total"] == 2

    by_level = northwind_client.get("/api/v1/logs", params={"q": "postback", "level": "warning"})
    assert by_level.json()["total"] == 0

    empty = northwind_client.get("/api/v1/logs", params={"q": "nothingmatchesthis"}).json()
    assert empty["total"] == 0


def test_log_search_is_case_insensitive_and_ignores_stopwords(northwind_client):
    northwind_client.post(
        "/api/v1/logs",
        json={
            "event_uid": "log-case",
            "ts": f"{TODAY}T09:00:00Z",
            "level": "info",
            "source": "sync",
            "message": "Platform Sync completed for the campaign",
        },
    )
    body = northwind_client.get("/api/v1/logs", params={"q": "PLATFORM the sync"}).json()
    assert body["total"] == 1
    assert body["tokens"] == ["platform", "sync"]


def test_health_and_metrics(northwind_client):
    assert northwind_client.get("/healthz").json() == {"status": "ok"}
    assert northwind_client.get("/readyz").status_code == 200
    assert "http_requests_total" in northwind_client.get("/metrics").text
