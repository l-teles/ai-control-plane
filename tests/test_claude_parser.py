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


def test_build_conversation_slash_command_emits_structured_kind() -> None:
    """Slash command markup is surfaced as a dedicated ``slash_command`` item
    with command name, args, stdout, and stderr exposed as fields rather than
    flattened into a notification string."""
    events = [
        _make_user_event(
            "<command-name>/review</command-name>"
            "<command-message>review</command-message>"
            "<command-args>--all</command-args>"
            "<local-command-stdout>10 issues found</local-command-stdout>"
            "<local-command-stderr></local-command-stderr>"
        ),
    ]
    conv = build_conversation(events)
    slash = [c for c in conv if c["kind"] == "slash_command"]
    assert len(slash) == 1
    assert slash[0]["command"] == "/review"
    assert slash[0]["args"] == "--all"
    assert slash[0]["stdout"] == "10 issues found"
    assert slash[0]["stderr"] == ""
    # Should NOT also appear as a notification or user_message
    assert not [c for c in conv if c["kind"] == "notification"]
    assert not [c for c in conv if c["kind"] == "user_message"]


def test_build_conversation_xml_without_command_name_is_still_notification() -> None:
    """Non-slash XML markup keeps the old notification + user_message split."""
    events = [
        _make_user_event("<ide_opened_file>foo.py</ide_opened_file>fix this"),
    ]
    conv = build_conversation(events)
    notifs = [c for c in conv if c["kind"] == "notification"]
    user_msgs = [c for c in conv if c["kind"] == "user_message"]
    assert len(notifs) == 1
    assert len(user_msgs) == 1
    assert user_msgs[0]["content"] == "fix this"


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


def test_build_conversation_reorders_resumed_session_by_dag() -> None:
    """A session resumed from message ``b`` (so original branch ``c=>d`` and
    resumed branch ``e=>f`` both share parent ``b``) should render with each
    branch's children adjacent to their parent — not interleaved by file order.
    """
    events = [
        _make_user_event("hi", uuid="a", ts="2026-04-26T10:00:00Z"),
        _make_assistant_event(
            [{"type": "text", "text": "hello"}],
            uuid="b",
            request_id="rb",
            ts="2026-04-26T10:00:01Z",
            parentUuid="a",
        ),
        # Original branch (c=>d), written first
        _make_user_event("first follow-up", uuid="c", ts="2026-04-26T10:00:02Z", parentUuid="b"),
        _make_assistant_event(
            [{"type": "text", "text": "answer for first"}],
            uuid="d",
            request_id="rd",
            ts="2026-04-26T10:00:03Z",
            parentUuid="c",
        ),
        # Resumed branch (e=>f), written later but parents off `b` again
        _make_user_event("second follow-up", uuid="e", ts="2026-04-26T10:00:04Z", parentUuid="b"),
        _make_assistant_event(
            [{"type": "text", "text": "answer for second"}],
            uuid="f",
            request_id="rf",
            ts="2026-04-26T10:00:05Z",
            parentUuid="e",
        ),
    ]
    conv = build_conversation(events)
    # Pull out the user_message contents in conversation order.
    user_msgs = [c["content"] for c in conv if c.get("kind") == "user_message"]
    # Branch c=>d should be fully visited before branch e=>f.
    assert user_msgs.index("first follow-up") < user_msgs.index("second follow-up")
    assistant_texts = [c["content"] for c in conv if c.get("kind") == "assistant_message"]
    # The "hello" reply (b) precedes both branch replies; "answer for first"
    # comes immediately after its user prompt and before "answer for second".
    assert assistant_texts.index("hello") < assistant_texts.index("answer for first")
    assert assistant_texts.index("answer for first") < assistant_texts.index("answer for second")


def test_extract_searchable_text_pulls_user_assistant_thinking_and_tool_results(tmp_path: Path) -> None:
    from ai_ctrl_plane.claude_parser import extract_searchable_text

    sid = "11111111-2222-3333-4444-555555555555"
    jsonl = tmp_path / f"{sid}.jsonl"
    events = [
        _make_user_event("plain user prompt", uuid="u1"),
        _make_assistant_event(
            [
                {"type": "thinking", "thinking": "internal_reasoning_token"},
                {"type": "text", "text": "assistant_reply_token"},
            ],
            uuid="a1",
        ),
        _make_user_event(
            [{"type": "tool_result", "tool_use_id": "t1", "content": "tool_output_token"}],
            uuid="u2",
        ),
        {"type": "summary", "summary": "summary_token", "leafUuid": "a1"},
    ]
    _write_jsonl(jsonl, events)
    text = extract_searchable_text(jsonl)
    assert "plain user prompt" in text
    assert "internal_reasoning_token" in text
    assert "assistant_reply_token" in text
    assert "tool_output_token" in text
    assert "summary_token" in text


