"""Rule engine — condition evaluation + decision derivation (T10.3).

Given a list of :class:`LoadedRule` and a :class:`RuleContext`, the engine
walks each rule's condition tree, collects the firing rule IDs, renders
their message templates against the context, and maps the maximum
severity to a :class:`DecisionValue`.

This module is intentionally self-contained: it does **not** call into
``policy.py`` or ``intents/preflight.py``. Wiring happens in T10.6.

Schema-level structural correctness (the shape of leaf and composite
nodes) is enforced by :mod:`hermes_skill_guard.rules.validator`, so the
engine focuses on evaluation rather than reparsing the tree.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from hermes_skill_guard.rules.context import RuleContext
from hermes_skill_guard.rules.loader import LoadedRule
from hermes_skill_guard.schemas import DecisionValue


class RuleEvaluationError(Exception):
    """Raised when a rule's condition tree cannot be evaluated.

    The loader/validator should prevent malformed conditions from reaching
    the engine; if one does (e.g. via a hand-built rule list in tests),
    we surface the failure rather than silently treating it as a miss.
    """


_SEVERITY_ORDER: dict[str, int] = {
    "info": 0,
    "warn": 1,
    "candidate": 2,
    "block": 3,
}


_SEVERITY_TO_DECISION: dict[str, DecisionValue] = {
    "info": DecisionValue.ALLOW,
    "warn": DecisionValue.WARN,
    "candidate": DecisionValue.CANDIDATE,
    "block": DecisionValue.BLOCK,
}


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """Outcome of evaluating a rule set against a context.

    Attributes:
        fired_rules: IDs of rules whose conditions matched, in evaluation
            order (which mirrors the loader's priority ordering).
        reasons: Rendered message templates from the firing rules, in the
            same order as :attr:`fired_rules`.
        severity: Highest severity seen across firing rules; defaults to
            ``"info"`` when no rule fires.
        decision: ``severity`` mapped through :data:`_SEVERITY_TO_DECISION`.
    """

    fired_rules: list[str]
    reasons: list[str]
    severity: str
    decision: DecisionValue


class RuleEngine:
    """Evaluate a list of :class:`LoadedRule` against a :class:`RuleContext`.

    The engine is stateless beyond the rule list, so callers may share one
    instance across many evaluations.
    """

    def __init__(self, rules: list[LoadedRule]) -> None:
        # Disabled rules are dropped at construction time so ``evaluate``
        # stays a tight loop.
        self._rules: list[LoadedRule] = [r for r in rules if r.enabled]

    @property
    def rules(self) -> list[LoadedRule]:
        """Return the active rule list (read-only view)."""
        return list(self._rules)

    def evaluate(self, ctx: RuleContext) -> EvaluationResult:
        """Walk every active rule and return the aggregated result."""
        fired_ids: list[str] = []
        reasons: list[str] = []
        max_severity = "info"

        # ``format_map`` requires a mapping; ``defaultdict(str)`` makes
        # missing placeholders render as empty strings instead of raising,
        # which keeps a template typo from breaking the whole pipeline.
        ctx_dict = asdict(ctx)
        ctx_safe: defaultdict[str, Any] = defaultdict(
            str,
            {k: ("" if v is None else v) for k, v in ctx_dict.items()},
        )

        for rule in self._rules:
            try:
                matched = self._eval_condition(rule.when, ctx)
            except RuleEvaluationError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                raise RuleEvaluationError(f"rule {rule.id} evaluation failed: {exc}") from exc
            if not matched:
                continue
            fired_ids.append(rule.id)
            try:
                rendered = rule.message_template.format_map(ctx_safe)
            except (ValueError, IndexError) as exc:
                # Bad format spec — surface rule ID so operators can fix it.
                raise RuleEvaluationError(
                    f"rule {rule.id} message template is invalid: {exc}"
                ) from exc
            reasons.append(rendered)
            if _SEVERITY_ORDER[rule.severity] > _SEVERITY_ORDER[max_severity]:
                max_severity = rule.severity

        return EvaluationResult(
            fired_rules=fired_ids,
            reasons=reasons,
            severity=max_severity,
            decision=_SEVERITY_TO_DECISION[max_severity],
        )

    # ------------------------------------------------------------------ #
    # Condition walker
    # ------------------------------------------------------------------ #

    def _eval_condition(self, node: Mapping[str, Any], ctx: RuleContext) -> bool:
        """Recursively evaluate a condition tree node."""
        if "and" in node:
            children = node["and"]
            if not isinstance(children, list):
                raise RuleEvaluationError(f"'and' must be a list, got {children!r}")
            return all(self._eval_condition(c, ctx) for c in children)
        if "or" in node:
            children = node["or"]
            if not isinstance(children, list):
                raise RuleEvaluationError(f"'or' must be a list, got {children!r}")
            return any(self._eval_condition(c, ctx) for c in children)
        if "not" in node:
            child = node["not"]
            if not isinstance(child, Mapping):
                raise RuleEvaluationError(f"'not' must wrap an object, got {child!r}")
            return not self._eval_condition(child, ctx)
        return self._eval_leaf(node, ctx)

    def _eval_leaf(self, node: Mapping[str, Any], ctx: RuleContext) -> bool:
        op = node.get("op")
        field = node.get("field")
        if not isinstance(op, str) or not isinstance(field, str):
            raise RuleEvaluationError(f"malformed leaf: {dict(node)!r}")

        actual = getattr(ctx, field, None)
        actual_str = "" if actual is None else str(actual)
        value = node.get("value")

        if op == "equals":
            return actual == value
        if op == "not_equals":
            return actual != value
        if op == "contains":
            return isinstance(value, str) and value in actual_str
        if op == "not_contains":
            return not (isinstance(value, str) and value in actual_str)
        if op == "matches":
            if not isinstance(value, str):
                return False
            try:
                return re.search(value, actual_str) is not None
            except re.error as exc:
                raise RuleEvaluationError(f"invalid regex in leaf {dict(node)!r}: {exc}") from exc
        if op == "missing":
            return actual is None or actual_str == ""
        if op == "present":
            return actual is not None and actual_str != ""
        if op == "length_less_than":
            return isinstance(value, int) and len(actual_str) < value
        if op == "length_greater_than":
            return isinstance(value, int) and len(actual_str) > value
        if op == "length_equals":
            return isinstance(value, int) and len(actual_str) == value
        raise RuleEvaluationError(f"unknown operator: {op!r}")


__all__ = [
    "EvaluationResult",
    "RuleEngine",
    "RuleEvaluationError",
]
