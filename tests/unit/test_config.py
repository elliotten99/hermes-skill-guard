from __future__ import annotations

import os
from pathlib import Path

import pytest

from hermes_skill_guard.config import (
    EnforcementConfig,
    EventsConfig,
    GuardConfig,
    LoggingConfig,
    TraceCacheConfig,
    _load_user_config_file,
    _merge_config,
    load_config,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("SKILL_GUARD_"):
            monkeypatch.delenv(key, raising=False)


def test_default_config() -> None:
    config = load_config()
    assert config.dry_run is True
    assert config.enforcement.mode == "audit"
    assert config.logging.redaction_mode == "strict"
    assert config.events.ttl_days == 7


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKILL_GUARD_DRY_RUN", "false")
    monkeypatch.setenv("SKILL_GUARD_ENFORCEMENT_MODE", "block")
    monkeypatch.setenv("SKILL_GUARD_PREFLIGHT_TIMEOUT_MS", "300")
    config = load_config()
    assert config.dry_run is False
    assert config.enforcement.mode == "block"
    assert config.enforcement.timeout_ms == 300


def test_user_config_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
dry_run: false
enforcement:
  mode: candidate
  timeout_ms: 200
logging:
  redaction_mode: permissive
events:
  ttl_days: 14
trace_cache:
  ttl_minutes: 20
""",
        encoding="utf-8",
    )
    config = load_config(user_config_path=config_path)
    assert config.dry_run is False
    assert config.enforcement.mode == "candidate"
    assert config.enforcement.timeout_ms == 200
    assert config.logging.redaction_mode == "permissive"
    assert config.events.ttl_days == 14
    assert config.trace_cache.ttl_minutes == 20


def test_env_overrides_user_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("dry_run: false\nenforcement:\n  mode: candidate\n", encoding="utf-8")
    monkeypatch.setenv("SKILL_GUARD_DRY_RUN", "true")
    config = load_config(user_config_path=config_path)
    assert config.dry_run is True
    assert config.enforcement.mode == "candidate"


def test_load_user_config_missing_file() -> None:
    assert _load_user_config_file(Path("/nonexistent/config.yaml")) == {}


def test_load_user_config_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("[not a dict", encoding="utf-8")
    assert _load_user_config_file(path) == {}


def test_merge_config_preserves_unset_fields() -> None:
    base = GuardConfig(
        dry_run=True,
        enforcement=EnforcementConfig(mode="audit"),
        logging=LoggingConfig(redaction_mode="strict"),
        events=EventsConfig(ttl_days=7),
        trace_cache=TraceCacheConfig(ttl_minutes=10),
    )
    merged = _merge_config(base, {"dry_run": False})
    assert merged.dry_run is False
    assert merged.enforcement.mode == "audit"
    assert merged.logging.redaction_mode == "strict"
