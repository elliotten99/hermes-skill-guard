"""Configurable rule engine — loader and merge layer (T10.2).

The loader reads built-in default rules (shipped under
``hermes_skill_guard/data/default_rules.json``) plus an optional user rule
file, validates them against ``rules.schema.json``, and produces a sorted
list of :class:`LoadedRule` instances for the engine (T10.3) to consume.

This module does *not* evaluate conditions or talk to ``PreflightPolicy``.
It exists purely to materialise the merged rule set.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from hermes_skill_guard.config import GuardConfig
from hermes_skill_guard.rules.validator import SchemaError, validate

_LOG = logging.getLogger(__name__)

_RULES_ENV_VAR = "HSG_RULES_PATH"


class RuleLoadError(RuntimeError):
    """Raised when the rule loader cannot satisfy a fail-closed contract.

    The bundled defaults must always parse cleanly; failure there always
    raises. User-rule failures only raise when ``enforcement.mode == "block"``;
    otherwise the loader logs a warning and returns the default rule set.
    """


@dataclass(frozen=True, slots=True)
class LoadedRule:
    """A normalised rule ready for the engine.

    Attributes:
        id: Stable dotted identifier (e.g. ``"naming.plugin_namespace"``).
        description: Human-readable summary; empty string when omitted.
        when: The unmodified condition tree (engine evaluates this in T10.3).
        severity: One of ``info``, ``warn``, ``candidate``, ``block``.
        message_template: Reason text appended to ``Decision.reasons``.
            May contain ``{field_name}`` placeholders.
        priority: Lower numbers run first (default 100).
        enabled: When False, the rule is loaded but skipped during eval.
    """

    id: str
    description: str
    when: dict[str, Any]
    severity: str
    message_template: str
    priority: int = 100
    enabled: bool = True


# ---------------------------------------------------------------------------
# Internal helpers — kept module-level for monkeypatch-friendly tests.
# ---------------------------------------------------------------------------


def _read_default_rules_text() -> str:
    """Return the raw text of the bundled default-rules JSON file."""
    return (
        resources.files("hermes_skill_guard.data")
        .joinpath("default_rules.json")
        .read_text(encoding="utf-8")
    )


def _read_schema_text() -> str:
    return (
        resources.files("hermes_skill_guard.data")
        .joinpath("rules.schema.json")
        .read_text(encoding="utf-8")
    )


def _parse_and_validate(text: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Parse *text* as JSON, validate against *schema*, return the dict."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuleLoadError(f"rule file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RuleLoadError("rule file root must be a JSON object")
    try:
        validate(data, schema)
    except SchemaError as exc:
        raise RuleLoadError(f"rule file failed schema validation: {exc}") from exc
    return data


def _to_loaded_rule(raw: dict[str, Any]) -> LoadedRule:
    action = raw["then"]
    return LoadedRule(
        id=raw["id"],
        description=raw.get("description", ""),
        when=dict(raw["when"]),
        severity=action["severity"],
        message_template=action["message"],
        priority=int(raw.get("priority", 100)),
        enabled=bool(raw.get("enabled", True)),
    )


def _resolve_user_path(config: GuardConfig) -> Path | None:
    """Return the user rules path honouring ``HSG_RULES_PATH`` over config."""
    env_value = os.environ.get(_RULES_ENV_VAR)
    if env_value:
        stripped = env_value.strip()
        if stripped:
            return Path(stripped)
    return config.rules_path


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------


class RuleLoader:
    """Load + merge default and user rule files.

    Construction is cheap; the actual filesystem work happens inside
    :meth:`load` so callers can decide when to pay the cost.
    """

    def __init__(self, config: GuardConfig) -> None:
        self._config = config

    def load(self) -> list[LoadedRule]:
        """Return the merged, validated, sorted rule set.

        See the module docstring for the failure-policy contract.
        """
        schema = json.loads(_read_schema_text())

        # 1. Bundled defaults — must always parse. Failure here is fatal
        #    regardless of enforcement mode.
        try:
            default_data = _parse_and_validate(_read_default_rules_text(), schema)
        except RuleLoadError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise RuleLoadError(f"failed to read bundled default rules: {exc}") from exc

        default_rules = [_to_loaded_rule(r) for r in default_data.get("rules", [])]

        # 2. Optional user file.
        user_path = _resolve_user_path(self._config)
        if user_path is None or not user_path.exists():
            return self._finalise(default_rules, disabled=set(), overrides={}, extras=[])

        try:
            user_data = _parse_and_validate(user_path.read_text(encoding="utf-8"), schema)
        except RuleLoadError as exc:
            mode = self._config.enforcement.mode
            if mode == "block":
                # Fail closed: refuse to start with an unparseable rule file.
                raise
            _LOG.warning(
                "user rules at %s could not be loaded (%s); falling back to defaults",
                user_path,
                exc,
            )
            return self._finalise(default_rules, disabled=set(), overrides={}, extras=[])

        disabled = set(user_data.get("disabled_rules", []) or [])
        overrides: dict[str, LoadedRule] = {}
        extras: list[LoadedRule] = []
        default_ids = {r.id for r in default_rules}
        for raw in user_data.get("rules", []) or []:
            loaded = _to_loaded_rule(raw)
            if loaded.id in default_ids:
                overrides[loaded.id] = loaded
            else:
                extras.append(loaded)

        return self._finalise(default_rules, disabled=disabled, overrides=overrides, extras=extras)

    @staticmethod
    def _finalise(
        defaults: list[LoadedRule],
        *,
        disabled: set[str],
        overrides: dict[str, LoadedRule],
        extras: list[LoadedRule],
    ) -> list[LoadedRule]:
        merged: list[LoadedRule] = []
        for rule in defaults:
            if rule.id in disabled:
                continue
            merged.append(overrides.get(rule.id, rule))
        merged.extend(extras)
        merged.sort(key=lambda r: (r.priority, r.id))
        return merged


__all__ = ["LoadedRule", "RuleLoadError", "RuleLoader"]
