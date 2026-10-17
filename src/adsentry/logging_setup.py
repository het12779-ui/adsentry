"""Structured logging.

Ad ops debugging is mostly "which campaign, which tenant, which request". Emit
one JSON object per line with those fields attached, so the output can ship to
Logstash/Kibana (or any log store) without a regex parser. All logs go to
stdout because containers should not own log files.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
tenant_id_var: ContextVar[str] = ContextVar("tenant_id", default="-")

_RESERVED = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
            "tenant_id": tenant_id_var.get(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # uvicorn installs its own handlers; route them through ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


async def request_logging_middleware(request, call_next):
    from adsentry.metrics import METRICS

    request_id = request.headers.get("X-Request-Id") or new_request_id()
    token = request_id_var.set(request_id)
    started = time.perf_counter()

    response = await call_next(request)

    elapsed = time.perf_counter() - started
    # Use the route template, not the raw URL: /campaigns/42 and /campaigns/43
    # must be one metric series, not unbounded cardinality.
    route = request.scope.get("route")
    path_label = getattr(route, "path", request.url.path)
    METRICS.inc(
        "http_requests_total",
        method=request.method,
        path=path_label,
        status=str(response.status_code),
    )
    METRICS.observe("http_request_duration_seconds", elapsed)

    logging.getLogger("adsentry.access").info(
        "request",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": round(elapsed * 1000, 2),
            "tenant_id": tenant_id_var.get(),
        },
    )

    response.headers["X-Request-Id"] = request_id
    request_id_var.reset(token)
    return response
