"""Command line interface for hermes-skill-guard."""

from __future__ import annotations

import argparse
import json
import tarfile
import zipfile
from typing import NoReturn

from hermes_skill_guard.config import load_config
from hermes_skill_guard.ids import new_candidate_id, new_event_id, new_promotion_attempt_id
from hermes_skill_guard.intents._extractors import build_skill_manage_create_args
from hermes_skill_guard.schemas import (
    Candidate,
    CandidateStatus,
    Confidence,
    PromotionAttempt,
    RelationType,
    SkillRelation,
)
from hermes_skill_guard.storage.repository import StateStore


def _store() -> StateStore:
    config = load_config()
    return StateStore(config.state_db, config.events)


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def cmd_doctor(args: argparse.Namespace) -> int:
    store = _store()
    check = args.check
    diagnostics: dict[str, object] = {}

    if check in ("all", "storage"):
        diagnostics["storage"] = {
            "wal_enabled": store.wal_enabled(),
            "db_size_mb": store.db_size_mb(),
            "summary": store.summary(),
        }
    if check in ("all", "config"):
        config = load_config()
        diagnostics["config"] = {
            "state_db": str(config.state_db),
            "events": {
                "ttl_days": config.events.ttl_days,
                "max_rows": config.events.max_rows,
                "max_db_mb": config.events.max_db_mb,
                "rotate_every_n_writes": config.events.rotate_every_n_writes,
            },
        }
    if check in ("all", "candidates"):
        diagnostics["candidates"] = {
            "status_counts": store.candidate_status_counts(),
            "dangling": store.dangling_candidates(),
        }
    if check in ("all", "counters"):
        diagnostics["counters"] = store.summary().get("counters", {})
    if check in ("all", "compat"):
        modules = store.list_module_statuses()
        warnings = [
            f"intent {m.get('intent_id')} is candidate_for_retirement"
            for m in modules
            if str(m.get("status", "")) == "candidate_for_retirement"
        ]
        diagnostics["compat"] = {
            "modules": modules,
            "warnings": warnings,
            "warning_count": len(warnings),
        }

    if check == "all":
        diagnostics["recent_audit_decisions"] = store.recent_audit_decisions(limit=10)

    _print_json({"ok": True, "check": check, "doctor": diagnostics})
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    store = _store()
    summary = store.summary()
    recent = store.recent_events(limit=args.limit)
    if args.json:
        _print_json({"ok": True, "summary": summary, "recent_events": recent})
    else:
        print("hermes-skill-guard report")
        print(f"events: {summary['events']}")
        print(f"audit_log: {summary['audit_log']}")
        print(f"candidates: {summary['candidates']}")
        print(f"sqlite_journal_mode: {summary['sqlite_journal_mode']}")
        print(f"recent_events ({len(recent)}):")
        for evt in recent:
            print(f"  - {evt['event_id']} ({evt['event_type']})")
    return 0


def cmd_candidates_list(_: argparse.Namespace) -> int:
    _print_json({"ok": True, "candidates": _store().list_candidates()})
    return 0


def cmd_candidates_transition(args: argparse.Namespace) -> int:
    if args.action == "promote":
        return cmd_candidates_promote(args)
    if args.action == "stage":
        status = CandidateStatus.CANDIDATE
    else:
        status = CandidateStatus.APPROVED if args.action == "approve" else CandidateStatus.REJECTED
    try:
        _store().transition_candidate(
            args.candidate_id, status, new_event_id(), f"cli {args.action}"
        )
    except KeyError:
        _print_json(
            {"ok": False, "error": "candidate not found", "candidate_id": args.candidate_id}
        )
        return 1
    except ValueError as exc:
        _print_json({"ok": False, "error": str(exc), "candidate_id": args.candidate_id})
        return 1
    _print_json({"ok": True, "candidate_id": args.candidate_id, "status": status.value})
    return 0


