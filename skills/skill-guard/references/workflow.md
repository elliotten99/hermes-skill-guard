# skill-guard workflow

`skill-guard` separates language guidance from deterministic operations.

- Bundled skill: explains when to call tools.
- Tools: perform preflight, candidate listing, and reporting.
- Hooks: observe `skill_manage create` and post-tool results.
- CLI: supports local doctor/report/maintenance.

v0.1 default is dry-run audit mode. Do not claim that a skill was blocked or
promoted unless the corresponding tool or CLI command confirms it.

