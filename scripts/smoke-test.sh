#!/usr/bin/env bash
set -euo pipefail

uv run --locked --extra dev python -m hermes_skill_guard doctor
uv run --locked --extra dev python -m hermes_skill_guard report --json
uv run --locked --extra dev pytest
