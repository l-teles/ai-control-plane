"""Typed views over Claude transcript JSONL entries.

The parsers in :mod:`claude_parser` historically read the raw dict
representation (``evt.get("message", {}).get("content", ...)``) which is
verbose and error-prone for the more elaborate rendering we'll build on top.
This module exposes a thin :class:`TranscriptEntry` dataclass that exposes
the fields we care about while keeping the original ``raw`` dict reachable
for anything not surfaced here.

The dataclass is intentionally additive: the existing dict-based pipeline
keeps working, and new code (DAG ordering, tool renderers, etc.) can switch
to the typed view incrementally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TranscriptEntry:
    """One JSONL line from a Claude session, normalised into typed fields."""

    type: str = ""
    uuid: str = ""
    parent_uuid: str = ""
    leaf_uuid: str = ""  # only set on summary entries
    session_id: str = ""
    timestamp: str = ""
    is_sidechain: bool = False
    is_meta: bool = False
    cwd: str = ""
    git_branch: str = ""
    version: str = ""
    user_type: str = ""
    agent_id: str = ""
    permission_mode: str = ""
    request_id: str = ""

    # Type-specific payloads kept as raw dicts — the renderer dispatchers in
    # later phases will reach into these via the helper properties below.
    message: dict[str, Any] = field(default_factory=dict)
    summary: str = ""  # for summary entries
    snapshot: dict[str, Any] = field(default_factory=dict)  # file-history-snapshot

    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TranscriptEntry:
        return cls(
            type=d.get("type", "") or "",
            uuid=d.get("uuid", "") or "",
            parent_uuid=d.get("parentUuid", "") or "",
            leaf_uuid=d.get("leafUuid", "") or "",
            session_id=d.get("sessionId", "") or "",
            timestamp=d.get("timestamp", "") or "",
            is_sidechain=bool(d.get("isSidechain", False)),
            is_meta=bool(d.get("isMeta", False)),
            cwd=d.get("cwd", "") or "",
            git_branch=d.get("gitBranch", "") or "",
            version=d.get("version", "") or "",
            user_type=d.get("userType", "") or "",
            agent_id=d.get("agentId", "") or "",
            permission_mode=d.get("permissionMode", "") or "",
            request_id=d.get("requestId", "") or "",
            message=d.get("message") or {},
            summary=d.get("summary", "") or "",
            snapshot=d.get("snapshot") or {},
            raw=d,
        )

    # -- Type predicates ----------------------------------------------------

    @property
    def is_user(self) -> bool:
        return self.type == "user"

    @property
    def is_assistant(self) -> bool:
        return self.type == "assistant"

    @property
    def is_system(self) -> bool:
        return self.type == "system"

    @property
    def is_summary(self) -> bool:
        return self.type == "summary"

    # -- Assistant-specific helpers ----------------------------------------

    @property
    def model(self) -> str:
        return self.message.get("model", "") if self.is_assistant else ""

    @property
    def usage(self) -> dict[str, Any]:
        return self.message.get("usage", {}) if self.is_assistant else {}

    @property
    def stop_reason(self) -> str:
        return self.message.get("stop_reason", "") if self.is_assistant else ""

    # -- Generic content access --------------------------------------------

    @property
    def content(self) -> Any:
        """Raw content payload — string for plain user messages, list of
        content blocks for assistants and structured user messages, ``""`` for
        entry types that don't carry message content (summary, snapshot, etc.)
        """
        return self.message.get("content", "")

    @property
    def content_blocks(self) -> list[dict[str, Any]]:
        """Always-a-list view of ``content`` for block-level rendering."""
        c = self.content
        if isinstance(c, list):
            return [b for b in c if isinstance(b, dict)]
        return []

    @property
    def text_content(self) -> str:
        """Concatenated text from text blocks, or the raw string content."""
        c = self.content
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
        return ""

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content_blocks if b.get("type") == "tool_use"]

    @property
    def tool_results(self) -> list[dict[str, Any]]:
        return [b for b in self.content_blocks if b.get("type") == "tool_result"]

    @property
    def thinking_text(self) -> str:
        return "\n\n".join(b.get("thinking", "") for b in self.content_blocks if b.get("type") == "thinking")


def parse_entries(events: list[dict[str, Any]]) -> list[TranscriptEntry]:
    """Lift a list of raw dict events into typed entries."""
    return [TranscriptEntry.from_dict(e) for e in events]
