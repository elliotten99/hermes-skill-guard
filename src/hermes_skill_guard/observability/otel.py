"""OpenTelemetry-backed metrics exporter.

Importing this module does **not** start any exporter; it merely loads the
SDK. Construction of :class:`OTelExporter` initializes a MeterProvider that
honors ``OTEL_EXPORTER_OTLP_ENDPOINT`` (and the standard OTLP env vars)
via the official OTLP/HTTP exporter.

If the OTel SDK is not installed, instantiating :class:`OTelExporter`
raises ``ImportError`` and the higher-level :func:`build_exporter`
factory catches that to fall back to :class:`NoopExporter`.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from hermes_skill_guard.observability import MetricsExporter

_logger = logging.getLogger("hermes_skill_guard.observability.otel")


class OTelExporter(MetricsExporter):
    """OpenTelemetry metrics exporter using OTLP/HTTP.

    Counters, histograms, and gauges are lazily created and cached per
    metric name. ``shutdown()`` flushes pending metrics.
    """

    def __init__(self) -> None:
        try:
            from opentelemetry import metrics
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        except ImportError as exc:  # pragma: no cover - exercised by fallback tests
            raise ImportError(
                "opentelemetry SDK + OTLP exporter not installed. "
                "Install with `pip install hermes-skill-guard[otel]`."
            ) from exc

        reader = PeriodicExportingMetricReader(OTLPMetricExporter())
        provider = MeterProvider(metric_readers=[reader])
        metrics.set_meter_provider(provider)
        self._provider = provider
        self._meter = metrics.get_meter("hermes_skill_guard", "0.1.11")
        self._lock = threading.Lock()
        self._counters: dict[str, Any] = {}
        self._histograms: dict[str, Any] = {}
        self._gauges: dict[str, float] = {}
        self._observable_gauges: dict[str, Any] = {}

    def _get_counter(self, name: str) -> Any:
        with self._lock:
            counter = self._counters.get(name)
            if counter is None:
                counter = self._meter.create_counter(name=name, description=name, unit="1")
                self._counters[name] = counter
        return counter

    def _get_histogram(self, name: str) -> Any:
        with self._lock:
            hist = self._histograms.get(name)
            if hist is None:
                hist = self._meter.create_histogram(name=name, description=name, unit="ms")
                self._histograms[name] = hist
        return hist

    def _ensure_observable_gauge(self, name: str) -> None:
        with self._lock:
            if name in self._observable_gauges:
                return

            def _callback(options: Any) -> Any:
                from opentelemetry.metrics import Observation

                value = self._gauges.get(name, 0.0)
                yield Observation(value, {})

            self._observable_gauges[name] = self._meter.create_observable_gauge(
                name=name, description=name, callbacks=[_callback]
            )

    def record_counter(self, name: str, value: int = 1, **labels: Any) -> None:
        try:
            self._get_counter(name).add(value, attributes=_to_attrs(labels))
        except Exception:  # pragma: no cover - exporter must never raise
            _logger.debug("otel counter failed", exc_info=True)

    def record_histogram(self, name: str, value: float, **labels: Any) -> None:
        try:
            self._get_histogram(name).record(value, attributes=_to_attrs(labels))
        except Exception:  # pragma: no cover - exporter must never raise
            _logger.debug("otel histogram failed", exc_info=True)

    def record_gauge(self, name: str, value: float, **labels: Any) -> None:
        # OTel async gauges report via callback; we publish via observable.
        try:
            self._gauges[name] = float(value)
            self._ensure_observable_gauge(name)
        except Exception:  # pragma: no cover
            _logger.debug("otel gauge failed", exc_info=True)

    def shutdown(self) -> None:
        try:
            self._provider.shutdown()
        except Exception:  # pragma: no cover - best-effort cleanup
            _logger.debug("otel shutdown failed", exc_info=True)


def _to_attrs(labels: dict[str, Any]) -> dict[str, Any]:
    """Convert label kwargs into OTel attribute dict (str-keyed scalars)."""
    return {k: v for k, v in labels.items() if v is not None}
