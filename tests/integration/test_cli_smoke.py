"""One subprocess smoke test to verify the CLI works as an installed entrypoint.

All other CLI tests use direct main() calls (test_cli_direct.py).
This single test catches packaging/import issues that direct calls miss.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.slow
class TestCliSmoke:
    """Subprocess smoke tests — run sparingly."""

    def test_cli_entrypoint_imports_and_runs(self, tmp_path: Path) -> None:
        """Verify `python -m hermes_skill_guard doctor` works end-to-end."""
        state_dir = tmp_path / "state"
        env = {**os.environ, "SKILL_GUARD_STATE_DIR": str(state_dir)}

        result = subprocess.run(
            [sys.executable, "-m", "hermes_skill_guard", "doctor"],
            capture_output=True,
            text=True,
            env=env,
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["ok"] is True
        assert output["doctor"]["storage"]["wal_enabled"] is True

    def test_cli_candidates_promote_nonexistent(self, tmp_path: Path) -> None:
        """Verify subprocess path returns JSON error for missing candidate."""
        state_dir = tmp_path / "state"
        env = {**os.environ, "SKILL_GUARD_STATE_DIR": str(state_dir)}

        result = subprocess.run(
            [sys.executable, "-m", "hermes_skill_guard", "candidates", "promote", "bad-id"],
            capture_output=True,
            text=True,
            env=env,
        )

        assert result.returncode == 1
        output = json.loads(result.stdout)
        assert output["ok"] is False
        assert "not found" in output["error"]
