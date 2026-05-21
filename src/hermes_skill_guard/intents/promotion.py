"""Promotion intent for moving approved candidates to promoted status."""

from __future__ import annotations

import json
from typing import Any

from hermes_skill_guard.context import SkillGuardContext
from hermes_skill_guard.hermes.adapter import HermesAdapter
from hermes_skill_guard.ids import new_promotion_attempt_id, new_trace_id
from hermes_skill_guard.intents._extractors import build_skill_manage_create_args
from hermes_skill_guard.schemas import PromotionAttempt

PROMOTE_SCHEMA = {
    "name": "skill_guard_promote",
    "description": "Promote an approved candidate skill to promoted status.",
    "parameters": {
        "type": "object",
        "properties": {
            "candidate_id": {
                "type": "string",
                "description": "ID of the approved candidate to promote.",
            },
        },
        "required": ["candidate_id"],
    },
}


class PromotionIntent:
    intent_id = "promotion"
    priority = 35

    def register(self, adapter: HermesAdapter, context: SkillGuardContext) -> None:
        def handler(args: dict[str, Any], **_: object) -> str:
            try:
                candidate_id = str(args["candidate_id"])
                candidate = context.store.get_candidate(candidate_id)
                if candidate is None:
                    return json.dumps(
                        {"ok": False, "error": "candidate not found"},
                        ensure_ascii=False,
                    )
                content = candidate.get("content")
                target_path = candidate.get("target_path")
                skill_manage_args = build_skill_manage_create_args(
                    name=str(candidate["name"]),
                    description=str(candidate["description"]),
                    content=str(content) if isinstance(content, str) else None,
                    target_path=str(target_path) if isinstance(target_path, str) else None,
                )
                attempt_id = new_promotion_attempt_id()
                skill_manage_args["skill_guard_promotion_attempt_id"] = attempt_id
                trace_id = str(candidate.get("trace_id") or new_trace_id())
                attempt = PromotionAttempt(
                    attempt_id=attempt_id,
                    candidate_id=candidate_id,
                    trace_id=trace_id,
                    tool_call_id=None,
                    skill_name=str(candidate["name"]),
                    skill_manage_args=skill_manage_args,
                )
                context.store.create_promotion_attempt(attempt)
                return json.dumps(
                    {
                        "ok": True,
                        "candidate_id": candidate_id,
                        "attempt_id": attempt_id,
                        "status": "pending_promotion",
                        "tool_name": "skill_manage",
                        "tool_args": skill_manage_args,
                    },
                    ensure_ascii=False,
                )
            except KeyError:
                return json.dumps(
                    {"ok": False, "error": "candidate not found"},
                    ensure_ascii=False,
                )
            except ValueError as exc:
                return json.dumps(
                    {"ok": False, "error": str(exc)},
                    ensure_ascii=False,
                )
            except Exception as exc:
                return json.dumps(
                    {"ok": False, "error": type(exc).__name__},
                    ensure_ascii=False,
                )

        adapter.register_tool(
            "skill_guard_promote",
            handler,
            "Promote an approved candidate skill to promoted status.",
            schema=PROMOTE_SCHEMA,
        )
