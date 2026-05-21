"""Metrics exporter abstraction with optional OTel / Prometheus backends.

The exporter is an additive layer on top of the existing internal counters
recorded via ``StateStore.increment_counter``. When neither backend is
enabled (or required dependencies are missing) the :class:`NoopExporter`
is returned and the rest of the system continues to use the SQLite-backed
counters unchanged.

Public surface:
    - :class:`MetricsExporter` — Protocol-ish abstract base.
    - :class:`NoopExporter`   — Default zero-cost implementation.
    - :func:`build_exporter`  — Factory selecting backend by config.

Metric naming convention is documented in ``docs/observability.md``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes_skill_guard.config import GuardConfig

__all__ = ["MetricsExporter", "NoopExporter", "build_exporter"]

_logger = logging.getLogger("hermes_skill_guard.observability")


class MetricsExporter:
    """Abstract base for metric backends.

    Subclasses implement OTel / Prometheus / etc. The default methods are
    no-ops so partial implementations remain safe; production backends
    should override the methods they support.
    """

    def record_counter(self, name: str, value: int = 1, **labels: Any) -> None:
        """Increment a monotonic counter by ``value``."""

    def record_histogram(self, name: str, value: float, **labels: Any) -> None:
        """Record an observation in a histogram (e.g., duration in ms)."""

    def record_gauge(self, name: str, value: float, **labels: Any) -> None:
        """Set the current value of a gauge."""

    def shutdown(self) -> None:
        """Flush and release backend resources (best-effort)."""


class NoopExporter(MetricsExporter):
    """No-op exporter used when observability is disabled."""


def build_exporter(config: GuardConfig) -> MetricsExporter:
    """Return the exporter implementation selected by config.

    Selection priority (any may fall back to :class:`NoopExporter` on
    import or runtime errors):

    1. ``observability.enabled = False`` -> ``NoopExporter``
    2. ``otel_enabled and prometheus_enabled`` -> a composite that
       multiplexes both backends. If both imports succeed, both export.
       If only one is importable, only that one exports.
    3. ``otel_enabled`` only -> :class:`OTelExporter` (or Noop on failure).
    4. ``prometheus_enabled`` only -> :class:`PrometheusExporter`
       (or Noop on failure).
    """
    if not config.observability.enabled:
        return NoopExporter()
    if not (config.observability.otel_enabled or config.observability.prometheus_enabled):
        return NoopExporter()

    backends: list[MetricsExporter] = []
    if config.observability.otel_enabled:
        backends.append(_safe_build_otel())
    if config.observability.prometheus_enabled:
        backends.append(_safe_build_prometheus(config.observability.prometheus_port))

    # Drop noops; if everything fell back, return a single Noop.
    real = [b for b in backends if not isinstance(b, NoopExporter)]
    if not real:
        return NoopExporter()
    if len(real) == 1:
        return real[0]
    return _CompositeExporter(real)


def _safe_build_otel() -> MetricsExporter:
    try:
        from hermes_skill_guard.observability.otel import OTelExporter

        return OTelExporter()
    except Exception as exc:  # pragma: no cover - defensive
        _logger.warning("otel exporter unavailable, falling back to noop: %s", exc)
        return NoopExporter()


def _safe_build_prometheus(port: int) -> MetricsExporter:
    try:
        from hermes_skill_guard.observability.prometheus import PrometheusExporter

        return PrometheusExporter(port=port)
    except Exception as exc:  # pragma: no cover - defensive
        _logger.warning("prometheus exporter unavailable, falling back to noop: %s", exc)
        return NoopExporter()


class _CompositeExporter(MetricsExporter):
    """Fan-out exporter dispatching to multiple backends."""

    def __init__(self, backends: list[MetricsExporter]) -> None:
        self._backends = backends

    def record_counter(self, name: str, value: int = 1, **labels: Any) -> None:
        for b in self._backends:
            b.record_counter(name, value, **labels)

    def record_histogram(self, name: str, value: float, **labels: Any) -> None:
        for b in self._backends:
            b.record_histogram(name, value, **labels)

    def record_gauge(self, name: str, value: float, **labels: Any) -> None:
        for b in self._backends:
            b.record_gauge(name, value, **labels)

    def shutdown(self) -> None:
        for b in self._backends:
            try:
                b.shutdown()
            except Exception:  # pragma: no cover - best-effort cleanup
                _logger.debug("exporter shutdown failed", exc_info=True)
