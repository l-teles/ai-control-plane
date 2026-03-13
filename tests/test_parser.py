"""Tests for the log parser module."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from ai_log_viewer.parser import (
    build_conversation,
    compute_stats,
    discover_sessions,
    duration_between,
    parse_events,
    parse_workspace,
    ts_display,
)


@pytest.fixture()
def tmp_session(tmp_path: Path) -> Path:
    """Create a minimal session directory for testing."""
    session_dir = tmp_path / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    session_dir.mkdir()

    (session_dir / "workspace.yaml").write_text(
        textwrap.dedent("""\
        id: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
        cwd: /tmp/project
        repository: org/repo
        branch: main
        summary: Test Session
        created_at: 2026-03-12T10:00:00.000Z
        updated_at: 2026-03-12T10:05:00.000Z
        """)
    )

    events = [
        {
            "type": "session.start",
            "data": {
                "sessionId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "copilotVersion": "1.0.0",
                "context": {"repository": "org/repo", "branch": "main", "cwd": "/tmp/project"},
            },
            "timestamp": "2026-03-12T10:00:00.000Z",
        },
        {
            "type": "user.message",
            "data": {"content": "Hello, add tests", "attachments": []},
            "timestamp": "2026-03-12T10:00:01.000Z",
        },
        {
            "type": "assistant.turn_start",
            "data": {"turnId": "0"},
            "timestamp": "2026-03-12T10:00:02.000Z",
        },
        {
            "type": "assistant.message",
            "data": {"content": "Sure, I will add tests.", "outputTokens": 42, "toolRequests": []},
            "timestamp": "2026-03-12T10:00:03.000Z",
        },
        {
            "type": "tool.execution_start",
            "data": {"toolCallId": "tc1", "toolName": "view", "arguments": {"filePath": "/tmp/a.py"}},
            "timestamp": "2026-03-12T10:00:04.000Z",
        },
        {
            "type": "tool.execution_complete",
            "data": {"toolCallId": "tc1", "success": True, "result": "file contents"},
            "timestamp": "2026-03-12T10:00:05.000Z",
        },
        {
            "type": "assistant.turn_end",
            "data": {"turnId": "0"},
            "timestamp": "2026-03-12T10:00:06.000Z",
        },
        {
            "type": "session.shutdown",
            "data": {},
            "timestamp": "2026-03-12T10:05:00.000Z",
        },
    ]
    with open(session_dir / "events.jsonl", "w") as f:
        for evt in events:
            f.write(json.dumps(evt) + "\n")

    return tmp_path


def test_discover_sessions(tmp_session: Path) -> None:
    sessions = discover_sessions(tmp_session)
    assert len(sessions) == 1
    assert sessions[0]["summary"] == "Test Session"
    assert sessions[0]["repository"] == "org/repo"


def test_parse_workspace(tmp_session: Path) -> None:
    ws = parse_workspace(tmp_session / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert ws["summary"] == "Test Session"
    assert ws["branch"] == "main"


def test_parse_events(tmp_session: Path) -> None:
    events = parse_events(tmp_session / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert len(events) == 8
    assert events[0]["type"] == "session.start"
    assert events[-1]["type"] == "session.shutdown"


def test_build_conversation(tmp_session: Path) -> None:
    events = parse_events(tmp_session / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    conv = build_conversation(events)
    kinds = [c["kind"] for c in conv]
    assert "session_start" in kinds
    assert "user_message" in kinds
    assert "assistant_message" in kinds
    assert "tool_start" in kinds
    assert "tool_complete" in kinds
    assert "session_end" in kinds


def test_compute_stats(tmp_session: Path) -> None:
    events = parse_events(tmp_session / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    stats = compute_stats(events)
    assert stats["user_messages"] == 1
    assert stats["assistant_messages"] == 1
    assert stats["total_tool_calls"] == 1
    assert stats["tool_calls"]["view"] == 1
    assert stats["total_output_tokens"] == 42
    assert stats["turns"] == 1


def test_ts_display() -> None:
    assert ts_display("2026-03-12T10:00:00.000Z") == "2026-03-12 10:00:00 UTC"
    assert ts_display("") == ""
    assert ts_display(None) == ""


def test_duration_between() -> None:
    assert duration_between("2026-03-12T10:00:00Z", "2026-03-12T10:00:30Z") == "30s"
    assert duration_between("2026-03-12T10:00:00Z", "2026-03-12T10:05:30Z") == "5m 30s"
    assert duration_between("2026-03-12T10:00:00Z", "2026-03-12T12:30:00Z") == "2h 30m"


def test_model_change_event() -> None:
    """session.model_change events produce model_change conversation items."""
    events = [
        {
            "type": "session.model_change",
            "data": {"newModel": "gpt-4o", "reasoningEffort": "high"},
            "timestamp": "2026-03-12T10:00:00Z",
        },
    ]
    conv = build_conversation(events)
    mc = [c for c in conv if c["kind"] == "model_change"]
    assert len(mc) == 1
    assert mc[0]["new_model"] == "gpt-4o"
    assert mc[0]["reasoning_effort"] == "high"


def test_mcp_server_name() -> None:
    """tool.execution_start with mcpServerName populates mcp_server field."""
    events = [
        {
            "type": "tool.execution_start",
            "data": {
                "toolCallId": "tc1",
                "toolName": "query",
                "arguments": {},
                "mcpServerName": "steampipe",
            },
            "timestamp": "2026-03-12T10:00:00Z",
        },
    ]
    conv = build_conversation(events)
    ts = [c for c in conv if c["kind"] == "tool_start"]
    assert len(ts) == 1
    assert ts[0]["mcp_server"] == "steampipe"


def test_model_change_stats() -> None:
    """compute_stats captures reasoning_effort from model_change events."""
    events = [
        {
            "type": "session.model_change",
            "data": {"newModel": "gpt-4o", "reasoningEffort": "medium"},
            "timestamp": "2026-03-12T10:00:00Z",
        },
    ]
    stats = compute_stats(events)
    assert stats["reasoning_effort"] == "medium"


def test_mcp_tool_calls_stats() -> None:
    """compute_stats tracks MCP tool calls separately."""
    events = [
        {
            "type": "tool.execution_start",
            "data": {"toolCallId": "tc1", "toolName": "query", "mcpServerName": "steampipe"},
            "timestamp": "2026-03-12T10:00:00Z",
        },
        {
            "type": "tool.execution_start",
            "data": {"toolCallId": "tc2", "toolName": "list", "mcpServerName": "steampipe"},
            "timestamp": "2026-03-12T10:00:01Z",
        },
        {
            "type": "tool.execution_start",
            "data": {"toolCallId": "tc3", "toolName": "Bash"},
            "timestamp": "2026-03-12T10:00:02Z",
        },
    ]
    stats = compute_stats(events)
    assert stats["total_tool_calls"] == 3
    assert stats["mcp_tool_calls"]["steampipe \u2192 query"] == 1
    assert stats["mcp_tool_calls"]["steampipe \u2192 list"] == 1
    assert "Bash" not in str(stats["mcp_tool_calls"])
