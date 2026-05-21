"""End-to-end hook flow tests: pre → cache → post → store.

These tests exercise the full behaviour path through registry registration,
hook invocation via the public FakeHermesContext.invoke_hook contract, and
observable outcomes in the SQLite store.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast

from hermes_skill_guard.config import EnforcementConfig, GuardConfig
from hermes_skill_guard.context import SkillGuardContext
from hermes_skill_guard.hermes.adapter import HermesAdapter
from hermes_skill_guard.ids import new_candidate_id, new_event_id, new_trace_id
from hermes_skill_guard.registry import register_intents
from hermes_skill_guard.runtime import TraceCache
from hermes_skill_guard.schemas import Candidate, CandidateStatus, DecisionValue
from hermes_skill_guard.storage.repository import StateStore

# FakeHermesContext is defined in tests/conftest.py and auto-imported by pytest.


def _make_context(
    tmp_path: Path, *, dry_run: bool = True, mode: str = "audit"
) -> SkillGuardContext:
    config = GuardConfig(
        dry_run=dry_run,
        state_dir=tmp_path,
        enforcement=EnforcementConfig(mode=mode),
    )
    return SkillGuardContext(
        config=config,
        store=StateStore(config.state_db, config.events),
        trace_cache=TraceCache(config.trace_cache),
        logger=logging.getLogger("test"),
    )


class TestPreflightHookFlow:
    """Tests for pre_tool_call hook behaviour."""

    def test_pre_hook_puts_decision_in_trace_cache(self, tmp_path: Path, fake_ctx: Any) -> None:
        ctx = _make_context(tmp_path)

        register_intents(HermesAdapter(fake_ctx), ctx)

        fake_ctx.invoke_hook(
            "pre_tool_call",
            tool_name="skill_manage",
            args={"action": "create", "name": "test-skill", "content": "some content"},
            tool_call_id="call_001",
        )

        decision = ctx.trace_cache.pop("call_001")
        assert decision is not None
        assert decision.tool_name == "skill_manage"
        assert decision.skill_name == "test-skill"

    def test_pre_hook_allows_non_skill_manage(self, tmp_path: Path, fake_ctx: Any) -> None:
        ctx = _make_context(tmp_path)

        register_intents(HermesAdapter(fake_ctx), ctx)

        fake_ctx.invoke_hook(
            "pre_tool_call",
            tool_name="read_file",
            args={"path": "/tmp/foo"},
            tool_call_id="call_002",
        )

        decision = ctx.trace_cache.pop("call_002")
        assert decision is not None
        assert decision.decision == DecisionValue.ALLOW

    def test_pre_hook_without_tool_call_id(self, tmp_path: Path, fake_ctx: Any) -> None:
        """When Hermes omits tool_call_id, hook should not crash."""
        ctx = _make_context(tmp_path)

        register_intents(HermesAdapter(fake_ctx), ctx)

        fake_ctx.invoke_hook(
            "pre_tool_call",
            tool_name="skill_manage",
            args={"action": "create", "name": "x"},
        )

        # trace_cache should be empty since no tool_call_id to key on
        assert ctx.trace_cache.pop("anything") is None
        assert len(fake_ctx.hook_calls("pre_tool_call")) == 1


class TestCaptureHookFlow:
    """Tests for post_tool_call hook behaviour including auto-candidate creation."""

    def test_post_hook_creates_event_and_audit(self, tmp_path: Path, fake_ctx: Any) -> None:
        ctx = _make_context(tmp_path)

        register_intents(HermesAdapter(fake_ctx), ctx)

        # Pre then post
        fake_ctx.invoke_hook(
            "pre_tool_call",
            tool_name="skill_manage",
            args={"action": "create", "name": "x", "content": "y"},
            tool_call_id="call_003",
        )
        fake_ctx.invoke_hook(
            "post_tool_call",
            tool_name="skill_manage",
            args={"action": "create", "name": "x", "content": "y"},
            result='{"ok": true}',
            tool_call_id="call_003",
            duration_ms=50,
        )

        summary = ctx.store.summary()
        assert summary["events"] == 1
        assert summary["audit_log"] == 1

    def test_post_hook_does_not_create_candidate_on_warn(
        self, tmp_path: Path, fake_ctx: Any
    ) -> None:
        """WARN decisions stay audit-only; CANDIDATE decisions create candidates."""
        ctx = _make_context(tmp_path, dry_run=True, mode="audit")

        register_intents(HermesAdapter(fake_ctx), ctx)

        fake_ctx.invoke_hook(
            "pre_tool_call",
            tool_name="skill_manage",
            args={"action": "create", "name": "my-skill", "content": "skill body here"},
            tool_call_id="call_004",
        )
        fake_ctx.invoke_hook(
            "post_tool_call",
            tool_name="skill_manage",
            args={"action": "create", "name": "my-skill", "content": "skill body here"},
            result='{"ok": true}',
            tool_call_id="call_004",
            duration_ms=100,
        )

        candidates = ctx.store.list_candidates()
        assert candidates == []
        summary = ctx.store.summary()
        assert summary["audit_log"] == 1

    def test_post_hook_no_candidate_on_allow(self, tmp_path: Path, fake_ctx: Any) -> None:
        """Non-skill_manage calls should not create candidates."""
        ctx = _make_context(tmp_path)

        register_intents(HermesAdapter(fake_ctx), ctx)

        fake_ctx.invoke_hook(
            "pre_tool_call",
            tool_name="read_file",
            args={"path": "/tmp/foo"},
            tool_call_id="call_005",
        )
        fake_ctx.invoke_hook(
            "post_tool_call",
            tool_name="read_file",
            args={"path": "/tmp/foo"},
            result="file contents",
            tool_call_id="call_005",
            duration_ms=10,
        )

        assert len(ctx.store.list_candidates()) == 0
        summary = ctx.store.summary()
        assert summary["events"] == 1

    def test_post_hook_trace_cache_miss(self, tmp_path: Path, fake_ctx: Any) -> None:
        """Post without pre should still record event but not audit."""
        ctx = _make_context(tmp_path)

        register_intents(HermesAdapter(fake_ctx), ctx)

        fake_ctx.invoke_hook(
            "post_tool_call",
            tool_name="skill_manage",
            args={"action": "create", "name": "x", "content": "short"},
            result='{"ok": true}',
            tool_call_id="call_no_pre",
            duration_ms=5,
        )

        summary = ctx.store.summary()
        assert summary["events"] == 1
        assert summary["audit_log"] == 0
        counters = cast(dict[str, object], summary["counters"])
        assert counters["trace_cache_miss_count"] == 1

    def test_post_hook_with_empty_args(self, tmp_path: Path, fake_ctx: Any) -> None:
        """Hook should not crash when args is missing or not a dict."""
        ctx = _make_context(tmp_path, dry_run=True)

        register_intents(HermesAdapter(fake_ctx), ctx)

        fake_ctx.invoke_hook(
            "pre_tool_call",
            tool_name="skill_manage",
            args={"action": "create", "name": "x", "content": "short"},
            tool_call_id="call_006",
        )
        # Pass None for args (edge case from real Hermes)
        fake_ctx.invoke_hook(
            "post_tool_call",
            tool_name="skill_manage",
            args=None,
            result='{"ok": true}',
            tool_call_id="call_006",
            duration_ms=5,
        )

        # Should not crash; candidate may be created with limited info
        summary = ctx.store.summary()
        assert summary["events"] == 1


class TestPromotionFlow:
    """Tests for the full promotion workflow via tools."""

    def test_promote_tool_transitions_approved_to_promoted(
        self, tmp_path: Path, fake_ctx: Any
    ) -> None:
        ctx = _make_context(tmp_path)

        register_intents(HermesAdapter(fake_ctx), ctx)

        # Create a candidate directly in storage
        cid = new_candidate_id()
        ctx.store.create_candidate(
            Candidate(
                candidate_id=cid,
                source_event_id=new_event_id(),
                trace_id=new_trace_id(),
                name="test",
                description="test candidate",
                content_hash="hash",
                status=CandidateStatus.APPROVED,
            )
        )

        # Use the registered tool handler (public contract)
        result = fake_ctx.tools["skill_guard_promote"]["handler"]({"candidate_id": cid})
        parsed = json.loads(result)

        assert parsed["ok"] is True
        assert parsed["status"] == "pending_promotion"
        assert parsed["tool_name"] == "skill_manage"
        assert parsed["tool_args"]["skill_guard_promotion_attempt_id"] == parsed["attempt_id"]

        rows = ctx.store.list_candidates()
        assert rows[0]["status"] == "approved"
        attempts = ctx.store.list_promotion_attempts()
        assert len(attempts) == 1
        assert attempts[0]["candidate_id"] == cid
        assert attempts[0]["status"] == "pending"

    def test_promote_tool_fails_for_nonexistent(self, tmp_path: Path, fake_ctx: Any) -> None:
        ctx = _make_context(tmp_path)

        register_intents(HermesAdapter(fake_ctx), ctx)

        result = fake_ctx.tools["skill_guard_promote"]["handler"]({"candidate_id": "nonexistent"})
        parsed = json.loads(result)

        assert parsed["ok"] is False
        assert "not found" in parsed["error"]

    def test_promote_tool_fails_for_invalid_transition(self, tmp_path: Path, fake_ctx: Any) -> None:
        ctx = _make_context(tmp_path)

        register_intents(HermesAdapter(fake_ctx), ctx)

        cid = new_candidate_id()
        ctx.store.create_candidate(
            Candidate(
                candidate_id=cid,
                source_event_id=new_event_id(),
                trace_id=new_trace_id(),
                name="test",
                description="test",
                content_hash="hash",
                status=CandidateStatus.DETECTED,  # Not approved
            )
        )

        result = fake_ctx.tools["skill_guard_promote"]["handler"]({"candidate_id": cid})
        parsed = json.loads(result)

        assert parsed["ok"] is False
        assert parsed["error"] == "candidate must be approved before promotion"
