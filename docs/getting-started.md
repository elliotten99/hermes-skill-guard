# Getting Started

This guide installs `hermes-skill-guard`, checks that local storage works, and
keeps the plugin in audit mode until you choose otherwise.

## 1. Install

```bash
pip install hermes-skill-guard
hermes plugins enable skill-guard
```

For local development from a checkout:

```bash
uv sync --locked --extra dev
uv run hermes-skill-guard doctor
```

## 2. Verify Storage And Config

Run:

```bash
hermes-skill-guard doctor
```

The result should include `"ok": true` and `wal_enabled: true`. The default
state database is:

```text
~/.hermes/skill-guard/state.db
```

The default mode is intentionally non-blocking:

```yaml
dry_run: true
enforcement:
  mode: audit
  fail_open: true
```

## 3. Open Hermes With The Plugin Enabled

```bash
hermes chat
```

Useful slash commands:

```text
/skill-guard-doctor
/skill-guard-report
```

Useful tools exposed by the plugin:

- `skill_guard_preflight`
- `skill_guard_candidates`
- `skill_guard_report`
- `skill_guard_doctor`

## 4. Review Candidates

List candidates:

```bash
hermes-skill-guard candidates list
```

Inspect one candidate:

```bash
hermes-skill-guard candidates details <candidate_id>
```

Approve or reject:

```bash
hermes-skill-guard candidates approve <candidate_id>
hermes-skill-guard candidates reject <candidate_id>
```

Create a promotion attempt after approval:

```bash
hermes-skill-guard candidates promote <candidate_id>
```

The promote command does not silently write a skill into production. It
creates a tracked `skill_manage create` attempt and records the transition.

## 5. Enable Enforcement Later

Observe first. After reports are clean and operators understand the workflow,
switch from audit mode to candidate mode:

```yaml
dry_run: false
enforcement:
  mode: candidate
  fail_open: true
```

Use `block` mode only when policy rules are mature and false positives are
acceptable:

```yaml
dry_run: false
enforcement:
  mode: block
  fail_open: true
```

## 6. Add Custom Rules

Create a JSON rule file and point `HSG_RULES_PATH` at it:

```bash
export HSG_RULES_PATH="$PWD/docs/examples/custom-rules.json"
hermes-skill-guard rules validate --path "$HSG_RULES_PATH"
hermes-skill-guard rules list
```

See [Rule Engine](rule-engine.md) for the full schema and merge behavior.

## 7. Troubleshoot

Start with:

```bash
hermes-skill-guard doctor
hermes-skill-guard report --json
```

Then check:

- `sqlite_journal_mode` is `wal`.
- `dry_run` is still enabled during first rollout.
- redaction counters are not unexpectedly increasing.
- `preflight_timeout_count` is zero or explained by a known policy delay.

For deeper operational answers, see [FAQ](faq.md) and
[Runtime Safety](runtime-safety.md).
