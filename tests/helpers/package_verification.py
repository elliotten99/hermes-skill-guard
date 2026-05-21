from __future__ import annotations

import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

REQUIRED_BUNDLED_SKILL_FILES = {
    "hermes_skill_guard/_bundled_skills/skill-guard/SKILL.md",
    "hermes_skill_guard/_bundled_skills/skill-guard/references/workflow.md",
    "hermes_skill_guard/_bundled_skills/skill-guard/references/troubleshooting.md",
}

REQUIRED_RUNTIME_DATA_FILES = {
    "hermes_skill_guard/data/default-config.yaml",
    "hermes_skill_guard/data/compat.yaml",
    "hermes_skill_guard/data/default_rules.json",
    "hermes_skill_guard/data/rules.schema.json",
}

REQUIRED_SDIST_FILES = {
    "pyproject.toml",
    "plugin.yaml",
    "GOVERNANCE.md",
    "MAINTAINERS.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "NOTICE.md",
    "skills/skill-guard/SKILL.md",
    "src/hermes_skill_guard/_bundled_skills/skill-guard/SKILL.md",
    "src/hermes_skill_guard/data/default_rules.json",
    "src/hermes_skill_guard/data/rules.schema.json",
}

FORBIDDEN_ARCHIVE_FRAGMENTS = {
    ".env",
    "state.db",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


@dataclass(frozen=True, slots=True)
class BuiltDistributions:
    wheel: Path
    sdist: Path


def find_built_distributions(dist_dir: Path) -> BuiltDistributions:
    wheels = sorted(dist_dir.glob("hermes_skill_guard-*.whl"))
    sdists = sorted(dist_dir.glob("hermes_skill_guard-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise AssertionError(
            f"expected exactly one wheel and one sdist in {dist_dir}, "
            f"found wheels={wheels}, sdists={sdists}"
        )
    return BuiltDistributions(wheel=wheels[0], sdist=sdists[0])


def assert_wheel_contains_runtime_contract(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        _assert_no_forbidden_archive_entries(names)

        missing_skill_files = REQUIRED_BUNDLED_SKILL_FILES - names
        assert not missing_skill_files, (
            f"wheel is missing bundled skill files: {missing_skill_files}"
        )
        missing_data_files = REQUIRED_RUNTIME_DATA_FILES - names
        assert not missing_data_files, f"wheel is missing runtime data files: {missing_data_files}"

        entry_points_name = _single_name_ending(names, ".dist-info/entry_points.txt")
        entry_points = archive.read(entry_points_name).decode("utf-8")
        assert "hermes-skill-guard = hermes_skill_guard.__main__:main" in entry_points

        metadata_name = _single_name_ending(names, ".dist-info/METADATA")
        metadata = archive.read(metadata_name).decode("utf-8")
        assert "Requires-Python: <3.14,>=3.11" in metadata


def duplicate_zip_entries(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as archive:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for name in archive.namelist():
            if name in seen:
                duplicates.add(name)
            seen.add(name)
    return sorted(duplicates)


def assert_sdist_contains_release_inputs(sdist: Path) -> None:
    with tarfile.open(sdist, "r:gz") as archive:
        names = {_strip_sdist_root(member.name) for member in archive.getmembers()}
        _assert_no_forbidden_archive_entries(names)

        missing = REQUIRED_SDIST_FILES - names
        assert not missing, f"sdist is missing release inputs: {missing}"


def _single_name_ending(names: set[str], suffix: str) -> str:
    matches = sorted(name for name in names if name.endswith(suffix))
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one archive entry ending with {suffix}: {matches}")
    return matches[0]


def _strip_sdist_root(name: str) -> str:
    parts = name.split("/", 1)
    if len(parts) == 1:
        return parts[0]
    return parts[1]


def _assert_no_forbidden_archive_entries(names: set[str]) -> None:
    leaked = sorted(
        name
        for name in names
        if any(fragment in Path(name).parts for fragment in FORBIDDEN_ARCHIVE_FRAGMENTS)
    )
    assert not leaked, f"archive contains forbidden local/test artifacts: {leaked}"
