"""Tests for the configurable rule engine (T10.3).

These tests cover condition evaluation, severity aggregation, message-template
rendering, and error handling. They do **not** exercise the loader (T10.2) or
schema validator (T10.1) — those have their own test modules.
"""

from __future__ import annotations

import pytest

from hermes_skill_guard.config import GuardConfig
from hermes_skill_guard.rules.context import RuleContext
from hermes_skill_guard.rules.engine import (
    RuleEngine,
    RuleEvaluationError,
)
from hermes_skill_guard.rules.loader import LoadedRule
from hermes_skill_guard.schemas import DecisionValue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(
    *,
    skill_name: str | None = "test-skill",
    tool_name: str = "skill_manage",
    content: str = "some skill content here",
    description: str = "",
    target_path: str | None = None,
    dry_run: bool = False,
    enforcement_mode: str = "audit",
) -> RuleContext:
    return RuleContext(
        skill_name=skill_name,
        tool_name=tool_name,
        content=content,
        content_length=len(content),
        description=description,
        target_path=target_path,
        dry_run=dry_run,
        enforcement_mode=enforcement_mode,
    )


def _rule(
    rid: str,
    when: dict[str, object],
    severity: str = "warn",
    message: str = "rule fired",
    priority: int = 100,
    enabled: bool = True,
) -> LoadedRule:
    return LoadedRule(
        id=rid,
        description="",
        when=when,
        severity=severity,
        message_template=message,
        priority=priority,
        enabled=enabled,
    )


# ---------------------------------------------------------------------------
# Construction / disabled-rule filtering
# ---------------------------------------------------------------------------


def test_engine_drops_disabled_rules_at_construction() -> None:
    rules = [
        _rule("a.enabled", {"op": "present", "field": "skill_name"}),
        _rule("b.disabled", {"op": "present", "field": "skill_name"}, enabled=False),
    ]
    engine = RuleEngine(rules)
    assert len(engine.rules) == 1
    assert engine.rules[0].id == "a.enabled"


def test_engine_exposes_read_only_rule_list() -> None:
    engine = RuleEngine([_rule("x", {"op": "present", "field": "skill_name"})])
    rules = engine.rules
    assert len(rules) == 1
    # The returned list is a copy; mutating it does not affect the engine.
    rules.clear()
    assert len(engine.rules) == 1


# ---------------------------------------------------------------------------
# Empty / no-match behaviour
# ---------------------------------------------------------------------------


def test_no_rules_defaults_to_info_allow() -> None:
    engine = RuleEngine([])
    result = engine.evaluate(_ctx())
    assert result.decision == DecisionValue.ALLOW
    assert result.severity == "info"
    assert result.fired_rules == []
    assert result.reasons == []


def test_no_matching_rules_defaults_to_info_allow() -> None:
    engine = RuleEngine(
        [
            _rule("never", {"op": "equals", "field": "skill_name", "value": "nonexistent"}),
        ]
    )
    result = engine.evaluate(_ctx(skill_name="real"))
    assert result.decision == DecisionValue.ALLOW
    assert result.fired_rules == []


# ---------------------------------------------------------------------------
# Leaf operators
# ---------------------------------------------------------------------------


class TestOpEquals:
    def test_matches_when_equal(self) -> None:
        engine = RuleEngine(
            [
                _rule("eq", {"op": "equals", "field": "skill_name", "value": "match"}),
            ]
        )
        result = engine.evaluate(_ctx(skill_name="match"))
        assert result.fired_rules == ["eq"]

    def test_no_match_when_different(self) -> None:
        engine = RuleEngine(
            [
                _rule("eq", {"op": "equals", "field": "skill_name", "value": "match"}),
            ]
        )
        result = engine.evaluate(_ctx(skill_name="other"))
        assert result.fired_rules == []

    def test_none_field_matches_none_value(self) -> None:
        engine = RuleEngine(
            [
                _rule("eq", {"op": "equals", "field": "skill_name", "value": None}),
            ]
        )
        result = engine.evaluate(_ctx(skill_name=None))
        assert result.fired_rules == ["eq"]


