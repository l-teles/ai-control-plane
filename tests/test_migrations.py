"""Tests for the schema migration runner."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ai_ctrl_plane.db import CacheDB
from ai_ctrl_plane.migrations.runner import run_migrations


def test_runner_creates_tables_on_fresh_db(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "fresh.db")
    applied = run_migrations(conn)
    assert "001" in applied
    assert "002" in applied
    assert "003" in applied
    assert "004" in applied
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"sessions", "projects", "project_memory", "tool_configs", "cache_meta", "schema_migrations"} <= tables
    # FTS5 virtual table exists alongside the regular tables.
    assert "sessions_fts" in tables
    # Migration 004 added a ``content`` column to the FTS index.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions_fts)").fetchall()}
    assert "content" in cols
    conn.close()


def test_runner_is_idempotent(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "idem.db")
    first = run_migrations(conn)
    second = run_migrations(conn)
    assert first  # something applied the first time
    assert second == []  # nothing left to apply
    conn.close()


def test_runner_records_applied_version_with_timestamp(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "ts.db")
    run_migrations(conn)
    rows = conn.execute("SELECT version, applied_at FROM schema_migrations ORDER BY version").fetchall()
    assert rows
    for version, applied_at in rows:
        assert version.isdigit() and len(version) >= 3
        assert applied_at  # ISO timestamp string
    conn.close()


def test_legacy_db_with_cache_meta_version_skips_initial_migration(tmp_path: Path) -> None:
    """A pre-runner database (``cache_meta.version`` row, no
    ``schema_migrations`` table) should be treated as having migration 001
    already applied so the runner doesn't try to re-create its tables on
    top.

    The fixture mirrors the *full* v3 schema — every table and column
    that migration 001 declares — so the test exercises a realistic
    upgrade path. A minimal stub would skip 001 and pass even if later
    migrations referenced columns the legacy DB didn't have.
    """
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    # Mirror migrations/001_initial.sql as the v3 state.
    conn.executescript(
        """
        CREATE TABLE cache_meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            uuid TEXT NOT NULL,
            summary TEXT,
            created TEXT,
            cwd TEXT,
            model TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_creation_tokens INTEGER DEFAULT 0,
            estimated_cost REAL DEFAULT 0,
            raw_json TEXT
        );
        CREATE TABLE projects (
            encoded_name TEXT PRIMARY KEY,
            path TEXT, name TEXT,
            session_count INTEGER DEFAULT 0,
            memory_file_count INTEGER DEFAULT 0,
            last_cost REAL,
            last_session_id TEXT,
            last_input_tokens INTEGER,
            last_output_tokens INTEGER,
            has_trust_accepted INTEGER DEFAULT 0,
            onboarding_seen_count INTEGER DEFAULT 0,
            metadata_json TEXT
        );
        CREATE TABLE project_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_encoded_name TEXT NOT NULL REFERENCES projects(encoded_name),
            filename TEXT NOT NULL,
            content TEXT
        );
        CREATE TABLE tool_configs (
            tool TEXT PRIMARY KEY, config_json TEXT, updated_at TEXT
        );
        CREATE INDEX idx_sessions_cwd ON sessions(cwd);
        CREATE INDEX idx_sessions_created ON sessions(created);
        INSERT INTO cache_meta (key, value) VALUES ('version', '3');
        -- And a representative legacy session row so the test verifies
        -- that downstream code (later migrations + refresh_cache) can
        -- read from columns that 001 declares.
        INSERT INTO sessions (id, source, uuid, summary, created, cwd, raw_json)
        VALUES ('claude:legacy-aaaa', 'claude', 'legacy-aaaa', 'old session',
                '2026-01-01T00:00:00Z', '/tmp/repo', '{}');
        """
    )
    conn.commit()

    applied = run_migrations(conn)
    # 001 should NOT have been re-applied (its CREATE TABLE would conflict);
    # later migrations should still apply.
    assert "001" not in applied
    assert "002" in applied
    assert "003" in applied
    assert "004" in applied
    versions = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
    assert versions == {"001", "002", "003", "004"}
    # The legacy row survives the migration and the new columns added
    # by 002 (source_path, source_mtime) are now reachable as NULL.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    assert "source_path" in cols
    assert "source_mtime" in cols
    row = conn.execute("SELECT id, source, uuid, source_path FROM sessions").fetchone()
    assert row[0] == "claude:legacy-aaaa"
    assert row[3] is None  # source_path is NULL on the legacy row
    conn.close()


def test_cache_db_exposes_schema_version(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "v.db")
    assert db.schema_version == "004"
    assert db.cache_status()["version"] == "004"
    db.close()


def test_schema_version_orders_numerically_not_lexically(tmp_path: Path) -> None:
    """``schema_version`` reads the highest applied migration; it must
    sort by integer value of the version, not lexically — once we cross
    ``999`` -> ``1000``, lex sort would pick ``999`` over ``1000``.
    Regression for PR #27 review #22."""
    db = CacheDB(tmp_path / "v.db")
    # Inject a forged future migration row at version "1000" so we can
    # verify the SQL ordering keeps it ahead of the lexically-greater
    # but numerically-smaller "999".
    db._conn.execute(
        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)", ("999", "2026-01-01")
    )
    db._conn.execute(
        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)", ("1000", "2026-02-01")
    )
    db._conn.commit()
    assert db.schema_version == "1000"
    db.close()


def test_migration_files_sort_numerically_not_lexically() -> None:
    """If we ever cross 999 -> 1000, lexical sort would put 1000 before
    999 ("1" < "9"). The runner sorts by ``int(version)`` to stay
    correct across digit-width boundaries. Regression for PR #27
    review #19."""
    from ai_ctrl_plane.migrations.runner import _VERSION_RE, _migration_files

    files = _migration_files()
    versions = [int(_VERSION_RE.match(f.name).group(1)) for f in files]  # type: ignore[union-attr]
    # The captured versions come out in ascending integer order.
    assert versions == sorted(versions)
