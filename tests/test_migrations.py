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
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"sessions", "projects", "project_memory", "tool_configs", "cache_meta", "schema_migrations"} <= tables
    # FTS5 virtual table exists alongside the regular tables.
    assert "sessions_fts" in tables
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
    """A pre-runner database (cache_meta.version row but no schema_migrations table)
    should be treated as having migration 001 already applied so we don't try to
    re-create its existing tables on top."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    # Simulate the v1/v2/v3 schema state that pre-dated the runner.
    conn.execute("CREATE TABLE cache_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, raw_json TEXT)")
    conn.execute("INSERT INTO cache_meta (key, value) VALUES ('version', '3')")
    conn.commit()

    applied = run_migrations(conn)
    # 001 should NOT have been re-applied (its CREATE TABLE would conflict);
    # later migrations should still apply.
    assert "001" not in applied
    assert "002" in applied
    assert "003" in applied
    versions = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
    assert versions == {"001", "002", "003"}
    conn.close()


def test_cache_db_exposes_schema_version(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "v.db")
    assert db.schema_version == "003"
    assert db.cache_status()["version"] == "003"
    db.close()