class TestOpNotEquals:
    def test_fires_when_different(self) -> None:
        engine = RuleEngine(
            [
                _rule("ne", {"op": "not_equals", "field": "skill_name", "value": "x"}),
            ]
        )
        result = engine.evaluate(_ctx(skill_name="y"))
        assert result.fired_rules == ["ne"]

    def test_no_fire_when_equal(self) -> None:
        engine = RuleEngine(
            [
                _rule("ne", {"op": "not_equals", "field": "skill_name", "value": "x"}),
            ]
        )
        result = engine.evaluate(_ctx(skill_name="x"))
        assert result.fired_rules == []


class TestOpContains:
    def test_fires_when_substring_present(self) -> None:
        engine = RuleEngine(
            [
                _rule("c", {"op": "contains", "field": "content", "value": "skill"}),
            ]
        )
        result = engine.evaluate(_ctx(content="my skill content"))
        assert result.fired_rules == ["c"]

    def test_no_fire_when_substring_missing(self) -> None:
        engine = RuleEngine(
            [
                _rule("c", {"op": "contains", "field": "content", "value": "xyz"}),
            ]
        )
        result = engine.evaluate(_ctx(content="abc"))
        assert result.fired_rules == []


class TestOpNotContains:
    def test_fires_when_substring_missing(self) -> None:
        engine = RuleEngine(
            [
                _rule("nc", {"op": "not_contains", "field": "content", "value": "xyz"}),
            ]
        )
        result = engine.evaluate(_ctx(content="abc"))
        assert result.fired_rules == ["nc"]

    def test_no_fire_when_substring_present(self) -> None:
        engine = RuleEngine(
            [
                _rule("nc", {"op": "not_contains", "field": "content", "value": "skill"}),
            ]
        )
        result = engine.evaluate(_ctx(content="my skill"))
        assert result.fired_rules == []


class TestOpMatches:
    def test_fires_on_regex_match(self) -> None:
        engine = RuleEngine(
            [
                _rule("m", {"op": "matches", "field": "content", "value": r"\d+"}),
            ]
        )
        result = engine.evaluate(_ctx(content="item 42"))
        assert result.fired_rules == ["m"]

    def test_no_fire_on_regex_miss(self) -> None:
        engine = RuleEngine(
            [
                _rule("m", {"op": "matches", "field": "content", "value": r"^\d+$"}),
            ]
        )
        result = engine.evaluate(_ctx(content="abc"))
        assert result.fired_rules == []

    def test_invalid_regex_raises(self) -> None:
        engine = RuleEngine(
            [
                _rule("m", {"op": "matches", "field": "content", "value": "("}),
            ]
        )
        with pytest.raises(RuleEvaluationError):
            engine.evaluate(_ctx(content="x"))


class TestOpMissing:
    def test_fires_when_field_is_none(self) -> None:
        engine = RuleEngine(
            [
                _rule("miss", {"op": "missing", "field": "skill_name"}),
            ]
        )
        result = engine.evaluate(_ctx(skill_name=None))
        assert result.fired_rules == ["miss"]

    def test_fires_when_field_is_empty_string(self) -> None:
        engine = RuleEngine(
            [
                _rule("miss", {"op": "missing", "field": "skill_name"}),
            ]
        )
        result = engine.evaluate(_ctx(skill_name=""))
        assert result.fired_rules == ["miss"]

    def test_no_fire_when_field_present(self) -> None:
        engine = RuleEngine(
            [
                _rule("miss", {"op": "missing", "field": "skill_name"}),
            ]
        )
        result = engine.evaluate(_ctx(skill_name="hello"))
        assert result.fired_rules == []


class TestOpPresent:
    def test_fires_when_field_has_value(self) -> None:
        engine = RuleEngine(
            [
                _rule("pres", {"op": "present", "field": "skill_name"}),
            ]
        )
        result = engine.evaluate(_ctx(skill_name="hello"))
        assert result.fired_rules == ["pres"]

    def test_no_fire_when_field_is_none(self) -> None:
        engine = RuleEngine(
            [
                _rule("pres", {"op": "present", "field": "skill_name"}),
            ]
        )
        result = engine.evaluate(_ctx(skill_name=None))
        assert result.fired_rules == []

    def test_no_fire_when_field_is_empty_string(self) -> None:
        engine = RuleEngine(
            [
                _rule("pres", {"op": "present", "field": "skill_name"}),
            ]
        )
        result = engine.evaluate(_ctx(skill_name=""))
        assert result.fired_rules == []


