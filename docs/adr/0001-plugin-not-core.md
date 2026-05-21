# ADR 0001: Plugin, Not Core Patch

## Decision

Implement skill governance as a Hermes plugin.

## Rationale

The plugin can observe `skill_manage create` without forking Hermes or
conflicting with official curator.

