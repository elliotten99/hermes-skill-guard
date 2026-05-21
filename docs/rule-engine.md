# Configurable Rule Engine

> Status: implemented (T10, v0.1).  
> Audience: operators tailoring `hermes-skill-guard` to a specific org policy.

## Overview

`hermes-skill-guard` evaluates every `skill_manage create` invocation through a deterministic preflight policy.  Starting with the T10 work the configurable checks are expressed declaratively as **JSON rules**; a user can extend, disable or override them via a single rule file.

The rule pipeline is:

1. Load **built-in defaults** from `src/hermes_skill_guard/data/default_rules.json` (shipped inside the wheel).
2. Optionally load a **user file** referenced by `GuardConfig.rules_path` (env var `HSG_RULES_PATH` takes precedence).
3. Merge the two: rule ids in `user.disabled_rules` are dropped, then user `rules` override defaults by id.  Any extra user rules are appended.
4. Sort by `priority` ascending (default `100`).
5. Evaluate each enabled rule's `when` against the call context; collect firing rules.
6. Combine severities: the engine picks the highest severity among firing rules (`info < warn < candidate < block`).  `info` adds a reason but never escalates from `allow`.
7. Apply external modifiers (`enforcement.mode`, `dry_run`) inside `PreflightPolicy.evaluate`.

## CLI

```bash
# List all active rules (defaults + user overrides)
python -m hermes_skill_guard rules list

# Validate the configured user rule file
python -m hermes_skill_guard rules validate

# Validate a specific file
python -m hermes_skill_guard rules validate --path ./my-rules.json
```

## Schema reference

Rule files validate against `src/hermes_skill_guard/data/rules.schema.json` (JSON Schema Draft 2020-12).

Top-level object:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `version` | string | yes | Must be `"1.0"`. |
| `disabled_rules` | string[] | no | Built-in rule ids to skip. |
| `rules` | Rule[] | no | New or overriding rules. |

Rule object:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Dotted identifier, unique in the merged set. Pattern `[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+`. |
| `description` | string | no | Human-readable purpose. |
| `enabled` | boolean | no | Default `true`. |
| `priority` | integer >= 0 | no | Lower = evaluated first. Default `100`. |
| `when` | Condition | yes | Boolean condition tree (see below). |
| `then` | Action | yes | `{severity, message}`. |

Action object:

| Field | Type | Description |
| --- | --- | --- |
| `severity` | `info` \| `warn` \| `candidate` \| `block` | Contribution to final decision. |
| `message` | string | Reason text. Supports `{field_name}` placeholders for context fields; unknown placeholders render as empty strings. |

## Condition language

Conditions are represented as **nested JSON objects** rather than a string DSL.  This avoids parsing ambiguities, keeps the schema self-describing, and prevents injection of arbitrary expressions.

### Leaf

```json
{ "op": "<operator>", "field": "<field>", "value": <value>, "ignore_case": false }
```

`value` is required for every op except `missing` and `present`.  `ignore_case` only affects string ops (`equals`, `not_equals`, `contains`, `not_contains`, `matches`).

### Combinators

```json
{ "and": [ <condition>, <condition>, ... ] }
{ "or":  [ <condition>, <condition>, ... ] }
{ "not": <condition> }
```

`and` and `or` require at least one child.

### Operators

| Operator | Field type | Value type | Semantics |
| --- | --- | --- | --- |
| `equals` | any | matches field | Strict equality.  With `ignore_case`, string comparison is lowercased. |
| `not_equals` | any | matches field | Negation of `equals`. |
| `contains` | string | string | Substring match. |
| `not_contains` | string | string | Negation of `contains`. |
| `matches` | string | string (regex) | Python `re.search`.  Anchor explicitly with `^` / `$` if needed. |
| `missing` | any | omitted | Field is `None`, empty string, or whitespace-only string. |
| `present` | any | omitted | Negation of `missing`. |
| `length_less_than` | string | integer | `len(field) < value`. |
| `length_greater_than` | string | integer | `len(field) > value`. |
| `length_equals` | string | integer | `len(field) == value`. |

