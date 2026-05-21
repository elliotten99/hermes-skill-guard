# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Optional OpenTelemetry + Prometheus metrics exporters
  (`hermes_skill_guard.observability`). Off by default; enable via
  `HSG_OTEL_ENABLED` / `HSG_PROMETHEUS_ENABLED` env vars or the new
  `observability:` config block. Install with the new `[otel]`,
  `[prometheus]`, or `[observability]` optional dependency groups.
  Exporter calls are additive to existing internal counters. See
  `docs/observability.md` for the metric catalog (`hsg_pre_tool_call_total`,
  `hsg_pre_tool_call_duration_ms`, `hsg_preflight_timeout_total`, etc.).

### Changed
- CI now runs `tests/integration/test_package_verification.py` against the
  freshly-built wheel and sdist (previously skipped without `HSG_DIST_DIR`).

### Deprecated
- _Reserved for the next release. Add APIs scheduled for removal here._

### Removed
- `hermes_skill_guard.storage.state_store` shim removed. The
  `DeprecationWarning` was emitted since v0.1.0; callers must now import
  `StateStore` from `hermes_skill_guard.storage.repository`.

### Fixed
- _Reserved for the next release. Add bug fixes here._

### Security
- _Reserved for the next release. Add security-relevant changes here._

## [0.1.11] - 2026-05-20

### Added
- `enforcement.timeout_ms` is now actively enforced via a thread-pool executor.
  When preflight policy exceeds the configured timeout, the request is allowed
  through (`fail_open=true`, default) or blocked (`fail_open=false`), with a
  `preflight_timeout_count` counter incremented in either case.
- Forward-only schema migration framework (`storage/migrations.py`) with a
  `schema_version` table (single-row, `CHECK (id = 1)`) and explicit
  `BEGIN IMMEDIATE` transactions so DDL is rolled back on failure. v1 is a
  noop baseline anchoring the current schema; future schema changes register
  as `Migration` entries instead of extending `_migrate_schema`.
- Test coverage push: 95% project total. `policy.py`, `redaction.py`,
  `__main__.py`, `intents/_extractors.py`, `intents/candidates.py`,
  `intents/preflight.py`, `storage/migrations.py` all at 100% line coverage;
  new dangling-candidate, migrations, preflight branch, and extractor test
  suites added.

### Changed
- `pyproject.toml` advertises a Beta classifier and bumps version to 0.1.11.
- `plugin.yaml` version bumped to `0.1.11`.

### Security
- CI gains a parallel `security` job running `pip-audit --skip-editable` and
  `bandit -r src/`. `pre-commit` also runs `bandit`. Three `# nosec` markers
  are documented in-line (B110 fallback in `config.py`, two B608 literal-only
  WHERE clauses in `repository.py`).

## [0.1.10] - 2026-05-20

### Added

- `enforcement.mode=candidate` and `enforcement.mode=block` now take effect in
  the `pre_tool_call` hook: when the deterministic policy raises an issue and
  `dry_run=false`, the hook returns `{"action": "block", "message": ...}` to
  Hermes. `dry_run=true` (the default) downgrades any enforcement decision to
  `warn`, so opting in to enforcement is an explicit two-step change.
- `compatibility.py` protocol gating: on `register()` the plugin probes the
  bundled Hermes capability matrix and automatically retires intents that are
  covered by a first-party Hermes feature (`retired_by_official`). Module
  status is persisted in the new `modules` table and surfaced through the
  `skill_guard_compat` tool and `hermes-skill-guard compat` subcommand
  (`probe`, `list`, `restore`).
- Skill relations: `skill_guard_relations` tool and `hermes-skill-guard
  relations {add,list,remove}` CLI subcommands. Supported relation types are
  `duplicate`, `conflict`, `supersedes`, `depends_on`, and `related_to`.
- `skill_guard_doctor` tool and the matching `hermes-skill-guard doctor`
  subcommand and `/skill-guard-doctor` slash command. `--check` accepts
  `all`, `storage`, `config`, `candidates`, `counters`, or `compat`.
- `hermes-skill-guard verify package` CLI subcommand validates that a built
  wheel or sdist contains the data files and bundled skill required at
  runtime. `scripts/verify-release.sh` now calls it against the built
  artifacts.
- Storage gained `promotion_attempts`, `skill_relations`, and `modules` tables
  with foreign keys back to `candidates`, plus probe-result persistence,
  `find_pending_promotion_by_skill`, `find_related_candidates`, and module
  status helpers.
- Test suite expanded with golden, integration, and contract coverage for the
  new intents and CLI surface; total line coverage is 91%.
- `plugin.yaml` now declares the `skill_guard_doctor` and
  `skill_guard_relations` tools for the v0.2 candidate surface.
- Documentation now describes v0.2 as a candidate line for doctor diagnostics,
  relation metadata, compatibility gating, and release packaging checks.

### Changed

- `plugin.yaml` version bumped to `0.1.10`.

### Deprecated

- `hermes_skill_guard.storage.state_store` remains a `DeprecationWarning`
  re-export of `StateStore`; the shim is scheduled for removal in the next
  minor release. New code must import from
  `hermes_skill_guard.storage.repository`.

## [0.1.0] - 2026-05-20

### Added

- Initial Hermes plugin skeleton targeting Hermes Agent `v0.14.0` and newer.
- Deterministic `skill_manage create` preflight in dry-run audit mode.
- Redacted event logging with SQLite WAL storage.
- Candidate pool state machine with `detected -> candidate -> approved -> promoted -> archived` lifecycle.
- `skill_guard_preflight`, `skill_guard_candidates`, `skill_guard_promote`, and `skill_guard_report` tools.
- `pre_tool_call` and `post_tool_call` hooks with `tool_call_id` correlation.
- Slash commands `/skill-guard-report` and `/skill-guard-doctor`.
- `hermes skill-guard ...` CLI subcommands (doctor, report, candidates, storage, rules) and standalone `hermes-skill-guard` entrypoint.
- Auto-candidate creation in `post_tool_call` when the preflight decision is `CANDIDATE` or `WARN`.
- User configuration file support (`~/.config/hermes-skill-guard/config.yaml`) with `env > user config > defaults` priority.
- Bundled `skill-guard` skill (read-only, namespaced under the plugin).
- Public API exposed from `hermes_skill_guard` package root (`GuardConfig`, `load_config`, `register`, schemas).
- 45 tests (unit + integration + golden + smoke) with 87% line coverage.
- GitHub Actions CI with uv caching, lint/test/build jobs, and matrix testing on Python 3.11/3.12/3.13.
- PyPI Trusted Publishing release workflow triggered by `v*` tags.
- Multi-stage Docker build using `uv` and a non-root `appuser`.
- Community files: `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), expanded `CONTRIBUTING.md`, `ROADMAP.md`, `docs/faq.md`, `CODEOWNERS`, `PULL_REQUEST_TEMPLATE.md`, `dependabot.yml`, `.editorconfig`, `.dockerignore`.

### Changed

- `plugin.py` now resolves the bundled skill via `importlib.resources` with a fallback to the legacy repo-relative path, making the skill loadable from wheel, editable, and Docker installs.
- Bundled skill files moved to `src/hermes_skill_guard/_bundled_skills/skill-guard/` and force-included into the wheel via Hatch.
- `FakeHermesContext` test fixture extracted to `tests/conftest.py` for reuse across integration tests.

### Deprecated

- `hermes_skill_guard.storage.state_store` module re-exports `StateStore` for backwards compatibility and emits a `DeprecationWarning`. Use `hermes_skill_guard.storage.repository` instead. The shim is scheduled for removal in a later 0.x release.
