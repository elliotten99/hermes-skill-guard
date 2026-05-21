#!/usr/bin/env bash
set -euo pipefail

dist_dir="$(mktemp -d)"
trap 'rm -rf "$dist_dir"' EXIT

uv run --locked --extra dev ruff check src tests
uv run --locked --extra dev ruff format --check src tests
uv run --locked --extra dev mypy src tests
uv run --locked --extra dev pytest --cov=hermes_skill_guard
uv build --out-dir "$dist_dir" --clear
HSG_DIST_DIR="$dist_dir" \
  uv run --locked --extra dev pytest tests/integration/test_package_verification.py

if uv run --locked --extra dev python -m hermes_skill_guard verify package \
  --help >/dev/null 2>&1; then
  uv run --locked --extra dev python -m hermes_skill_guard verify package \
    "$dist_dir"/*.whl "$dist_dir"/*.tar.gz
else
  echo "ERROR: 'verify package' CLI subcommand is unavailable." >&2
  echo "This should never happen on v0.1.10+ — investigate before shipping." >&2
  exit 1
fi
