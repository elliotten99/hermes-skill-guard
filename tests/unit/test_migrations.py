"""Tests for the schema migration framework."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from hermes_skill_guard.storage import migrations as migrations_module
from hermes_skill_guard.storage.migrations import (
    MIGRATIONS,
    Migration,
    apply_migrations,
    current_version,
    ensure_schema_version_table,
)
from hermes_skill_guard.storage.repository import StateStore


@pytest.fixture
def store(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "state.db")


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(tmp_path / "raw.db")
    try:
        yield c
    finally:
        c.close()


def _head_version() -> int:
    return max(m.version for m in MIGRATIONS)


def test_fresh_db_records_version_1(store: StateStore) -> None:
    """A freshly-initialized StateStore stamps the schema_version table."""
    with store.connect() as conn:
        assert current_version(conn) == _head_version()
        row = conn.execute("SELECT id, version, applied_at FROM schema_version").fetchall()
    assert len(row) == 1
    assert row[0][0] == 1  # id pinned to 1
    assert row[0][1] == _head_version()
    assert row[0][2]  # applied_at populated


def test_existing_v1_db_does_nothing(store: StateStore) -> None:
    """Re-initializing a v1 database leaves schema_version unchanged."""
    with store.connect() as conn:
        first = conn.execute(
            "SELECT version, applied_at FROM schema_version WHERE id = 1"
        ).fetchone()

    # Construct a second store on the same path → triggers another initialize().
    StateStore(store.db_path)

    with store.connect() as conn:
        second = conn.execute(
            "SELECT version, applied_at FROM schema_version WHERE id = 1"
        ).fetchone()

    assert first == second


def test_failed_migration_does_not_advance_version(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a migration raises, the version pointer must not advance."""
    ensure_schema_version_table(conn)
    # Pretend we are already at v1 (the baseline).
    conn.execute("INSERT INTO schema_version (id, version, applied_at) VALUES (1, 1, '2026-01-01')")
    conn.commit()

    def boom(_c: sqlite3.Connection) -> None:
        raise RuntimeError("intentional failure")

    fake_migrations = [
        Migration(version=1, description="baseline", up=lambda _c: None),
        Migration(version=2, description="broken", up=boom),
    ]
    monkeypatch.setattr(migrations_module, "MIGRATIONS", fake_migrations)

    with pytest.raises(RuntimeError, match="intentional failure"):
        apply_migrations(conn)

    assert current_version(conn) == 1


def test_apply_migrations_is_idempotent(store: StateStore) -> None:
    """Calling apply_migrations twice yields the same head version."""
    with store.connect() as conn:
        v1 = apply_migrations(conn)
        v2 = apply_migrations(conn)
        v3 = apply_migrations(conn)
    assert v1 == v2 == v3 == _head_version()


def test_schema_version_table_has_single_row_constraint(
    store: StateStore,
) -> None:
    """The CHECK on id prevents inserting a second row."""
    with store.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO schema_version (id, version, applied_at) VALUES (2, 99, '2026-01-01')"
        )


def test_current_version_returns_zero_on_empty_table(
    conn: sqlite3.Connection,
) -> None:
    """If schema_version exists but is empty, current_version is 0."""
    ensure_schema_version_table(conn)
    assert current_version(conn) == 0


def test_apply_migrations_logs_each_step(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each applied migration emits an INFO log entry."""
    applied: list[int] = []

    def up_v2(_c: sqlite3.Connection) -> None:
        applied.append(2)

    fake_migrations = [
        Migration(version=1, description="baseline", up=lambda _c: None),
        Migration(version=2, description="add foo", up=up_v2),
    ]
    monkeypatch.setattr(migrations_module, "MIGRATIONS", fake_migrations)

    logger = logging.getLogger("test_migrations")
    with caplog.at_level(logging.INFO, logger="test_migrations"):
        final = apply_migrations(conn, logger)

    assert final == 2
    assert applied == [2]
    # Two "applying" + two "applied" lines at minimum.
    info_lines = [r.message for r in caplog.records if r.levelno == logging.INFO]
    assert any("v1" in m for m in info_lines)
    assert any("v2" in m for m in info_lines)


def test_apply_migrations_returns_head_before_when_no_pending(
    conn: sqlite3.Connection,
) -> None:
    """If everything is already applied, return the existing head."""
    ensure_schema_version_table(conn)
    conn.execute(
        "INSERT INTO schema_version (id, version, applied_at) VALUES (1, ?, '2026-01-01')",
        (_head_version(),),
    )
    conn.commit()
    assert apply_migrations(conn) == _head_version()


def test_failed_ddl_migration_rolls_back(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing migration that issued DDL must have that DDL rolled back.

    Python's sqlite3 default isolation level autocommits DDL statements, so
    a naive ``with conn:`` rollback would leave the partially-created table
    behind. ``apply_migrations`` uses an explicit ``BEGIN IMMEDIATE`` so
    both DML and DDL participate in the same transaction.
    """
    ensure_schema_version_table(conn)
    conn.execute("INSERT INTO schema_version (id, version, applied_at) VALUES (1, 1, '2026-01-01')")
    conn.commit()

    def up_v2_with_ddl(c: sqlite3.Connection) -> None:
        c.execute("CREATE TABLE foo (id INTEGER)")
        raise RuntimeError("simulated DDL failure")

    fake_migrations = [
        Migration(version=1, description="baseline", up=lambda _c: None),
        Migration(version=2, description="broken ddl", up=up_v2_with_ddl),
    ]
    monkeypatch.setattr(migrations_module, "MIGRATIONS", fake_migrations)

    with pytest.raises(RuntimeError, match="simulated DDL failure"):
        apply_migrations(conn)

    # DDL must have been rolled back: the `foo` table should not exist.
    foo_rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='foo'"
    ).fetchall()
    assert foo_rows == [], "DDL from failed migration leaked past rollback"

    # Schema version pointer must still be at v1.
    assert current_version(conn) == 1
