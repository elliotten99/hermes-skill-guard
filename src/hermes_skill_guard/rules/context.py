"""Rule evaluation context (T10.3).

A :class:`RuleContext` is a flat, immutable snapshot of the fields a rule
condition can reference. It is built once per tool call (via
:meth:`RuleContext.from_tool_call`) and then handed to
:class:`hermes_skill_guard.rules.engine.RuleEngine` for evaluation.

Keeping the context flat (no nested dicts) lets the engine reference fields
with ``getattr``/``str.format_map`` directly, without any path-walking logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hermes_skill_guard.config import GuardConfig


@dataclass(frozen=True, slots=True)
class RuleContext:
    """Immutable snapshot of fields a rule can reference.

    Attributes:
        skill_name: Extracted skill name (``None`` when missing).
        tool_name: Name of the tool being intercepted (e.g.
            ``"hermes__skill_manage"``).
        content: Combined content/body, stripped of surrounding whitespace.
        content_length: ``len(content)`` — precomputed so leaf operators
            never need to recompute on every comparison.
        description: Extracted description (empty string when missing).
        target_path: Optional explicit path argument (``None`` when missing).
        dry_run: Snapshot of ``config.dry_run`` at the time of the call.
        enforcement_mode: Snapshot of ``config.enforcement.mode``.
    """

    skill_name: str | None
    tool_name: str
    content: str
    content_length: int
    description: str
    target_path: str | None
    dry_run: bool
    enforcement_mode: str

    @classmethod
    def from_tool_call(
        cls,
        tool_name: str,
        args: dict[str, Any],
        config: GuardConfig,
    ) -> RuleContext:
        """Build a context from the canonical tool-call signature."""
        # Imported lazily so this module stays importable in environments
        # where intents are not wired up yet (e.g. unit tests).
        from hermes_skill_guard.intents._extractors import (
            extract_content,
            extract_description,
            extract_skill_name,
            extract_target_path,
        )

        raw_content = extract_content(args) or ""
        content = raw_content.strip()
        raw_description = extract_description(args) or ""
        description = raw_description.strip()
        return cls(
            skill_name=extract_skill_name(args),
            tool_name=tool_name,
            content=content,
            content_length=len(content),
            description=description,
            target_path=extract_target_path(args),
            dry_run=config.dry_run,
            enforcement_mode=config.enforcement.mode,
        )


__all__ = ["RuleContext"]
