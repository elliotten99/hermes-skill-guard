"""Configurable rule engine package (T10).

T10.2 exposed the loader; T10.3 adds the runtime engine and context.
Wiring into ``PreflightPolicy`` happens in T10.6.
"""

from __future__ import annotations

from hermes_skill_guard.rules.context import RuleContext
from hermes_skill_guard.rules.engine import (
    EvaluationResult,
    RuleEngine,
    RuleEvaluationError,
)
from hermes_skill_guard.rules.loader import LoadedRule, RuleLoader, RuleLoadError
from hermes_skill_guard.rules.validator import SchemaError, validate

__all__ = [
    "EvaluationResult",
    "LoadedRule",
    "RuleContext",
    "RuleEngine",
    "RuleEvaluationError",
    "RuleLoadError",
    "RuleLoader",
    "SchemaError",
    "validate",
]
