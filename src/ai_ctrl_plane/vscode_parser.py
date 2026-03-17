"""Parsing logic for VS Code Chat session logs.

Sessions live under ~/Library/Application Support/Code/User/ (macOS)
or ~/.config/Code/User/ (Linux) in two locations:
  - workspaceStorage/{hash}/chatSessions/{uuid}.json
  - globalStorage/emptyWindowChatSessions/{uuid}.jsonl
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from .parser import MAX_RESULT_CHARS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ms_to_iso(ms: int | float) -> str:
    """Convert a Unix-millisecond timestamp to an ISO 8601 string."""
    if not ms:
        return ""
    try:
        return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()
    except (OSError, ValueError, OverflowError):
        return ""


def _extract_model(request: dict) -> str:
    """Extract a human-readable model name from a VS Code Chat request."""
    model_id = request.get("modelId", "")
    if model_id:
        # Strip provider prefix: "copilot/claude-sonnet-4" -> "claude-sonnet-4"
        return model_id.split("/", 1)[-1] if "/" in model_id else model_id

    details = request.get("result", {}).get("details", "")
    if details:
        # "Claude Sonnet 4 . 1x" -> take first part
        return details.split("\u2022")[0].strip().split(" . ")[0].strip()
    return ""


def _extract_cost_multiplier(request: dict) -> str:
    """Extract cost multiplier from result.details (e.g. 'Claude Haiku 4.5 . 0.33x')."""
    details = request.get("result", {}).get("details", "")
    if not details:
        return ""
    # Look for pattern like "0.33x" or "1x"
    parts = details.split("\u2022")
    if len(parts) >= 2:
        return parts[-1].strip()
    parts = details.split(" . ")
    if len(parts) >= 2:
        return parts[-1].strip()
    return ""


_AGENT_MODE_MAP = {
    "editsagent": "Edit",
    "chatagent": "Chat",
    "agent": "Agent",
}


def _agent_mode_label(agent_id: str) -> str:
    """Map a VS Code agent ID to a human-readable mode label."""
    if not agent_id:
        return ""
    suffix = agent_id.rsplit(".", 1)[-1].lower()
    return _AGENT_MODE_MAP.get(suffix, suffix.title() if suffix else "")


def _folder_uri_to_path(uri: str) -> str:
    """Convert a VS Code folder URI to a filesystem path.

    On Windows, ``file:///C:/Users/...`` parses with a leading ``/`` before
    the drive letter that must be stripped.
    """
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return uri
    path = unquote(parsed.path)
    # Strip leading slash before drive letter on Windows (e.g. /C:/...)
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return path


def _read_session_json(path: Path) -> dict | None:
    """Read a VS Code Chat session from a .json or .jsonl file."""
    try:
        if path.suffix == ".jsonl":
            with open(path, encoding="utf-8") as f:
                state: dict = {}
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        wrapper = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(wrapper, dict):
                        continue
                    kind = wrapper.get("kind")
                    try:
                        if kind == 0:
                            state = wrapper.get("v", {})
                        elif kind in (1, 2):
                            keys = wrapper.get("k", [])
                            value = wrapper.get("v")
                            if len(keys) == 1:
                                state[keys[0]] = value
                            elif len(keys) == 2:
                                k0, k1 = keys[0], keys[1]
                                if isinstance(k1, int):
                                    if not isinstance(state.get(k0), list):
                                        state[k0] = []
                                    while len(state[k0]) <= k1:
                                        state[k0].append({})
                                    state[k0][k1] = value
                                else:
                                    if not isinstance(state.get(k0), dict):
                                        state[k0] = {}
                                    state[k0][k1] = value
                            elif len(keys) == 3:
                                k0, k1, k2 = keys[0], keys[1], keys[2]
                                if not isinstance(state.get(k0), list):
                                    state[k0] = []
                                while len(state[k0]) <= k1:
                                    state[k0].append({})
                                if not isinstance(state[k0][k1], dict):
                                    state[k0][k1] = {}
                                state[k0][k1][k2] = value
                    except (TypeError, IndexError, AttributeError, KeyError):
                        continue
                return state if state else None
        else:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


def _default_vscode_dir() -> Path:
    """Return the platform-default VS Code user data directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Code" / "User"
    elif sys.platform == "win32":
        import os

        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "Code" / "User" if appdata else Path.home() / "Code" / "User"
    else:
        return Path.home() / ".config" / "Code" / "User"


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------


