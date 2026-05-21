"""Typed schemas shared by policies, hooks, and storage."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class DecisionValue(StrEnum):
    ALLOW = "allow"
    WARN = "warn"
    CANDIDATE = "candidate"
    BLOCK = "block"
    FAIL_OPEN = "fail_open"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EnforcementMode(StrEnum):
    AUDIT = "audit"
    CANDIDATE = "candidate"
    BLOCK = "block"


class CandidateStatus(StrEnum):
    DETECTED = "detected"
    CANDIDATE = "candidate"
    APPROVED = "approved"
    PROMOTED = "promoted"
    ARCHIVED = "archived"
    REJECTED = "rejected"
    DANGLING = "dangling"


class PromotionAttemptStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ModuleStatus(StrEnum):
    ENABLED = "enabled"
    READ_ONLY = "read_only"
    CANDIDATE_FOR_RETIREMENT = "candidate_for_retirement"
    RETIRED_BY_OFFICIAL = "retired_by_official"
    DISABLED_BY_USER = "disabled_by_user"
    ERROR = "error"


class RelationType(StrEnum):
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    SUPERSEDES = "supersedes"
    DEPENDS_ON = "depends_on"
    RELATED_TO = "related_to"


ALLOWED_CANDIDATE_TRANSITIONS: dict[CandidateStatus, set[CandidateStatus]] = {
    CandidateStatus.DETECTED: {CandidateStatus.CANDIDATE, CandidateStatus.ARCHIVED},
    CandidateStatus.CANDIDATE: {
        CandidateStatus.APPROVED,
        CandidateStatus.REJECTED,
        CandidateStatus.ARCHIVED,
    },
    CandidateStatus.APPROVED: {CandidateStatus.PROMOTED, CandidateStatus.ARCHIVED},
    CandidateStatus.PROMOTED: {CandidateStatus.ARCHIVED, CandidateStatus.DANGLING},
    CandidateStatus.DANGLING: {CandidateStatus.ARCHIVED},
    CandidateStatus.REJECTED: {CandidateStatus.ARCHIVED},
    CandidateStatus.ARCHIVED: set(),
}


@dataclass(frozen=True, slots=True)
class Decision:
    decision: DecisionValue
    confidence: Confidence
    reasons: list[str]
    rule_ids: list[str]
    event_id: str
    trace_id: str
    tool_name: str
    skill_name: str | None
    dry_run: bool
    enforcement_mode: EnforcementMode

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["decision"] = self.decision.value
        data["confidence"] = self.confidence.value
        data["enforcement_mode"] = self.enforcement_mode.value
        return data


@dataclass(frozen=True, slots=True)
class EventRecord:
    event_id: str
    trace_id: str
    parent_event_id: str | None
    event_type: str
    tool_name: str | None
    skill_name: str | None
    payload_summary: dict[str, Any]
    payload_hash: str
    redaction_applied: bool
    redaction_failed: bool
    duration_ms: int | None = None
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class Candidate:
    candidate_id: str
    source_event_id: str
    trace_id: str
    name: str
    description: str
    content_hash: str
    status: CandidateStatus = CandidateStatus.DETECTED
    reasons: list[str] = field(default_factory=list)
    actor: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    tool_call_id: str | None = None
    payload_hash: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    promoted_at: str | None = None
    promotable: bool = True
    content: str | None = None
    target_path: str | None = None


@dataclass(frozen=True, slots=True)
class PromotionAttempt:
    attempt_id: str
    candidate_id: str
    trace_id: str
    tool_call_id: str | None
    skill_name: str
    skill_manage_args: dict[str, Any]
    status: PromotionAttemptStatus = PromotionAttemptStatus.PENDING
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SkillRelation:
    relation_id: str
    source_candidate_id: str
    target_candidate_id: str
    relation_type: RelationType
    confidence: Confidence
    reasons: list[str]
    created_at: str


def validate_candidate_transition(old: CandidateStatus, new: CandidateStatus) -> None:
    if new not in ALLOWED_CANDIDATE_TRANSITIONS[old]:
        raise ValueError(f"illegal candidate transition: {old.value} -> {new.value}")
