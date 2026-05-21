# ADR 0006: Configurable Rule Engine

## Status

Accepted — implemented in T10 (v0.1).

## Context

`PreflightPolicy.evaluate` (v0.1) contained five hard-coded checks embedded in Python:

1. `manifest.name_missing` — skill name absent
2. `naming.plugin_namespace` — name contains `:`
3. `manifest.description_too_short` — combined content < 20 chars
4. `safety.secret_pattern` — content matches known secret regexes
5. `lifecycle.dry_run_downgrade` — informational marker when `dry_run=true`

This worked for the initial release but blocked operators from:

- Adding org-specific checks (e.g. banned namespaces, required prefixes)
- Disabling a default check without forking the codebase
- Changing severity (e.g. treating secret patterns as `block` instead of `warn`)
- A/B testing rule changes before rolling them out

We needed a mechanism to externalise the configurable checks while keeping boundary guards (tool_name, operation, promotion_attempt_id) as hard-coded short-circuit logic.

## Decision

We will use a **JSON-based declarative rule engine** with the following design:

- **Format**: JSON files (not YAML, not Python, not Rego)
- **Schema**: JSON Schema Draft 2020-12, validated by a zero-dependency minimal validator
- **Condition language**: Nested JSON objects (leaf ops + `and`/`or`/`not` combinators)
- **Deployment**: Built-in defaults shipped in the wheel + optional user file referenced by `GuardConfig.rules_path`
- **Evaluation**: Pure-memory, stateless `RuleEngine` invoked by `PreflightPolicy.evaluate`

### Why JSON over alternatives

| Alternative | Rejected because |
|---|---|
| YAML | Inconsistent parsing ( Norway problem ), harder to programmatically generate, no schema advantage for our use case |
| Python code (lambda / function strings) | Security risk (arbitrary code execution), harder to sandbox, no static validation |
| OPA/Rego | Heavy dependency (Open Policy Agent or WASM runtime), steeper learning curve for operators, overkill for ~10 checks |
| SQLite / SQL rules | Requires schema migration, no versioned file semantics, harder to diff in PRs |
| Embedded DSL (string) | Parsing ambiguity, injection risk, harder to validate statically |

JSON was chosen because:

1. **Zero new runtime dependencies** — Python `json` module is stdlib
2. **Self-describing schema** — JSON Schema gives us completion, validation, and documentation in one file
3. **Git-friendly** — rules change via PRs, diffable, reviewable
4. **Programmatically generatable** — CI pipelines or admin UIs can emit JSON without a YAML parser
5. **Hermes-native** — Hermes skills already use JSON schemas for tool definitions; operators are familiar with the pattern

### Why a custom condition language instead of Rego/JQ

Our condition needs are extremely narrow: equality, substring, regex, presence, and length comparisons against a flat context of ~8 fields. A full expression language (Rego, JQ,CEL) would add:

- A parser dependency or WASM runtime
- Operator training burden
- Security surface (arbitrary expression evaluation)
- Complexity without proportional value

The nested JSON condition tree is verbose but unambiguous, trivial to validate structurally, and impossible to inject.

### Why loader merges defaults + user file

This gives us three usage patterns:

1. **Stock install** — no user file, defaults apply unchanged
2. **Selective override** — `disabled_rules` drops unwanted defaults; user `rules` add new ones
3. **Full replacement** — user file redefines every rule id, effectively forking the policy

Merge happens at load time (process start), not per-evaluation, keeping the hot path a simple list iteration.

## Consequences

### Positive

- Operators can customise policy without code changes
- Rule changes are version-controlled and reviewable
- The engine is hermetic: no I/O during evaluation, easy to unit test
- Built-in rules serve as living documentation of the v0.1 policy

### Negative

- Condition language is intentionally limited: no arithmetic, no cross-field comparison, no function calls
- Adding a new context field requires updating `_extractors`, the schema enum, and the documentation
- Regex flavour is Python `re`; operators must escape backslashes in JSON strings
- No hot-reload: rule file changes require process restart

### Neutral

- Boundary checks (tool_name, operation, promotion_attempt_id) remain hard-coded in `PreflightPolicy`. They are short-circuit guards that should never be configurable because misconfiguration would break the plugin contract with Hermes.
- `dry_run` downgrade and `enforcement.mode` escalation remain code-level concerns, not rule-level. Rules declare their "natural" severity; the policy layer maps that to the final decision.

## Migration from v0.1 hard-coded checks

The five v0.1 checks were ported to `default_rules.json` with identical semantics:

| v0.1 rule id | Condition | Severity |
|---|---|---|
| `manifest.name_missing` | `missing skill_name` | `warn` |
| `naming.plugin_namespace` | `present skill_name` + `contains ":"` | `warn` |
| `manifest.description_too_short` | `length_less_than content 20` | `warn` |
| `safety.secret_pattern` | `matches content <secret-regex>` | `warn` |
| `lifecycle.dry_run_downgrade` | `equals dry_run true` | `info` |

`PreflightPolicy.evaluate` now delegates the configurable checks to `RuleEngine.evaluate` while retaining the boundary guards and enforcement mapping in code.
