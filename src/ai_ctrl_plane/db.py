"""SQLite cache layer for AI Control Plane.

Schema is managed by the numbered migration files in
:mod:`ai_ctrl_plane.migrations`.  On startup the cache is refreshed in a
background thread; routes serve whatever data is already available
(partial or full).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from .migrations.runner import run_migrations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _escape_like(value: str) -> str:
    """Escape ``%`` and ``_`` for use in a LIKE pattern with ``ESCAPE '\\'``."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def default_cache_dir() -> Path:
    """Return the default cache directory for the app."""
    import sys

    if sys.platform == "win32":
        import os

        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        import os

        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "ai-ctrl-plane"


# ---------------------------------------------------------------------------
# Cache manager
# ---------------------------------------------------------------------------


class CacheDB:
    """Thread-safe SQLite cache manager."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = _connect(db_path)
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            run_migrations(self._conn)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- Meta helpers -------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM cache_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache_meta (key, value) VALUES (?, ?)",
                (key, value),
            )
            self._conn.commit()

    @property
    def status(self) -> str:
        return self.get_meta("status") or "empty"

    @property
    def built_at(self) -> str | None:
        return self.get_meta("built_at")

    # -- Status API ---------------------------------------------------------

    def cache_status(self) -> dict:
        """Return cache status for the /api/cache-status endpoint."""
        return {
            "status": self.status,
            "built_at": self.built_at,
            "version": self.schema_version,
            "db_path": str(self.db_path),
        }

    @property
    def schema_version(self) -> str:
        """Highest applied migration version (e.g. ``"002"``)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
            ).fetchone()
        return row["version"] if row else ""

    # -- Bulk write (used during cache build) --------------------------------

    def _clear_all(self) -> None:
        with self._lock:
            for tbl in ("project_memory", "sessions", "projects", "tool_configs"):
                self._conn.execute(f"DELETE FROM {tbl}")  # noqa: S608
            self._conn.commit()

    def insert_sessions(self, sessions: list[dict]) -> None:
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO sessions "
                "(id, source, uuid, summary, created, cwd, model, "
                "input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, "
                "estimated_cost, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        f"{s['source']}:{s['id']}",
                        s.get("source", ""),
                        s.get("id", ""),
                        s.get("summary", ""),
                        s.get("created_at", ""),
                        s.get("cwd", "").replace("\\", "/"),
                        s.get("model", ""),
                        s.get("input_tokens", 0),
                        s.get("output_tokens", 0),
                        s.get("cache_read_tokens", 0),
                        s.get("cache_creation_tokens", 0),
                        s.get("estimated_cost", 0),
                        json.dumps(s, default=str),
                    )
                    for s in sessions
                ],
            )
            self._conn.commit()

    def insert_projects(self, projects: list[dict]) -> None:
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO projects "
                "(encoded_name, path, name, session_count, memory_file_count, "
                "last_cost, last_session_id, last_input_tokens, last_output_tokens, "
                "has_trust_accepted, onboarding_seen_count, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        p["encoded_name"],
                        p.get("path", "").replace("\\", "/"),
                        p.get("name", ""),
                        p.get("session_count", 0),
                        p.get("memory_file_count", 0),
                        p.get("last_cost"),
                        p.get("last_session_id"),
                        p.get("last_input_tokens"),
                        p.get("last_output_tokens"),
                        1 if p.get("has_trust_accepted") else 0,
                        p.get("onboarding_seen_count", 0),
                        json.dumps(p.get("metadata", {}), default=str),
                    )
                    for p in projects
                ],
            )
            self._conn.commit()

    def insert_project_memory(self, items: list[dict]) -> None:
        with self._lock:
            self._conn.executemany(
                "INSERT INTO project_memory (project_encoded_name, filename, content) VALUES (?, ?, ?)",
                [(m["project_encoded_name"], m["filename"], m["content"]) for m in items],
            )
            self._conn.commit()

    def insert_tool_config(self, tool: str, config: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO tool_configs (tool, config_json, updated_at) VALUES (?, ?, ?)",
                (tool, json.dumps(config, default=str), _now_iso()),
            )
            self._conn.commit()

    # -- Read helpers -------------------------------------------------------

    def get_sessions(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT raw_json FROM sessions ORDER BY created DESC").fetchall()
        return [json.loads(r["raw_json"]) for r in rows]

    def get_session_index(self) -> dict[str, dict]:
        with self._lock:
            rows = self._conn.execute("SELECT id, raw_json FROM sessions").fetchall()
        return {r["id"]: json.loads(r["raw_json"]) for r in rows}

    def get_projects(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT *, "
                "(SELECT COUNT(*) FROM project_memory pm "
                "WHERE pm.project_encoded_name = p.encoded_name) AS memory_count, "
                "(SELECT COALESCE(SUM(s.estimated_cost), 0) FROM sessions s "
                "WHERE p.path != '' AND ("
                "s.cwd = p.path OR s.cwd LIKE REPLACE(REPLACE(REPLACE("
                "p.path, '\\', '\\\\'), '%', '\\%'), '_', '\\_') || '/%' ESCAPE '\\'"
                ")) AS estimated_cost, "
                "(SELECT COUNT(*) FROM sessions s "
                "WHERE p.path != '' AND ("
                "s.cwd = p.path OR s.cwd LIKE REPLACE(REPLACE(REPLACE("
                "p.path, '\\', '\\\\'), '%', '\\%'), '_', '\\_') || '/%' ESCAPE '\\'"
                ")) AS real_session_count "
                "FROM projects p ORDER BY name"
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["metadata"] = json.loads(d.pop("metadata_json", "{}"))
            d["has_trust_accepted"] = bool(d.get("has_trust_accepted"))
            d["memory_file_count"] = d.pop("memory_count", 0)
            d["estimated_cost"] = d.get("estimated_cost", 0)
            # Use actual session count from sessions table when path is available
            real = d.pop("real_session_count", 0)
            if real:
                d["session_count"] = real
            result.append(d)
        return result

    def get_project(self, encoded_name: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM projects WHERE encoded_name = ?", (encoded_name,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["metadata"] = json.loads(d.pop("metadata_json", "{}"))
        d["has_trust_accepted"] = bool(d.get("has_trust_accepted"))
        return d

    def get_project_memory(self, encoded_name: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT filename, content FROM project_memory WHERE project_encoded_name = ? ORDER BY filename",
                (encoded_name,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_tool_config(self, tool: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT config_json FROM tool_configs WHERE tool = ?", (tool,)).fetchone()
        return json.loads(row["config_json"]) if row else None

    def get_all_tool_configs(self) -> dict[str, dict]:
        with self._lock:
            rows = self._conn.execute("SELECT tool, config_json FROM tool_configs").fetchall()
        return {r["tool"]: json.loads(r["config_json"]) for r in rows}

    def get_project_global_stats(self) -> dict:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS total_projects FROM projects").fetchone()
            mem_row = self._conn.execute("SELECT COUNT(*) AS total_memory_files FROM project_memory").fetchone()
            cost_row = self._conn.execute(
                "SELECT COALESCE(SUM(estimated_cost), 0) AS aggregate_cost FROM sessions"
            ).fetchone()
            session_row = self._conn.execute("SELECT COUNT(*) AS total_sessions FROM sessions").fetchone()
        return {
            "total_projects": row["total_projects"],
            "total_sessions": session_row["total_sessions"],
            "aggregate_cost": round(cost_row["aggregate_cost"], 2),
            "total_memory_files": mem_row["total_memory_files"],
        }

    def get_project_sessions(self, project_path: str) -> list[dict]:
        """Get sessions whose cwd starts with the given project path."""
        project_path = project_path.replace("\\", "/")
        escaped = _escape_like(project_path)
        with self._lock:
            rows = self._conn.execute(
                "SELECT raw_json FROM sessions WHERE (cwd = ? OR cwd LIKE ? ESCAPE '\\') ORDER BY created DESC",
                (project_path, escaped + "/%"),
            ).fetchall()
        return [json.loads(r["raw_json"]) for r in rows]

    def get_project_cost(self, project_path: str) -> dict:
        """Get aggregated token usage and cost for sessions in a project."""
        project_path = project_path.replace("\\", "/")
        escaped = _escape_like(project_path)
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(input_tokens), 0) AS input_tokens, "
                "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
                "COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens, "
                "COALESCE(SUM(cache_creation_tokens), 0) AS cache_creation_tokens, "
                "COALESCE(SUM(estimated_cost), 0) AS estimated_cost "
                "FROM sessions WHERE (cwd = ? OR cwd LIKE ? ESCAPE '\\')",
                (project_path, escaped + "/%"),
            ).fetchone()
        return (
            dict(row)
            if row
            else {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "estimated_cost": 0,
            }
        )


# ---------------------------------------------------------------------------
# Background cache builder
# ---------------------------------------------------------------------------


def build_cache(
    cache: CacheDB,
    copilot_path: Path,
    claude_path: Path,
    vscode_path: Path,
    desktop_path: Path | None = None,
) -> None:
    """Scan all sources and populate the cache database.

    Intended to be called in a background thread.
    """
    from .config_readers import read_all_configs
    from .config_readers.claude_config import read_claude_desktop_config, read_claude_projects

    try:
        cache.set_meta("status", "building")
        cache._clear_all()

        # -- Sessions -------------------------------------------------------
        from .claude_parser import discover_sessions as claude_discover
        from .parser import discover_sessions as copilot_discover
        from .vscode_parser import discover_all_vscode_sessions

        copilot_sessions = copilot_discover(copilot_path)
        for s in copilot_sessions:
            s.setdefault("source", "copilot")

        claude_sessions = claude_discover(claude_path)
        vscode_sessions = discover_all_vscode_sessions(vscode_path)

        all_sessions = copilot_sessions + claude_sessions + vscode_sessions
        all_sessions.sort(key=lambda s: s.get("created_at", ""), reverse=True)
        cache.insert_sessions(all_sessions)

        # -- Tool configs ---------------------------------------------------
        configs = read_all_configs()
        for tool, cfg in configs.items():
            cache.insert_tool_config(tool, cfg)

        # Claude Desktop config
        desktop_cfg = read_claude_desktop_config(desktop_dir=desktop_path)
        cache.insert_tool_config("claude_desktop", desktop_cfg)

        # -- Claude projects ------------------------------------------------
        # claude_path points to ~/.claude/projects/ — parent is ~/.claude/
        claude_home = claude_path.parent
        project_data = read_claude_projects(claude_home)
        cache.insert_projects(project_data["projects"])

        # Memory files
        memory_items = []
        for p in project_data["projects"]:
            for mf in p.get("memory_files", []):
                memory_items.append(
                    {
                        "project_encoded_name": p["encoded_name"],
                        "filename": mf["filename"],
                        "content": mf["content"],
                    }
                )
        if memory_items:
            cache.insert_project_memory(memory_items)

        cache.set_meta("status", "ready")
        cache.set_meta("built_at", _now_iso())

    except Exception:
        cache.set_meta("status", "error")
        raise


def start_background_build(
    cache: CacheDB,
    copilot_path: Path,
    claude_path: Path,
    vscode_path: Path,
    desktop_path: Path | None = None,
) -> threading.Thread:
    """Start cache build in a daemon thread. Returns the thread."""
    # Set status synchronously to prevent double-start races
    cache.set_meta("status", "building")
    t = threading.Thread(
        target=build_cache,
        args=(cache, copilot_path, claude_path, vscode_path),
        kwargs={"desktop_path": desktop_path},
        daemon=True,
        name="cache-builder",
    )
    t.start()
    return t
