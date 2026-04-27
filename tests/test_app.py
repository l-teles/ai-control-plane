"""Tests for the Flask application routes and security."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from ai_ctrl_plane.app import create_app


@pytest.fixture()
def app_with_data(tmp_path: Path):
    """Create an app backed by a temporary session directory."""
    session_dir = tmp_path / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    session_dir.mkdir()

    (session_dir / "workspace.yaml").write_text(
        textwrap.dedent("""\
        id: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
        summary: Test Session
        repository: org/repo
        branch: main
        created_at: 2026-03-12T10:00:00.000Z
        updated_at: 2026-03-12T10:05:00.000Z
        """)
    )

    events = [
        {
            "type": "session.start",
            "data": {"copilotVersion": "1.0.0", "context": {}},
            "timestamp": "2026-03-12T10:00:00Z",
        },
        {"type": "user.message", "data": {"content": "hello"}, "timestamp": "2026-03-12T10:00:01Z"},
        {"type": "session.shutdown", "data": {}, "timestamp": "2026-03-12T10:05:00Z"},
    ]
    with open(session_dir / "events.jsonl", "w") as f:
        for evt in events:
            f.write(json.dumps(evt) + "\n")

    # Create a backup file for the backup endpoint test
    backups = session_dir / "rewind-snapshots" / "backups"
    backups.mkdir(parents=True)
    (backups / "abcdef0123456789-1234567890123").write_text("backup content")

    app = create_app(tmp_path, tmp_path / "empty_claude", tmp_path / "empty_vscode", cache_dir=tmp_path / "cache")
    app.config["TESTING"] = True
    return app


def test_index_returns_200(app_with_data) -> None:
    with app_with_data.test_client() as c:
        r = c.get("/")
        assert r.status_code == 200
        assert b"Test Session" in r.data


def test_session_view_returns_200(app_with_data) -> None:
    with app_with_data.test_client() as c:
        r = c.get("/session/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        assert r.status_code == 200
        assert b"hello" in r.data


def test_session_view_404_for_missing(app_with_data) -> None:
    with app_with_data.test_client() as c:
        r = c.get("/session/11111111-2222-3333-4444-555555555555")
        assert r.status_code == 404


def test_session_view_rejects_path_traversal(app_with_data) -> None:
    """Path traversal attempts are blocked (Flask normalizes ../ to 404)."""
    with app_with_data.test_client() as c:
        r = c.get("/session/../../etc/passwd")
        assert r.status_code in (400, 404)  # blocked either way


def test_session_view_400_for_non_uuid(app_with_data) -> None:
    with app_with_data.test_client() as c:
        r = c.get("/session/not-a-uuid")
        assert r.status_code == 400


def test_api_sessions(app_with_data) -> None:
    with app_with_data.test_client() as c:
        r = c.get("/api/sessions")
        assert r.status_code == 200
        data = r.get_json()
        assert len(data) == 1
        assert data[0]["summary"] == "Test Session"


def test_api_events(app_with_data) -> None:
    with app_with_data.test_client() as c:
        r = c.get("/api/session/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/events")
        assert r.status_code == 200
        data = r.get_json()
        assert len(data) == 3


def test_api_backup_returns_content(app_with_data) -> None:
    with app_with_data.test_client() as c:
        r = c.get("/api/session/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/backup/abcdef0123456789-1234567890123")
        assert r.status_code == 200
        assert r.data == b"backup content"


def test_api_backup_rejects_path_traversal(app_with_data) -> None:
    """Path traversal in backup hash is blocked."""
    with app_with_data.test_client() as c:
        r = c.get("/api/session/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/backup/../../etc/passwd")
        assert r.status_code in (400, 404)  # blocked either way


def test_security_headers(app_with_data) -> None:
    with app_with_data.test_client() as c:
        r = c.get("/")
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert "Content-Security-Policy" in r.headers
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]


# ---------------------------------------------------------------------------
# Claude Code session tests
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, events: list[dict]) -> None:
    with open(path, "w") as f:
        for evt in events:
            f.write(json.dumps(evt) + "\n")


@pytest.fixture()
def app_with_claude(tmp_path: Path):
    """Create an app with a Claude Code session."""
    claude_dir = tmp_path / "claude_projects"
    project_dir = claude_dir / "-Users-test-project"
    project_dir.mkdir(parents=True)

    events = [
        {
            "type": "user",
            "message": {"role": "user", "content": "Write tests"},
            "uuid": "u1",
            "timestamp": "2026-03-12T10:00:01Z",
            "sessionId": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
            "cwd": "/tmp/proj",
            "version": "2.1.74",
            "gitBranch": "dev",
        },
        {
            "type": "assistant",
            "message": {
                "model": "claude-opus-4-6",
                "role": "assistant",
                "content": [{"type": "text", "text": "Sure!"}],
                "usage": {"input_tokens": 50, "output_tokens": 10},
            },
            "uuid": "a1",
            "requestId": "req_01",
            "timestamp": "2026-03-12T10:00:02Z",
            "sessionId": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
            "cwd": "/tmp/proj",
            "version": "2.1.74",
            "gitBranch": "dev",
        },
    ]
    _write_jsonl(project_dir / "bbbbbbbb-cccc-dddd-eeee-ffffffffffff.jsonl", events)

    app = create_app(tmp_path / "empty_copilot", claude_dir, tmp_path / "empty_vscode", cache_dir=tmp_path / "cache")
    app.config["TESTING"] = True
    return app


def test_claude_session_in_index(app_with_claude) -> None:
    with app_with_claude.test_client() as c:
        r = c.get("/")
        assert r.status_code == 200
        assert b"Claude Code" in r.data


def test_claude_session_view(app_with_claude) -> None:
    with app_with_claude.test_client() as c:
        r = c.get("/session/bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
        assert r.status_code == 200
        assert b"Write tests" in r.data
        assert b"Sure!" in r.data


def test_claude_api_events(app_with_claude) -> None:
    with app_with_claude.test_client() as c:
        r = c.get("/api/session/bbbbbbbb-cccc-dddd-eeee-ffffffffffff/events")
        assert r.status_code == 200
        data = r.get_json()
        assert len(data) == 2


@pytest.fixture()
def app_mixed(tmp_path: Path):
    """App with both Copilot and Claude sessions."""
    # Copilot session
    session_dir = tmp_path / "copilot" / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    session_dir.mkdir(parents=True)
    (session_dir / "workspace.yaml").write_text(
        textwrap.dedent("""\
        id: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
        summary: Copilot Session
        repository: org/repo
        branch: main
        created_at: 2026-03-12T09:00:00.000Z
        updated_at: 2026-03-12T09:05:00.000Z
        """)
    )
    events_copilot = [
        {
            "type": "session.start",
            "data": {"copilotVersion": "1.0.0", "context": {}},
            "timestamp": "2026-03-12T09:00:00Z",
        },
        {"type": "user.message", "data": {"content": "copilot msg"}, "timestamp": "2026-03-12T09:00:01Z"},
        {"type": "session.shutdown", "data": {}, "timestamp": "2026-03-12T09:05:00Z"},
    ]
    _write_jsonl(session_dir / "events.jsonl", events_copilot)

    # Claude session
    claude_dir = tmp_path / "claude"
    project_dir = claude_dir / "-Users-test"
    project_dir.mkdir(parents=True)
    events_claude = [
        {
            "type": "user",
            "message": {"role": "user", "content": "claude msg"},
            "uuid": "u1",
            "timestamp": "2026-03-12T10:00:01Z",
            "sessionId": "cccccccc-dddd-eeee-ffff-111111111111",
            "cwd": "/tmp/p",
            "version": "2.1.74",
            "gitBranch": "main",
        },
        {
            "type": "assistant",
            "message": {
                "model": "claude-opus-4-6",
                "role": "assistant",
                "content": [{"type": "text", "text": "OK"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
            "uuid": "a1",
            "requestId": "r1",
            "timestamp": "2026-03-12T10:00:02Z",
            "sessionId": "cccccccc-dddd-eeee-ffff-111111111111",
            "cwd": "/tmp/p",
            "version": "2.1.74",
            "gitBranch": "main",
        },
    ]
    _write_jsonl(project_dir / "cccccccc-dddd-eeee-ffff-111111111111.jsonl", events_claude)

    app = create_app(tmp_path / "copilot", claude_dir, tmp_path / "empty_vscode", cache_dir=tmp_path / "cache")
    app.config["TESTING"] = True
    return app


def test_mixed_sessions_index(app_mixed) -> None:
    with app_mixed.test_client() as c:
        r = c.get("/api/sessions")
        assert r.status_code == 200
        data = r.get_json()
        assert len(data) == 2
        sources = {s["source"] for s in data}
        assert sources == {"copilot", "claude"}


# ---------------------------------------------------------------------------
# VS Code Chat session tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_with_vscode(tmp_path: Path):
    """Create an app with a VS Code Chat session."""
    vscode_dir = tmp_path / "vscode_user"
    ws_dir = vscode_dir / "workspaceStorage" / "abc123hash"
    chat_dir = ws_dir / "chatSessions"
    chat_dir.mkdir(parents=True)

    (ws_dir / "workspace.json").write_text(json.dumps({"folder": "file:///Users/test/my-project"}))

    session = {
        "version": 3,
        "requesterUsername": "test-user",
        "responderUsername": "GitHub Copilot",
        "initialLocation": "panel",
        "requests": [
            {
                "requestId": "request_11111111-2222-3333-4444-555555555555",
                "message": {"text": "Fix the auth bug", "parts": [{"text": "Fix the auth bug", "kind": "text"}]},
                "variableData": {"variables": []},
                "response": [{"value": "I'll fix that for you."}],
                "responseId": "resp_001",
                "result": {
                    "timings": {"firstProgress": 500, "totalElapsed": 3000},
                    "metadata": {"toolCallRounds": [], "toolCallResults": {}},
                    "details": "Claude Sonnet 4",
                },
                "followups": [],
                "isCanceled": False,
                "agent": {"id": "github.copilot.editsAgent", "name": "agent"},
                "contentReferences": [],
                "codeCitations": [],
                "timestamp": 1710237601000,
                "modelId": "copilot/claude-sonnet-4",
            }
        ],
        "sessionId": "dddddddd-eeee-ffff-1111-222222222222",
        "creationDate": 1710237600000,
        "lastMessageDate": 1710237601000,
        "customTitle": "Fix auth bug",
    }
    (chat_dir / "dddddddd-eeee-ffff-1111-222222222222.json").write_text(json.dumps(session))

    app = create_app(tmp_path / "empty_copilot", tmp_path / "empty_claude", vscode_dir, cache_dir=tmp_path / "cache")
    app.config["TESTING"] = True
    return app


def test_vscode_session_in_index(app_with_vscode) -> None:
    with app_with_vscode.test_client() as c:
        r = c.get("/")
        assert r.status_code == 200
        assert b"VS Code Chat" in r.data
        assert b"Fix auth bug" in r.data


def test_vscode_session_view(app_with_vscode) -> None:
    with app_with_vscode.test_client() as c:
        r = c.get("/session/dddddddd-eeee-ffff-1111-222222222222")
        assert r.status_code == 200
        assert b"Fix the auth bug" in r.data
        assert b"fix that for you" in r.data


def test_vscode_api_events(app_with_vscode) -> None:
    with app_with_vscode.test_client() as c:
        r = c.get("/api/session/dddddddd-eeee-ffff-1111-222222222222/events")
        assert r.status_code == 200
        data = r.get_json()
        assert len(data) == 2  # 1 meta + 1 request


def test_all_three_sources(tmp_path: Path) -> None:
    """App with Copilot, Claude, and VS Code sessions."""
    # Copilot
    copilot_dir = tmp_path / "copilot" / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    copilot_dir.mkdir(parents=True)
    (copilot_dir / "workspace.yaml").write_text(
        textwrap.dedent("""\
        id: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
        summary: Copilot Session
        created_at: 2026-03-12T09:00:00.000Z
        updated_at: 2026-03-12T09:05:00.000Z
        """)
    )
    _write_jsonl(
        copilot_dir / "events.jsonl",
        [
            {
                "type": "session.start",
                "data": {"copilotVersion": "1.0.0", "context": {}},
                "timestamp": "2026-03-12T09:00:00Z",
            },
        ],
    )

    # Claude
    claude_dir = tmp_path / "claude" / "-Users-test"
    claude_dir.mkdir(parents=True)
    _write_jsonl(
        claude_dir / "bbbbbbbb-cccc-dddd-eeee-ffffffffffff.jsonl",
        [
            {
                "type": "user",
                "message": {"role": "user", "content": "hi"},
                "uuid": "u1",
                "timestamp": "2026-03-12T10:00:01Z",
                "sessionId": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
                "cwd": "/tmp",
                "version": "2.1.74",
                "gitBranch": "main",
            },
        ],
    )

    # VS Code
    vscode_dir = tmp_path / "vscode"
    vs_ws = vscode_dir / "workspaceStorage" / "hash1" / "chatSessions"
    vs_ws.mkdir(parents=True)
    (vs_ws.parent / "workspace.json").write_text(json.dumps({"folder": "file:///tmp/proj"}))
    (vs_ws / "cccccccc-dddd-eeee-ffff-111111111111.json").write_text(
        json.dumps(
            {
                "version": 3,
                "sessionId": "cccccccc-dddd-eeee-ffff-111111111111",
                "creationDate": 1710237600000,
                "lastMessageDate": 1710237601000,
                "requests": [
                    {
                        "requestId": "req_1",
                        "message": {"text": "hello", "parts": []},
                        "variableData": {"variables": []},
                        "response": [{"value": "Hi!"}],
                        "result": {
                            "timings": {},
                            "metadata": {"toolCallRounds": [], "toolCallResults": {}},
                            "details": "",
                        },
                        "followups": [],
                        "isCanceled": False,
                        "agent": {"id": "agent", "name": "agent"},
                        "contentReferences": [],
                        "codeCitations": [],
                        "timestamp": 1710237601000,
                        "modelId": "copilot/gpt-4o",
                    }
                ],
            }
        )
    )

    app = create_app(tmp_path / "copilot", tmp_path / "claude", vscode_dir, cache_dir=tmp_path / "cache")
    app.config["TESTING"] = True

    with app.test_client() as c:
        r = c.get("/api/sessions")
        assert r.status_code == 200
        data = r.get_json()
        sources = {s["source"] for s in data}
        assert sources == {"copilot", "claude", "vscode"}


# ---------------------------------------------------------------------------
# Phase 4 — search and date filtering
# ---------------------------------------------------------------------------


def test_natural_date_parser_iso_passthrough() -> None:
    from ai_ctrl_plane.app import _parse_natural_date

    assert _parse_natural_date("2026-04-26") == "2026-04-26"


def test_natural_date_parser_today_yesterday() -> None:
    from datetime import UTC, datetime, timedelta

    from ai_ctrl_plane.app import _parse_natural_date

    today = datetime.now(UTC).date()
    assert _parse_natural_date("today") == today.isoformat()
    assert _parse_natural_date("yesterday") == (today - timedelta(days=1)).isoformat()


def test_natural_date_parser_n_days_ago() -> None:
    from datetime import UTC, datetime, timedelta

    from ai_ctrl_plane.app import _parse_natural_date

    today = datetime.now(UTC).date()
    assert _parse_natural_date("3 days ago") == (today - timedelta(days=3)).isoformat()
    assert _parse_natural_date("1 day ago") == (today - timedelta(days=1)).isoformat()


def test_natural_date_parser_last_week_and_last_month() -> None:
    """``last week`` / ``last month`` shorthands. Regression for PR #27 review."""
    from datetime import UTC, datetime, timedelta

    from ai_ctrl_plane.app import _parse_natural_date

    today = datetime.now(UTC).date()
    assert _parse_natural_date("last week") == (today - timedelta(days=7)).isoformat()
    assert _parse_natural_date("last month") == (today - timedelta(days=30)).isoformat()


