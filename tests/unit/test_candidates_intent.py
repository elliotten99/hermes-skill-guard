"""Unit tests for the candidates intent handler."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hermes_skill_guard.ids import new_candidate_id, new_event_id, new_trace_id
from hermes_skill_guard.intents.candidates import CandidatesIntent
from hermes_skill_guard.schemas import Candidate, CandidateStatus, EventRecord
from hermes_skill_guard.storage.repository import StateStore


class FakeAdapter:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def register_tool(
        self,
        name: str,
        handler: Any,
        description: str = "",
        schema: dict[str, Any] | None = None,
    ) -> None:
        self.tools[name] = {"handler": handler, "description": description, "schema": schema}


class FakeContext:
    def __init__(self, store: StateStore) -> None:
        self.store = store


@pytest.fixture
def setup(tmp_path: Path) -> tuple[FakeAdapter, FakeContext, Any]:
    store = StateStore(tmp_path / "state.db")
    adapter = FakeAdapter()
    context = FakeContext(store)
    intent = CandidatesIntent()
    intent.register(adapter, context)  # type: ignore[arg-type]
    handler = adapter.tools["skill_guard_candidates"]["handler"]
    return adapter, context, handler


def test_list_empty(setup: tuple[FakeAdapter, FakeContext, Any]) -> None:
    _, _, handler = setup
    result = json.loads(handler({"action": "list"}))
    assert result["ok"] is True
    assert result["candidates"] == []


def test_create_requires_fields(setup: tuple[FakeAdapter, FakeContext, Any]) -> None:
    _, _, handler = setup
    result = json.loads(handler({"action": "create", "event_id": "evt_1"}))
    assert result["ok"] is False
    assert "required" in result["error"]


def test_create_requires_existing_event(setup: tuple[FakeAdapter, FakeContext, Any]) -> None:
    _, _, handler = setup
    result = json.loads(
        handler(
            {
                "action": "create",
                "event_id": "evt_missing",
                "name": "Skill",
                "description": "Desc",
                "content_hash": "hash",
            }
        )
    )
    assert result["ok"] is False
    assert "event not found" in result["error"]


def test_create_success(setup: tuple[FakeAdapter, FakeContext, Any]) -> None:
    _, context, handler = setup
    event_id = new_event_id()
    context.store.record_event(
        EventRecord(
            event_id=event_id,
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
    )
    result = json.loads(
        handler(
            {
                "action": "create",
                "event_id": event_id,
                "name": "Skill",
                "description": "Desc",
                "content_hash": "hash",
                "reasons": ["reason1"],
            }
        )
    )
    assert result["ok"] is True
    assert result["status"] == "detected"
    assert "candidate_id" in result


def test_approve_success(setup: tuple[FakeAdapter, FakeContext, Any]) -> None:
    _, context, handler = setup
    cid = new_candidate_id()
    context.store.create_candidate(
        Candidate(
            candidate_id=cid,
            source_event_id=new_event_id(),
            trace_id=new_trace_id(),
            name="test",
            description="test",
            content_hash="hash",
            status=CandidateStatus.CANDIDATE,
        )
    )
    result = json.loads(handler({"action": "approve", "candidate_id": cid}))
    assert result["ok"] is True
    assert result["status"] == "approved"


def test_archive_success(setup: tuple[FakeAdapter, FakeContext, Any]) -> None:
    _, context, handler = setup
    cid = new_candidate_id()
    context.store.create_candidate(
        Candidate(
            candidate_id=cid,
            source_event_id=new_event_id(),
            trace_id=new_trace_id(),
            name="test",
            description="test",
            content_hash="hash",
            status=CandidateStatus.APPROVED,
        )
    )
    result = json.loads(handler({"action": "archive", "candidate_id": cid}))
    assert result["ok"] is True
    assert result["status"] == "archived"


def test_archive_requires_candidate_id(setup: tuple[FakeAdapter, FakeContext, Any]) -> None:
    _, _, handler = setup
    result = json.loads(handler({"action": "archive"}))
    assert result["ok"] is False
    assert "candidate_id is required" in result["error"]


def test_unsupported_action(setup: tuple[FakeAdapter, FakeContext, Any]) -> None:
    _, _, handler = setup
    result = json.loads(handler({"action": "delete"}))
    assert result["ok"] is False
    assert "unsupported action" in result["error"]


def test_invalid_transition_caught(setup: tuple[FakeAdapter, FakeContext, Any]) -> None:
    _, context, handler = setup
    cid = new_candidate_id()
    context.store.create_candidate(
        Candidate(
            candidate_id=cid,
            source_event_id=new_event_id(),
            trace_id=new_trace_id(),
            name="test",
            description="test",
            content_hash="hash",
            status=CandidateStatus.DETECTED,
        )
    )
    # DETECTED -> REJECTED is not a valid transition
    result = json.loads(handler({"action": "reject", "candidate_id": cid}))
    assert result["ok"] is False
    assert result["error"] == "ValueError"


# --- details action --------------------------------------------------------


def test_details_requires_candidate_id(setup: tuple[FakeAdapter, FakeContext, Any]) -> None:
    _, _, handler = setup
    result = json.loads(handler({"action": "details"}))
    assert result["ok"] is False
    assert "candidate_id is required" in result["error"]


def test_details_not_found(setup: tuple[FakeAdapter, FakeContext, Any]) -> None:
    _, _, handler = setup
    result = json.loads(handler({"action": "details", "candidate_id": "cand_missing"}))
    assert result["ok"] is False
    assert result["error"] == "candidate not found"


def test_details_returns_candidate_and_attempts(
    setup: tuple[FakeAdapter, FakeContext, Any],
) -> None:
    _, context, handler = setup
    cid = new_candidate_id()
    context.store.create_candidate(
        Candidate(
            candidate_id=cid,
            source_event_id=new_event_id(),
            trace_id=new_trace_id(),
            name="alpha",
            description="d",
            content_hash="hash",
            status=CandidateStatus.DETECTED,
        )
    )
    result = json.loads(handler({"action": "details", "candidate_id": cid}))
    assert result["ok"] is True
    assert result["candidate"]["candidate_id"] == cid
    assert result["promotion_attempts"] == []


# --- stage action ----------------------------------------------------------


def test_stage_requires_candidate_id(setup: tuple[FakeAdapter, FakeContext, Any]) -> None:
    _, _, handler = setup
    result = json.loads(handler({"action": "stage"}))
    assert result["ok"] is False
    assert "candidate_id is required" in result["error"]


def test_stage_success(setup: tuple[FakeAdapter, FakeContext, Any]) -> None:
    _, context, handler = setup
    cid = new_candidate_id()
    context.store.create_candidate(
        Candidate(
            candidate_id=cid,
            source_event_id=new_event_id(),
            trace_id=new_trace_id(),
            name="s",
            description="d",
            content_hash="hash",
            status=CandidateStatus.DETECTED,
        )
    )
    result = json.loads(handler({"action": "stage", "candidate_id": cid}))
    assert result["ok"] is True
    assert result["status"] == "candidate"
    assert result["candidate_id"] == cid


def test_stage_unknown_candidate_returns_error(
    setup: tuple[FakeAdapter, FakeContext, Any],
) -> None:
    """Hitting transition_candidate with a missing id should raise KeyError, which
    the stage branch catches and surfaces as an error envelope."""
    _, _, handler = setup
    result = json.loads(handler({"action": "stage", "candidate_id": "cand_missing"}))
    assert result["ok"] is False
    # KeyError repr from transition_candidate(candidate_id) -> str(exc) is repr'd
    assert "cand_missing" in result["error"]


def test_stage_invalid_transition_returns_error(
    setup: tuple[FakeAdapter, FakeContext, Any],
) -> None:
    """APPROVED -> CANDIDATE is not a valid transition; stage must return an error envelope
    without raising."""
    _, context, handler = setup
    cid = new_candidate_id()
    context.store.create_candidate(
        Candidate(
            candidate_id=cid,
            source_event_id=new_event_id(),
            trace_id=new_trace_id(),
            name="s",
            description="d",
            content_hash="hash",
            status=CandidateStatus.APPROVED,
        )
    )
    result = json.loads(handler({"action": "stage", "candidate_id": cid}))
    assert result["ok"] is False
    # ValueError str typically includes the transition or just the message;
    # we just confirm it surfaced from the store rather than crashing.
    assert isinstance(result["error"], str) and result["error"]


# --- archive action: ValueError branch -------------------------------------


def test_archive_invalid_transition_returns_error(
    setup: tuple[FakeAdapter, FakeContext, Any],
) -> None:
    """ARCHIVED has no outgoing transitions, so archiving an already-archived candidate
    must raise ValueError inside transition_candidate; the handler must catch it."""
    _, context, handler = setup
    cid = new_candidate_id()
    context.store.create_candidate(
        Candidate(
            candidate_id=cid,
            source_event_id=new_event_id(),
            trace_id=new_trace_id(),
            name="s",
            description="d",
            content_hash="hash",
            status=CandidateStatus.ARCHIVED,
        )
    )
    result = json.loads(handler({"action": "archive", "candidate_id": cid}))
    assert result["ok"] is False
    assert isinstance(result["error"], str) and result["error"]


# --- approve/reject KeyError outer-catch -----------------------------------


def test_approve_missing_candidate_id_raises_keyerror_caught(
    setup: tuple[FakeAdapter, FakeContext, Any],
) -> None:
    """approve/reject use args['candidate_id'] (direct indexing). Omitting it raises
    KeyError, which the outer except branch maps to 'candidate not found'."""
    _, _, handler = setup
    result = json.loads(handler({"action": "approve"}))
    assert result["ok"] is False
    assert result["error"] == "candidate not found"


def test_approve_unknown_candidate_id_returns_keyerror_envelope(
    setup: tuple[FakeAdapter, FakeContext, Any],
) -> None:
    """transition_candidate raises KeyError when the id is unknown; this propagates
    to the outer except KeyError block and yields 'candidate not found'."""
    _, _, handler = setup
    result = json.loads(handler({"action": "approve", "candidate_id": "cand_missing"}))
    assert result["ok"] is False
    assert result["error"] == "candidate not found"
