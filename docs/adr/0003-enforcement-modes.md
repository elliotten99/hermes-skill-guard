# ADR 0003: Enforcement Modes

## Decision

Introduce three enforcement modes: `audit`, `candidate`, and `block`.
`dry_run` remains an orthogonal escape hatch.

## Rationale

Governance must roll out gradually. `audit` only observes, `candidate`
collects skills for human review, `block` rejects outright. Separating
`dry_run` from mode keeps the kill switch independent of policy choice.
