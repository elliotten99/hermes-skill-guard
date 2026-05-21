# Intent Development

Intents are small registration units under `src/hermes_skill_guard/intents/`.
Each intent owns one behavior surface such as a tool, hook, command, or
candidate workflow.

## Interface

```python
class MyIntent:
    intent_id = "my_intent"
    priority = 50

    def register(self, adapter, context):
        adapter.register_tool(
            "skill_guard_example",
            handler,
            "Describe the example tool.",
            schema=EXAMPLE_SCHEMA,
        )
```

`registry.default_intents()` returns the active intent set. In the current line,
defaults are, in priority order:

1. `capture` (priority 10) - `post_tool_call` hook + redacted event capture.
2. `preflight` (priority 20) - `skill_guard_preflight` tool and
   `pre_tool_call` hook.
3. `compatibility` (priority 25) - `skill_guard_compat` tool and the
   capability probe used for protocol gating.
4. `candidates` (priority 30) - manual candidate workflow tool.
5. `promotion` (priority 31) - promotion attempt lifecycle.
6. `relations` (priority 32) - `skill_guard_relations` tool.
7. `reporting` (priority 40) - `skill_guard_report`, `skill_guard_doctor`,
   slash commands, and the `hermes skill-guard ...` CLI bridge.

Registration order is ascending by `priority`. Intents marked
`retired_by_official` by protocol gating are skipped during registration.

## Rules

- Use `HermesAdapter`; do not call `ctx.*` directly from intents.
- Match Hermes v0.14 keyword signatures first and keep fallback behavior in the
  adapter.
- Hook handlers must accept keyword arguments and remain forward-compatible.
- Never raise unhandled exceptions into Hermes callbacks.
- Keep `pre_tool_call` pure memory and fast.
- Return JSON strings from registered tools.
- Add unit or integration tests for every new tool, hook, command, or state
  transition.
- Update README and the relevant `docs/*.md` file when behavior changes.
