"""Tests for the VS Code Chat log parser module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_ctrl_plane.vscode_parser import (
    build_conversation,
    compute_stats,
    discover_all_vscode_sessions,
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
    (ws_dir / "workspace.json").write_text(json.dumps({"folder": "file:///Users/test/my-project"}))

    # Session with tool calls
    session = _make_session(
        custom_title="Fix the bug in auth module",
        requests=[_make_request(), _make_request_with_tools()],
    )
    (chat_dir / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json").write_text(json.dumps(session))

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
    (global_dir / "bbbbbbbb-cccc-dddd-eeee-ffffffffffff.jsonl").write_text(json.dumps(wrapper) + "\n")

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


def test_discover_all_vscode_sessions_combines_stable_and_insiders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """discover_all_vscode_sessions returns sessions from both Stable and Insiders."""
    stable = tmp_path / "stable"
    insiders = tmp_path / "insiders"

    # Stable: one workspace session
    chat_stable = stable / "workspaceStorage" / "stablehash" / "chatSessions"
    chat_stable.mkdir(parents=True)
    (chat_stable.parent / "workspace.json").write_text(json.dumps({"folder": "file:///stable/proj"}))
    session_stable = _make_session(
        session_id="11111111-0000-0000-0000-000000000000",
        custom_title="Stable session",
    )
    (chat_stable / "11111111-0000-0000-0000-000000000000.json").write_text(json.dumps(session_stable))

    # Insiders: one workspace session
    chat_insiders = insiders / "workspaceStorage" / "insidershash" / "chatSessions"
    chat_insiders.mkdir(parents=True)
    (chat_insiders.parent / "workspace.json").write_text(json.dumps({"folder": "file:///insiders/proj"}))
    session_insiders = _make_session(
        session_id="22222222-0000-0000-0000-000000000000",
        custom_title="Insiders session",
    )
    (chat_insiders / "22222222-0000-0000-0000-000000000000.json").write_text(json.dumps(session_insiders))

    import ai_ctrl_plane.vscode_parser as vscode_parser_mod

    monkeypatch.setattr(vscode_parser_mod, "default_vscode_insiders_dir", lambda: insiders)

    sessions = discover_all_vscode_sessions(stable)
    ids = {s["id"] for s in sessions}
    assert "11111111-0000-0000-0000-000000000000" in ids
    assert "22222222-0000-0000-0000-000000000000" in ids
    assert len(sessions) == 2


def test_discover_all_vscode_sessions_insiders_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the Insiders directory does not exist only Stable sessions are returned."""
    stable = tmp_path / "stable"
    chat_stable = stable / "workspaceStorage" / "stablehash" / "chatSessions"
    chat_stable.mkdir(parents=True)
    (chat_stable.parent / "workspace.json").write_text(json.dumps({"folder": "file:///stable/proj"}))
    session_stable = _make_session(
        session_id="33333333-0000-0000-0000-000000000000",
        custom_title="Only stable",
    )
    (chat_stable / "33333333-0000-0000-0000-000000000000.json").write_text(json.dumps(session_stable))

    import ai_ctrl_plane.vscode_parser as vscode_parser_mod

    monkeypatch.setattr(vscode_parser_mod, "default_vscode_insiders_dir", lambda: tmp_path / "nonexistent")

    sessions = discover_all_vscode_sessions(stable)
    assert len(sessions) == 1
    assert sessions[0]["id"] == "33333333-0000-0000-0000-000000000000"


def test_jsonl_patch_reconstruction(tmp_path: Path) -> None:
    """JSONL kind 1/2 patches reconstruct the full session state."""
    ws_dir = tmp_path / "workspaceStorage" / "patchhash"
    chat_dir = ws_dir / "chatSessions"
    chat_dir.mkdir(parents=True)
    (ws_dir / "workspace.json").write_text(json.dumps({"folder": "file:///tmp/proj"}))

    # kind 0: initial snapshot with one request
    session = _make_session(
        session_id="cccccccc-dddd-eeee-ffff-000000000000",
        custom_title="Initial title",
    )
    lines = [json.dumps({"kind": 0, "v": session})]
    # kind 1: update top-level key (customTitle)
    lines.append(json.dumps({"kind": 1, "k": ["customTitle"], "v": "Patched title"}))
    # kind 2: update nested key (requests[0].isCanceled)
    lines.append(json.dumps({"kind": 2, "k": ["requests", 0, "isCanceled"], "v": True}))

    jsonl_path = chat_dir / "cccccccc-dddd-eeee-ffff-000000000000.jsonl"
    jsonl_path.write_text("\n".join(lines) + "\n")

    sessions = discover_sessions(tmp_path)
    assert len(sessions) == 1
    assert sessions[0]["summary"] == "Patched title"

    events = parse_events(jsonl_path)
    assert len(events) >= 2  # meta + request(s)
    assert events[0]["sessionId"] == "cccccccc-dddd-eeee-ffff-000000000000"