def cmd_candidates_details(args: argparse.Namespace) -> int:
    store = _store()
    candidate = store.get_candidate(args.candidate_id)
    if candidate is None:
        _print_json(
            {"ok": False, "error": "candidate not found", "candidate_id": args.candidate_id}
        )
        return 1
    _print_json(
        {
            "ok": True,
            "candidate": candidate,
            "promotion_attempts": store.list_promotion_attempts(args.candidate_id),
        }
    )
    return 0


def cmd_candidates_promote(args: argparse.Namespace) -> int:
    store = _store()
    candidate = store.get_candidate(args.candidate_id)
    if candidate is None:
        _print_json(
            {"ok": False, "error": "candidate not found", "candidate_id": args.candidate_id}
        )
        return 1
    content = candidate.get("content")
    target_path = candidate.get("target_path")
    skill_manage_args = build_skill_manage_create_args(
        name=str(candidate["name"]),
        description=str(candidate["description"]),
        content=str(content) if isinstance(content, str) else None,
        target_path=str(target_path) if isinstance(target_path, str) else None,
    )
    attempt_id = new_promotion_attempt_id()
    skill_manage_args["skill_guard_promotion_attempt_id"] = attempt_id
    attempt = PromotionAttempt(
        attempt_id=attempt_id,
        candidate_id=args.candidate_id,
        trace_id=str(candidate.get("trace_id") or args.candidate_id),
        tool_call_id=None,
        skill_name=str(candidate["name"]),
        skill_manage_args=skill_manage_args,
    )
    try:
        store.create_promotion_attempt(attempt)
    except KeyError:
        _print_json(
            {"ok": False, "error": "candidate not found", "candidate_id": args.candidate_id}
        )
        return 1
    except ValueError as exc:
        _print_json({"ok": False, "error": str(exc), "candidate_id": args.candidate_id})
        return 1
    _print_json(
        {
            "ok": True,
            "candidate_id": args.candidate_id,
            "attempt_id": attempt_id,
            "status": "pending_promotion",
            "tool_name": "skill_manage",
            "tool_args": skill_manage_args,
        }
    )
    return 0


def cmd_candidates_archive(args: argparse.Namespace) -> int:
    try:
        _store().transition_candidate(
            args.candidate_id, CandidateStatus.ARCHIVED, new_event_id(), "cli archive"
        )
    except KeyError:
        _print_json(
            {"ok": False, "error": "candidate not found", "candidate_id": args.candidate_id}
        )
        return 1
    except ValueError as exc:
        _print_json({"ok": False, "error": str(exc), "candidate_id": args.candidate_id})
        return 1
    _print_json({"ok": True, "candidate_id": args.candidate_id, "status": "archived"})
    return 0


def cmd_candidates_create(args: argparse.Namespace) -> int:
    store = _store()
    events = store.list_events()
    event = next((e for e in events if e.get("event_id") == args.event_id), None)
    if event is None:
        _print_json({"ok": False, "error": "event not found", "event_id": args.event_id})
        return 1
    trace_id = str(event.get("trace_id") or args.event_id)
    candidate = Candidate(
        candidate_id=new_candidate_id(),
        source_event_id=args.event_id,
        trace_id=trace_id,
        name=args.name,
        description=args.description,
        content_hash=args.content_hash,
        status=CandidateStatus.DETECTED,
        reasons=args.reasons,
        content=args.content,
        target_path=args.target_path,
    )
    store.create_candidate(candidate)
    _print_json({"ok": True, "candidate_id": candidate.candidate_id, "status": "detected"})
    return 0


def cmd_candidates_status(_: argparse.Namespace) -> int:
    _print_json({"ok": True, "status_counts": _store().candidate_status_counts()})
    return 0


def cmd_candidates_auto_promote(_: argparse.Namespace) -> int:
    from hermes_skill_guard.config import load_config
    from hermes_skill_guard.intents.auto_promoter import AutoPromoter

    config = load_config()
    store = _store()
    promoter = AutoPromoter(config, store)
    results = promoter.scan()
    _print_json(
        {
            "ok": True,
            "enabled": config.auto_promote.enabled,
            "dry_run": config.auto_promote.dry_run,
            "scanned": len(results),
            "promoted": sum(1 for r in results if r.promoted),
            "results": [
                {
                    "candidate_id": r.candidate_id,
                    "name": r.candidate_name,
                    "promoted": r.promoted,
                    "reason": r.reason,
                    "dry_run": r.dry_run,
                }
                for r in results
            ],
        }
    )
    return 0


