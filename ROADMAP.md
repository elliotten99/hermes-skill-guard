# Roadmap

This roadmap describes the direction of `hermes-skill-guard`. Items are
grouped by milestone and ordered by priority within each milestone. Dates
are intentionally omitted; releases ship when the work is ready and
verified against the supported Hermes Agent matrix.

## v0.1 - Audit Mode

The first release establishes a safe, observational baseline. The plugin
records what skills are being created without blocking any Hermes tool
calls.

| Capability | Status |
|---|---|
| Dry-run preflight (`pre_tool_call` evaluates rules, returns `None`) | Shipped |
| Redacted event logging (`post_tool_call` writes one event per call) | Shipped |
| Manual candidate promotion (`skill-guard promote <id>`) | Shipped |
| SQLite WAL storage with rotation by age, rows, and size | Shipped |
| User configuration (YAML file plus environment overrides) | Shipped |
| CLI: `report`, `doctor`, `promote` | Shipped |

Default behavior in v0.1: `dry_run=true`, `enforcement.mode=audit`,
`fail_open=true`. No tool call is ever blocked.

## v0.2 - Candidate Semantics

The second milestone keeps the default runtime behavior conservative while
making candidate review, doctor diagnostics, and skill relation metadata
first-class plugin surfaces. Users opt in to enforcement behavior by changing
`enforcement.mode`; the default remains `audit`.

| Capability | Status |
|---|---|
| `skill_guard_doctor` tool and `/skill-guard-doctor` diagnostics | Shipped |
| `skill_guard_relations` tool (`duplicate`, `conflict`, `supersedes`, `depends_on`, `related_to`) | Shipped |
| Manifest version `0.1.11` with doctor and relations declarations | Shipped |
| Package verification command in release checks | Shipped |
| `enforcement.mode=candidate` | Shipped |
| `enforcement.mode=block` | Shipped |
| `compatibility.py` protocol gating | Shipped |
| Configurable rule engine | Planned for v0.3+ |

v0.2 keeps `dry_run` as an independent escape hatch: setting `dry_run=true`
downgrades any enforcement decision to a warning, even when `mode` is
`candidate` or `block`.

## v0.3 (Future) - Intelligence

The third milestone introduces optional, opt-in intelligence on top of
the rule engine. None of these features are required to operate the
plugin in audit or enforcement mode.

| Capability | Description |
|---|---|
| Duplicate skill detection | Embedding-based similarity over skill name, intent, and tool signature. Surfaces likely duplicates in `report`. |
| Conflict tool early warning | Static analysis of tool dependencies to flag skills that touch overlapping resources. |
| LLM-assisted review (optional) | Offline summarization and risk scoring of candidate skills. Disabled by default; requires explicit configuration. |

## Backlog

Items below are accepted in principle but not scheduled. They graduate
into a milestone when a concrete design lands.

- Additional storage backends (PostgreSQL) for shared deployments.
- Distributed audit log with append-only export.
- Web UI for reviewing candidates, events, and rule effects.
- Structured export to SIEM-compatible formats.
- Multi-tenant scoping for `state_dir`.

## Versioning Policy

`hermes-skill-guard` follows Semantic Versioning.

| Version range | Stability guarantee |
|---|---|
| `0.x` | The public API (Python, CLI, config schema, event schema) may change between minor versions. Migration notes ship in the changelog. |
| `>= 1.0` | Backwards-incompatible changes require a major version bump. Deprecations are announced at least one minor version in advance. |

The plugin will not declare `1.0` until enforcement mode (shipped in v0.2)
has been exercised in real Hermes deployments and the event schema has been
stable for at least one minor cycle.

## How to Influence the Roadmap

The roadmap is shaped by real Hermes deployments. To propose a change:

1. Open a GitHub issue describing the use case, current pain point, and
   the smallest viable solution. Issues are preferred for concrete bugs
   or scoped feature requests.
2. Start a GitHub discussion for broader ideas, design tradeoffs, or
   anything that touches the rule engine, event schema, or enforcement
   protocol.
3. Reference the relevant ADR (`docs/adr/`) when your proposal would
   revisit a previous decision such as "plugin, not core" or
   "dry-run first".

Pull requests that implement backlog items are welcome, but please open
an issue first so the design can be agreed before code review.