def test_natural_date_parser_n_weeks_ago_with_pluralisation() -> None:
    """``N weeks ago`` and ``1 week ago``. Regression for PR #27 review."""
    from datetime import UTC, datetime, timedelta

    from ai_ctrl_plane.app import _parse_natural_date

    today = datetime.now(UTC).date()
    assert _parse_natural_date("2 weeks ago") == (today - timedelta(weeks=2)).isoformat()
    assert _parse_natural_date("1 week ago") == (today - timedelta(weeks=1)).isoformat()


def test_natural_date_parser_n_months_ago_with_pluralisation() -> None:
    """``N months ago`` and ``1 month ago``. Months are approximated as 30
    days, which the helper documents and ``_filter_by_date_range`` accepts.
    Regression for PR #27 review."""
    from datetime import UTC, datetime, timedelta

    from ai_ctrl_plane.app import _parse_natural_date

    today = datetime.now(UTC).date()
    assert _parse_natural_date("3 months ago") == (today - timedelta(days=90)).isoformat()
    assert _parse_natural_date("1 month ago") == (today - timedelta(days=30)).isoformat()


def test_validate_session_id_rejects_uppercase_source_prefix() -> None:
    """``_UUID_RE`` is intentionally lowercase-only — stored session ids and
    source names are all lowercase, so ``CLAUDE:<uuid>`` would validate but
    fail the lookup. Reject it up front instead.  Regression for PR #27."""
    import pytest

    from ai_ctrl_plane.app import _validate_session_id

    # Lowercase composite — accepted.
    _validate_session_id("claude:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    # Bare lowercase UUID — accepted.
    _validate_session_id("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    # Uppercase source prefix — must reject (HTTP 400 via abort()).
    with pytest.raises(Exception, match="(?i)400|invalid"):
        _validate_session_id("CLAUDE:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    # Uppercase UUID hex — also rejected.
    with pytest.raises(Exception, match="(?i)400|invalid"):
        _validate_session_id("AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE")


def test_natural_date_parser_unrecognised_returns_empty() -> None:
    from ai_ctrl_plane.app import _parse_natural_date

    assert _parse_natural_date("sometime in the future") == ""
    assert _parse_natural_date("") == ""


def test_natural_date_parser_rejects_invalid_calendar_dates() -> None:
    """``2026-99-99`` matches the regex but isn't a real date; accepting
    it would cause confusing filter behaviour because the lexical compare
    in ``_filter_by_date_range`` would drop or include sessions arbitrarily.
    Regression for PR #27 review #23."""
    from ai_ctrl_plane.app import _parse_natural_date

    assert _parse_natural_date("2026-99-99") == ""
    assert _parse_natural_date("2026-13-01") == ""  # invalid month
    assert _parse_natural_date("2026-02-30") == ""  # Feb 30 doesn't exist
    # Real dates still pass through.
    assert _parse_natural_date("2026-04-26") == "2026-04-26"
    assert _parse_natural_date("2024-02-29") == "2024-02-29"  # leap year


def test_filter_by_date_range_inclusive_on_both_bounds() -> None:
    from ai_ctrl_plane.app import _filter_by_date_range

    sessions = [
        {"id": "a", "created_at": "2026-04-20T10:00:00Z"},
        {"id": "b", "created_at": "2026-04-22T10:00:00Z"},
        {"id": "c", "created_at": "2026-04-25T10:00:00Z"},
    ]
    out = _filter_by_date_range(sessions, "2026-04-21", "2026-04-23")
    assert [s["id"] for s in out] == ["b"]


def test_filter_by_date_range_open_ended() -> None:
    from ai_ctrl_plane.app import _filter_by_date_range

    sessions = [
        {"id": "a", "created_at": "2026-04-20T10:00:00Z"},
        {"id": "b", "created_at": "2026-04-25T10:00:00Z"},
    ]
    out = _filter_by_date_range(sessions, "2026-04-22", "")
    assert [s["id"] for s in out] == ["b"]
    out = _filter_by_date_range(sessions, "", "2026-04-22")
    assert [s["id"] for s in out] == ["a"]


def test_search_route_uses_fts_index(tmp_path: Path) -> None:
    """A GET to /sessions?q=… runs through ``db.search_sessions`` (FTS) and
    only the matching session is rendered."""
    project_dir = tmp_path / "claude_logs" / "-Users-test-project"
    project_dir.mkdir(parents=True)
    sid_a = "11111111-aaaa-aaaa-aaaa-111111111111"
    sid_b = "22222222-bbbb-bbbb-bbbb-222222222222"
    for sid, summary in [(sid_a, "refactor authentication"), (sid_b, "fix the cache layer")]:
        with open(project_dir / f"{sid}.jsonl", "w") as f:
            f.write(
                json.dumps(
                    {
                        "type": "user",
                        "uuid": "u1",
                        "sessionId": sid,
                        "timestamp": "2026-04-26T10:00:00Z",
                        "cwd": "/repo",
                        "version": "2.1",
                        "gitBranch": "main",
                        "message": {"role": "user", "content": summary},
                    }
                )
                + "\n"
            )

    app = create_app(tmp_path / "copilot", tmp_path / "claude_logs", tmp_path / "vscode", cache_dir=tmp_path / "cache")
    app.config["TESTING"] = True

    with app.test_client() as c:
        # First, hit /sessions to populate the cache (build runs in
        # background — we may need a small synchronous nudge).
        c.get("/sessions")
        # Force a cache build so search has data to hit.
        from ai_ctrl_plane.db import build_cache

        build_cache(
            app.config["cache_db"],
            tmp_path / "copilot",
            tmp_path / "claude_logs",
            tmp_path / "vscode",
        )
        r = c.get("/api/search?q=authentication")
        assert r.status_code == 200
        results = r.get_json()
        ids = {s["id"] for s in results}
        assert sid_a in ids
        assert sid_b not in ids


def test_search_route_returns_empty_for_blank_query(tmp_path: Path) -> None:
    app = create_app(tmp_path / "c", tmp_path / "cl", tmp_path / "v", cache_dir=tmp_path / "cache")
    app.config["TESTING"] = True
    with app.test_client() as cl:
        r = cl.get("/api/search?q=   ")
        assert r.status_code == 200
        assert r.get_json() == []


def test_search_route_returns_slim_response_shape(tmp_path: Path) -> None:
    """``/api/search`` returns ``[{source, id}]`` only — the live-filter
    JS just needs the ids, and a slim response keeps per-keystroke
    bandwidth low. Full session payload is on /api/sessions.
    Regression for PR #27 review comment 13.
    """
    project_dir = tmp_path / "claude_logs" / "-Users-test-project"
    project_dir.mkdir(parents=True)
    sid = "11111111-aaaa-aaaa-aaaa-111111111111"
    with open(project_dir / f"{sid}.jsonl", "w") as f:
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "uuid": "u1",
                    "sessionId": sid,
                    "timestamp": "2026-04-26T10:00:00Z",
                    "cwd": "/repo",
                    "version": "2.1",
                    "gitBranch": "main",
                    "message": {"role": "user", "content": "needle_token_xyz"},
                }
            )
            + "\n"
        )

    app = create_app(tmp_path / "copilot", tmp_path / "claude_logs", tmp_path / "vscode", cache_dir=tmp_path / "cache")
    app.config["TESTING"] = True

    with app.test_client() as c:
        from ai_ctrl_plane.db import build_cache

        build_cache(
            app.config["cache_db"], tmp_path / "copilot", tmp_path / "claude_logs", tmp_path / "vscode"
        )
        r = c.get("/api/search?q=needle_token_xyz")
        assert r.status_code == 200
        results = r.get_json()
        assert len(results) == 1
        # Slim shape: only source + id
        assert set(results[0].keys()) == {"source", "id"}
        assert results[0]["id"] == sid


