from __future__ import annotations

from datetime import date, timedelta

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from adsentry.services.detection import DayPoint, evaluate_series

scenarios("features/detection.feature")

TODAY = date(2026, 10, 17)


@pytest.fixture
def ctx() -> dict:
    return {}


def _steady(days: int = 15, last_overrides: dict | None = None, include_today: bool = True):
    points = []
    for offset in range(days - 1, -1, -1):
        day = TODAY - timedelta(days=offset)
        if not include_today and offset == 0:
            continue
        wobble = (offset % 3) - 1
        values = {
            "impressions": 20_000 + wobble * 200,
            "clicks": 600 + wobble * 6,
            "conversions": 24 + wobble,
            "spend_cents": 90_000 + wobble * 1_000,
        }
        if last_overrides and offset == 0:
            values.update(last_overrides)
        points.append(DayPoint(day=day, **values))
    return points


@given("a campaign with two weeks of steady history")
def steady_history(ctx):
    ctx["points"] = _steady()


@when("impressions fall to zero on the latest day")
def impressions_zero(ctx):
    ctx["findings"] = evaluate_series(
        _steady(
            last_overrides={
                "impressions": 0,
                "clicks": 0,
                "conversions": 0,
                "spend_cents": 0,
            }
        ),
        TODAY,
    )


@when("the latest day matches the baseline")
def latest_is_normal(ctx):
    ctx["findings"] = evaluate_series(_steady(), TODAY)


@when("the latest day has no metrics row")
def latest_missing(ctx):
    ctx["findings"] = evaluate_series(_steady(include_today=False), TODAY)


@when("spend triples on the latest day")
def spend_triples(ctx):
    ctx["findings"] = evaluate_series(
        _steady(last_overrides={"spend_cents": 270_000}), TODAY
    )


@then(parsers.parse('the detector reports "{kind}" as critical'))
def reports_critical(ctx, kind: str):
    match = next((finding for finding in ctx["findings"] if finding.kind == kind), None)
    assert match is not None, [finding.kind for finding in ctx["findings"]]
    assert match.severity == "critical"


@then(parsers.parse('the detector reports a "{kind}" warning'))
def reports_warning(ctx, kind: str):
    match = next((finding for finding in ctx["findings"] if finding.kind == kind), None)
    assert match is not None, [finding.kind for finding in ctx["findings"]]
    assert match.severity == "warning"


@then("no incident is reported")
def no_incident(ctx):
    assert ctx["findings"] == [], [finding.kind for finding in ctx["findings"]]
