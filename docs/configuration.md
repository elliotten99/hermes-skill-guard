# Configuration

Configuration priority:

```text
environment variables > user config > plugin defaults
```

## Defaults

```yaml
dry_run: true
enforcement:
  mode: audit
  timeout_ms: 150
  fail_open: true
trace_cache:
  ttl_minutes: 10
  max_entries: 1000
events:
  ttl_days: 7
  max_rows: 10000
  max_db_mb: 50
  rotate_every_n_writes: 100
logging:
  capture_raw_payloads: false
  redaction_mode: strict
  max_field_length: 512
  hash_redacted_values: true
intents:
  enabled: []  # empty list = enable every default intent
rules_path: null
auto_promote:
  enabled: false
  min_age_hours: 168
  require_no_conflicts: true
  require_no_duplicates: false
  dry_run: true
observability:
  enabled: false
  otel_enabled: false
  prometheus_enabled: false
  prometheus_port: 0
```

`enforcement.mode` accepts `audit`, `candidate`, or `block`. `candidate` and
`block` route the `pre_tool_call` hook to return a Hermes block decision
when the deterministic policy raises an issue. `dry_run: true` always wins
and downgrades the decision to `warn`, so enabling enforcement is an
explicit two-step change (`mode` plus `dry_run: false`).

`enforcement.timeout_ms` is the hard budget for `PreflightPolicy.evaluate`.
The hook runs the policy in a small dedicated thread pool and aborts the
wait once the budget is exceeded. On timeout the `preflight_timeout_count`
counter is incremented and a warning is logged. The follow-up action
depends on `enforcement.fail_open`:

- `fail_open: true` (default) — the hook returns `None`, letting Hermes
  proceed as if no preflight had run. This protects availability over
  strict policy enforcement when the policy itself misbehaves.
- `fail_open: false` — the hook returns a block decision with the message
  `skill-guard preflight timed out and fail_open=false`. Use this only when
  the policy is reliable and you would rather refuse the call than let it
  bypass guards.

`intents.enabled` is an allow-list. The default empty list registers every
default intent (`capture`, `preflight`, `compatibility`, `candidates`,
`promotion`, `relations`, `reporting`, `auto_promote`). Intents covered by a
first-party Hermes feature are still removed by protocol gating regardless of
this setting.

`rules_path` points at an optional JSON rule file. Use it when your
organization wants to override, disable, or add deterministic policy rules.

`auto_promote` is off by default. When enabled, it scans approved candidates
and creates promotion attempts only after the configured age and relation gates
pass. Keep `auto_promote.dry_run: true` until the scan output is boring.

`observability` is off by default. The OpenTelemetry and Prometheus exporters
are additive; they do not change hook decisions.

## User Config File

`hermes-skill-guard` reads a YAML configuration file with lower priority than
environment variables but higher than defaults.

**Default path**: `~/.config/hermes-skill-guard/config.yaml`  
**Override via env**: `SKILL_GUARD_CONFIG=/path/to/config.yaml`

Example `config.yaml`:

```yaml
dry_run: false
state_dir: ~/.hermes/skill-guard
enforcement:
  mode: candidate
  timeout_ms: 200
  fail_open: true
logging:
  capture_raw_payloads: false
  redaction_mode: strict
  max_field_length: 512
  hash_redacted_values: true
events:
  ttl_days: 14
  max_rows: 5000
  max_db_mb: 25
  rotate_every_n_writes: 50
trace_cache:
  ttl_minutes: 20
  max_entries: 2000
rules_path: ./docs/examples/custom-rules.json
auto_promote:
  enabled: false
  min_age_hours: 168
  require_no_conflicts: true
  require_no_duplicates: false
  dry_run: true
observability:
  enabled: false
  otel_enabled: false
  prometheus_enabled: false
  prometheus_port: 0
```

Only keys present in the file override defaults; missing keys keep their
default values. Environment variables override everything.

## Environment Variables

