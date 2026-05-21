# Data Model

Tables:

- `events`: high-frequency redacted facts (one row per hook firing).
- `audit_log`: plugin decisions (one row per recorded `Decision`, including
  candidate transitions and relation events).
- `candidates`: candidate skills with state-machine status, promotability,
  optional captured content, and target path.
- `candidate_transitions`: candidate state changes, linked back to the event
  that triggered them.
- `counters`: failure and degradation counters.
- `promotion_attempts`: pending or finalized promotion calls. A promotion
  attempt is created when an operator promotes an approved candidate and
  finalized when the matching official `skill_manage create` is observed.
- `skill_relations`: relation edges between candidates. `relation_type` is
  one of `duplicate`, `conflict`, `supersedes`, `depends_on`, `related_to`.
  A unique index on `(source_candidate_id, target_candidate_id,
  relation_type)` prevents duplicates.
- `modules`: per-intent module status used by protocol gating. One row per
  intent, keyed by `intent_id`; `status` is one of `enabled`,
  `candidate_for_retirement`, or `retired_by_official`. Populated by the
  `CapabilityProbe` at registration and by the `skill_guard_compat` tool.

Correlation:

- `event_id` identifies one fact.
- `trace_id` links pre-hook, post-hook, audit, candidates, promotion attempts,
  and relation audit rows.
- candidate transitions, promotion attempts, and relation additions all write
  audit records.

Candidate lifecycle:

```text
detected -> candidate -> approved -> promoted -> archived
candidate -> rejected
promoted -> dangling
candidate -> archived
```

Promotion attempt lifecycle: `pending -> succeeded` (on observed
`skill_manage create` with a matching `skill_guard_promotion_attempt_id`) or
`pending -> failed` (on error). A successful promotion attempt transitions
the candidate from `approved` to `promoted` atomically.

Field definitions live in `src/hermes_skill_guard/storage/repository.py`
(`CREATE TABLE` statements in `StateStore.initialize`).

## Migrations

Forward-only schema migrations are tracked in the `schema_version` table
(single-row, primary key pinned to `1`) and applied by
`hermes_skill_guard.storage.migrations.apply_migrations`, which
`StateStore.initialize` invokes after the idempotent `CREATE TABLE IF NOT
EXISTS` block runs.

- `Migration(version, description, up)` registers a step in `MIGRATIONS`.
- Each step runs inside its own SQLite transaction; on failure the version
  pointer is *not* advanced and the exception propagates.
- The current baseline is **v1** (the v0.1.10 schema). Future column
  changes append `Migration(version=2, ...)` etc.; consult the
  `migrations.py` module docstring for the contributor checklist.
- Downgrade is not supported on the v0.x line — restore from backup.

