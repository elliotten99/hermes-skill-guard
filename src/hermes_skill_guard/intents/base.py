"""Intent protocol and registration helpers."""

from __future__ import annotations

from typing import Protocol

from hermes_skill_guard.context import SkillGuardContext
from hermes_skill_guard.hermes.adapter import HermesAdapter


class IntentHandler(Protocol):
    intent_id: str
    priority: int

    def register(self, adapter: HermesAdapter, context: SkillGuardContext) -> None:
        """Register tools, hooks, commands, or services."""
