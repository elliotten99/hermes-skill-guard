---
name: skill-guard
description: Use when reviewing, auditing, or managing Hermes agent-created skill creation.
version: 0.1.0
---

# Skill Guard

Use this plugin skill when a task involves creating, reviewing, approving, or
diagnosing Hermes skills.

Required workflow:

1. Before creating a new skill, call `skill_guard_preflight`.
2. Treat v0.1 as audit-only unless the user explicitly disables dry-run.
3. Use `skill_guard_report` to explain current guard state.
4. Use `skill_guard_candidates` for manual candidate review.

Boundaries:

- Do not edit plugin-bundled skills with `skill_manage`.
- Do not bypass official Hermes curator ownership.
- Do not store secrets in skills.

For details, read `references/workflow.md`.