class TestOpLength:
    def test_length_less_than(self) -> None:
        engine = RuleEngine(
            [
                _rule("lt", {"op": "length_less_than", "field": "content", "value": 10}),
            ]
        )
        assert engine.evaluate(_ctx(content="short")).fired_rules == ["lt"]
        assert engine.evaluate(_ctx(content="this is long")).fired_rules == []

    def test_length_greater_than(self) -> None:
        engine = RuleEngine(
            [
                _rule("gt", {"op": "length_greater_than", "field": "content", "value": 5}),
            ]
        )
        assert engine.evaluate(_ctx(content="hello world")).fired_rules == ["gt"]
        assert engine.evaluate(_ctx(content="hi")).fired_rules == []

    def test_length_equals(self) -> None:
        engine = RuleEngine(
            [
                _rule("eq", {"op": "length_equals", "field": "content", "value": 5}),
            ]
        )
        assert engine.evaluate(_ctx(content="hello")).fired_rules == ["eq"]
        assert engine.evaluate(_ctx(content="hi")).fired_rules == []


# ---------------------------------------------------------------------------
# Logical combinators
# ---------------------------------------------------------------------------


class TestAndCombinator:
    def test_fires_when_all_children_match(self) -> None:
        engine = RuleEngine(
            [
                _rule(
                    "and",
                    {
                        "and": [
                            {"op": "present", "field": "skill_name"},
                            {"op": "contains", "field": "skill_name", "value": ":"},
                        ]
                    },
                ),
            ]
        )
        result = engine.evaluate(_ctx(skill_name="plugin:foo"))
        assert result.fired_rules == ["and"]

    def test_no_fire_when_any_child_misses(self) -> None:
        engine = RuleEngine(
            [
                _rule(
                    "and",
                    {
                        "and": [
                            {"op": "present", "field": "skill_name"},
                            {"op": "contains", "field": "skill_name", "value": ":"},
                        ]
                    },
                ),
            ]
        )
        result = engine.evaluate(_ctx(skill_name="plain"))
        assert result.fired_rules == []

    def test_single_child_and(self) -> None:
        engine = RuleEngine(
            [
                _rule("and", {"and": [{"op": "present", "field": "skill_name"}]}),
            ]
        )
        assert engine.evaluate(_ctx(skill_name="x")).fired_rules == ["and"]


class TestOrCombinator:
    def test_fires_when_any_child_matches(self) -> None:
        engine = RuleEngine(
            [
                _rule(
                    "or",
                    {
                        "or": [
                            {"op": "equals", "field": "skill_name", "value": "a"},
                            {"op": "equals", "field": "skill_name", "value": "b"},
                        ]
                    },
                ),
            ]
        )
        assert engine.evaluate(_ctx(skill_name="b")).fired_rules == ["or"]

    def test_no_fire_when_all_children_miss(self) -> None:
        engine = RuleEngine(
            [
                _rule(
                    "or",
                    {
                        "or": [
                            {"op": "equals", "field": "skill_name", "value": "a"},
                            {"op": "equals", "field": "skill_name", "value": "b"},
                        ]
                    },
                ),
            ]
        )
        assert engine.evaluate(_ctx(skill_name="c")).fired_rules == []


class TestNotCombinator:
    def test_inverts_child_result(self) -> None:
        engine = RuleEngine(
            [
                _rule("not", {"not": {"op": "equals", "field": "skill_name", "value": "x"}}),
            ]
        )
        assert engine.evaluate(_ctx(skill_name="y")).fired_rules == ["not"]
        assert engine.evaluate(_ctx(skill_name="x")).fired_rules == []

    def test_nested_not(self) -> None:
        engine = RuleEngine(
            [
                _rule("nn", {"not": {"not": {"op": "present", "field": "skill_name"}}}),
            ]
        )
        assert engine.evaluate(_ctx(skill_name="x")).fired_rules == ["nn"]


