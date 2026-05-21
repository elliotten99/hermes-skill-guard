# Contributing to hermes-skill-guard

Thank you for your interest in improving hermes-skill-guard. This project
welcomes contributions from anyone willing to follow these guidelines and
treat others with respect.

## Development Environment

We use `uv` for dependency and environment management. Python 3.11 through 3.13
are supported.

```bash
# Clone your fork
git clone https://github.com/elliotten99/hermes-skill-guard.git
cd hermes-skill-guard

# Install dependencies and hooks
uv sync --locked --extra dev
uv run --locked --extra dev pre-commit install

# Verify everything works
uv run --locked --extra dev pytest
```

## Project Structure

```
hermes-skill-guard/
├── src/hermes_skill_guard/    # Main source code
│   ├── intents/               # Intent handlers (add new ones here)
│   ├── data/                  # Default configs and compatibility data
│   └── __init__.py            # Plugin entry point
├── tests/                     # Test suite (pytest)
│   ├── golden/                # Golden test cases
│   └── unit/                  # Unit tests
├── docs/                      # Documentation
├── skills/skill-guard/        # Bundled skill (read-only)
├── plugin.yaml                # Plugin manifest
└── pyproject.toml             # Project metadata and tool config
```

## Development Workflow

1. **Fork** the repository on GitHub
2. **Branch** from `main`: `git checkout -b feature/short-description`
3. **Commit** with clear messages following Conventional Commits (see below)
4. **Test** locally with the full quality gate (see below)
5. **Push** to your fork: `git push origin feature/short-description`
6. **Open a Pull Request** against `main`

Pull requests should be focused. If you are fixing a bug and adding a feature,
please open separate PRs.

## Commit Message Convention

We recommend [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

Common types:

| Type | Use for |
|------|---------|
| `feat` | New features or intents |
| `fix` | Bug fixes |
| `docs` | Documentation changes |
| `test` | Adding or fixing tests |
| `refactor` | Code changes that neither fix bugs nor add features |
| `chore` | Maintenance, dependencies, tooling |

Examples:

```
feat(intents): add duplicate skill detection intent

fix(redactor): handle null values in payload fields
docs(configuration): clarify timeout behavior in comments
```

## Code Standards

All code must pass:

```bash
# Linting (ruff)
uv run --locked --extra dev ruff check .

# Formatting (ruff)
uv run --locked --extra dev ruff format --check .

# Type checking (mypy strict)
uv run --locked --extra dev mypy src tests
```

Configuration for these tools lives in `pyproject.toml`. Do not disable
strict mode for new code.

## Developer Certificate of Origin

By opening a pull request, you certify the Developer Certificate of Origin 1.1:

```text
By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I have the
right to submit it under the open source license indicated in the file; or

(b) The contribution is based upon previous work that, to the best of my
knowledge, is covered under an appropriate open source license and I have the
right under that license to submit that work with modifications; or

(c) The contribution was provided directly to me by another person who
certified (a), (b), or (c) and I have not modified it.
```

## Testing Requirements

- All new features must include tests
- Bug fixes must include a regression test
- Coverage threshold: **80%** minimum (`pytest --cov=hermes_skill_guard`)
- Golden tests should be updated if behavior changes

Run the full suite:

```bash
uv run --locked --extra dev pytest --cov=hermes_skill_guard --cov-report=term-missing
```

## Documentation Requirements

Changes that affect user-visible behavior must include documentation updates:

- README.md for high-level changes
- docs/ for detailed behavior, configuration, or architecture changes
- Inline docstrings for public APIs
- CHANGELOG.md entry under the Unreleased section

## Intent Development

Add new behavior as a file under `src/hermes_skill_guard/intents/`.
Do not modify the registry for ordinary extensions.

Every new intent must:

- implement `IntentHandler`
- be safe when disabled
- avoid unhandled exceptions in Hermes callbacks
- include tests and documentation

## Updating the Changelog

Any user-visible change must include a `CHANGELOG.md` entry under the
`[Unreleased]` section, in the appropriate Keep-a-Changelog category:

- **Added** — new features
- **Changed** — changes to existing behavior
- **Deprecated** — soon-to-be-removed features
- **Removed** — removed features
- **Fixed** — bug fixes
- **Security** — vulnerability fixes

When cutting a release, the maintainer moves `[Unreleased]` content into a
new dated `[X.Y.Z] - YYYY-MM-DD` section and resets `[Unreleased]` to the
six-category skeleton.

## Release Process (Maintainers)

1. Update version in `plugin.yaml` and `pyproject.toml`
2. Update `CHANGELOG.md` with release date
3. Create a signed tag: `git tag -s v0.X.Y -m "Release v0.X.Y"`
4. Push tag: `git push origin v0.X.Y`
5. CI will build and publish to PyPI

## Getting Help

- Open a [discussion](https://github.com/elliotten99/hermes-skill-guard/discussions) for questions
- Open an [issue](https://github.com/elliotten99/hermes-skill-guard/issues) for bugs or feature requests
- Review [existing documentation](docs/) before asking
- For security issues, see [SECURITY.md](SECURITY.md)

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
By participating, you agree to uphold this code.