def test_session_view_accepts_composite_source_id(tmp_path: Path) -> None:
    """Two sessions sharing a UUID across sources should each be
    individually reachable via ``/session/<source>:<uuid>``. Bare-UUID
    URLs would 400 with "Ambiguous session ID" in this case.
    Regression for PR #27 review #50."""
    # Set up a Copilot session and a Claude session sharing the same UUID.
    sid = "11111111-2222-3333-4444-555555555555"

    copilot_session = tmp_path / "copilot" / sid
    copilot_session.mkdir(parents=True)
    (copilot_session / "workspace.yaml").write_text(
        f"id: {sid}\nsummary: Copilot Side\nrepository: x\nbranch: main\n"
        "created_at: 2026-04-26T10:00:00.000Z\nupdated_at: 2026-04-26T10:05:00.000Z\n",
        encoding="utf-8",
    )
    (copilot_session / "events.jsonl").write_text(
        json.dumps({"type": "session.start", "data": {}, "timestamp": "2026-04-26T10:00:00Z"}) + "\n",
        encoding="utf-8",
    )

    claude_dir = tmp_path / "claude" / "-Users-test-project"
    claude_dir.mkdir(parents=True)
    with open(claude_dir / f"{sid}.jsonl", "w") as f:
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "uuid": "u1",
                    "sessionId": sid,
                    "timestamp": "2026-04-26T11:00:00Z",
                    "cwd": "/repo",
                    "version": "2.1",
                    "gitBranch": "main",
                    "message": {"role": "user", "content": "Claude side"},
                }
            )
            + "\n"
        )

    app = create_app(tmp_path / "copilot", tmp_path / "claude", tmp_path / "vscode", cache_dir=tmp_path / "cache")
    app.config["TESTING"] = True

    with app.test_client() as c:
        # Bare UUID is ambiguous — server returns 400.
        r_bare = c.get(f"/session/{sid}")
        assert r_bare.status_code == 400

        # Composite IDs are unambiguous — both sessions are reachable.
        r_copilot = c.get(f"/session/copilot:{sid}")
        assert r_copilot.status_code == 200
        assert b"Copilot Side" in r_copilot.data

        r_claude = c.get(f"/session/claude:{sid}")
        assert r_claude.status_code == 200
        # Claude render uses summary or first-user-content; either way
        # the page renders without a 400 / 500.

        # /sessions list emits composite-form hrefs so users can click
        # through to the right session.
        r_list = c.get("/sessions")
        assert r_list.status_code == 200
        assert f"/session/copilot:{sid}".encode() in r_list.data
        assert f"/session/claude:{sid}".encode() in r_list.data


