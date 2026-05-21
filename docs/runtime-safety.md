# Runtime Safety

`hermes-skill-guard` is designed to be unable to break Hermes during normal
operation. Hook failures are caught, counted, and fail open.

## Pre Hook

`pre_tool_call` is pure memory:

- no SQLite reads
- no filesystem reads
- no directory scans
- no network calls
- no LLM calls
- no embedding search

The hook may write SQLite rows (one event, one audit row, and at most one
candidate) only when the deterministic policy decides to route the call to
the candidate queue or block it; that path is taken solely when
`enforcement.mode` is `candidate` or `block` and `dry_run=false`. The default
configuration (`dry_run=true`, `enforcement.mode=audit`) never reaches the
persistence branch.

Hermes v0.14 allows `pre_tool_call` hooks to block a tool by returning
`{"action": "block", "message": "..."}`. The current plugin returns a block
decision in two cases:

1. The deterministic policy raises an issue, `enforcement.mode` is
   `candidate` or `block`, and `dry_run=false`.
2. The hook itself fails and `enforcement.fail_open=false`.

In every other case (including `dry_run=true`, `audit` mode, or fail-open with
an exception), the hook evaluates the rules, records the decision in
`TraceCache`, and returns `None`. `dry_run=true` deterministically downgrades
any enforcement decision to `warn` before the hook returns.

## Post Hook

`post_tool_call` is observational. Hermes ignores its return value, and this
plugin never mutates the tool result. The hook:

1. Looks up the cached preflight decision by `tool_call_id`.
2. Redacts the hook payload and result preview.
3. Writes one event to SQLite.
4. Writes the audit decision if correlation succeeded.
5. Increments counters for cache misses or failures.

## Storage

SQLite uses WAL:

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=3000;
PRAGMA foreign_keys=ON;
```

Events rotate by age, row count, and database size. Rotation failures increment
`rotation_failed_count` and do not propagate into Hermes hooks.

## Redaction

Raw payload capture is disabled by default. In strict mode:

- sensitive keys such as `password`, `token`, `api_key`, and `authorization`
  are replaced with redaction summaries
- common secret patterns are detected in strings
- non-secret strings are summarized with type, length, and SHA-256
- long result previews are capped by `logging.max_field_length`

## Failure Counters

Important counters surfaced by `skill_guard_report` and `doctor` include:

- `fail_open_count`
- `pre_tool_call_failed:<ExceptionName>`
- `post_tool_call_failed:<ExceptionName>`
- `capture_failed_count`
- `trace_cache_miss_count`
- `sqlite_busy_count`
- `dropped_write_count`
- `rotation_failed_count`
