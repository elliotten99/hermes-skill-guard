from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from hermes_skill_guard.config import load_config
from hermes_skill_guard.context import SkillGuardContext
from hermes_skill_guard.ids import new_candidate_id, new_event_id, new_trace_id
from hermes_skill_guard.runtime import TraceCache
from hermes_skill_guard.schemas import (
    Candidate,
    CandidateStatus,
    Confidence,
    RelationType,
    SkillRelation,
)
from hermes_skill_guard.storage.repository import StateStore, utc_now


def _make_context(store: StateStore) -> SkillGuardContext:
    config = load_config()
    return SkillGuardContext(
        config=config,
        store=store,
        trace_cache=TraceCache(config.trace_cache),
        logger=logging.getLogger("test"),
    )


@pytest.fixture
def store(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "state.db")


@pytest.fixture
def candidate_a(store: StateStore) -> Candidate:
    cand = Candidate(
        candidate_id=new_candidate_id(),
        source_event_id=new_event_id(),
        trace_id=new_trace_id(),
        name="skill-a",
        description="first skill",
        content_hash="hash-a",
        status=CandidateStatus.CANDIDATE,
    )
    store.create_candidate(cand)
    return cand


@pytest.fixture
def candidate_b(store: StateStore) -> Candidate:
    cand = Candidate(
        candidate_id=new_candidate_id(),
        source_event_id=new_event_id(),
        trace_id=new_trace_id(),
        name="skill-b",
        description="second skill",
        content_hash="hash-b",
        status=CandidateStatus.CANDIDATE,
    )
    store.create_candidate(cand)
    return cand


class TestAddRelation:
    def test_add_relation_success(
        self, store: StateStore, candidate_a: Candidate, candidate_b: Candidate
    ) -> None:
        relation = SkillRelation(
            relation_id="rel_001",
            source_candidate_id=candidate_a.candidate_id,
            target_candidate_id=candidate_b.candidate_id,
            relation_type=RelationType.DUPLICATE,
            confidence=Confidence.HIGH,
            reasons=["same content hash"],
            created_at=utc_now(),
        )
        store.add_relation(relation)

        rows = store.list_relations()
        assert len(rows) == 1
        assert rows[0]["relation_type"] == "duplicate"
        assert rows[0]["confidence"] == "high"

    def test_add_relation_with_foreign_key_violation(
        self, store: StateStore, candidate_a: Candidate
    ) -> None:
        relation = SkillRelation(
            relation_id="rel_002",
            source_candidate_id=candidate_a.candidate_id,
            target_candidate_id="nonexistent",
            relation_type=RelationType.CONFLICT,
            confidence=Confidence.MEDIUM,
            reasons=["name collision"],
            created_at=utc_now(),
        )
        with pytest.raises(sqlite3.IntegrityError):
            store.add_relation(relation)


class TestListRelations:
    def test_filter_by_source(
        self, store: StateStore, candidate_a: Candidate, candidate_b: Candidate
    ) -> None:
        rel_ab = SkillRelation(
            relation_id="rel_ab",
            source_candidate_id=candidate_a.candidate_id,
            target_candidate_id=candidate_b.candidate_id,
            relation_type=RelationType.DEPENDS_ON,
            confidence=Confidence.HIGH,
            reasons=["a depends on b"],
            created_at=utc_now(),
        )
        store.add_relation(rel_ab)

        rows = store.list_relations(source_candidate_id=candidate_a.candidate_id)
        assert len(rows) == 1
        assert rows[0]["relation_id"] == "rel_ab"

    def test_filter_by_relation_type(
        self, store: StateStore, candidate_a: Candidate, candidate_b: Candidate
    ) -> None:
        rel = SkillRelation(
            relation_id="rel_003",
            source_candidate_id=candidate_a.candidate_id,
            target_candidate_id=candidate_b.candidate_id,
            relation_type=RelationType.SUPERSEDES,
            confidence=Confidence.LOW,
            reasons=["v2 replaces v1"],
            created_at=utc_now(),
        )
        store.add_relation(rel)

        rows = store.list_relations(relation_type=RelationType.SUPERSEDES)
        assert len(rows) == 1
        assert rows[0]["relation_type"] == "supersedes"

    def test_list_empty(self, store: StateStore) -> None:
        rows = store.list_relations()
        assert rows == []


class TestRemoveRelation:
    def test_remove_existing(
        self, store: StateStore, candidate_a: Candidate, candidate_b: Candidate
    ) -> None:
        rel = SkillRelation(
            relation_id="rel_rm",
            source_candidate_id=candidate_a.candidate_id,
            target_candidate_id=candidate_b.candidate_id,
            relation_type=RelationType.RELATED_TO,
            confidence=Confidence.MEDIUM,
            reasons=["related"],
            created_at=utc_now(),
        )
        store.add_relation(rel)
        assert store.remove_relation("rel_rm") is True
        assert store.list_relations() == []

    def test_remove_nonexistent(self, store: StateStore) -> None:
        assert store.remove_relation("rel_noop") is False


