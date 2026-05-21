# Governance

`hermes-skill-guard` currently uses a maintainer-led governance model.

## Maintainer Responsibilities

Maintainers are responsible for:

- reviewing security-sensitive changes before merge
- keeping release automation and package metadata accurate
- preserving deterministic, auditable behavior in hook paths
- triaging issues and pull requests in a reasonable timeframe
- documenting compatibility changes with Hermes Agent

## Decision Process

Small fixes can be merged after normal review and passing CI. Changes that
affect policy behavior, candidate state transitions, storage schema, release
automation, redaction, or enforcement defaults should include one of:

- a linked issue describing the problem
- an ADR under `docs/adr/`
- a design note in the pull request

Security-sensitive defaults require maintainer approval.

## Contribution Rights

External contributors retain copyright to their work and submit contributions
under the MIT license. Pull requests must certify the Developer Certificate of
Origin statement in the PR template.

## Release Authority

Only maintainers with PyPI Trusted Publishing and GitHub release access may
cut a release. Release tags must follow `vMAJOR.MINOR.PATCH`.
