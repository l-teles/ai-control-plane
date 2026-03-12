"""Tests for the VS Code Chat log parser module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_log_viewer.vscode_parser import (
    build_conversation,
    compute_stats,
    discover_sessions,
    extract_workspace,
    parse_events,
)


def _make_session(
    *,
    session_id: str = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    creation_date: int = 1710237600000,  # 2024-03-12T10:00:00Z
    last_message_date: int = 1710237900000,  # 2024-03-12T10:05:00Z
    custom_title: str = "",
    requests: list[dict] | None = None,
) -> dict:
    """Build a minimal VS Code Chat session dict."""
    if requests is None:
        requests = [_make_request()]
    data: dict = {
        "version": 3,
        "requesterUsername": "test-user",
        "responderUsername": "GitHub Copilot",
        "initialLocation": "panel",
        "requests": requests,
        "sessionId": session_id,
        "creationDate": creation_date,
        "lastMessageDate": last_message_date,
        "isImported": False,
    }
    if custom_title:
        data["customTitle"] = custom_title
    return data


def _make_request(
    *,
    text: str = "Help me fix this bug",
    timestamp: int = 1710237601000,
    model_id: str = "copilot/claude-sonnet-4",
    response_text: str = "I can help with that. Let me look at the code.",
    tool_call_rounds: list[dict] | None = None,
    tool_call_results: dict | None = None,
    is_canceled: bool = False,
) -> dict:
    req: dict = {
        "requestId": "request_11111111-2222-3333-4444-555555555555",
        "message": {
            "parts": [{"text": text, "kind": "text", "range": {"start": 0, "endExclusive": len(text)}}],
            "text": text,
        },
        "variableData": {"variables": []},
        "response": [
            {
                "value": response_text,
                "supportThemeIcons": False,
                "supportHtml": False,
            }
        ],
        "responseId": "response_22222222-3333-4444-5555-666666666666",
        "result": {
            "timings": {"firstProgress": 1000, "totalElapsed": 5000},
            "metadata": {
                "toolCallRounds": tool_call_rounds or [],
                "toolCallResults": tool_call_results or {},
            },
            "details": "Claude Sonnet 4",
        },
        "responseMarkdownInfo": [],
        "followups": [],
        "isCanceled": is_canceled,
        "agent": {
            "id": "github.copilot.editsAgent",
            "name": "agent",
        },
        "contentReferences": [],
        "codeCitations": [],
        "timestamp": timestamp,
        "modelId": model_id,
    }
    return req


def _make_request_with_tools() -> dict:
    """Build a request with tool call rounds and results."""
    return _make_request(
        text="List the files",
        response_text="",
        tool_call_rounds=[
            {
                "response": "Let me check the directory.",
                "toolCalls": [
                    {
                        "name": "read_file",
                        "arguments": '{"filePath": "/tmp/test.py"}',
                        "id": "toolu_001__vscode-123",
                    }
                ],
                "toolInputRetry": 0,
                "id": "round_001",
            },
            {
                "response": "I can see the file content.",
                "toolCalls": [
                    {
                        "name": "run_in_terminal",
                        "arguments": '{"command": "ls -la"}',
                        "id": "toolu_002__vscode-124",
                    }
                ],
                "toolInputRetry": 0,
                "id": "round_002",
            },
        ],
        tool_call_results={
            "toolu_001__vscode-123": {
                "$mid": 20,
                "content": [{"$mid": 21, "value": "print('hello')"}],
            },
            "toolu_002__vscode-124": {
                "$mid": 20,
                "content": [{"$mid": 21, "value": "total 4\n-rw-r--r-- 1 user user 14 test.py"}],
            },
        },
    )


@pytest.fixture()
def vscode_workspace(tmp_path: Path) -> Path:
    """Create a minimal VS Code workspace storage structure."""
    ws_dir = tmp_path / "workspaceStorage" / "abc123hash"
    chat_dir = ws_dir / "chatSessions"
    chat_dir.mkdir(parents=True)

    # workspace.json
    (ws_dir / "workspace.json").write_text(
        json.dumps({"folder": "file:///Users/test/my-project"})
    )

    # Session with tool calls
    session = _make_session(
        custom_title="Fix the bug in auth module",
        requests=[_make_request(), _make_request_with_tools()],
    )
    (chat_dir / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json").write_text(
        json.dumps(session)
    )

    return tmp_path


@pytest.fixture()
def vscode_global_session(tmp_path: Path) -> Path:
    """Create a global (empty window) chat session."""
    global_dir = tmp_path / "globalStorage" / "emptyWindowChatSessions"
    global_dir.mkdir(parents=True)

    session = _make_session(
        session_id="bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        custom_title="NPM not found",
    )
    wrapper = {"kind": 0, "v": session}
    (global_dir / "bbbbbbbb-cccc-dddd-eeee-ffffffffffff.jsonl").write_text(
        json.dumps(wrapper) + "\n"
    )

    return tmp_path


# ---------------------------------------------------------------------------
# discover_sessions
# ---------------------------------------------------------------------------

def test_discover_sessions(vscode_workspace: Path) -> None:
    sessions = discover_sessions(vscode_workspace)
    assert len(sessions) == 1
    s = sessions[0]
    assert s["id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert s["source"] == "vscode"
    assert s["summary"] == "Fix the bug in auth module"
    assert s["cwd"] == "/Users/test/my-project"
    assert s["repository"] == "my-project"
    assert s["model"] == "claude-sonnet-4"


def test_discover_global_sessions(vscode_global_session: Path) -> None:
    sessions = discover_sessions(vscode_global_session)
    assert len(sessions) == 1
    s = sessions[0]
    assert s["id"] == "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
    assert s["source"] == "vscode"
    assert s["summary"] == "NPM not found"
    assert s["cwd"] == ""


def test_discover_empty_dir(tmp_path: Path) -> None:
    sessions = discover_sessions(tmp_path)
    assert sessions == []


# ---------------------------------------------------------------------------
# parse_events
# ---------------------------------------------------------------------------

def test_parse_events(vscode_workspace: Path) -> None:
    session_file = (
        vscode_workspace / "workspaceStorage" / "abc123hash"
        / "chatSessions" / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json"
    )
    events = parse_events(session_file)
    assert len(events) == 3  # 1 meta + 2 requests
    assert events[0].get("_vscode_meta") is True
    assert events[0]["sessionId"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert events[0]["cwd"] == "/Users/test/my-project"


def test_parse_events_global(vscode_global_session: Path) -> None:
    session_file = (
        vscode_global_session / "globalStorage" / "emptyWindowChatSessions"
        / "bbbbbbbb-cccc-dddd-eeee-ffffffffffff.jsonl"
    )
    events = parse_events(session_file)
    assert len(events) >= 2  # 1 meta + at least 1 request
    assert events[0].get("_vscode_meta") is True


# ---------------------------------------------------------------------------
# build_conversation
# ---------------------------------------------------------------------------

def test_build_conversation_session_start(vscode_workspace: Path) -> None:
    session_file = (
        vscode_workspace / "workspaceStorage" / "abc123hash"
        / "chatSessions" / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json"
    )
    events = parse_events(session_file)
    conv = build_conversation(events)
    assert conv[0]["kind"] == "session_start"
    assert conv[0]["cwd"] == "/Users/test/my-project"


def test_build_conversation_user_message(vscode_workspace: Path) -> None:
    session_file = (
        vscode_workspace / "workspaceStorage" / "abc123hash"
        / "chatSessions" / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json"
    )
    events = parse_events(session_file)
    conv = build_conversation(events)
    user_msgs = [c for c in conv if c["kind"] == "user_message"]
    assert len(user_msgs) == 2
    assert user_msgs[0]["content"] == "Help me fix this bug"
    assert user_msgs[1]["content"] == "List the files"


def test_build_conversation_assistant_message() -> None:
    """Simple text response without tool calls."""
    meta = {"_vscode_meta": True, "sessionId": "test", "creationDate": 1710237600000, "lastMessageDate": 0}
    req = _make_request(text="Hello", response_text="Hi there!")
    conv = build_conversation([meta, req])
    asst = [c for c in conv if c["kind"] == "assistant_message"]
    assert len(asst) == 1
    assert asst[0]["content"] == "Hi there!"


def test_build_conversation_tool_calls() -> None:
    """Request with tool call rounds produces tool_start and tool_complete."""
    meta = {"_vscode_meta": True, "sessionId": "test", "creationDate": 1710237600000, "lastMessageDate": 0}
    req = _make_request_with_tools()
    conv = build_conversation([meta, req])

    tool_starts = [c for c in conv if c["kind"] == "tool_start"]
    assert len(tool_starts) == 2
    assert tool_starts[0]["tool_name"] == "read_file"
    assert tool_starts[0]["arguments"] == {"filePath": "/tmp/test.py"}
    assert tool_starts[1]["tool_name"] == "run_in_terminal"

    tool_completes = [c for c in conv if c["kind"] == "tool_complete"]
    assert len(tool_completes) == 2
    assert "print('hello')" in tool_completes[0]["result"]
    assert tool_completes[0]["success"] is True


def test_build_conversation_canceled_request() -> None:
    meta = {"_vscode_meta": True, "sessionId": "test", "creationDate": 1710237600000, "lastMessageDate": 0}
    req = _make_request(text="Do something", is_canceled=True)
    conv = build_conversation([meta, req])
    errors = [c for c in conv if c["kind"] == "error"]
    assert len(errors) == 1
    assert "canceled" in errors[0]["message"].lower()


def test_build_conversation_session_end() -> None:
    meta = {"_vscode_meta": True, "sessionId": "test", "creationDate": 1710237600000, "lastMessageDate": 0}
    req = _make_request()
    conv = build_conversation([meta, req])
    assert conv[-1]["kind"] == "session_end"


def test_build_conversation_tool_rounds_interleaved() -> None:
    """Multiple tool call rounds produce interleaved assistant/tool items."""
    meta = {"_vscode_meta": True, "sessionId": "test", "creationDate": 1710237600000, "lastMessageDate": 0}
    req = _make_request_with_tools()
    conv = build_conversation([meta, req])

    # Should have: session_start, user_message, then rounds interleaved, then session_end
    asst_msgs = [c for c in conv if c["kind"] == "assistant_message"]
    assert len(asst_msgs) == 2  # one per round
    assert "check the directory" in asst_msgs[0]["content"]
    assert "see the file content" in asst_msgs[1]["content"]


# ---------------------------------------------------------------------------
# compute_stats
# ---------------------------------------------------------------------------

def test_compute_stats(vscode_workspace: Path) -> None:
    session_file = (
        vscode_workspace / "workspaceStorage" / "abc123hash"
        / "chatSessions" / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json"
    )
    events = parse_events(session_file)
    stats = compute_stats(events)
    assert stats["user_messages"] == 2
    assert stats["assistant_messages"] == 2
    assert stats["turns"] == 2
    assert stats["total_tool_calls"] == 2
    assert stats["tool_calls"]["read_file"] == 1
    assert stats["tool_calls"]["run_in_terminal"] == 1
    assert stats["errors"] == 0


def test_compute_stats_canceled() -> None:
    meta = {"_vscode_meta": True}
    req = _make_request(is_canceled=True)
    stats = compute_stats([meta, req])
    assert stats["errors"] == 1


# ---------------------------------------------------------------------------
# extract_workspace
# ---------------------------------------------------------------------------

def test_extract_workspace(vscode_workspace: Path) -> None:
    session_file = (
        vscode_workspace / "workspaceStorage" / "abc123hash"
        / "chatSessions" / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json"
    )
    events = parse_events(session_file)
    ws = extract_workspace(events)
    assert ws["id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert ws["cwd"] == "/Users/test/my-project"
    assert ws["model"] == "claude-sonnet-4"
    assert ws["summary"] == "Fix the bug in auth module"
    assert ws["created_at"]  # non-empty ISO timestamp
