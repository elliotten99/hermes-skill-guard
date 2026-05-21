from __future__ import annotations

from hermes_skill_guard.config import GuardConfig, TraceCacheConfig
from hermes_skill_guard.policy import PreflightPolicy, ToolCall
from hermes_skill_guard.runtime import TraceCache


def test_trace_cache_round_trip() -> None:
    cache = TraceCache(TraceCacheConfig(ttl_minutes=10, max_entries=10))
    decision = PreflightPolicy(GuardConfig()).evaluate(ToolCall("read_file", {}))

    cache.put(decision)

    assert cache.get(decision.trace_id) == decision
    assert cache.pop(decision.trace_id) == decision
    assert cache.get(decision.trace_id) is None
