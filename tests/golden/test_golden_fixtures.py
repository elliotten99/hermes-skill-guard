from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

from hermes_skill_guard.config import GuardConfig
from hermes_skill_guard.policy import PreflightPolicy, ToolCall
from hermes_skill_guard.redaction import Redactor
from hermes_skill_guard.schemas import CandidateStatus, validate_candidate_transition

FIXTURES = Path(__file__).resolve().parent


def _load(name: str) -> list[dict[str, object]]:
    loaded = yaml.safe_load((FIXTURES / name).read_text())
    return cast(list[dict[str, object]], loaded)


def test_decision_golden() -> None:
    for case in _load("decision_cases.yaml"):
        input_data = cast(dict[str, Any], case["input"])
        assert isinstance(input_data, dict)
        decision = PreflightPolicy(GuardConfig()).evaluate(
            ToolCall(str(input_data["tool_name"]), dict(cast(dict[str, Any], input_data["args"])))
        )
        expected = cast(dict[str, Any], case["expected"])
        assert isinstance(expected, dict)
        assert decision.decision.value == expected["decision"]
        assert expected["rule_id"] in decision.rule_ids


def test_redaction_golden() -> None:
    for case in _load("redaction_cases.yaml"):
        summary, _, _, _ = Redactor().redact(case["input"])
        expected = cast(dict[str, str], case["expected"])
        assert expected["not_contains"] not in str(summary)


def test_candidate_state_golden() -> None:
    for case in _load("candidate_state_cases.yaml"):
        allowed = bool(case["allowed"])
        try:
            validate_candidate_transition(
                CandidateStatus(str(case["from"])), CandidateStatus(str(case["to"]))
            )
            result = True
        except ValueError:
            result = False
        assert result is allowed
