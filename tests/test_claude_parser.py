"""Tests for the Claude Code log parser module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_ctrl_plane.claude_parser import (
    build_conversation,
    compute_stats,
    discover_sessions,
    extract_workspace,
    parse_events,
    parse_events_for_conversation,
)


def _write_jsonl(path: Path, events: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for evt in events:
            f.write(json.dumps(evt) + "\n")


def _make_user_event(content, *, ts="2026-03-12T10:00:01Z", session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", **kw):
    return {
        "type": "user",
        "message": {"role": "user", "content": content},
        "uuid": kw.get("uuid", "u1"),
        "timestamp": ts,
        "sessionId": session_id,
        "cwd": "/tmp/project",
        "version": "2.1.74",
        "gitBranch": "main",
        **kw,
    }


def _make_assistant_event(
    content_blocks,
    *,
    ts="2026-03-12T10:00:02Z",
    request_id="req_001",
    output_tokens=50,
    model="claude-opus-4-6",
    **kw,
):
    return {
        "type": "assistant",
        "message": {
            "model": model,
            "role": "assistant",
            "content": content_blocks,
            "usage": {"input_tokens": 100, "output_tokens": output_tokens},
        },
        "uuid": kw.get("uuid", "a1"),
        "requestId": request_id,
        "timestamp": ts,
        "sessionId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "cwd": "/tmp/project",
        "version": "2.1.74",
        "gitBranch": "main",
        **kw,
    }


@pytest.fixture()
def claude_project(tmp_path: Path) -> Path:
    """Create a minimal Claude project directory."""
    project_dir = tmp_path / "-Users-test-project"
    project_dir.mkdir()

    events = [
        {"type": "file-history-snapshot", "snapshot": {}, "messageId": "x"},
        {
            "type": "progress",
            "data": {
                "type": "hook_progress",
                "hookEvent": "PreToolUse",
                "hookName": "PreToolUse:Bash",
                "command": "echo ok",
            },
            "uuid": "p0",
            "timestamp": "2026-03-12T10:00:00Z",
            "sessionId": "s1",
            "cwd": "/tmp/project",
            "version": "2.1.74",
            "gitBranch": "main",
        },
        _make_user_event("Hello, help me write tests", uuid="u1", ts="2026-03-12T10:00:01Z"),
        _make_assistant_event(
            [{"type": "thinking", "thinking": "Let me think about this...", "signature": "sig1"}],
            ts="2026-03-12T10:00:02Z",
            request_id="req_001",
            uuid="a1",
        ),
        _make_assistant_event(
            [{"type": "text", "text": "Sure, I will help you write tests."}],
            ts="2026-03-12T10:00:03Z",
            request_id="req_001",
            uuid="a2",
            output_tokens=30,
        ),
        _make_assistant_event(
            [{"type": "tool_use", "id": "toolu_01", "name": "Bash", "input": {"command": "ls"}}],
            ts="2026-03-12T10:00:04Z",
            request_id="req_001",
            uuid="a3",
            output_tokens=50,
        ),
        _make_user_event(
            [{"type": "tool_result", "tool_use_id": "toolu_01", "content": "file1.py\nfile2.py", "is_error": False}],
            uuid="u2",
            ts="2026-03-12T10:00:05Z",
        ),
        _make_assistant_event(
            [{"type": "text", "text": "I can see two files."}],
            ts="2026-03-12T10:00:06Z",
            request_id="req_002",
            uuid="a4",
            output_tokens=20,
        ),
        _make_user_event("Thanks!", uuid="u3", ts="2026-03-12T10:00:07Z"),
    ]

    _write_jsonl(project_dir / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl", events)
    return tmp_path


def test_discover_sessions(claude_project: Path) -> None:
    sessions = discover_sessions(claude_project)
    assert len(sessions) == 1
    s = sessions[0]
    assert s["id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert s["source"] == "claude"
    assert s["branch"] == "main"
    assert s["cwd"] == "/tmp/project"
    assert s["model"] == "claude-opus-4-6"


def test_parse_events_filters_snapshots(claude_project: Path) -> None:
    jsonl = claude_project / "-Users-test-project" / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl"
    events = parse_events(jsonl)
    types = {e["type"] for e in events}
    assert "file-history-snapshot" not in types
    assert "user" in types
    assert "assistant" in types


def test_parse_events_for_conversation(claude_project: Path) -> None:
    """parse_events_for_conversation keeps progress and snapshot events."""
    jsonl = claude_project / "-Users-test-project" / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl"
    # Default filters out progress/file-history-snapshot
    default_types = {e["type"] for e in parse_events(jsonl)}
    assert "progress" not in default_types
    # Conversation loader keeps them
    full = parse_events_for_conversation(jsonl)
    full_types = {e["type"] for e in full}
    assert "progress" in full_types
    assert "file-history-snapshot" in full_types


def test_build_conversation_session_start(claude_project: Path) -> None:
    jsonl = claude_project / "-Users-test-project" / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl"
    events = parse_events(jsonl)
    conv = build_conversation(events)
    assert conv[0]["kind"] == "session_start"
    assert conv[0]["branch"] == "main"
    assert conv[0]["version"] == "2.1.74"


def test_build_conversation_user_message(claude_project: Path) -> None:
    jsonl = claude_project / "-Users-test-project" / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl"
    events = parse_events(jsonl)
    conv = build_conversation(events)
    user_msgs = [c for c in conv if c["kind"] == "user_message"]
    assert len(user_msgs) == 2
    assert user_msgs[0]["content"] == "Hello, help me write tests"
    assert user_msgs[1]["content"] == "Thanks!"


def test_build_conversation_assistant_with_reasoning(claude_project: Path) -> None:
    jsonl = claude_project / "-Users-test-project" / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl"
    events = parse_events(jsonl)
    conv = build_conversation(events)
    asst_msgs = [c for c in conv if c["kind"] == "assistant_message"]
    # req_001 (with thinking+text+tool_use) and req_002 (text only)
    assert len(asst_msgs) == 2
    assert asst_msgs[0]["reasoning"] == "Let me think about this..."
    assert "help you write tests" in asst_msgs[0]["content"]
    assert asst_msgs[0]["output_tokens"] == 50


def test_build_conversation_tool_start(claude_project: Path) -> None:
    jsonl = claude_project / "-Users-test-project" / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl"
    events = parse_events(jsonl)
    conv = build_conversation(events)
    tool_starts = [c for c in conv if c["kind"] == "tool_start"]
    assert len(tool_starts) == 1
    assert tool_starts[0]["tool_name"] == "Bash"
    assert tool_starts[0]["arguments"] == {"command": "ls"}
    assert tool_starts[0]["tool_call_id"] == "toolu_01"


def test_build_conversation_tool_complete(claude_project: Path) -> None:
    jsonl = claude_project / "-Users-test-project" / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl"
    events = parse_events(jsonl)
    conv = build_conversation(events)
    tool_completes = [c for c in conv if c["kind"] == "tool_complete"]
    assert len(tool_completes) == 1
    assert tool_completes[0]["success"] is True
    assert "file1.py" in tool_completes[0]["result"]
    assert tool_completes[0]["tool_call_id"] == "toolu_01"


def test_build_conversation_tool_error() -> None:
    events = [
        _make_user_event("do something"),
        _make_assistant_event(
            [{"type": "tool_use", "id": "toolu_err", "name": "Bash", "input": {"command": "fail"}}],
            request_id="req_err",
        ),
        _make_user_event(
            [{"type": "tool_result", "tool_use_id": "toolu_err", "content": "command failed", "is_error": True}],
            ts="2026-03-12T10:00:05Z",
        ),
    ]
    conv = build_conversation(events)
    tc = [c for c in conv if c["kind"] == "tool_complete"]
    assert len(tc) == 1
    assert tc[0]["success"] is False


def test_build_conversation_skips_meta() -> None:
    events = [
        _make_user_event("<local-command-caveat>caveat text</local-command-caveat>", isMeta=True),
        _make_user_event("Real message"),
    ]
    conv = build_conversation(events)
    user_msgs = [c for c in conv if c["kind"] == "user_message"]
    assert len(user_msgs) == 1
    assert user_msgs[0]["content"] == "Real message"


def test_build_conversation_xml_context_with_user_text() -> None:
    """XML context tags are split into notification + user message."""
    events = [
        _make_user_event(
            "<ide_opened_file>The user opened foo.py in the IDE.</ide_opened_file> Heya, Please fix the TF diffs."
        ),
    ]
    conv = build_conversation(events)
    notifs = [c for c in conv if c["kind"] == "notification"]
    user_msgs = [c for c in conv if c["kind"] == "user_message"]
    assert len(notifs) == 1
    assert "foo.py" in notifs[0]["message"]
    assert len(user_msgs) == 1
    assert "Heya, Please fix the TF diffs." in user_msgs[0]["content"]


def test_build_conversation_xml_only_no_user_text() -> None:
    """Pure XML context with no trailing user text becomes just a notification."""
    events = [
        _make_user_event("<command-name>/review</command-name><command-args></command-args>"),
    ]
    conv = build_conversation(events)
    notifs = [c for c in conv if c["kind"] == "notification"]
    user_msgs = [c for c in conv if c["kind"] == "user_message"]
    assert len(notifs) == 1
    assert "/review" in notifs[0]["message"]
    assert len(user_msgs) == 0


def test_build_conversation_requestid_merge() -> None:
    """Multiple assistant entries with same requestId produce one assistant_message."""
    events = [
        _make_user_event("Hi"),
        _make_assistant_event(
            [{"type": "thinking", "thinking": "hmm", "signature": "s"}],
            request_id="req_X",
            uuid="a1",
            ts="2026-03-12T10:00:02Z",
            output_tokens=0,
        ),
        _make_assistant_event(
            [{"type": "text", "text": "Hello!"}],
            request_id="req_X",
            uuid="a2",
            ts="2026-03-12T10:00:03Z",
            output_tokens=10,
        ),
        _make_assistant_event(
            [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file": "a.py"}}],
            request_id="req_X",
            uuid="a3",
            ts="2026-03-12T10:00:04Z",
            output_tokens=20,
        ),
    ]
    conv = build_conversation(events)
    asst = [c for c in conv if c["kind"] == "assistant_message"]
    assert len(asst) == 1
    assert asst[0]["content"] == "Hello!"
    assert asst[0]["reasoning"] == "hmm"
    assert len(asst[0]["tool_requests"]) == 1
    assert asst[0]["tool_requests"][0]["toolName"] == "Read"
    assert asst[0]["output_tokens"] == 20  # last wins

    tools = [c for c in conv if c["kind"] == "tool_start"]
    assert len(tools) == 1


def test_build_conversation_session_end(claude_project: Path) -> None:
    jsonl = claude_project / "-Users-test-project" / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl"
    events = parse_events(jsonl)
    conv = build_conversation(events)
    assert conv[-1]["kind"] == "session_end"


def test_compute_stats(claude_project: Path) -> None:
    jsonl = claude_project / "-Users-test-project" / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl"
    events = parse_events(jsonl)
    stats = compute_stats(events)
    assert stats["user_messages"] == 2  # "Hello..." and "Thanks!"
    assert stats["assistant_messages"] == 2  # req_001 and req_002
    assert stats["total_tool_calls"] == 1
    assert stats["tool_calls"]["Bash"] == 1
    assert stats["total_output_tokens"] == 50 + 20  # req_001=50, req_002=20
    assert stats["turns"] == 2


def test_extract_workspace(claude_project: Path) -> None:
    jsonl = claude_project / "-Users-test-project" / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl"
    events = parse_events(jsonl)
    ws = extract_workspace(events)
    assert ws["id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert ws["cwd"] == "/tmp/project"
    assert ws["branch"] == "main"
    assert ws["model"] == "claude-opus-4-6"


def test_cache_token_stats() -> None:
    """compute_stats tracks cache read/creation tokens per requestId."""
    events = [
        _make_user_event("Hi"),
        {
            "type": "assistant",
            "message": {
                "model": "claude-opus-4-6",
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello"}],
                "usage": {
                    "input_tokens": 500,
                    "output_tokens": 30,
                    "cache_read_input_tokens": 200,
                    "cache_creation_input_tokens": 100,
                },
            },
            "uuid": "a1",
            "requestId": "req_cache",
            "timestamp": "2026-03-12T10:00:02Z",
            "sessionId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "cwd": "/tmp/project",
            "version": "2.1.74",
            "gitBranch": "main",
        },
    ]
    stats = compute_stats(events)
    assert stats["cache_read_tokens"] == 200
    assert stats["cache_creation_tokens"] == 100
    assert stats["total_input_tokens"] == 500


def test_stop_reason_on_message() -> None:
    """build_conversation includes stop_reason from assistant messages."""
    events = [
        _make_user_event("Write a long essay"),
        {
            "type": "assistant",
            "message": {
                "model": "claude-opus-4-6",
                "role": "assistant",
                "content": [{"type": "text", "text": "Here is..."}],
                "usage": {"input_tokens": 10, "output_tokens": 100},
                "stop_reason": "max_tokens",
            },
            "uuid": "a1",
            "requestId": "req_stop",
            "timestamp": "2026-03-12T10:00:02Z",
            "sessionId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "cwd": "/tmp/project",
            "version": "2.1.74",
            "gitBranch": "main",
        },
    ]
    conv = build_conversation(events)
    asst = [c for c in conv if c["kind"] == "assistant_message"]
    assert len(asst) == 1
    assert asst[0]["stop_reason"] == "max_tokens"


def test_service_tier_stats() -> None:
    """compute_stats tracks service_tier from usage."""
    events = [
        _make_user_event("Hi"),
        {
            "type": "assistant",
            "message": {
                "model": "claude-opus-4-6",
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello"}],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "service_tier": "priority",
                },
            },
            "uuid": "a1",
            "requestId": "req_st",
            "timestamp": "2026-03-12T10:00:02Z",
            "sessionId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "cwd": "/tmp/project",
            "version": "2.1.74",
            "gitBranch": "main",
        },
    ]
    stats = compute_stats(events)
    assert stats["service_tier"] == "priority"


def test_permission_mode() -> None:
    """build_conversation includes permissionMode on user messages."""
    events = [
        {
            "type": "user",
            "message": {"role": "user", "content": "Do it"},
            "permissionMode": "acceptEdits",
            "uuid": "u1",
            "timestamp": "2026-03-12T10:00:01Z",
            "sessionId": "s1",
            "cwd": "/tmp",
            "version": "2.1.74",
            "gitBranch": "main",
        },
    ]
    conv = build_conversation(events)
    user_msgs = [c for c in conv if c["kind"] == "user_message"]
    assert len(user_msgs) == 1
    assert user_msgs[0]["permission_mode"] == "acceptEdits"


def test_is_sidechain() -> None:
    """build_conversation includes isSidechain on messages."""
    events = [
        {
            "type": "user",
            "message": {"role": "user", "content": "Side task"},
            "isSidechain": True,
            "uuid": "u1",
            "timestamp": "2026-03-12T10:00:01Z",
            "sessionId": "s1",
            "cwd": "/tmp",
            "version": "2.1.74",
            "gitBranch": "main",
        },
        _make_assistant_event(
            [{"type": "text", "text": "Done"}],
            request_id="req_sc",
            isSidechain=True,
        ),
    ]
    conv = build_conversation(events)
    user_msgs = [c for c in conv if c["kind"] == "user_message"]
    asst_msgs = [c for c in conv if c["kind"] == "assistant_message"]
    assert user_msgs[0]["is_sidechain"] is True
    assert asst_msgs[0]["is_sidechain"] is True


def test_hook_events() -> None:
    """progress events with hook_progress type produce hook items."""
    events = [
        _make_user_event("Hi"),
        {
            "type": "progress",
            "data": {
                "type": "hook_progress",
                "hookEvent": "PostToolUse",
                "hookName": "PostToolUse:Read",
                "command": "callback",
            },
            "uuid": "p1",
            "timestamp": "2026-03-12T10:00:02Z",
            "sessionId": "s1",
            "cwd": "/tmp",
            "version": "2.1.74",
            "gitBranch": "main",
        },
    ]
    conv = build_conversation(events)
    hooks = [c for c in conv if c["kind"] == "hook"]
    assert len(hooks) == 1
    assert hooks[0]["hook_event"] == "PostToolUse"
    assert hooks[0]["hook_name"] == "PostToolUse:Read"


def test_file_snapshot() -> None:
    """file-history-snapshot with tracked files produces file_snapshot."""
    events = [
        _make_user_event("Hi"),
        {
            "type": "file-history-snapshot",
            "messageId": "m1",
            "snapshot": {
                "trackedFileBackups": {
                    "foo.py": {"backupFileName": "abc"},
                    "bar.py": {"backupFileName": "def"},
                },
                "timestamp": "2026-03-12T10:00:02Z",
            },
            "timestamp": "2026-03-12T10:00:02Z",
        },
    ]
    conv = build_conversation(events)
    snaps = [c for c in conv if c["kind"] == "file_snapshot"]
    assert len(snaps) == 1
    assert snaps[0]["file_count"] == 2
    assert "foo.py" in snaps[0]["files"]


def test_last_prompt() -> None:
    """last-prompt events produce last_prompt items."""
    events = [
        _make_user_event("Hi"),
        {
            "type": "last-prompt",
            "lastPrompt": "Fix the bug in auth",
            "sessionId": "s1",
            "timestamp": "2026-03-12T10:00:02Z",
        },
    ]
    conv = build_conversation(events)
    lp = [c for c in conv if c["kind"] == "last_prompt"]
    assert len(lp) == 1
    assert "Fix the bug" in lp[0]["content"]


def test_subagent_count_in_stats() -> None:
    """compute_stats counts Agent tool calls as subagents."""
    events = [
        _make_user_event("Do something complex"),
        _make_assistant_event(
            [
                {
                    "type": "tool_use",
                    "id": "toolu_agent1",
                    "name": "Agent",
                    "input": {"description": "Search code", "prompt": "Find the bug"},
                },
                {"type": "tool_use", "id": "toolu_bash1", "name": "Bash", "input": {"command": "ls"}},
            ],
            request_id="req_sub1",
        ),
        _make_user_event(
            [
                {"type": "tool_result", "tool_use_id": "toolu_agent1", "content": "Found the bug"},
                {"type": "tool_result", "tool_use_id": "toolu_bash1", "content": "file1.py"},
            ],
            ts="2026-03-12T10:00:05Z",
        ),
        _make_assistant_event(
            [
                {"type": "tool_use", "id": "toolu_agent2", "name": "dispatch_agent", "input": {"prompt": "Fix it"}},
            ],
            request_id="req_sub2",
            ts="2026-03-12T10:00:06Z",
        ),
    ]
    stats = compute_stats(events)
    assert stats["subagents"] == 2  # Agent + dispatch_agent
    assert stats["total_tool_calls"] == 3  # Agent + Bash + dispatch_agent
    assert stats["tool_calls"]["Agent"] == 1
    assert stats["tool_calls"]["Bash"] == 1
    assert stats["tool_calls"]["dispatch_agent"] == 1


def test_subagent_conversation_events() -> None:
    """build_conversation emits subagent_start / subagent_complete for Agent tools."""
    events = [
        _make_user_event("Run an agent"),
        _make_assistant_event(
            [
                {
                    "type": "tool_use",
                    "id": "toolu_ag",
                    "name": "Agent",
                    "input": {"description": "Explore codebase", "prompt": "Look around"},
                },
            ],
            request_id="req_ag",
        ),
        _make_user_event(
            [{"type": "tool_result", "tool_use_id": "toolu_ag", "content": "Done exploring"}],
            ts="2026-03-12T10:00:05Z",
        ),
    ]
    conv = build_conversation(events)
    starts = [c for c in conv if c["kind"] == "subagent_start"]
    completes = [c for c in conv if c["kind"] == "subagent_complete"]
    assert len(starts) == 1
    assert starts[0]["agent_name"] == "Explore codebase"
    assert starts[0]["tool_call_id"] == "toolu_ag"
    assert len(completes) == 1
    assert completes[0]["tool_call_id"] == "toolu_ag"

    # Regular tool_start/tool_complete should NOT be emitted for Agent
    tool_starts = [c for c in conv if c["kind"] == "tool_start"]
    tool_completes = [c for c in conv if c["kind"] == "tool_complete"]
    assert all(ts["tool_name"] != "Agent" for ts in tool_starts)
    # The tool_complete for the agent should be subagent_complete instead
    assert all(tc.get("tool_call_id") != "toolu_ag" for tc in tool_completes)
