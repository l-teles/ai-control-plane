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


def test_refresh_detects_session_when_mtime_goes_backwards(tmp_path: Path) -> None:
    """If a file is restored from backup or checked out from VCS, its
    mtime can be older than the cached value but the content is still
    different. ``!=`` (rather than ``>``) catches that case. Regression
    for PR #27 review #35.

    Tests the *logic* (cached vs FS mtime delta) by bumping the cached
    mtime forward via SQL — using ``os.utime`` to set the file's mtime
    backwards isn't portable enough across CI filesystems (NTFS on the
    Windows runner doesn't always honour the rewind in pytest tempdirs).
    """
    import time

    db = CacheDB(tmp_path / "cache.db")
    copilot_dir = tmp_path / "copilot"
    claude_dir = tmp_path / "claude"
    vscode_dir = tmp_path / "vscode"
    for d in (copilot_dir, claude_dir, vscode_dir):
        d.mkdir()

    _make_copilot_session(copilot_dir, "aaaaaaaa-1111-2222-3333-444444444444")
    build_cache(db, copilot_dir, claude_dir, vscode_dir)

    # Force the cached mtime to be *ahead* of the FS mtime — exactly the
    # state that would arise from a backup restore or a VCS checkout
    # that moved the file's mtime backwards. The file on disk is
    # unchanged from build time, so under the old ``>`` semantics
    # refresh would treat it as unchanged. Under ``!=`` it correctly
    # re-parses.
    future = time.time() + 86400
    db._conn.execute("UPDATE sessions SET source_mtime = ?", (future,))
    db._conn.commit()

    counts = refresh_cache(db, copilot_dir, claude_dir, vscode_dir)
    assert counts["updated"] == 1, counts
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


def test_refresh_drops_legacy_null_source_path_session_when_file_gone(tmp_path: Path) -> None:
    """A session inserted before migration 002 (so source_path is NULL)
    whose underlying file no longer exists must still be removed by
    refresh_cache. Regression for PR #27 review comment 6."""
    db = CacheDB(tmp_path / "cache.db")
    copilot_dir = tmp_path / "copilot"
    claude_dir = tmp_path / "claude"
    vscode_dir = tmp_path / "vscode"
    for d in (copilot_dir, claude_dir, vscode_dir):
        d.mkdir()

    # Simulate a legacy row directly via SQL — bypass insert_sessions which
    # would populate source_path. This mirrors the state of a cache that
    # was first built under the old schema.
    sid = "claude:legacy-1111-2222-3333-444444444444"
    db._conn.execute(
        "INSERT INTO sessions (id, source, uuid, summary, created, raw_json, source_path, source_mtime) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)",
        (sid, "claude", "legacy-1111-2222-3333-444444444444", "old", "2026-01-01T00:00:00Z", "{}"),
    )
    db._conn.commit()
    assert sid in db.get_all_session_ids()
    # And it's invisible to the path-based map by design:
    assert db.get_session_anchors() == {}

    # Now refresh with empty filesystem — the legacy row should be dropped.
    counts = refresh_cache(db, copilot_dir, claude_dir, vscode_dir)
    assert counts["removed"] == 1
    assert sid not in db.get_all_session_ids()
    db.close()


