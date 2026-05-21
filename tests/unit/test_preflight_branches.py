"""Branch-coverage tests for preflight.py uncovered paths.

Targets lines:
- 57   : _extract_tool_call -> non-dict args fallback
- 78-80: tool_handler exception path
- 129-136: _persist_preflight_decision raises -> persist_failed counter + fail open/closed
- 145-153: outer pre_tool_call exception path (fail open / closed)
- 181  : _persist_preflight_decision -> non-dict args fallback
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from hermes_skill_guard.config import EnforcementConfig, GuardConfig
from hermes_skill_guard.context import SkillGuardContext
from hermes_skill_guard.hermes.adapter import HermesAdapter
from hermes_skill_guard.intents.preflight import _extract_tool_call
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
    mode: str = "candidate",
    fail_open: bool = True,
    dry_run: bool = False,
) -> SkillGuardContext:
    config = GuardConfig(
        dry_run=dry_run,
        state_dir=tmp_path,
        enforcement=EnforcementConfig(mode=mode, fail_open=fail_open),
    )
    return SkillGuardContext(
        config=config,
        store=StateStore(config.state_db, config.events),
        trace_cache=TraceCache(config.trace_cache),
        logger=logging.getLogger("test.preflight_branches"),
    )


# ---------------------------------------------------------------------------
# Line 57: non-dict tool_args coerced to {}
# Line 181: non-dict args in _persist_preflight_decision coerced to {}
# ---------------------------------------------------------------------------


def test_extract_tool_call_non_dict_args_falls_back_to_empty_dict() -> None:
    """L57: a non-dict ``args`` value must not propagate; coerce to {}."""
    call = _extract_tool_call({"tool_name": "skill_manage", "args": "not-a-dict"})
    assert call.tool_name == "skill_manage"
    assert call.args == {}


def test_persist_branch_handles_non_dict_args_in_hook_kwargs(
    tmp_path: Path,
    fake_ctx: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L181: when hook_kwargs['args'] is not a dict the persist branch must
    still execute (args defaults to {} inside _persist_preflight_decision).

    The outer ``_extract_tool_call`` already coerces non-dict args to {},
    so a non-dict ``args`` alone yields an ALLOW decision and never reaches
    the persistence branch. We force a CANDIDATE decision by patching
    ``policy.evaluate`` and then pass a non-dict ``args`` to drive line 181.
    """
    ctx = _make_context(tmp_path, mode="candidate")

    from hermes_skill_guard.ids import new_event_id, new_trace_id
    from hermes_skill_guard.policy import PreflightPolicy
    from hermes_skill_guard.schemas import (
        Confidence,
        Decision,
        DecisionValue,
        EnforcementMode,
    )

    def _force_candidate(self: Any, call: Any) -> Decision:
        return Decision(
            decision=DecisionValue.CANDIDATE,
            confidence=Confidence.HIGH,
            reasons=["forced for branch coverage"],
            rule_ids=["test.forced"],
            event_id=new_event_id(),
            trace_id=new_trace_id(),
            tool_name=call.tool_name,
            skill_name="forced-candidate",
            dry_run=False,
            enforcement_mode=EnforcementMode.CANDIDATE,
        )

    monkeypatch.setattr(PreflightPolicy, "evaluate", _force_candidate)

    register_intents(HermesAdapter(fake_ctx), ctx)

    result = fake_ctx.invoke_hook(
        "pre_tool_call",
        tool_name="skill_manage",
        args=["not", "a", "dict"],  # truthy non-dict to hit L181 fallback
        tool_call_id="branch-call",
    )

    assert isinstance(result, dict)
    assert result.get("action") == "block"
    # Candidate row should be present because we forced a CANDIDATE decision.
    candidates = ctx.store.list_candidates()
    assert len(candidates) == 1
    assert candidates[0]["name"] == "forced-candidate"


# ---------------------------------------------------------------------------
# Lines 78-80: tool_handler exception path
# ---------------------------------------------------------------------------


