"""Tests for the SQLite cache layer."""

from __future__ import annotations

from pathlib import Path

from ai_ctrl_plane.db import CacheDB


def test_schema_creates_tables(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "test.db")
    tables = [
        r[0] for r in db._conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    ]
    assert "cache_meta" in tables
    assert "sessions" in tables
    assert "projects" in tables
    assert "project_memory" in tables
    assert "tool_configs" in tables
    db.close()


def test_cache_status_lifecycle(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "test.db")
    assert db.status == "empty"
    db.set_meta("status", "building")
    assert db.status == "building"
    db.set_meta("status", "ready")
    assert db.status == "ready"
    db.close()


def test_insert_and_get_sessions(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "test.db")
    sessions = [
        {
            "source": "claude",
            "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "summary": "Test session",
            "created_at": "2026-03-12T10:00:00Z",
            "cwd": "/tmp/proj",
        },
    ]
    db.insert_sessions(sessions)
    result = db.get_sessions()
    assert len(result) == 1
    assert result[0]["summary"] == "Test session"
    assert result[0]["source"] == "claude"
    db.close()


def test_insert_and_get_projects(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "test.db")
    projects = [
        {
            "encoded_name": "-Users-test-project",
            "path": "/Users/test/project",
            "name": "project",
            "session_count": 5,
            "memory_file_count": 2,
            "last_cost": 1.50,
            "last_session_id": "abc-123",
            "last_input_tokens": 1000,
            "last_output_tokens": 500,
            "has_trust_accepted": True,
            "onboarding_seen_count": 3,
            "metadata": {"allowedTools": ["Read", "Write"]},
        },
    ]
    db.insert_projects(projects)
    result = db.get_projects()
    assert len(result) == 1
    assert result[0]["name"] == "project"
    assert result[0]["has_trust_accepted"] is True
    assert result[0]["metadata"]["allowedTools"] == ["Read", "Write"]

    single = db.get_project("-Users-test-project")
    assert single is not None
    assert single["path"] == "/Users/test/project"
    db.close()


def test_get_project_returns_none_for_missing(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "test.db")
    assert db.get_project("nonexistent") is None
    db.close()


def test_insert_and_get_project_memory(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "test.db")
    db.insert_projects([{"encoded_name": "-proj", "path": "/proj", "name": "proj", "metadata": {}}])
    db.insert_project_memory(
        [
            {"project_encoded_name": "-proj", "filename": "MEMORY.md", "content": "# Notes"},
        ]
    )
    result = db.get_project_memory("-proj")
    assert len(result) == 1
    assert result[0]["filename"] == "MEMORY.md"
    assert result[0]["content"] == "# Notes"
    db.close()


def test_insert_and_get_tool_config(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "test.db")
    db.insert_tool_config("claude_desktop", {"installed": True, "mcp_servers": []})
    result = db.get_tool_config("claude_desktop")
    assert result is not None
    assert result["installed"] is True
    db.close()


def test_get_tool_config_returns_none_for_missing(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "test.db")
    assert db.get_tool_config("nonexistent") is None
    db.close()


def test_get_all_tool_configs(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "test.db")
    db.insert_tool_config("claude", {"installed": True})
    db.insert_tool_config("copilot", {"installed": False})
    result = db.get_all_tool_configs()
    assert "claude" in result
    assert "copilot" in result
    db.close()


def test_project_global_stats(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "test.db")
    db.insert_projects(
        [
            {
                "encoded_name": "-p1",
                "path": "/p1",
                "name": "p1",
                "session_count": 3,
                "metadata": {},
            },
            {
                "encoded_name": "-p2",
                "path": "/p2",
                "name": "p2",
                "session_count": 7,
                "metadata": {},
            },
        ]
    )
    # Cost comes from sessions, not projects
    db.insert_sessions(
        [
            {"source": "claude", "id": "s1", "cwd": "/p1", "estimated_cost": 1.5},
            {"source": "claude", "id": "s2", "cwd": "/p2", "estimated_cost": 2.0},
        ]
    )
    db.insert_project_memory(
        [
            {"project_encoded_name": "-p1", "filename": "a.md", "content": "a"},
            {"project_encoded_name": "-p2", "filename": "b.md", "content": "b"},
        ]
    )
    stats = db.get_project_global_stats()
    assert stats["total_projects"] == 2
    assert stats["total_sessions"] == 2  # counts actual session rows, not project.session_count
    assert stats["aggregate_cost"] == 3.5
    assert stats["total_memory_files"] == 2
    db.close()