def test_extract_searchable_text_strips_xml_markup(tmp_path: Path) -> None:
    """Slash-command and IDE-context tags add noise; they shouldn't appear
    as bare angle brackets in the indexed text."""
    from ai_ctrl_plane.claude_parser import extract_searchable_text

    sid = "11111111-2222-3333-4444-555555555555"
    jsonl = tmp_path / f"{sid}.jsonl"
    _write_jsonl(jsonl, [_make_user_event("<command-name>/test</command-name>visible_text", uuid="u1")])
    text = extract_searchable_text(jsonl)
    assert "visible_text" in text
    assert "<command-name>" not in text
    assert "</command-name>" not in text


def test_extract_searchable_text_preserves_non_context_angle_brackets(tmp_path: Path) -> None:
    """Generics like ``List<int>``, ``Function<T, R>``, and HTML/JSX
    samples must stay searchable — only Claude-injected context tags get
    scrubbed. Regression for PR #27 review comment 14."""
    from ai_ctrl_plane.claude_parser import extract_searchable_text

    sid = "11111111-2222-3333-4444-555555555555"
    jsonl = tmp_path / f"{sid}.jsonl"
    _write_jsonl(
        jsonl,
        [
            _make_user_event(
                "Use List<int> with Function<T, R> and inside <div>HTML</div> and <component prop={value} />",
                uuid="u1",
            ),
        ],
    )
    text = extract_searchable_text(jsonl)
    assert "List<int>" in text
    assert "Function<T, R>" in text
    assert "<div>HTML</div>" in text
    assert "<component" in text


def test_count_permissions_handles_non_string_cwd() -> None:
    """``cwd`` from a malformed transcript event can be non-string;
    constructing a Path would TypeError. Coerce to safe defaults.
    Regression for PR #27 review #53."""
    from ai_ctrl_plane.claude_parser import _count_permissions

    for bad in (None, 42, [], {"x": 1}, True):
        result = _count_permissions(bad)
        assert result == {"allow": 0, "deny": 0, "ask": 0}


def test_extract_searchable_text_returns_empty_for_missing_file(tmp_path: Path) -> None:
    from ai_ctrl_plane.claude_parser import extract_searchable_text

    assert extract_searchable_text(tmp_path / "nope.jsonl") == ""


def test_extract_searchable_text_survives_corrupt_jsonl_lines(tmp_path: Path) -> None:
    """A line that JSON-parses to a non-dict (``null``, ``[]``, scalar)
    must not crash extraction — the function should skip the bad line
    and continue indexing the rest of the session. Regression for PR
    #27 review #44, plus a broader sweep across every claude_parser
    function that scans JSONL line-by-line."""
    from ai_ctrl_plane.claude_parser import (
        _first_metadata,
        _last_timestamp,
        _scan_summaries,
        _scan_token_usage,
        extract_searchable_text,
        parse_events,
    )

    sid = "11111111-2222-3333-4444-555555555555"
    jsonl = tmp_path / f"{sid}.jsonl"
    # Mix valid events with several corrupt-but-valid-JSON lines.
    valid_user = _make_user_event("findable_token", uuid="u1", session_id=sid)
    valid_assistant = _make_assistant_event(
        [{"type": "text", "text": "answer_token"}], uuid="a1", request_id="r1"
    )
    summary_event = {"type": "summary", "summary": "summary_token", "leafUuid": "a1"}
    lines = [
        json.dumps(valid_user),
        "null",  # JSON-decodes to None
        "[]",  # JSON-decodes to a list
        '"just a string"',  # JSON-decodes to a str
        "42",  # JSON-decodes to an int
        json.dumps(valid_assistant),
        "true",  # JSON-decodes to a bool
        json.dumps(summary_event),
    ]
    jsonl.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # None of these should raise; the corrupt lines are silently skipped
    # and the valid events surface as expected.
    text = extract_searchable_text(jsonl)
    assert "findable_token" in text
    assert "answer_token" in text
    assert "summary_token" in text

    events = parse_events(jsonl)
    types = [e.get("type") for e in events]
    assert "user" in types
    assert "assistant" in types

    meta = _first_metadata(jsonl)
    assert meta.get("sessionId") == sid

    assert _last_timestamp(jsonl)  # not empty
    tokens = _scan_token_usage(jsonl)
    assert tokens["output_tokens"] >= 0  # didn't crash
    summaries = _scan_summaries(jsonl)
    assert any("summary_token" in s for _, s in summaries)