class TestFindRelatedCandidates:
    def test_find_related(
        self, store: StateStore, candidate_a: Candidate, candidate_b: Candidate
    ) -> None:
        rel = SkillRelation(
            relation_id="rel_find",
            source_candidate_id=candidate_a.candidate_id,
            target_candidate_id=candidate_b.candidate_id,
            relation_type=RelationType.CONFLICT,
            confidence=Confidence.HIGH,
            reasons=["naming collision"],
            created_at=utc_now(),
        )
        store.add_relation(rel)

        rows = store.find_related_candidates(candidate_a.candidate_id)
        assert len(rows) == 1
        assert rows[0]["target_candidate_id"] == candidate_b.candidate_id

        rows_b = store.find_related_candidates(candidate_b.candidate_id)
        assert len(rows_b) == 1
        assert rows_b[0]["source_candidate_id"] == candidate_a.candidate_id


class TestRelationsIntentHandler:
    def test_add_missing_fields(self, tmp_path: Path) -> None:
        from hermes_skill_guard.intents.relations import _handle_add

        store = StateStore(tmp_path / "state.db")
        ctx = _make_context(store)

        result = json.loads(_handle_add(ctx, {"source_candidate_id": "c1"}))
        assert result["ok"] is False
        assert "target_candidate_id" in result["error"]

    def test_add_invalid_relation_type(
        self, store: StateStore, candidate_a: Candidate, candidate_b: Candidate
    ) -> None:
        from hermes_skill_guard.intents.relations import _handle_add

        ctx = _make_context(store)

        result = json.loads(
            _handle_add(
                ctx,
                {
                    "source_candidate_id": candidate_a.candidate_id,
                    "target_candidate_id": candidate_b.candidate_id,
                    "relation_type": "bogus",
                    "confidence": "high",
                    "reasons": ["test"],
                },
            )
        )
        assert result["ok"] is False
        assert "invalid relation_type" in result["error"]

    def test_add_candidate_not_found(self, store: StateStore, candidate_a: Candidate) -> None:
        from hermes_skill_guard.intents.relations import _handle_add

        ctx = _make_context(store)

        result = json.loads(
            _handle_add(
                ctx,
                {
                    "source_candidate_id": candidate_a.candidate_id,
                    "target_candidate_id": "missing",
                    "relation_type": "duplicate",
                    "confidence": "high",
                    "reasons": ["test"],
                },
            )
        )
        assert result["ok"] is False
        assert "target_candidate_id not found" in result["error"]

    def test_add_success(
        self, store: StateStore, candidate_a: Candidate, candidate_b: Candidate
    ) -> None:
        from hermes_skill_guard.intents.relations import _handle_add

        ctx = _make_context(store)

        result = json.loads(
            _handle_add(
                ctx,
                {
                    "source_candidate_id": candidate_a.candidate_id,
                    "target_candidate_id": candidate_b.candidate_id,
                    "relation_type": "depends_on",
                    "confidence": "medium",
                    "reasons": ["shared dependency"],
                },
            )
        )
        assert result["ok"] is True
        assert result["relation_id"].startswith("rel_")

    def test_list_and_remove(
        self, store: StateStore, candidate_a: Candidate, candidate_b: Candidate
    ) -> None:
        from hermes_skill_guard.intents.relations import _handle_add, _handle_list, _handle_remove

        ctx = _make_context(store)

        add_result = json.loads(
            _handle_add(
                ctx,
                {
                    "source_candidate_id": candidate_a.candidate_id,
                    "target_candidate_id": candidate_b.candidate_id,
                    "relation_type": "related_to",
                    "confidence": "low",
                    "reasons": ["similar domain"],
                },
            )
        )
        rel_id = add_result["relation_id"]

        list_result = json.loads(_handle_list(ctx, {}))
        assert list_result["ok"] is True
        assert len(list_result["relations"]) == 1

        remove_result = json.loads(_handle_remove(ctx, {"relation_id": rel_id}))
        assert remove_result["ok"] is True

        list_after = json.loads(_handle_list(ctx, {}))
        assert list_after["relations"] == []

    def test_remove_missing_id(self, store: StateStore) -> None:
        from hermes_skill_guard.intents.relations import _handle_remove

        ctx = _make_context(store)

        result = json.loads(_handle_remove(ctx, {}))
        assert result["ok"] is False
        assert "relation_id is required" in result["error"]

    def test_unsupported_action(self, store: StateStore) -> None:
        from hermes_skill_guard.intents.relations import RelationsIntent

        ctx = _make_context(store)

        intent = RelationsIntent()
        handler: Any = None

        class FakeAdapter:
            def register_tool(
                self, name: str, h: Any, description: str, schema: Any = None
            ) -> None:
                nonlocal handler
                handler = h

        intent.register(FakeAdapter(), ctx)  # type: ignore[arg-type]
        assert handler is not None
        result = json.loads(handler({"action": "bogus"}))
        assert result["ok"] is False
        assert "unsupported action" in result["error"]