def test_jsonl_patch_two_level_dict_key(tmp_path: Path) -> None:
    """JSONL kind 1 with a 2-level dict key path works."""
    ws_dir = tmp_path / "workspaceStorage" / "dicthash"
    chat_dir = ws_dir / "chatSessions"
    chat_dir.mkdir(parents=True)
    (ws_dir / "workspace.json").write_text(json.dumps({"folder": "file:///tmp/proj"}))

    session = _make_session(
        session_id="dddddddd-eeee-ffff-0000-111111111111",
    )
    lines = [
        json.dumps({"kind": 0, "v": session}),
        # Add hasPendingEdits via a 1-key patch
        json.dumps({"kind": 1, "k": ["hasPendingEdits"], "v": True}),
    ]
    jsonl_path = chat_dir / "dddddddd-eeee-ffff-0000-111111111111.jsonl"
    jsonl_path.write_text("\n".join(lines) + "\n")

    sessions = discover_sessions(tmp_path)
    assert len(sessions) == 1
    assert sessions[0].get("has_pending_edits") is True


def test_jsonl_malformed_patch_ignored(tmp_path: Path) -> None:
    """Malformed JSONL patches don't crash the parser."""
    ws_dir = tmp_path / "workspaceStorage" / "badhash"
    chat_dir = ws_dir / "chatSessions"
    chat_dir.mkdir(parents=True)
    (ws_dir / "workspace.json").write_text(json.dumps({"folder": "file:///tmp/proj"}))

    session = _make_session(
        session_id="eeeeeeee-ffff-0000-1111-222222222222",
    )
    lines = [
        json.dumps({"kind": 0, "v": session}),
        "not valid json",
        json.dumps({"kind": 1, "k": [], "v": "empty keys"}),
        json.dumps({"kind": 99, "k": ["x"], "v": "unknown kind"}),
    ]
    jsonl_path = chat_dir / "eeeeeeee-ffff-0000-1111-222222222222.jsonl"
    jsonl_path.write_text("\n".join(lines) + "\n")

    # Should not crash, should still discover the session
    sessions = discover_sessions(tmp_path)
    assert len(sessions) == 1


# ---------------------------------------------------------------------------
# parse_events
# ---------------------------------------------------------------------------


