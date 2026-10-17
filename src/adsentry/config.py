"""Configuration via environment variables (12-factor).

Detection thresholds live here rather than in the algorithm, because "what
counts as an anomaly" is an operational policy that differs per account.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    app_name: str = "AdSentry"
    version: str = "1.0.0"
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./adsentry.db")
    )
    demo_mode: bool = field(default_factory=lambda: _bool("ADSENTRY_DEMO", True))
    seed_demo_data: bool = field(default_factory=lambda: _bool("ADSENTRY_SEED", True))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    #: How many days of history form the baseline.
    baseline_days: int = field(default_factory=lambda: _int("DETECTION_BASELINE_DAYS", 14))
    #: Modified z-score threshold. 3.5 is the Iglewicz-Hoaglin recommendation.
    z_threshold: float = field(default_factory=lambda: _float("DETECTION_Z_THRESHOLD", 3.5))
    #: Ignore metrics below this level; a 2x move on 3 clicks is not news.
    min_impressions: int = field(default_factory=lambda: _int("DETECTION_MIN_IMPRESSIONS", 100))
    min_clicks: int = field(default_factory=lambda: _int("DETECTION_MIN_CLICKS", 20))
    #: An opened incident auto-resolves once its condition stops recurring.
    auto_resolve: bool = field(default_factory=lambda: _bool("DETECTION_AUTO_RESOLVE", True))


@lru_cache
def get_settings() -> Settings:
    return Settings()
