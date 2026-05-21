"""Preflight hook and tool."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import replace
from time import monotonic
from typing import Any

from hermes_skill_guard.context import SkillGuardContext
from hermes_skill_guard.hermes.adapter import HermesAdapter
from hermes_skill_guard.ids import new_candidate_id
from hermes_skill_guard.intents._extractors import (
    extract_content,
    extract_description,
    extract_skill_name,
    extract_target_path,
)
from hermes_skill_guard.policy import PreflightPolicy, ToolCall, content_hash
from hermes_skill_guard.redaction import SECRET_PATTERNS, Redactor
from hermes_skill_guard.schemas import (
    Candidate,
    CandidateStatus,
    Decision,
    DecisionValue,
    EventRecord,
)

# Process-wide executor used to bound PreflightPolicy.evaluate latency.
# Pure-computation work, but isolated so a buggy/slow policy cannot stall
# the calling hook thread beyond enforcement.timeout_ms.
#
# Stage B note: policy.evaluate must remain read-mostly. The executor does
# not register atexit shutdown; when Hermes moves to an async/long-lived
# daemon model, switch this to asyncio.to_thread or register
# atexit.register(_PREFLIGHT_EXECUTOR.shutdown, wait=False, cancel_futures=True).
_PREFLIGHT_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hsg-preflight")

PREFLIGHT_SCHEMA = {
    "name": "skill_guard_preflight",
    "description": "Run deterministic preflight for a proposed skill_manage call.",
    "parameters": {
        "type": "object",
        "properties": {
            "tool_name": {"type": "string", "description": "Name of the tool to evaluate."},
            "args": {
                "type": "object",
                "description": "Arguments that would be passed to the tool.",
            },
        },
        "required": ["tool_name", "args"],
    },
}


def _extract_tool_call(kwargs: dict[str, Any]) -> ToolCall:
    tool_name = str(kwargs.get("tool_name") or kwargs.get("name") or "")
    tool_args = kwargs.get("args") or kwargs.get("tool_args") or {}
    if not isinstance(tool_args, dict):
        tool_args = {}
    return ToolCall(tool_name=tool_name, args=dict(tool_args))


class PreflightIntent:
    intent_id = "preflight"
    priority = 20

    def register(self, adapter: HermesAdapter, context: SkillGuardContext) -> None:
        policy = PreflightPolicy(context.config)
        redactor = Redactor(
            capture_raw_payloads=False,
            max_field_length=context.config.logging.max_field_length,
            hash_redacted_values=context.config.logging.hash_redacted_values,
        )

        def tool_handler(args: dict[str, Any], **kwargs: object) -> str:
            try:
                call = _extract_tool_call(dict(args))
                decision = policy.evaluate(call)
                return json.dumps({"ok": True, "decision": decision.to_dict()}, ensure_ascii=False)
            except Exception as exc:
                context.store.increment_counter("preflight_tool_failed")
                return json.dumps({"ok": False, "error": type(exc).__name__}, ensure_ascii=False)

        def pre_tool_call(**hook_kwargs: object) -> dict[str, str] | None:
            """Pre hook that can block candidate/block mode before skill creation.

            Hermes invokes pre_tool_call with kwargs:
            tool_name, args, task_id, session_id, tool_call_id.
            We use tool_call_id (when present) as the trace key so the
            paired post_tool_call can look up the decision.
            """
            try:
                start = monotonic()
                call = _extract_tool_call(dict(hook_kwargs))
                timeout_s = max(context.config.enforcement.timeout_ms, 1) / 1000.0
                future = _PREFLIGHT_EXECUTOR.submit(policy.evaluate, call)
                try:
                    decision = future.result(timeout=timeout_s)
                except FutureTimeoutError:
                    elapsed_ms = (monotonic() - start) * 1000
                    context.logger.warning(
                        "preflight policy exceeded timeout %dms (took %.1fms)",
                        context.config.enforcement.timeout_ms,
                        elapsed_ms,
                    )
                    context.store.increment_counter("preflight_timeout_count")
                    context.exporter.record_counter(
                        "hsg_preflight_timeout_total", 1, tool_name=call.tool_name
                    )
                    context.exporter.record_histogram(
                        "hsg_pre_tool_call_duration_ms",
                        elapsed_ms,
                        tool_name=call.tool_name,
                        decision="timeout",
                    )
                    # Best-effort cancellation; if the worker is already running
                    # the cancel will no-op but the thread will be reclaimed
                    # by the pool once policy.evaluate returns.
                    future.cancel()
                    if context.config.enforcement.fail_open:
                        return None
                    return {
                        "action": "block",
                        "message": ("skill-guard preflight timed out and fail_open=false"),
                    }
                tool_call_id = str(hook_kwargs.get("tool_call_id") or "")
                if tool_call_id:
                    # Align trace_id with tool_call_id so post_tool_call can correlate
                    decision = replace(decision, trace_id=tool_call_id)
                context.trace_cache.put(decision)
                elapsed_ms = (monotonic() - start) * 1000
                context.exporter.record_counter(
                    "hsg_pre_tool_call_total",
                    1,
                    tool_name=call.tool_name,
                    decision=decision.decision.value,
                )
                context.exporter.record_histogram(
                    "hsg_pre_tool_call_duration_ms",
                    elapsed_ms,
                    tool_name=call.tool_name,
                    decision=decision.decision.value,
                )
                if decision.decision in (DecisionValue.CANDIDATE, DecisionValue.BLOCK):
                    try:
                        _persist_preflight_decision(
                            context=context,
                            redactor=redactor,
                            decision=decision,
                            hook_kwargs=dict(hook_kwargs),
                            create_candidate=decision.decision == DecisionValue.CANDIDATE,
                        )
                    except Exception as exc:
                        context.store.increment_counter("preflight_persist_failed")
                        context.store.increment_counter(
                            f"preflight_persist_failed:{type(exc).__name__}"
                        )
                        if context.config.enforcement.fail_open:
                            return None
                        return {
                            "action": "block",
                            "message": "skill-guard could not persist preflight decision",
                        }
                    return {
                        "action": "block",
                        "message": _block_message(decision),
                    }
                return None
            except Exception as exc:
                context.store.increment_counter("fail_open_count")
                context.store.increment_counter(f"pre_tool_call_failed:{type(exc).__name__}")
                if not context.config.enforcement.fail_open:
                    return {
                        "action": "block",
                        "message": "skill-guard preflight failed",
                    }
                return None

        adapter.register_tool(
            "skill_guard_preflight",
            tool_handler,
            "Run deterministic preflight for a proposed skill_manage call.",
            schema=PREFLIGHT_SCHEMA,
        )
        adapter.register_hook("pre_tool_call", pre_tool_call)


def _block_message(decision: Decision) -> str:
    reasons = "; ".join(decision.reasons[:3]) or "skill creation requires review"
    if decision.decision == DecisionValue.CANDIDATE:
        return f"skill-guard routed this skill to the candidate queue: {reasons}"
    return f"skill-guard blocked this skill creation: {reasons}"


def _persist_preflight_decision(
    *,
    context: SkillGuardContext,
    redactor: Redactor,
    decision: Decision,
    hook_kwargs: dict[str, object],
    create_candidate: bool,
) -> None:
    args = hook_kwargs.get("args") or hook_kwargs.get("tool_args") or {}
    if not isinstance(args, dict):
        args = {}
    payload_summary, payload_hash, applied, failed = redactor.redact(hook_kwargs)
    event = EventRecord(
        event_id=decision.event_id,
        trace_id=decision.trace_id,
        parent_event_id=None,
        event_type="pre_tool_call",
        tool_name=decision.tool_name,
        skill_name=decision.skill_name,
        payload_summary=payload_summary,
        payload_hash=payload_hash,
        redaction_applied=applied,
        redaction_failed=failed,
        error_type=None,
    )
    context.store.record_event(event)
    context.store.record_audit(decision)
    if create_candidate:
        content = extract_content(args)
        max_promotable_length = context.config.logging.max_field_length * 20
        has_secret = any(pattern.search(content) for pattern in SECRET_PATTERNS)
        promotable = (
            bool(content.strip()) and not has_secret and len(content) <= max_promotable_length
        )
        candidate = Candidate(
            candidate_id=new_candidate_id(),
            source_event_id=decision.event_id,
            trace_id=decision.trace_id,
            name=extract_skill_name(args) or decision.skill_name or "unknown",
            description=extract_description(args),
            content_hash=content_hash(content),
            status=CandidateStatus.DETECTED,
            reasons=list(decision.reasons),
            actor=str(hook_kwargs.get("actor") or hook_kwargs.get("user") or "") or None,
            session_id=str(hook_kwargs.get("session_id") or "") or None,
            task_id=str(hook_kwargs.get("task_id") or "") or None,
            tool_call_id=str(hook_kwargs.get("tool_call_id") or "") or None,
            payload_hash=payload_hash,
            promotable=promotable,
            content=content if promotable else None,
            target_path=extract_target_path(args),
        )
        if context.store.find_candidate_by_source_event(decision.event_id) is None:
            context.store.create_candidate(candidate)
            context.store.increment_counter("preflight_candidate_created")