def test_extract_searchable_text_skips_non_string_block_values(tmp_path: Path) -> None:
    """A malformed transcript with ``{"text": null}``, ``{"thinking": 42}``,
    or non-string ``tool_result.content`` would crash ``len()`` / ``join``
    and break FTS indexing for the entire session. Each non-string value
    must be skipped silently. Regression for PR #27 review #33."""
    from ai_ctrl_plane.claude_parser import extract_searchable_text

    sid = "11111111-2222-3333-4444-555555555555"
    jsonl = tmp_path / f"{sid}.jsonl"
    events = [
        # User message with a mix of valid and malformed text blocks.
        _make_user_event(
            [
                {"type": "text", "text": "user_visible"},
                {"type": "text", "text": None},
                {"type": "text", "text": 42},
            ],
            uuid="u1",
        ),
        # Assistant with malformed thinking / text + a tool_use we ignore.
        _make_assistant_event(
            [
                {"type": "thinking", "thinking": None},
                {"type": "thinking", "thinking": "internal_reasoning"},
                {"type": "thinking", "thinking": ["bad"]},
                {"type": "text", "text": "assistant_visible"},
            ],
            uuid="a1",
        ),
        # Malformed tool_result with non-string content + non-string text in inner blocks.
        _make_user_event(
            [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu1",
                    "content": [
                        {"type": "text", "text": "tool_visible"},
                        {"type": "text", "text": None},
                        {"type": "text", "text": ["bad"]},
                    ],
                },
                {"type": "tool_result", "tool_use_id": "tu2", "content": 42},  # bad content type
            ],
            uuid="u2",
        ),
        # Malformed summary entry.
        {"type": "summary", "summary": None, "leafUuid": "u1"},
    ]
    _write_jsonl(jsonl, events)

    # No raise — only the string-typed payloads end up in the index.
    text = extract_searchable_text(jsonl)
    assert "user_visible" in text
    assert "internal_reasoning" in text
    assert "assistant_visible" in text
    assert "tool_visible" in text


def test_summary_entry_overrides_first_user_message_in_session_list(tmp_path: Path) -> None:
    """When Claude auto-generated a `summary` entry for a session, the most
    recent one should be used as the session label rather than the first
    user message."""
    project_dir = tmp_path / "-Users-test-project"
    project_dir.mkdir()
    sid = "11111111-2222-3333-4444-555555555555"
    events = [
        _make_user_event("How do I write tests?", uuid="u1", session_id=sid),
        _make_assistant_event(
            [{"type": "text", "text": "Here's how"}], request_id="r1", uuid="a1"
        ),
        {"type": "summary", "summary": "Walked through how to write pytest fixtures", "leafUuid": "a1"},
    ]
    enriched = [{"sessionId": sid, **e} if e.get("type") != "summary" else e for e in events]
    _write_jsonl(project_dir / f"{sid}.jsonl", enriched)
    sessions = discover_sessions(tmp_path)
    assert len(sessions) == 1
    assert sessions[0]["summary"] == "Walked through how to write pytest fixtures"


def test_parse_subagent_transcripts_skips_non_dict_meta(tmp_path: Path) -> None:
    """A subagent ``.meta.json`` whose root is a list / scalar / null
    must not crash subagent loading — skip the bad file silently and
    process the rest. Regression for PR #27 review #45."""
    from ai_ctrl_plane.claude_parser import parse_subagent_transcripts

    sid = "11111111-2222-3333-4444-555555555555"
    project_dir = tmp_path / "-Users-test"
    project_dir.mkdir()
    session_jsonl = project_dir / f"{sid}.jsonl"
    sub_dir = project_dir / sid / "subagents"
    sub_dir.mkdir(parents=True)

    # Bad meta files at every shape JSON allows at the root.
    for i, payload in enumerate(["null", "[]", '"just a string"', "42", "true"]):
        (sub_dir / f"agent-bad{i}.meta.json").write_text(payload, encoding="utf-8")
        (sub_dir / f"agent-bad{i}.jsonl").write_text("", encoding="utf-8")

    # And one valid meta + transcript so we can verify it's still loaded.
    (sub_dir / "agent-good.meta.json").write_text(
        json.dumps({"agentType": "Explore", "description": "Find the bug"}), encoding="utf-8"
    )
    _write_jsonl(
        sub_dir / "agent-good.jsonl",
        [_make_user_event("Find the bug", uuid="su1", session_id=sid, isSidechain=True)],
    )

    subs = parse_subagent_transcripts(session_jsonl)
    # Bad meta files are silently skipped; the good one is loaded.
    assert "Find the bug" in subs
    assert len(subs) == 1


