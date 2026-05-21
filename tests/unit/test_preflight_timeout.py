"""Tests that EnforcementConfig.timeout_ms is actively enforced by the hook."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import pytest

from hermes_skill_guard.config import EnforcementConfig, GuardConfig
from hermes_skill_guard.context import SkillGuardContext
from hermes_skill_guard.hermes.adapter import HermesAdapter
from hermes_skill_guard.registry import register_intents
from hermes_skill_guard.runtime import TraceCache
from hermes_skill_guard.storage.repository import StateStore


def _counter_value(ctx: SkillGuardContext, name: str) -> int:
    counters = ctx.store.summary().get("counters", {})
    assert isinstance(counters, dict)
    return int(counters.get(name, 0))


def _make_context(
    tmp_path: Path,
    *,
    timeout_ms: int,
    fail_open: bool,
    mode: str = "audit",
) -> SkillGuardContext:
    config = GuardConfig(
        dry_run=False,
        state_dir=tmp_path,
        enforcement=EnforcementConfig(mode=mode, timeout_ms=timeout_ms, fail_open=fail_open),
    )
    return SkillGuardContext(
        config=config,
        store=StateStore(config.state_db, config.events),
        trace_cache=TraceCache(config.trace_cache),
        logger=logging.getLogger("test.preflight_timeout"),
    )


def _slow_evaluate_factory(delay_s: float) -> Any:
    """Return a fake `policy.evaluate` that sleeps before returning."""

    def _slow(self: Any, call: Any) -> Any:  # pragma: no cover - executed in worker
        time.sleep(delay_s)
        raise AssertionError("slow_evaluate should be interrupted by timeout")

    return _slow


def test_normal_evaluation_within_timeout_returns_normal_decision(
    tmp_path: Path, fake_ctx: Any
) -> None:
    """Fast evaluation should complete and not trip the timeout path."""
    ctx = _make_context(tmp_path, timeout_ms=500, fail_open=True, mode="audit")
    register_intents(HermesAdapter(fake_ctx), ctx)

    # A non-skill-manage tool returns ALLOW quickly => hook returns None.
    result = fake_ctx.invoke_hook(
        "pre_tool_call",
        tool_name="read_file",
        args={"path": "x"},
        tool_call_id="fast-call",
    )

    assert result is None
    assert _counter_value(ctx, "preflight_timeout_count") == 0


def test_timeout_fail_open_returns_none_and_increments_counter(
    tmp_path: Path,
    fake_ctx: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When fail_open=True and evaluate exceeds timeout_ms, hook must return None."""
    ctx = _make_context(tmp_path, timeout_ms=50, fail_open=True, mode="candidate")

    # Replace evaluate on PreflightPolicy with a slow stub before registration
    from hermes_skill_guard.policy import PreflightPolicy

    monkeypatch.setattr(PreflightPolicy, "evaluate", _slow_evaluate_factory(0.5))

    register_intents(HermesAdapter(fake_ctx), ctx)

    caplog.set_level(logging.WARNING)

    result = fake_ctx.invoke_hook(
        "pre_tool_call",
        tool_name="skill_manage",
        args={"action": "create", "name": "x", "content": "y"},
        tool_call_id="timeout-call",
    )

    assert result is None, "fail_open=true should allow the call through on timeout"
    assert _counter_value(ctx, "preflight_timeout_count") == 1
    messages = [r.getMessage() for r in caplog.records]
    assert any("exceeded timeout" in m.lower() for m in messages), (
        f"expected a warning log mentioning 'exceeded timeout', got: {messages}"
    )


def test_timeout_fail_closed_returns_block_action(
    tmp_path: Path,
    fake_ctx: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When fail_open=False and evaluate exceeds timeout_ms, hook must block."""
    ctx = _make_context(tmp_path, timeout_ms=50, fail_open=False, mode="candidate")

    from hermes_skill_guard.policy import PreflightPolicy

    monkeypatch.setattr(PreflightPolicy, "evaluate", _slow_evaluate_factory(0.5))

    register_intents(HermesAdapter(fake_ctx), ctx)

    result = fake_ctx.invoke_hook(
        "pre_tool_call",
        tool_name="skill_manage",
        args={"action": "create", "name": "x", "content": "y"},
        tool_call_id="timeout-block",
    )

    assert isinstance(result, dict)
    assert result.get("action") == "block"
    assert "timed out" in result.get("message", "").lower()
    assert _counter_value(ctx, "preflight_timeout_count") == 1


def test_counter_increments_on_each_timeout(
    tmp_path: Path,
    fake_ctx: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated timeouts should accumulate the counter."""
    ctx = _make_context(tmp_path, timeout_ms=30, fail_open=True, mode="candidate")

    from hermes_skill_guard.policy import PreflightPolicy

    monkeypatch.setattr(PreflightPolicy, "evaluate", _slow_evaluate_factory(0.3))

    register_intents(HermesAdapter(fake_ctx), ctx)

    for i in range(3):
        result = fake_ctx.invoke_hook(
            "pre_tool_call",
            tool_name="skill_manage",
            args={"action": "create", "name": f"x{i}", "content": "y"},
            tool_call_id=f"timeout-call-{i}",
        )
        assert result is None

    assert _counter_value(ctx, "preflight_timeout_count") == 3
