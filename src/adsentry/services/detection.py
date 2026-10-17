"""Anomaly detection for ad delivery metrics.

Two detectors run over the same series and their findings are merged:

**1. Robust statistics.** For each metric, compare today against a baseline of
the previous days, preferring the *same weekday* (ad delivery has a strong
weekly shape - Sunday is not comparable to Tuesday).

The score is the modified z-score:

    z = (x - median) / (1.4826 * MAD)

Median and MAD are used instead of mean and standard deviation because a single
Black-Friday spike would inflate a standard deviation enough to hide the next
two weeks of real anomalies. 3.5 is the Iglewicz-Hoaglin cut-off.

**2. Tracking-health rules.** Some failures are not statistical - zero
impressions, clicks with no conversions, spend with no delivery. These are
absolute conditions, so they are expressed as rules, not deviations.

Both detectors are pure functions of a series, which makes the thresholds
testable and the incident engine deterministic.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date

#: Metrics that are scored statistically. `ctr` is derived from impressions/clicks.
SCORED_METRICS = ("impressions", "clicks", "conversions", "spend_cents", "ctr")

#: Below this many baseline points a z-score is not meaningful.
MIN_BASELINE_POINTS = 5

#: Prefer same-weekday baseline once this many samples exist.
MIN_SAME_WEEKDAY_POINTS = 3

#: Require a material relative move as well as a statistical one, so a very
#: low-variance series does not alert on a 2% wobble.
MIN_RELATIVE_CHANGE = 0.25

METRIC_LABELS = {
    "impressions": "Impressions",
    "clicks": "Clicks",
    "conversions": "Conversions",
    "spend_cents": "Spend",
    "ctr": "CTR",
}

#: Incident kinds read better as IMPRESSIONS_DROP than IMPRESSIONS_CENTS_DROP.
KIND_PREFIX = {
    "impressions": "IMPRESSIONS",
    "clicks": "CLICKS",
    "conversions": "CONVERSIONS",
    "spend_cents": "SPEND",
    "ctr": "CTR",
}


@dataclass(frozen=True, slots=True)
class DayPoint:
    day: date
    impressions: int
    clicks: int
    conversions: int
    spend_cents: int

    @property
    def ctr(self) -> float:
        return self.clicks / self.impressions if self.impressions else 0.0

    @property
    def cvr(self) -> float:
        return self.conversions / self.clicks if self.clicks else 0.0


@dataclass(frozen=True, slots=True)
class Thresholds:
    baseline_days: int = 14
    z_threshold: float = 3.5
    min_impressions: int = 100
    min_clicks: int = 20


@dataclass(frozen=True, slots=True)
class Finding:
    kind: str
    severity: str
    metric: str
    day: date
    observed: float
    baseline: float
    z: float
    message: str
    detail: str


def metric_value(point: DayPoint, metric: str) -> float:
    if metric == "ctr":
        return point.ctr
    if metric == "cvr":
        return point.cvr
    return float(getattr(point, metric))


def robust_z(value: float, sample: list[float]) -> float:
    """Modified z-score.

    When the historical sample is perfectly flat (MAD = 0) the scale falls back
    to 5% of the median level, or 1.0 for a zero median. Without that floor a
    constant series would divide by zero and every tiny deviation would look
    infinitely anomalous.
    """

    if len(sample) < MIN_BASELINE_POINTS:
        return 0.0

    median = statistics.median(sample)
    mad = statistics.median(abs(item - median) for item in sample)
    scale = 1.4826 * mad
    if scale <= 0:
        scale = max(abs(median) * 0.05, 1.0)

    return (value - median) / scale


def _sample(
    points: list[DayPoint], metric: str, as_of: date, baseline_days: int
) -> list[float]:
    history = [point for point in points if point.day < as_of][-baseline_days:]
    same_weekday = [point for point in history if point.day.weekday() == as_of.weekday()]
    chosen = same_weekday if len(same_weekday) >= MIN_SAME_WEEKDAY_POINTS else history
    return [metric_value(point, metric) for point in chosen]


def _volume_ok(point: DayPoint, metric: str, baseline: list[float], thresholds: Thresholds) -> bool:
    if not baseline:
        return False
    level = statistics.median(baseline)
    if metric == "impressions":
        return level >= thresholds.min_impressions
    if metric == "clicks":
        return level >= thresholds.min_clicks
    if metric == "conversions":
        return level >= 3
    if metric == "ctr":
        return point.impressions >= 500 and level > 0
    if metric == "spend_cents":
        return level >= 1_000
    return True


def _format_value(metric: str, value: float) -> str:
    if metric in {"ctr", "cvr"}:
        return f"{value * 100:.2f}%"
    if metric == "spend_cents":
        return f"₹{value / 100:,.0f}"
    return f"{value:,.0f}"


def _describe(metric: str, observed: float, baseline: float, direction: str) -> str:
    label = METRIC_LABELS[metric]
    if baseline > 0:
        change = abs(observed - baseline) / baseline * 100
        return (
            f"{label} {direction} {change:.0f}% vs the baseline "
            f"({_format_value(metric, observed)} vs {_format_value(metric, baseline)})"
        )
    return f"{label} {direction} to {_format_value(metric, observed)}"


def baseline_for(points: list[DayPoint], metric: str, as_of: date, baseline_days: int) -> float:
    sample = _sample(points, metric, as_of, baseline_days)
    return statistics.median(sample) if sample else 0.0


def evaluate_series(
    points: list[DayPoint],
    as_of: date,
    thresholds: Thresholds | None = None,
    campaign_name: str = "Campaign",
) -> list[Finding]:
    """All findings for one campaign on ``as_of``."""

    thresholds = thresholds or Thresholds()
    points = sorted(points, key=lambda point: point.day)
    today = next((point for point in points if point.day == as_of), None)

    if today is None:
        # A silent gap is the classic ad-ops failure: nothing alerts because
        # nothing arrived. Treat missing data as its own incident.
        history = [point for point in points if point.day < as_of][-thresholds.baseline_days:]
        if not history:
            return []
        baseline = statistics.median(point.impressions for point in history)
        return [
            Finding(
                kind="NO_DATA",
                severity="critical",
                metric="impressions",
                day=as_of,
                observed=0.0,
                baseline=baseline,
                z=0.0,
                message=f"{campaign_name} reported no metrics for {as_of.isoformat()}",
                detail=(
                    "The campaign has history but no row for this day. Check the platform "
                    "sync job before assuming delivery simply stopped."
                ),
            )
        ]

    findings: list[Finding] = []
    covered_metrics: set[str] = set()

    baseline_impressions = baseline_for(
        points, "impressions", as_of, thresholds.baseline_days
    )
    baseline_clicks = baseline_for(points, "clicks", as_of, thresholds.baseline_days)
    baseline_ctr = baseline_for(points, "ctr", as_of, thresholds.baseline_days)
    baseline_cvr = baseline_for(points, "cvr", as_of, thresholds.baseline_days)

    # --- rules: absolute conditions ----------------------------------------

    if today.spend_cents > 0 and today.impressions == 0:
        findings.append(
            Finding(
                kind="SPEND_WITHOUT_DELIVERY",
                severity="critical",
                metric="spend_cents",
                day=as_of,
                observed=float(today.spend_cents),
                baseline=baseline_impressions,
                z=0.0,
                message=f"{campaign_name} is spending with zero impressions",
                detail=(
                    "Money is being billed but no delivery is recorded. Check the "
                    "tracking pixel, the ad server sync, and the billing feed in that order."
                ),
            )
        )
        # One root cause, one incident: a delivery outage makes impressions,
        # clicks, CTR, conversions and spend all collapse together. Reporting
        # five deviations would bury the actual finding.
        covered_metrics.update(SCORED_METRICS)

    elif today.impressions == 0 and baseline_impressions >= thresholds.min_impressions:
        findings.append(
            Finding(
                kind="TRACKING_DARK",
                severity="critical",
                metric="impressions",
                day=as_of,
                observed=0.0,
                baseline=baseline_impressions,
                z=0.0,
                message=f"{campaign_name} reported zero impressions",
                detail=(
                    f"Baseline is {baseline_impressions:,.0f} impressions/day. A hard zero "
                    "usually means a broken pixel or a failed platform sync, not a pause."
                ),
            )
        )
        covered_metrics.update(SCORED_METRICS)

    elif (
        today.clicks == 0
        and today.impressions >= 500
        and baseline_clicks >= thresholds.min_clicks
    ):
        findings.append(
            Finding(
                kind="TOTAL_CLICK_LOSS",
                severity="critical",
                metric="clicks",
                day=as_of,
                observed=0.0,
                baseline=baseline_clicks,
                z=0.0,
                message=f"{campaign_name} delivered impressions but zero clicks",
                detail=(
                    "Impressions are flowing so the creative is serving, but the click "
                    "tracker or landing URL is likely broken."
                ),
            )
        )
        covered_metrics.update(SCORED_METRICS)

    if (
        today.clicks >= 50
        and today.conversions == 0
        and baseline_cvr >= 0.01
    ):
        findings.append(
            Finding(
                kind="CONVERSION_COLLAPSE",
                severity="critical",
                metric="conversions",
                day=as_of,
                observed=0.0,
                baseline=baseline_cvr,
                z=0.0,
                message=f"{campaign_name} recorded clicks but no conversions",
                detail=(
                    f"Baseline conversion rate is {baseline_cvr * 100:.2f}% on "
                    f"{today.clicks:,} clicks today. Suspect the postback, the checkout, "
                    "or attribution window changes."
                ),
            )
        )
        covered_metrics.add("conversions")

    if (
        baseline_ctr > 0
        and today.impressions >= 1_000
        and today.ctr < baseline_ctr * 0.5
    ):
        findings.append(
            Finding(
                kind="CREATIVE_FATIGUE",
                severity="warning",
                metric="ctr",
                day=as_of,
                observed=today.ctr,
                baseline=baseline_ctr,
                z=0.0,
                message=f"{campaign_name} CTR is less than half its baseline",
                detail=(
                    f"{today.ctr * 100:.2f}% today vs {baseline_ctr * 100:.2f}% baseline on "
                    f"{today.impressions:,} impressions. Rotate creative before CPA follows."
                ),
            )
        )
        covered_metrics.add("ctr")

    # --- statistics: deviations from the campaign's own baseline ------------

    for metric in SCORED_METRICS:
        if metric in covered_metrics:
            continue

        sample = _sample(points, metric, as_of, thresholds.baseline_days)
        if len(sample) < MIN_BASELINE_POINTS:
            continue
        if not _volume_ok(today, metric, sample, thresholds):
            continue

        observed = metric_value(today, metric)
        baseline = statistics.median(sample)
        z = robust_z(observed, sample)

        if abs(z) < thresholds.z_threshold:
            continue
        if baseline > 0 and abs(observed - baseline) / baseline < MIN_RELATIVE_CHANGE:
            continue

        direction = "up" if z > 0 else "down"
        suffix = "SPIKE" if z > 0 else "DROP"
        severity = "critical" if z <= -6 or (observed == 0 and metric in {"impressions", "clicks"}) else "warning"

        findings.append(
            Finding(
                kind=f"{KIND_PREFIX.get(metric, metric.upper())}_{suffix}",
                severity=severity,
                metric=metric,
                day=as_of,
                observed=observed,
                baseline=baseline,
                z=round(z, 2),
                message=f"{campaign_name}: {_describe(metric, observed, baseline, direction)}",
                detail=(
                    f"Modified z-score {z:.1f} against a {len(sample)}-point baseline "
                    f"(median {_format_value(metric, baseline)})."
                ),
            )
        )

    findings.sort(key=lambda finding: (finding.severity != "critical", finding.metric))
    return findings