def discover_sessions(base: Path) -> list[dict]:
    """Scan VS Code workspaceStorage and globalStorage for chat sessions."""
    sessions: list[dict] = []
    if not base.is_dir():
        return sessions

    # 1. Workspace chat sessions
    ws_storage = base / "workspaceStorage"
    if ws_storage.is_dir():
        for ws_dir in sorted(ws_storage.iterdir()):
            if not ws_dir.is_dir():
                continue
            chat_dir = ws_dir / "chatSessions"
            if not chat_dir.is_dir():
                continue

            # Read workspace.json for cwd
            cwd = ""
            repo = ""
            ws_json = ws_dir / "workspace.json"
            if ws_json.is_file():
                try:
                    with open(ws_json, encoding="utf-8") as f:
                        ws_data = json.load(f)
                    folder = ws_data.get("folder", "")
                    if folder:
                        cwd = _folder_uri_to_path(folder)
                        # Derive repo from last path segment
                        repo = Path(cwd).name if cwd else ""
                except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                    pass

            for session_file in sorted(list(chat_dir.glob("*.json")) + list(chat_dir.glob("*.jsonl"))):
                entry = _session_entry_from_file(session_file, cwd, repo)
                if entry:
                    sessions.append(entry)

    # 2. Global (empty window) chat sessions
    global_dir = base / "globalStorage" / "emptyWindowChatSessions"
    if global_dir.is_dir():
        for session_file in sorted(global_dir.glob("*.jsonl")):
            entry = _session_entry_from_file(session_file, "", "")
            if entry:
                sessions.append(entry)

    sessions.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    return sessions


def _session_entry_from_file(path: Path, cwd: str, repo: str) -> dict | None:
    """Build a session index entry from a chat session file."""
    data = _read_session_json(path)
    if not data:
        return None

    session_id = data.get("sessionId", "")
    if not session_id:
        return None

    requests = data.get("requests", [])
    if not requests:
        return None

    # Summary: prefer customTitle, then first user message
    summary = data.get("customTitle", "")
    if not summary and requests:
        summary = (requests[0].get("message", {}).get("text", "") or "")[:120]
    if not summary:
        summary = session_id

    # Model from first request
    model = ""
    for req in requests:
        model = _extract_model(req)
        if model:
            break

    created_at = _ms_to_iso(data.get("creationDate", 0))
    updated_at = _ms_to_iso(data.get("lastMessageDate", 0)) or created_at

    # Check if any request hit the tool call limit
    max_tool_calls_exceeded = any(
        req.get("result", {}).get("metadata", {}).get("maxToolCallsExceeded", False) for req in requests
    )

    entry: dict = {
        "id": session_id,
        "path": str(path),
        "summary": summary,
        "repository": repo,
        "branch": "",
        "cwd": cwd,
        "created_at": created_at,
        "updated_at": updated_at,
        "source": "vscode",
        "model": model,
    }
    if max_tool_calls_exceeded:
        entry["max_tool_calls_exceeded"] = True
    if data.get("hasPendingEdits", False):
        entry["has_pending_edits"] = True
    return entry


# ---------------------------------------------------------------------------
# Event parsing
# ---------------------------------------------------------------------------


def parse_events(path: Path) -> list[dict]:
    """Read a VS Code Chat session file and return a metadata dict + requests.

    Element 0 is a synthetic ``_vscode_meta`` dict; the rest are the raw
    request objects from the session JSON.
    """
    data = _read_session_json(path)
    if not data:
        return []

    meta = {
        "_vscode_meta": True,
        "sessionId": data.get("sessionId", ""),
        "creationDate": data.get("creationDate", 0),
        "lastMessageDate": data.get("lastMessageDate", 0),
        "responderUsername": data.get("responderUsername", ""),
        "customTitle": data.get("customTitle", ""),
    }

    # Attach cwd from workspace.json if available
    ws_json = path.parent.parent / "workspace.json"
    if ws_json.is_file():
        try:
            with open(ws_json, encoding="utf-8") as f:
                ws_data = json.load(f)
            folder = ws_data.get("folder", "")
            if folder:
                meta["cwd"] = _folder_uri_to_path(folder)
        except (json.JSONDecodeError, OSError):
            pass

    return [meta] + data.get("requests", [])


# ---------------------------------------------------------------------------
# Workspace metadata
# ---------------------------------------------------------------------------


