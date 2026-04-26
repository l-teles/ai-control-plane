"""Tests for typed transcript-entry models."""

from __future__ import annotations

from ai_ctrl_plane.models import TranscriptEntry, parse_entries


def test_from_dict_populates_common_fields() -> None:
    entry = TranscriptEntry.from_dict(
        {
            "type": "user",
            "uuid": "abc",
            "parentUuid": "parent",
            "sessionId": "sess-1",
            "timestamp": "2026-04-26T10:00:00Z",
            "isSidechain": True,
            "isMeta": False,
            "cwd": "/tmp/proj",
            "gitBranch": "main",
            "version": "2.1.5",
            "userType": "external",
            "permissionMode": "acceptEdits",
            "message": {"content": "hi"},
        }
    )
    assert entry.type == "user"
    assert entry.uuid == "abc"
    assert entry.parent_uuid == "parent"
    assert entry.session_id == "sess-1"
    assert entry.is_sidechain is True
    assert entry.cwd == "/tmp/proj"
    assert entry.git_branch == "main"
    assert entry.permission_mode == "acceptEdits"
    assert entry.is_user is True
    assert entry.is_assistant is False


def test_assistant_message_helpers() -> None:
    entry = TranscriptEntry.from_dict(
        {
            "type": "assistant",
            "uuid": "x",
            "requestId": "req-1",
            "message": {
                "model": "claude-sonnet-4-6",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "content": [
                    {"type": "thinking", "thinking": "let me consider"},
                    {"type": "text", "text": "Hello"},
                    {"type": "tool_use", "id": "tu_1", "name": "Read", "input": {"file_path": "/tmp/x"}},
                ],
            },
        }
    )
    assert entry.model == "claude-sonnet-4-6"
    assert entry.stop_reason == "end_turn"
    assert entry.usage["output_tokens"] == 20
    assert entry.text_content == "Hello"
    assert entry.thinking_text == "let me consider"
    assert len(entry.tool_uses) == 1
    assert entry.tool_uses[0]["name"] == "Read"
    assert entry.request_id == "req-1"


def test_user_tool_result_blocks() -> None:
    entry = TranscriptEntry.from_dict(
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_1", "content": "OK", "is_error": False},
                ]
            },
        }
    )
    assert entry.is_user
    assert entry.tool_uses == []
    assert len(entry.tool_results) == 1
    assert entry.tool_results[0]["tool_use_id"] == "tu_1"


def test_summary_entry() -> None:
    entry = TranscriptEntry.from_dict(
        {
            "type": "summary",
            "summary": "Refactored the cache layer",
            "leafUuid": "leaf-123",
        }
    )
    assert entry.is_summary
    assert entry.summary == "Refactored the cache layer"
    assert entry.leaf_uuid == "leaf-123"


def test_text_content_handles_string_payload() -> None:
    entry = TranscriptEntry.from_dict({"type": "user", "message": {"content": "hello there"}})
    assert entry.text_content == "hello there"
    assert entry.content_blocks == []


def test_raw_dict_is_preserved() -> None:
    raw = {"type": "user", "extra_field": 42, "message": {"content": "x"}}
    entry = TranscriptEntry.from_dict(raw)
    assert entry.raw is raw
    assert entry.raw["extra_field"] == 42


def test_parse_entries_returns_typed_list() -> None:
    events = [
        {"type": "user", "uuid": "u1", "message": {"content": "hi"}},
        {"type": "assistant", "uuid": "a1", "message": {"content": []}},
    ]
    entries = parse_entries(events)
    assert len(entries) == 2
    assert all(isinstance(e, TranscriptEntry) for e in entries)
    assert entries[0].is_user
    assert entries[1].is_assistant


def test_text_content_filters_non_string_text_blocks() -> None:
    """``text_content`` joins on ``b['text']``; a block with ``"text": null``
    or any non-string would otherwise crash ``"\\n".join`` with TypeError.
    Regression for PR #27 review #31."""
    entry = TranscriptEntry.from_dict(
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "text", "text": "first"},
                    {"type": "text", "text": None},  # would have crashed
                    {"type": "text", "text": 42},  # also non-string
                    {"type": "text", "text": ["wrapped"]},
                    {"type": "text", "text": "last"},
                ]
            },
        }
    )
    # No raise; non-string blocks dropped.
    assert entry.text_content == "first\nlast"


def test_from_dict_coerces_non_dict_message_and_snapshot() -> None:
    """A malformed event with ``message: [list]`` or ``snapshot: "str"``
    used to leak non-dicts into the entry; the property accessors
    (``model``, ``usage``, ``content``) would then crash on ``.get``.
    Regression for PR #27 review #42."""
    entry = TranscriptEntry.from_dict(
        {
            "type": "assistant",
            "uuid": "x",
            "message": ["list", "instead", "of", "dict"],
            "snapshot": "string instead of dict",
        }
    )
    # Coerced to empty dict — accessors don't raise.
    assert entry.message == {}
    assert entry.snapshot == {}
    assert entry.model == ""
    assert entry.usage == {}
    assert entry.content == ""


def test_from_dict_coerces_non_string_fields() -> None:
    """A malformed event with non-string ``type`` / ``uuid`` / ``timestamp``
    etc. shouldn't leak those values into the typed fields where they
    would silently break comparisons (``entry.type == \"user\"``)."""
    entry = TranscriptEntry.from_dict(
        {
            "type": 42,
            "uuid": ["not", "a", "string"],
            "timestamp": None,
            "cwd": {"x": 1},
        }
    )
    assert entry.type == ""
    assert entry.uuid == ""
    assert entry.timestamp == ""
    assert entry.cwd == ""


def test_thinking_text_filters_non_string_blocks() -> None:
    """Same defensive filter as ``text_content`` but for thinking blocks.
    Regression for PR #27 review #32."""
    entry = TranscriptEntry.from_dict(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "step one"},
                    {"type": "thinking", "thinking": None},  # would crash
                    {"type": "thinking", "thinking": 7},
                    {"type": "thinking", "thinking": "step two"},
                ]
            },
        }
    )
    assert entry.thinking_text == "step one\n\nstep two"
