# ADR 0005: Skill Relations Model

## Decision

Layer a relations table on top of candidates with kinds: `duplicate`,
`conflict`, `supersedes`, `depends_on`, `related_to`. Enforce foreign
keys against the candidate set.

## Rationale

Reviewing candidates in isolation hides structure. Relations turn review
into a graph judgment, surfacing duplicates and dependencies. Foreign
keys keep the graph consistent as candidates are promoted or rejected.
