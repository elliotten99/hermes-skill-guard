"""Tests for the configurable rule loader (T10.2).

These tests cover loading default + user rules, schema validation, the
disable/override merge semantics, env-var priority, and the fail-open vs
fail-closed behaviour gated by ``enforcement.mode``.

The loader does **not** evaluate conditions (T10.3 does); it only returns
``LoadedRule`` instances ready for the engine.
"""

from __future__ import annotations

import json
import logging
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from hermes_skill_guard.config import EnforcementConfig, GuardConfig
from hermes_skill_guard.rules import LoadedRule, RuleLoader, RuleLoadError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_user_rules(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _make_config(
    *,
    rules_path: Path | None = None,
    mode: str = "audit",
) -> GuardConfig:
    return GuardConfig(
        rules_path=rules_path,
        enforcement=EnforcementConfig(mode=mode),
    )


# ---------------------------------------------------------------------------
# Default-rule loading
# ---------------------------------------------------------------------------


def test_load_default_only_returns_5_rules() -> None:
    loader = RuleLoader(_make_config())
    rules = loader.load()
    assert len(rules) == 5
    ids = {r.id for r in rules}
    assert ids == {
        "manifest.name_missing",
        "naming.plugin_namespace",
        "manifest.description_too_short",
        "safety.secret_pattern",
        "lifecycle.dry_run_downgrade",
    }
    # All returned items are LoadedRule frozen dataclasses.
    for rule in rules:
        assert isinstance(rule, LoadedRule)
        with pytest.raises(FrozenInstanceError):
            rule.id = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# User-rule merging
# ---------------------------------------------------------------------------


def test_load_with_user_file_appends_rules(tmp_path: Path) -> None:
    user_file = _write_user_rules(
        tmp_path / "rules.json",
        {
            "version": "1.0",
            "rules": [
                {
                    "id": "org.custom_extra",
                    "when": {"op": "present", "field": "skill_name"},
                    "then": {"severity": "warn", "message": "custom"},
                }
            ],
        },
    )
    loader = RuleLoader(_make_config(rules_path=user_file))
    rules = loader.load()
    ids = [r.id for r in rules]
    assert "org.custom_extra" in ids
    assert len(rules) == 6


def test_load_with_disabled_rules_removes_defaults(tmp_path: Path) -> None:
    user_file = _write_user_rules(
        tmp_path / "rules.json",
        {
            "version": "1.0",
            "disabled_rules": ["manifest.description_too_short"],
            "rules": [],
        },
    )
    loader = RuleLoader(_make_config(rules_path=user_file))
    rules = loader.load()
    ids = {r.id for r in rules}
    assert "manifest.description_too_short" not in ids
    assert len(rules) == 4


def test_user_rule_overrides_default_by_id(tmp_path: Path) -> None:
    user_file = _write_user_rules(
        tmp_path / "rules.json",
        {
            "version": "1.0",
            "rules": [
                {
                    "id": "manifest.description_too_short",
                    "description": "overridden",
                    "priority": 5,
                    "when": {
                        "op": "length_less_than",
                        "field": "content",
                        "value": 80,
                    },
                    "then": {"severity": "block", "message": "too short (override)"},
                }
            ],
        },
    )
    loader = RuleLoader(_make_config(rules_path=user_file))
    rules = loader.load()
    # Still 5 total (override, not addition).
    assert len(rules) == 5
    overridden = next(r for r in rules if r.id == "manifest.description_too_short")
    assert overridden.severity == "block"
    assert overridden.priority == 5
    assert overridden.message_template == "too short (override)"


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


def test_priority_sort_order(tmp_path: Path) -> None:
    user_file = _write_user_rules(
        tmp_path / "rules.json",
        {
            "version": "1.0",
            "rules": [
                {
                    "id": "org.last",
                    "priority": 9999,
                    "when": {"op": "present", "field": "skill_name"},
                    "then": {"severity": "info", "message": "z"},
                },
                {
                    "id": "org.first",
                    "priority": 0,
                    "when": {"op": "present", "field": "skill_name"},
                    "then": {"severity": "info", "message": "a"},
                },
            ],
        },
    )
    loader = RuleLoader(_make_config(rules_path=user_file))
    rules = loader.load()
    priorities = [r.priority for r in rules]
    assert priorities == sorted(priorities)
    assert rules[0].id == "org.first"
    assert rules[-1].id == "org.last"


# ---------------------------------------------------------------------------
# Env-var precedence
# ---------------------------------------------------------------------------


def test_env_var_overrides_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_file = _write_user_rules(
        tmp_path / "config_rules.json",
        {
            "version": "1.0",
            "rules": [
                {
                    "id": "org.from_config",
                    "when": {"op": "present", "field": "skill_name"},
                    "then": {"severity": "info", "message": "config"},
                }
            ],
        },
    )
    env_file = _write_user_rules(
        tmp_path / "env_rules.json",
        {
            "version": "1.0",
            "rules": [
                {
                    "id": "org.from_env",
                    "when": {"op": "present", "field": "skill_name"},
                    "then": {"severity": "info", "message": "env"},
                }
            ],
        },
    )
    monkeypatch.setenv("HSG_RULES_PATH", str(env_file))
    loader = RuleLoader(_make_config(rules_path=config_file))
    ids = {r.id for r in loader.load()}
    assert "org.from_env" in ids
    assert "org.from_config" not in ids


# ---------------------------------------------------------------------------
# Failure policies
# ---------------------------------------------------------------------------


def test_default_load_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the bundled defaults cannot be parsed, always raise (fail-closed)."""
    from hermes_skill_guard.rules import loader as loader_module

    def fake_read() -> str:
        return "{not valid json"

    monkeypatch.setattr(loader_module, "_read_default_rules_text", fake_read)
    loader = RuleLoader(_make_config(mode="audit"))
    with pytest.raises(RuleLoadError):
        loader.load()


def test_user_load_failure_fail_open_in_audit_mode(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{not valid json", encoding="utf-8")
    loader = RuleLoader(_make_config(rules_path=bad_file, mode="audit"))
    with caplog.at_level(logging.WARNING, logger="hermes_skill_guard.rules"):
        rules = loader.load()
    assert len(rules) == 5  # defaults only
    assert any("user rules" in rec.message.lower() for rec in caplog.records)


def test_user_load_failure_fail_closed_in_block_mode(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{not valid json", encoding="utf-8")
    loader = RuleLoader(_make_config(rules_path=bad_file, mode="block"))
    with pytest.raises(RuleLoadError):
        loader.load()


def test_schema_validation_rejects_malformed_user_file(tmp_path: Path) -> None:
    """Schema errors in audit mode degrade to default-only with a warning;
    in block mode they raise."""
    bad_file = _write_user_rules(
        tmp_path / "rules.json",
        {
            "version": "2.0",  # bad const
            "rules": [],
        },
    )
    # block mode → raise
    block_loader = RuleLoader(_make_config(rules_path=bad_file, mode="block"))
    with pytest.raises(RuleLoadError):
        block_loader.load()
    # audit mode → defaults only
    audit_loader = RuleLoader(_make_config(rules_path=bad_file, mode="audit"))
    rules = audit_loader.load()
    assert len(rules) == 5


# ---------------------------------------------------------------------------
# LoadedRule field shape
# ---------------------------------------------------------------------------


def test_loaded_rule_carries_when_tree_unchanged(tmp_path: Path) -> None:
    user_file = _write_user_rules(
        tmp_path / "rules.json",
        {
            "version": "1.0",
            "rules": [
                {
                    "id": "org.tree",
                    "when": {
                        "and": [
                            {"op": "present", "field": "skill_name"},
                            {"op": "contains", "field": "skill_name", "value": "x"},
                        ]
                    },
                    "then": {"severity": "warn", "message": "m"},
                }
            ],
        },
    )
    loader = RuleLoader(_make_config(rules_path=user_file))
    rule = next(r for r in loader.load() if r.id == "org.tree")
    assert "and" in rule.when
    assert isinstance(rule.when["and"], list)
    assert rule.severity == "warn"
    assert rule.enabled is True


def test_missing_user_file_falls_back_to_defaults(tmp_path: Path) -> None:
    loader = RuleLoader(_make_config(rules_path=tmp_path / "nonexistent.json"))
    rules = loader.load()
    assert len(rules) == 5
