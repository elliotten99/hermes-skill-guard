from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest
from tests.helpers.package_verification import (
    assert_sdist_contains_release_inputs,
    assert_wheel_contains_runtime_contract,
    duplicate_zip_entries,
    find_built_distributions,
)

ROOT = Path(__file__).resolve().parents[2]


def test_packaging_declares_runtime_skill_artifacts() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    wheel_target = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel_target["packages"] == ["src/hermes_skill_guard"]
    assert "plugin.yaml" in wheel_target["artifacts"]

    sdist_target = pyproject["tool"]["hatch"]["build"]["targets"]["sdist"]
    assert "src/hermes_skill_guard/**" in sdist_target["include"]
    assert "plugin.yaml" in sdist_target["include"]

    scripts = pyproject["project"]["scripts"]
    assert scripts["hermes-skill-guard"] == "hermes_skill_guard.__main__:main"


def test_source_and_bundled_skill_copies_stay_in_sync() -> None:
    source_skill = ROOT / "skills" / "skill-guard"
    bundled_skill = ROOT / "src" / "hermes_skill_guard" / "_bundled_skills" / "skill-guard"

    source_files = {
        path.relative_to(source_skill): path.read_bytes()
        for path in source_skill.rglob("*")
        if path.is_file()
    }
    bundled_files = {
        path.relative_to(bundled_skill): path.read_bytes()
        for path in bundled_skill.rglob("*")
        if path.is_file()
    }

    assert bundled_files == source_files


def test_built_distributions_include_runtime_contract() -> None:
    dist_dir_raw = os.environ.get("HSG_DIST_DIR")
    if dist_dir_raw is None:
        pytest.skip("set HSG_DIST_DIR to verify built wheel and sdist artifacts")

    distributions = find_built_distributions(Path(dist_dir_raw))
    assert_wheel_contains_runtime_contract(distributions.wheel)
    assert_sdist_contains_release_inputs(distributions.sdist)


def test_built_wheel_has_no_duplicate_archive_entries() -> None:
    dist_dir_raw = os.environ.get("HSG_DIST_DIR")
    if dist_dir_raw is None:
        pytest.skip("set HSG_DIST_DIR to verify built wheel artifacts")

    distributions = find_built_distributions(Path(dist_dir_raw))
    assert duplicate_zip_entries(distributions.wheel) == []
