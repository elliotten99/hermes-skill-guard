"""Capability probe for Hermes first-party feature detection.

This module provides a read-only local check against a bundled compatibility
matrix. It does NOT make network calls or invoke the Hermes API.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from hermes_skill_guard.schemas import Confidence, ModuleStatus


def _parse_version(version_str: str) -> tuple[int, ...]:
    """Parse a version string into a tuple of integers for comparison.

    Non-numeric suffixes (e.g. '1.0a1') are truncated at the first
    non-digit character.
    """
    parts: list[int] = []
    for part in version_str.split("."):
        numeric = ""
        for ch in part:
            if ch.isdigit():
                numeric += ch
            else:
                break
        parts.append(int(numeric) if numeric else 0)
    return tuple(parts)


@dataclass(frozen=True, slots=True)
class CoverageResult:
    """Result of probing a single capability."""

    covered: bool
    confidence: Confidence
    since_version: str | None
    reason: str


class CapabilityProbe:
    """Read-only probe of the local Hermes capability matrix.

    Args:
        compat_path: Optional path to a compatibility YAML file. When
            omitted, the bundled ``data/compat.yaml`` is used.
    """

    def __init__(self, compat_path: Path | None = None) -> None:
        self._compat_path = compat_path
        self._matrix: dict[str, Any] | None = None

    def load_compat_matrix(self) -> dict[str, Any]:
        """Load and cache the compatibility matrix from YAML.

        Returns:
            A dict with at least a ``known_capabilities`` key mapping
            capability IDs to their metadata.
        """
        if self._matrix is not None:
            return self._matrix

        if self._compat_path is not None:
            path = self._compat_path
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        else:
            import importlib.resources as _res

            ref = _res.files("hermes_skill_guard.data") / "compat.yaml"
            data = yaml.safe_load(ref.read_text(encoding="utf-8"))

        if not isinstance(data, dict):
            data = {}
        self._matrix = data
        return data

    def probe_hermes_version(self) -> str:
        """Return the detected Hermes version string.

        Checks the ``HERMES_VERSION`` environment variable first. If unset
        or empty, returns ``"unknown"``.
        """
        version = os.environ.get("HERMES_VERSION", "").strip()
        return version if version else "unknown"

    def check_coverage(self, intent_id: str) -> CoverageResult:
        """Check whether *intent_id* is covered by a Hermes capability.

        A capability "covers" an intent when:

        1. The capability's ``supersedes_intent`` matches *intent_id*.
        2. The detected Hermes version is >= the capability's
           ``hermes_min_version``.

        If the Hermes version is ``"unknown"``, coverage is reported as
        ``False`` with ``LOW`` confidence.

        Args:
            intent_id: The intent identifier to check (e.g. ``"preflight"``).

        Returns:
            A :class:`CoverageResult` describing the coverage status.
        """
        matrix = self.load_compat_matrix()
        known = matrix.get("hermes", {}).get("known_capabilities", {})
        hermes_version = self.probe_hermes_version()

        if hermes_version == "unknown":
            return CoverageResult(
                covered=False,
                confidence=Confidence.LOW,
                since_version=None,
                reason="hermes version unknown",
            )

        hermes_parsed = _parse_version(hermes_version)
        matches: list[tuple[tuple[int, ...], str]] = []

        for _capability_id, meta in known.items():
            if not isinstance(meta, dict):
                continue
            if meta.get("supersedes_intent") != intent_id:
                continue
            min_version = meta.get("hermes_min_version")
            if not isinstance(min_version, str):
                continue
            matches.append((_parse_version(min_version), min_version))

        if not matches:
            return CoverageResult(
                covered=False,
                confidence=Confidence.MEDIUM,
                since_version=None,
                reason="no known capability covers this intent",
            )

        # Use the lowest version requirement (most permissive match)
        min_parsed, min_version = min(matches, key=lambda x: x[0])
        if hermes_parsed >= min_parsed:
            return CoverageResult(
                covered=True,
                confidence=Confidence.HIGH,
                since_version=min_version,
                reason=f"hermes {hermes_version} >= {min_version}",
            )
        return CoverageResult(
            covered=False,
            confidence=Confidence.HIGH,
            since_version=min_version,
            reason=f"hermes {hermes_version} < {min_version}",
        )

    def check_all(self) -> dict[str, CoverageResult]:
        """Check coverage for every intent mentioned in the matrix.

        Returns:
            Mapping from ``supersedes_intent`` value to its
            :class:`CoverageResult`.
        """
        matrix = self.load_compat_matrix()
        known = matrix.get("hermes", {}).get("known_capabilities", {})
        results: dict[str, CoverageResult] = {}
        seen: set[str] = set()

        for _capability_id, meta in known.items():
            if not isinstance(meta, dict):
                continue
            intent_id = meta.get("supersedes_intent")
            if not isinstance(intent_id, str) or intent_id in seen:
                continue
            seen.add(intent_id)
            results[intent_id] = self.check_coverage(intent_id)

        return results


COMPAT_SCHEMA = {
    "name": "skill_guard_compat",
    "description": "Probe, list, or restore Hermes capability compatibility.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["probe", "list", "restore"],
                "description": "Action to perform.",
            },
            "intent_id": {
                "type": "string",
                "description": "Intent ID (required for restore).",
            },
        },
        "required": ["action"],
    },
}


class CompatibilityIntent:
    intent_id = "compatibility"
    priority = 25

    def register(self, adapter: Any, context: Any) -> None:
        from hermes_skill_guard.context import SkillGuardContext
        from hermes_skill_guard.hermes.adapter import HermesAdapter

        ctx: SkillGuardContext = context
        _adapter: HermesAdapter = adapter

        def handler(args: dict[str, Any], **_: object) -> str:
            try:
                action = str(args.get("action", "list"))
                if action == "probe":
                    probe = CapabilityProbe()
                    results = probe.check_all()
                    for intent_id, result in results.items():
                        status = (
                            ModuleStatus.RETIRED_BY_OFFICIAL.value
                            if result.covered
                            else ModuleStatus.CANDIDATE_FOR_RETIREMENT.value
                        )
                        ctx.store.record_probe_result(
                            intent_id=intent_id,
                            status=status,
                            confidence=result.confidence.value,
                            since_version=result.since_version,
                            reason=result.reason,
                        )
                    return json.dumps(
                        {
                            "ok": True,
                            "probed": len(results),
                            "results": {
                                k: {
                                    "covered": v.covered,
                                    "confidence": v.confidence.value,
                                    "since_version": v.since_version,
                                    "reason": v.reason,
                                }
                                for k, v in results.items()
                            },
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                if action == "list":
                    modules = ctx.store.list_module_statuses()
                    return json.dumps(
                        {"ok": True, "modules": modules},
                        ensure_ascii=False,
                        default=str,
                    )
                if action == "restore":
                    raw_intent_id = args.get("intent_id")
                    if not isinstance(raw_intent_id, str):
                        return json.dumps(
                            {"ok": False, "error": "intent_id is required for restore"}
                        )
                    intent_id = raw_intent_id
                    modules = ctx.store.list_module_statuses()
                    current = next((m for m in modules if m.get("intent_id") == intent_id), None)
                    if current is None:
                        return json.dumps({"ok": False, "error": f"module not found: {intent_id}"})
                    current_status = str(current.get("status", ""))
                    if current_status not in {
                        ModuleStatus.CANDIDATE_FOR_RETIREMENT.value,
                        ModuleStatus.RETIRED_BY_OFFICIAL.value,
                    }:
                        return json.dumps(
                            {
                                "ok": False,
                                "error": (
                                    f"cannot restore module {intent_id} "
                                    f"from status {current_status}"
                                ),
                            }
                        )
                    ctx.store.update_module_status(
                        intent_id, ModuleStatus.ENABLED.value, "manual restore"
                    )
                    return json.dumps(
                        {"ok": True, "intent_id": intent_id, "status": ModuleStatus.ENABLED.value}
                    )
                return json.dumps({"ok": False, "error": f"unsupported action: {action}"})
            except Exception as exc:
                return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

        _adapter.register_tool(
            "skill_guard_compat",
            handler,
            "Probe, list, or restore Hermes capability compatibility.",
            schema=COMPAT_SCHEMA,
        )
