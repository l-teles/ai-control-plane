"""Tests for incremental cache refresh."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ai_ctrl_plane.db import CacheDB, build_cache, refresh_cache


def _make_copilot_session(base: Path, session_id: str, summary: str = "test") -> Path:
    """Create a minimal Copilot session directory and return its path."""
    sdir = base / session_id
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "workspace.yaml").write_text(
        f"summary: {summary}\nrepository: test/repo\nbranch: main\ncwd: /tmp/proj\n"
        "created_at: '2026-04-26T10:00:00Z'\n"
        "updated_at: '2026-04-26T10:01:00Z'\n",
        encoding="utf-8",
    )
    (sdir / "events.jsonl").write_text(
        json.dumps({"type": "session.start", "timestamp": "2026-04-26T10:00:00Z", "data": {}}) + "\n",
        encoding="utf-8",
    )
    return sdir


def test_refresh_picks_up_new_session(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "cache.db")
    copilot_dir = tmp_path / "copilot"
    claude_dir = tmp_path / "claude"
    vscode_dir = tmp_path / "vscode"
    for d in (copilot_dir, claude_dir, vscode_dir):
        d.mkdir()

    _make_copilot_session(copilot_dir, "aaaaaaaa-1111-2222-3333-444444444444")
    build_cache(db, copilot_dir, claude_dir, vscode_dir)
    assert len(db.get_sessions()) == 1

    _make_copilot_session(copilot_dir, "bbbbbbbb-1111-2222-3333-555555555555")
    counts = refresh_cache(db, copilot_dir, claude_dir, vscode_dir)

    assert counts["added"] == 1
    assert counts["updated"] == 0
    assert counts["unchanged"] == 1
    assert counts["removed"] == 0
    assert len(db.get_sessions()) == 2
    db.close()


def test_refresh_detects_modified_session(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "cache.db")
    copilot_dir = tmp_path / "copilot"
    claude_dir = tmp_path / "claude"
    vscode_dir = tmp_path / "vscode"
    for d in (copilot_dir, claude_dir, vscode_dir):
        d.mkdir()

    sdir = _make_copilot_session(copilot_dir, "aaaaaaaa-1111-2222-3333-444444444444")
    build_cache(db, copilot_dir, claude_dir, vscode_dir)

    # Bump mtime forward so the refresh sees a change. Filesystem mtime
    # resolution is sometimes coarse (1 s on HFS+), so just set it explicitly.
    events = sdir / "events.jsonl"
    events.write_text(
        events.read_text(encoding="utf-8") + json.dumps(
            {"type": "user.message", "timestamp": "2026-04-26T10:02:00Z", "data": {"content": "hi"}}
        ) + "\n",
        encoding="utf-8",
    )
    future = time.time() + 60
    os.utime(events, (future, future))

    counts = refresh_cache(db, copilot_dir, claude_dir, vscode_dir)
    assert counts["updated"] == 1
    assert counts["unchanged"] == 0
    db.close()


def test_refresh_removes_deleted_session(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "cache.db")
    copilot_dir = tmp_path / "copilot"
    claude_dir = tmp_path / "claude"
    vscode_dir = tmp_path / "vscode"
    for d in (copilot_dir, claude_dir, vscode_dir):
        d.mkdir()

    sid = "aaaaaaaa-1111-2222-3333-444444444444"
    sdir = _make_copilot_session(copilot_dir, sid)
    build_cache(db, copilot_dir, claude_dir, vscode_dir)
    assert len(db.get_sessions()) == 1

    # Simulate the user deleting the session directory.
    for f in sdir.iterdir():
        f.unlink()
    sdir.rmdir()

    counts = refresh_cache(db, copilot_dir, claude_dir, vscode_dir)
    assert counts["removed"] == 1
    assert len(db.get_sessions()) == 0
    db.close()


def test_refresh_leaves_unchanged_sessions_alone(tmp_path: Path) -> None:
    """Touch nothing; refresh should report 0 added/updated/removed."""
    db = CacheDB(tmp_path / "cache.db")
    copilot_dir = tmp_path / "copilot"
    claude_dir = tmp_path / "claude"
    vscode_dir = tmp_path / "vscode"
    for d in (copilot_dir, claude_dir, vscode_dir):
        d.mkdir()

    _make_copilot_session(copilot_dir, "aaaaaaaa-1111-2222-3333-444444444444")
    _make_copilot_session(copilot_dir, "bbbbbbbb-1111-2222-3333-555555555555")
    build_cache(db, copilot_dir, claude_dir, vscode_dir)

    counts = refresh_cache(db, copilot_dir, claude_dir, vscode_dir)
    assert counts == {"added": 0, "updated": 0, "removed": 0, "unchanged": 2}
    db.close()


def test_refresh_sets_status_back_to_ready(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "cache.db")
    copilot_dir = tmp_path / "copilot"
    claude_dir = tmp_path / "claude"
    vscode_dir = tmp_path / "vscode"
    for d in (copilot_dir, claude_dir, vscode_dir):
        d.mkdir()

    refresh_cache(db, copilot_dir, claude_dir, vscode_dir)
    assert db.status == "ready"
    db.close()


def test_get_session_anchors_round_trip(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / "cache.db")
    db.insert_sessions(
        [
            {
                "source": "claude",
                "id": "11111111-2222-3333-4444-555555555555",
                "source_path": "/tmp/foo.jsonl",
                "source_mtime": 12345.0,
                "summary": "x",
                "created_at": "",
                "cwd": "",
            },
            {
                "source": "copilot",
                "id": "22222222-3333-4444-5555-666666666666",
                "source_path": "/tmp/bar/events.jsonl",
                "source_mtime": 67890.0,
                "summary": "y",
                "created_at": "",
                "cwd": "",
            },
        ]
    )
    anchors = db.get_session_anchors()
    assert anchors["/tmp/foo.jsonl"] == ("claude:11111111-2222-3333-4444-555555555555", 12345.0)
    assert anchors["/tmp/bar/events.jsonl"] == ("copilot:22222222-3333-4444-5555-666666666666", 67890.0)
    db.close()
