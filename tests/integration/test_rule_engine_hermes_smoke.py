"""Hermes smoke tests for the T10 configurable rule engine integration.

These tests exercise the full plugin registration → RuleEngine evaluation →
Decision derivation path through the FakeHermesContext contract.  They verify
that:

* The rule engine is loaded at plugin registration time
* Default rules produce the same rule_ids as the pre-T10 hard-coded checks
* User rule files can override defaults and affect decisions
* Boundary guards (tool_name, operation, promotion) still short-circuit
* Enforcement mode mapping and dry_run downgrade still apply
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hermes_skill_guard.config import EnforcementConfig, GuardConfig
from hermes_skill_guard.context import SkillGuardContext
from hermes_skill_guard.hermes.adapter import HermesAdapter
from hermes_skill_guard.registry import register_intents
from hermes_skill_guard.runtime import TraceCache
from hermes_skill_guard.storage.repository import StateStore


def _make_guard_ctx(
    tmp_path: Path, *, mode: str = "audit", dry_run: bool = False
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
        logger=None,  # type: ignore[arg-type]
    )


class TestRuleEngineIntegrationSmoke:
    """Smoke tests for RuleEngine inside the Hermes plugin lifecycle."""

    def test_preflight_returns_default_rule_ids(
        self, tmp_path: Path, fake_ctx: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A short skill name triggers the rule-engine path and returns the
        same rule_ids that the old hard-coded policy used to produce."""
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))

        ctx = _make_guard_ctx(tmp_path)
        register_intents(HermesAdapter(fake_ctx), ctx)

        raw = fake_ctx.tools["skill_guard_preflight"]["handler"](
            {
                "tool_name": "skill_manage",
                "args": {"action": "create", "name": "", "content": "x"},
            }
        )
        result = json.loads(raw)

        assert result["ok"] is True
        assert "manifest.name_missing" in result["decision"]["rule_ids"]
        assert "manifest.description_too_short" in result["decision"]["rule_ids"]

    def test_preflight_secret_pattern_rule_fires(
        self, tmp_path: Path, fake_ctx: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Content containing a secret pattern triggers the engine's
        ``safety.secret_pattern`` rule."""
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))

        ctx = _make_guard_ctx(tmp_path)
        register_intents(HermesAdapter(fake_ctx), ctx)

        raw = fake_ctx.tools["skill_guard_preflight"]["handler"](
            {
                "tool_name": "skill_manage",
                "args": {
                    "action": "create",
                    "name": "leaky",
                    "content": "api key: sk-123456789012345678901234567890",
                },
            }
        )
        result = json.loads(raw)

        assert result["ok"] is True
        assert "safety.secret_pattern" in result["decision"]["rule_ids"]

    def test_preflight_plugin_namespace_rule_fires(
        self, tmp_path: Path, fake_ctx: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A name containing ``:`` triggers ``naming.plugin_namespace``."""
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))

        ctx = _make_guard_ctx(tmp_path)
        register_intents(HermesAdapter(fake_ctx), ctx)

        raw = fake_ctx.tools["skill_guard_preflight"]["handler"](
            {
                "tool_name": "skill_manage",
                "args": {
                    "action": "create",
                    "name": "plugin:foo",
                    "content": "some reasonably long description here",
                },
            }
        )
        result = json.loads(raw)

        assert result["ok"] is True
        assert "naming.plugin_namespace" in result["decision"]["rule_ids"]

    def test_preflight_allows_clean_skill(
        self, tmp_path: Path, fake_ctx: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A well-formed skill passes all default rules and returns ALLOW."""
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))

        ctx = _make_guard_ctx(tmp_path)
        register_intents(HermesAdapter(fake_ctx), ctx)

        raw = fake_ctx.tools["skill_guard_preflight"]["handler"](
            {
                "tool_name": "skill_manage",
                "args": {
                    "action": "create",
                    "name": "good-skill",
                    "content": "this is a reasonably long description for a skill",
                },
            }
        )
        result = json.loads(raw)

        assert result["ok"] is True
        assert result["decision"]["decision"] == "allow"
        assert result["decision"]["rule_ids"] == ["lifecycle.allow_static"]

    def test_user_rule_override_changes_severity(
        self, tmp_path: Path, fake_ctx: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A user rule file can override a default rule's severity."""
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "rules": [
                        {
                            "id": "manifest.description_too_short",
                            "priority": 10,
                            "when": {"op": "length_less_than", "field": "content", "value": 20},
                            "then": {
                                "severity": "block",
                                "message": "content is way too short",
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))
        monkeypatch.setenv("HSG_RULES_PATH", str(rules_file))

        ctx = _make_guard_ctx(tmp_path, mode="block")
        register_intents(HermesAdapter(fake_ctx), ctx)

        raw = fake_ctx.tools["skill_guard_preflight"]["handler"](
            {
                "tool_name": "skill_manage",
                "args": {
                    "action": "create",
                    "name": "x",
                    "content": "short",
                },
            }
        )
        result = json.loads(raw)

        assert result["ok"] is True
        assert result["decision"]["decision"] == "block"
        assert "content is way too short" in result["decision"]["reasons"]

    def test_disabled_rule_skips_evaluation(
        self, tmp_path: Path, fake_ctx: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Disabling a default rule removes it from evaluation."""
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "disabled_rules": ["manifest.name_missing"],
                    "rules": [],
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))
        monkeypatch.setenv("HSG_RULES_PATH", str(rules_file))

        ctx = _make_guard_ctx(tmp_path)
        register_intents(HermesAdapter(fake_ctx), ctx)

        raw = fake_ctx.tools["skill_guard_preflight"]["handler"](
            {
                "tool_name": "skill_manage",
                "args": {
                    "action": "create",
                    "name": "",  # would normally trigger name_missing
                    "content": "this is a reasonably long description for a skill",
                },
            }
        )
        result = json.loads(raw)

        # name_missing is disabled, so the only firing rule might be none
        # (description is long enough).  Decision should be ALLOW.
        assert result["ok"] is True
        assert "manifest.name_missing" not in result["decision"]["rule_ids"]

    def test_boundary_short_circuits_still_work(
        self, tmp_path: Path, fake_ctx: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-skill_manage calls bypass the rule engine entirely."""
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))

        ctx = _make_guard_ctx(tmp_path)
        register_intents(HermesAdapter(fake_ctx), ctx)

        raw = fake_ctx.tools["skill_guard_preflight"]["handler"](
            {
                "tool_name": "read_file",
                "args": {"path": "/etc/passwd"},
            }
        )
        result = json.loads(raw)

        assert result["ok"] is True
        assert result["decision"]["decision"] == "allow"
        assert "boundary.tool_not_skill_manage" in result["decision"]["rule_ids"]

    def test_enforcement_mode_escalation_via_hook(
        self, tmp_path: Path, fake_ctx: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In block mode the pre hook returns a block action."""
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))

        ctx = _make_guard_ctx(tmp_path, mode="block")
        register_intents(HermesAdapter(fake_ctx), ctx)

        action = fake_ctx.invoke_hook(
            "pre_tool_call",
            tool_name="skill_manage",
            args={"action": "create", "name": "", "content": "x"},
            tool_call_id="tc-001",
        )

        assert action is not None
        assert action["action"] == "block"

    def test_dry_run_downgrade_in_audit_mode(
        self, tmp_path: Path, fake_ctx: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dry_run=true downgrades candidate/block to warn."""
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))

        ctx = _make_guard_ctx(tmp_path, mode="block", dry_run=True)
        register_intents(HermesAdapter(fake_ctx), ctx)

        action = fake_ctx.invoke_hook(
            "pre_tool_call",
            tool_name="skill_manage",
            args={"action": "create", "name": "", "content": "x"},
            tool_call_id="tc-002",
        )

        # dry_run changes decision to WARN, so the hook does not block.
        assert action is None

    def test_rule_engine_loaded_at_plugin_registration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The plugin constructs a RuleEngine during registration; a bad user
        rule file in block mode should cause PreflightPolicy construction to
        raise."""
        from hermes_skill_guard.policy import PreflightPolicy
        from hermes_skill_guard.rules import RuleLoadError

        bad_rules = tmp_path / "bad.json"
        bad_rules.write_text("{not valid json", encoding="utf-8")

        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))
        monkeypatch.setenv("HSG_RULES_PATH", str(bad_rules))

        # In block mode, an invalid user rule file is fatal.
        cfg = GuardConfig(
            dry_run=False,
            state_dir=tmp_path,
            enforcement=EnforcementConfig(mode="block"),
        )
        with pytest.raises(RuleLoadError):
            PreflightPolicy(cfg)

    def test_user_rule_file_in_audit_mode_graceful_degradation(
        self, tmp_path: Path, fake_ctx: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In audit mode an invalid user rule file falls back to defaults."""
        bad_rules = tmp_path / "bad.json"
        bad_rules.write_text("{not valid json", encoding="utf-8")

        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))
        monkeypatch.setenv("HSG_RULES_PATH", str(bad_rules))

        ctx = _make_guard_ctx(tmp_path, mode="audit")
        register_intents(HermesAdapter(fake_ctx), ctx)

        # Should work fine with defaults
        raw = fake_ctx.tools["skill_guard_preflight"]["handler"](
            {
                "tool_name": "skill_manage",
                "args": {"action": "create", "name": "x", "content": "short"},
            }
        )
        result = json.loads(raw)
        assert result["ok"] is True
