from __future__ import annotations

from dataclasses import replace

from hermes_skill_guard.config import EnforcementConfig, GuardConfig
from hermes_skill_guard.policy import PreflightPolicy, ToolCall, _operation, content_hash
from hermes_skill_guard.schemas import Confidence, DecisionValue, EnforcementMode


def _config(*, mode: str = "audit", dry_run: bool = True) -> GuardConfig:
    return GuardConfig(dry_run=dry_run, enforcement=EnforcementConfig(mode=mode))


def test_dry_run_downgrades_candidate_to_warn() -> None:
    decision = PreflightPolicy(GuardConfig()).evaluate(
        ToolCall("skill_manage", {"action": "create", "name": "x", "content": "short"})
    )

    assert decision.decision == DecisionValue.WARN
    assert "lifecycle.dry_run_downgrade" in decision.rule_ids
    assert decision.dry_run is True


def test_non_skill_manage_is_allowed() -> None:
    decision = PreflightPolicy(GuardConfig()).evaluate(ToolCall("read_file", {"path": "x"}))

    assert decision.decision == DecisionValue.ALLOW
    assert "boundary.tool_not_skill_manage" in decision.rule_ids
    assert decision.confidence == Confidence.LOW


def test_operation_helper_returns_none_when_missing() -> None:
    # Covers `_operation` returning None when no recognised key is present.
    assert _operation({"unrelated": "value"}) is None
    # Also covers the case where a key exists but is non-string.
    assert _operation({"action": 123}) is None


def test_skill_manage_non_create_operation_is_allowed() -> None:
    decision = PreflightPolicy(_config()).evaluate(ToolCall("skill_manage", {"action": "list"}))

    assert decision.decision == DecisionValue.ALLOW
    assert "boundary.operation_not_create" in decision.rule_ids


def test_skill_manage_without_recognised_operation_is_allowed() -> None:
    # `_operation` returns None when there is no action/operation/op/command key.
    decision = PreflightPolicy(_config()).evaluate(ToolCall("skill_manage", {"name": "x"}))

    assert decision.decision == DecisionValue.ALLOW
    assert "boundary.operation_not_create" in decision.rule_ids


def test_promotion_attempt_id_short_circuits_to_allow() -> None:
    decision = PreflightPolicy(_config(mode="block", dry_run=False)).evaluate(
        ToolCall(
            "skill_manage",
            {
                "action": "create",
                "name": "x",
                "content": "hello world hello world hello",
                "skill_guard_promotion_attempt_id": "attempt-123",
            },
        )
    )

    assert decision.decision == DecisionValue.ALLOW
    assert "lifecycle.authorized_promotion" in decision.rule_ids
    assert decision.confidence == Confidence.MEDIUM


def test_missing_skill_name_triggers_manifest_rule_in_block_mode() -> None:
    decision = PreflightPolicy(_config(mode="block", dry_run=False)).evaluate(
        ToolCall(
            "skill_manage",
            {"action": "create", "content": "x" * 50},
        )
    )

    assert decision.decision == DecisionValue.BLOCK
    assert "manifest.name_missing" in decision.rule_ids
    assert decision.confidence == Confidence.HIGH


def test_plugin_namespace_in_name_is_flagged() -> None:
    decision = PreflightPolicy(_config(mode="candidate", dry_run=False)).evaluate(
        ToolCall(
            "skill_manage",
            {"action": "create", "name": "plugin:foo", "content": "x" * 50},
        )
    )

    assert decision.decision == DecisionValue.CANDIDATE
    assert "naming.plugin_namespace" in decision.rule_ids


def test_secret_pattern_in_content_is_flagged_and_blocked() -> None:
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    decision = PreflightPolicy(_config(mode="block", dry_run=False)).evaluate(
        ToolCall(
            "skill_manage",
            {
                "action": "create",
                "name": "valid-name",
                "content": f"plenty of context around the secret {secret} stays embedded",
            },
        )
    )

    assert decision.decision == DecisionValue.BLOCK
    assert "safety.secret_pattern" in decision.rule_ids
    # The reasons collection must include the secret-pattern human-readable reason.
    assert any("secret" in reason for reason in decision.reasons)


def test_clean_create_returns_allow_static() -> None:
    decision = PreflightPolicy(_config(mode="block", dry_run=False)).evaluate(
        ToolCall(
            "skill_manage",
            {
                "action": "create",
                "name": "valid-name",
                "content": "this content is comfortably above the twenty character threshold",
            },
        )
    )

    assert decision.decision == DecisionValue.ALLOW
    assert "lifecycle.allow_static" in decision.rule_ids
    assert decision.confidence == Confidence.MEDIUM
    assert decision.enforcement_mode == EnforcementMode.BLOCK


def test_audit_mode_keeps_warn_when_dry_run_is_off() -> None:
    decision = PreflightPolicy(_config(mode="audit", dry_run=False)).evaluate(
        ToolCall(
            "skill_manage",
            {"action": "create", "name": "valid", "content": "too short"},
        )
    )

    assert decision.decision == DecisionValue.WARN
    # No dry-run downgrade rule should fire when dry_run is off.
    assert "lifecycle.dry_run_downgrade" not in decision.rule_ids


def test_candidate_mode_produces_candidate_decision() -> None:
    decision = PreflightPolicy(_config(mode="candidate", dry_run=False)).evaluate(
        ToolCall(
            "skill_manage",
            {"action": "create", "name": "valid", "content": "too short"},
        )
    )

    assert decision.decision == DecisionValue.CANDIDATE


def test_dry_run_downgrades_block_to_warn_with_extra_reason() -> None:
    cfg = _config(mode="block", dry_run=True)
    decision = PreflightPolicy(cfg).evaluate(
        ToolCall(
            "skill_manage",
            {"action": "create", "name": "valid", "content": "too short"},
        )
    )

    assert decision.decision == DecisionValue.WARN
    assert "lifecycle.dry_run_downgrade" in decision.rule_ids
    assert any("dry_run" in reason for reason in decision.reasons)


def test_operation_helper_picks_first_recognised_key() -> None:
    # `_operation` is a deterministic helper; ensure case-insensitive normalisation.
    assert _operation({"command": "CREATE"}) == "create"
    assert _operation({"op": "List"}) == "list"


def test_content_hash_is_stable_and_strips_whitespace() -> None:
    first = content_hash("  hello world  ")
    second = content_hash("hello world")
    assert first == second
    # Different content produces a different hash.
    assert content_hash("hello") != content_hash("world")


def test_decision_is_immutable_dataclass() -> None:
    # Smoke-test the dataclass surface: replacing fields creates a new instance.
    decision = PreflightPolicy(_config()).evaluate(
        ToolCall("skill_manage", {"action": "create", "name": "x", "content": "y" * 50})
    )
    swapped = replace(decision, dry_run=not decision.dry_run)
    assert swapped.dry_run != decision.dry_run
