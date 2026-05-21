"""Runtime helpers that keep hooks fast and fail-open."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic

from hermes_skill_guard.config import TraceCacheConfig
from hermes_skill_guard.schemas import Decision


@dataclass(slots=True)
class _TraceEntry:
    decision: Decision
    created_at: float


class TraceCache:
    """Bounded in-memory decision cache linking pre and post hooks."""

    def __init__(self, config: TraceCacheConfig) -> None:
        self.ttl_seconds = config.ttl_minutes * 60
        self.max_entries = config.max_entries
        self._entries: OrderedDict[str, _TraceEntry] = OrderedDict()
        self.evicted_count = 0
        self.miss_count = 0

    def put(self, decision: Decision) -> None:
        self._evict_expired()
        self._entries[decision.trace_id] = _TraceEntry(decision=decision, created_at=monotonic())
        self._entries.move_to_end(decision.trace_id)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
            self.evicted_count += 1

    def get(self, trace_id: str) -> Decision | None:
        self._evict_expired()
        entry = self._entries.get(trace_id)
        if entry is None:
            self.miss_count += 1
            return None
        self._entries.move_to_end(trace_id)
        return entry.decision

    def pop(self, trace_id: str) -> Decision | None:
        self._evict_expired()
        entry = self._entries.pop(trace_id, None)
        if entry is None:
            self.miss_count += 1
            return None
        return entry.decision

    def _evict_expired(self) -> None:
        now = monotonic()
        expired = [
            key for key, entry in self._entries.items() if now - entry.created_at > self.ttl_seconds
        ]
        for key in expired:
            self._entries.pop(key, None)
            self.evicted_count += 1
