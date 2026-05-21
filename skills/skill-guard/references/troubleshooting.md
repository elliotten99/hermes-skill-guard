# Troubleshooting

Run:

```bash
hermes-skill-guard doctor
hermes-skill-guard report --json
```

Check:

- SQLite journal mode is `wal`.
- `dry_run` is enabled for first installs.
- redaction counters are not increasing unexpectedly.

