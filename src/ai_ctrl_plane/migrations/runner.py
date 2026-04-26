"""Numbered SQL migration runner for the cache database.

Discovers ``NNN_*.sql`` files inside this package, applies each one in order,
and records what's been applied in the ``schema_migrations`` table.

A pre-existing database created before this runner existed is detected via
the legacy ``cache_meta.version`` row and treated as already at migration
``001`` so we don't try to re-create its tables.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from importlib.resources import files
from importlib.resources.abc import Traversable

_VERSION_RE = re.compile(r"^(\d{3,})_")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _migration_files() -> list[Traversable]:
    """Return migration files sorted by version number.

    Sorts numerically (``int(version)``) rather than lexically so a
    future ``1000_*.sql`` doesn't sort before ``999_*.sql`` once
    versions cross a digit-width boundary.
    """
    pkg = files(__package__)
    found: list[tuple[int, Traversable]] = []
    for entry in pkg.iterdir():
        name = entry.name
        if not name.endswith(".sql"):
            continue
        m = _VERSION_RE.match(name)
        if not m:
            continue
        found.append((int(m.group(1)), entry))
    found.sort(key=lambda x: x[0])
    return [entry for _, entry in found]


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )


def _applied_versions(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}


def _backfill_legacy(conn: sqlite3.Connection, applied: set[str]) -> None:
    """If the DB pre-dates the runner, mark the initial migration as applied.

    Older databases used ``cache_meta.version`` to track schema state. When
    that row exists we already have the tables created by ``001_initial.sql``,
    so we should skip re-running it and let later migrations apply on top.
    """
    if applied:
        return
    try:
        row = conn.execute("SELECT value FROM cache_meta WHERE key = 'version'").fetchone()
    except sqlite3.OperationalError:
        return  # cache_meta doesn't exist — fresh DB
    if not row:
        return
    conn.execute(
        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        ("001", _now_iso()),
    )
    conn.commit()
    applied.add("001")


def run_migrations(conn: sqlite3.Connection) -> list[str]:
    """Apply pending migrations. Returns the list of versions just applied."""
    _ensure_migrations_table(conn)
    applied = _applied_versions(conn)
    _backfill_legacy(conn, applied)

    just_applied: list[str] = []
    for entry in _migration_files():
        m = _VERSION_RE.match(entry.name)
        assert m is not None  # filtered in _migration_files
        version = m.group(1)
        if version in applied:
            continue
        sql = entry.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (version, _now_iso()),
        )
        conn.commit()
        just_applied.append(version)
    return just_applied
