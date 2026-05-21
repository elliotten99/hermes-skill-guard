# ADR 0004: Compatibility Protocol Gating

## Decision

Probe the Hermes version at startup and skip intents already covered by
Hermes core.

## Rationale

The plugin must not duplicate Hermes responsibilities. Gating by detected
capability lets the plugin retire features automatically as Hermes
evolves, avoiding drift and double-enforcement.