class TestNestedCombinators:
    def test_and_inside_or(self) -> None:
        engine = RuleEngine(
            [
                _rule(
                    "complex",
                    {
                        "or": [
                            {
                                "and": [
                                    {"op": "present", "field": "skill_name"},
                                    {"op": "contains", "field": "skill_name", "value": ":"},
                                ]
                            },
                            {"op": "equals", "field": "tool_name", "value": "other"},
                        ]
                    },
                ),
            ]
        )
        # Neither branch matches
        r1 = engine.evaluate(_ctx(skill_name="plain", tool_name="skill_manage"))
        assert r1.fired_rules == []
        # First branch matches
        r2 = engine.evaluate(_ctx(skill_name="p:foo", tool_name="skill_manage"))
        assert r2.fired_rules == ["complex"]
        # Second branch matches
        r3 = engine.evaluate(_ctx(skill_name="plain", tool_name="other"))
        assert r3.fired_rules == ["complex"]


# ---------------------------------------------------------------------------
# Severity aggregation
# ---------------------------------------------------------------------------


class TestSeverityAggregation:
    def test_single_warn(self) -> None:
        engine = RuleEngine(
            [
                _rule("w", {"op": "present", "field": "skill_name"}, severity="warn"),
            ]
        )
        result = engine.evaluate(_ctx())
        assert result.severity == "warn"
        assert result.decision == DecisionValue.WARN

    def test_single_candidate(self) -> None:
        engine = RuleEngine(
            [
                _rule("c", {"op": "present", "field": "skill_name"}, severity="candidate"),
            ]
        )
        result = engine.evaluate(_ctx())
        assert result.severity == "candidate"
        assert result.decision == DecisionValue.CANDIDATE

    def test_single_block(self) -> None:
        engine = RuleEngine(
            [
                _rule("b", {"op": "present", "field": "skill_name"}, severity="block"),
            ]
        )
        result = engine.evaluate(_ctx())
        assert result.severity == "block"
        assert result.decision == DecisionValue.BLOCK

    def test_single_info(self) -> None:
        engine = RuleEngine(
            [
                _rule("i", {"op": "present", "field": "skill_name"}, severity="info"),
            ]
        )
        result = engine.evaluate(_ctx())
        assert result.severity == "info"
        assert result.decision == DecisionValue.ALLOW

    def test_severity_escalation_warn_to_block(self) -> None:
        engine = RuleEngine(
            [
                _rule("w", {"op": "present", "field": "skill_name"}, severity="warn"),
                _rule("b", {"op": "present", "field": "skill_name"}, severity="block"),
            ]
        )
        result = engine.evaluate(_ctx())
        assert result.severity == "block"
        assert result.decision == DecisionValue.BLOCK

    def test_severity_escalation_info_to_candidate(self) -> None:
        engine = RuleEngine(
            [
                _rule("i", {"op": "present", "field": "skill_name"}, severity="info"),
                _rule("c", {"op": "present", "field": "skill_name"}, severity="candidate"),
            ]
        )
        result = engine.evaluate(_ctx())
        assert result.severity == "candidate"
        assert result.decision == DecisionValue.CANDIDATE

    def test_mixed_severities_take_max(self) -> None:
        engine = RuleEngine(
            [
                _rule("i", {"op": "present", "field": "skill_name"}, severity="info"),
                _rule("w", {"op": "present", "field": "skill_name"}, severity="warn"),
                _rule("c", {"op": "present", "field": "skill_name"}, severity="candidate"),
            ]
        )
        result = engine.evaluate(_ctx())
        assert result.severity == "candidate"

    def test_evaluation_preserves_input_order(self) -> None:
        engine = RuleEngine(
            [
                _rule("second", {"op": "present", "field": "skill_name"}, priority=200),
                _rule("first", {"op": "present", "field": "skill_name"}, priority=10),
            ]
        )
        result = engine.evaluate(_ctx())
        # Engine does not sort; it evaluates in the order rules are passed.
        # Sorting is the loader's responsibility (T10.2).
        assert result.fired_rules == ["second", "first"]


# ---------------------------------------------------------------------------
# Message template rendering
# ---------------------------------------------------------------------------


