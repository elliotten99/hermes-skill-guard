"""Hermes plugin entrypoint."""

from __future__ import annotations

import logging
from contextlib import ExitStack, suppress
from importlib.resources import as_file
from importlib.resources import files as resource_files
from pathlib import Path

from hermes_skill_guard.config import GuardConfig, load_config
from hermes_skill_guard.context import SkillGuardContext
from hermes_skill_guard.hermes.adapter import HermesAdapter
from hermes_skill_guard.observability import build_exporter
from hermes_skill_guard.registry import default_intents, register_intents
from hermes_skill_guard.runtime import TraceCache
from hermes_skill_guard.schemas import ModuleStatus
from hermes_skill_guard.storage.repository import StateStore

_BUNDLED_SKILL_PACKAGE = "hermes_skill_guard._bundled_skills"
_BUNDLED_SKILL_NAME = "skill-guard"


def _resolve_bundled_skill_path(stack: ExitStack) -> Path | None:
    """Resolve the bundled skill directory across install layouts.

    Tries ``importlib.resources`` first (works for wheel, zip, editable
    installs). Falls back to the legacy repo-relative path so that running
    from a source checkout that has not been re-installed still works.

    Returns ``None`` if no bundled skill can be located, in which case the
    caller should skip skill registration.
    """
    with suppress(ModuleNotFoundError, FileNotFoundError, AttributeError):
        resource = resource_files(_BUNDLED_SKILL_PACKAGE).joinpath(_BUNDLED_SKILL_NAME)
        if resource.is_dir():
            return Path(stack.enter_context(as_file(resource)))

    legacy = Path(__file__).resolve().parents[2] / "skills" / _BUNDLED_SKILL_NAME
    if legacy.is_dir():
        return legacy

    return None


def _resolve_enabled_intents(
    config: GuardConfig,
    store: StateStore,
    logger: logging.Logger,
) -> set[str] | None:
    """Determine which intents should be registered.

    Probes Hermes for first-party capabilities and excludes any intents
    that are already covered. The intent of the config gating is preserved:

    - When ``config.enabled_intents`` is empty (default), all default
      intents *minus* the retired ones are eligible. The function returns
      ``None`` if no intents are retired, so the registry registers
      everything.
    - When ``config.enabled_intents`` is non-empty, only the listed
      intents are eligible, minus the retired ones.

    Returns:
        ``None`` to indicate "register every default intent" (no gating),
        or an explicit ``set[str]`` of intent IDs to register. An empty
        set means "register nothing".
    """
    from hermes_skill_guard.intents.compatibility import CapabilityProbe

    probe = CapabilityProbe()
    coverage = probe.check_all()
    retired_intents: set[str] = set()

    for intent_id, result in coverage.items():
        if result.covered:
            status = ModuleStatus.RETIRED_BY_OFFICIAL.value
            retired_intents.add(intent_id)
        else:
            status = (
                ModuleStatus.CANDIDATE_FOR_RETIREMENT.value
                if result.since_version
                else ModuleStatus.ENABLED.value
            )
        store.record_probe_result(
            intent_id=intent_id,
            status=status,
            confidence=result.confidence.value,
            since_version=result.since_version,
            reason=result.reason,
        )
        logger.info(
            "capability probe: %s -> %s (confidence=%s, reason=%s)",
            intent_id,
            status,
            result.confidence.value,
            result.reason,
        )

    for intent_id in retired_intents:
        logger.info("skipping retired intent: %s (covered by Hermes)", intent_id)

    if config.enabled_intents:
        # Explicit allow-list: subtract retired intents. Empty result means
        # nothing is registered, which is the user's expressed intent.
        return config.enabled_intents - retired_intents

    # Default (empty allow-list) means "all defaults". If nothing is retired,
    # signal "no gating" by returning None so register_intents enumerates
    # every default. Otherwise return the surviving subset explicitly.
    if not retired_intents:
        # None here uses register_intents contract: None = register all defaults.
        return None
    all_intent_ids = {getattr(i, "intent_id", "?") for i in default_intents()}
    return all_intent_ids - retired_intents


def register(ctx: object) -> None:
    """Register Hermes tools, hooks, CLI commands, slash commands, and bundled skill."""
    config = load_config()
    logger = logging.getLogger("hermes_skill_guard")
    store = StateStore(config.state_db, config.events)
    trace_cache = TraceCache(config.trace_cache)
    exporter = build_exporter(config)
    context = SkillGuardContext(
        config=config,
        store=store,
        trace_cache=trace_cache,
        logger=logger,
        exporter=exporter,
    )
    adapter = HermesAdapter(ctx)

    enabled = _resolve_enabled_intents(config, store, logger)
    register_intents(adapter, context, enabled_intents=enabled)

    with ExitStack() as stack:
        skill_path = _resolve_bundled_skill_path(stack)
        if skill_path is None:
            logger.warning("bundled skill-guard skill not found; skipping registration")
            return
        adapter.register_skill(_BUNDLED_SKILL_NAME, skill_path)
