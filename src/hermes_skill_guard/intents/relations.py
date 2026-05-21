"""Relations intent for managing skill-to-skill relationships."""

from __future__ import annotations

import json
from typing import Any

from hermes_skill_guard.context import SkillGuardContext
from hermes_skill_guard.hermes.adapter import HermesAdapter
from hermes_skill_guard.ids import new_id
from hermes_skill_guard.schemas import Confidence, RelationType, SkillRelation
from hermes_skill_guard.storage.repository import utc_now

RELATIONS_SCHEMA = {
    "name": "skill_guard_relations",
    "description": "Manage relations between candidate skills.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "list", "remove"],
                "description": "Action to perform on relations.",
            },
            "source_candidate_id": {
                "type": "string",
                "description": "Source candidate ID (required for add/list).",
            },
            "target_candidate_id": {
                "type": "string",
                "description": "Target candidate ID (required for add).",
            },
            "relation_type": {
                "type": "string",
                "enum": ["duplicate", "conflict", "supersedes", "depends_on", "related_to"],
                "description": "Type of relation (required for add).",
            },
            "confidence": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Confidence level (required for add).",
            },
            "reasons": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Reasons for the relation (required for add).",
            },
            "relation_id": {
                "type": "string",
                "description": "Relation ID (required for remove).",
            },
        },
        "required": ["action"],
    },
}


class RelationsIntent:
    intent_id = "relations"
    priority = 32

    def register(self, adapter: HermesAdapter, context: SkillGuardContext) -> None:
        def handler(args: dict[str, Any], **_: object) -> str:
            try:
                action = str(args.get("action", ""))
                if action == "add":
                    return _handle_add(context, args)
                if action == "list":
                    return _handle_list(context, args)
                if action == "remove":
                    return _handle_remove(context, args)
                return json.dumps(
                    {"ok": False, "error": f"unsupported action: {action}"},
                    ensure_ascii=False,
                )
            except Exception as exc:
                return json.dumps(
                    {"ok": False, "error": type(exc).__name__},
                    ensure_ascii=False,
                )

        adapter.register_tool(
            "skill_guard_relations",
            handler,
            "Manage relations between candidate skills.",
            schema=RELATIONS_SCHEMA,
        )


def _handle_add(context: SkillGuardContext, args: dict[str, Any]) -> str:
    source_candidate_id = args.get("source_candidate_id")
    target_candidate_id = args.get("target_candidate_id")
    relation_type_raw = args.get("relation_type")
    confidence_raw = args.get("confidence")
    reasons = args.get("reasons")

    if not source_candidate_id:
        return json.dumps(
            {"ok": False, "error": "source_candidate_id is required for add"},
            ensure_ascii=False,
        )
    if not target_candidate_id:
        return json.dumps(
            {"ok": False, "error": "target_candidate_id is required for add"},
            ensure_ascii=False,
        )
    if not relation_type_raw:
        return json.dumps(
            {"ok": False, "error": "relation_type is required for add"},
            ensure_ascii=False,
        )
    if not confidence_raw:
        return json.dumps(
            {"ok": False, "error": "confidence is required for add"},
            ensure_ascii=False,
        )
    if not reasons or not isinstance(reasons, list):
        return json.dumps(
            {"ok": False, "error": "reasons is required for add and must be a list"},
            ensure_ascii=False,
        )

    try:
        relation_type = RelationType(str(relation_type_raw))
    except ValueError:
        return json.dumps(
            {"ok": False, "error": f"invalid relation_type: {relation_type_raw}"},
            ensure_ascii=False,
        )

    try:
        confidence = Confidence(str(confidence_raw))
    except ValueError:
        return json.dumps(
            {"ok": False, "error": f"invalid confidence: {confidence_raw}"},
            ensure_ascii=False,
        )

    # Verify both candidates exist
    candidates = context.store.list_candidates()
    candidate_ids = {str(c["candidate_id"]) for c in candidates}
    if str(source_candidate_id) not in candidate_ids:
        return json.dumps(
            {"ok": False, "error": "source_candidate_id not found"},
            ensure_ascii=False,
        )
    if str(target_candidate_id) not in candidate_ids:
        return json.dumps(
            {"ok": False, "error": "target_candidate_id not found"},
            ensure_ascii=False,
        )

    relation = SkillRelation(
        relation_id=new_id("rel"),
        source_candidate_id=str(source_candidate_id),
        target_candidate_id=str(target_candidate_id),
        relation_type=relation_type,
        confidence=confidence,
        reasons=[str(r) for r in reasons],
        created_at=utc_now(),
    )
    context.store.add_relation(relation)
    return json.dumps(
        {"ok": True, "relation_id": relation.relation_id},
        ensure_ascii=False,
    )


def _handle_list(context: SkillGuardContext, args: dict[str, Any]) -> str:
    source_candidate_id = args.get("source_candidate_id")
    target_candidate_id = args.get("target_candidate_id")
    relation_type_raw = args.get("relation_type")

    relation_type: RelationType | None = None
    if relation_type_raw:
        try:
            relation_type = RelationType(str(relation_type_raw))
        except ValueError:
            return json.dumps(
                {"ok": False, "error": f"invalid relation_type: {relation_type_raw}"},
                ensure_ascii=False,
            )

    relations = context.store.list_relations(
        source_candidate_id=str(source_candidate_id) if source_candidate_id else None,
        target_candidate_id=str(target_candidate_id) if target_candidate_id else None,
        relation_type=relation_type,
    )
    return json.dumps({"ok": True, "relations": relations}, ensure_ascii=False)


def _handle_remove(context: SkillGuardContext, args: dict[str, Any]) -> str:
    relation_id = args.get("relation_id")
    if not relation_id:
        return json.dumps(
            {"ok": False, "error": "relation_id is required for remove"},
            ensure_ascii=False,
        )
    removed = context.store.remove_relation(str(relation_id))
    if not removed:
        return json.dumps(
            {"ok": False, "error": "relation not found"},
            ensure_ascii=False,
        )
    return json.dumps({"ok": True, "relation_id": str(relation_id)}, ensure_ascii=False)