def test_parse_events(vscode_workspace: Path) -> None:
    session_file = (
        vscode_workspace
        / "workspaceStorage"
        / "abc123hash"
        / "chatSessions"
        / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json"
    )
    events = parse_events(session_file)
    assert len(events) == 3  # 1 meta + 2 requests
    assert events[0].get("_vscode_meta") is True
    assert events[0]["sessionId"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert events[0]["cwd"] == "/Users/test/my-project"


def test_parse_events_global(vscode_global_session: Path) -> None:
    session_file = (
        vscode_global_session
        / "globalStorage"
        / "emptyWindowChatSessions"
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
        vscode_workspace
        / "workspaceStorage"
        / "abc123hash"
        / "chatSessions"
        / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json"
    )
    events = parse_events(session_file)
    conv = build_conversation(events)
    assert conv[0]["kind"] == "session_start"
    assert conv[0]["cwd"] == "/Users/test/my-project"


def test_build_conversation_user_message(vscode_workspace: Path) -> None:
    session_file = (
        vscode_workspace
        / "workspaceStorage"
        / "abc123hash"
        / "chatSessions"
        / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json"
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
        vscode_workspace
        / "workspaceStorage"
        / "abc123hash"
        / "chatSessions"
        / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json"
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
        vscode_workspace
        / "workspaceStorage"
        / "abc123hash"
        / "chatSessions"
        / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json"
    )
    events = parse_events(session_file)
    ws = extract_workspace(events)
    assert ws["id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert ws["cwd"] == "/Users/test/my-project"
    assert ws["model"] == "claude-sonnet-4"
    assert ws["summary"] == "Fix the bug in auth module"
    assert ws["created_at"]  # non-empty ISO timestamp


def test_max_tool_calls_warning() -> None:
    """maxToolCallsExceeded emits a warning event in conversation."""
    meta = {
        "_vscode_meta": True,
        "sessionId": "test",
        "creationDate": 1710237600000,
        "lastMessageDate": 0,
    }
    req = _make_request()
    req["result"]["metadata"]["maxToolCallsExceeded"] = True
    conv = build_conversation([meta, req])
    warnings = [c for c in conv if c["kind"] == "warning"]
    assert len(warnings) == 1
    assert "tool call limit" in warnings[0]["message"]


def test_max_tool_calls_on_session_entry(tmp_path: Path) -> None:
    """Session entry includes max_tool_calls_exceeded flag."""
    ws_dir = tmp_path / "workspaceStorage" / "hash1"
    chat_dir = ws_dir / "chatSessions"
    chat_dir.mkdir(parents=True)
    (ws_dir / "workspace.json").write_text(json.dumps({"folder": "file:///tmp/proj"}))
    req = _make_request()
    req["result"]["metadata"]["maxToolCallsExceeded"] = True
    session = _make_session(requests=[req])
    (chat_dir / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json").write_text(json.dumps(session))
    sessions = discover_sessions(tmp_path)
    assert len(sessions) == 1
    assert sessions[0].get("max_tool_calls_exceeded") is True


def test_session_summary() -> None:
    """Auto-generated summary from last request metadata."""
    meta = {
        "_vscode_meta": True,
        "sessionId": "test",
        "creationDate": 1710237600000,
        "lastMessageDate": 0,
    }
    req = _make_request()
    req["result"]["metadata"]["summary"] = {"text": "Fixed auth bugs"}
    conv = build_conversation([meta, req])
    summaries = [c for c in conv if c["kind"] == "session_summary"]
    assert len(summaries) == 1
    assert summaries[0]["content"] == "Fixed auth bugs"


def test_response_timings() -> None:
    """Assistant messages include timing fields from result.timings."""
    meta = {
        "_vscode_meta": True,
        "sessionId": "test",
        "creationDate": 1710237600000,
        "lastMessageDate": 0,
    }
    req = _make_request()
    req["result"]["timings"] = {"firstProgress": 800, "totalElapsed": 3000}
    conv = build_conversation([meta, req])
    asst = [c for c in conv if c["kind"] == "assistant_message"]
    assert len(asst) == 1
    assert asst[0]["first_progress_ms"] == 800
    assert asst[0]["total_elapsed_ms"] == 3000


def test_thinking_blocks() -> None:
    """Thinking text from tool call rounds is captured as reasoning."""
    meta = {
        "_vscode_meta": True,
        "sessionId": "test",
        "creationDate": 1710237600000,
        "lastMessageDate": 0,
    }
    req = _make_request(
        text="Help me",
        response_text="",
        tool_call_rounds=[
            {
                "response": "Let me check.",
                "toolCalls": [],
                "thinking": {"text": "I should look at the code first"},
                "id": "round_1",
            },
        ],
    )
    conv = build_conversation([meta, req])
    asst = [c for c in conv if c["kind"] == "assistant_message"]
    assert len(asst) == 1
    assert asst[0]["reasoning"] == "I should look at the code first"


def test_cost_multiplier() -> None:
    """Cost multiplier is extracted from result.details."""
    meta = {
        "_vscode_meta": True,
        "sessionId": "test",
        "creationDate": 1710237600000,
        "lastMessageDate": 0,
    }
    req = _make_request()
    req["result"]["details"] = "Claude Haiku 4.5 . 0.33x"
    conv = build_conversation([meta, req])
    asst = [c for c in conv if c["kind"] == "assistant_message"]
    assert len(asst) == 1
    assert asst[0]["cost_multiplier"] == "0.33x"


def test_time_spent_waiting() -> None:
    """User messages include timeSpentWaiting from request."""
    meta = {
        "_vscode_meta": True,
        "sessionId": "test",
        "creationDate": 1710237600000,
        "lastMessageDate": 0,
    }
    req = _make_request()
    req["timeSpentWaiting"] = 2500
    conv = build_conversation([meta, req])
    user_msgs = [c for c in conv if c["kind"] == "user_message"]
    assert len(user_msgs) == 1
    assert user_msgs[0]["time_spent_waiting_ms"] == 2500


def test_agent_mode() -> None:
    """User messages include agent mode label from request.agent.id."""
    meta = {
        "_vscode_meta": True,
        "sessionId": "test",
        "creationDate": 1710237600000,
        "lastMessageDate": 0,
    }
    req = _make_request()
    req["agent"] = {"id": "github.copilot.editsAgent", "name": "agent"}
    conv = build_conversation([meta, req])
    user_msgs = [c for c in conv if c["kind"] == "user_message"]
    assert user_msgs[0]["agent_mode"] == "Edit"


def test_past_tense_message() -> None:
    """Tool invocations include pastTenseMessage."""
    meta = {
        "_vscode_meta": True,
        "sessionId": "test",
        "creationDate": 1710237600000,
        "lastMessageDate": 0,
    }
    req = _make_request(text="Do it", response_text="")
    req["response"] = [
        {
            "kind": "toolInvocationSerialized",
            "toolCallId": "tc1",
            "toolId": "create_file",
            "invocationMessage": {"value": "Creating file..."},
            "pastTenseMessage": {"value": "Created 3 files"},
            "isComplete": True,
        }
    ]
    conv = build_conversation([meta, req])
    tool_starts = [c for c in conv if c["kind"] == "tool_start"]
    assert len(tool_starts) == 1
    assert tool_starts[0]["past_tense"] == "Created 3 files"


def test_past_tense_via_tool_call_rounds() -> None:
    """pastTenseMessage is matched positionally when toolCallRounds exist."""
    meta = {
        "_vscode_meta": True,
        "sessionId": "test",
        "creationDate": 1710237600000,
        "lastMessageDate": 0,
    }
    req = _make_request(text="Do it", response_text="")
    # response[] has pastTenseMessage with different IDs than toolCallRounds
    req["response"] = [
        {
            "kind": "toolInvocationSerialized",
            "toolCallId": "resp-id-1",
            "toolId": "copilot_readFile",
            "invocationMessage": {"value": "Reading file..."},
            "pastTenseMessage": {"value": "Read main.py"},
            "isComplete": True,
        },
        {
            "kind": "toolInvocationSerialized",
            "toolCallId": "resp-id-2",
            "toolId": "copilot_runCommand",
            "invocationMessage": {"value": "Running tests..."},
            "pastTenseMessage": {"value": "Ran 5 tests"},
            "isComplete": True,
        },
    ]
    req["result"]["metadata"]["toolCallRounds"] = [
        {
            "response": "Let me check.",
            "toolCalls": [
                {
                    "name": "read_file",
                    "arguments": '{"filePath": "main.py"}',
                    "id": "toolu_001__vscode-111",
                },
                {
                    "name": "run_command",
                    "arguments": '{"command": "pytest"}',
                    "id": "toolu_002__vscode-222",
                },
            ],
        },
    ]
    conv = build_conversation([meta, req])
    tool_starts = [c for c in conv if c["kind"] == "tool_start"]
    assert len(tool_starts) == 2
    assert tool_starts[0]["past_tense"] == "Read main.py"
    assert tool_starts[1]["past_tense"] == "Ran 5 tests"


def test_has_pending_edits(tmp_path: Path) -> None:
    """Session entry includes has_pending_edits flag."""
    ws_dir = tmp_path / "workspaceStorage" / "hash2"
    chat_dir = ws_dir / "chatSessions"
    chat_dir.mkdir(parents=True)
    (ws_dir / "workspace.json").write_text(json.dumps({"folder": "file:///tmp/proj"}))
    session = _make_session()
    session["hasPendingEdits"] = True
    (chat_dir / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json").write_text(json.dumps(session))
    sessions = discover_sessions(tmp_path)
    assert len(sessions) == 1
    assert sessions[0].get("has_pending_edits") is True


def test_followups() -> None:
    """Follow-up suggestions produce followups items."""
    meta = {
        "_vscode_meta": True,
        "sessionId": "test",
        "creationDate": 1710237600000,
        "lastMessageDate": 0,
    }
    req = _make_request()
    req["followups"] = [
        {"message": "Tell me more"},
        {"message": "Show an example"},
    ]
    conv = build_conversation([meta, req])
    fups = [c for c in conv if c["kind"] == "followups"]
    assert len(fups) == 1
    assert fups[0]["suggestions"] == ["Tell me more", "Show an example"]


def test_progress_task() -> None:
    """progressTaskSerialized items produce progress_task events."""
    meta = {
        "_vscode_meta": True,
        "sessionId": "test",
        "creationDate": 1710237600000,
        "lastMessageDate": 0,
    }
    req = _make_request(text="Optimize", response_text="")
    req["response"] = [
        {
            "kind": "progressTaskSerialized",
            "content": {"value": "Optimizing tool selection..."},
        },
        {"value": "Done optimizing."},
    ]
    req["result"]["metadata"]["toolCallRounds"] = []
    conv = build_conversation([meta, req])
    tasks = [c for c in conv if c["kind"] == "progress_task"]
    assert len(tasks) == 1
    assert "Optimizing" in tasks[0]["content"]


def test_prompt_token_details() -> None:
    """compute_stats aggregates prompt token breakdown."""
    meta = {"_vscode_meta": True}
    req = _make_request()
    req["result"]["metadata"]["usage"] = {
        "promptTokenDetails": {
            "system": 40,
            "toolDefinitions": 30,
            "messages": 20,
            "files": 10,
        }
    }
    stats = compute_stats([meta, req])
    ptd = stats["prompt_token_details"]
    assert ptd["system"] == 40
    assert ptd["toolDefinitions"] == 30
    assert ptd["messages"] == 20
    assert ptd["files"] == 10


# ---------------------------------------------------------------------------
# UTF-8 encoding regression (Windows cp1252 crash)
# ---------------------------------------------------------------------------


def test_parse_events_utf8_content(tmp_path: Path) -> None:
    """parse_events must read UTF-8 session files without UnicodeDecodeError."""
    ws_dir = tmp_path / "workspaceStorage" / "utf8hash"
    chat_dir = ws_dir / "chatSessions"
    chat_dir.mkdir(parents=True)
    (ws_dir / "workspace.json").write_text(
        json.dumps({"folder": "file:///tmp/proj"}), encoding="utf-8"
    )

    session = _make_session(custom_title="Héllo — 日本語 🎉")
    jsonl_path = chat_dir / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl"
    jsonl_path.write_text(
        json.dumps({"kind": 0, "v": session}) + "\n", encoding="utf-8"
    )

    events = parse_events(jsonl_path)
    assert len(events) >= 1
    assert any("Héllo" in str(e) or "日本語" in str(e) for e in events)


def test_parse_events_cp1252_invalid_bytes(tmp_path: Path) -> None:
    """parse_events must not crash on bytes that are valid UTF-8 but invalid in cp1252.

    Ł (U+0141) encodes to 0xC5 0x81 in UTF-8; 0x81 is an undefined code point in
    cp1252 and would raise UnicodeDecodeError under the system default encoding on
    Windows. This is the exact failure mode the encoding fix addresses.
    """
    ws_dir = tmp_path / "workspaceStorage" / "cp1252hash"
    chat_dir = ws_dir / "chatSessions"
    chat_dir.mkdir(parents=True)
    (ws_dir / "workspace.json").write_text(
        json.dumps({"folder": "file:///tmp/proj"}), encoding="utf-8"
    )

    session = _make_session(custom_title="Łódź project")
    jsonl_path = chat_dir / "aaaaaaaa-bbbb-cccc-dddd-ffffffffffff.jsonl"
    jsonl_path.write_text(
        json.dumps({"kind": 0, "v": session}) + "\n", encoding="utf-8"
    )

    events = parse_events(jsonl_path)
    assert len(events) >= 1
    assert any("Łódź" in str(e) for e in events)


def test_extract_searchable_text_concatenates_request_and_response(tmp_path: Path) -> None:
    from ai_ctrl_plane.vscode_parser import extract_searchable_text

    chat_dir = tmp_path / "chatSessions"
    chat_dir.mkdir(parents=True)
    session = _make_session(
        requests=[
            _make_request(text="user_prompt_token", response_text="assistant_response_token"),
        ]
    )
    p = chat_dir / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json"
    p.write_text(json.dumps(session), encoding="utf-8")

    text = extract_searchable_text(p)
    assert "user_prompt_token" in text
    assert "assistant_response_token" in text


def test_extract_searchable_text_returns_empty_for_missing_file(tmp_path: Path) -> None:
    from ai_ctrl_plane.vscode_parser import extract_searchable_text

    assert extract_searchable_text(tmp_path / "does-not-exist.json") == ""


def test_parse_events_coerces_non_list_requests(tmp_path: Path) -> None:
    """A malformed session file with ``requests`` as a non-list (dict /
    string / null) used to crash ``[meta] + requests`` with TypeError.
    Coerce to ``[]`` and filter to dict entries. Regression for PR #27
    review #49."""
    chat_dir = tmp_path / "chatSessions"
    chat_dir.mkdir(parents=True)
    p = chat_dir / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json"

    for bad in ('{"sessionId": "x", "requests": null}', '{"sessionId": "x", "requests": "not-a-list"}',
                '{"sessionId": "x", "requests": {"k": "v"}}', '{"sessionId": "x", "requests": 42}'):
        p.write_text(bad, encoding="utf-8")
        events = parse_events(p)
        # Just the meta entry — no requests, but no crash.
        assert len(events) == 1
        assert events[0].get("_vscode_meta") is True

    # Also a list with non-dict items — they get filtered out.
    p.write_text(
        '{"sessionId": "x", "requests": ["not-a-dict", null, {"requestId": "real"}]}',
        encoding="utf-8",
    )
    events = parse_events(p)
    assert len(events) == 2  # meta + the one valid dict request
    assert events[1].get("requestId") == "real"


def test_extract_searchable_text_returns_empty_for_non_dict_root(tmp_path: Path) -> None:
    """``json.load`` of a ``.json`` file can legally return a list/scalar/null
    at the root. The function should bail rather than crash on
    ``data.get(...)``. Regression for PR #27 review #41."""
    from ai_ctrl_plane.vscode_parser import extract_searchable_text

    for payload in ('["a", "b"]', '"just a string"', "42", "null", "true"):
        p = tmp_path / "session.json"
        p.write_text(payload, encoding="utf-8")
        assert extract_searchable_text(p) == "", f"payload {payload!r} should return empty"


def test_extract_searchable_text_coerces_non_list_requests(tmp_path: Path) -> None:
    """If ``requests`` is a dict / scalar / null instead of a list, the
    previous ``... or []`` walked dict keys (silently dropping all
    searchable text). Now coerced to ``[]`` like ``parse_events`` does.
    Regression for PR #27."""
    from ai_ctrl_plane.vscode_parser import extract_searchable_text

    p = tmp_path / "session.json"

    # Dict at requests[] would have iterated its keys — extract returns "".
    p.write_text(json.dumps({"sessionId": "x", "requests": {"foo": "bar"}}), encoding="utf-8")
    assert extract_searchable_text(p) == ""

    # String at requests[] would have iterated its characters.
    p.write_text(json.dumps({"sessionId": "x", "requests": "not-a-list"}), encoding="utf-8")
    assert extract_searchable_text(p) == ""

    # null requests is also tolerated.
    p.write_text(json.dumps({"sessionId": "x", "requests": None}), encoding="utf-8")
    assert extract_searchable_text(p) == ""

    # And a real list still works (sanity check).
    p.write_text(
        json.dumps({"sessionId": "x", "requests": [{"message": {"text": "hello world"}}]}),
        encoding="utf-8",
    )
    assert "hello world" in extract_searchable_text(p)


def test_build_conversation_tolerates_non_dict_intermediates() -> None:
    """``result``, ``result.metadata``, ``message``, ``agent``, ``thinking``
    and ``variableData`` could each be the wrong shape in a corrupted
    session file. ``build_conversation`` and ``compute_stats`` must not
    crash on chained ``.get`` walks. Proactive sweep for PR #27."""
    meta = {"_vscode_meta": True, "creationDate": 1, "lastMessageDate": 2, "cwd": "/x"}
    # Each request has at least one wrong-typed intermediate.
    bad_requests: list[dict] = [
        {"timestamp": 1, "result": "not-a-dict", "message": "not-a-dict", "agent": "not-a-dict"},
        {"timestamp": 2, "result": {"metadata": "not-a-dict"}, "message": {"text": None}},
        {"timestamp": 3, "result": {"metadata": {"toolCallRounds": "not-a-list"}}},
        {
            "timestamp": 4,
            "result": {"metadata": {"toolCallRounds": [{"toolCalls": "not-a-list", "thinking": "x"}]}},
        },
        {"timestamp": 5, "variableData": "not-a-dict"},
        {"timestamp": 6, "variableData": {"variables": [{"kind": "file", "value": "not-a-dict"}]}},
    ]
    # Should not raise.
    convo = build_conversation([meta, *bad_requests])
    assert isinstance(convo, list)
    stats = compute_stats([meta, *bad_requests])
    assert stats["total_events"] == len(bad_requests)