| Variable | Default | Meaning |
|---|---:|---|
| `SKILL_GUARD_CONFIG` | `~/.config/hermes-skill-guard/config.yaml` | Path to user config YAML file. |
| `SKILL_GUARD_STATE_DIR` | `~/.hermes/skill-guard` | Directory containing `state.db`. |
| `SKILL_GUARD_DRY_RUN` | `true` | Downgrades enforcement decisions to warnings before they reach Hermes. |
| `SKILL_GUARD_ENFORCEMENT_MODE` | `audit` | One of `audit`, `candidate`, or `block`. `candidate` and `block` only take effect when `SKILL_GUARD_DRY_RUN=false`. |
| `SKILL_GUARD_ENABLED_INTENTS` | _(unset = all)_ | Comma-separated allow-list of intent IDs to register (`capture`, `preflight`, `compatibility`, `candidates`, `promotion`, `relations`, `reporting`). Empty/unset enables all defaults. |
| `SKILL_GUARD_PREFLIGHT_TIMEOUT_MS` | `150` | Hard budget for `pre_tool_call`. Exceeding it increments `preflight_timeout_count`; the call is allowed when `fail_open=true` and blocked otherwise. |
| `SKILL_GUARD_FAIL_OPEN` | `true` | When `false`, an unhandled exception or timeout in `pre_tool_call` returns a block decision instead of passing the call through. |
| `SKILL_GUARD_CAPTURE_RAW_PAYLOADS` | `false` | Stores raw string payload previews only when explicitly enabled. |
| `SKILL_GUARD_REDACTION_MODE` | `strict` | Documents the intended redaction mode. |
| `SKILL_GUARD_MAX_FIELD_LENGTH` | `512` | Maximum length for raw payload field previews. |
| `SKILL_GUARD_HASH_REDACTED_VALUES` | `true` | Whether to include SHA-256 hashes for redacted values. |
| `SKILL_GUARD_TRACE_CACHE_TTL_MINUTES` | `10` | In-memory pre/post correlation TTL. |
| `SKILL_GUARD_TRACE_CACHE_MAX_ENTRIES` | `1000` | Maximum cached preflight decisions. |
| `SKILL_GUARD_EVENTS_TTL_DAYS` | `7` | Event retention period. |
| `SKILL_GUARD_EVENTS_MAX_ROWS` | `10000` | Maximum event rows before rotation. |
| `SKILL_GUARD_EVENTS_MAX_DB_MB` | `50` | Maximum database size in MB before rotation. |
| `SKILL_GUARD_EVENTS_ROTATE_EVERY` | `100` | Write count between rotation checks. |
| `HSG_RULES_PATH` | _(unset)_ | Optional JSON rule file. Takes priority over YAML `rules_path`. |
| `SKILL_GUARD_AUTO_PROMOTE_ENABLED` | `false` | Enables the approved-candidate promotion scanner. |
| `SKILL_GUARD_AUTO_PROMOTE_MIN_AGE_HOURS` | `168` | Minimum age after approval before a candidate can be promoted. |
| `SKILL_GUARD_AUTO_PROMOTE_NO_CONFLICTS` | `true` | Requires no `conflict` relation before promotion. |
| `SKILL_GUARD_AUTO_PROMOTE_NO_DUPLICATES` | `false` | Requires no `duplicate` relation before promotion. |
| `SKILL_GUARD_AUTO_PROMOTE_DRY_RUN` | `true` | Reports promotable candidates without creating promotion attempts. |
| `HSG_OBSERVABILITY_ENABLED` | `false` | Master switch for optional metrics exporters. |
| `HSG_OTEL_ENABLED` | `false` | Enables OpenTelemetry metrics export when observability is enabled. |
| `HSG_PROMETHEUS_ENABLED` | `false` | Enables Prometheus metrics export when observability is enabled. |
| `HSG_PROMETHEUS_PORT` | `0` | Optional embedded Prometheus HTTP port. `0` means no embedded server. |
| `HERMES_VERSION` | _(unset)_ | Optional; consumed by the capability probe to decide which intents are covered by first-party Hermes features. Unset means "unknown" and disables protocol gating. |

## Local Development

Use `uv` and the lockfile for reproducible tooling:

```bash
uv sync --locked --extra dev
uv run --locked --extra dev pytest
uv run --locked --extra dev ruff check src tests
uv run --locked --extra dev ruff format --check src tests
uv run --locked --extra dev mypy src tests
```

The package declares `requires-python = ">=3.11,<3.14"`. Do not run release
checks with Python 3.14 until the supported matrix is updated.
