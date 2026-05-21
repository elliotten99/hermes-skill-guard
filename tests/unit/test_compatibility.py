from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from hermes_skill_guard.config import GuardConfig
from hermes_skill_guard.context import SkillGuardContext
from hermes_skill_guard.hermes.adapter import HermesAdapter
from hermes_skill_guard.intents.compatibility import CapabilityProbe
from hermes_skill_guard.registry import register_intents
from hermes_skill_guard.runtime import TraceCache
from hermes_skill_guard.storage.repository import StateStore


class _FakeHermesCtx:
    """Minimal fake Hermes context for compatibility tests."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def register_tool(
        self,
        name: str,
        toolset: str,
        schema: dict[str, Any],
        handler: Any,
        description: str = "",
    ) -> None:
        self.tools[name] = {"handler": handler, "schema": schema, "description": description}


def _compat_context(tmp_path: Path) -> SkillGuardContext:
    config = GuardConfig(state_dir=tmp_path)
    return SkillGuardContext(
        config=config,
        store=StateStore(config.state_db, config.events),
        trace_cache=TraceCache(config.trace_cache),
        logger=logging.getLogger("test"),
    )


SetHermesVersion = Callable[[str | None], None]


@pytest.fixture
def hermes_version(monkeypatch: pytest.MonkeyPatch) -> SetHermesVersion:
    """Return a setter that controls the ``HERMES_VERSION`` env var.

    Pass ``None`` to clear the variable; pass a string to set it. The
    monkeypatch fixture automatically restores the prior value when the
    test ends, removing the need for manual ``try/finally`` cleanup.
    """

    def _set(version: str | None) -> None:
        if version is None:
            monkeypatch.delenv("HERMES_VERSION", raising=False)
        else:
            monkeypatch.setenv("HERMES_VERSION", version)

    return _set


def _write_compat(path: Path, capabilities: dict[str, dict[str, str]]) -> Path:
    """Write a minimal compat YAML file and return its path."""
    path.write_text(
        yaml.safe_dump({"hermes": {"known_capabilities": capabilities}}),
        encoding="utf-8",
    )
    return path


class TestCapabilityProbe:
    """Tests for CapabilityProbe read-only capability detection."""

    def test_probe_hermes_version_unknown_when_unset(
        self, hermes_version: SetHermesVersion
    ) -> None:
        hermes_version(None)
        probe = CapabilityProbe()
        assert probe.probe_hermes_version() == "unknown"

    def test_probe_hermes_version_reads_env(self, hermes_version: SetHermesVersion) -> None:
        hermes_version("1.2.3")
        probe = CapabilityProbe()
        assert probe.probe_hermes_version() == "1.2.3"

    def test_check_coverage_unknown_version(self, hermes_version: SetHermesVersion) -> None:
        hermes_version(None)
        probe = CapabilityProbe()
        result = probe.check_coverage("preflight")
        assert result.covered is False
        assert result.confidence.value == "low"
        assert result.since_version is None
        assert "unknown" in result.reason

    def test_check_coverage_version_too_low(self, hermes_version: SetHermesVersion) -> None:
        hermes_version("0.13.0")
        probe = CapabilityProbe()
        result = probe.check_coverage("preflight")
        assert result.covered is False
        assert result.confidence.value == "high"
        assert result.since_version == "0.14.0"
        assert "0.13.0 < 0.14.0" in result.reason

    def test_check_coverage_version_meets_min(self, hermes_version: SetHermesVersion) -> None:
        hermes_version("0.14.0")
        probe = CapabilityProbe()
        result = probe.check_coverage("preflight")
        assert result.covered is True
        assert result.confidence.value == "high"
        assert result.since_version == "0.14.0"
        assert "0.14.0 >= 0.14.0" in result.reason

    def test_check_coverage_version_exceeds_min(self, hermes_version: SetHermesVersion) -> None:
        hermes_version("0.20.0")
        probe = CapabilityProbe()
        result = probe.check_coverage("preflight")
        assert result.covered is True
        assert result.confidence.value == "high"
        assert result.since_version == "0.14.0"

    def test_check_coverage_no_capability(self, hermes_version: SetHermesVersion) -> None:
        hermes_version("1.0.0")
        probe = CapabilityProbe()
        result = probe.check_coverage("nonexistent_intent")
        assert result.covered is False
        assert result.confidence.value == "medium"
        assert result.since_version is None
        assert "no known capability" in result.reason

    def test_check_coverage_picks_lowest_matching_version(
        self, tmp_path: Path, hermes_version: SetHermesVersion
    ) -> None:
        """When multiple capabilities supersede the same intent, the
        probe must consider the lowest ``hermes_min_version`` (the most
        permissive match) rather than stopping at the first iterated
        entry. With Hermes 0.13.5 and two capabilities requiring
        ``0.13.0`` and ``0.14.0``, the intent should be covered because
        0.13.5 already satisfies the 0.13.0 floor.
        """
        compat_path = _write_compat(
            tmp_path / "compat.yaml",
            {
                "cap_old": {
                    "hermes_min_version": "0.13.0",
                    "supersedes_intent": "preflight",
                },
                "cap_new": {
                    "hermes_min_version": "0.14.0",
                    "supersedes_intent": "preflight",
                },
            },
        )
        hermes_version("0.13.5")
        probe = CapabilityProbe(compat_path=compat_path)
        result = probe.check_coverage("preflight")
        assert result.covered is True
        assert result.since_version == "0.13.0"
        assert "0.13.5 >= 0.13.0" in result.reason

    def test_check_coverage_lowest_version_too_high(
        self, tmp_path: Path, hermes_version: SetHermesVersion
    ) -> None:
        """When the lowest required version still exceeds the detected
        Hermes version, ``covered`` is False and the reported
        ``since_version`` is that lowest floor."""
        compat_path = _write_compat(
            tmp_path / "compat.yaml",
            {
                "cap_a": {
                    "hermes_min_version": "0.13.0",
                    "supersedes_intent": "preflight",
                },
                "cap_b": {
                    "hermes_min_version": "0.14.0",
                    "supersedes_intent": "preflight",
                },
            },
        )
        hermes_version("0.12.0")
        probe = CapabilityProbe(compat_path=compat_path)
        result = probe.check_coverage("preflight")
        assert result.covered is False
        assert result.since_version == "0.13.0"
        assert "0.12.0 < 0.13.0" in result.reason

    def test_check_coverage_with_equal_versions_uses_first_inserted(
        self, tmp_path: Path, hermes_version: SetHermesVersion
    ) -> None:
        """When multiple capabilities share the same ``hermes_min_version``
        and supersede the same intent, ``min()`` is stable in Python and
        dict insertion order is preserved, so the first inserted capability
        wins the tie. This test pins that behaviour so future refactors
        cannot silently change tie-breaking semantics.
        """
        compat_path = _write_compat(
            tmp_path / "compat.yaml",
            {
                "cap_first": {
                    "hermes_min_version": "0.14.0",
                    "supersedes_intent": "preflight",
                },
                "cap_second": {
                    "hermes_min_version": "0.14.0",
                    "supersedes_intent": "preflight",
                },
            },
        )
        hermes_version("0.14.0")
        probe = CapabilityProbe(compat_path=compat_path)
        result = probe.check_coverage("preflight")
        assert result.covered is True
        assert result.since_version == "0.14.0"

    def test_check_all_returns_all_intents(
        self, tmp_path: Path, hermes_version: SetHermesVersion
    ) -> None:
        """Self-contained: ``check_all`` derives its intent list from the
        provided compat file rather than the bundled default."""
        compat_path = _write_compat(
            tmp_path / "compat.yaml",
            {
                "skill_preflight": {
                    "hermes_min_version": "0.14.0",
                    "supersedes_intent": "preflight",
                },
                "skill_curation": {
                    "hermes_min_version": "0.15.0",
                    "supersedes_intent": "candidates",
                },
            },
        )
        hermes_version("0.14.0")
        probe = CapabilityProbe(compat_path=compat_path)
        results = probe.check_all()
        assert set(results.keys()) == {"preflight", "candidates"}
        assert results["preflight"].covered is True
        assert results["candidates"].covered is False  # 0.14.0 < 0.15.0


class TestCompatibilityIntent:
    """Tests for CompatibilityIntent tool handler."""

    def _handler(self, tmp_path: Path) -> Any:
        ctx = _compat_context(tmp_path)
        fake = _FakeHermesCtx()
        register_intents(HermesAdapter(fake), ctx)
        return fake.tools["skill_guard_compat"]["handler"]

    def test_probe_action_records_and_returns_results(
        self, tmp_path: Path, hermes_version: SetHermesVersion
    ) -> None:
        hermes_version("0.14.0")
        handler = self._handler(tmp_path)
        raw = handler({"action": "probe"})
        result = json.loads(raw)
        assert result["ok"] is True
        assert result["probed"] == 2
        assert result["results"]["preflight"]["covered"] is True
        assert result["results"]["candidates"]["covered"] is False

    def test_list_action_returns_modules(
        self, tmp_path: Path, hermes_version: SetHermesVersion
    ) -> None:
        hermes_version("0.14.0")
        handler = self._handler(tmp_path)
        # First probe to populate modules
        handler({"action": "probe"})
        raw = handler({"action": "list"})
        result = json.loads(raw)
        assert result["ok"] is True
        assert len(result["modules"]) == 2
        intents = {m["intent_id"] for m in result["modules"]}
        assert intents == {"preflight", "candidates"}

    def test_restore_action_enables_module(
        self, tmp_path: Path, hermes_version: SetHermesVersion
    ) -> None:
        hermes_version("0.14.0")
        handler = self._handler(tmp_path)
        handler({"action": "probe"})
        raw = handler({"action": "restore", "intent_id": "preflight"})
        result = json.loads(raw)
        assert result["ok"] is True
        assert result["intent_id"] == "preflight"
        assert result["status"] == "enabled"
        # Verify the status was updated
        list_raw = handler({"action": "list"})
        list_result = json.loads(list_raw)
        preflight = next((m for m in list_result["modules"] if m["intent_id"] == "preflight"), None)
        assert preflight is not None
        assert preflight["status"] == "enabled"

    def test_restore_missing_intent_id(self, tmp_path: Path) -> None:
        handler = self._handler(tmp_path)
        raw = handler({"action": "restore"})
        result = json.loads(raw)
        assert result["ok"] is False
        assert "intent_id is required" in result["error"]

    def test_restore_nonexistent_module(self, tmp_path: Path) -> None:
        handler = self._handler(tmp_path)
        raw = handler({"action": "restore", "intent_id": "nonexistent"})
        result = json.loads(raw)
        assert result["ok"] is False
        assert "module not found" in result["error"]

    def test_restore_from_non_retirable_status(
        self, tmp_path: Path, hermes_version: SetHermesVersion
    ) -> None:
        hermes_version("0.14.0")
        handler = self._handler(tmp_path)
        handler({"action": "probe"})
        # First restore to enabled
        handler({"action": "restore", "intent_id": "preflight"})
        # Try restoring again from enabled status
        raw = handler({"action": "restore", "intent_id": "preflight"})
        result = json.loads(raw)
        assert result["ok"] is False
        assert "cannot restore" in result["error"]

    def test_invalid_action_returns_error(self, tmp_path: Path) -> None:
        handler = self._handler(tmp_path)
        raw = handler({"action": "invalid"})
        result = json.loads(raw)
        assert result["ok"] is False
        assert "unsupported action" in result["error"]