def cmd_storage_rotate(_: argparse.Namespace) -> int:
    store = _store()
    store.rotate_events()
    _print_json({"ok": True, "summary": store.summary()})
    return 0


def cmd_relations_add(args: argparse.Namespace) -> int:
    store = _store()
    try:
        relation = SkillRelation(
            relation_id=f"rel_{new_candidate_id()}",
            source_candidate_id=args.source_candidate_id,
            target_candidate_id=args.target_candidate_id,
            relation_type=RelationType(args.relation_type),
            confidence=Confidence(args.confidence),
            reasons=args.reasons,
            created_at=__import__(
                "hermes_skill_guard.storage.repository"
            ).storage.repository.utc_now(),
        )
        store.add_relation(relation)
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 1
    _print_json({"ok": True, "relation_id": relation.relation_id})
    return 0


def cmd_relations_list(args: argparse.Namespace) -> int:
    relation_type = RelationType(args.relation_type) if args.relation_type else None
    _print_json(
        {
            "ok": True,
            "relations": _store().list_relations(
                source_candidate_id=args.source_candidate_id,
                target_candidate_id=args.target_candidate_id,
                relation_type=relation_type,
            ),
        }
    )
    return 0


def cmd_relations_remove(args: argparse.Namespace) -> int:
    removed = _store().remove_relation(args.relation_id)
    if not removed:
        _print_json({"ok": False, "error": "relation not found"})
        return 1
    _print_json({"ok": True, "relation_id": args.relation_id})
    return 0


def _archive_names(path: str) -> set[str]:
    if path.endswith(".whl"):
        with zipfile.ZipFile(path) as zf:
            return set(zf.namelist())
    if path.endswith((".tar.gz", ".tgz")):
        with tarfile.open(path) as tf:
            return {name.split("/", 1)[1] if "/" in name else name for name in tf.getnames()}
    return set()


_WHEEL_REQUIRED_FILES = frozenset(
    {
        "hermes_skill_guard/data/default-config.yaml",
        "hermes_skill_guard/data/compat.yaml",
        "hermes_skill_guard/data/default_rules.json",
        "hermes_skill_guard/data/rules.schema.json",
        "hermes_skill_guard/_bundled_skills/skill-guard/SKILL.md",
        "hermes_skill_guard/_bundled_skills/skill-guard/references/workflow.md",
        "hermes_skill_guard/_bundled_skills/skill-guard/references/troubleshooting.md",
    }
)

_SDIST_REQUIRED_FILES = frozenset(
    {
        "src/hermes_skill_guard/data/default-config.yaml",
        "src/hermes_skill_guard/data/compat.yaml",
        "src/hermes_skill_guard/data/default_rules.json",
        "src/hermes_skill_guard/data/rules.schema.json",
        "src/hermes_skill_guard/_bundled_skills/skill-guard/SKILL.md",
        "src/hermes_skill_guard/_bundled_skills/skill-guard/references/workflow.md",
        "src/hermes_skill_guard/_bundled_skills/skill-guard/references/troubleshooting.md",
    }
)


def cmd_verify_package(args: argparse.Namespace) -> int:
    paths = list(args.artifacts)
    if not paths:
        import glob

        paths = glob.glob("dist/*.whl") + glob.glob("dist/*.tar.gz")
    reports = []
    ok = True
    for path in paths:
        names = _archive_names(path)
        required = _WHEEL_REQUIRED_FILES if path.endswith(".whl") else _SDIST_REQUIRED_FILES
        missing = sorted(required - names)
        reports.append({"path": path, "missing": missing})
        ok = ok and not missing
    _print_json({"ok": ok, "artifacts": reports})
    return 0 if ok else 1