class TestMessageTemplates:
    def test_renders_field_placeholders(self) -> None:
        engine = RuleEngine(
            [
                _rule(
                    "m",
                    {
                        "op": "present",
                        "field": "skill_name",
                    },
                    message="skill {skill_name} detected",
                ),
            ]
        )
        result = engine.evaluate(_ctx(skill_name="my-skill"))
        assert result.reasons == ["skill my-skill detected"]

    def test_unknown_placeholder_renders_empty(self) -> None:
        engine = RuleEngine(
            [
                _rule(
                    "m",
                    {
                        "op": "present",
                        "field": "skill_name",
                    },
                    message="unknown {not_a_field}",
                ),
            ]
        )
        result = engine.evaluate(_ctx(skill_name="x"))
        assert result.reasons == ["unknown "]

    def test_none_field_renders_empty(self) -> None:
        engine = RuleEngine(
            [
                _rule(
                    "m",
                    {
                        "op": "missing",
                        "field": "skill_name",
                    },
                    message="name is {skill_name}",
                ),
            ]
        )
        result = engine.evaluate(_ctx(skill_name=None))
        assert result.reasons == ["name is "]

    def test_multiple_placeholders(self) -> None:
        engine = RuleEngine(
            [
                _rule(
                    "m",
                    {
                        "op": "present",
                        "field": "skill_name",
                    },
                    message="{skill_name} via {tool_name}",
                ),
            ]
        )
        result = engine.evaluate(_ctx(skill_name="s", tool_name="t"))
        assert result.reasons == ["s via t"]

    def test_invalid_format_spec_raises(self) -> None:
        engine = RuleEngine(
            [
                _rule(
                    "m",
                    {
                        "op": "present",
                        "field": "skill_name",
                    },
                    message="bad {skill_name!}",
                ),
            ]
        )
        with pytest.raises(RuleEvaluationError):
            engine.evaluate(_ctx(skill_name="x"))


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestEvaluationErrors:
    def test_unknown_operator_raises(self) -> None:
        engine = RuleEngine(
            [
                _rule("bad", {"op": "flibbety", "field": "skill_name"}),
            ]
        )
        with pytest.raises(RuleEvaluationError, match="unknown operator"):
            engine.evaluate(_ctx())

    def test_malformed_leaf_raises(self) -> None:
        engine = RuleEngine(
            [
                _rule("bad", {"op": 123, "field": "skill_name"}),
            ]
        )
        with pytest.raises(RuleEvaluationError, match="malformed leaf"):
            engine.evaluate(_ctx())

    def test_and_with_non_list_raises(self) -> None:
        engine = RuleEngine(
            [
                _rule("bad", {"and": "notalist"}),
            ]
        )
        with pytest.raises(RuleEvaluationError, match="'and' must be a list"):
            engine.evaluate(_ctx())

    def test_or_with_non_list_raises(self) -> None:
        engine = RuleEngine(
            [
                _rule("bad", {"or": "notalist"}),
            ]
        )
        with pytest.raises(RuleEvaluationError, match="'or' must be a list"):
            engine.evaluate(_ctx())

    def test_not_with_non_object_raises(self) -> None:
        engine = RuleEngine(
            [
                _rule("bad", {"not": "notanobject"}),
            ]
        )
        with pytest.raises(RuleEvaluationError, match="'not' must wrap an object"):
            engine.evaluate(_ctx())


# ---------------------------------------------------------------------------
# Integration with RuleContext.from_tool_call
# ---------------------------------------------------------------------------


class TestContextFromToolCall:
    def test_extracts_fields_from_skill_manage_args(self) -> None:
        ctx = RuleContext.from_tool_call(
            tool_name="skill_manage",
            args={
                "name": "my-skill",
                "content": "skill body content",
                "description": "A useful skill",
                "target_path": "/skills/my-skill.py",
            },
            config=_make_config(),
        )
        assert ctx.skill_name == "my-skill"
        # extract_content joins content + description (and other fallback keys).
        assert ctx.content == "skill body content\nA useful skill"
        assert ctx.description == "A useful skill"
        assert ctx.target_path == "/skills/my-skill.py"
        assert ctx.tool_name == "skill_manage"

    def test_none_skill_name_when_missing(self) -> None:
        ctx = RuleContext.from_tool_call(
            tool_name="skill_manage",
            args={"content": "body"},
            config=_make_config(),
        )
        assert ctx.skill_name is None

    def test_dry_run_and_enforcement_mode_snapshots(self) -> None:
        from hermes_skill_guard.config import EnforcementConfig, GuardConfig

        config = GuardConfig(
            dry_run=True,
            enforcement=EnforcementConfig(mode="block"),
        )
        ctx = RuleContext.from_tool_call(
            tool_name="x",
            args={},
            config=config,
        )
        assert ctx.dry_run is True
        assert ctx.enforcement_mode == "block"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> GuardConfig:
    return GuardConfig()
