"""Parsing logic for Copilot session log directories."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import yaml


def _default_copilot_dir() -> Path:
    """Return the platform-default Copilot session-state directory."""
    if sys.platform == "win32":
        localappdata = os.environ.get("LOCALAPPDATA", "")
        if localappdata:
            return Path(localappdata) / "github-copilot" / "session-state"
    return Path.home() / ".copilot" / "session-state"


def _safe_open(base_dir: Path, *parts: str) -> str | None:
    """Read a file under base_dir, verifying it doesn't escape the directory."""
    target = base_dir.joinpath(*parts)
    real_base = os.path.realpath(base_dir)
    real_target = os.path.realpath(target)
    if not real_target.startswith(real_base):
        return None
    if not os.path.isfile(real_target):
        return None
    try:
        with open(real_target, encoding="utf-8") as f:  # noqa: PTH123
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def parse_workspace(session_dir: Path) -> dict:
    """Read workspace.yaml metadata for a session."""
    content = _safe_open(session_dir, "workspace.yaml")
    if content is None:
        return {}
    return yaml.safe_load(content) or {}


def parse_events(session_dir: Path) -> list[dict]:
    """Read the events.jsonl file and return a list of parsed event dicts."""
    content = _safe_open(session_dir, "events.jsonl")
    if content is None:
        return []
    events: list[dict] = []
    for line in content.splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events


def parse_snapshots(session_dir: Path) -> dict:
    """Read the rewind-snapshots/index.json file."""
    content = _safe_open(session_dir, "rewind-snapshots", "index.json")
    if content is None:
        return {}
    return json.loads(content)


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


def ts_display(iso_str) -> str:
    """Format an ISO timestamp (or datetime) for display."""
    if not iso_str:
        return ""
    if isinstance(iso_str, datetime):
        return iso_str.strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError, OverflowError):
        return str(iso_str)


def duration_between(start_iso, end_iso) -> str:
    """Human-readable duration between two ISO timestamps."""
    try:
        s = datetime.fromisoformat(str(start_iso).replace("Z", "+00:00"))
        e = datetime.fromisoformat(str(end_iso).replace("Z", "+00:00"))
        secs = int((e - s).total_seconds())
        if secs < 0:
            return ""
        if secs < 60:
            return f"{secs}s"
        m, s2 = divmod(secs, 60)
        if m < 60:
            return f"{m}m {s2}s"
        h, m2 = divmod(m, 60)
        return f"{h}h {m2}m"
    except (ValueError, TypeError, OverflowError):
        return ""


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------


def discover_sessions(base: Path) -> list[dict]:
    """Scan *base* for subdirectories that look like Copilot session logs."""
    sessions: list[dict] = []
    if not base.is_dir():
        return sessions
    for d in sorted(base.iterdir()):
        if d.is_dir() and (d / "events.jsonl").exists():
            ws = parse_workspace(d)
            sessions.append(
                {
                    "id": d.name,
                    "path": str(d),
                    "summary": ws.get("summary", d.name),
                    "repository": ws.get("repository", ""),
                    "branch": ws.get("branch", ""),
                    "cwd": ws.get("cwd", ""),
                    "created_at": str(ws.get("created_at", "")),
                    "updated_at": str(ws.get("updated_at", "")),
                }
            )
    sessions.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    return sessions


# ---------------------------------------------------------------------------
# Conversation builder
# ---------------------------------------------------------------------------

MAX_RESULT_CHARS = 10_000


