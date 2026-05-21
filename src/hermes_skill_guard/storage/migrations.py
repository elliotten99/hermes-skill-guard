"""Schema migration framework for the SQLite state store.

This module owns forward-only schema evolution for ``StateStore``. The
:class:`StateStore` invokes :func:`apply_migrations` once per process startup
(after the idempotent ``CREATE TABLE IF NOT EXISTS`` block in
``StateStore.initialize``) so existing databases pick up additive changes
shipped in newer plugin versions.

Ordering note
-------------

This framework runs after ``StateStore._migrate_schema`` (the legacy column
adder kept for backwards compatibility with pre-migration databases). Future
migrations should fold any new legacy ``ALTER TABLE`` logic into a real
``Migration`` v2+ entry below rather than extending ``_migrate_schema``.

How to add a new migration
--------------------------

1. Pick the next integer version (current head is the largest ``version`` in
   :data:`MIGRATIONS`).
2. Write an ``up`` function that takes a :class:`sqlite3.Connection` and runs
   the DDL/DML needed to move forward by exactly one schema version. The
   function must be idempotent in the sense that re-running the *same*
   migration on a database that already advanced past its version is not
   expected (the framework guards against that with the ``schema_version``
   table) — but feel free to use ``IF NOT EXISTS`` clauses for safety.
3. Append a :class:`Migration` entry to :data:`MIGRATIONS`. Keep the list
   sorted by ``version``.
4. Update :func:`StateStore.initialize` only if the new migration is *not*
   covered by the baseline ``CREATE TABLE IF NOT EXISTS`` block (i.e. when
   adding columns or altering constraints rather than introducing whole new
   tables that the baseline already creates).
5. Add a test under ``tests/unit/test_migrations.py``.

The framework deliberately does not support ``downgrade`` while the plugin is
on the v0.x line — operators should restore from backup instead.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

__all__ = [
    "MIGRATIONS",
    "Migration",
    "apply_migrations",
    "current_version",
    "ensure_schema_version_table",
]


@dataclass(frozen=True, slots=True)
class Migration:
    """A single forward schema migration step."""

    version: int
    description: str
    up: Callable[[sqlite3.Connection], None]


def _noop(_conn: sqlite3.Connection) -> None:
    """Placeholder up() for the baseline migration.

    The v0.1.10 schema is created idempotently by
    ``StateStore.initialize`` via ``CREATE TABLE IF NOT EXISTS``. The
    baseline migration only exists to stamp ``schema_version`` so future
    migrations have a reference point.
    """


MIGRATIONS: list[Migration] = [
    Migration(
        version=1,
        description="initial schema (v0.1.10 baseline)",
        up=_noop,
    ),
]


def ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    """Create the ``schema_version`` table if it does not exist.

    The table is constrained to a single row via a ``CHECK`` on the primary
    key, so callers can rely on ``SELECT version FROM schema_version`` to
    return at most one row.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def current_version(conn: sqlite3.Connection) -> int:
    """Return the current schema version, or 0 if unset.

    A return value of 0 indicates that no migration has been recorded
    (either because ``schema_version`` does not yet exist or because it is
    empty). Callers should treat 0 as "apply every migration".
    """
    ensure_schema_version_table(conn)
    row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
    if row is None:
        return 0
    return int(row[0])


def _record_version(conn: sqlite3.Connection, version: int) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT INTO schema_version (id, version, applied_at) VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            version = excluded.version,
            applied_at = excluded.applied_at
        """,
        (version, now),
    )


def apply_migrations(
    conn: sqlite3.Connection,
    logger: logging.Logger | None = None,
) -> int:
    """Apply every migration with ``version > current_version(conn)``.

    Each migration runs inside an explicit ``BEGIN IMMEDIATE`` /
    ``COMMIT`` / ``ROLLBACK`` block. The explicit transaction is required
    because Python's ``sqlite3`` driver only opens implicit transactions
    for DML; DDL statements (``CREATE TABLE``, ``ALTER TABLE``, ...)
    autocommit under the default isolation level and would otherwise
    escape a ``with conn:`` rollback. ``BEGIN IMMEDIATE`` also takes the
    write lock up-front, preventing two concurrent processes from
    racing to apply the same migration.

    On failure both DML and DDL are rolled back via explicit
    ``BEGIN IMMEDIATE`` / ``ROLLBACK``; the ``schema_version`` pointer
    is not advanced, and the exception propagates so callers can decide
    whether to abort startup.

    Args:
        conn: An open SQLite connection. Caller owns its lifetime.
        logger: Optional logger used for ``INFO`` events per migration.

    Returns:
        The schema version after migrations finished (== head when
        successful).
    """
    log = logger or logging.getLogger(__name__)
    ensure_schema_version_table(conn)
    head_before = current_version(conn)
    pending = sorted(
        (m for m in MIGRATIONS if m.version > head_before),
        key=lambda m: m.version,
    )
    if not pending:
        log.debug("schema migrations: nothing to apply (head=%d)", head_before)
        return head_before

    applied_to = head_before
    for migration in pending:
        log.info(
            "applying schema migration v%d: %s",
            migration.version,
            migration.description,
        )
        # Explicit transaction so DDL (CREATE/ALTER) is included in rollback.
        # The default sqlite3 isolation level autocommits DDL, defeating
        # the `with conn:` context manager's rollback semantics.
        try:
            conn.execute("BEGIN IMMEDIATE")
            migration.up(conn)
            _record_version(conn, migration.version)
            conn.commit()
        except Exception:
            conn.rollback()
            log.exception(
                "schema migration v%d failed; halting at v%d",
                migration.version,
                applied_to,
            )
            raise
        applied_to = migration.version
        log.info("schema migration v%d applied", migration.version)
    return applied_to