def test_get_project_sessions(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "test.db")
    sessions = [
        {
            "source": "claude",
            "id": "11111111-2222-3333-4444-555555555555",
            "summary": "In project",
            "created_at": "2026-03-12T10:00:00Z",
            "cwd": "/Users/test/project",
        },
        {
            "source": "claude",
            "id": "22222222-3333-4444-5555-666666666666",
            "summary": "Different project",
            "created_at": "2026-03-12T11:00:00Z",
            "cwd": "/Users/test/other",
        },
    ]
    db.insert_sessions(sessions)
    result = db.get_project_sessions("/Users/test/project")
    assert len(result) == 1
    assert result[0]["summary"] == "In project"
    db.close()


def test_cache_status_dict(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "test.db")
    status = db.cache_status()
    assert status["status"] == "empty"
    assert status["db_path"] == str(tmp_path / "test.db")
    assert "version" in status
    db.close()


def test_search_sessions_finds_by_summary_token(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "test.db")
    db.insert_sessions(
        [
            {
                "source": "claude",
                "id": "11111111-2222-3333-4444-555555555555",
                "summary": "Refactor authentication module to use JWT",
                "cwd": "/repo/auth",
                "model": "claude-opus-4-6",
                "first_user_content": "I want to refactor auth",
            },
            {
                "source": "copilot",
                "id": "22222222-3333-4444-5555-666666666666",
                "summary": "Add unit tests for parser",
                "cwd": "/repo/parser",
                "model": "gpt-4",
                "first_user_content": "Need tests",
            },
        ]
    )
    hits = db.search_sessions("authentication")
    assert len(hits) == 1
    assert hits[0]["summary"].startswith("Refactor authentication")
    db.close()


def test_search_sessions_returns_relevance_order(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "test.db")
    db.insert_sessions(
        [
            {
                "source": "claude",
                "id": "aaaaaaaa-1111-2222-3333-444444444444",
                "summary": "auth auth auth tokens",
                "cwd": "",
                "first_user_content": "",
            },
            {
                "source": "claude",
                "id": "bbbbbbbb-1111-2222-3333-444444444444",
                "summary": "mention of auth, but mostly about parsing",
                "cwd": "",
                "first_user_content": "",
            },
        ]
    )
    hits = db.search_sessions("auth")
    # Both match; the one with denser term frequency should rank first.
    assert len(hits) == 2
    assert hits[0]["summary"].startswith("auth auth auth")


def test_search_sessions_empty_query_returns_empty(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "test.db")
    db.insert_sessions(
        [{"source": "claude", "id": "x-1-2-3-4", "summary": "x", "cwd": "", "first_user_content": ""}]
    )
    assert db.search_sessions("") == []
    assert db.search_sessions("   ") == []
    db.close()


def test_search_sessions_falls_back_on_invalid_fts_query(tmp_path: Path) -> None:
    """A query that fails FTS5 parsing (e.g. a stray quote) should still
    return results via a LIKE fallback, not raise."""
    db = CacheDB(tmp_path / "test.db")
    db.insert_sessions(
        [
            {
                "source": "claude",
                "id": "c-1-2-3-4",
                "summary": "Working with regex patterns",
                "cwd": "",
                "first_user_content": "",
            },
        ]
    )
    hits = db.search_sessions('regex "')
    assert any("regex" in h["summary"] for h in hits)
    db.close()