def cmd_rules_list(_: argparse.Namespace) -> int:
    from hermes_skill_guard.config import load_config
    from hermes_skill_guard.rules import RuleLoader

    config = load_config()
    loader = RuleLoader(config)
    rules = loader.load()
    output = [
        {
            "id": r.id,
            "enabled": r.enabled,
            "severity": r.severity,
            "priority": r.priority,
            "description": r.description,
            "message_template": r.message_template,
        }
        for r in rules
    ]
    _print_json({"ok": True, "rules": output, "count": len(output)})
    return 0


def cmd_rules_validate(args: argparse.Namespace) -> int:
    from pathlib import Path

    from hermes_skill_guard.config import GuardConfig, load_config
    from hermes_skill_guard.rules import RuleLoader, RuleLoadError

    config = load_config()
    # If a path is given, temporarily override the config rules_path.
    if args.path:
        config = GuardConfig(
            dry_run=config.dry_run,
            state_dir=config.state_dir,
            enabled_intents=config.enabled_intents,
            enforcement=config.enforcement,
            logging=config.logging,
            events=config.events,
            trace_cache=config.trace_cache,
            observability=config.observability,
            rules_path=Path(args.path),
        )

    loader = RuleLoader(config)
    try:
        rules = loader.load()
        _print_json({"ok": True, "valid": True, "count": len(rules)})
        return 0
    except RuleLoadError as exc:
        _print_json({"ok": False, "valid": False, "error": str(exc)})
        return 1


def cmd_rules_test(_: argparse.Namespace) -> int:
    print("Run `pytest tests/golden` from the project root.")
    return 0


def cmd_compat_probe(_: argparse.Namespace) -> int:
    from hermes_skill_guard.intents.compatibility import CapabilityProbe

    store = _store()
    probe = CapabilityProbe()
    results = probe.check_all()
    for intent_id, result in results.items():
        status = "retired_by_official" if result.covered else "candidate_for_retirement"
        store.record_probe_result(
            intent_id=intent_id,
            status=status,
            confidence=result.confidence.value,
            since_version=result.since_version,
            reason=result.reason,
        )
    _print_json(
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
        }
    )
    return 0


def cmd_compat_list(_: argparse.Namespace) -> int:
    _print_json({"ok": True, "modules": _store().list_module_statuses()})
    return 0


def cmd_compat_restore(args: argparse.Namespace) -> int:
    from hermes_skill_guard.schemas import ModuleStatus

    store = _store()
    intent_id = args.intent_id
    modules = store.list_module_statuses()
    current = next((m for m in modules if m.get("intent_id") == intent_id), None)
    if current is None:
        _print_json({"ok": False, "error": f"module not found: {intent_id}"})
        return 1
    current_status = str(current.get("status", ""))
    if current_status not in {
        ModuleStatus.CANDIDATE_FOR_RETIREMENT.value,
        ModuleStatus.RETIRED_BY_OFFICIAL.value,
    }:
        _print_json(
            {
                "ok": False,
                "error": f"cannot restore module {intent_id} from status {current_status}",
            }
        )
        return 1
    store.update_module_status(intent_id, ModuleStatus.ENABLED.value, "manual restore")
    _print_json({"ok": True, "intent_id": intent_id, "status": ModuleStatus.ENABLED.value})
    return 0


