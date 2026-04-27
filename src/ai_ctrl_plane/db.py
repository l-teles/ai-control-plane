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


_FTS_PUNCT_RE = None  # lazy-init below


def _sanitise_fts_query(q: str) -> str:
    """Make a free-form user query safe to pass to FTS5 ``MATCH``.

    Removes characters that FTS5 treats as operators or syntax (``-`` as
    NOT, ``:`` for column filters, parens, quotes, etc.) so that typing
    ``kernel-bypass`` or ``foo()`` returns the obvious matches instead
    of confusing FTS errors or NOT semantics.

    Multi-word queries become an implicit AND (FTS5 default).
    """
    global _FTS_PUNCT_RE
    if _FTS_PUNCT_RE is None:
        import re as _re

        _FTS_PUNCT_RE = _re.compile(r'[\-"():*+!,;<>=\[\]{}|?@#$%^&\\/`~]')
    cleaned = _FTS_PUNCT_RE.sub(" ", q)
    cleaned = " ".join(cleaned.split())  # collapse whitespace
    return cleaned.strip()


def _extract_session_content(s: dict) -> str:
    """Pull the full conversation text for *s* via its source parser.

    Used to populate the FTS ``content`` column.  Returns ``""`` if the
    session lacks a ``source_path`` (older cached entries) or the file
    is unreadable — search will still find it by metadata in that case.
    """
    source = s.get("source", "")
    path_str = s.get("source_path", "")
    if not path_str:
        return ""
    p = Path(path_str)
    try:
        if source == "claude":
            from .claude_parser import extract_searchable_text as _claude_extract

            return _claude_extract(p)
        if source == "copilot":
            # Copilot anchor is ``<dir>/events.jsonl``; extractor wants the dir.
            from .parser import extract_searchable_text as _copilot_extract

            return _copilot_extract(p.parent)
        if source == "vscode":
            from .vscode_parser import extract_searchable_text as _vscode_extract

            return _vscode_extract(p)
    except Exception:
        # Best-effort indexing — never let a malformed session break the
        # whole cache build.
        return ""
    return ""


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
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO cache_meta (key, value) VALUES (?, ?)",
                    (key, value),
                )
                self._conn.commit()
            except sqlite3.ProgrammingError:
                # DB was closed while a background refresh / build was still
                # running (typical in tests at teardown). Status writes are
                # best-effort — drop the write and let the thread exit.
                pass

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
        """Highest applied migration version (e.g. ``"002"``).

        Sorts numerically (``CAST(version AS INTEGER)``) rather than
        lexically — the column is TEXT but holds numeric strings, and
        once a future migration crosses a digit-width boundary
        (``"1000"`` vs ``"999"``) lexical ordering would lie.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT version FROM schema_migrations "
                "ORDER BY CAST(version AS INTEGER) DESC LIMIT 1"
            ).fetchone()
        return row["version"] if row else ""

    # -- Bulk write (used during cache build) --------------------------------

    def _clear_all(self) -> None:
        with self._lock:
            for tbl in ("project_memory", "sessions", "projects", "tool_configs"):
                self._conn.execute(f"DELETE FROM {tbl}")  # noqa: S608
            self._conn.execute("DELETE FROM sessions_fts")
            self._conn.commit()

    def _index_sessions_fts(self, sessions: list[dict], content_by_id: dict[str, str]) -> None:
        """Insert/replace each session in the FTS index.

        ``content_by_id`` maps ``source:id`` to the pre-extracted
        searchable text for each session — caller is responsible for
        running ``_extract_session_content`` outside the DB lock so
        concurrent route reads aren't blocked by file I/O during a build
        or refresh. Uses ``DELETE`` + ``INSERT`` rather than ``INSERT
        OR REPLACE`` because FTS5 virtual tables don't support replace
        semantics.
        """
        ids = [f"{s['source']}:{s['id']}" for s in sessions]
        self._conn.executemany("DELETE FROM sessions_fts WHERE session_id = ?", [(i,) for i in ids])

        rows = []
        for s in sessions:
            full_id = f"{s['source']}:{s['id']}"
            rows.append(
                (
                    full_id,
                    s.get("summary", "") or "",
                    s.get("cwd", "") or "",
                    s.get("model", "") or "",
                    s.get("first_user_content", "") or "",
                    content_by_id.get(full_id, ""),
                )
            )
        self._conn.executemany(
            "INSERT INTO sessions_fts "
            "(session_id, summary, cwd, model, first_user_message, content) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )

    def search_sessions(self, query: str, *, limit: int = 50) -> list[dict]:
        """Run a full-text search against indexed sessions.

        Returns full session rows (raw_json decoded) ranked by FTS5
        relevance.  The query is sanitised to strip FTS5-meaningful
        punctuation (``-`` parses as NOT, ``:`` for column filters, etc.)
        so the user's typed text behaves like a plain word search.
        Power users wanting raw FTS syntax can use the lower-level
        ``_conn`` directly.

        An empty query returns an empty list.

        Implementation notes
        --------------------
        Ranking uses FTS5's standard ``rank`` virtual column (BM25-derived,
        stable since SQLite 3.20 / 2017; Python 3.13 ships ≥ 3.43).  The
        ``except OperationalError`` branch below is a defensive fallback
        for genuinely malformed queries that slip past the sanitiser; it
        only matches against ``summary`` and ``cwd`` (not the conversation
        body), so search quality degrades there.  In practice almost
        every user query takes the FTS path.
        """
        cleaned = _sanitise_fts_query(query or "")
        if not cleaned:
            return []
        with self._lock:
            try:
                # Reference the FTS table by its real name throughout —
                # FTS5's ``MATCH`` operator only resolves the LHS against
                # the underlying virtual-table identifier (an alias on
                # the JOIN raises "no such column"), so ``sessions_fts``
                # appears on both the MATCH and the rank column for
                # consistency.
                rows = self._conn.execute(
                    "SELECT s.raw_json FROM sessions s "
                    "JOIN sessions_fts ON s.id = sessions_fts.session_id "
                    "WHERE sessions_fts MATCH ? "
                    "ORDER BY sessions_fts.rank LIMIT ?",
                    (cleaned, limit),
                ).fetchall()
                return [json.loads(r["raw_json"]) for r in rows]
            except sqlite3.OperationalError:
                # Malformed FTS query (e.g. bare quote) — fall back to a
                # word-level LIKE search so the caller still gets *some*
                # answer rather than a 500.  One static query per word,
                # results unioned in Python (avoids dynamic SQL).
                import re as _re

                words = _re.findall(r"\w+", query)
                if not words:
                    return []
                results: list[dict] = []
                seen_ids: set[str] = set()
                for w in words:
                    pat = "%" + _escape_like(w) + "%"
                    rows = self._conn.execute(
                        "SELECT id, raw_json FROM sessions "
                        "WHERE summary LIKE ? ESCAPE '\\' OR cwd LIKE ? ESCAPE '\\' "
                        "ORDER BY created DESC LIMIT ?",
                        (pat, pat, limit),
                    ).fetchall()
                    for r in rows:
                        if r["id"] in seen_ids:
                            continue
                        seen_ids.add(r["id"])
                        results.append(json.loads(r["raw_json"]))
                        if len(results) >= limit:
                            return results
                return results

    def insert_sessions(self, sessions: list[dict]) -> None:
        # Compute the per-session FTS content (file I/O + parsing) BEFORE
        # acquiring the DB lock, so concurrent route reads aren't blocked
        # while we read up to N session files from disk during a refresh
        # or full build. The lock then only wraps the DELETE/INSERT pair.
        content_by_id = {
            f"{s['source']}:{s['id']}": _extract_session_content(s) for s in sessions
        }

        def _str(value: object, default: str = "") -> str:
            """Coerce defensively — discover_sessions normalises most
            fields, but a malformed cached row or test fixture could put
            a non-string in any of these slots and crash ``.replace`` /
            SQLite's adapter."""
            return value if isinstance(value, str) else default

        def _num(value: object, default: float = 0) -> float | int:
            return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default

        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO sessions "
                "(id, source, uuid, summary, created, cwd, model, "
                "input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, "
                "estimated_cost, source_path, source_mtime, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        f"{s['source']}:{s['id']}",
                        _str(s.get("source")),
                        _str(s.get("id")),
                        _str(s.get("summary")),
                        _str(s.get("created_at")),
                        _str(s.get("cwd")).replace("\\", "/"),
                        _str(s.get("model")),
                        _num(s.get("input_tokens"), 0),
                        _num(s.get("output_tokens"), 0),
                        _num(s.get("cache_read_tokens"), 0),
                        _num(s.get("cache_creation_tokens"), 0),
                        _num(s.get("estimated_cost"), 0),
                        _str(s.get("source_path")),
                        _num(s.get("source_mtime"), 0.0),
                        json.dumps(s, default=str),
                    )
                    for s in sessions
                ],
            )
            self._index_sessions_fts(sessions, content_by_id)
            self._conn.commit()

    def get_session_anchors(self) -> dict[str, tuple[str, float]]:
        """Return ``{source_path: (session_id, mtime)}`` for every cached session.

        Used by the incremental refresh to skip re-parsing files whose mtime
        hasn't advanced.  Rows with a NULL ``source_path`` (legacy entries
        from caches built before migration 002) aren't included here — see
        :meth:`get_all_session_ids` for the gone-detection diff that does
        catch them.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, source_path, source_mtime FROM sessions WHERE source_path IS NOT NULL"
            ).fetchall()
        return {r["source_path"]: (r["id"], r["source_mtime"] or 0.0) for r in rows if r["source_path"]}

    def get_all_session_ids(self) -> set[str]:
        """Return every cached session's full ``source:uuid`` id.

        Used alongside :meth:`get_session_anchors` so refresh can drop
        legacy rows (NULL ``source_path``) whose source files have been
        deleted on disk — those rows would otherwise persist forever
        because the path-based gone-detection can't see them.
        """
        with self._lock:
            rows = self._conn.execute("SELECT id FROM sessions").fetchall()
        return {r["id"] for r in rows}

    def reindex_missing_fts(self) -> int:
        """Rebuild FTS rows for any session that exists in ``sessions``
        but is missing from ``sessions_fts``.

        Called by :func:`refresh_cache` so a migration that recreates
        ``sessions_fts`` (e.g. 004 dropping/re-creating it for the new
        ``content`` column) doesn't leave the index half-populated:
        without this, refresh would only re-index sessions whose source
        file's mtime had advanced, and unchanged sessions would silently
        disappear from search until the user touched the file.

        Reads each missing session's metadata from the canonical
        ``sessions`` columns (not ``raw_json``, which may be empty for
        legacy rows) and supplements it with the on-disk content via
        ``source_path``.  Returns the number of sessions re-indexed.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, source, uuid, summary, cwd, model, source_path, raw_json FROM sessions s "
                "WHERE NOT EXISTS (SELECT 1 FROM sessions_fts f WHERE f.session_id = s.id)"
            ).fetchall()
        if not rows:
            return 0
        rebuilt: list[dict] = []
        for r in rows:
            try:
                raw = json.loads(r["raw_json"]) if r["raw_json"] else {}
            except (json.JSONDecodeError, TypeError):
                raw = {}
            # Prefer values from raw_json when present (it has the richest
            # set of fields, including the per-source ``first_user_content``
            # used for the metadata FTS column) but fall back to the
            # canonical columns so legacy rows still get indexed.
            session: dict = dict(raw) if isinstance(raw, dict) else {}
            session.setdefault("source", r["source"] or "")
            # Fallback id: strip the ``source:`` prefix from the row id
            # since the column ``uuid`` may itself be missing on very old rows.
            row_id = r["id"] or ""
            session.setdefault("id", r["uuid"] or (row_id.split(":", 1)[-1] if ":" in row_id else row_id))
            session.setdefault("summary", r["summary"] or "")
            session.setdefault("cwd", r["cwd"] or "")
            session.setdefault("model", r["model"] or "")
            if r["source_path"]:
                session.setdefault("source_path", r["source_path"])
            # Skip rows that lack the minimum identity fields — they can't
            # produce a stable FTS key and will be picked up by the FS
            # scan downstream if their source files still exist.
            if not session.get("source") or not session.get("id"):
                continue
            rebuilt.append(session)
        if not rebuilt:
            return 0
        # Compute content outside the lock — see ``insert_sessions``
        # for the same reasoning.
        content_by_id = {
            f"{s['source']}:{s['id']}": _extract_session_content(s) for s in rebuilt
        }
        with self._lock:
            self._index_sessions_fts(rebuilt, content_by_id)
            self._conn.commit()
        return len(rebuilt)

    def delete_sessions(self, ids: list[str]) -> None:
        if not ids:
            return
        with self._lock:
            self._conn.executemany("DELETE FROM sessions WHERE id = ?", [(sid,) for sid in ids])
            self._conn.executemany("DELETE FROM sessions_fts WHERE session_id = ?", [(sid,) for sid in ids])
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