def build_conversation(events: list[dict]) -> list[dict]:
    """Build a linear conversation view from raw JSONL events."""
    conversation: list[dict] = []

    for evt in events:
        etype = evt.get("type", "")
        data = evt.get("data", {})
        ts = evt.get("timestamp", "")

        if etype == "session.start":
            ctx = data.get("context", {})
            conversation.append(
                {
                    "kind": "session_start",
                    "timestamp": ts,
                    "version": data.get("copilotVersion", ""),
                    "repo": ctx.get("repository", ""),
                    "branch": ctx.get("branch", ""),
                    "cwd": ctx.get("cwd", ""),
                }
            )

        elif etype == "user.message":
            att_list = []
            for a in data.get("attachments", []):
                att_list.append(
                    {
                        "type": a.get("type", ""),
                        "path": a.get("path", a.get("displayName", "")),
                        "name": a.get("displayName", a.get("path", "")),
                    }
                )
            conversation.append(
                {
                    "kind": "user_message",
                    "timestamp": ts,
                    "content": data.get("content", ""),
                    "attachments": att_list,
                }
            )

        elif etype == "assistant.message":
            tr_info = [
                {"toolCallId": tr.get("toolCallId", ""), "toolName": tr.get("name", tr.get("toolName", "unknown"))}
                for tr in data.get("toolRequests", [])
            ]
            conversation.append(
                {
                    "kind": "assistant_message",
                    "timestamp": ts,
                    "content": data.get("content", ""),
                    "reasoning": data.get("reasoningText", ""),
                    "tool_requests": tr_info,
                    "parent_tool_call_id": data.get("parentToolCallId"),
                    "output_tokens": data.get("outputTokens", 0),
                }
            )

        elif etype == "tool.execution_start":
            conversation.append(
                {
                    "kind": "tool_start",
                    "timestamp": ts,
                    "tool_call_id": data.get("toolCallId", ""),
                    "tool_name": data.get("toolName", "unknown"),
                    "arguments": data.get("arguments", {}),
                    "mcp_server": data.get("mcpServerName", ""),
                }
            )

        elif etype == "tool.execution_complete":
            result = data.get("result", "")
            if isinstance(result, str):
                result = result[:MAX_RESULT_CHARS]
            else:
                result = str(result)[:MAX_RESULT_CHARS]
            conversation.append(
                {
                    "kind": "tool_complete",
                    "timestamp": ts,
                    "tool_call_id": data.get("toolCallId", ""),
                    "success": data.get("success", False),
                    "result": result,
                }
            )

        elif etype == "subagent.started":
            conversation.append(
                {
                    "kind": "subagent_start",
                    "timestamp": ts,
                    "agent_name": data.get("agentDisplayName", data.get("agentName", "")),
                    "tool_call_id": data.get("toolCallId", ""),
                }
            )

        elif etype == "subagent.completed":
            conversation.append(
                {
                    "kind": "subagent_complete",
                    "timestamp": ts,
                    "agent_name": data.get("agentDisplayName", data.get("agentName", "")),
                    "tool_call_id": data.get("toolCallId", ""),
                }
            )

        elif etype == "session.error":
            conversation.append(
                {
                    "kind": "error",
                    "timestamp": ts,
                    "message": str(data.get("message", data.get("error", str(data)))),
                }
            )

        elif etype == "system.notification":
            conversation.append(
                {
                    "kind": "notification",
                    "timestamp": ts,
                    "message": data.get("message", str(data)),
                }
            )

        elif etype == "session.shutdown":
            conversation.append({"kind": "session_end", "timestamp": ts})

        elif etype == "assistant.turn_start":
            conversation.append({"kind": "turn_start", "timestamp": ts, "turn_id": data.get("turnId", "")})

        elif etype == "assistant.turn_end":
            conversation.append({"kind": "turn_end", "timestamp": ts, "turn_id": data.get("turnId", "")})

        elif etype == "session.model_change":
            conversation.append(
                {
                    "kind": "model_change",
                    "timestamp": ts,
                    "new_model": data.get("newModel", ""),
                    "reasoning_effort": data.get("reasoningEffort", ""),
                }
            )

        elif etype == "session.info":
            conversation.append(
                {
                    "kind": "notification",
                    "timestamp": ts,
                    "message": data.get("message", str(data)),
                }
            )

    return conversation


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def compute_stats(events: list[dict]) -> dict:
    """Compute aggregate statistics from a list of raw events."""
    stats: dict = {
        "total_events": len(events),
        "user_messages": 0,
        "assistant_messages": 0,
        "tool_calls": {},
        "mcp_tool_calls": {},
        "subagents": 0,
        "errors": 0,
        "total_output_tokens": 0,
        "turns": 0,
        "reasoning_effort": "",
    }
    for evt in events:
        t = evt.get("type", "")
        d = evt.get("data", {})
        if t == "user.message":
            stats["user_messages"] += 1
        elif t == "assistant.message":
            stats["assistant_messages"] += 1
            stats["total_output_tokens"] += d.get("outputTokens", 0)
        elif t == "tool.execution_start":
            tn = d.get("toolName", "unknown")
            stats["tool_calls"][tn] = stats["tool_calls"].get(tn, 0) + 1
            mcp = d.get("mcpServerName", "")
            if mcp:
                mcp_key = f"{mcp} \u2192 {tn}"
                stats["mcp_tool_calls"][mcp_key] = stats["mcp_tool_calls"].get(mcp_key, 0) + 1
        elif t == "subagent.started":
            stats["subagents"] += 1
        elif t == "session.error":
            stats["errors"] += 1
        elif t == "assistant.turn_end":
            stats["turns"] += 1
        elif t == "session.model_change":
            stats["reasoning_effort"] = d.get("reasoningEffort", "")

    stats["total_tool_calls"] = sum(stats["tool_calls"].values())
    return stats
