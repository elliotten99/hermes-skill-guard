from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hermes_skill_guard.ids import new_candidate_id, new_event_id, new_trace_id
from hermes_skill_guard.plugin import register
from hermes_skill_guard.schemas import Candidate, CandidateStatus
from hermes_skill_guard.storage.repository import StateStore


class TestRelationsToolFlow:
    def test_add_list_remove_relation(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_ctx: Any,
    ) -> None:
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))

        register(fake_ctx)
        assert "skill_guard_relations" in fake_ctx.tools

        handler = fake_ctx.tools["skill_guard_relations"]["handler"]

        # Seed two candidates
        store = StateStore(tmp_path / "state.db")
        cand_a = Candidate(
            candidate_id=new_candidate_id(),
            source_event_id=new_event_id(),
            trace_id=new_trace_id(),
            name="skill-a",
            description="first",
            content_hash="hash-a",
            status=CandidateStatus.CANDIDATE,
        )
        cand_b = Candidate(
            candidate_id=new_candidate_id(),
            source_event_id=new_event_id(),
            trace_id=new_trace_id(),
            name="skill-b",
            description="second",
            content_hash="hash-b",
            status=CandidateStatus.CANDIDATE,
        )
        store.create_candidate(cand_a)
        store.create_candidate(cand_b)

        # Add relation
        add_raw = handler(
            {
                "action": "add",
                "source_candidate_id": cand_a.candidate_id,
                "target_candidate_id": cand_b.candidate_id,
                "relation_type": "duplicate",
                "confidence": "high",
                "reasons": ["identical content"],
            }
        )
        add_result = json.loads(add_raw)
        assert add_result["ok"] is True
        relation_id = add_result["relation_id"]
        assert relation_id.startswith("rel_")

        # List relations
        list_raw = handler({"action": "list"})
        list_result = json.loads(list_raw)
        assert list_result["ok"] is True
        assert len(list_result["relations"]) == 1
        assert list_result["relations"][0]["relation_type"] == "duplicate"

        # List filtered by source
        list_src_raw = handler({"action": "list", "source_candidate_id": cand_a.candidate_id})
        list_src_result = json.loads(list_src_raw)
        assert list_src_result["ok"] is True
        assert len(list_src_result["relations"]) == 1

        # Remove relation
        remove_raw = handler({"action": "remove", "relation_id": relation_id})
        remove_result = json.loads(remove_raw)
        assert remove_result["ok"] is True
        assert remove_result["relation_id"] == relation_id

        # Verify empty after removal
        list_after_raw = handler({"action": "list"})
        list_after = json.loads(list_after_raw)
        assert list_after["ok"] is True
        assert list_after["relations"] == []

    def test_add_relation_missing_target(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_ctx: Any,
    ) -> None:
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))
        register(fake_ctx)
        handler = fake_ctx.tools["skill_guard_relations"]["handler"]

        result = json.loads(
            handler(
                {
                    "action": "add",
                    "source_candidate_id": "c1",
                    "relation_type": "conflict",
                    "confidence": "medium",
                    "reasons": ["test"],
                }
            )
        )
        assert result["ok"] is False
        assert "target_candidate_id" in result["error"]

    def test_add_relation_candidate_not_found(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_ctx: Any,
    ) -> None:
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))
        register(fake_ctx)
        handler = fake_ctx.tools["skill_guard_relations"]["handler"]

        result = json.loads(
            handler(
                {
                    "action": "add",
                    "source_candidate_id": "missing",
                    "target_candidate_id": "also_missing",
                    "relation_type": "depends_on",
                    "confidence": "low",
                    "reasons": ["test"],
                }
            )
        )
        assert result["ok"] is False
        assert "source_candidate_id not found" in result["error"]

    def test_remove_nonexistent_relation(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_ctx: Any,
    ) -> None:
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))
        register(fake_ctx)
        handler = fake_ctx.tools["skill_guard_relations"]["handler"]

        result = json.loads(handler({"action": "remove", "relation_id": "rel_nonexistent"}))
        assert result["ok"] is False
        assert "relation not found" in result["error"]

    def test_invalid_relation_type(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_ctx: Any,
    ) -> None:
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))
        register(fake_ctx)
        handler = fake_ctx.tools["skill_guard_relations"]["handler"]

        result = json.loads(
            handler(
                {
                    "action": "add",
                    "source_candidate_id": "c1",
                    "target_candidate_id": "c2",
                    "relation_type": "bogus",
                    "confidence": "high",
                    "reasons": ["test"],
                }
            )
        )
        assert result["ok"] is False
        assert "invalid relation_type" in result["error"]

    def test_all_relation_types_supported(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_ctx: Any,
    ) -> None:
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))
        register(fake_ctx)
        handler = fake_ctx.tools["skill_guard_relations"]["handler"]

        store = StateStore(tmp_path / "state.db")
        candidates: list[Candidate] = []
        for idx in range(6):
            cand = Candidate(
                candidate_id=new_candidate_id(),
                source_event_id=new_event_id(),
                trace_id=new_trace_id(),
                name=f"skill-{idx}",
                description=f"desc {idx}",
                content_hash=f"hash-{idx}",
                status=CandidateStatus.CANDIDATE,
            )
            store.create_candidate(cand)
            candidates.append(cand)

        types = ["duplicate", "conflict", "supersedes", "depends_on", "related_to"]
        for idx, rel_type in enumerate(types):
            src = candidates[idx]
            tgt = candidates[idx + 1]
            raw = handler(
                {
                    "action": "add",
                    "source_candidate_id": src.candidate_id,
                    "target_candidate_id": tgt.candidate_id,
                    "relation_type": rel_type,
                    "confidence": "high",
                    "reasons": [f"reason for {rel_type}"],
                }
            )
            res = json.loads(raw)
            assert res["ok"] is True, f"failed for {rel_type}: {res}"

        list_raw = handler({"action": "list"})
        list_result = json.loads(list_raw)
        assert list_result["ok"] is True
        assert len(list_result["relations"]) == 5
