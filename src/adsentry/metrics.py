"""Prometheus metrics.

Deliberately dependency-free: the exposition format is small and stable, and a
single-process demo does not need the client library. Counters are labelled
with the *route template* so cardinality stays bounded (see middleware).

In production this endpoint is what a Prometheus scrape config points at; the
same numbers would feed Grafana panels next to the ELK dashboards.
"""

from __future__ import annotations

import threading
from collections import defaultdict

LabelSet = tuple[tuple[str, str], ...]


def _labels(**labels: str) -> LabelSet:
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def _render_labels(labels: LabelSet) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{key}="{value}"' for key, value in labels)
    return "{" + inner + "}"


class MetricsRegistry:
    def __init__(self) -> None:
        self._counters: dict[str, dict[LabelSet, float]] = defaultdict(dict)
        self._observations: dict[str, dict[LabelSet, list[float]]] = defaultdict(dict)
        self._lock = threading.Lock()

    def inc(self, name: str, amount: float = 1.0, **labels: str) -> None:
        key = _labels(**labels)
        with self._lock:
            bucket = self._counters[name]
            bucket[key] = bucket.get(key, 0.0) + amount

    def observe(self, name: str, value: float, **labels: str) -> None:
        key = _labels(**labels)
        with self._lock:
            self._observations[name].setdefault(key, []).append(value)

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        key = _labels(**labels)
        with self._lock:
            self._counters[f"__gauge__{name}"][key] = value

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            for name, series in sorted(self._counters.items()):
                if name.startswith("__gauge__"):
                    real = name.removeprefix("__gauge__")
                    lines.append(f"# TYPE {real} gauge")
                    for labels, value in sorted(series.items()):
                        lines.append(f"{real}{_render_labels(labels)} {value:g}")
                    continue
                lines.append(f"# TYPE {name} counter")
                for labels, value in sorted(series.items()):
                    lines.append(f"{name}{_render_labels(labels)} {value:g}")

            for name, series in sorted(self._observations.items()):
                lines.append(f"# TYPE {name} summary")
                for labels, values in sorted(series.items()):
                    total = sum(values)
                    lines.append(f"{name}_count{_render_labels(labels)} {len(values)}")
                    lines.append(f"{name}_sum{_render_labels(labels)} {total:.6f}")
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._observations.clear()


METRICS = MetricsRegistry()
