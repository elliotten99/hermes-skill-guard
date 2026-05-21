# Publishing

Release only from a clean worktree after validating against the supported
Hermes compatibility line. The current package line is `0.1.x` beta; tags
should match the version in `pyproject.toml`, `plugin.yaml`, and
`src/hermes_skill_guard/__init__.py`.

## Preflight

1. Update local Hermes and record the checked commit:

   ```bash
   export HERMES_AGENT_CHECKOUT=/path/to/hermes-agent
   git -C "$HERMES_AGENT_CHECKOUT" fetch --tags origin
   git -C "$HERMES_AGENT_CHECKOUT" pull --ff-only
   git -C "$HERMES_AGENT_CHECKOUT" describe --tags --always
   ```

2. Confirm the minimum target remains Hermes Agent `v2026.5.16` or newer.
3. Update `CHANGELOG.md`.
4. Confirm `plugin.yaml` declares the release version and includes every
   registered tool, including `skill_guard_doctor`, `skill_guard_relations`,
   and `skill_guard_auto_promote`.
5. Remove generated local artifacts from the release diff:
   `.venv/`, `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`, `dist/`,
   `.coverage`, `coverage.xml`, and `__pycache__/`.

## Quality Gates

```bash
uv sync --locked --extra dev
uv run --locked --extra dev pytest
uv run --locked --extra dev ruff check src tests
uv run --locked --extra dev ruff format --check src tests
uv run --locked --extra dev mypy src tests
uv run --locked --extra dev pytest --cov=hermes_skill_guard
./scripts/verify-release.sh
```

Run this sentinel scan before tagging. Any hit should be fixed or documented
as intentional:

```bash
rg -n "FIXME|your-org|PersonWorkspace|0\\.2\\.0" README.md docs src tests .github
```

## Build

```bash
uv build --out-dir dist --clear
```

`scripts/verify-release.sh` runs `hermes-skill-guard verify package` against
the built wheel and sdist. The CLI subcommand checks that each archive
contains the runtime data files and the bundled `skill-guard` skill (see
`required` in `cmd_verify_package`).

Inspect the wheel contents and confirm these runtime files are included:

- `plugin.yaml`
- `hermes_skill_guard/data/default-config.yaml`
- `hermes_skill_guard/data/compat.yaml`
- `hermes_skill_guard/data/default_rules.json`
- `hermes_skill_guard/data/rules.schema.json`
- `hermes_skill_guard/_bundled_skills/skill-guard/SKILL.md`
- `hermes_skill_guard/_bundled_skills/skill-guard/references/*.md`

## Publish

Publishing is handled by GitHub Actions and PyPI Trusted Publishing. Create and
push a tag after the quality gates pass:

```bash
git tag -s v0.1.11 -m "Release v0.1.11"
git push origin v0.1.11
```

The release workflow rebuilds artifacts, reruns lint/type/test/security gates,
verifies package contents, publishes to PyPI, and creates the GitHub Release.
