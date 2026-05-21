"""Intent registry."""

from __future__ import annotations

from hermes_skill_guard.context import SkillGuardContext
from hermes_skill_guard.hermes.adapter import HermesAdapter
from hermes_skill_guard.intents.auto_promoter import AutoPromoteIntent
from hermes_skill_guard.intents.base import IntentHandler
from hermes_skill_guard.intents.candidates import CandidatesIntent
from hermes_skill_guard.intents.capture import CaptureIntent
from hermes_skill_guard.intents.compatibility import CompatibilityIntent
from hermes_skill_guard.intents.preflight import PreflightIntent
from hermes_skill_guard.intents.promotion import PromotionIntent
from hermes_skill_guard.intents.relations import RelationsIntent
from hermes_skill_guard.intents.reporting import ReportingIntent


def default_intents() -> list[IntentHandler]:
    """Return v0.1 default intents."""
    return [
        CaptureIntent(),
        PreflightIntent(),
        CompatibilityIntent(),
        CandidatesIntent(),
        PromotionIntent(),
        RelationsIntent(),
        ReportingIntent(),
        AutoPromoteIntent(),
    ]


def register_intents(
    adapter: HermesAdapter, context: SkillGuardContext, *, enabled_intents: set[str] | None = None
) -> None:
    """Register default intents with the adapter.

    Args:
        adapter: The Hermes adapter to register tools on.
        context: Skill guard runtime context.
        enabled_intents: ``None`` registers all default intents. Pass an
            explicit ``set[str]`` to restrict registration to the named
            intent IDs; an empty set therefore disables every intent.
    """
    for intent in sorted(default_intents(), key=lambda item: item.priority):
        intent_id = getattr(intent, "intent_id", "?")
        if enabled_intents is not None and intent_id not in enabled_intents:
            context.logger.warning("intent %s is disabled by configuration", intent_id)
            continue
        try:
            intent.register(adapter, context)
        except Exception as exc:
            context.logger.exception("intent registration failed: %s", intent_id)
            context.store.increment_counter(f"intent_registration_failed:{type(exc).__name__}")