def test_search_sessions_finds_match_in_conversation_body(tmp_path: Path) -> None:
    """A word that only appears deep in the conversation — not in summary,
    cwd, model, or first user message — should still be findable."""
    import json as _json

    project_dir = tmp_path / "claude_logs" / "-Users-test-project"
    project_dir.mkdir(parents=True)
    sid = "11111111-2222-3333-4444-555555555555"
    jsonl = project_dir / f"{sid}.jsonl"
    events = [
        {
            "type": "user",
            "uuid": "u1",
            "sessionId": sid,
            "timestamp": "2026-04-26T10:00:00Z",
            "cwd": "/repo",
            "version": "2.1",
            "gitBranch": "main",
            "message": {"role": "user", "content": "general greeting"},
        },
        {
            "type": "assistant",
            "uuid": "a1",
            "requestId": "r1",
            "sessionId": sid,
            "timestamp": "2026-04-26T10:00:01Z",
            "cwd": "/repo",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Discussing kernel-bypass networking via DPDK"}
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        },
    ]
    with open(jsonl, "w", encoding="utf-8") as fh:
        for e in events:
            fh.write(_json.dumps(e) + "\n")

    db = CacheDB(tmp_path / "cache.db")
    db.insert_sessions(
        [
            {
                "source": "claude",
                "id": sid,
                "summary": "general greeting",  # deliberately NOT containing the term we'll search for
                "cwd": "/repo",
                "model": "",
                "first_user_content": "general greeting",
                "source_path": str(jsonl),
                "source_mtime": jsonl.stat().st_mtime,
                "created_at": "2026-04-26T10:00:00Z",
            }
        ]
    )

    # Term appears only in the assistant body — must still be searchable.
    hits = db.search_sessions("DPDK")
    assert len(hits) == 1
    assert hits[0]["id"] == sid

    # Multi-token query (FTS implicit AND) on body terms
    hits = db.search_sessions("kernel bypass")
    assert len(hits) == 1
    db.close()


def test_search_sessions_indexes_tool_result_text(tmp_path: Path) -> None:
    """Tool results carry a lot of useful searchable text (file contents,
    Bash output, etc.) — the FTS index should cover them."""
    import json as _json

    project_dir = tmp_path / "claude_logs" / "-Users-test-project"
    project_dir.mkdir(parents=True)
    sid = "22222222-3333-4444-5555-666666666666"
    jsonl = project_dir / f"{sid}.jsonl"
    events = [
        {
            "type": "user",
            "uuid": "u1",
            "sessionId": sid,
            "timestamp": "2026-04-26T10:00:00Z",
            "message": {"role": "user", "content": "list files"},
        },
        {
            "type": "user",
            "uuid": "u2",
            "sessionId": sid,
            "timestamp": "2026-04-26T10:00:01Z",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_1",
                        "content": "needle_token_xyz appears here",
                    }
                ],
            },
        },
    ]
    with open(jsonl, "w", encoding="utf-8") as fh:
        for e in events:
            fh.write(_json.dumps(e) + "\n")

    db = CacheDB(tmp_path / "cache.db")
    db.insert_sessions(
        [
            {
                "source": "claude",
                "id": sid,
                "summary": "list files",
                "cwd": "/r",
                "first_user_content": "list files",
                "source_path": str(jsonl),
                "source_mtime": jsonl.stat().st_mtime,
                "created_at": "2026-04-26T10:00:00Z",
            }
        ]
    )
    hits = db.search_sessions("needle_token_xyz")
    assert len(hits) == 1
    db.close()


def test_delete_session_removes_from_fts(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "test.db")
    db.insert_sessions(
        [{"source": "claude", "id": "d-1-2-3-4", "summary": "deletable", "cwd": "", "first_user_content": ""}]
    )
    assert db.search_sessions("deletable")
    db.delete_sessions(["claude:d-1-2-3-4"])
    assert db.search_sessions("deletable") == []
    db.close()


def test_clear_all(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "test.db")
    db.insert_sessions([{"source": "claude", "id": "a-b-c-d-e", "summary": "x", "created_at": "", "cwd": ""}])
    assert len(db.get_sessions()) == 1
    db._clear_all()
    assert len(db.get_sessions()) == 0
    db.close()
