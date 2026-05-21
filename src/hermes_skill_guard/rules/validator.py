"""Minimal JSON Schema validator for the configurable rule engine.

Supports the JSON Schema Draft 2020-12 keywords used by
``data/rules.schema.json``:

* ``type``, ``enum``, ``const``
* ``properties``, ``required``, ``additionalProperties``
* ``items``, ``minItems``, ``uniqueItems``
* ``oneOf``, ``$ref`` (intra-document, ``#/$defs/...``)
* ``minLength``, ``minimum``, ``pattern``

Anything else in the schema is treated as a documentation-only annotation
so we can keep the dependency footprint at zero (no ``jsonschema`` import).
"""

from __future__ import annotations

import json
import re
from typing import Any


class SchemaError(ValueError):
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
    """Validate *instance* against *schema*.

    Raises :class:`SchemaError` on the first violation.
    """
    _validate(instance, schema, schema, "$")