def test_subagent_transcript_loaded_and_attached(tmp_path: Path) -> None:
    """When a session has subagent files in `<session>/subagents/`, the
    matching `Agent` tool_use should carry an inlined transcript."""
    from ai_ctrl_plane.claude_parser import parse_subagent_transcripts

    sid = "11111111-2222-3333-4444-555555555555"
    project_dir = tmp_path / "-Users-test"
    project_dir.mkdir()
    session_jsonl = project_dir / f"{sid}.jsonl"
    sub_dir = project_dir / sid / "subagents"
    sub_dir.mkdir(parents=True)

    # Subagent meta + transcript
    (sub_dir / "agent-deadbeef.meta.json").write_text(
        json.dumps({"agentType": "Explore", "description": "Find the bug"}),
        encoding="utf-8",
    )
    _write_jsonl(
        sub_dir / "agent-deadbeef.jsonl",
        [
            _make_user_event("Find the bug", uuid="su1", session_id=sid, isSidechain=True, agentId="deadbeef"),
            _make_assistant_event(
                [{"type": "text", "text": "Found it in foo.py:42"}],
                request_id="rsub",
                uuid="sa1",
            ),
        ],
    )

    # Verify the helper finds and parses both files
    subs = parse_subagent_transcripts(session_jsonl)
    assert "Find the bug" in subs
    assert subs["Find the bug"]["agent_type"] == "Explore"
    assert len(subs["Find the bug"]["events"]) == 2

    # Verify the conversation builder attaches the transcript on subagent_start
    main_events = [
        _make_user_event("Help me find a bug", uuid="u1", session_id=sid),
        _make_assistant_event(
            [
                {
                    "type": "tool_use",
                    "id": "toolu_x",
                    "name": "Agent",
                    "input": {"description": "Find the bug", "prompt": "Look in foo.py"},
                },
            ],
            request_id="r1",
            uuid="a1",
        ),
    ]
    conv = build_conversation(main_events, subagent_transcripts=subs)
    starts = [c for c in conv if c["kind"] == "subagent_start"]
    assert len(starts) == 1
    assert starts[0]["agent_type"] == "Explore"
    assert "transcript" in starts[0]
    assert isinstance(starts[0]["transcript"], list)
    inner_msgs = [c for c in starts[0]["transcript"] if c["kind"] == "assistant_message"]
    assert any("Found it in foo.py:42" in m["content"] for m in inner_msgs)


def test_subagent_transcript_absent_when_no_match(tmp_path: Path) -> None:
    """If no subagent file matches the Agent tool_use's description, the
    item still emits — just without a `transcript` field."""
    main_events = [
        _make_user_event("Help"),
        _make_assistant_event(
            [
                {
                    "type": "tool_use",
                    "id": "toolu_y",
                    "name": "Agent",
                    "input": {"description": "Some other task", "prompt": "..."},
                },
            ],
            request_id="r1",
        ),
    ]
    conv = build_conversation(main_events, subagent_transcripts={"a different task": {"events": []}})
    starts = [c for c in conv if c["kind"] == "subagent_start"]
    assert len(starts) == 1
    assert "transcript" not in starts[0]


def test_subagent_handles_non_dict_input() -> None:
    """Agent / dispatch_agent tool_use ``input`` is meant to be a dict but a
    malformed transcript or an MCP server could emit a list / string. The
    builder must coerce to ``{}`` instead of crashing on
    ``agent_input.get(...)`` or ``sub_lookup.get(unhashable)``. Proactive
    sweep for PR #27."""
    bad_inputs: list[object] = [
        "raw string input",
        ["list", "of", "things"],
        42,
        None,
    ]
    for bad in bad_inputs:
        events = [
            _make_user_event("Run an agent"),
            _make_assistant_event(
                [{"type": "tool_use", "id": "toolu_z", "name": "Agent", "input": bad}],
                request_id="req",
            ),
        ]
        conv = build_conversation(events)
        starts = [c for c in conv if c["kind"] == "subagent_start"]
        assert len(starts) == 1
        # Falls back to the tool name when neither description nor prompt are usable.
        assert starts[0]["agent_name"] == "Agent"
        assert starts[0]["agent_prompt"] == ""

    # ``description`` as a non-hashable type used to crash the
    # ``sub_lookup.get(description)`` call with TypeError.
    events = [
        _make_user_event("Run an agent"),
        _make_assistant_event(
            [
                {
                    "type": "tool_use",
                    "id": "toolu_w",
                    "name": "Agent",
                    "input": {"description": ["not", "hashable"], "prompt": "Look around"},
                },
            ],
            request_id="req",
        ),
    ]
    conv = build_conversation(events, subagent_transcripts={"x": {"events": []}})
    starts = [c for c in conv if c["kind"] == "subagent_start"]
    assert len(starts) == 1
    # description coerced to "" → falls back to prompt for agent_name.
    assert starts[0]["agent_name"] == "Look around"


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
