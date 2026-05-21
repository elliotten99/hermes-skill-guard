"""Auto-promotion for approved candidates (T13).

Scans APPROVED candidates and promotes those that have met both the time
threshold (min_age_hours since approval) and the configured safety gates
(no conflicts / no duplicates).

Promotion is always dry-run by default; set ``auto_promote.dry_run=false``
to let the tool create real ``skill_manage create`` attempts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from hermes_skill_guard.config import GuardConfig
from hermes_skill_guard.context import SkillGuardContext
from hermes_skill_guard.hermes.adapter import HermesAdapter
from hermes_skill_guard.ids import new_candidate_id, new_event_id
from hermes_skill_guard.intents._extractors import build_skill_manage_create_args
from hermes_skill_guard.schemas import (
    CandidateStatus,
    PromotionAttempt,
    PromotionAttemptStatus,
    RelationType,
)
from hermes_skill_guard.storage.repository import StateStore

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AutoPromoteResult:
    """Outcome of a single auto-promote scan."""

    candidate_id: str
    candidate_name: str
    promoted: bool
    reason: str
    dry_run: bool


class AutoPromoter:
    """Scan and auto-promote approved candidates that meet configured gates."""

    def __init__(self, config: GuardConfig, store: StateStore) -> None:
        self._config = config.auto_promote
        self._store = store

    def scan(self) -> list[AutoPromoteResult]:
        """Return a list of scan results without mutating state.

        When ``dry_run`` is off, qualifying candidates are promoted
        (a :class:`PromotionAttempt` is created in the store).
        """
        if not self._config.enabled:
            return []

        candidates = self._store.list_candidates()
        approved = [
            c for c in candidates if str(c.get("status", "")) == CandidateStatus.APPROVED.value
        ]

        results: list[AutoPromoteResult] = []
        for candidate in approved:
            result = self._evaluate_candidate(candidate)
            results.append(result)
            if result.promoted and not result.dry_run:
                self._promote(candidate)

        return results

    def _evaluate_candidate(self, candidate: dict[str, Any]) -> AutoPromoteResult:
        cid = str(candidate["candidate_id"])
        name = str(candidate.get("name", ""))
        reviewed_at = candidate.get("reviewed_at")

        # Time gate
        if reviewed_at:
            try:
                reviewed = datetime.fromisoformat(reviewed_at)
                age = datetime.now(UTC) - reviewed
                if age < timedelta(hours=self._config.min_age_hours):
                    return AutoPromoteResult(
                        candidate_id=cid,
                        candidate_name=name,
                        promoted=False,
                        reason="candidate has not aged enough",
                        dry_run=self._config.dry_run,
                    )
            except ValueError:
                pass  # malformed timestamp, treat as aged

        # Relation gates
        relations = self._store.find_related_candidates(cid)
        if self._config.require_no_conflicts:
            conflicts = [
                r
                for r in relations
                if str(r.get("relation_type", "")) == RelationType.CONFLICT.value
            ]
            if conflicts:
                return AutoPromoteResult(
                    candidate_id=cid,
                    candidate_name=name,
                    promoted=False,
                    reason="candidate has conflict relations",
                    dry_run=self._config.dry_run,
                )

        if self._config.require_no_duplicates:
            duplicates = [
                r
                for r in relations
                if str(r.get("relation_type", "")) == RelationType.DUPLICATE.value
            ]
            if duplicates:
                return AutoPromoteResult(
                    candidate_id=cid,
                    candidate_name=name,
                    promoted=False,
                    reason="candidate has duplicate relations",
                    dry_run=self._config.dry_run,
                )

        return AutoPromoteResult(
            candidate_id=cid,
            candidate_name=name,
            promoted=True,
            reason="all gates passed",
            dry_run=self._config.dry_run,
        )

    def _promote(self, candidate: dict[str, Any]) -> None:
        """Create a promotion attempt for *candidate*."""
        cid = str(candidate["candidate_id"])
        content = candidate.get("content")
        target_path = candidate.get("target_path")
        skill_manage_args = build_skill_manage_create_args(
            name=str(candidate.get("name", "")),
            description=str(candidate.get("description", "")),
            content=str(content) if isinstance(content, str) else None,
            target_path=str(target_path) if isinstance(target_path, str) else None,
        )
        attempt_id = f"auto_{new_candidate_id()}"
        skill_manage_args["skill_guard_promotion_attempt_id"] = attempt_id
        attempt = PromotionAttempt(
            attempt_id=attempt_id,
            candidate_id=cid,
            trace_id=str(candidate.get("trace_id") or cid),
            tool_call_id=None,
            skill_name=str(candidate.get("name", "")),
            skill_manage_args=skill_manage_args,
            status=PromotionAttemptStatus.PENDING,
        )
        self._store.create_promotion_attempt(attempt)
        self._store.transition_candidate(
            cid,
            CandidateStatus.PROMOTED,
            new_event_id(),
            "auto-promote (time + condition gates passed)",
        )
        _LOG.info("auto-promoted candidate %s (%s)", cid, candidate.get("name"))


class AutoPromoteIntent:
    intent_id = "auto_promote"
    priority = 40

    def register(self, adapter: HermesAdapter, context: SkillGuardContext) -> None:
        promoter = AutoPromoter(context.config, context.store)

        def handler(args: dict[str, Any], **_: object) -> str:
            try:
                results = promoter.scan()
                return json.dumps(
                    {
                        "ok": True,
                        "enabled": context.config.auto_promote.enabled,
                        "dry_run": context.config.auto_promote.dry_run,
                        "scanned": len(results),
                        "promoted": sum(1 for r in results if r.promoted),
                        "results": [
                            {
                                "candidate_id": r.candidate_id,
                                "name": r.candidate_name,
                                "promoted": r.promoted,
                                "reason": r.reason,
                                "dry_run": r.dry_run,
                            }
                            for r in results
                        ],
                    },
                    ensure_ascii=False,
                    default=str,
                )
            except Exception as exc:
                _LOG.exception("auto-promote scan failed")
                return json.dumps(
                    {"ok": False, "error": type(exc).__name__, "message": str(exc)},
                    ensure_ascii=False,
                )

        adapter.register_tool(
            "skill_guard_auto_promote",
            handler,
            "Scan approved candidates and auto-promote those that meet configured gates.",
            schema={
                "name": "skill_guard_auto_promote",
                "description": (
                    "Scan approved candidates and auto-promote those that meet configured gates."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        )
