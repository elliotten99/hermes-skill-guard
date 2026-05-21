"""Tests for ``StateStore.mark_dangling_candidates``.

Covers the behaviour described in ``repository.py:328-421``:

- Selects PROMOTED candidates whose ``updated_at`` is older than ``days``.
- Transitions them to DANGLING and records events / transitions / audit_log.
- Returns the list of candidate IDs that were marked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from hermes_skill_guard.ids import new_candidate_id, new_event_id, new_trace_id
from hermes_skill_guard.schemas import Candidate, CandidateStatus
from hermes_skill_guard.storage.repository import StateStore


def _make_promoted(store: StateStore, *, name: str = "demo") -> str:
    """Create a candidate and walk it through to PROMOTED status.

    Returns the candidate_id.
    """

    candidate = Candidate(
        candidate_id=new_candidate_id(),
        source_event_id=new_event_id(),
        trace_id=new_trace_id(),
        name=name,
        description=f"{name} candidate",
        content_hash=f"hash-{name}",
    )
    store.create_candidate(candidate)
    store.transition_candidate(
        candidate.candidate_id, CandidateStatus.CANDIDATE, new_event_id(), "review"
    )
    store.transition_candidate(
        candidate.candidate_id, CandidateStatus.APPROVED, new_event_id(), "approved"
    )
    store.transition_candidate(
        candidate.candidate_id, CandidateStatus.PROMOTED, new_event_id(), "promoted"
    )
    return candidate.candidate_id


def _age_candidate(store: StateStore, candidate_id: str, days_ago: int) -> None:
    """Force ``updated_at`` of a candidate to ``days_ago`` days in the past."""

    target = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    with store.connect() as conn:
        conn.execute(
            "UPDATE candidates SET updated_at = ? WHERE candidate_id = ?",
            (target, candidate_id),
        )


def _count_audit_for_event(store: StateStore, event_type: str) -> int:
    with store.connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM audit_log
            WHERE event_id IN (SELECT event_id FROM events WHERE event_type = ?)
            """,
            (event_type,),
        ).fetchone()
    return int(row[0])


def test_no_promoted_candidates_returns_empty(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    assert store.mark_dangling_candidates() == []


def test_recent_promoted_not_dangling(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    candidate_id = _make_promoted(store)

    # Candidate was just promoted, so it should not be dangling.
    assert store.mark_dangling_candidates(days=30) == []

    record = store.get_candidate(candidate_id)
    assert record is not None
    assert record["status"] == CandidateStatus.PROMOTED.value


def test_old_promoted_marked_dangling(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    candidate_id = _make_promoted(store)
    _age_candidate(store, candidate_id, days_ago=45)

    result = store.mark_dangling_candidates(days=30)

    assert result == [candidate_id]
    record = store.get_candidate(candidate_id)
    assert record is not None
    assert record["status"] == CandidateStatus.DANGLING.value


def test_custom_days_threshold(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    recent_id = _make_promoted(store, name="recent")
    old_id = _make_promoted(store, name="old")
    _age_candidate(store, recent_id, days_ago=3)
    _age_candidate(store, old_id, days_ago=10)

    result = store.mark_dangling_candidates(days=7)

    assert result == [old_id]
    recent_record = store.get_candidate(recent_id)
    old_record = store.get_candidate(old_id)
    assert recent_record is not None and old_record is not None
    assert recent_record["status"] == CandidateStatus.PROMOTED.value
    assert old_record["status"] == CandidateStatus.DANGLING.value


def test_idempotent(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    candidate_id = _make_promoted(store)
    _age_candidate(store, candidate_id, days_ago=60)

    first = store.mark_dangling_candidates(days=30)
    second = store.mark_dangling_candidates(days=30)

    assert first == [candidate_id]
    assert second == []


def test_audit_log_recorded(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    candidate_id = _make_promoted(store)
    _age_candidate(store, candidate_id, days_ago=45)

    store.mark_dangling_candidates(days=30)

    # Exactly one audit_log entry tied to a candidate_dangling event.
    assert _count_audit_for_event(store, "candidate_dangling") == 1

    # And a matching candidate_transitions row from promoted -> dangling.
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT from_status, to_status, reason FROM candidate_transitions
            WHERE candidate_id = ? AND to_status = ?
            """,
            (candidate_id, CandidateStatus.DANGLING.value),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["from_status"] == CandidateStatus.PROMOTED.value
    assert "30 days" in cast(str, rows[0]["reason"])


def test_non_promoted_status_ignored(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")

    # DETECTED candidate, aged into the past.
    detected = Candidate(
        candidate_id=new_candidate_id(),
        source_event_id=new_event_id(),
        trace_id=new_trace_id(),
        name="detected",
        description="detected",
        content_hash="hash-detected",
    )
    store.create_candidate(detected)
    _age_candidate(store, detected.candidate_id, days_ago=90)

    # CANDIDATE status, aged.
    candidate_only = Candidate(
        candidate_id=new_candidate_id(),
        source_event_id=new_event_id(),
        trace_id=new_trace_id(),
        name="cand",
        description="cand",
        content_hash="hash-cand",
    )
    store.create_candidate(candidate_only)
    store.transition_candidate(
        candidate_only.candidate_id, CandidateStatus.CANDIDATE, new_event_id(), "review"
    )
    _age_candidate(store, candidate_only.candidate_id, days_ago=90)

    # APPROVED status, aged.
    approved = Candidate(
        candidate_id=new_candidate_id(),
        source_event_id=new_event_id(),
        trace_id=new_trace_id(),
        name="approved",
        description="approved",
        content_hash="hash-approved",
    )
    store.create_candidate(approved)
    store.transition_candidate(
        approved.candidate_id, CandidateStatus.CANDIDATE, new_event_id(), "review"
    )
    store.transition_candidate(
        approved.candidate_id, CandidateStatus.APPROVED, new_event_id(), "approved"
    )
    _age_candidate(store, approved.candidate_id, days_ago=90)

    # REJECTED status, aged.
    rejected = Candidate(
        candidate_id=new_candidate_id(),
        source_event_id=new_event_id(),
        trace_id=new_trace_id(),
        name="rejected",
        description="rejected",
        content_hash="hash-rejected",
    )
    store.create_candidate(rejected)
    store.transition_candidate(
        rejected.candidate_id, CandidateStatus.CANDIDATE, new_event_id(), "review"
    )
    store.transition_candidate(
        rejected.candidate_id, CandidateStatus.REJECTED, new_event_id(), "rejected"
    )
    _age_candidate(store, rejected.candidate_id, days_ago=90)

    result = store.mark_dangling_candidates(days=30)

    assert result == []
    for cid in (
        detected.candidate_id,
        candidate_only.candidate_id,
        approved.candidate_id,
        rejected.candidate_id,
    ):
        record = store.get_candidate(cid)
        assert record is not None
        assert record["status"] != CandidateStatus.DANGLING.value
