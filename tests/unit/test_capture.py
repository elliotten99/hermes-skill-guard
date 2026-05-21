"""Unit tests for capture intent helpers and hook behaviour.

Targets uncovered branches in capture.py:
- post_tool_call exception handler (lines 82-84)
- _maybe_create_candidate full body (lines 106-134)
- _result_succeeded across all return paths (lines 138-147)
- _maybe_finalize_promotion early returns and completion (lines 159, 163-164, 172-174)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from hermes_skill_guard.config import EnforcementConfig, GuardConfig
from hermes_skill_guard.context import SkillGuardContext
from hermes_skill_guard.hermes.adapter import HermesAdapter
from hermes_skill_guard.ids import new_candidate_id, new_event_id, new_trace_id
from hermes_skill_guard.intents.capture import (
    CaptureIntent,
    _maybe_create_candidate,
    _maybe_finalize_promotion,
    _result_succeeded,
)
from hermes_skill_guard.registry import register_intents
from hermes_skill_guard.runtime import TraceCache
from hermes_skill_guard.schemas import (
    Candidate,
    CandidateStatus,
    Confidence,
    Decision,
    DecisionValue,
    EnforcementMode,
    EventRecord,
    PromotionAttempt,
    PromotionAttemptStatus,
)
from hermes_skill_guard.storage.repository import StateStore


def _make_context(
    tmp_path: Path, *, dry_run: bool = False, mode: str = "candidate"
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


# ---------------------------------------------------------------------------
# _result_succeeded
# ---------------------------------------------------------------------------


class TestResultSucceeded:
    def test_dict_ok_true(self) -> None:
        assert _result_succeeded({"ok": True}) is True

    def test_dict_no_explicit_ok(self) -> None:
        assert _result_succeeded({"data": "x"}) is True

    def test_dict_ok_false(self) -> None:
        assert _result_succeeded({"ok": False}) is False

    def test_dict_with_error(self) -> None:
        assert _result_succeeded({"error": "boom"}) is False

    def test_string_valid_json_ok(self) -> None:
        assert _result_succeeded('{"ok": true}') is True

    def test_string_valid_json_error(self) -> None:
        assert _result_succeeded('{"ok": false, "error": "oops"}') is False

    def test_string_invalid_json_without_error(self) -> None:
        assert _result_succeeded("done") is True

    def test_string_invalid_json_with_error_substring(self) -> None:
        assert _result_succeeded("ERROR: something went wrong") is False

    def test_string_parses_to_non_dict(self) -> None:
        # JSON list — falls through to "result is not None" check
        assert _result_succeeded("[1, 2, 3]") is True

    def test_none_returns_false(self) -> None:
        assert _result_succeeded(None) is False

    def test_other_truthy(self) -> None:
        assert _result_succeeded(42) is True


# ---------------------------------------------------------------------------
# _maybe_create_candidate
# ---------------------------------------------------------------------------


class TestMaybeCreateCandidate:
    def _decision(
        self,
        *,
        decision_value: DecisionValue = DecisionValue.CANDIDATE,
        tool_name: str = "skill_manage",
        skill_name: str | None = "my-skill",
    ) -> Decision:
        return Decision(
            event_id=new_event_id(),
            trace_id=new_trace_id(),
            decision=decision_value,
            confidence=Confidence.HIGH,
            reasons=["bad name"],
            rule_ids=["test.rule"],
            tool_name=tool_name,
            skill_name=skill_name,
            dry_run=False,
            enforcement_mode=EnforcementMode.CANDIDATE,
        )

    def test_no_decision_returns_early(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        _maybe_create_candidate(ctx, None, {}, "ev1", "tr1")
        assert ctx.store.list_candidates() == []

    def test_non_candidate_decision_skipped(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        dec = self._decision(decision_value=DecisionValue.ALLOW)
        _maybe_create_candidate(ctx, dec, {"args": {"name": "x"}}, "ev1", "tr1")
        assert ctx.store.list_candidates() == []

    def test_non_skill_manage_tool_skipped(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        dec = self._decision(tool_name="other_tool")
        _maybe_create_candidate(ctx, dec, {"args": {"name": "x"}}, "ev1", "tr1")
        assert ctx.store.list_candidates() == []

    def test_non_dict_args_skipped(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        dec = self._decision()
        _maybe_create_candidate(ctx, dec, {"args": "not a dict"}, "ev1", "tr1")
        assert ctx.store.list_candidates() == []

    def test_creates_candidate_for_skill_manage_candidate(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        dec = self._decision()
        data = {
            "args": {
                "name": "my-skill",
                "description": "does things",
                "content": "skill body content",
            },
            "session_id": "sess-1",
            "task_id": "task-1",
            "tool_call_id": "call-1",
        }
        _maybe_create_candidate(ctx, dec, data, "ev1", "tr1")

        rows = ctx.store.list_candidates()
        assert len(rows) == 1
        assert rows[0]["name"] == "my-skill"
        counters = ctx.store.summary()["counters"]
        assert isinstance(counters, dict)
        assert counters.get("auto_candidate_created") == 1

    def test_uses_tool_args_alias(self, tmp_path: Path) -> None:
        """args lookup falls back to tool_args when args missing."""
        ctx = _make_context(tmp_path)
        dec = self._decision()
        data = {
            "tool_args": {"name": "alt-skill", "content": "body"},
        }
        _maybe_create_candidate(ctx, dec, data, "ev1", "tr1")
        rows = ctx.store.list_candidates()
        assert len(rows) == 1
        assert rows[0]["name"] == "alt-skill"

    def test_duplicate_source_event_not_recreated(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        # Pre-populate a candidate with the same source_event_id
        existing = Candidate(
            candidate_id=new_candidate_id(),
            source_event_id="ev1",
            trace_id="tr1",
            name="existing",
            description="",
            content_hash="hash",
            status=CandidateStatus.DETECTED,
        )
        ctx.store.create_candidate(existing)

        dec = self._decision()
        _maybe_create_candidate(
            ctx,
            dec,
            {"args": {"name": "my-skill", "content": "skill body"}},
            "ev1",
            "tr1",
        )
        # Still just one candidate
        assert len(ctx.store.list_candidates()) == 1


# ---------------------------------------------------------------------------
# _maybe_finalize_promotion
# ---------------------------------------------------------------------------


class TestMaybeFinalizePromotion:
    def test_wrong_tool_name_skipped(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        _maybe_finalize_promotion(ctx, {"tool_name": "not_skill_manage"}, "ev1")
        # The function must short-circuit before recording any promotion
        # state — wrong tool name is not our concern.
        assert ctx.store.list_promotion_attempts() == []

    def test_non_dict_args_skipped(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        _maybe_finalize_promotion(ctx, {"tool_name": "skill_manage", "args": "bad"}, "ev1")
        # Malformed args must be ignored without producing any state.
        assert ctx.store.list_promotion_attempts() == []

    def test_no_attempt_and_no_skill_name_returns(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        # Args dict has neither attempt_id nor a name we can resolve
        _maybe_finalize_promotion(
            ctx,
            {"tool_name": "skill_manage", "args": {"action": "create"}},
            "ev1",
        )

    def test_no_attempt_skill_name_no_pending(self, tmp_path: Path) -> None:
        """skill_name resolves but no pending promotion exists -> early return."""
        ctx = _make_context(tmp_path)
        _maybe_finalize_promotion(
            ctx,
            {
                "tool_name": "skill_manage",
                "args": {"action": "create", "name": "unknown-skill"},
            },
            "ev1",
        )

    def _seed_approved_candidate_and_attempt(
        self, ctx: SkillGuardContext, skill_name: str = "promoted-skill"
    ) -> tuple[str, str]:
        cid = new_candidate_id()
        ctx.store.create_candidate(
            Candidate(
                candidate_id=cid,
                source_event_id=new_event_id(),
                trace_id=new_trace_id(),
                name=skill_name,
                description="",
                content_hash="hash",
                status=CandidateStatus.APPROVED,
            )
        )
        attempt_id = "attempt-1"
        ctx.store.create_promotion_attempt(
            PromotionAttempt(
                attempt_id=attempt_id,
                candidate_id=cid,
                trace_id=new_trace_id(),
                tool_call_id="tc-1",
                skill_name=skill_name,
                skill_manage_args={"action": "create", "name": skill_name},
                status=PromotionAttemptStatus.PENDING,
            )
        )
        return cid, attempt_id

    def test_finalize_via_attempt_id_success(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        _cid, attempt_id = self._seed_approved_candidate_and_attempt(ctx)
        event_id = self._record_event(ctx, "ev-final")

        _maybe_finalize_promotion(
            ctx,
            {
                "tool_name": "skill_manage",
                "args": {
                    "action": "create",
                    "name": "promoted-skill",
                    "skill_guard_promotion_attempt_id": attempt_id,
                },
                "result": '{"ok": true}',
            },
            event_id,
        )
        attempts = ctx.store.list_promotion_attempts()
        assert len(attempts) == 1
        assert attempts[0]["status"] == PromotionAttemptStatus.SUCCEEDED.value

    def test_finalize_via_skill_name_lookup_failure(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        _cid, _attempt_id = self._seed_approved_candidate_and_attempt(ctx)
        event_id = self._record_event(ctx, "ev-final")

        _maybe_finalize_promotion(
            ctx,
            {
                "tool_name": "skill_manage",
                "args": {"action": "create", "name": "promoted-skill"},
                "result": '{"ok": false, "error": "nope"}',
                "error_type": "ValidationError",
            },
            event_id,
        )
        attempts = ctx.store.list_promotion_attempts()
        assert attempts[0]["status"] == PromotionAttemptStatus.FAILED.value
        assert attempts[0]["error"] == "ValidationError"

    def test_finalize_unknown_attempt_id_falls_back_to_skill_name(self, tmp_path: Path) -> None:
        """When attempt_id doesn't match, fall back to skill name lookup."""
        ctx = _make_context(tmp_path)
        _cid, _attempt_id = self._seed_approved_candidate_and_attempt(ctx)
        event_id = self._record_event(ctx, "ev-final")

        _maybe_finalize_promotion(
            ctx,
            {
                "tool_name": "skill_manage",
                "args": {
                    "action": "create",
                    "name": "promoted-skill",
                    "skill_guard_promotion_attempt_id": "non-existent-attempt",
                },
                "result": '{"ok": true}',
            },
            event_id,
        )
        attempts = ctx.store.list_promotion_attempts()
        assert attempts[0]["status"] == PromotionAttemptStatus.SUCCEEDED.value

    @staticmethod
    def _record_event(ctx: SkillGuardContext, event_id: str) -> str:
        ctx.store.record_event(
            EventRecord(
                event_id=event_id,
                trace_id="trace-finalize",
                parent_event_id=None,
                event_type="post_tool_call",
                tool_name="skill_manage",
                skill_name="promoted-skill",
                payload_summary={},
                payload_hash="hash",
                redaction_applied=False,
                redaction_failed=False,
            )
        )
        return event_id


