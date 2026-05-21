"""Tests for the auto-promote intent (T13).

Covers time gates, relation gates, dry-run behaviour, and the promotion
mutation path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from hermes_skill_guard.config import AutoPromoteConfig, GuardConfig
from hermes_skill_guard.ids import new_candidate_id, new_event_id, new_trace_id
from hermes_skill_guard.intents.auto_promoter import AutoPromoter
from hermes_skill_guard.schemas import (
    Candidate,
    CandidateStatus,
    Confidence,
    RelationType,
    SkillRelation,
)
from hermes_skill_guard.storage.repository import StateStore


def _store(tmp_path: Path) -> StateStore:
    config = GuardConfig(state_dir=tmp_path)
    return StateStore(config.state_db, config.events)


def _approved_candidate(
    *,
    reviewed_at: str | None = None,
    name: str = "test",
) -> Candidate:
    return Candidate(
        candidate_id=new_candidate_id(),
        source_event_id=new_event_id(),
        trace_id=new_trace_id(),
        name=name,
        description="desc",
        content_hash="hash",
        status=CandidateStatus.APPROVED,
        promotable=True,
        reviewed_at=reviewed_at,
    )


def _relation(
    source: str,
    target: str,
    relation_type: RelationType = RelationType.CONFLICT,
) -> SkillRelation:
    return SkillRelation(
        relation_id=f"rel_{new_candidate_id()}",
        source_candidate_id=source,
        target_candidate_id=target,
        relation_type=relation_type,
        confidence=Confidence.HIGH,
        reasons=["test"],
        created_at=datetime.now(UTC).isoformat(),
    )


class TestAutoPromoterDisabled:
    def test_returns_empty_when_disabled(self, tmp_path: Path) -> None:
        store = StateStore(GuardConfig(state_dir=tmp_path).state_db, GuardConfig().events)
        promoter = AutoPromoter(GuardConfig(auto_promote=AutoPromoteConfig(enabled=False)), store)
        assert promoter.scan() == []


class TestTimeGate:
    def test_promotes_when_old_enough(self, tmp_path: Path) -> None:
        old = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
        candidate = _approved_candidate(reviewed_at=old)
        store = StateStore(GuardConfig(state_dir=tmp_path).state_db, GuardConfig().events)
        store.create_candidate(candidate)

        config = GuardConfig(
            state_dir=tmp_path,
            auto_promote=AutoPromoteConfig(enabled=True, min_age_hours=24, dry_run=True),
        )
        promoter = AutoPromoter(config, store)
        results = promoter.scan()

        assert len(results) == 1
        assert results[0].promoted is True
        assert results[0].candidate_id == candidate.candidate_id

    def test_skips_when_too_young(self, tmp_path: Path) -> None:
        recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        candidate = _approved_candidate(reviewed_at=recent)
        store = StateStore(GuardConfig(state_dir=tmp_path).state_db, GuardConfig().events)
        store.create_candidate(candidate)

        config = GuardConfig(
            state_dir=tmp_path,
            auto_promote=AutoPromoteConfig(enabled=True, min_age_hours=24, dry_run=True),
        )
        promoter = AutoPromoter(config, store)
        results = promoter.scan()

        assert len(results) == 1
        assert results[0].promoted is False
        assert "aged" in results[0].reason

    def test_promotes_when_no_reviewed_at(self, tmp_path: Path) -> None:
        candidate = _approved_candidate(reviewed_at=None)
        store = StateStore(GuardConfig(state_dir=tmp_path).state_db, GuardConfig().events)
        store.create_candidate(candidate)

        config = GuardConfig(
            state_dir=tmp_path,
            auto_promote=AutoPromoteConfig(enabled=True, min_age_hours=1, dry_run=True),
        )
        promoter = AutoPromoter(config, store)
        results = promoter.scan()

        assert len(results) == 1
        assert results[0].promoted is True


class TestRelationGates:
    def test_skips_when_conflict_exists(self, tmp_path: Path) -> None:
        old = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
        candidate = _approved_candidate(reviewed_at=old)
        other = _approved_candidate(reviewed_at=old, name="other")
        store = StateStore(GuardConfig(state_dir=tmp_path).state_db, GuardConfig().events)
        store.create_candidate(candidate)
        store.create_candidate(other)
        store.add_relation(
            _relation(candidate.candidate_id, other.candidate_id, RelationType.CONFLICT)
        )

        config = GuardConfig(
            state_dir=tmp_path,
            auto_promote=AutoPromoteConfig(
                enabled=True, min_age_hours=1, require_no_conflicts=True, dry_run=True
            ),
        )
        promoter = AutoPromoter(config, store)
        results = promoter.scan()

        r = next(r for r in results if r.candidate_id == candidate.candidate_id)
        assert r.promoted is False
        assert "conflict" in r.reason

    def test_promotes_when_no_conflict_and_gate_off(self, tmp_path: Path) -> None:
        old = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
        candidate = _approved_candidate(reviewed_at=old)
        other = _approved_candidate(reviewed_at=old, name="other")
        store = StateStore(GuardConfig(state_dir=tmp_path).state_db, GuardConfig().events)
        store.create_candidate(candidate)
        store.create_candidate(other)
        store.add_relation(
            _relation(candidate.candidate_id, other.candidate_id, RelationType.CONFLICT)
        )

        config = GuardConfig(
            state_dir=tmp_path,
            auto_promote=AutoPromoteConfig(
                enabled=True, min_age_hours=1, require_no_conflicts=False, dry_run=True
            ),
        )
        promoter = AutoPromoter(config, store)
        results = promoter.scan()

        r = next(r for r in results if r.candidate_id == candidate.candidate_id)
        assert r.promoted is True

    def test_skips_when_duplicate_exists(self, tmp_path: Path) -> None:
        old = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
        candidate = _approved_candidate(reviewed_at=old)
        other = _approved_candidate(reviewed_at=old, name="other")
        store = StateStore(GuardConfig(state_dir=tmp_path).state_db, GuardConfig().events)
        store.create_candidate(candidate)
        store.create_candidate(other)
        store.add_relation(
            _relation(candidate.candidate_id, other.candidate_id, RelationType.DUPLICATE)
        )

        config = GuardConfig(
            state_dir=tmp_path,
            auto_promote=AutoPromoteConfig(
                enabled=True,
                min_age_hours=1,
                require_no_conflicts=False,
                require_no_duplicates=True,
                dry_run=True,
            ),
        )
        promoter = AutoPromoter(config, store)
        results = promoter.scan()

        r = next(r for r in results if r.candidate_id == candidate.candidate_id)
        assert r.promoted is False
        assert "duplicate" in r.reason


class TestDryRunVsReal:
    def test_dry_run_does_not_mutate_store(self, tmp_path: Path) -> None:
        old = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
        candidate = _approved_candidate(reviewed_at=old)
        store = StateStore(GuardConfig(state_dir=tmp_path).state_db, GuardConfig().events)
        store.create_candidate(candidate)

        config = GuardConfig(
            state_dir=tmp_path,
            auto_promote=AutoPromoteConfig(enabled=True, min_age_hours=1, dry_run=True),
        )
        promoter = AutoPromoter(config, store)
        promoter.scan()

        # Candidate should still be APPROVED, no promotion attempt created.
        row = store.get_candidate(candidate.candidate_id)
        assert row is not None
        assert row["status"] == CandidateStatus.APPROVED.value
        assert store.list_promotion_attempts(candidate.candidate_id) == []

    def test_real_run_mutates_store(self, tmp_path: Path) -> None:
        old = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
        candidate = _approved_candidate(reviewed_at=old)
        store = StateStore(GuardConfig(state_dir=tmp_path).state_db, GuardConfig().events)
        store.create_candidate(candidate)

        config = GuardConfig(
            state_dir=tmp_path,
            auto_promote=AutoPromoteConfig(enabled=True, min_age_hours=1, dry_run=False),
        )
        promoter = AutoPromoter(config, store)
        results = promoter.scan()

        assert results[0].promoted is True
        row = store.get_candidate(candidate.candidate_id)
        assert row is not None
        assert row["status"] == CandidateStatus.PROMOTED.value
        attempts = store.list_promotion_attempts(candidate.candidate_id)
        assert len(attempts) == 1
        assert str(attempts[0]["status"]) == "pending"


class TestNonApprovedCandidates:
    def test_ignores_detected_and_candidate_status(self, tmp_path: Path) -> None:
        for status in (CandidateStatus.DETECTED, CandidateStatus.CANDIDATE):
            store = StateStore(GuardConfig(state_dir=tmp_path).state_db, GuardConfig().events)
            c = Candidate(
                candidate_id=new_candidate_id(),
                source_event_id=new_event_id(),
                trace_id=new_trace_id(),
                name="x",
                description="d",
                content_hash="h",
                status=status,
            )
            store.create_candidate(c)

            config = GuardConfig(
                state_dir=tmp_path,
                auto_promote=AutoPromoteConfig(enabled=True, min_age_hours=1, dry_run=True),
            )
            promoter = AutoPromoter(config, store)
            assert promoter.scan() == []