def test_tool_handler_catches_exception_and_returns_error_envelope(
    tmp_path: Path,
    fake_ctx: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L78-80: tool_handler must catch exceptions, bump counter, return JSON."""
    ctx = _make_context(tmp_path, mode="audit")

    from hermes_skill_guard.policy import PreflightPolicy

    def _boom(self: Any, call: Any) -> Any:
        raise RuntimeError("policy exploded")

    monkeypatch.setattr(PreflightPolicy, "evaluate", _boom)

    register_intents(HermesAdapter(fake_ctx), ctx)

    handler = fake_ctx.tools["skill_guard_preflight"]["handler"]
    payload = handler(args={"tool_name": "skill_manage", "args": {"action": "create"}})

    decoded = json.loads(payload)
    assert decoded == {"ok": False, "error": "RuntimeError"}
    assert _counter_value(ctx, "preflight_tool_failed") == 1


def test_tool_handler_happy_path_returns_decision(tmp_path: Path, fake_ctx: Any) -> None:
    """Sanity check that the success branch still returns ok=True."""
    ctx = _make_context(tmp_path, mode="audit")
    register_intents(HermesAdapter(fake_ctx), ctx)
    handler = fake_ctx.tools["skill_guard_preflight"]["handler"]
    payload = handler(args={"tool_name": "read_file", "args": {"path": "x"}})
    decoded = json.loads(payload)
    assert decoded["ok"] is True
    assert "decision" in decoded


# ---------------------------------------------------------------------------
# Lines 129-136: _persist_preflight_decision raises -> persist_failed counters
# ---------------------------------------------------------------------------


def test_persist_failure_fail_open_returns_none(
    tmp_path: Path,
    fake_ctx: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L129-135: when persistence raises and fail_open=True hook returns None."""
    ctx = _make_context(tmp_path, mode="candidate", fail_open=True)

    # Patch store.record_event to raise so the inner try fails.
    def _bad_record(self: Any, event: Any) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(StateStore, "record_event", _bad_record)

    register_intents(HermesAdapter(fake_ctx), ctx)

    result = fake_ctx.invoke_hook(
        "pre_tool_call",
        tool_name="skill_manage",
        args={"action": "create", "name": "persist-fail", "content": "short"},
        tool_call_id="persist-open",
    )

    assert result is None
    assert _counter_value(ctx, "preflight_persist_failed") == 1
    assert _counter_value(ctx, "preflight_persist_failed:RuntimeError") == 1


def test_persist_failure_fail_closed_returns_block(
    tmp_path: Path,
    fake_ctx: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L136-139: when persistence raises and fail_open=False hook blocks."""
    ctx = _make_context(tmp_path, mode="candidate", fail_open=False)

    def _bad_record(self: Any, event: Any) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(StateStore, "record_event", _bad_record)

    register_intents(HermesAdapter(fake_ctx), ctx)

    result = fake_ctx.invoke_hook(
        "pre_tool_call",
        tool_name="skill_manage",
        args={"action": "create", "name": "persist-fail", "content": "short"},
        tool_call_id="persist-closed",
    )

    assert isinstance(result, dict)
    assert result.get("action") == "block"
    assert "could not persist" in result.get("message", "")
    assert _counter_value(ctx, "preflight_persist_failed") == 1


# ---------------------------------------------------------------------------
# Lines 145-153: outer try/except in pre_tool_call (non-timeout failure)
# ---------------------------------------------------------------------------


def test_outer_hook_failure_fail_open_returns_none(
    tmp_path: Path,
    fake_ctx: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L145-147, 153: unexpected exception in pre hook -> fail-open returns None.

    Exception is raised from trace_cache.put which lives AFTER the
    timeout handling block so it falls into the outer except.
    """
    ctx = _make_context(tmp_path, mode="audit", fail_open=True)

    def _explode(self: Any, decision: Any) -> None:
        raise RuntimeError("cache exploded")

    monkeypatch.setattr(TraceCache, "put", _explode)

    register_intents(HermesAdapter(fake_ctx), ctx)

    result = fake_ctx.invoke_hook(
        "pre_tool_call",
        tool_name="skill_manage",
        args={"action": "create", "name": "outer", "content": "ok content"},
        tool_call_id="outer-open",
    )

    assert result is None
    assert _counter_value(ctx, "fail_open_count") == 1
    assert _counter_value(ctx, "pre_tool_call_failed:RuntimeError") == 1


def test_outer_hook_failure_fail_closed_returns_block(
    tmp_path: Path,
    fake_ctx: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L148-152: unexpected exception with fail_open=False -> block envelope."""
    ctx = _make_context(tmp_path, mode="audit", fail_open=False)

    def _explode(self: Any, decision: Any) -> None:
        raise RuntimeError("cache exploded")

    monkeypatch.setattr(TraceCache, "put", _explode)

    register_intents(HermesAdapter(fake_ctx), ctx)

    result = fake_ctx.invoke_hook(
        "pre_tool_call",
        tool_name="skill_manage",
        args={"action": "create", "name": "outer", "content": "ok content"},
        tool_call_id="outer-closed",
    )

    assert isinstance(result, dict)
    assert result.get("action") == "block"
    assert result.get("message") == "skill-guard preflight failed"
    assert _counter_value(ctx, "fail_open_count") == 1