### Available fields

The engine builds a `RuleContext` from the `ToolCall` using `intents._extractors`:

| Field | Source | Type |
| --- | --- | --- |
| `skill_name` | `extract_skill_name(args)` | string \| None |
| `tool_name` | `call.tool_name` | string |
| `content` | `extract_content(args)` | string |
| `content_length` | `len(content)` | integer |
| `description` | `extract_description(args)` | string |
| `target_path` | `extract_target_path(args)` | string \| None |
| `dry_run` | `config.dry_run` | boolean |
| `enforcement_mode` | `config.enforcement.mode` | `"audit"` \| `"candidate"` \| `"block"` |

## Loading order and merge semantics

```python
final_rules = [overrides.get(r.id, r) for r in default_rules if r.id not in disabled]
final_rules += extras
final_rules.sort(key=lambda r: (r.priority, r.id))
```

- A user rule with an id matching a default rule **replaces** the default entirely.
- `disabled_rules` is the recommended way to drop a default rule without redefining it.
- Extra rules (ids not in defaults) are appended.
- The merged set is sorted by `priority` ascending, then `id` ascending.

## Failure policy

| Scenario | `audit` mode | `block` mode |
| --- | --- | --- |
| Bundled defaults corrupt | Fatal (raise) | Fatal (raise) |
| User file missing | Fall back to defaults | Fall back to defaults |
| User file invalid JSON / schema | Log warning, fall back to defaults | Fatal (raise) |

## Integration with PreflightPolicy

`PreflightPolicy.evaluate` uses the rule engine for the configurable checks while keeping three boundary guards as hard-coded short-circuits:

1. `tool_name != "skill_manage"` → `ALLOW`
2. `operation != "create"` → `ALLOW`
3. `skill_guard_promotion_attempt_id` present → `ALLOW`

After the rule engine returns its result, `PreflightPolicy` applies:

- `enforcement.mode` escalation (`audit` keeps `warn`, `candidate` escalates to `candidate`, `block` escalates to `block`)
- `dry_run=true` downgrade (forces `warn`)

This separation means rules declare their "natural" severity; the runtime policy layer maps that to the final enforcement decision.

## Examples

A complete example lives in `docs/examples/custom-rules.json`.  Highlights:

- **Disable a default rule and replace it with a stricter version**

  ```json
  {
    "version": "1.0",
    "disabled_rules": ["manifest.description_too_short"],
    "rules": [
      {
        "id": "org.min_description_length",
        "when": { "op": "length_less_than", "field": "content", "value": 80 },
        "then": { "severity": "warn", "message": "content must be >= 80 chars (got {content_length})" }
      }
    ]
  }
  ```

- **Block a reserved namespace**

  ```json
  {
    "id": "org.banned_namespace",
    "when": { "op": "matches", "field": "skill_name", "value": "^internal:" },
    "then": { "severity": "block", "message": "'{skill_name}' uses reserved namespace" }
  }
  ```

## Limitations

- No arithmetic, no cross-field comparison (`field A == field B`).
- No access to the raw `args` dict beyond the extractor outputs above.  Adding a field requires updating `_extractors`, the schema enum, and this doc.
- Regex flavour is Python `re`; users must escape backslashes in JSON.
- Severity is per-rule, not per-condition.  Compose with `and` / `or` if you need multi-criteria gating.
- Rules cannot mutate the context or short-circuit other rules; they only contribute reasons + severity.
- Rules are loaded once at `PreflightPolicy` construction.  File changes require a process restart.

## See also

- `docs/adr/0006-configurable-rule-engine.md` — architectural rationale
- `src/hermes_skill_guard/data/rules.schema.json` — canonical schema
- `src/hermes_skill_guard/data/default_rules.json` — built-in rules
- `src/hermes_skill_guard/rules/` — loader, engine, validator, context
