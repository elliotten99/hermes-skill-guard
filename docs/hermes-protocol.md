# Hermes Protocol

`hermes-skill-guard` targets the Hermes Agent v0.14 plugin
protocol. The minimum compatibility line is `v2026.5.16`; current development
is checked against local Hermes `main` at `ff0a7038`.

## Plugin API Surface

The plugin manifest is `plugin.yaml`. Runtime registration happens from
`hermes_skill_guard.plugin:register`.

Hermes v0.14 exposes these relevant `PluginContext` methods:

- `ctx.register_tool(name, toolset, schema, handler, ...)`
- `ctx.register_hook(name, handler)`
- `ctx.register_command(name, handler, description="", args_hint="")`
- `ctx.register_cli_command(name, help, setup_fn, handler_fn=None, description="")`
- `ctx.register_skill(name, path)`

This plugin uses `HermesAdapter` as a compatibility layer. The real v0.14
keyword signatures are the primary path; older positional test contexts are
fallbacks only.

## Registered Tools

All tool handlers return JSON strings.

- `skill_guard_preflight`: evaluates a proposed tool call with deterministic
  rules.
- `skill_guard_candidates`: lists, approves, or rejects candidate skills.
- `skill_guard_relations`: adds, lists, or removes relations between candidate
  skills. Supported relation types are `duplicate`, `conflict`, `supersedes`,
  `depends_on`, and `related_to`.
- `skill_guard_compat`: probes the local Hermes capability matrix, lists
  module status, or restores a module that was retired by protocol gating.
- `skill_guard_doctor`: returns storage, config, candidate, counter, compat,
  and recent risk diagnostics.
- `skill_guard_report`: returns summary state, counters, dry-run mode, and WAL
  status.

Tool schemas are OpenAI-function-style dictionaries with `name`,
`description`, and `parameters`.

## Hooks

### `pre_tool_call`

Hermes invokes plugin pre hooks with keyword arguments:

- `tool_name: str`
- `args: dict`
- `task_id: str`
- `session_id: str`
- `tool_call_id: str`

Hermes supports blocking when a pre hook returns:

```json
{"action": "block", "message": "Reason"}
```

The current line keeps the safe default (`dry_run=true`, `enforcement.mode=audit`)
and returns `None` in that configuration. When the operator opts in with
`dry_run=false` and `enforcement.mode=candidate` or `enforcement.mode=block`,
the deterministic preflight returns the corresponding block decision back to
Hermes:

- `candidate` mode: routes the skill into the candidate queue and blocks the
  current `skill_manage create` call with a message pointing at the queue.
- `block` mode: blocks the current call outright with the rule-derived
  reason summary.

`dry_run=true` always wins: any enforcement decision is downgraded to a `warn`
audit row and the hook returns `None`. Decisions are cached in memory using
`tool_call_id` as the correlation key when Hermes supplies it.

### Protocol Gating

At plugin registration the bundled Hermes capability matrix
(`hermes_skill_guard/data/compat.yaml`) is probed against `HERMES_VERSION`.
For each intent that is covered by a first-party Hermes feature at or below
the detected version, the corresponding module row is marked
`retired_by_official` and the intent is skipped during registration. This
prevents `skill-guard` from racing or shadowing native Hermes behavior.
Operators can restore a retired intent with `hermes-skill-guard compat
restore <intent_id>` or the `skill_guard_compat` tool.

### `post_tool_call`

Hermes invokes post hooks with keyword arguments:

- `tool_name: str`
- `args: dict`
- `result: str`
- `task_id: str`
- `session_id: str`
- `tool_call_id: str`
- `duration_ms: int`

Hermes ignores `post_tool_call` return values. This plugin uses the hook only
to redact and persist an event, then writes the matching audit decision when
the trace cache contains the `tool_call_id`.

## Boundaries

The current line evaluates only explicit agent tool calls to
`skill_manage create`. It excludes curator actions, plugin-bundled skills, hub
skills, built-ins, manual file edits, and filesystem watchers.

The bundled skill is read-only and not stored under `~/.hermes/skills/`.