def extract_workspace(events: list[dict]) -> dict:
    """Synthesize a workspace-like dict from VS Code Chat events."""
    ws: dict = {}

    meta = events[0] if events and events[0].get("_vscode_meta") else {}
    ws["id"] = meta.get("sessionId", "")
    ws["cwd"] = meta.get("cwd", "")
    ws["branch"] = ""
    ws["created_at"] = _ms_to_iso(meta.get("creationDate", 0))
    ws["updated_at"] = _ms_to_iso(meta.get("lastMessageDate", 0)) or ws["created_at"]

    # Model and summary from requests
    requests = [e for e in events if not e.get("_vscode_meta")]
    for req in requests:
        model = _extract_model(req)
        if model:
            ws["model"] = model
            break

    summary = meta.get("customTitle", "")
    if not summary and requests:
        summary = (requests[0].get("message", {}).get("text", "") or "")[:120]
    ws["summary"] = summary or ws["id"]

    return ws


# ---------------------------------------------------------------------------
# Conversation builder
# ---------------------------------------------------------------------------


def build_conversation(events: list[dict]) -> list[dict]:
    """Build a standardized conversation view from VS Code Chat events.

    Produces items with the same ``kind`` values as the Copilot and Claude
    parsers so the templates can render them identically.
    """
    conversation: list[dict] = []

    meta = events[0] if events and events[0].get("_vscode_meta") else {}
    requests = [e for e in events if not e.get("_vscode_meta")]

    if not requests:
        return conversation

    # Session start
    first_req = requests[0]
    conversation.append(
        {
            "kind": "session_start",
            "timestamp": _ms_to_iso(first_req.get("timestamp", 0) or meta.get("creationDate", 0)),
            "version": "",
            "repo": "",
            "branch": "",
            "cwd": meta.get("cwd", ""),
        }
    )

    # Check for maxToolCallsExceeded across all requests
    if any(req.get("result", {}).get("metadata", {}).get("maxToolCallsExceeded", False) for req in requests):
        conversation.append(
            {
                "kind": "warning",
                "timestamp": _ms_to_iso(first_req.get("timestamp", 0) or meta.get("creationDate", 0)),
                "message": "Agent hit tool call limit — session may be incomplete",
            }
        )

    # Extract auto-generated summary from last request (display near session start)
    last_req = requests[-1] if requests else {}
    first_req = requests[0] if requests else {}
    auto_summary = last_req.get("result", {}).get("metadata", {}).get("summary", {}).get("text", "")
    if auto_summary:
        conversation.append(
            {
                "kind": "session_summary",
                "timestamp": _ms_to_iso(first_req.get("timestamp", 0)),
                "content": auto_summary,
            }
        )

    for req in requests:
        ts = _ms_to_iso(req.get("timestamp", 0))
        timings = req.get("result", {}).get("timings", {})
        cost_multiplier = _extract_cost_multiplier(req)

        # Agent mode from request.agent.id (e.g. "github.copilot.editsAgent" -> "Edit")
        agent_id = req.get("agent", {}).get("id", "") if isinstance(req.get("agent"), dict) else ""
        agent_mode = _agent_mode_label(agent_id)

        # --- User message ---
        user_text = req.get("message", {}).get("text", "")
        attachments: list[dict] = []
        # Extract file references from variableData
        for var in req.get("variableData", {}).get("variables", []):
            if var.get("kind") == "file":
                uri_data = var.get("value", {}).get("uri", {})
                file_path = uri_data.get("path", "") or uri_data.get("fsPath", "")
                if file_path:
                    attachments.append({"type": "file", "name": Path(file_path).name, "path": file_path})

        if user_text:
            conversation.append(
                {
                    "kind": "user_message",
                    "timestamp": ts,
                    "content": user_text,
                    "attachments": attachments,
                    "agent_mode": agent_mode,
                    "time_spent_waiting_ms": req.get("timeSpentWaiting", 0),
                }
            )

        # --- Process tool call rounds from metadata (structured data) ---
        result_meta = req.get("result", {}).get("metadata", {})
        tool_call_rounds = result_meta.get("toolCallRounds", [])
        tool_call_results = result_meta.get("toolCallResults", {})

        # Build ordered list of pastTenseMessage from response array.
        # IDs differ between response[] and toolCallRounds, so match by
        # position (both arrays list tools in the same order).
        past_tense_list: list[str] = []
        for item in req.get("response", []):
            if isinstance(item, dict) and item.get("kind") == "toolInvocationSerialized":
                pt = item.get("pastTenseMessage", "")
                if isinstance(pt, dict):
                    pt = pt.get("value", "")
                past_tense_list.append(pt or "")

        if tool_call_rounds:
            _pt_idx = 0  # positional index into past_tense_list
            for round_data in tool_call_rounds:
                response_text = round_data.get("response", "")
                tool_calls = round_data.get("toolCalls", [])
                thinking = round_data.get("thinking", {}).get("text", "")

                # Emit assistant message for this round
                if response_text or tool_calls:
                    conversation.append(
                        {
                            "kind": "assistant_message",
                            "timestamp": ts,
                            "content": response_text,
                            "reasoning": thinking,
                            "tool_requests": [
                                {"toolCallId": tc.get("id", ""), "toolName": tc.get("name", "unknown")}
                                for tc in tool_calls
                            ],
                            "parent_tool_call_id": None,
                            "output_tokens": 0,
                            "first_progress_ms": timings.get("firstProgress", 0),
                            "total_elapsed_ms": timings.get("totalElapsed", 0),
                            "cost_multiplier": cost_multiplier,
                        }
                    )

                # Emit tool_start and tool_complete for each tool call
                for tc in tool_calls:
                    tc_id = tc.get("id", "")
                    tc_name = tc.get("name", "unknown")
                    tc_args = {}
                    try:
                        tc_args = json.loads(tc.get("arguments", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        tc_args = {"raw": tc.get("arguments", "")}

                    pt = past_tense_list[_pt_idx] if _pt_idx < len(past_tense_list) else ""
                    _pt_idx += 1

                    conversation.append(
                        {
                            "kind": "tool_start",
                            "timestamp": ts,
                            "tool_call_id": tc_id,
                            "tool_name": tc_name,
                            "arguments": tc_args,
                            "past_tense": pt,
                        }
                    )

                    # Tool result
                    result_data = tool_call_results.get(tc_id)
                    result_text = _extract_tool_result(result_data)
                    conversation.append(
                        {
                            "kind": "tool_complete",
                            "timestamp": ts,
                            "tool_call_id": tc_id,
                            "success": True,
                            "result": result_text[:MAX_RESULT_CHARS] if result_text else "",
                        }
                    )
        else:
            # No tool call rounds — build from response[] array
            text_parts = []
            for item in req.get("response", []):
                if isinstance(item, dict):
                    if item.get("kind") == "progressTaskSerialized":
                        content_val = item.get("content", {})
                        if isinstance(content_val, dict):
                            content_val = content_val.get("value", "")
                        if content_val:
                            conversation.append(
                                {
                                    "kind": "progress_task",
                                    "timestamp": ts,
                                    "content": str(content_val)[:200],
                                }
                            )
                    elif item.get("kind") == "confirmation":
                        title = item.get("title", "")
                        if isinstance(title, dict):
                            title = title.get("value", "")
                        conversation.append(
                            {
                                "kind": "notification",
                                "timestamp": ts,
                                "message": title or "User confirmation requested",
                            }
                        )
                    elif "value" in item and "kind" not in item:
                        # Plain text response
                        val = item["value"]
                        if isinstance(val, str):
                            text_parts.append(val)
                    elif item.get("kind") == "toolInvocationSerialized":
                        # Tool call from response array
                        tc_id = item.get("toolCallId", "")
                        tc_name = item.get("toolId", "unknown")
                        msg = item.get("invocationMessage", "")
                        if isinstance(msg, dict):
                            msg = msg.get("value", "")
                        past_tense = item.get("pastTenseMessage", "")
                        if isinstance(past_tense, dict):
                            past_tense = past_tense.get("value", "")

                        conversation.append(
                            {
                                "kind": "tool_start",
                                "timestamp": ts,
                                "tool_call_id": tc_id,
                                "tool_name": tc_name,
                                "arguments": {"description": msg},
                                "past_tense": past_tense,
                            }
                        )

                        result_data = tool_call_results.get(tc_id)
                        result_text = _extract_tool_result(result_data)
                        conversation.append(
                            {
                                "kind": "tool_complete",
                                "timestamp": ts,
                                "tool_call_id": tc_id,
                                "success": bool(item.get("isComplete", True)),
                                "result": result_text[:MAX_RESULT_CHARS] if result_text else "",
                            }
                        )

            if text_parts:
                conversation.append(
                    {
                        "kind": "assistant_message",
                        "timestamp": ts,
                        "content": "\n\n".join(text_parts),
                        "reasoning": "",
                        "tool_requests": [],
                        "parent_tool_call_id": None,
                        "output_tokens": 0,
                        "first_progress_ms": timings.get("firstProgress", 0),
                        "total_elapsed_ms": timings.get("totalElapsed", 0),
                        "cost_multiplier": cost_multiplier,
                    }
                )

        # Handle canceled requests
        if req.get("isCanceled"):
            conversation.append(
                {
                    "kind": "error",
                    "timestamp": ts,
                    "message": "Request was canceled by user",
                }
            )

        # Follow-up suggestions
        followups = req.get("followups", [])
        suggestions = [f.get("message", "") for f in followups if isinstance(f, dict) and f.get("message")]
        if suggestions:
            conversation.append(
                {
                    "kind": "followups",
                    "timestamp": ts,
                    "suggestions": suggestions,
                }
            )

    # Session end
    last_ts = _ms_to_iso(requests[-1].get("timestamp", 0) if requests else meta.get("lastMessageDate", 0))
    if last_ts:
        conversation.append({"kind": "session_end", "timestamp": last_ts})

    return conversation


def _extract_tool_result(result_data: dict | None) -> str:
    """Extract readable text from a VS Code tool call result."""
    if not result_data:
        return ""
    if not isinstance(result_data, dict):
        return str(result_data)

    content = result_data.get("content", [])
    if not isinstance(content, list):
        return str(content)

    parts = []
    for item in content:
        if isinstance(item, dict):
            val = item.get("value", "")
            if isinstance(val, str):
                parts.append(val)
            # Some results have nested node structures — skip those
        elif isinstance(item, str):
            parts.append(item)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def compute_stats(events: list[dict]) -> dict:
    """Compute aggregate statistics from VS Code Chat events."""
    requests = [e for e in events if not e.get("_vscode_meta")]

    stats: dict = {
        "total_events": len(requests),
        "user_messages": 0,
        "assistant_messages": 0,
        "tool_calls": {},
        "subagents": 0,
        "errors": 0,
        "total_output_tokens": 0,
        "turns": 0,
        "prompt_token_details": {},
        "_ptd_count": 0,
    }

    for req in requests:
        # Each request is a user-assistant turn
        msg_text = req.get("message", {}).get("text", "")
        if msg_text:
            stats["user_messages"] += 1
            stats["turns"] += 1

        # Count assistant responses
        has_response = bool(req.get("response"))
        if has_response:
            stats["assistant_messages"] += 1

        # Count tool calls from toolCallRounds
        result_meta = req.get("result", {}).get("metadata", {})
        for round_data in result_meta.get("toolCallRounds", []):
            for tc in round_data.get("toolCalls", []):
                name = tc.get("name", "unknown")
                stats["tool_calls"][name] = stats["tool_calls"].get(name, 0) + 1

        # Fallback: count from response[] if no rounds
        if not result_meta.get("toolCallRounds"):
            for item in req.get("response", []):
                if isinstance(item, dict) and item.get("kind") == "toolInvocationSerialized":
                    name = item.get("toolId", "unknown")
                    stats["tool_calls"][name] = stats["tool_calls"].get(name, 0) + 1

        if req.get("isCanceled"):
            stats["errors"] += 1

        # Prompt token details — treat missing keys as 0 so averages
        # aren't biased by requests where a category happens to be zero.
        ptd = result_meta.get("usage", {}).get("promptTokenDetails", {})
        if ptd:
            for key in ("system", "toolDefinitions", "messages", "files"):
                pct = ptd.get(key, 0)
                try:
                    pct = float(pct)
                except (TypeError, ValueError):
                    pct = 0.0
                stats["prompt_token_details"][key] = stats["prompt_token_details"].get(key, 0) + pct
            stats["_ptd_count"] += 1

    stats["total_tool_calls"] = sum(stats["tool_calls"].values())
    # Average prompt token percentages if we have multiple requests
    if stats.get("_ptd_count", 0) > 1:
        for key in stats["prompt_token_details"]:
            stats["prompt_token_details"][key] = round(stats["prompt_token_details"][key] / stats["_ptd_count"])
    stats.pop("_ptd_count", None)
    return stats
