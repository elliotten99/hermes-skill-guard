"""Prometheus-backed metrics exporter.

If ``port > 0`` is supplied at construction time, an embedded HTTP server
is started via :func:`prometheus_client.start_http_server`. Otherwise the
metrics are recorded into the default registry and callers can expose
them out-of-band (e.g., through their own ASGI / WSGI integration).

If ``prometheus_client`` is not installed, constructing
:class:`PrometheusExporter` raises ``ImportError``; the higher-level
:func:`build_exporter` factory catches that to fall back to
:class:`NoopExporter`.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from hermes_skill_guard.observability import MetricsExporter

_logger = logging.getLogger("hermes_skill_guard.observability.prometheus")


class PrometheusExporter(MetricsExporter):
    """Prometheus metrics exporter.

    Metrics are lazily created and cached per name. Labels passed via
    ``**labels`` are sorted to derive a stable label-key set for the
    underlying Counter / Histogram / Gauge family.
    """

    def __init__(self, port: int = 0) -> None:
        try:
            from prometheus_client import (
                CONTENT_TYPE_LATEST,  # noqa: F401
                Counter,
                Gauge,
                Histogram,
                start_http_server,
            )
        except ImportError as exc:  # pragma: no cover - exercised by fallback tests
            raise ImportError(
                "prometheus_client not installed. "
                "Install with `pip install hermes-skill-guard[prometheus]`."
            ) from exc

        self._Counter = Counter
        self._Histogram = Histogram
        self._Gauge = Gauge
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[str, ...]], Any] = {}
        self._histograms: dict[tuple[str, tuple[str, ...]], Any] = {}
        self._gauges: dict[tuple[str, tuple[str, ...]], Any] = {}
        self._port = port
        if port > 0:
            try:
                start_http_server(port)
                _logger.info("prometheus HTTP exporter listening on :%d", port)
            except Exception:  # pragma: no cover - port-in-use should not break startup
                _logger.warning("could not bind prometheus port %d", port, exc_info=True)

    @staticmethod
    def _key(name: str, labels: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
        return (name, tuple(sorted(labels.keys())))

    def _get_counter(self, name: str, labels: dict[str, Any]) -> Any:
        key = self._key(name, labels)
        with self._lock:
            metric = self._counters.get(key)
            if metric is None:
                metric = self._Counter(name, name.replace("_", " "), labelnames=list(key[1]))
                self._counters[key] = metric
        return metric

    def _get_histogram(self, name: str, labels: dict[str, Any]) -> Any:
        key = self._key(name, labels)
        with self._lock:
            metric = self._histograms.get(key)
            if metric is None:
                metric = self._Histogram(name, name.replace("_", " "), labelnames=list(key[1]))
                self._histograms[key] = metric
        return metric

    def _get_gauge(self, name: str, labels: dict[str, Any]) -> Any:
        key = self._key(name, labels)
        with self._lock:
            metric = self._gauges.get(key)
            if metric is None:
                metric = self._Gauge(name, name.replace("_", " "), labelnames=list(key[1]))
                self._gauges[key] = metric
        return metric

    def record_counter(self, name: str, value: int = 1, **labels: Any) -> None:
        try:
            metric = self._get_counter(name, labels)
            if labels:
                metric.labels(**{k: str(v) for k, v in labels.items()}).inc(value)
            else:
                metric.inc(value)
        except Exception:  # pragma: no cover - exporter must never raise
            _logger.debug("prometheus counter failed", exc_info=True)

    def record_histogram(self, name: str, value: float, **labels: Any) -> None:
        try:
            metric = self._get_histogram(name, labels)
            if labels:
                metric.labels(**{k: str(v) for k, v in labels.items()}).observe(value)
            else:
                metric.observe(value)
        except Exception:  # pragma: no cover
            _logger.debug("prometheus histogram failed", exc_info=True)

    def record_gauge(self, name: str, value: float, **labels: Any) -> None:
        try:
            metric = self._get_gauge(name, labels)
            if labels:
                metric.labels(**{k: str(v) for k, v in labels.items()}).set(value)
            else:
                metric.set(value)
        except Exception:  # pragma: no cover
            _logger.debug("prometheus gauge failed", exc_info=True)

    def shutdown(self) -> None:
        # prometheus_client's default HTTP server has no clean shutdown
        # hook; closing it would require tracking the server thread.
        # The metrics themselves live in the default registry.
        return None
