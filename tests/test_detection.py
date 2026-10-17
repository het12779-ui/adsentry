from __future__ import annotations

from datetime import date, timedelta

import pytest

from adsentry.services.detection import (
    DayPoint,
    Thresholds,
    baseline_for,
    evaluate_series,
    robust_z,
)

TODAY = date(2026, 10, 17)


def series(
    days: int = 15,
    impressions: int = 10_000,
    clicks: int = 300,
    conversions: int = 12,
    spend_cents: int = 40_000,
    jitter: bool = True,
    last: dict | None = None,
    include_today: bool = True,
) -> list[DayPoint]:
    points = []
    for offset in range(days - 1, -1, -1):
        day = TODAY - timedelta(days=offset)
        if not include_today and offset == 0:
            continue

        wobble = (offset % 3) - 1 if jitter else 0
        values = {
            "impressions": impressions + wobble * 120,
            "clicks": clicks + wobble * 4,
            "conversions": conversions + wobble,
            "spend_cents": spend_cents + wobble * 400,
        }
        if last and offset == 0:
            values.update(last)

        points.append(DayPoint(day=day, **values))
    return points


def kinds(findings) -> set[str]:
    return {finding.kind for finding in findings}


# --- statistics ------------------------------------------------------------


def test_flat_series_does_not_divide_by_zero():
    assert robust_z(100, [100, 100, 100, 100, 100, 100]) == 0.0


def test_flat_series_flags_a_large_deviation():
    # MAD is zero, so the scale floor (5% of the level) applies: a 50% jump is 10 units.
    assert robust_z(150, [100] * 10) >= 9.9


def test_too_few_points_is_not_scored():
    assert robust_z(999, [100, 101]) == 0.0


def test_median_and_mad_resist_a_single_spike():
    sample = [100, 102, 98, 101, 99, 100, 5_000]
    # Median 100, MAD 1 -> the 5,000 outlier does not inflate the scale enough
    # to hide a 40% move.
    assert robust_z(140, sample) > 10


def test_baseline_prefers_the_same_weekday():
    points = []
    for offset in range(28, -1, -1):
        day = TODAY - timedelta(days=offset)
        value = 100 if day.weekday() == TODAY.weekday() else 900
        points.append(
            DayPoint(day=day, impressions=value, clicks=1, conversions=0, spend_cents=0)
        )

    # 14 days only contains two same-weekday samples, so it falls back to all
    # days; 21 days gives three and the weekly shape wins.
    assert baseline_for(points, "impressions", TODAY, 14) == pytest.approx(900)
    assert baseline_for(points, "impressions", TODAY, 21) == pytest.approx(100)


def test_healthy_campaign_raises_nothing():
    assert evaluate_series(series(), TODAY) == []


def test_low_volume_is_ignored():
    # 40 impressions doubling is not an incident.
    findings = evaluate_series(
        series(impressions=40, clicks=2, conversions=0, spend_cents=100), TODAY
    )
    assert findings == []


def test_spend_spike_is_detected():
    findings = evaluate_series(series(spend_cents=40_000, last={"spend_cents": 130_000}), TODAY)
    assert "SPEND_SPIKE" in kinds(findings)
    spike = next(finding for finding in findings if finding.kind == "SPEND_SPIKE")
    assert spike.observed == 130_000
    assert spike.z > 3.5


# --- rules -----------------------------------------------------------------


def test_tracking_blackout_is_critical():
    findings = evaluate_series(
        series(last={"impressions": 0, "clicks": 0, "conversions": 0, "spend_cents": 0}),
        TODAY,
    )
    dark = next(finding for finding in findings if finding.kind == "TRACKING_DARK")
    assert dark.severity == "critical"
    assert "zero impressions" in dark.message


def test_spend_without_delivery_outranks_tracking_dark():
    findings = evaluate_series(
        series(last={"impressions": 0, "clicks": 0, "conversions": 0, "spend_cents": 35_000}),
        TODAY,
    )
    assert "SPEND_WITHOUT_DELIVERY" in kinds(findings)
    assert "TRACKING_DARK" not in kinds(findings)


def test_zero_clicks_with_impressions():
    findings = evaluate_series(
        series(impressions=20_000, clicks=500, last={"clicks": 0, "conversions": 0}), TODAY
    )
    assert "TOTAL_CLICK_LOSS" in kinds(findings)


def test_conversion_collapse():
    findings = evaluate_series(
        series(clicks=400, conversions=20, last={"conversions": 0}), TODAY
    )
    assert "CONVERSION_COLLAPSE" in kinds(findings)
    collapse = next(finding for finding in findings if finding.kind == "CONVERSION_COLLAPSE")
    assert collapse.severity == "critical"


def test_creative_fatigue():
    # CTR falls from 2% to 0.5% (75% down) on a large impression base.
    findings = evaluate_series(
        series(impressions=60_000, clicks=1_200, last={"impressions": 60_000, "clicks": 300}),
        TODAY,
    )
    assert "CREATIVE_FATIGUE" in kinds(findings)
    assert "ctr_DROP" not in kinds(findings)  # the specific rule suppresses the generic one


def test_missing_day_is_its_own_incident():
    findings = evaluate_series(series(include_today=False), TODAY)
    assert kinds(findings) == {"NO_DATA"}
    assert findings[0].severity == "critical"


def test_no_history_means_no_findings():
    assert evaluate_series([], TODAY) == []


def test_custom_thresholds_are_respected():
    # The same 2x spend move is flagged at the default threshold and ignored
    # when an operator deliberately raises the bar.
    points = series(spend_cents=40_000, last={"spend_cents": 80_000})
    assert "SPEND_SPIKE" in kinds(evaluate_series(points, TODAY))
    assert "SPEND_SPIKE" not in kinds(
        evaluate_series(points, TODAY, Thresholds(z_threshold=100.0))
    )
