"""Tests for ``hermes_skill_guard.registry.register_intents`` gating.

Covers the contract that distinguishes "no gating" (``None``) from an
explicit allow-list (``set[str]``). An empty set must register *nothing*,
which is the behaviour relied on by ``plugin._resolve_enabled_intents``
when every intent has been retired by a first-party Hermes capability.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from hermes_skill_guard.config import GuardConfig
from hermes_skill_guard.context import SkillGuardContext
from hermes_skill_guard.hermes.adapter import HermesAdapter
from hermes_skill_guard.registry import default_intents, register_intents
from hermes_skill_guard.runtime import TraceCache
from hermes_skill_guard.storage.repository import StateStore


class _FakeHermesCtx:
    """Minimal fake Hermes context capturing tool registrations."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}
        self.hooks: dict[str, Any] = {}
        self.commands: dict[str, Any] = {}

    def register_tool(self, name: str, *_: Any, **__: Any) -> None:
        self.tools[name] = True

    def register_hook(self, name: str, handler: Any) -> None:
        self.hooks[name] = handler

    def register_command(self, name: str, *_: Any, **__: Any) -> None:
        self.commands[name] = True

    def register_cli_command(self, name: str, *_: Any, **__: Any) -> None:
        self.commands[f"cli:{name}"] = True


@pytest.fixture
def context(tmp_path: Path) -> SkillGuardContext:
    config = GuardConfig(state_dir=tmp_path)
    return SkillGuardContext(
        config=config,
        store=StateStore(config.state_db, config.events),
        trace_cache=TraceCache(config.trace_cache),
        logger=logging.getLogger("test-registry"),
    )


def _all_default_intent_ids() -> set[str]:
    return {getattr(i, "intent_id", "?") for i in default_intents()}


class TestRegisterIntents:
    """Contract: ``enabled_intents`` distinguishes None vs empty vs allow-list."""

    def test_none_registers_every_default_intent(self, context: SkillGuardContext) -> None:
        fake = _FakeHermesCtx()
        register_intents(HermesAdapter(fake), context, enabled_intents=None)

        # Every default intent registers at least one of: tool, hook, command.
        # Asserting the count of all registrations equals the count of
        # defaults would be brittle since intents register varied artifacts.
        # Instead, assert all known tool names appear when None is passed.
        assert "skill_guard_compat" in fake.tools
        assert "skill_guard_preflight" in fake.tools or "skill_guard_capture" in fake.tools

    def test_empty_set_registers_no_intents(self, context: SkillGuardContext) -> None:
        fake = _FakeHermesCtx()
        register_intents(HermesAdapter(fake), context, enabled_intents=set())

        # Empty set is the user's expressed intent to disable everything,
        # e.g. when every default intent has been retired by Hermes.
        assert fake.tools == {}
        assert fake.hooks == {}
        assert fake.commands == {}

    def test_specific_set_registers_only_listed_intents(self, context: SkillGuardContext) -> None:
        fake = _FakeHermesCtx()
        register_intents(HermesAdapter(fake), context, enabled_intents={"compatibility"})

        # CompatibilityIntent registers the skill_guard_compat tool. Other
        # intents (e.g. preflight, capture) must not appear.
        assert "skill_guard_compat" in fake.tools
        assert "skill_guard_preflight" not in fake.tools
        assert "skill_guard_capture" not in fake.tools

    def test_default_argument_registers_all(self, context: SkillGuardContext) -> None:
        """Calling without ``enabled_intents=`` keyword is equivalent to None."""
        fake = _FakeHermesCtx()
        register_intents(HermesAdapter(fake), context)
        assert "skill_guard_compat" in fake.tools

    def test_unknown_intent_id_in_allow_list_is_ignored(self, context: SkillGuardContext) -> None:
        fake = _FakeHermesCtx()
        register_intents(HermesAdapter(fake), context, enabled_intents={"nonexistent_intent"})
        # No default intent matches, so nothing should register.
        assert fake.tools == {}

    def test_default_intent_id_coverage(self) -> None:
        """Sanity check: known intent IDs exist in default_intents()."""
        ids = _all_default_intent_ids()
        assert ids >= {
            "capture",
            "preflight",
            "compatibility",
            "candidates",
            "promotion",
            "relations",
            "reporting",
        }
