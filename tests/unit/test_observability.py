"""Unit tests for the observability metrics exporter layer.

Covers:
- NoopExporter records nothing but conforms to the protocol.
- build_exporter selects NoopExporter when observability is disabled.
- OTel and Prometheus exporters fall back to Noop when their optional
  dependencies cannot be imported.
- Preflight hook records a counter (and histogram) through the exporter.
- Timeout path records the dedicated timeout counter.

These tests must not require opentelemetry or prometheus_client to be
installed; everything else is exercised through mocks or fallback paths.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from hermes_skill_guard.config import (
    EnforcementConfig,
    GuardConfig,
    ObservabilityConfig,
)
from hermes_skill_guard.context import SkillGuardContext
from hermes_skill_guard.hermes.adapter import HermesAdapter
from hermes_skill_guard.observability import (
    MetricsExporter,
    NoopExporter,
    build_exporter,
)
from hermes_skill_guard.registry import register_intents
from hermes_skill_guard.runtime import TraceCache
from hermes_skill_guard.storage.repository import StateStore


def _make_context(
    tmp_path: Path,
    *,
    exporter: MetricsExporter | None = None,
    timeout_ms: int = 500,
    fail_open: bool = True,
    mode: str = "candidate",
) -> SkillGuardContext:
    config = GuardConfig(
        dry_run=False,
        state_dir=tmp_path,
        enforcement=EnforcementConfig(mode=mode, timeout_ms=timeout_ms, fail_open=fail_open),
    )
    ctx = SkillGuardContext(
        config=config,
        store=StateStore(config.state_db, config.events),
        trace_cache=TraceCache(config.trace_cache),
        logger=logging.getLogger("test.observability"),
        exporter=exporter or NoopExporter(),
    )
    return ctx


# ---------------------------------------------------------------------------
# Base / Noop exporter
# ---------------------------------------------------------------------------


def test_noop_exporter_records_nothing_but_conforms_to_protocol() -> None:
    exporter = NoopExporter()
    # All calls must be no-ops; we just ensure they don't raise.
    exporter.record_counter("hsg_x", 1, tool="t")
    exporter.record_histogram("hsg_y", 12.5, decision="allow")
    exporter.record_gauge("hsg_z", 3.14)
    exporter.shutdown()


def test_noop_exporter_is_metrics_exporter_instance() -> None:
    assert isinstance(NoopExporter(), MetricsExporter)


# ---------------------------------------------------------------------------
# build_exporter selection
# ---------------------------------------------------------------------------


def test_build_exporter_defaults_to_noop_when_disabled() -> None:
    config = GuardConfig(observability=ObservabilityConfig(enabled=False))
    exporter = build_exporter(config)
    assert isinstance(exporter, NoopExporter)


def test_build_exporter_returns_noop_if_both_backends_disabled() -> None:
    config = GuardConfig(
        observability=ObservabilityConfig(
            enabled=True, otel_enabled=False, prometheus_enabled=False
        )
    )
    exporter = build_exporter(config)
    assert isinstance(exporter, NoopExporter)


def test_otel_exporter_falls_back_to_noop_if_import_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If opentelemetry isn't importable, we should return NoopExporter."""
    # Force the import to fail at construction time.
    for mod in list(sys.modules):
        if mod.startswith("opentelemetry"):
            monkeypatch.setitem(sys.modules, mod, None)
    monkeypatch.setitem(sys.modules, "opentelemetry", None)
    monkeypatch.setitem(sys.modules, "opentelemetry.metrics", None)

    config = GuardConfig(observability=ObservabilityConfig(enabled=True, otel_enabled=True))
    exporter = build_exporter(config)
    assert isinstance(exporter, NoopExporter)


def test_prometheus_exporter_falls_back_to_noop_if_import_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If prometheus_client isn't importable, we should return NoopExporter."""
    monkeypatch.setitem(sys.modules, "prometheus_client", None)

    config = GuardConfig(observability=ObservabilityConfig(enabled=True, prometheus_enabled=True))
    exporter = build_exporter(config)
    # Should not start any HTTP server and must return Noop on missing dep.
    assert isinstance(exporter, NoopExporter)


# ---------------------------------------------------------------------------
# Integration with preflight hook
# ---------------------------------------------------------------------------


def test_preflight_hook_records_counter(tmp_path: Path, fake_ctx: Any) -> None:
    """A pre_tool_call invocation should record the hsg_pre_tool_call_total counter."""
    exporter = MagicMock(spec=MetricsExporter)
    # The MagicMock methods need to exist; spec gives us record_counter etc.
    ctx = _make_context(tmp_path, exporter=exporter)
    register_intents(HermesAdapter(fake_ctx), ctx)

    result = fake_ctx.invoke_hook(
        "pre_tool_call",
        tool_name="read_file",
        args={"path": "x"},
        tool_call_id="counter-call",
    )
    assert result is None

    counter_names = [c.args[0] for c in exporter.record_counter.call_args_list]
    assert "hsg_pre_tool_call_total" in counter_names
    # Histogram should be recorded too.
    histogram_names = [c.args[0] for c in exporter.record_histogram.call_args_list]
    assert "hsg_pre_tool_call_duration_ms" in histogram_names


def test_timeout_records_dedicated_counter(
    tmp_path: Path,
    fake_ctx: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timeout path must record hsg_preflight_timeout_total via the exporter."""
    exporter = MagicMock(spec=MetricsExporter)
    ctx = _make_context(tmp_path, exporter=exporter, timeout_ms=30, fail_open=True)

    from hermes_skill_guard.policy import PreflightPolicy

    def _slow(self: Any, call: Any) -> Any:  # pragma: no cover - executed in worker
        time.sleep(0.3)
        raise AssertionError("should be interrupted by timeout")

    monkeypatch.setattr(PreflightPolicy, "evaluate", _slow)
    register_intents(HermesAdapter(fake_ctx), ctx)

    result = fake_ctx.invoke_hook(
        "pre_tool_call",
        tool_name="skill_manage",
        args={"action": "create", "name": "x", "content": "y"},
        tool_call_id="timeout-call",
    )
    assert result is None

    counter_names = [c.args[0] for c in exporter.record_counter.call_args_list]
    assert "hsg_preflight_timeout_total" in counter_names


# ---------------------------------------------------------------------------
# ObservabilityConfig env-var parsing
# ---------------------------------------------------------------------------


def test_config_env_vars_enable_observability(monkeypatch: pytest.MonkeyPatch) -> None:
    from hermes_skill_guard.config import load_config

    # Clear all HSG_ env vars first
    for key in list(__import__("os").environ):
        if key.startswith(("HSG_", "SKILL_GUARD_")):
            monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("HSG_OTEL_ENABLED", "true")
    monkeypatch.setenv("HSG_PROMETHEUS_ENABLED", "true")
    monkeypatch.setenv("HSG_PROMETHEUS_PORT", "9300")
    config = load_config()
    assert config.observability.otel_enabled is True
    assert config.observability.prometheus_enabled is True
    assert config.observability.prometheus_port == 9300
    assert config.observability.enabled is True
