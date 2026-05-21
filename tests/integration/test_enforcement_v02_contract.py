from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from hermes_skill_guard.config import EnforcementConfig, GuardConfig
from hermes_skill_guard.context import SkillGuardContext
from hermes_skill_guard.hermes.adapter import HermesAdapter
from hermes_skill_guard.policy import PreflightPolicy, ToolCall
from hermes_skill_guard.registry import register_intents
from hermes_skill_guard.runtime import TraceCache
from hermes_skill_guard.schemas import DecisionValue
from hermes_skill_guard.storage.repository import StateStore


def _make_context(tmp_path: Path, *, dry_run: bool, mode: str) -> SkillGuardContext:
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


def test_candidate_mode_pre_hook_returns_candidate_intercept(tmp_path: Path, fake_ctx: Any) -> None:
    ctx = _make_context(tmp_path, dry_run=False, mode="candidate")
    register_intents(HermesAdapter(fake_ctx), ctx)

    result = fake_ctx.invoke_hook(
        "pre_tool_call",
        tool_name="skill_manage",
        args={"action": "create", "name": "candidate-skill", "content": "short"},
        tool_call_id="candidate-call",
    )

    assert result == {
        "action": "block",
        "message": (
            "skill-guard routed this skill to the candidate queue: "
            "skill content or description is too short"
        ),
    }
    candidates = ctx.store.list_candidates()
    assert len(candidates) == 1
    assert candidates[0]["name"] == "candidate-skill"
    assert candidates[0]["status"] == "detected"


def test_block_mode_pre_hook_returns_hermes_v014_block_action(
    tmp_path: Path, fake_ctx: Any
) -> None:
    ctx = _make_context(tmp_path, dry_run=False, mode="block")
    register_intents(HermesAdapter(fake_ctx), ctx)

    result = fake_ctx.invoke_hook(
        "pre_tool_call",
        tool_name="skill_manage",
        args={"action": "create", "name": "blocked-skill", "content": "short"},
        tool_call_id="block-call",
    )

    assert result == {
        "action": "block",
        "message": (
            "skill-guard blocked this skill creation: skill content or description is too short"
        ),
    }
    assert ctx.store.list_candidates() == []


def test_dry_run_downgrades_candidate_or_block_rule_to_warning() -> None:
    config = GuardConfig(
        dry_run=True,
        enforcement=EnforcementConfig(mode="block"),
    )
    decision = PreflightPolicy(config).evaluate(
        ToolCall(
            "skill_manage",
            {"action": "create", "name": "dry-run-skill", "content": "short"},
        )
    )

    assert decision.decision == DecisionValue.WARN
    assert "lifecycle.dry_run_downgrade" in decision.rule_ids
