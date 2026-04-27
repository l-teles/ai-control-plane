"""Parsing logic for VS Code Chat session logs.

Sessions live under ~/Library/Application Support/Code/User/ (macOS)
or ~/.config/Code/User/ (Linux) in two locations:
  - workspaceStorage/{hash}/chatSessions/{uuid}.json
  - globalStorage/emptyWindowChatSessions/{uuid}.jsonl

VS Code Insiders uses the same structure under "Code - Insiders" instead of "Code".
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from .config_readers._common import safe_read_json
from .parser import MAX_RESULT_CHARS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dget(obj: object, *keys: str, default: object = None) -> object:
    """Walk a chain of dict keys, returning *default* on any non-dict step.

    VS Code Chat session JSON is shaped by another tool's writes, so any
    intermediate node may be missing or, if the file is corrupted, the
    wrong type.  This helper mirrors ``a.get(k1, {}).get(k2, ...)`` chains
    without crashing when an intermediate is a string / list / None.
    """
    for k in keys:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(k)
    return default if obj is None else obj


def _dget_dict(obj: object, *keys: str) -> dict:
    """Like :func:`_dget`, but coerces the leaf to ``{}`` if it isn't a dict.

    Use when the caller is about to ``.get(...)`` further off the result.
    """
    v = _dget(obj, *keys, default={})
    return v if isinstance(v, dict) else {}


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
    if isinstance(model_id, str) and model_id:
        # Strip provider prefix: "copilot/claude-sonnet-4" -> "claude-sonnet-4"
        return model_id.split("/", 1)[-1] if "/" in model_id else model_id

    result = request.get("result")
    details = result.get("details", "") if isinstance(result, dict) else ""
    if isinstance(details, str) and details:
        # "Claude Sonnet 4 . 1x" -> take first part
        return details.split("\u2022")[0].strip().split(" . ")[0].strip()
    return ""


def _extract_cost_multiplier(request: dict) -> str:
    """Extract cost multiplier from result.details (e.g. 'Claude Haiku 4.5 . 0.33x')."""
    result = request.get("result")
    details = result.get("details", "") if isinstance(result, dict) else ""
    if not isinstance(details, str) or not details:
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
    if not isinstance(agent_id, str) or not agent_id:
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
    """Return the platform-default VS Code (Stable) user data directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Code" / "User"
    elif sys.platform == "win32":
        import os

        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "Code" / "User" if appdata else Path.home() / "Code" / "User"
    else:
        return Path.home() / ".config" / "Code" / "User"