def _safe_thread_target(fn, *args, **kwargs) -> None:
    """Wrap a background-thread target so a closed-DB at process / test
    teardown doesn't raise on stderr — the thread just exits."""
    try:
        fn(*args, **kwargs)
    except sqlite3.ProgrammingError:
        # DB closed underneath us (typical in tests). Status writes already
        # swallow this; the build path may still hit it via other queries.
        pass


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
        target=_safe_thread_target,
        args=(build_cache, cache, copilot_path, claude_path, vscode_path),
        kwargs={"desktop_path": desktop_path},
        daemon=True,
        name="cache-builder",
    )
    t.start()
    return t


# ---------------------------------------------------------------------------
# Incremental refresh
# ---------------------------------------------------------------------------


def refresh_cache(
    cache: CacheDB,
    copilot_path: Path,
    claude_path: Path,
    vscode_path: Path,
    desktop_path: Path | None = None,
) -> dict[str, int]:
    """Update the cache without wiping it.

    Walks each source's filesystem, compares each session's source-file mtime
    against the cached value, and only re-parses sessions that are new or
    modified. Sessions whose source file no longer exists are dropped.

    Tool configs and project memory are always re-read (cheap relative to
    session parsing).

    Returns ``{"added": int, "updated": int, "removed": int, "unchanged": int}``.
    """
    from .claude_parser import discover_sessions as claude_discover
    from .config_readers import read_all_configs
    from .config_readers.claude_config import read_claude_desktop_config, read_claude_projects
    from .parser import discover_sessions as copilot_discover
    from .vscode_parser import discover_all_vscode_sessions

    counts = {"added": 0, "updated": 0, "removed": 0, "unchanged": 0}

    try:
        cache.set_meta("status", "refreshing")

        # If a migration just recreated ``sessions_fts`` (e.g. 004 added the
        # ``content`` column by drop-and-recreate), unchanged sessions would
        # otherwise stay invisible to search until their files were touched.
        # Backfill any missing FTS rows up front using whatever's already in
        # the canonical ``sessions`` table.
        cache.reindex_missing_fts()

        # Path-based map: lets us skip re-parsing unchanged files (mtime check).
        cached = cache.get_session_anchors()  # {source_path: (id, mtime)}
        # Id-based set: lets us drop sessions whose source files no longer
        # exist, including legacy rows (pre-migration-002) that have a
        # NULL source_path and therefore aren't in the path map above.
        cached_ids = cache.get_all_session_ids()

        copilot_sessions = copilot_discover(copilot_path)
        for s in copilot_sessions:
            s.setdefault("source", "copilot")
        claude_sessions = claude_discover(claude_path)
        vscode_sessions = discover_all_vscode_sessions(vscode_path)
        fs_sessions = copilot_sessions + claude_sessions + vscode_sessions

        to_upsert: list[dict] = []
        fs_ids: set[str] = set()
        for s in fs_sessions:
            full_id = f"{s['source']}:{s['id']}"
            fs_ids.add(full_id)
            sp = s.get("source_path", "")
            if not sp:
                # Parser didn't supply an anchor — fall back to a full upsert
                # so the session still ends up in the cache.
                to_upsert.append(s)
                counts["added"] += 1
                continue
            current = cached.get(sp)
            # Parsers should always supply a numeric ``source_mtime``,
            # but a buggy / partial session dict could provide a string
            # or ``None`` and crash ``float()``. Treat anything we can't
            # coerce as 0 — the comparison below will then look like a
            # change and trigger a re-upsert, which is the safe default.
            # Exclude ``bool`` explicitly: it's a subclass of ``int``, so
            # ``True`` would otherwise sneak through as a 1.0 mtime.
            raw_mtime = s.get("source_mtime", 0.0)
            if isinstance(raw_mtime, bool) or not isinstance(raw_mtime, int | float | str):
                new_mtime = 0.0
            else:
                try:
                    new_mtime = float(raw_mtime)
                except (TypeError, ValueError):
                    new_mtime = 0.0
            if current is None:
                # New file OR a legacy NULL-anchor row whose id is now
                # being upgraded to a proper anchor.  ``insert_sessions``
                # uses INSERT OR REPLACE, so the row is upgraded in place.
                to_upsert.append(s)
                counts["added"] += 1
            elif new_mtime != current[1]:
                # Use ``!=`` rather than ``>`` so a file restored from
                # backup, checked out from VCS, or edited on a filesystem
                # with coarse/skewed mtime resolution is still detected
                # as changed (its mtime can equal or even predate the
                # cached value).
                to_upsert.append(s)
                counts["updated"] += 1
            else:
                counts["unchanged"] += 1

        if to_upsert:
            cache.insert_sessions(to_upsert)

        # Id-based gone detection — drops both modern path-tracked rows
        # and legacy NULL-anchor rows whose underlying files are gone.
        gone_ids = cached_ids - fs_ids
        if gone_ids:
            cache.delete_sessions(list(gone_ids))
            counts["removed"] = len(gone_ids)

        # Tool configs and project metadata are cheap; refresh them every time.
        configs = read_all_configs()
        for tool, cfg in configs.items():
            cache.insert_tool_config(tool, cfg)
        cache.insert_tool_config("claude_desktop", read_claude_desktop_config(desktop_dir=desktop_path))

        claude_home = claude_path.parent
        project_data = read_claude_projects(claude_home)
        # Wipe + re-insert projects/memory — small data volume, simpler than diffing.
        with cache._lock:
            cache._conn.execute("DELETE FROM project_memory")
            cache._conn.execute("DELETE FROM projects")
            cache._conn.commit()
        cache.insert_projects(project_data["projects"])
        memory_items = [
            {
                "project_encoded_name": p["encoded_name"],
                "filename": mf["filename"],
                "content": mf["content"],
            }
            for p in project_data["projects"]
            for mf in p.get("memory_files", [])
        ]
        if memory_items:
            cache.insert_project_memory(memory_items)

        # Cooperative cancellation: if a manual rebuild flipped the
        # status to "building" while we were running, don't clobber its
        # state with our "ready" write — let the build's own completion
        # write the final status. The refresh's writes that already
        # landed will be wiped by the build's ``_clear_all`` and rebuilt
        # from scratch, so no data loss either way.
        if cache.status == "refreshing":
            cache.set_meta("status", "ready")
            cache.set_meta("built_at", _now_iso())
        return counts

    except Exception:
        # Same caveat for the error branch: don't override "building" /
        # "ready" set by a concurrent caller.
        if cache.status == "refreshing":
            cache.set_meta("status", "error")
        raise


def start_background_refresh(
    cache: CacheDB,
    copilot_path: Path,
    claude_path: Path,
    vscode_path: Path,
    desktop_path: Path | None = None,
) -> threading.Thread:
    """Start incremental refresh in a daemon thread."""
    cache.set_meta("status", "refreshing")
    t = threading.Thread(
        target=_safe_thread_target,
        args=(refresh_cache, cache, copilot_path, claude_path, vscode_path),
        kwargs={"desktop_path": desktop_path},
        daemon=True,
        name="cache-refresher",
    )
    t.start()
    return t
