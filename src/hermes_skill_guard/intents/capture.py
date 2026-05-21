"""Post-tool capture intent."""

from __future__ import annotations

import json
from typing import Any

from hermes_skill_guard.context import SkillGuardContext
from hermes_skill_guard.hermes.adapter import HermesAdapter
from hermes_skill_guard.ids import new_candidate_id, new_event_id, new_trace_id
from hermes_skill_guard.intents._extractors import (
    extract_content,
    extract_description,
    extract_skill_name,
)
from hermes_skill_guard.policy import content_hash
from hermes_skill_guard.redaction import Redactor
from hermes_skill_guard.schemas import Candidate, CandidateStatus, EventRecord


class CaptureIntent:
    intent_id = "capture"
    priority = 10

    def register(self, adapter: HermesAdapter, context: SkillGuardContext) -> None:
        redactor = Redactor(
            capture_raw_payloads=context.config.logging.capture_raw_payloads,
            max_field_length=context.config.logging.max_field_length,
            hash_redacted_values=context.config.logging.hash_redacted_values,
        )

        def post_tool_call(**hook_kwargs: object) -> None:
            """Capture and persist a tool call result.

            Hermes invokes post_tool_call with kwargs:
            tool_name, args, result, task_id, session_id, tool_call_id, duration_ms.
            """
            try:
                data = dict(hook_kwargs)
                tool_name = str(data.get("tool_name") or data.get("name") or "")
                tool_call_id = str(data.get("tool_call_id") or "")
                result = data.get("result")
                duration_ms = data.get("duration_ms")

                # Correlate with the pre_tool_call decision via tool_call_id
                decision = context.trace_cache.pop(tool_call_id) if tool_call_id else None
                if decision is None and tool_call_id:
                    context.store.increment_counter("trace_cache_miss_count")

                trace_id = tool_call_id if tool_call_id else new_trace_id()
                event_id = decision.event_id if decision is not None else new_event_id()

                # Build payload data for redaction (include result when available)
                payload_data = dict(data)
                if isinstance(result, str):
                    max_len = context.config.logging.max_field_length
                    payload_data["result_preview"] = result[:max_len]

                payload_summary, payload_hash, applied, failed = redactor.redact(payload_data)
                event = EventRecord(
                    event_id=event_id,
                    trace_id=trace_id,
                    parent_event_id=None,
                    event_type="post_tool_call",
                    tool_name=tool_name or (decision.tool_name if decision else None),
                    skill_name=decision.skill_name if decision else None,
                    payload_summary=payload_summary,
                    payload_hash=payload_hash,
                    redaction_applied=applied,
                    redaction_failed=failed,
                    duration_ms=int(duration_ms) if isinstance(duration_ms, (int, float)) else None,
                    error_type=str(data.get("error_type") or "") or None,
                )
                context.store.record_event(event)
                if decision is not None:
                    context.store.record_audit(decision)

                _maybe_finalize_promotion(context, data, event_id)
                # Auto-create candidate when the decision flags a skill creation
                _maybe_create_candidate(context, decision, data, event_id, trace_id)

            except Exception as exc:
                context.store.increment_counter("capture_failed_count")
                context.store.increment_counter(f"post_tool_call_failed:{type(exc).__name__}")

        adapter.register_hook("post_tool_call", post_tool_call)


def _maybe_create_candidate(
    context: SkillGuardContext,
    decision: Any,
    data: dict[str, Any],
    event_id: str,
    trace_id: str,
) -> None:
    """Create a candidate record if the decision warrants review."""
    if decision is None:
        return

    from hermes_skill_guard.schemas import DecisionValue

    if decision.decision != DecisionValue.CANDIDATE:
        return

    # Only auto-create for skill_manage create calls
    if decision.tool_name != "skill_manage":
        return

    args = data.get("args") or data.get("tool_args") or {}
    if not isinstance(args, dict):
        return

    # Extract skill metadata from the tool call arguments
    name = extract_skill_name(args) or decision.skill_name or "unknown"
    description = extract_description(args)
    content = extract_content(args)

    candidate = Candidate(
        candidate_id=new_candidate_id(),
        source_event_id=event_id,
        trace_id=trace_id,
        name=name,
        description=description,
        content_hash=content_hash(content),
        status=CandidateStatus.DETECTED,
        reasons=list(decision.reasons) if decision.reasons else [],
        session_id=str(data.get("session_id") or "") or None,
        task_id=str(data.get("task_id") or "") or None,
        tool_call_id=str(data.get("tool_call_id") or "") or None,
        content=content or None,
    )
    if context.store.find_candidate_by_source_event(event_id) is None:
        context.store.create_candidate(candidate)
        context.store.increment_counter("auto_candidate_created")


def _result_succeeded(result: object) -> bool:
    if isinstance(result, dict):
        return result.get("ok") is not False and not result.get("error")
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            return "error" not in result.lower()
        if isinstance(parsed, dict):
            return parsed.get("ok") is not False and not parsed.get("error")
    return result is not None


def _maybe_finalize_promotion(
    context: SkillGuardContext,
    data: dict[str, Any],
    event_id: str,
) -> None:
    if str(data.get("tool_name") or data.get("name") or "") != "skill_manage":
        return
    args = data.get("args") or data.get("tool_args") or {}
    if not isinstance(args, dict):
        return
    attempt_id = args.get("skill_guard_promotion_attempt_id")
    attempt: dict[str, object] | None = None
    if isinstance(attempt_id, str) and attempt_id:
        attempts = context.store.list_promotion_attempts()
        attempt = next((row for row in attempts if row.get("attempt_id") == attempt_id), None)
    if attempt is None:
        name = extract_skill_name(args)
        if not name:
            return
        attempt = context.store.find_pending_promotion_by_skill(name)
    if attempt is None:
        return
    succeeded = _result_succeeded(data.get("result"))
    error = None if succeeded else str(data.get("error_type") or "official skill_manage failed")
    context.store.complete_promotion_attempt(
        str(attempt["attempt_id"]),
        succeeded=succeeded,
        event_id=event_id,
        error=error,
    )