def default_vscode_insiders_dir() -> Path:
    """Return the platform-default VS Code Insiders user data directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Code - Insiders" / "User"
    elif sys.platform == "win32":
        import os

        appdata = os.environ.get("APPDATA", "")
        return (
            Path(appdata) / "Code - Insiders" / "User"
            if appdata
            else Path.home() / "Code - Insiders" / "User"
        )
    else:
        return Path.home() / ".config" / "Code - Insiders" / "User"


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
            # ``safe_read_json`` returns ``None`` for non-dict roots
            # (a workspace.json with a list / scalar at the root from a
            # corrupted file would otherwise crash ``ws_data.get``).
            ws_data = safe_read_json(ws_dir / "workspace.json")
            if ws_data:
                folder = ws_data.get("folder", "")
                if isinstance(folder, str) and folder:
                    cwd = _folder_uri_to_path(folder)
                    # Derive repo from last path segment
                    repo = Path(cwd).name if cwd else ""

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


def discover_all_vscode_sessions(vscode_path: Path) -> list[dict]:
    """Discover sessions from VS Code Stable and Insiders together.

    Combines :func:`discover_sessions` for the Stable user directory with an
    automatic scan of the Insiders directory (when it exists and differs from
    ``vscode_path``).  Both callers — the SQLite cache builder and the
    filesystem-scan fallback — should use this helper so their behaviour stays
    in sync.
    """
    sessions = discover_sessions(vscode_path)
    insiders_path = default_vscode_insiders_dir()
    if insiders_path != vscode_path and insiders_path.is_dir():
        sessions += discover_sessions(insiders_path)
    return sessions


def _session_entry_from_file(path: Path, cwd: str, repo: str) -> dict | None:
    """Build a session index entry from a chat session file."""
    data = _read_session_json(path)
    # JSON root can legally be a list / scalar / null; we only handle
    # dict-shaped session files.
    if not isinstance(data, dict):
        return None

    session_id = data.get("sessionId", "")
    if not isinstance(session_id, str) or not session_id:
        return None

    requests = data.get("requests", [])
    if not isinstance(requests, list) or not requests:
        return None

    # Summary: prefer customTitle, then first user message.  Each ``.get``
    # on the way is defended because a malformed transcript or stub
    # session can put non-dict / non-string values at any layer.
    summary = data.get("customTitle", "")
    if not isinstance(summary, str):
        summary = ""
    if not summary and requests:
        first = requests[0] if isinstance(requests[0], dict) else {}
        msg = first.get("message")
        text = msg.get("text", "") if isinstance(msg, dict) else ""
        if isinstance(text, str):
            summary = text[:120]
    if not summary:
        summary = session_id

    # Model from first request
    model = ""
    for req in requests:
        if not isinstance(req, dict):
            continue
        model = _extract_model(req)
        if model:
            break

    created_at = _ms_to_iso(data.get("creationDate", 0))
    updated_at = _ms_to_iso(data.get("lastMessageDate", 0)) or created_at

    # Check if any request hit the tool call limit. Each layer is
    # type-checked so a non-dict ``result`` / ``metadata`` doesn't crash
    # the discover pass.
    def _hit_tool_limit(req: object) -> bool:
        if not isinstance(req, dict):
            return False
        result = req.get("result")
        if not isinstance(result, dict):
            return False
        metadata = result.get("metadata")
        if not isinstance(metadata, dict):
            return False
        return bool(metadata.get("maxToolCallsExceeded", False))

    max_tool_calls_exceeded = any(_hit_tool_limit(r) for r in requests)

    # Take the max mtime across the session file and the sibling
    # ``workspace.json`` — the entry's cached ``cwd`` / ``repository``
    # come from workspace.json, so editing it alone (without touching
    # the session file) needs to invalidate the row. Same pattern as
    # the Copilot parser's ``events.jsonl`` + ``workspace.yaml`` fix.
    mtimes: list[float] = []
    try:
        mtimes.append(path.stat().st_mtime)
    except OSError:
        pass
    workspace_json = path.parent.parent / "workspace.json"
    if workspace_json.is_file():
        try:
            mtimes.append(workspace_json.stat().st_mtime)
        except OSError:
            pass
    source_mtime = max(mtimes) if mtimes else 0.0

    entry: dict = {
        "id": session_id,
        "path": str(path),
        "source_path": str(path),
        "source_mtime": source_mtime,
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


_FTS_CONTENT_LIMIT = 500_000


def extract_searchable_text(path: Path) -> str:
    """Concatenate user prompts + model responses from a VS Code Chat
    session for FTS.

    Reads the request/response array; each request carries a user
    ``message.text`` and a ``response`` list of ``{value, kind}`` chunks.
    """
    data = _read_session_json(path)
    # ``_read_session_json`` returns ``json.load`` for ``.json`` files,
    # which is legally any JSON value at the root (list, string, number,
    # null, …). We only support dict-shaped sessions; bail otherwise so
    # ``data.get()`` can't crash on a non-dict.
    if not isinstance(data, dict):
        return ""
    # ``requests`` is supposed to be a list of request dicts but a
    # malformed session file could put a dict / string / scalar there.
    # ``... or []`` previously hid the type bug — iterating a dict would
    # silently walk its *keys*, dropping every searchable string. Coerce
    # the same way :func:`parse_events` does so FTS extraction stays
    # consistent across both code paths.
    raw_requests = data.get("requests")
    requests = raw_requests if isinstance(raw_requests, list) else []
    parts: list[str] = []
    total = 0
    for req in requests:
        if total >= _FTS_CONTENT_LIMIT:
            break
        if not isinstance(req, dict):
            continue
        msg = req.get("message") or {}
        if isinstance(msg, dict):
            text = msg.get("text", "") or msg.get("parsedText", "") or ""
            if isinstance(text, str) and text:
                parts.append(text)
                total += len(text)
        response = req.get("response")
        if not isinstance(response, list):
            continue
        for r in response:
            if not isinstance(r, dict):
                continue
            text = r.get("value", "") or r.get("text", "") or ""
            if isinstance(text, str) and text:
                parts.append(text)
                total += len(text)
    return "\n".join(parts)[:_FTS_CONTENT_LIMIT]


def parse_events(path: Path) -> list[dict]:
    """Read a VS Code Chat session file and return a metadata dict + requests.

    Element 0 is a synthetic ``_vscode_meta`` dict; the rest are the raw
    request objects from the session JSON.
    """
    data = _read_session_json(path)
    # JSON root can legally be a list / scalar / null; we only handle
    # dict-shaped session files.
    if not isinstance(data, dict):
        return []

    meta = {
        "_vscode_meta": True,
        "sessionId": data.get("sessionId", ""),
        "creationDate": data.get("creationDate", 0),
        "lastMessageDate": data.get("lastMessageDate", 0),
        "responderUsername": data.get("responderUsername", ""),
        "customTitle": data.get("customTitle", ""),
    }

    # Attach cwd from workspace.json if available — ``safe_read_json``
    # handles missing / malformed / non-dict-root cases.
    ws_data = safe_read_json(path.parent.parent / "workspace.json")
    if ws_data:
        folder = ws_data.get("folder", "")
        if isinstance(folder, str) and folder:
            meta["cwd"] = _folder_uri_to_path(folder)

    # ``requests`` is supposed to be a list of request dicts but a
    # malformed session file could put any JSON value there; coerce to
    # ``[]`` so the ``+`` concatenation can't TypeError, and filter to
    # dict entries so downstream consumers don't have to.
    requests = data.get("requests")
    if not isinstance(requests, list):
        requests = []
    return [meta] + [r for r in requests if isinstance(r, dict)]


# ---------------------------------------------------------------------------
# Workspace metadata
# ---------------------------------------------------------------------------


def extract_workspace(events: list[dict]) -> dict:
    """Synthesize a workspace-like dict from VS Code Chat events."""
    ws: dict = {}

    meta = events[0] if events and isinstance(events[0], dict) and events[0].get("_vscode_meta") else {}
    ws["id"] = meta.get("sessionId", "") if isinstance(meta, dict) else ""
    ws["cwd"] = meta.get("cwd", "") if isinstance(meta, dict) else ""
    ws["branch"] = ""
    ws["created_at"] = _ms_to_iso(meta.get("creationDate", 0)) if isinstance(meta, dict) else ""
    ws["updated_at"] = (
        _ms_to_iso(meta.get("lastMessageDate", 0)) if isinstance(meta, dict) else ""
    ) or ws["created_at"]

    # Model and summary from requests
    requests = [e for e in events if isinstance(e, dict) and not e.get("_vscode_meta")]
    for req in requests:
        model = _extract_model(req)
        if model:
            ws["model"] = model
            break

    summary = meta.get("customTitle", "") if isinstance(meta, dict) else ""
    if not isinstance(summary, str):
        summary = ""
    if not summary and requests:
        msg = requests[0].get("message")
        text = msg.get("text", "") if isinstance(msg, dict) else ""
        if isinstance(text, str):
            summary = text[:120]
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
    if any(_dget(req, "result", "metadata", "maxToolCallsExceeded", default=False) for req in requests):
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
    auto_summary = _dget(last_req, "result", "metadata", "summary", "text", default="")
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
        timings = _dget_dict(req, "result", "timings")
        cost_multiplier = _extract_cost_multiplier(req)

        # Agent mode from request.agent.id (e.g. "github.copilot.editsAgent" -> "Edit")
        agent_id = _dget(req, "agent", "id", default="")
        agent_mode = _agent_mode_label(agent_id) if isinstance(agent_id, str) else ""

        # --- User message ---
        user_text = _dget(req, "message", "text", default="")
        if not isinstance(user_text, str):
            user_text = ""
        attachments: list[dict] = []
        # Extract file references from variableData
        variables = _dget(req, "variableData", "variables", default=[])
        if not isinstance(variables, list):
            variables = []
        for var in variables:
            if not isinstance(var, dict):
                continue
            if var.get("kind") == "file":
                uri_data = _dget_dict(var, "value", "uri")
                file_path = uri_data.get("path", "") or uri_data.get("fsPath", "")
                if file_path and isinstance(file_path, str):
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
        result_meta = _dget_dict(req, "result", "metadata")
        tool_call_rounds = result_meta.get("toolCallRounds", [])
        if not isinstance(tool_call_rounds, list):
            tool_call_rounds = []
        tool_call_results = result_meta.get("toolCallResults", {})
        if not isinstance(tool_call_results, dict):
            tool_call_results = {}

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
                if not isinstance(round_data, dict):
                    continue
                response_text = round_data.get("response", "")
                tool_calls = round_data.get("toolCalls", [])
                if not isinstance(tool_calls, list):
                    tool_calls = []
                thinking = _dget(round_data, "thinking", "text", default="")

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
        if not isinstance(req, dict):
            continue
        # Each request is a user-assistant turn
        msg_text = _dget(req, "message", "text", default="")
        if msg_text:
            stats["user_messages"] += 1
            stats["turns"] += 1

        # Count assistant responses
        has_response = bool(req.get("response"))
        if has_response:
            stats["assistant_messages"] += 1

        # Count tool calls from toolCallRounds
        result_meta = _dget_dict(req, "result", "metadata")
        rounds = result_meta.get("toolCallRounds", [])
        if not isinstance(rounds, list):
            rounds = []
        for round_data in rounds:
            if not isinstance(round_data, dict):
                continue
            tcs = round_data.get("toolCalls", [])
            if not isinstance(tcs, list):
                continue
            for tc in tcs:
                if not isinstance(tc, dict):
                    continue
                name = tc.get("name", "unknown")
                stats["tool_calls"][name] = stats["tool_calls"].get(name, 0) + 1

        # Fallback: count from response[] if no rounds
        if not rounds:
            response_items = req.get("response", [])
            if isinstance(response_items, list):
                for item in response_items:
                    if isinstance(item, dict) and item.get("kind") == "toolInvocationSerialized":
                        name = item.get("toolId", "unknown")
                        stats["tool_calls"][name] = stats["tool_calls"].get(name, 0) + 1

        if req.get("isCanceled"):
            stats["errors"] += 1

        # Prompt token details — treat missing keys as 0 so averages
        # aren't biased by requests where a category happens to be zero.
        ptd = _dget_dict(result_meta, "usage", "promptTokenDetails")
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
