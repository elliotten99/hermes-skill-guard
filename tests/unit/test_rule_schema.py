"""Schema validation tests for the configurable rule engine (T10.1).

These tests only validate that the schema, default rules and example user
rules are well-formed. They do **not** exercise the rule engine itself -
that lives in T10.2 (loader) and T10.3 (engine).

The project does not depend on the third-party ``jsonschema`` library, so
this module ships a deliberately small validator that supports just the
JSON Schema keywords used by ``rules.schema.json``:

* ``type``, ``enum``, ``const``
* ``properties``, ``required``, ``additionalProperties``
* ``items``, ``minItems``, ``uniqueItems``
* ``oneOf``, ``$ref`` (intra-document, ``#/$defs/...``)
* ``minLength``, ``minimum``, ``pattern``

Anything else is treated as a documentation-only annotation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "src" / "hermes_skill_guard" / "data" / "rules.schema.json"
DEFAULT_RULES_PATH = REPO_ROOT / "src" / "hermes_skill_guard" / "data" / "default_rules.json"
EXAMPLE_RULES_PATH = REPO_ROOT / "docs" / "examples" / "custom-rules.json"


# ---------------------------------------------------------------------------
# Minimal JSON Schema validator (Draft 2020-12 subset)
# ---------------------------------------------------------------------------


class SchemaError(AssertionError):
    """Raised when an instance fails schema validation."""


_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


def _resolve_ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise SchemaError(f"only intra-document refs supported, got: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    if not isinstance(node, dict):
        raise SchemaError(f"$ref {ref} did not resolve to an object")
    return node


def _validate(instance: Any, schema: dict[str, Any], root: dict[str, Any], path: str) -> None:
    if "$ref" in schema:
        _validate(instance, _resolve_ref(root, schema["$ref"]), root, path)
        return

    if "const" in schema and instance != schema["const"]:
        raise SchemaError(f"{path}: expected const {schema['const']!r}, got {instance!r}")

    if "enum" in schema and instance not in schema["enum"]:
        raise SchemaError(f"{path}: {instance!r} not in enum {schema['enum']}")

    if "type" in schema:
        expected = schema["type"]
        types = _TYPE_MAP[expected]
        # JSON booleans must not match integer (bool is subclass of int).
        if expected == "integer" and isinstance(instance, bool):
            raise SchemaError(f"{path}: expected integer, got bool")
        if not isinstance(instance, types):
            raise SchemaError(f"{path}: expected type {expected}, got {type(instance).__name__}")

    if "oneOf" in schema:
        matches = 0
        last_err: SchemaError | None = None
        for idx, sub in enumerate(schema["oneOf"]):
            try:
                _validate(instance, sub, root, f"{path}/oneOf[{idx}]")
                matches += 1
            except SchemaError as exc:
                last_err = exc
        if matches != 1:
            raise SchemaError(
                f"{path}: oneOf matched {matches} branches (need exactly 1); last error: {last_err}"
            )

    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                raise SchemaError(f"{path}: missing required key {key!r}")
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            if key in properties:
                _validate(value, properties[key], root, f"{path}/{key}")
            elif additional is False:
                raise SchemaError(f"{path}: additional property {key!r} not allowed")
            elif isinstance(additional, dict):
                _validate(value, additional, root, f"{path}/{key}")

    if isinstance(instance, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for idx, item in enumerate(instance):
                _validate(item, items, root, f"{path}[{idx}]")
        if "minItems" in schema and len(instance) < schema["minItems"]:
            raise SchemaError(f"{path}: array shorter than minItems={schema['minItems']}")
        if schema.get("uniqueItems"):
            unique = {json.dumps(i, sort_keys=True) for i in instance}
            if len(instance) != len(unique):
                raise SchemaError(f"{path}: array items not unique")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            raise SchemaError(f"{path}: string shorter than minLength={schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            raise SchemaError(f"{path}: {instance!r} does not match pattern {schema['pattern']!r}")

    if (
        isinstance(instance, int)
        and not isinstance(instance, bool)
        and "minimum" in schema
        and instance < schema["minimum"]
    ):
        raise SchemaError(f"{path}: {instance} below minimum {schema['minimum']}")


def validate(instance: Any, schema: dict[str, Any]) -> None:
    _validate(instance, schema, schema, "$")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def default_rules() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(DEFAULT_RULES_PATH.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def example_rules() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(EXAMPLE_RULES_PATH.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_schema_is_valid_draft_2020_12_skeleton(schema: dict[str, Any]) -> None:
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["type"] == "object"
    assert "$defs" in schema
    for required_def in ("rule", "action", "condition", "leafCondition"):
        assert required_def in schema["$defs"], f"missing $defs/{required_def}"


def test_default_rules_parse_as_json(default_rules: dict[str, Any]) -> None:
    assert default_rules["version"] == "1.0"
    assert isinstance(default_rules["rules"], list)
    assert len(default_rules["rules"]) == 5


def test_default_rules_have_unique_ids(default_rules: dict[str, Any]) -> None:
    ids = [rule["id"] for rule in default_rules["rules"]]
    assert len(ids) == len(set(ids)), f"duplicate rule ids: {ids}"


def test_default_rules_cover_v01_hardcoded_checks(default_rules: dict[str, Any]) -> None:
    ids = {rule["id"] for rule in default_rules["rules"]}
    assert ids == {
        "manifest.name_missing",
        "naming.plugin_namespace",
        "manifest.description_too_short",
        "safety.secret_pattern",
        "lifecycle.dry_run_downgrade",
    }


def test_default_rules_validate_against_schema(
    default_rules: dict[str, Any], schema: dict[str, Any]
) -> None:
    validate(default_rules, schema)


def test_example_rules_validate_against_schema(
    example_rules: dict[str, Any], schema: dict[str, Any]
) -> None:
    validate(example_rules, schema)


def test_example_uses_disabled_rules_field(example_rules: dict[str, Any]) -> None:
    assert "manifest.description_too_short" in example_rules.get("disabled_rules", [])


def test_validator_rejects_bad_version(schema: dict[str, Any]) -> None:
    bad = {"version": "2.0", "rules": []}
    with pytest.raises(SchemaError):
        validate(bad, schema)


def test_validator_rejects_unknown_operator(schema: dict[str, Any]) -> None:
    bad = {
        "version": "1.0",
        "rules": [
            {
                "id": "x.y",
                "when": {"op": "not_a_real_op", "field": "skill_name"},
                "then": {"severity": "warn", "message": "nope"},
            }
        ],
    }
    with pytest.raises(SchemaError):
        validate(bad, schema)


def test_validator_rejects_unknown_field(schema: dict[str, Any]) -> None:
    bad = {
        "version": "1.0",
        "rules": [
            {
                "id": "x.y",
                "when": {"op": "equals", "field": "not_a_field", "value": "z"},
                "then": {"severity": "warn", "message": "nope"},
            }
        ],
    }
    with pytest.raises(SchemaError):
        validate(bad, schema)


def test_validator_rejects_bad_severity(schema: dict[str, Any]) -> None:
    bad = {
        "version": "1.0",
        "rules": [
            {
                "id": "x.y",
                "when": {"op": "present", "field": "skill_name"},
                "then": {"severity": "panic", "message": "nope"},
            }
        ],
    }
    with pytest.raises(SchemaError):
        validate(bad, schema)


def test_validator_rejects_missing_required_action_field(schema: dict[str, Any]) -> None:
    bad = {
        "version": "1.0",
        "rules": [
            {
                "id": "x.y",
                "when": {"op": "present", "field": "skill_name"},
                "then": {"severity": "warn"},
            }
        ],
    }
    with pytest.raises(SchemaError):
        validate(bad, schema)


def test_validator_accepts_nested_logical_combinators(schema: dict[str, Any]) -> None:
    good = {
        "version": "1.0",
        "rules": [
            {
                "id": "x.y",
                "when": {
                    "and": [
                        {"op": "present", "field": "skill_name"},
                        {
                            "or": [
                                {"op": "contains", "field": "skill_name", "value": ":"},
                                {"not": {"op": "missing", "field": "target_path"}},
                            ]
                        },
                    ]
                },
                "then": {"severity": "warn", "message": "ok"},
            }
        ],
    }
    validate(good, schema)
