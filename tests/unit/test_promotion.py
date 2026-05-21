from __future__ import annotations

from pathlib import Path

import pytest

from hermes_skill_guard.ids import new_candidate_id, new_event_id, new_trace_id
from hermes_skill_guard.schemas import Candidate, CandidateStatus
from hermes_skill_guard.storage.repository import StateStore


def test_promote_approved_candidate(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    candidate = Candidate(
        candidate_id=new_candidate_id(),
        source_event_id=new_event_id(),
        trace_id=new_trace_id(),
        name="demo",
        description="demo candidate",
        content_hash="hash",
        status=CandidateStatus.APPROVED,
    )
    store.create_candidate(candidate)
    store.transition_candidate(
        candidate.candidate_id, CandidateStatus.PROMOTED, new_event_id(), "test promote"
    )

    rows = store.list_candidates()
    assert len(rows) == 1
    assert rows[0]["status"] == "promoted"


def test_promote_non_approved_fails(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    candidate = Candidate(
        candidate_id=new_candidate_id(),
        source_event_id=new_event_id(),
        trace_id=new_trace_id(),
        name="demo",
        description="demo candidate",
        content_hash="hash",
        status=CandidateStatus.DETECTED,
    )
    store.create_candidate(candidate)
    with pytest.raises(ValueError, match="illegal candidate transition"):
        store.transition_candidate(
            candidate.candidate_id, CandidateStatus.PROMOTED, new_event_id(), "test"
        )
