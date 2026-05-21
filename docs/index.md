# Documentation

Languages: English | [简体中文](zh-CN/index.md)

This directory is the long-form documentation for `hermes-skill-guard`. The
root README stays short; operational detail lives here.

## Start

| Document | What it covers |
|---|---|
| [Getting Started](getting-started.md) | Install, doctor, audit-mode rollout, candidate review. |
| [Configuration](configuration.md) | YAML config, environment variables, enforcement, rules, metrics. |
| [FAQ](faq.md) | Short operational answers. |

## Run

| Document | What it covers |
|---|---|
| [Runtime Safety](runtime-safety.md) | Threat model, redaction, fail-open behavior, raw payload boundaries. |
| [Rule Engine](rule-engine.md) | Custom rule schema, merge order, severity mapping. |
| [Observability](observability.md) | OpenTelemetry and Prometheus metrics. |
| [Data Model](data-model.md) | SQLite tables, retention, migrations. |

## Build

| Document | What it covers |
|---|---|
| [Architecture](architecture.md) | Components, diagrams, data flow, extension points. |
| [Hermes Protocol](hermes-protocol.md) | Hermes plugin API assumptions and hook payloads. |
| [Intent Development](intent-development.md) | How to add or change an intent. |
| [Publishing](publishing.md) | Release gates, package verification, trusted publishing. |

## Decisions

Architecture decision records live in [adr/](adr/):

- [0001: plugin, not core](adr/0001-plugin-not-core.md)
- [0002: dry-run first](adr/0002-dry-run-first.md)
- [0003: enforcement modes](adr/0003-enforcement-modes.md)
- [0004: compatibility protocol gating](adr/0004-compatibility-protocol-gating.md)
- [0005: skill relations model](adr/0005-skill-relations-model.md)
- [0006: configurable rule engine](adr/0006-configurable-rule-engine.md)
