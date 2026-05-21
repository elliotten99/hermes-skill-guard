"""Candidate management tools."""

from __future__ import annotations

import json
from typing import Any

from hermes_skill_guard.context import SkillGuardContext
from hermes_skill_guard.hermes.adapter import HermesAdapter
from hermes_skill_guard.ids import new_candidate_id, new_event_id
from hermes_skill_guard.schemas import Candidate, CandidateStatus

CANDIDATES_SCHEMA = {
    "name": "skill_guard_candidates",
    "description": "List or update hermes-skill-guard candidates.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "details", "stage", "approve", "reject", "archive", "create"],
                "description": "Action to perform on candidates.",
            },
            "candidate_id": {
                "type": "string",
                "description": "Candidate ID (required for approve/reject/archive).",
            },
            "event_id": {
                "type": "string",
                "description": "Event ID (required for create).",
            },
            "name": {
                "type": "string",
                "description": "Candidate name (required for create).",
            },
            "description": {
                "type": "string",
                "description": "Candidate description (required for create).",
            },
            "content_hash": {
                "type": "string",
                "description": "Content hash (required for create).",
            },
            "content": {
                "type": "string",
                "description": "Optional promotable skill content for create.",
            },
            "target_path": {
                "type": "string",
                "description": "Optional target path for promotion.",
            },
            "reasons": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Reasons for candidate creation.",
            },
        },
        "required": ["action"],
    },
}


class CandidatesIntent:
    intent_id = "candidates"
    priority = 30

    def register(self, adapter: HermesAdapter, context: SkillGuardContext) -> None:
        def handler(args: dict[str, Any], **_: object) -> str:
            try:
                action = str(args.get("action", "list"))
                if action == "list":
                    return json.dumps(
                        {"ok": True, "candidates": context.store.list_candidates()},
                        ensure_ascii=False,
                        default=str,
                    )
                if action == "details":
                    raw_candidate_id = args.get("candidate_id")
                    if raw_candidate_id is None:
                        return json.dumps(
                            {"ok": False, "error": "candidate_id is required for details"}
                        )
                    candidate = context.store.get_candidate(str(raw_candidate_id))
                    if candidate is None:
                        return json.dumps({"ok": False, "error": "candidate not found"})
                    return json.dumps(
                        {
                            "ok": True,
                            "candidate": candidate,
                            "promotion_attempts": context.store.list_promotion_attempts(
                                str(raw_candidate_id)
                            ),
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                if action == "stage":
                    stage_candidate_id = args.get("candidate_id")
                    if stage_candidate_id is None:
                        return json.dumps(
                            {"ok": False, "error": "candidate_id is required for stage"}
                        )
                    try:
                        context.store.transition_candidate(
                            str(stage_candidate_id),
                            CandidateStatus.CANDIDATE,
                            new_event_id(),
                            "manual stage",
                        )
                    except (KeyError, ValueError) as exc:
                        return json.dumps({"ok": False, "error": str(exc)})
                    return json.dumps(
                        {
                            "ok": True,
                            "candidate_id": str(stage_candidate_id),
                            "status": "candidate",
                        }
                    )
                if action in {"approve", "reject"}:
                    candidate_id = str(args["candidate_id"])
                    status = (
                        CandidateStatus.APPROVED
                        if action == "approve"
                        else CandidateStatus.REJECTED
                    )
                    context.store.transition_candidate(
                        candidate_id, status, new_event_id(), f"manual {action}"
                    )
                    return json.dumps({"ok": True, "candidate_id": candidate_id, "status": status})
                if action == "archive":
                    archive_candidate_id = args.get("candidate_id")
                    if archive_candidate_id is None:
                        return json.dumps(
                            {"ok": False, "error": "candidate_id is required for archive"}
                        )
                    try:
                        context.store.transition_candidate(
                            str(archive_candidate_id),
                            CandidateStatus.ARCHIVED,
                            new_event_id(),
                            "manual archive",
                        )
                    except ValueError as exc:
                        return json.dumps({"ok": False, "error": str(exc)})
                    return json.dumps(
                        {
                            "ok": True,
                            "candidate_id": str(archive_candidate_id),
                            "status": "archived",
                        }
                    )
                if action == "create":
                    event_id = args.get("event_id")
                    name = args.get("name")
                    description = args.get("description")
                    content_hash = args.get("content_hash")
                    missing = [
                        f
                        for f, v in [
                            ("event_id", event_id),
                            ("name", name),
                            ("description", description),
                            ("content_hash", content_hash),
                        ]
                        if v is None
                    ]
                    if missing:
                        return json.dumps(
                            {
                                "ok": False,
                                "error": f"required fields missing: {', '.join(missing)}",
                            }
                        )
                    events = context.store.list_events()
                    event = next((e for e in events if e.get("event_id") == event_id), None)
                    if event is None:
                        return json.dumps({"ok": False, "error": "event not found"})
                    trace_id = str(event["trace_id"])
                    reasons = args.get("reasons")
                    new_candidate = Candidate(
                        candidate_id=new_candidate_id(),
                        source_event_id=str(event_id),
                        trace_id=trace_id,
                        name=str(name),
                        description=str(description),
                        content_hash=str(content_hash),
                        status=CandidateStatus.DETECTED,
                        reasons=reasons if isinstance(reasons, list) else [],
                        content=str(args["content"])
                        if isinstance(args.get("content"), str)
                        else None,
                        target_path=(
                            str(args["target_path"])
                            if isinstance(args.get("target_path"), str)
                            else None
                        ),
                    )
                    context.store.create_candidate(new_candidate)
                    return json.dumps(
                        {
                            "ok": True,
                            "candidate_id": new_candidate.candidate_id,
                            "status": "detected",
                        }
                    )
                return json.dumps({"ok": False, "error": f"unsupported action: {action}"})
            except KeyError:
                return json.dumps({"ok": False, "error": "candidate not found"})
            except Exception as exc:
                return json.dumps({"ok": False, "error": type(exc).__name__})

        adapter.register_tool(
            "skill_guard_candidates",
            handler,
            "List or update hermes-skill-guard candidates.",
            schema=CANDIDATES_SCHEMA,
        )
