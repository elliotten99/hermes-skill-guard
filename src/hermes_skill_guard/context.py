"""Shared runtime context for intents and handlers."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from hermes_skill_guard.config import GuardConfig
from hermes_skill_guard.observability import MetricsExporter, NoopExporter
from hermes_skill_guard.runtime import TraceCache
from hermes_skill_guard.storage.repository import StateStore


@dataclass(frozen=True, slots=True)
class SkillGuardContext:
    config: GuardConfig
    store: StateStore
    trace_cache: TraceCache
    logger: logging.Logger
    exporter: MetricsExporter = field(default_factory=NoopExporter)
