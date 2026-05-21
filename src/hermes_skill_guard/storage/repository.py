"""SQLite WAL-backed state store."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hermes_skill_guard.config import EventsConfig
from hermes_skill_guard.ids import new_audit_id, new_candidate_id, new_event_id
from hermes_skill_guard.schemas import (
    Candidate,
    CandidateStatus,
    Decision,
    EventRecord,
    PromotionAttempt,
    PromotionAttemptStatus,
    RelationType,
    SkillRelation,
    validate_candidate_transition,
)
from hermes_skill_guard.storage.migrations import apply_migrations

_logger = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class StoreCounters:
    dropped_write_count: int = 0
    sqlite_busy_count: int = 0
    rotation_failed_count: int = 0


class StateStore:
    def __init__(self, db_path: Path, events_config: EventsConfig | None = None) -> None:
        self.db_path = db_path.expanduser()
        self.events_config = events_config or EventsConfig()
        self.counters = StoreCounters()
        self._write_count = 0
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=3)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=3000;")
            conn.execute("PRAGMA foreign_keys=ON;")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    parent_event_id TEXT,
                    created_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    tool_name TEXT,
                    skill_name TEXT,
                    payload_summary TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    redaction_applied INTEGER NOT NULL,
                    redaction_failed INTEGER NOT NULL,
                    duration_ms INTEGER,
                    error_type TEXT
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    audit_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    reasons TEXT NOT NULL,
                    rule_ids TEXT NOT NULL,
                    dry_run INTEGER NOT NULL,
                    enforcement_mode TEXT NOT NULL,
                    FOREIGN KEY(event_id) REFERENCES events(event_id)
                );

                CREATE TABLE IF NOT EXISTS candidates (
                    candidate_id TEXT PRIMARY KEY,
                    source_event_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reasons TEXT NOT NULL,
                    actor TEXT,
                    session_id TEXT,
                    task_id TEXT,
                    tool_call_id TEXT,
                    payload_hash TEXT,
                    reviewed_by TEXT,
                    reviewed_at TEXT,
                    promoted_at TEXT,
                    promotable INTEGER NOT NULL DEFAULT 1,
                    content TEXT,
                    target_path TEXT
                );

                CREATE TABLE IF NOT EXISTS candidate_transitions (
                    transition_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    from_status TEXT NOT NULL,
                    to_status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
                );

                CREATE TABLE IF NOT EXISTS counters (
                    name TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS skill_relations (
                    relation_id TEXT PRIMARY KEY,
                    source_candidate_id TEXT NOT NULL,
                    target_candidate_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    reasons TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(source_candidate_id) REFERENCES candidates(candidate_id),
                    FOREIGN KEY(target_candidate_id) REFERENCES candidates(candidate_id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_candidates_source_event
                    ON candidates(source_event_id);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_relations_unique
                    ON skill_relations(source_candidate_id, target_candidate_id, relation_type);

                CREATE TABLE IF NOT EXISTS promotion_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    tool_call_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    skill_name TEXT NOT NULL,
                    skill_manage_args TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
                );

                CREATE TABLE IF NOT EXISTS modules (
                    module_id TEXT PRIMARY KEY,
                    intent_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    coverage_confidence TEXT,
                    covered_since_version TEXT,
                    last_probe_at TEXT,
                    probe_result TEXT
                );
                """
            )
            self._migrate_schema(conn)
            apply_migrations(conn, _logger)

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        # Stage B note: legacy column-adder kept for back-compat. New schema
        # changes should land as a Migration v2+ in storage/migrations.py
        # rather than extending this method.
        candidate_columns = {row["name"] for row in conn.execute("PRAGMA table_info(candidates)")}
        migrations = {
            "actor": "ALTER TABLE candidates ADD COLUMN actor TEXT",
            "session_id": "ALTER TABLE candidates ADD COLUMN session_id TEXT",
            "task_id": "ALTER TABLE candidates ADD COLUMN task_id TEXT",
            "tool_call_id": "ALTER TABLE candidates ADD COLUMN tool_call_id TEXT",
            "payload_hash": "ALTER TABLE candidates ADD COLUMN payload_hash TEXT",
            "reviewed_by": "ALTER TABLE candidates ADD COLUMN reviewed_by TEXT",
            "reviewed_at": "ALTER TABLE candidates ADD COLUMN reviewed_at TEXT",
            "promoted_at": "ALTER TABLE candidates ADD COLUMN promoted_at TEXT",
            "promotable": (
                "ALTER TABLE candidates ADD COLUMN promotable INTEGER NOT NULL DEFAULT 1"
            ),
            "content": "ALTER TABLE candidates ADD COLUMN content TEXT",
            "target_path": "ALTER TABLE candidates ADD COLUMN target_path TEXT",
        }
        for column, sql in migrations.items():
            if column not in candidate_columns:
                conn.execute(sql)

    def _try_record(self, fn: Callable[[sqlite3.Connection], None], counter_name: str) -> None:
        try:
            with self.connect() as conn:
                fn(conn)
        except sqlite3.OperationalError:
            self.counters.sqlite_busy_count += 1
            self.increment_counter("sqlite_busy_count")
        except Exception:
            self.counters.dropped_write_count += 1
            self.increment_counter(counter_name)

    def record_event(self, event: EventRecord) -> None:
        def _insert(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT OR REPLACE INTO events (
                    event_id, trace_id, parent_event_id, created_at, event_type,
                    tool_name, skill_name, payload_summary, payload_hash,
                    redaction_applied, redaction_failed, duration_ms, error_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.trace_id,
                    event.parent_event_id,
                    utc_now(),
                    event.event_type,
                    event.tool_name,
                    event.skill_name,
                    json.dumps(event.payload_summary, ensure_ascii=False, sort_keys=True),
                    event.payload_hash,
                    int(event.redaction_applied),
                    int(event.redaction_failed),
                    event.duration_ms,
                    event.error_type,
                ),
            )

        self._try_record(_insert, "dropped_write_count")
        self._write_count += 1
        if self._write_count % self.events_config.rotate_every_n_writes == 0:
            self.rotate_events()

    def record_audit(self, decision: Decision) -> None:
        def _insert(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT OR REPLACE INTO audit_log (
                    audit_id, event_id, trace_id, created_at, decision, confidence,
                    reasons, rule_ids, dry_run, enforcement_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_audit_id(),
                    decision.event_id,
                    decision.trace_id,
                    utc_now(),
                    decision.decision.value,
                    decision.confidence.value,
                    json.dumps(decision.reasons, ensure_ascii=False),
                    json.dumps(decision.rule_ids, ensure_ascii=False),
                    int(decision.dry_run),
                    decision.enforcement_mode.value,
                ),
            )

        self._try_record(_insert, "dropped_write_count")

    def increment_counter(self, name: str, amount: int = 1) -> None:
        try:
            with self.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO counters(name, value) VALUES(?, ?)
                    ON CONFLICT(name) DO UPDATE SET value = value + excluded.value
                    """,
                    (name, amount),
                )
        except Exception:
            _logger.warning("Counter increment failed for %s", name, exc_info=True)

    def rotate_events(self) -> None:
        try:
            cutoff = datetime.now(UTC) - timedelta(days=self.events_config.ttl_days)
            with self.connect() as conn:
                conn.execute("DELETE FROM events WHERE created_at < ?", (cutoff.isoformat(),))
                count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                overflow = count - self.events_config.max_rows
                if overflow > 0:
                    conn.execute(
                        """
                        DELETE FROM events WHERE event_id IN (
                            SELECT event_id FROM events ORDER BY created_at ASC LIMIT ?
                        )
                        """,
                        (overflow,),
                    )
            max_bytes = self.events_config.max_db_mb * 1024 * 1024
            if self.db_path.exists() and self.db_path.stat().st_size > max_bytes:
                with self.connect() as conn:
                    conn.execute(
                        """
                        DELETE FROM events WHERE event_id IN (
                            SELECT event_id FROM events ORDER BY created_at ASC LIMIT 1000
                        )
                        """
                    )
        except Exception:
            self.counters.rotation_failed_count += 1
            self.increment_counter("rotation_failed_count")

    def list_events(self) -> list[dict[str, object]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM events ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def list_candidates(self) -> list[dict[str, object]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM candidates ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def mark_dangling_candidates(self, days: int = 30) -> list[str]:
        """Mark promoted candidates with no recent activity as dangling.

        Finds all PROMOTED candidates whose updated_at is older than *days*
        and transitions them to DANGLING status. Records the transition
        in candidate_transitions and an audit_log entry.

        Args:
            days: Number of days of inactivity before a candidate is
                considered dangling. Defaults to 30.

        Returns:
            List of candidate IDs that were marked dangling.
        """
        cutoff = datetime.now(UTC) - timedelta(days=days)
        cutoff_iso = cutoff.isoformat()
        dangling_ids: list[str] = []

        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT candidate_id, trace_id FROM candidates
                WHERE status = ? AND updated_at < ?
                """,
                (CandidateStatus.PROMOTED.value, cutoff_iso),
            ).fetchall()

            for row in rows:
                candidate_id = str(row["candidate_id"])
                trace_id = str(row["trace_id"])
                event_id = f"evt_{new_candidate_id()}"
                reason = f"no activity for {days} days"

                conn.execute(
                    """
                    INSERT OR IGNORE INTO events (
                        event_id, trace_id, parent_event_id, created_at, event_type,
                        tool_name, skill_name, payload_summary, payload_hash,
                        redaction_applied, redaction_failed, duration_ms, error_type
                    ) VALUES (?, ?, NULL, ?, ?, NULL, NULL, ?, ?, 1, 0, NULL, NULL)
                    """,
                    (
                        event_id,
                        trace_id,
                        utc_now(),
                        "candidate_dangling",
                        "{}",
                        "candidate-dangling",
                    ),
                )
                conn.execute(
                    "UPDATE candidates SET status = ?, updated_at = ? WHERE candidate_id = ?",
                    (CandidateStatus.DANGLING.value, utc_now(), candidate_id),
                )
                conn.execute(
                    """
                    INSERT INTO candidate_transitions (
                        transition_id, candidate_id, event_id, created_at, from_status,
                        to_status, reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"trn_{new_candidate_id()}",
                        candidate_id,
                        event_id,
                        utc_now(),
                        CandidateStatus.PROMOTED.value,
                        CandidateStatus.DANGLING.value,
                        reason,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO audit_log (
                        audit_id, event_id, trace_id, created_at, decision, confidence,
                        reasons, rule_ids, dry_run, enforcement_mode
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_audit_id(),
                        event_id,
                        trace_id,
                        utc_now(),
                        "candidate_transition",
                        "high",
                        json.dumps([reason], ensure_ascii=False),
                        json.dumps(["candidate.dangling_detection"], ensure_ascii=False),
                        0,
                        "audit",
                    ),
                )
                dangling_ids.append(candidate_id)

        return dangling_ids

    def create_candidate(self, candidate: Candidate) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO candidates (
                    candidate_id, source_event_id, trace_id, created_at, updated_at,
                    name, description, content_hash, status, reasons, actor,
                    session_id, task_id, tool_call_id, payload_hash, reviewed_by,
                    reviewed_at, promoted_at, promotable, content, target_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.candidate_id,
                    candidate.source_event_id,
                    candidate.trace_id,
                    utc_now(),
                    utc_now(),
                    candidate.name,
                    candidate.description,
                    candidate.content_hash,
                    candidate.status.value,
                    json.dumps(candidate.reasons, ensure_ascii=False),
                    candidate.actor,
                    candidate.session_id,
                    candidate.task_id,
                    candidate.tool_call_id,
                    candidate.payload_hash,
                    candidate.reviewed_by,
                    candidate.reviewed_at,
                    candidate.promoted_at,
                    int(candidate.promotable),
                    candidate.content,
                    candidate.target_path,
                ),
            )
            conn.execute(
                """
                INSERT INTO candidate_transitions (
                    transition_id, candidate_id, event_id, created_at, from_status,
                    to_status, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"trn_{new_candidate_id()}",
                    candidate.candidate_id,
                    candidate.source_event_id,
                    utc_now(),
                    "none",
                    candidate.status.value,
                    "candidate created",
                ),
            )

    def get_candidate(self, candidate_id: str) -> dict[str, object] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def find_candidate_by_source_event(self, event_id: str) -> dict[str, object] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM candidates WHERE source_event_id = ?", (event_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def find_pending_promotion_by_skill(self, name: str) -> dict[str, object] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM promotion_attempts
                WHERE skill_name = ? AND status = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (name, PromotionAttemptStatus.PENDING.value),
            ).fetchone()
        return dict(row) if row is not None else None

    def create_promotion_attempt(self, attempt: PromotionAttempt) -> None:
        with self.connect() as conn:
            candidate = conn.execute(
                "SELECT status, promotable FROM candidates WHERE candidate_id = ?",
                (attempt.candidate_id,),
            ).fetchone()
            if candidate is None:
                raise KeyError(attempt.candidate_id)
            if CandidateStatus(str(candidate["status"])) != CandidateStatus.APPROVED:
                raise ValueError("candidate must be approved before promotion")
            if int(candidate["promotable"]) != 1:
                raise ValueError("candidate is not promotable")
            now = utc_now()
            conn.execute(
                """
                INSERT INTO promotion_attempts (
                    attempt_id, candidate_id, trace_id, tool_call_id, created_at,
                    updated_at, skill_name, skill_manage_args, status, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.attempt_id,
                    attempt.candidate_id,
                    attempt.trace_id,
                    attempt.tool_call_id,
                    now,
                    now,
                    attempt.skill_name,
                    json.dumps(attempt.skill_manage_args, ensure_ascii=False, sort_keys=True),
                    attempt.status.value,
                    attempt.error,
                ),
            )

    def complete_promotion_attempt(
        self,
        attempt_id: str,
        *,
        succeeded: bool,
        event_id: str,
        error: str | None = None,
    ) -> None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT candidate_id, trace_id, status FROM promotion_attempts
                WHERE attempt_id = ?
                """,
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            if str(row["status"]) != PromotionAttemptStatus.PENDING.value:
                return
            now = utc_now()
            status = (
                PromotionAttemptStatus.SUCCEEDED if succeeded else PromotionAttemptStatus.FAILED
            )
            conn.execute(
                """
                UPDATE promotion_attempts
                SET status = ?, error = ?, updated_at = ?
                WHERE attempt_id = ?
                """,
                (status.value, error, now, attempt_id),
            )
            if succeeded:
                candidate_id = str(row["candidate_id"])
                candidate_row = conn.execute(
                    "SELECT status FROM candidates WHERE candidate_id = ?", (candidate_id,)
                ).fetchone()
                if candidate_row is None:
                    raise KeyError(candidate_id)
                from_status = CandidateStatus(str(candidate_row["status"]))
                validate_candidate_transition(from_status, CandidateStatus.PROMOTED)
                conn.execute(
                    """
                    UPDATE candidates
                    SET status = ?, updated_at = ?, promoted_at = ?
                    WHERE candidate_id = ?
                    """,
                    (CandidateStatus.PROMOTED.value, now, now, candidate_id),
                )
                conn.execute(
                    """
                    INSERT INTO candidate_transitions (
                        transition_id, candidate_id, event_id, created_at, from_status,
                        to_status, reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"trn_{new_candidate_id()}",
                        candidate_id,
                        event_id,
                        now,
                        from_status.value,
                        CandidateStatus.PROMOTED.value,
                        "official skill_manage create observed",
                    ),
                )
            conn.execute(
                """
                INSERT INTO audit_log (
                    audit_id, event_id, trace_id, created_at, decision, confidence,
                    reasons, rule_ids, dry_run, enforcement_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_audit_id(),
                    event_id,
                    str(row["trace_id"]),
                    now,
                    "promotion_succeeded" if succeeded else "promotion_failed",
                    "high" if succeeded else "medium",
                    json.dumps([error or "promotion finalized"], ensure_ascii=False),
                    json.dumps(["candidate.promotion"], ensure_ascii=False),
                    0,
                    "audit",
                ),
            )

    def list_promotion_attempts(self, candidate_id: str | None = None) -> list[dict[str, object]]:
        where = ""
        params: tuple[str, ...] = ()
        if candidate_id is not None:
            where = "WHERE candidate_id = ?"
            params = (candidate_id,)
        with self.connect() as conn:
            # {where} is a fixed literal "WHERE candidate_id = ?"; the value is
            # passed via parameterized query (safe from SQL injection).
            rows = conn.execute(
                f"SELECT * FROM promotion_attempts {where} ORDER BY created_at DESC",  # nosec B608
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def transition_candidate(
        self, candidate_id: str, to_status: CandidateStatus, event_id: str, reason: str
    ) -> None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT status, trace_id FROM candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise KeyError(candidate_id)
            from_status = CandidateStatus(str(row["status"]))
            trace_id = str(row["trace_id"])
            validate_candidate_transition(from_status, to_status)
            now = utc_now()
            reviewed_at = (
                now if to_status in {CandidateStatus.APPROVED, CandidateStatus.REJECTED} else None
            )
            promoted_at = now if to_status == CandidateStatus.PROMOTED else None
            conn.execute(
                """
                INSERT OR IGNORE INTO events (
                    event_id, trace_id, parent_event_id, created_at, event_type,
                    tool_name, skill_name, payload_summary, payload_hash,
                    redaction_applied, redaction_failed, duration_ms, error_type
                ) VALUES (?, ?, NULL, ?, ?, NULL, NULL, ?, ?, 1, 0, NULL, NULL)
                """,
                (
                    event_id,
                    trace_id,
                    now,
                    "candidate_transition",
                    "{}",
                    "candidate-transition",
                ),
            )
            conn.execute(
                """
                UPDATE candidates
                SET status = ?,
                    updated_at = ?,
                    reviewed_at = COALESCE(?, reviewed_at),
                    promoted_at = COALESCE(?, promoted_at)
                WHERE candidate_id = ?
                """,
                (to_status.value, now, reviewed_at, promoted_at, candidate_id),
            )
            conn.execute(
                """
                INSERT INTO candidate_transitions (
                    transition_id, candidate_id, event_id, created_at, from_status,
                    to_status, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"trn_{new_candidate_id()}",
                    candidate_id,
                    event_id,
                    now,
                    from_status.value,
                    to_status.value,
                    reason,
                ),
            )
            conn.execute(
                """
                INSERT INTO audit_log (
                    audit_id, event_id, trace_id, created_at, decision, confidence,
                    reasons, rule_ids, dry_run, enforcement_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_audit_id(),
                    event_id,
                    trace_id,
                    now,
                    "candidate_transition",
                    "high",
                    json.dumps([reason], ensure_ascii=False),
                    json.dumps(["candidate.state_transition"], ensure_ascii=False),
                    0,
                    "audit",
                ),
            )

    def summary(self) -> dict[str, object]:
        with self.connect() as conn:
            events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            audits = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
            candidates = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
            rows = conn.execute("SELECT name, value FROM counters")
            counters = {row["name"]: row["value"] for row in rows}
            wal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        return {
            "events": events,
            "audit_log": audits,
            "candidates": candidates,
            "counters": counters,
            "sqlite_journal_mode": wal_mode,
        }

    def add_relation(self, relation: SkillRelation) -> None:
        """Persist a skill relation between two candidates."""
        if relation.source_candidate_id == relation.target_candidate_id:
            raise ValueError("relation cannot target the same candidate")
        with self.connect() as conn:
            source = conn.execute(
                "SELECT trace_id FROM candidates WHERE candidate_id = ?",
                (relation.source_candidate_id,),
            ).fetchone()
            if source is None:
                raise sqlite3.IntegrityError("source candidate not found")
            conn.execute(
                """
                INSERT INTO skill_relations (
                    relation_id, source_candidate_id, target_candidate_id,
                    relation_type, confidence, reasons, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    relation.relation_id,
                    relation.source_candidate_id,
                    relation.target_candidate_id,
                    relation.relation_type.value,
                    relation.confidence.value,
                    json.dumps(relation.reasons, ensure_ascii=False),
                    relation.created_at,
                ),
            )
            event_id = new_event_id()
            trace_id = str(source["trace_id"])
            conn.execute(
                """
                INSERT INTO events (
                    event_id, trace_id, parent_event_id, created_at, event_type,
                    tool_name, skill_name, payload_summary, payload_hash,
                    redaction_applied, redaction_failed, duration_ms, error_type
                ) VALUES (?, ?, NULL, ?, ?, NULL, NULL, ?, ?, 1, 0, NULL, NULL)
                """,
                (
                    event_id,
                    trace_id,
                    utc_now(),
                    "relation_add",
                    json.dumps(
                        {
                            "relation_id": relation.relation_id,
                            "relation_type": relation.relation_type.value,
                        },
                        ensure_ascii=False,
                    ),
                    "relation-add",
                ),
            )
            conn.execute(
                """
                INSERT INTO audit_log (
                    audit_id, event_id, trace_id, created_at, decision, confidence,
                    reasons, rule_ids, dry_run, enforcement_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_audit_id(),
                    event_id,
                    trace_id,
                    utc_now(),
                    "relation_add",
                    relation.confidence.value,
                    json.dumps(relation.reasons, ensure_ascii=False),
                    json.dumps(["relation.add"], ensure_ascii=False),
                    0,
                    "audit",
                ),
            )

    def list_relations(
        self,
        source_candidate_id: str | None = None,
        target_candidate_id: str | None = None,
        relation_type: RelationType | None = None,
    ) -> list[dict[str, object]]:
        """List skill relations with optional filtering."""
        conditions: list[str] = []
        params: list[object] = []
        if source_candidate_id is not None:
            conditions.append("source_candidate_id = ?")
            params.append(source_candidate_id)
        if target_candidate_id is not None:
            conditions.append("target_candidate_id = ?")
            params.append(target_candidate_id)
        if relation_type is not None:
            conditions.append("relation_type = ?")
            params.append(relation_type.value)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        # conditions are hard-coded predicates with parameter placeholders;
        # values come exclusively from params (safe from SQL injection).
        sql = f"SELECT * FROM skill_relations {where} ORDER BY created_at DESC"  # nosec B608
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def remove_relation(self, relation_id: str) -> bool:
        """Remove a skill relation by ID. Returns True if a row was deleted."""
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM skill_relations WHERE relation_id = ?",
                (relation_id,),
            )
            return int(cursor.rowcount) > 0

    def find_related_candidates(self, candidate_id: str) -> list[dict[str, object]]:
        """Find all relations where the given candidate is either source or target."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM skill_relations
                WHERE source_candidate_id = ? OR target_candidate_id = ?
                ORDER BY created_at DESC
                """,
                (candidate_id, candidate_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        """Close any open resources. Currently a no-op since connections are context-managed."""
        pass

    def wal_enabled(self) -> bool:
        with self.connect() as conn:
            return str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"

    def candidate_status_counts(self) -> dict[str, int]:
        """Count candidates grouped by status."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as count FROM candidates GROUP BY status"
            ).fetchall()
        result: dict[str, int] = {status.value: 0 for status in CandidateStatus}
        for row in rows:
            result[str(row["status"])] = int(row["count"])
        return result

    def recent_audit_decisions(self, limit: int = 10) -> list[dict[str, object]]:
        """Return recent audit_log entries with risky decisions (warn/candidate/block)."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT audit_id, event_id, trace_id, created_at, decision,
                       confidence, reasons, rule_ids, dry_run, enforcement_mode
                FROM audit_log
                WHERE decision IN ('warn', 'candidate', 'block')
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def db_size_mb(self) -> float:
        """Return database file size in megabytes."""
        if not self.db_path.exists():
            return 0.0
        return round(self.db_path.stat().st_size / (1024 * 1024), 2)

    def dangling_candidates(self) -> list[dict[str, object]]:
        """Return candidates in 'dangling' status (promoted but no longer valid)."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT candidate_id, source_event_id, trace_id, created_at,
                       updated_at, name, description, content_hash, status, reasons
                FROM candidates
                WHERE status = 'dangling'
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_events(self, limit: int = 5) -> list[dict[str, object]]:
        """Return most recent events ordered by created_at descending."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, trace_id, parent_event_id, created_at, event_type,
                       tool_name, skill_name, payload_summary, payload_hash,
                       redaction_applied, redaction_failed, duration_ms, error_type
                FROM events
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_probe_result(
        self,
        intent_id: str,
        status: str,
        confidence: str | None,
        since_version: str | None,
        reason: str,
    ) -> None:
        """Persist the result of a capability probe for an intent.

        Upserts a row in the ``modules`` table keyed by *intent_id*.
        """

        def _upsert(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO modules (
                    module_id, intent_id, status, coverage_confidence,
                    covered_since_version, last_probe_at, probe_result
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(intent_id) DO UPDATE SET
                    status = excluded.status,
                    coverage_confidence = excluded.coverage_confidence,
                    covered_since_version = excluded.covered_since_version,
                    last_probe_at = excluded.last_probe_at,
                    probe_result = excluded.probe_result
                """,
                (
                    f"mod_{intent_id}",
                    intent_id,
                    status,
                    confidence,
                    since_version,
                    utc_now(),
                    reason,
                ),
            )

        self._try_record(_upsert, "dropped_write_count")

    def list_module_statuses(self) -> list[dict[str, object]]:
        """Return all rows from the ``modules`` table."""
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM modules ORDER BY last_probe_at DESC").fetchall()
        return [dict(row) for row in rows]

    def update_module_status(self, intent_id: str, status: str, reason: str) -> None:
        """Update the status of a module row by *intent_id*.

        Raises:
            KeyError: If no module row exists for the given *intent_id*.
        """

        def _update(conn: sqlite3.Connection) -> None:
            cursor = conn.execute(
                "SELECT module_id FROM modules WHERE intent_id = ?",
                (intent_id,),
            )
            if cursor.fetchone() is None:
                raise KeyError(intent_id)
            conn.execute(
                """
                UPDATE modules
                SET status = ?, probe_result = ?, last_probe_at = ?
                WHERE intent_id = ?
                """,
                (status, reason, utc_now(), intent_id),
            )

        self._try_record(_update, "dropped_write_count")
