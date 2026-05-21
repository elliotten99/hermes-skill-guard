"""Deterministic preflight policy for skill creation.

T10.6 migrated the configurable rule checks (name_missing, plugin_namespace,
description_too_short, secret_pattern) from hard-coded Python to the
:class:`hermes_skill_guard.rules.engine.RuleEngine`.  Boundary checks
(tool_name, operation, promotion_attempt_id) remain hard-coded because they
are short-circuit guards that never produce a candidate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hermes_skill_guard.config import GuardConfig
from hermes_skill_guard.ids import new_event_id, new_trace_id
from hermes_skill_guard.redaction import stable_hash
from hermes_skill_guard.rules import RuleContext, RuleEngine, RuleLoader
from hermes_skill_guard.schemas import Confidence, Decision, DecisionValue, EnforcementMode


@dataclass(frozen=True, slots=True)
class ToolCall:
    tool_name: str
    args: dict[str, Any]


def _operation(args: dict[str, Any]) -> str | None:
    for key in ("action", "operation", "op", "command"):
        value = args.get(key)
        if isinstance(value, str):
            return value.lower()
    return None


class PreflightPolicy:
    """Deterministic rules for ``skill_manage create``.

    The policy evaluates a :class:`ToolCall` in three stages:

    1. **Hard-coded boundary guards** — short-circuit ``ALLOW`` when the call
       is outside the skill-guard perimeter (wrong tool, wrong operation,
       authorised promotion).
    2. **Configurable rule engine** — evaluate the loaded rule set against the
       call context.  This covers naming, content length, secret patterns, etc.
    3. **Enforcement mapping** — translate the highest rule severity into a
       :class:`DecisionValue`, honouring ``enforcement.mode`` and ``dry_run``.
    """

    def __init__(self, config: GuardConfig) -> None:
        self.config = config
        self._engine = RuleEngine(RuleLoader(config).load())

    def evaluate(self, call: ToolCall) -> Decision:
        event_id = new_event_id()
        trace_id = new_trace_id()

        # ------------------------------------------------------------------
        # Stage 1 — hard-coded short-circuit guards
        # ------------------------------------------------------------------
        if call.tool_name != "skill_manage":
            return self._decision(
                DecisionValue.ALLOW,
                Confidence.LOW,
                ["tool is outside skill guard boundary"],
                ["boundary.tool_not_skill_manage"],
                event_id,
                trace_id,
                call.tool_name,
                None,
            )

        if _operation(call.args) != "create":
            return self._decision(
                DecisionValue.ALLOW,
                Confidence.LOW,
                ["skill_manage operation is not create"],
                ["boundary.operation_not_create"],
                event_id,
                trace_id,
                call.tool_name,
                None,
            )

        if isinstance(call.args.get("skill_guard_promotion_attempt_id"), str):
            return self._decision(
                DecisionValue.ALLOW,
                Confidence.MEDIUM,
                ["skill creation is tied to an approved skill-guard promotion attempt"],
                ["lifecycle.authorized_promotion"],
                event_id,
                trace_id,
                call.tool_name,
                None,
            )

        # ------------------------------------------------------------------
        # Stage 2 — configurable rule engine (T10.6)
        # ------------------------------------------------------------------
        ctx = RuleContext.from_tool_call(call.tool_name, call.args, self.config)
        result = self._engine.evaluate(ctx)

        reasons = list(result.reasons)
        rule_ids = list(result.fired_rules)

        if not rule_ids:
            return self._decision(
                DecisionValue.ALLOW,
                Confidence.MEDIUM,
                ["deterministic rules found no v0.1 issue"],
                ["lifecycle.allow_static"],
                event_id,
                trace_id,
                call.tool_name,
                ctx.skill_name,
            )

        # ------------------------------------------------------------------
        # Stage 3 — enforcement mode mapping + dry_run downgrade
        # ------------------------------------------------------------------
        desired = _map_severity_to_decision(result.severity)
        mode = self.config.enforcement.mode

        if mode == EnforcementMode.CANDIDATE.value and desired == DecisionValue.WARN:
            desired = DecisionValue.CANDIDATE
        elif mode == EnforcementMode.BLOCK.value and desired in (
            DecisionValue.WARN,
            DecisionValue.CANDIDATE,
        ):
            desired = DecisionValue.BLOCK

        if self.config.dry_run:
            desired = DecisionValue.WARN
            reasons.append("dry_run=true downgraded enforcement decision to warn")
            rule_ids.append("lifecycle.dry_run_downgrade")

        return self._decision(
            desired,
            Confidence.HIGH,
            reasons,
            rule_ids,
            event_id,
            trace_id,
            call.tool_name,
            ctx.skill_name,
        )

    def _decision(
        self,
        decision: DecisionValue,
        confidence: Confidence,
        reasons: list[str],
        rule_ids: list[str],
        event_id: str,
        trace_id: str,
        tool_name: str,
        skill_name: str | None,
    ) -> Decision:
        mode = EnforcementMode(self.config.enforcement.mode)
        return Decision(
            decision=decision,
            confidence=confidence,
            reasons=reasons,
            rule_ids=rule_ids,
            event_id=event_id,
            trace_id=trace_id,
            tool_name=tool_name,
            skill_name=skill_name,
            dry_run=self.config.dry_run,
            enforcement_mode=mode,
        )


def _map_severity_to_decision(severity: str) -> DecisionValue:
    return {
        "info": DecisionValue.ALLOW,
        "warn": DecisionValue.WARN,
        "candidate": DecisionValue.CANDIDATE,
        "block": DecisionValue.BLOCK,
    }.get(severity, DecisionValue.WARN)


def content_hash(content: str) -> str:
    return stable_hash(content.strip())