def test_refresh_upgrades_legacy_null_source_path_row_in_place(tmp_path: Path) -> None:
    """When a legacy NULL-anchor row's id matches a file still on disk,
    the refresh should re-insert it with proper source_path/mtime
    populated rather than leaving it stuck without an anchor."""
    db = CacheDB(tmp_path / "cache.db")
    copilot_dir = tmp_path / "copilot"
    claude_dir = tmp_path / "claude"
    vscode_dir = tmp_path / "vscode"
    for d in (copilot_dir, claude_dir, vscode_dir):
        d.mkdir()

    # Make a real Copilot session on disk and pre-seed a legacy row with
    # the same id but NULL anchor.
    sid_uuid = "aaaaaaaa-1111-2222-3333-444444444444"
    _make_copilot_session(copilot_dir, sid_uuid)
    composite_id = f"copilot:{sid_uuid}"
    db._conn.execute(
        "INSERT INTO sessions (id, source, uuid, summary, created, raw_json, source_path, source_mtime) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)",
        (composite_id, "copilot", sid_uuid, "old", "2026-01-01T00:00:00Z", "{}"),
    )
    db._conn.commit()

    refresh_cache(db, copilot_dir, claude_dir, vscode_dir)

    # After refresh the row must have a real source_path / source_mtime.
    anchors = db.get_session_anchors()
    assert any(p.endswith("events.jsonl") for p in anchors)
    # And it's still the same session id (replaced in place, not duplicated).
    assert composite_id in db.get_all_session_ids()
    assert len([sid for sid in db.get_all_session_ids() if sid_uuid in sid]) == 1
    db.close()


def test_reindex_missing_fts_backfills_after_index_drop(tmp_path: Path) -> None:
    """Migration 004 drops sessions_fts. After it runs, the canonical
    sessions table still has rows but the FTS index is empty until each
    session file is touched. ``reindex_missing_fts`` should rebuild the
    missing rows from whatever's in the sessions table. Regression for
    PR #27 review comment 8."""
    db = CacheDB(tmp_path / "cache.db")
    copilot_dir = tmp_path / "copilot"
    claude_dir = tmp_path / "claude"
    vscode_dir = tmp_path / "vscode"
    for d in (copilot_dir, claude_dir, vscode_dir):
        d.mkdir()

    sdir = _make_copilot_session(copilot_dir, "aaaaaaaa-1111-2222-3333-444444444444", "indexed_keyword")
    build_cache(db, copilot_dir, claude_dir, vscode_dir)
    # Confirm the FTS index has the row to start with.
    assert db.search_sessions("indexed_keyword")

    # Simulate the post-migration state: drop and recreate sessions_fts
    # (mirroring what migration 004 does).
    db._conn.execute("DELETE FROM sessions_fts")
    db._conn.commit()
    assert db.search_sessions("indexed_keyword") == []

    # Touch nothing on disk; just call refresh — it should backfill via
    # reindex_missing_fts before doing anything else.
    refresh_cache(db, copilot_dir, claude_dir, vscode_dir)
    assert db.search_sessions("indexed_keyword")

    # Direct test of the helper too.
    db._conn.execute("DELETE FROM sessions_fts")
    db._conn.commit()
    n = db.reindex_missing_fts()
    assert n == 1
    assert db.search_sessions("indexed_keyword")
    db.close()
    assert sdir.exists()  # sanity


