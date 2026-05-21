# Observability

The skill-guard exposes optional **OpenTelemetry** and **Prometheus**
metric exporters. Both are off by default; enabling either is purely
additive — the existing internal SQLite counters (visible via
`hermes-skill-guard doctor` and `store.summary()`) continue to work
unchanged.

## Installation

The default install carries no observability dependencies:

```bash
pip install hermes-skill-guard
```

Add one or both backends:

```bash
pip install "hermes-skill-guard[otel]"          # OpenTelemetry only
pip install "hermes-skill-guard[prometheus]"    # Prometheus only
pip install "hermes-skill-guard[observability]" # both
```

If a backend's optional dependency is missing at runtime the exporter
falls back to a `NoopExporter` and logs a warning — nothing else breaks.

## Enabling

### Environment variables

| Variable                    | Default | Description                                          |
| --------------------------- | ------- | ---------------------------------------------------- |
| `HSG_OBSERVABILITY_ENABLED` | _auto_  | Master switch. Auto-enabled if a backend is on.      |
| `HSG_OTEL_ENABLED`          | `false` | Turn on the OTel exporter.                           |
| `HSG_PROMETHEUS_ENABLED`    | `false` | Turn on the Prometheus exporter.                     |
| `HSG_PROMETHEUS_PORT`       | `0`     | If `>0`, start `start_http_server(port)` on launch.  |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | _OTel default_ | Standard OTel env var honored by the OTLP/HTTP exporter. |

### `config.yaml`

```yaml
observability:
  enabled: true
  otel_enabled: true
  prometheus_enabled: true
  prometheus_port: 9300
```

## Metric reference

All metrics use the `hsg_` prefix so they are easy to filter in dashboards.

| Name                              | Type      | Unit | Labels                  | Description                                          |
| --------------------------------- | --------- | ---- | ----------------------- | ---------------------------------------------------- |
| `hsg_pre_tool_call_total`         | counter   | 1    | `tool_name`, `decision` | Total `pre_tool_call` invocations.                   |
| `hsg_pre_tool_call_duration_ms`   | histogram | ms   | `tool_name`, `decision` | Preflight latency (includes worker dispatch).        |
| `hsg_preflight_timeout_total`     | counter   | 1    | `tool_name`             | Preflight timeouts (matches internal `preflight_timeout_count`). |
| `hsg_candidate_status_total`      | gauge     | 1    | `status`                | Candidates per status (sampled during `doctor`).     |
| `hsg_audit_log_total`             | counter   | 1    | _none_                  | Audit log row count.                                 |
| `hsg_storage_db_size_mb`          | gauge     | MB   | _none_                  | State DB size in megabytes.                          |
| `hsg_intent_registered_total`     | gauge     | 1    | _none_                  | Number of intents registered.                        |
| `hsg_intent_retired_total`        | gauge     | 1    | _none_                  | Intents retired by protocol-gating.                  |

## Relationship to internal counters

The exporter layer is **additive**: every metric also continues to be
written to the internal SQLite counter store via
`StateStore.increment_counter`. Existing tooling (the `doctor`
subcommand, `state.db` queries, the diagnostic CLI) is unaffected.

- Use internal counters for offline forensics and reproducible CI checks.
- Use the OTel / Prometheus exporters for live dashboards, alerting, and
  cross-service correlation in production.

## Failure modes

- **Backend missing**: `build_exporter` catches `ImportError` and returns
  `NoopExporter`. A warning is logged via the
  `hermes_skill_guard.observability` logger.
- **Backend errors at runtime**: each exporter swallows exceptions inside
  `record_*` so a misconfigured backend never breaks the preflight hook.
- **Port collision**: `PrometheusExporter` logs a warning if it cannot
  bind `HSG_PROMETHEUS_PORT`; metrics are still recorded into the
  registry and can be exposed by user code.