def test_search_skips_fallback_when_cache_ready(tmp_path: Path) -> None:
    """Once the cache is ``ready``, FTS is authoritative — a zero-hit
    query must NOT trigger a filesystem fallback (which would be wasted
    work on every legitimate no-match search and could surface results
    with different match semantics than FTS). Regression for PR #27."""
    project_dir = tmp_path / "claude_logs" / "-Users-test-project"
    project_dir.mkdir(parents=True)
    sid = "33333333-cccc-cccc-cccc-333333333333"
    with open(project_dir / f"{sid}.jsonl", "w") as f:
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "uuid": "u1",
                    "sessionId": sid,
                    "timestamp": "2026-04-26T10:00:00Z",
                    "cwd": "/repo",
                    "version": "2.1",
                    "gitBranch": "main",
                    "message": {"role": "user", "content": "frontend refactor work"},
                }
            )
            + "\n"
        )

    app = create_app(tmp_path / "copilot", tmp_path / "claude_logs", tmp_path / "vscode", cache_dir=tmp_path / "cache")
    app.config["TESTING"] = True

    db = app.config["cache_db"]
    # Simulate a built-but-stale cache: status="ready" but no FTS rows.
    db.set_meta("status", "ready")
    assert db.search_sessions("frontend") == []

    with app.test_client() as c:
        r = c.get("/api/search?q=frontend")
        assert r.status_code == 200
        # No fallback in "ready" state — empty FTS result returned as-is.
        assert r.get_json() == []