def test_vscode_source_mtime_tracks_workspace_json_too(tmp_path: Path) -> None:
    """If a VS Code session's sibling ``workspace.json`` is updated
    without touching the chat session file, refresh must still re-parse
    the session — otherwise cached ``cwd`` / ``repository`` go stale.
    Same parity fix as ``test_copilot_source_mtime_tracks_workspace_yaml_too``
    but for VS Code. Regression for PR #27 review #54."""
    import os
    import time

    db = CacheDB(tmp_path / "cache.db")
    copilot_dir = tmp_path / "copilot"
    claude_dir = tmp_path / "claude"
    vscode_dir = tmp_path / "vscode"
    for d in (copilot_dir, claude_dir, vscode_dir):
        d.mkdir()

    # Build a minimal VS Code session layout.
    ws_dir = vscode_dir / "workspaceStorage" / "abc123hash"
    chat_dir = ws_dir / "chatSessions"
    chat_dir.mkdir(parents=True)
    workspace_json = ws_dir / "workspace.json"
    workspace_json.write_text(
        json.dumps({"folder": "file:///Users/demo/proj"}),
        encoding="utf-8",
    )
    sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    session_file = chat_dir / f"{sid}.json"
    session_file.write_text(
        json.dumps(
            {
                "version": 3,
                "sessionId": sid,
                "creationDate": 1710237600000,
                "lastMessageDate": 1710237900000,
                "requests": [
                    {
                        "requestId": "r1",
                        "message": {"text": "hi"},
                        "response": [{"value": "hello"}],
                        "result": {"details": "Claude Sonnet 4"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    build_cache(db, copilot_dir, claude_dir, vscode_dir)
    assert any(s.get("source") == "vscode" for s in db.get_sessions())

    # Update ONLY workspace.json; leave the chat session file untouched.
    workspace_json.write_text(
        json.dumps({"folder": "file:///Users/demo/different-proj"}),
        encoding="utf-8",
    )
    future = time.time() + 60
    os.utime(workspace_json, (future, future))

    counts = refresh_cache(db, copilot_dir, claude_dir, vscode_dir)
    assert counts["updated"] == 1, counts
    db.close()


def test_copilot_source_mtime_tracks_workspace_yaml_too(tmp_path: Path) -> None:
    """If workspace.yaml is updated without touching events.jsonl,
    refresh must still re-parse the session — otherwise cached metadata
    (summary / repo / branch / cwd) goes stale. Regression for PR #27
    review comment 9."""
    import os
    import time

    db = CacheDB(tmp_path / "cache.db")
    copilot_dir = tmp_path / "copilot"
    claude_dir = tmp_path / "claude"
    vscode_dir = tmp_path / "vscode"
    for d in (copilot_dir, claude_dir, vscode_dir):
        d.mkdir()

    sdir = _make_copilot_session(copilot_dir, "aaaaaaaa-1111-2222-3333-444444444444", "Original Summary")
    build_cache(db, copilot_dir, claude_dir, vscode_dir)
    assert db.get_sessions()[0]["summary"] == "Original Summary"

    # Update workspace.yaml ONLY — leave events.jsonl untouched.
    (sdir / "workspace.yaml").write_text(
        "summary: Updated Summary\nrepository: test/repo\nbranch: main\ncwd: /tmp/proj\n"
        "created_at: '2026-04-26T10:00:00Z'\nupdated_at: '2026-04-26T10:01:00Z'\n",
        encoding="utf-8",
    )
    future = time.time() + 60
    os.utime(sdir / "workspace.yaml", (future, future))

    counts = refresh_cache(db, copilot_dir, claude_dir, vscode_dir)
    assert counts["updated"] == 1
    assert db.get_sessions()[0]["summary"] == "Updated Summary"
    db.close()


def test_refresh_skips_status_write_if_rebuild_took_over(tmp_path: Path) -> None:
    """If a manual rebuild flips the status to "building" while a
    background refresh is running, refresh must NOT overwrite the
    status with "ready" on completion — that would falsely declare the
    build done. Regression for PR #27 review #56."""
    db = CacheDB(tmp_path / "cache.db")
    copilot_dir = tmp_path / "copilot"
    claude_dir = tmp_path / "claude"
    vscode_dir = tmp_path / "vscode"
    for d in (copilot_dir, claude_dir, vscode_dir):
        d.mkdir()

    # Simulate a rebuild having taken over: set status to "building"
    # *before* refresh exits. Easiest way is to start refresh and then
    # mid-flight have something flip the status — but for a unit test
    # we can just prove the post-condition.  Set status to "building"
    # before calling refresh_cache; refresh sets it to "refreshing" on
    # entry, does work, then checks status before writing ready.
    # If we override "refreshing" → "building" between the entry write
    # and the exit check, the exit check sees "building" and skips the
    # ready write.
    #
    # We can't easily race threads here, but we can verify the exit
    # logic directly: monkey-patch insert_tool_config (one of the last
    # operations) to flip status, then assert refresh respected it.
    original_insert = db.insert_tool_config

    def _flip_status(tool: str, config: dict) -> None:
        original_insert(tool, config)
        db.set_meta("status", "building")  # simulate rebuild override

    db.insert_tool_config = _flip_status  # type: ignore[method-assign]

    refresh_cache(db, copilot_dir, claude_dir, vscode_dir)

    # Status should still be "building" — refresh didn't clobber it.
    assert db.status == "building"
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
