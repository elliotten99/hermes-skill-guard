"""Report and doctor tools."""

from __future__ import annotations

import json
import os
from typing import Any

from hermes_skill_guard.__main__ import configure_parser
from hermes_skill_guard.context import SkillGuardContext
from hermes_skill_guard.hermes.adapter import HermesAdapter
from hermes_skill_guard.schemas import ModuleStatus

REPORT_SCHEMA = {
    "name": "skill_guard_report",
    "description": "Return hermes-skill-guard report.",
    "parameters": {
        "type": "object",
        "properties": {
            "json": {
                "type": "boolean",
                "description": "Return JSON output (default true).",
            },
        },
    },
}

DOCTOR_SCHEMA = {
    "name": "skill_guard_doctor",
    "description": "Run deep diagnostics on hermes-skill-guard state.",
    "parameters": {
        "type": "object",
        "properties": {
            "check": {
                "type": "string",
                "enum": ["all", "storage", "config", "candidates", "counters", "compat"],
                "description": "Which diagnostic check to run.",
            },
        },
    },
}


class ReportingIntent:
    intent_id = "reporting"
    priority = 40

    def register(self, adapter: HermesAdapter, context: SkillGuardContext) -> None:
        def report(args: dict[str, Any], **_: object) -> str:
            try:
                summary = context.store.summary()
                summary["dry_run"] = context.config.dry_run
                summary["enforcement_mode"] = context.config.enforcement.mode
                summary["wal_enabled"] = context.store.wal_enabled()
                summary["recent_events"] = context.store.recent_events(limit=5)
                summary["recent_risks"] = context.store.recent_audit_decisions(limit=5)
                summary["candidate_summary"] = context.store.candidate_status_counts()
                summary["modules"] = context.store.list_module_statuses()
                if args.get("json", True):
                    return json.dumps({"ok": True, "summary": summary}, ensure_ascii=False)
                return (
                    "hermes-skill-guard report\n"
                    f"events: {summary['events']}\n"
                    f"audit_log: {summary['audit_log']}\n"
                    f"candidates: {summary['candidates']}\n"
                    f"wal: {summary['sqlite_journal_mode']}\n"
                )
            except Exception as exc:
                return json.dumps({"ok": False, "error": type(exc).__name__})

        def report_slash_handler(raw_args: str) -> str:
            """Handler for in-session slash command /skill-guard report."""
            args = {"json": True}
            if raw_args.strip().lower() in ("--text", "text", "-t"):
                args["json"] = False
            return report(args)

        def _build_compat_diagnostics(ctx: SkillGuardContext) -> dict[str, Any]:
            modules = ctx.store.list_module_statuses()
            warnings: list[str] = []
            for mod in modules:
                if str(mod.get("status", "")) == ModuleStatus.CANDIDATE_FOR_RETIREMENT.value:
                    warnings.append(f"intent {mod.get('intent_id')} is candidate_for_retirement")
            return {
                "modules": modules,
                "warnings": warnings,
                "warning_count": len(warnings),
            }

        def _detect_config_source() -> str:
            env_keys = [
                "SKILL_GUARD_STATE_DIR",
                "SKILL_GUARD_DRY_RUN",
                "SKILL_GUARD_ENFORCEMENT_MODE",
                "SKILL_GUARD_PREFLIGHT_TIMEOUT_MS",
                "SKILL_GUARD_FAIL_OPEN",
                "SKILL_GUARD_CAPTURE_RAW_PAYLOADS",
                "SKILL_GUARD_REDACTION_MODE",
                "SKILL_GUARD_MAX_FIELD_LENGTH",
                "SKILL_GUARD_HASH_REDACTED_VALUES",
                "SKILL_GUARD_TRACE_CACHE_TTL_MINUTES",
                "SKILL_GUARD_TRACE_CACHE_MAX_ENTRIES",
                "SKILL_GUARD_EVENTS_TTL_DAYS",
                "SKILL_GUARD_EVENTS_MAX_ROWS",
                "SKILL_GUARD_EVENTS_MAX_DB_MB",
                "SKILL_GUARD_EVENTS_ROTATE_EVERY",
            ]
            if any(key in os.environ for key in env_keys):
                return "env"
            user_path = os.environ.get("SKILL_GUARD_CONFIG")
            if user_path:
                return "user_config"
            from hermes_skill_guard.config import _default_user_config_path

            if _default_user_config_path().exists():
                return "user_config"
            return "defaults"

        _ALLOWED_PRAGMAS = {"journal_mode", "foreign_keys", "busy_timeout", "synchronous"}

        def _query_pragma(name: str) -> Any:
            if name not in _ALLOWED_PRAGMAS:
                return None
            import sqlite3

            conn = sqlite3.connect(context.store.db_path, timeout=3)
            try:
                row = conn.execute(f"PRAGMA {name}").fetchone()
                return row[0] if row else None
            finally:
                conn.close()

        def doctor(args: dict[str, Any], **_: object) -> str:
            try:
                check = args.get("check", "all")
                _raw_counters = context.store.summary().get("counters")
                summary_counters: dict[str, Any] = dict(
                    _raw_counters if isinstance(_raw_counters, dict) else {}
                )
                sqlite_busy = summary_counters.get("sqlite_busy_count", 0)
                dropped_write = summary_counters.get("dropped_write_count", 0)
                rotation_failed = summary_counters.get("rotation_failed_count", 0)
                health_score = 100 - (sqlite_busy + dropped_write * 5 + rotation_failed * 10)
                diagnostics: dict[str, Any] = {
                    "config_source": _detect_config_source(),
                    "storage": {
                        "db_path": str(context.store.db_path),
                        "size_mb": context.store.db_size_mb(),
                        "wal_mode": "wal" if context.store.wal_enabled() else "off",
                        "foreign_keys": bool(_query_pragma("foreign_keys")),
                        "busy_timeout": _query_pragma("busy_timeout") or 0,
                    },
                    "candidates": {
                        "total": sum(context.store.candidate_status_counts().values()),
                        "by_status": context.store.candidate_status_counts(),
                    },
                    "counters": {
                        "sqlite_busy_count": sqlite_busy,
                        "dropped_write_count": dropped_write,
                    },
                    "dangling": context.store.dangling_candidates(),
                    "recent_risks": context.store.recent_audit_decisions(limit=10),
                    "health_score": max(0, health_score),
                    "compat": _build_compat_diagnostics(context),
                }
                if check != "all":
                    # Filter diagnostics to the requested check area only
                    if check == "storage":
                        diagnostics = {k: v for k, v in diagnostics.items() if k in ("storage",)}
                    elif check == "config":
                        diagnostics = {
                            k: v for k, v in diagnostics.items() if k in ("config_source",)
                        }
                    elif check == "candidates":
                        diagnostics = {
                            k: v for k, v in diagnostics.items() if k in ("candidates", "dangling")
                        }
                    elif check == "counters":
                        diagnostics = {
                            k: v
                            for k, v in diagnostics.items()
                            if k in ("counters", "health_score")
                        }
                    elif check == "compat":
                        diagnostics = {k: v for k, v in diagnostics.items() if k in ("compat",)}
                return json.dumps({"ok": True, "diagnostics": diagnostics}, ensure_ascii=False)
            except Exception as exc:
                return json.dumps({"ok": False, "error": type(exc).__name__})

        def doctor_slash_handler(raw_args: str) -> str:
            """Handler for in-session slash command /skill-guard doctor."""
            args = {"check": "all"}
            stripped = raw_args.strip().lower()
            if stripped in ("storage", "config", "candidates", "counters", "compat"):
                args["check"] = stripped
            return doctor(args)

        adapter.register_tool(
            "skill_guard_report",
            report,
            "Return hermes-skill-guard report.",
            schema=REPORT_SCHEMA,
        )
        adapter.register_tool(
            "skill_guard_doctor",
            doctor,
            "Run deep diagnostics on hermes-skill-guard state.",
            schema=DOCTOR_SCHEMA,
        )
        adapter.register_slash_command(
            "skill-guard report",
            report_slash_handler,
            "Show hermes-skill-guard report.",
        )
        adapter.register_slash_command(
            "skill-guard doctor",
            doctor_slash_handler,
            "Run hermes-skill-guard doctor.",
        )
        adapter.register_cli_command(
            "skill-guard",
            "Inspect and maintain hermes-skill-guard state",
            configure_parser,
            description=(
                "Operator CLI for hermes-skill-guard reports, candidate review, "
                "storage rotation, and rule fixture checks."
            ),
        )