def configure_parser(parser: argparse.ArgumentParser) -> None:
    """Attach hermes-skill-guard subcommands to an argparse parser."""
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor")
    doctor.add_argument(
        "--check",
        choices=["all", "storage", "config", "candidates", "counters", "compat"],
        default="all",
    )
    doctor.set_defaults(func=cmd_doctor)

    report = subparsers.add_parser("report")
    report.add_argument("--json", action="store_true")
    report.add_argument("--limit", type=int, default=5, help="Number of recent items to show")
    report.set_defaults(func=cmd_report)

    candidates = subparsers.add_parser("candidates")
    candidate_sub = candidates.add_subparsers(dest="candidate_command", required=True)
    candidate_list = candidate_sub.add_parser("list")
    candidate_list.set_defaults(func=cmd_candidates_list)
    details = candidate_sub.add_parser("details")
    details.add_argument("candidate_id")
    details.set_defaults(func=cmd_candidates_details)

    for action in ("stage", "approve", "reject", "promote"):
        command = candidate_sub.add_parser(action)
        command.add_argument("candidate_id")
        command.set_defaults(func=cmd_candidates_transition, action=action)

    archive = candidate_sub.add_parser("archive")
    archive.add_argument("candidate_id")
    archive.set_defaults(func=cmd_candidates_archive)

    create = candidate_sub.add_parser("create")
    create.add_argument("--event-id", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--description", required=True)
    create.add_argument("--content-hash", required=True)
    create.add_argument("--content")
    create.add_argument("--target-path")
    create.add_argument("--reasons", nargs="*", default=[])
    create.set_defaults(func=cmd_candidates_create)

    status = candidate_sub.add_parser("status")
    status.set_defaults(func=cmd_candidates_status)

    auto_promote = candidate_sub.add_parser("auto-promote")
    auto_promote.set_defaults(func=cmd_candidates_auto_promote)

    storage = subparsers.add_parser("storage")
    storage_sub = storage.add_subparsers(dest="storage_command", required=True)
    rotate = storage_sub.add_parser("rotate")
    rotate.set_defaults(func=cmd_storage_rotate)

    relations = subparsers.add_parser("relations")
    relations_sub = relations.add_subparsers(dest="relations_command", required=True)
    relations_add = relations_sub.add_parser("add")
    relations_add.add_argument("source_candidate_id")
    relations_add.add_argument("target_candidate_id")
    relations_add.add_argument("relation_type", choices=[r.value for r in RelationType])
    relations_add.add_argument(
        "--confidence", choices=[c.value for c in Confidence], default="medium"
    )
    relations_add.add_argument("--reasons", nargs="+", required=True)
    relations_add.set_defaults(func=cmd_relations_add)
    relations_list = relations_sub.add_parser("list")
    relations_list.add_argument("--source-candidate-id")
    relations_list.add_argument("--target-candidate-id")
    relations_list.add_argument("--relation-type", choices=[r.value for r in RelationType])
    relations_list.set_defaults(func=cmd_relations_list)
    relations_remove = relations_sub.add_parser("remove")
    relations_remove.add_argument("relation_id")
    relations_remove.set_defaults(func=cmd_relations_remove)

    verify = subparsers.add_parser("verify")
    verify_sub = verify.add_subparsers(dest="verify_command", required=True)
    verify_package = verify_sub.add_parser("package")
    verify_package.add_argument("artifacts", nargs="*")
    verify_package.set_defaults(func=cmd_verify_package)

    rules = subparsers.add_parser("rules")
    rules_sub = rules.add_subparsers(dest="rules_command", required=True)
    rules_list = rules_sub.add_parser("list")
    rules_list.set_defaults(func=cmd_rules_list)
    rules_validate = rules_sub.add_parser("validate")
    rules_validate.add_argument("--path", help="Path to a rule file to validate")
    rules_validate.set_defaults(func=cmd_rules_validate)
    rules_test = rules_sub.add_parser("test")
    rules_test.set_defaults(func=cmd_rules_test)

    compat = subparsers.add_parser("compat")
    compat_sub = compat.add_subparsers(dest="compat_command", required=True)
    compat_probe = compat_sub.add_parser("probe")
    compat_probe.set_defaults(func=cmd_compat_probe)
    compat_list = compat_sub.add_parser("list")
    compat_list.set_defaults(func=cmd_compat_list)
    compat_restore = compat_sub.add_parser("restore")
    compat_restore.add_argument("intent_id")
    compat_restore.set_defaults(func=cmd_compat_restore)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-skill-guard")
    configure_parser(parser)

    return parser


def main(argv: list[str] | None = None) -> NoReturn:
    parser = build_parser()
    args = parser.parse_args(argv)
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