# ---------------------------------------------------------------------------
# CaptureIntent.post_tool_call exception path
# ---------------------------------------------------------------------------


class _ExplodingStore:
    """A store stand-in whose record_event raises, forcing the except branch."""

    def __init__(self, real: StateStore) -> None:
        self._real = real
        self.counters: dict[str, int] = {}

    def record_event(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("boom")

    def increment_counter(self, name: str, amount: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + amount

    def __getattr__(self, item: str) -> Any:
        # Delegate everything else to the real store
        return getattr(self._real, item)


class TestCaptureHookExceptionPath:
    def test_post_tool_call_swallows_exception_and_bumps_counter(
        self, tmp_path: Path, fake_ctx: Any
    ) -> None:
        """When inner processing raises, counters are bumped and no exception propagates."""
        real = StateStore(tmp_path / "state.db")
        ctx_orig = _make_context(tmp_path)
        exploding = _ExplodingStore(real)

        # Build a context with a store that raises on record_event
        bad_ctx = SkillGuardContext(
            config=ctx_orig.config,
            store=exploding,  # type: ignore[arg-type]
            trace_cache=ctx_orig.trace_cache,
            logger=ctx_orig.logger,
        )

        adapter = HermesAdapter(fake_ctx)
        CaptureIntent().register(adapter, bad_ctx)

        # Invoke the registered post_tool_call hook
        fake_ctx.invoke_hook(
            "post_tool_call",
            tool_name="skill_manage",
            args={"action": "create", "name": "x", "content": "y"},
            result='{"ok": true}',
            tool_call_id="tc-x",
            duration_ms=10,
        )

        assert exploding.counters.get("capture_failed_count") == 1
        # Also one counter for the specific exception type
        assert any(k.startswith("post_tool_call_failed:") for k in exploding.counters)


# ---------------------------------------------------------------------------
# Integration: auto-candidate creation through registered hook
# ---------------------------------------------------------------------------


class TestCaptureHookAutoCandidate:
    def test_candidate_mode_creates_auto_candidate(self, tmp_path: Path, fake_ctx: Any) -> None:
        """With enforcement mode=candidate and dry_run=False, short content
        produces a CANDIDATE decision and the post hook creates a candidate."""
        ctx = _make_context(tmp_path, dry_run=False, mode="candidate")
        register_intents(HermesAdapter(fake_ctx), ctx)

        # Short content triggers a violation rule and CANDIDATE decision
        fake_ctx.invoke_hook(
            "pre_tool_call",
            tool_name="skill_manage",
            args={"action": "create", "name": "auto-skill", "content": "tiny"},
            tool_call_id="tc-auto",
        )
        fake_ctx.invoke_hook(
            "post_tool_call",
            tool_name="skill_manage",
            args={"action": "create", "name": "auto-skill", "content": "tiny"},
            result='{"ok": true}',
            tool_call_id="tc-auto",
            duration_ms=12,
        )

        candidates = ctx.store.list_candidates()
        assert len(candidates) == 1
        assert candidates[0]["name"] == "auto-skill"


# Re-use fake_ctx fixture from conftest.py
pytest_plugins: list[str] = []
