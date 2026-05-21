# FAQ

Short answers to the questions that come up most often. For deeper
background, see `architecture.md`, `runtime-safety.md`, and
`configuration.md`.

## General

### What does `hermes-skill-guard` do?

It is a Hermes Agent plugin that observes `skill_manage` and related tool
calls, evaluates deterministic rules in memory, and writes a redacted
audit event for each call. By default it never blocks a call; it records
what the agent tried to do and surfaces candidates for human review. The
current line includes opt-in enforcement (`candidate` and `block` modes) and
protocol gating that retires intents already covered by first-party Hermes
capabilities.

### Why not just disable `skill_manage` in Hermes?

Disabling the tool removes the agent's ability to extend itself, which
defeats the point of running Hermes. `skill-guard` keeps the capability
available but adds an audit trail and a path to enforce policy once you
trust the signal.

### Is this part of Hermes core?

No. It is a separate plugin, by design. See
`docs/adr/0001-plugin-not-core.md` for the rationale.

## Installation

### What is the minimum Python version?

Python 3.11. The package declares `requires-python = ">=3.11,<3.14"` and
is tested on 3.11, 3.12, and 3.13.

### What is the minimum Hermes Agent version?

Hermes Agent v0.14. v0.14 introduced the `pre_tool_call` and
`post_tool_call` hook protocol that `skill-guard` relies on.

### Do I need root to install it?

No. `pip install hermes-skill-guard` works inside a virtualenv or with
`uv` in user mode. The plugin writes only to `state_dir`
(default `~/.hermes/skill-guard`) and reads only its config file.

## Configuration

### What is the default behavior after installation?

`dry_run=true`, `enforcement.mode=audit`, `fail_open=true`. No tool call
is blocked. Events are written to SQLite under `~/.hermes/skill-guard/`.

### How do I enable enforcement?

The plugin ships two opt-in enforcement modes: `candidate` and `block`. To turn
either of them on:

1. In your YAML config (or the matching env vars), set
   `enforcement.mode: candidate` or `enforcement.mode: block`.
2. Also set `dry_run: false`. `dry_run: true` deterministically downgrades
   enforcement decisions to `warn` audit rows and lets the call through,
   regardless of `mode`.

In `candidate` mode the `pre_tool_call` hook routes the offending
`skill_manage create` into the candidate queue and returns a block decision
to Hermes pointing the operator at the queue. In `block` mode the hook
blocks the call outright with the rule-derived reason summary. In `audit`
mode (the default) decisions are stored but the hook always returns
`None`.

### How does protocol gating work?

At plugin registration the bundled Hermes capability matrix
(`hermes_skill_guard/data/compat.yaml`) is checked against the
`HERMES_VERSION` environment variable. Any intent that is covered by a
first-party Hermes feature at or below the detected version is marked
`retired_by_official` in the `modules` table and skipped during
registration, so `skill-guard` never races or shadows native Hermes
behavior. Use `hermes-skill-guard compat list` to inspect module status
and `hermes-skill-guard compat restore <intent_id>` (or the
`skill_guard_compat` tool) to re-enable a retired intent.

### Which configuration source wins?

Highest priority first:

```text
environment variables > user config (YAML) > plugin defaults
```

Only keys present in the YAML file override defaults. Missing keys keep
their default values.

## Performance

### How much latency does the hook add?

`pre_tool_call` is pure memory: no SQLite writes, no filesystem reads,
no network. Typical overhead is sub-millisecond. The budget is set by
`enforcement.timeout_ms` (default 150 ms) for future enforcement paths.

### Does SQLite block the hook?

The write happens in `post_tool_call`, which Hermes treats as
observational. Writes use WAL with `busy_timeout=3000`. If SQLite is
busy, the counter `sqlite_busy_count` increments and the event is
dropped (`dropped_write_count`). Hermes is never blocked.

### How does event rotation work?

Rotation runs at most every `events.rotate_every_n_writes` writes
(default 100) and trims by three independent limits:

| Limit | Default |
|---|---:|
| `events.ttl_days` | 7 |
| `events.max_rows` | 10000 |
| `events.max_db_mb` | 50 |

Rotation failures increment `rotation_failed_count` and never propagate
into hooks.

## Security

### When are raw payloads recorded?

Only when `logging.capture_raw_payloads=true` is explicitly set. The
default is `false`. Even when enabled, payloads pass through the
redactor first.

### Where does the database live?

`${state_dir}/state.db`, default `~/.hermes/skill-guard/state.db`. The
directory is created with the user's umask; no system-wide path is used.

### Can I turn redaction off?

No. Redaction is not a feature flag. In strict mode (the default and
only mode currently shipped), sensitive keys are replaced, secret patterns
in strings are detected, and non-secret strings are summarized with type,
length, and SHA-256. The only knob is whether raw payloads are captured
at all (see above).

## Operations

### A candidate is stuck in `detected`. What now?

Run `skill-guard report` to confirm the candidate is recorded, then
`skill-guard promote <candidate_id>` to move it forward. If `promote`
errors, run `skill-guard doctor` to check storage health and the failure
counters (`pre_tool_call_failed`, `post_tool_call_failed`,
`capture_failed_count`).

### How do I recover from a corrupted database?

Stop Hermes, move `state.db`, `state.db-wal`, and `state.db-shm` aside,
and restart. The plugin recreates an empty database on next hook. The
moved files can be opened with the `sqlite3` CLI for forensic recovery;
no events are replayed automatically.
