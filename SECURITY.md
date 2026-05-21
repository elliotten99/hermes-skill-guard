# Security Policy

## Supported Versions

Security fixes are provided for the latest minor release.

## Reporting a Vulnerability

Do not open public issues for vulnerabilities.

Preferred path: use GitHub private vulnerability reporting on this repository.
If private reporting is not available, contact `@elliotten99` through the
GitHub profile and clearly mark the message as security-sensitive.

Include:

- affected version
- reproduction steps
- expected impact
- whether secrets or private data may be exposed

## Security Design

`hermes-skill-guard` defaults to dry-run audit mode and strict redaction. It
does not store raw tool payloads unless explicitly configured to do so.
