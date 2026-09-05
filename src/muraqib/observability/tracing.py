"""Minimal span tracing that upgrades to OpenTelemetry when available.

Always-on local timing, zero dependency. If the ``otel`` extra is installed and
MURAQIB_OTEL_ENABLED=1, spans are additionally emitted to an OTLP collector.
"""

from __future__ import annotations

import contextlib
import logging
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("muraqib.trace")


@dataclass(slots=True)
class Span:
    name: str
    span_id: str
    start: float
    end: float = 0.0
    attributes: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @property
    def duration_ms(self) -> float:
        return round(((self.end or time.perf_counter()) - self.start) * 1000, 2)


class Tracer:
    def __init__(self, enabled_otel: bool = False, service: str = "muraqib"):
        self.spans: list[Span] = []
        self._otel = None
        if enabled_otel:
            try:
                from opentelemetry import trace  # noqa: PLC0415

                self._otel = trace.get_tracer(service)
            except Exception:  # noqa: BLE001
                log.info("opentelemetry not available; using local spans only")

    @contextlib.contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Span]:
        s = Span(
            name=name,
            span_id=uuid.uuid4().hex[:12],
            start=time.perf_counter(),
            attributes=attributes,
        )
        self.spans.append(s)
        otel_cm = self._otel.start_as_current_span(name) if self._otel else contextlib.nullcontext()
        try:
            with otel_cm:
                yield s
        except Exception as exc:
            s.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            s.end = time.perf_counter()
            log.debug("span", extra={"span": name, "ms": s.duration_ms, **attributes})

    def summary(self) -> list[dict[str, Any]]:
        agg: dict[str, dict[str, Any]] = {}
        for s in self.spans:
            a = agg.setdefault(s.name, {"name": s.name, "count": 0, "total_ms": 0.0, "errors": 0})
            a["count"] += 1
            a["total_ms"] = round(a["total_ms"] + s.duration_ms, 2)
            if s.error:
                a["errors"] += 1
        return sorted(agg.values(), key=lambda x: x["total_ms"], reverse=True)


_default = Tracer()


@contextlib.contextmanager
def span(name: str, **attributes: Any) -> Iterator[Span]:
    with _default.span(name, **attributes) as s:
        yield s
