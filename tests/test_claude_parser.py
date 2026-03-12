"""Tests for the Claude Code log parser module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_log_viewer.claude_parser import (
    build_conversation,
    compute_stats,
    discover_sessions,
    extract_workspace,
    parse_events,
)


def _write_jsonl(path: Path, events: list[dict]) -> None:
    with open(path, "w") as f:
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


def _make_assistant_event(content_blocks, *, ts="2026-03-12T10:00:02Z", request_id="req_001", output_tokens=50, model="claude-opus-4-6", **kw):
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
        _make_user_event("Hello, help me write tests", uuid="u1", ts="2026-03-12T10:00:01Z"),
        _make_assistant_event(
            [{"type": "thinking", "thinking": "Let me think about this...", "signature": "sig1"}],
            ts="2026-03-12T10:00:02Z", request_id="req_001", uuid="a1",
        ),
        _make_assistant_event(
            [{"type": "text", "text": "Sure, I will help you write tests."}],
            ts="2026-03-12T10:00:03Z", request_id="req_001", uuid="a2", output_tokens=30,
        ),
        _make_assistant_event(
            [{"type": "tool_use", "id": "toolu_01", "name": "Bash", "input": {"command": "ls"}}],
            ts="2026-03-12T10:00:04Z", request_id="req_001", uuid="a3", output_tokens=50,
        ),
        _make_user_event(
            [{"type": "tool_result", "tool_use_id": "toolu_01", "content": "file1.py\nfile2.py", "is_error": False}],
            uuid="u2", ts="2026-03-12T10:00:05Z",
        ),
        _make_assistant_event(
            [{"type": "text", "text": "I can see two files."}],
            ts="2026-03-12T10:00:06Z", request_id="req_002", uuid="a4", output_tokens=20,
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
            "<ide_opened_file>The user opened foo.py in the IDE.</ide_opened_file> "
            "Heya, Please fix the TF diffs."
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
            request_id="req_X", uuid="a1", ts="2026-03-12T10:00:02Z", output_tokens=0,
        ),
        _make_assistant_event(
            [{"type": "text", "text": "Hello!"}],
            request_id="req_X", uuid="a2", ts="2026-03-12T10:00:03Z", output_tokens=10,
        ),
        _make_assistant_event(
            [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file": "a.py"}}],
            request_id="req_X", uuid="a3", ts="2026-03-12T10:00:04Z", output_tokens=20,
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