def test_search_falls_back_to_fs_scan_when_cache_empty(tmp_path: Path) -> None:
    """On a fresh install the FTS index is empty during the initial
    background build. A search request shouldn't return zero hits when
    sessions exist on disk — it should fall back to a filesystem scan
    plus a token match. Regression for PR #27 review comments 12 + 13.
    """
    project_dir = tmp_path / "claude_logs" / "-Users-test-project"
    project_dir.mkdir(parents=True)
    sid = "22222222-bbbb-bbbb-bbbb-222222222222"
    with open(project_dir / f"{sid}.jsonl", "w") as f:
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "uuid": "u1",
                    "sessionId": sid,
                    "timestamp": "2026-04-26T10:00:00Z",
                    "cwd": "/repo",
                    "version": "2.1",
                    "gitBranch": "main",
                    "message": {"role": "user", "content": "frontend refactor work"},
                }
            )
            + "\n"
        )

    app = create_app(tmp_path / "copilot", tmp_path / "claude_logs", tmp_path / "vscode", cache_dir=tmp_path / "cache")
    app.config["TESTING"] = True

    # Don't run build_cache — simulate the fresh-install state where the
    # initial background build hasn't populated the cache yet.
    db = app.config["cache_db"]
    assert db.search_sessions("frontend") == []  # FTS is empty

    with app.test_client() as c:
        # /api/search should still return the session via FS fallback.
        r = c.get("/api/search?q=frontend")
        assert r.status_code == 200
        results = r.get_json()
        assert len(results) == 1
        assert results[0]["id"] == sid

        # And /sessions?q=… HTML render should include the matching card.
        r = c.get("/sessions?q=frontend")
        assert r.status_code == 200
        assert sid.encode() in r.data
