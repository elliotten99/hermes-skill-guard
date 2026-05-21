from __future__ import annotations

from pathlib import Path
from typing import cast

from hermes_skill_guard.config import EventsConfig
from hermes_skill_guard.ids import new_candidate_id, new_event_id, new_trace_id
from hermes_skill_guard.schemas import Candidate, CandidateStatus, EventRecord
from hermes_skill_guard.storage.repository import StateStore


def test_sqlite_wal_and_event_recording(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    event = EventRecord(
        event_id=new_event_id(),
        trace_id=new_trace_id(),
        parent_event_id=None,
        event_type="test",
        tool_name="skill_manage",
        skill_name="demo",
        payload_summary={"ok": True},
        payload_hash="hash",
        redaction_applied=True,
        redaction_failed=False,
    )

    store.record_event(event)
    summary = store.summary()

    assert store.wal_enabled() is True
    assert summary["events"] == 1


def test_event_rotation_by_row_count(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db", EventsConfig(max_rows=2, rotate_every_n_writes=100))
    for index in range(4):
        store.record_event(
            EventRecord(
                event_id=f"evt_{index}",
                trace_id="trc",
                parent_event_id=None,
                event_type="test",
                tool_name=None,
                skill_name=None,
                payload_summary={},
                payload_hash=str(index),
                redaction_applied=True,
                redaction_failed=False,
            )
        )

    store.rotate_events()

    assert cast(int, store.summary()["events"]) <= 2


def test_candidate_transition_writes_audit(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    candidate = Candidate(
        candidate_id=new_candidate_id(),
        source_event_id=new_event_id(),
        trace_id=new_trace_id(),
        name="demo",
        description="demo candidate",
        content_hash="hash",
    )
    store.create_candidate(candidate)
    store.transition_candidate(
        candidate.candidate_id, CandidateStatus.CANDIDATE, new_event_id(), "test"
    )

    summary = store.summary()
    assert summary["candidates"] == 1
    assert summary["audit_log"] == 1
