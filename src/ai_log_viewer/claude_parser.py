"""Parsing logic for Claude Code session logs (~/.claude/projects/)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from .parser import MAX_RESULT_CHARS


def _default_claude_dir() -> Path:
    """Return the platform-default Claude Code projects directory."""
    if sys.platform == "win32":
        import os
        localappdata = os.environ.get("LOCALAPPDATA", "")
        if localappdata:
            return Path(localappdata) / "claude" / "projects"
    return Path.home() / ".claude" / "projects"

# Matches a complete XML-style tag block: <tag>...</tag> or self-closing <tag .../>
_XML_BLOCK_RE = re.compile(
    r"<([a-zA-Z_][\w.-]*)(?:\s[^>]*)?>.*?</\1>|<[a-zA-Z_][\w.-]*(?:\s[^>]*)?/>",
    re.DOTALL,
)


def _split_xml_and_text(content: str) -> tuple[str, str]:
    """Split content into XML context (notification) and remaining user text.

    Returns (xml_stripped_text, user_text). Either may be empty.
    """
    remaining = _XML_BLOCK_RE.sub("", content).strip()
    xml_text = ""
    for m in _XML_BLOCK_RE.finditer(content):
        stripped = re.sub(r"<[^>]+>", "", m.group()).strip()
        if stripped:
            xml_text += stripped + " "
    return xml_text.strip(), remaining


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

_SKIP_TYPES = frozenset({"queue-operation"})
# Types to skip during metadata discovery (still parsed for conversation building)
_DISCOVERY_SKIP_TYPES = frozenset({"file-history-snapshot", "queue-operation", "progress"})


def _load_events(jsonl_path: Path, skip: frozenset[str]) -> list[dict]:
    """Load events from a JSONL file, dropping types in *skip*."""
    if not jsonl_path.is_file():
        return []
    events: list[dict] = []
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if evt.get("type") in skip:
                continue
            events.append(evt)
    return events


def parse_events(jsonl_path: Path) -> list[dict]:
    """Read a Claude session JSONL file for stats/metadata display.

    Filters out progress, file-history-snapshot, and queue-operation events.
    """
    return _load_events(jsonl_path, _DISCOVERY_SKIP_TYPES)


def parse_events_for_conversation(jsonl_path: Path) -> list[dict]:
    """Read a Claude session JSONL file for conversation building.

    Keeps progress and file-history-snapshot events (needed for hook
    and snapshot timeline items) while still filtering queue-operation.
    """
    return _load_events(jsonl_path, _SKIP_TYPES)


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------

def _first_metadata(jsonl_path: Path) -> dict:
    """Read just enough of a JSONL to extract session metadata."""
    meta: dict = {}
    first_user_content: str = ""
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if evt.get("type") in _DISCOVERY_SKIP_TYPES:
                continue
            if evt.get("isMeta"):
                continue

            if not meta.get("sessionId"):
                meta["sessionId"] = evt.get("sessionId", "")
                meta["cwd"] = evt.get("cwd", "")
                meta["gitBranch"] = evt.get("gitBranch", "")
                meta["version"] = evt.get("version", "")
                meta["created_at"] = evt.get("timestamp", "")

            if evt.get("slug") and not meta.get("slug"):
                meta["slug"] = evt["slug"]

            if evt.get("type") == "assistant":
                msg = evt.get("message", {})
                if msg.get("model") and not meta.get("model"):
                    meta["model"] = msg["model"]

            if evt.get("type") == "user" and not first_user_content:
                content = evt.get("message", {}).get("content", "")
                if isinstance(content, list):
                    # Extract text from content blocks
                    parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                    content = " ".join(parts)
                if isinstance(content, str) and content:
                    _, user_text = _split_xml_and_text(content)
                    if user_text:
                        first_user_content = user_text[:120]

            # Once we have everything, stop reading
            if meta.get("sessionId") and meta.get("model") and (meta.get("slug") or first_user_content):
                break

    meta["first_user_content"] = first_user_content
    return meta


def _last_timestamp(jsonl_path: Path) -> str:
    """Read the last timestamp from a JSONL file efficiently.

    Reads the last 4 KB first (fast path). If every line in that chunk is
    truncated / unparseable, falls back to a full forward scan so that large
    tool-result events don't cause us to miss the real last timestamp.
    """
    last_ts = ""
    try:
        with open(jsonl_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            read_size = min(size, 4096)
            f.seek(size - read_size)
            chunk = f.read().decode("utf-8", errors="replace")
    except OSError:
        return last_ts

    for line in reversed(chunk.strip().split("\n")):
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
            ts = evt.get("timestamp", "")
            if ts:
                return ts
        except json.JSONDecodeError:
            continue

    # Fallback: full forward scan (handles lines longer than 4 KB)
    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                    ts = evt.get("timestamp", "")
                    if ts:
                        last_ts = ts
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return last_ts


def discover_sessions(base: Path) -> list[dict]:
    """Scan Claude project directories for session JSONL files."""
    sessions: list[dict] = []
    if not base.is_dir():
        return sessions

    for project_dir in sorted(base.iterdir()):
        if not project_dir.is_dir():
            continue
        # Skip known non-session directories
        if project_dir.name in ("memory", ".cache"):
            continue

        for jsonl_file in sorted(project_dir.glob("*.jsonl")):
            # Skip files in subdirectories (subagent logs etc.)
            if jsonl_file.parent != project_dir:
                continue

            meta = _first_metadata(jsonl_file)
            session_id = meta.get("sessionId", "")
            if not session_id:
                continue

            # Prefer first user message over slug (slug is a random codename)
            summary = meta.get("first_user_content", "") or meta.get("slug", "") or session_id
            # Convert slug from kebab-case to title case
            raw_slug = meta.get("slug", "")
            slug_display = raw_slug.replace("-", " ").title() if raw_slug else ""
            # Fall back to slug only if no user content
            if summary == raw_slug and summary:
                summary = slug_display

            updated_at = _last_timestamp(jsonl_file)

            session_entry: dict = {
                "id": session_id,
                "path": str(jsonl_file),
                "summary": summary,
                "repository": "",
                "branch": meta.get("gitBranch", ""),
                "cwd": meta.get("cwd", ""),
                "created_at": meta.get("created_at", ""),
                "updated_at": updated_at or meta.get("created_at", ""),
                "source": "claude",
                "model": meta.get("model", ""),
            }
            if slug_display:
                session_entry["slug"] = slug_display
            sessions.append(session_entry)

    sessions.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    return sessions


# ---------------------------------------------------------------------------
# Workspace metadata (synthesized from events)
# ---------------------------------------------------------------------------

def extract_workspace(events: list[dict]) -> dict:
    """Synthesize a workspace-like dict from Claude events."""
    ws: dict = {}
    slug = ""
    first_user_content = ""
    for evt in events:
        if evt.get("type") in _DISCOVERY_SKIP_TYPES or evt.get("isMeta"):
            continue
        ws.setdefault("id", evt.get("sessionId", ""))
        ws.setdefault("cwd", evt.get("cwd", ""))
        ws.setdefault("branch", evt.get("gitBranch", ""))
        ws.setdefault("created_at", evt.get("timestamp", ""))
        if evt.get("slug") and not slug:
            slug = evt["slug"].replace("-", " ").title()
        if evt.get("type") == "assistant":
            msg = evt.get("message", {})
            ws.setdefault("model", msg.get("model", ""))
        # Updated_at will be set from last event
        ws["updated_at"] = evt.get("timestamp", ws.get("updated_at", ""))
        if evt.get("type") == "user" and not first_user_content:
            content = evt.get("message", {}).get("content", "")
            if isinstance(content, list):
                parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                content = " ".join(parts)
            if isinstance(content, str) and content:
                _, user_text = _split_xml_and_text(content)
                if user_text:
                    first_user_content = user_text[:80]
    if slug:
        ws["slug"] = slug
    # Prefer first user message over random slug
    ws.setdefault("summary", first_user_content or slug or ws.get("id", ""))
    return ws


# ---------------------------------------------------------------------------
# Conversation builder
# ---------------------------------------------------------------------------

def build_conversation(events: list[dict]) -> list[dict]:
    """Build a conversation view from Claude JSONL events.

    Produces items with the same ``kind`` values as the Copilot parser so the
    templates can render them identically.
    """
    conversation: list[dict] = []

    # Synthesize session_start from first meaningful event
    for evt in events:
        if evt.get("type") in _DISCOVERY_SKIP_TYPES or evt.get("isMeta"):
            continue
        conversation.append({
            "kind": "session_start",
            "timestamp": evt.get("timestamp", ""),
            "version": evt.get("version", ""),
            "repo": "",
            "branch": evt.get("gitBranch", ""),
            "cwd": evt.get("cwd", ""),
        })
        break

    # Merge assistant entries by requestId to reconstruct full turns
    # Claude streams each content block as a separate JSONL line with the same requestId
    merged_assistant: dict[str, dict] = {}  # requestId -> merged info
    assistant_order: list[str] = []  # preserve order of first appearance

    for evt in events:
        if evt.get("type") != "assistant":
            continue
        rid = evt.get("requestId", evt.get("uuid", ""))
        msg = evt.get("message", {})
        blocks = msg.get("content", [])
        if not isinstance(blocks, list):
            continue

        if rid not in merged_assistant:
            merged_assistant[rid] = {
                "blocks": [],
                "timestamp": evt.get("timestamp", ""),
                "usage": {},
                "model": msg.get("model", ""),
                "uuid": evt.get("uuid", ""),
                "is_sidechain": evt.get("isSidechain", False),
            }
            assistant_order.append(rid)

        merged_assistant[rid]["blocks"].extend(blocks)
        # Take the latest usage (last entry per requestId has final counts)
        usage = msg.get("usage", {})
        if usage.get("output_tokens"):
            merged_assistant[rid]["usage"] = usage
        # Capture stop_reason (last wins)
        stop_reason = msg.get("stop_reason", "")
        if stop_reason:
            merged_assistant[rid]["stop_reason"] = stop_reason
        # Update timestamp to latest
        merged_assistant[rid]["timestamp"] = evt.get("timestamp", merged_assistant[rid]["timestamp"])

    # Build a set of requestIds we've emitted, to avoid duplication
    emitted_requests: set[str] = set()

    # Now walk events in order to produce conversation items
    for evt in events:
        etype = evt.get("type", "")
        ts = evt.get("timestamp", "")

        if etype in _SKIP_TYPES:
            continue

        if etype == "user":
            if evt.get("isMeta"):
                continue

            msg = evt.get("message", {})
            content = msg.get("content", "")
            _perm = evt.get("permissionMode", "")
            _sidechain = evt.get("isSidechain", False)

            # Tool results
            if isinstance(content, list):
                has_tool_result = any(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in content
                )
                if has_tool_result:
                    for block in content:
                        if not isinstance(block, dict) or block.get("type") != "tool_result":
                            continue
                        result_content = block.get("content", "")
                        if isinstance(result_content, list):
                            # tool_reference or structured content — stringify
                            result_content = json.dumps(result_content, indent=2)
                        if isinstance(result_content, str):
                            result_content = result_content[:MAX_RESULT_CHARS]
                        else:
                            result_content = str(result_content)[:MAX_RESULT_CHARS]

                        tc_id = block.get("tool_use_id", "")
                        conversation.append({
                            "kind": "tool_complete",
                            "timestamp": ts,
                            "tool_call_id": tc_id,
                            "success": not block.get("is_error", False),
                            "result": result_content,
                        })
                    continue

                # Array of text blocks (non-tool-result)
                texts = [
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                if texts:
                    joined = "\n".join(texts)
                    if joined.startswith("<"):
                        notif_text, user_text = _split_xml_and_text(joined)
                        if notif_text:
                            conversation.append({
                                "kind": "notification",
                                "timestamp": ts,
                                "message": notif_text[:500],
                            })
                        if user_text:
                            conversation.append({
                                "kind": "user_message",
                                "timestamp": ts,
                                "content": user_text,
                                "attachments": [],
                                "permission_mode": _perm,
                                "is_sidechain": _sidechain,
                            })
                    else:
                        conversation.append({
                            "kind": "user_message",
                            "timestamp": ts,
                            "content": joined,
                            "attachments": [],
                            "permission_mode": _perm,
                            "is_sidechain": _sidechain,
                        })
                continue

            # String content — user message
            if isinstance(content, str) and content:
                # System-injected XML context (e.g. <command-name>, <ide_opened_file>,
                # <local-command-stderr>) — split into notification + user message.
                if content.startswith("<"):
                    notif_text, user_text = _split_xml_and_text(content)
                    if notif_text:
                        conversation.append({
                            "kind": "notification",
                            "timestamp": ts,
                            "message": notif_text[:500],
                        })
                    if user_text:
                        conversation.append({
                            "kind": "user_message",
                            "timestamp": ts,
                            "content": user_text,
                            "attachments": [],
                            "permission_mode": _perm,
                            "is_sidechain": _sidechain,
                        })
                    continue
                conversation.append({
                    "kind": "user_message",
                    "timestamp": ts,
                    "content": content,
                    "attachments": [],
                    "permission_mode": _perm,
                    "is_sidechain": _sidechain,
                })

        elif etype == "assistant":
            rid = evt.get("requestId", evt.get("uuid", ""))
            if rid in emitted_requests:
                continue
            emitted_requests.add(rid)

            info = merged_assistant.get(rid)
            if not info:
                continue

            blocks = info["blocks"]
            usage = info["usage"]

            # Extract thinking/reasoning
            reasoning_parts = []
            text_parts = []
            tool_uses = []

            for block in blocks:
                bt = block.get("type", "")
                if bt == "thinking":
                    thinking_text = block.get("thinking", "")
                    if thinking_text:
                        reasoning_parts.append(thinking_text)
                elif bt == "text":
                    text_parts.append(block.get("text", ""))
                elif bt == "tool_use":
                    tool_uses.append(block)

            reasoning = "\n\n".join(reasoning_parts)
            output_tokens = usage.get("output_tokens", 0)

            stop_reason = info.get("stop_reason", "")

            is_sidechain = info.get("is_sidechain", False)

            # Emit assistant_message if there's text content or only reasoning
            if text_parts or (reasoning and not tool_uses):
                conversation.append({
                    "kind": "assistant_message",
                    "timestamp": info["timestamp"],
                    "content": "\n\n".join(text_parts),
                    "reasoning": reasoning,
                    "tool_requests": [
                        {"toolCallId": tu["id"], "toolName": tu.get("name", "unknown")}
                        for tu in tool_uses
                    ],
                    "parent_tool_call_id": None,
                    "output_tokens": output_tokens,
                    "stop_reason": stop_reason,
                    "is_sidechain": is_sidechain,
                })
            elif tool_uses:
                # No text — just emit a minimal assistant message with tool requests
                conversation.append({
                    "kind": "assistant_message",
                    "timestamp": info["timestamp"],
                    "content": "",
                    "reasoning": reasoning,
                    "tool_requests": [
                        {"toolCallId": tu["id"], "toolName": tu.get("name", "unknown")}
                        for tu in tool_uses
                    ],
                    "parent_tool_call_id": None,
                    "output_tokens": output_tokens,
                    "stop_reason": stop_reason,
                    "is_sidechain": is_sidechain,
                })

            # Emit tool_start for each tool_use
            for tu in tool_uses:
                conversation.append({
                    "kind": "tool_start",
                    "timestamp": info["timestamp"],
                    "tool_call_id": tu["id"],
                    "tool_name": tu.get("name", "unknown"),
                    "arguments": tu.get("input", {}),
                })

        elif etype == "system":
            content = evt.get("message", {}).get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            if content:
                conversation.append({
                    "kind": "notification",
                    "timestamp": ts,
                    "message": str(content)[:500],
                })

        elif etype == "progress":
            data = evt.get("data", {})
            if data.get("type") == "hook_progress":
                conversation.append({
                    "kind": "hook",
                    "timestamp": ts,
                    "hook_event": data.get("hookEvent", ""),
                    "hook_name": data.get("hookName", ""),
                    "command": data.get("command", ""),
                })

        elif etype == "file-history-snapshot":
            snapshot = evt.get("snapshot", {})
            backups = snapshot.get("trackedFileBackups", {})
            if backups:
                conversation.append({
                    "kind": "file_snapshot",
                    "timestamp": ts,
                    "file_count": len(backups),
                    "files": list(backups.keys())[:5],
                })

        elif etype == "last-prompt":
            prompt = evt.get("lastPrompt", "")
            if prompt:
                conversation.append({
                    "kind": "last_prompt",
                    "timestamp": ts,
                    "content": prompt[:500],
                })

    # Synthesize session_end from last event
    if events:
        last_ts = ""
        for evt in reversed(events):
            if evt.get("timestamp"):
                last_ts = evt["timestamp"]
                break
        if last_ts:
            conversation.append({"kind": "session_end", "timestamp": last_ts})

    return conversation


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def compute_stats(events: list[dict]) -> dict:
    """Compute aggregate statistics from Claude session events."""
    stats: dict = {
        "total_events": len(events),
        "user_messages": 0,
        "assistant_messages": 0,
        "tool_calls": {},
        "subagents": 0,
        "errors": 0,
        "total_output_tokens": 0,
        "total_input_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "turns": 0,
        "service_tier": "",
    }

    seen_request_ids: set[str] = set()
    token_by_request: dict[str, int] = {}  # requestId -> output_tokens (last wins)
    input_by_request: dict[str, int] = {}
    cache_read_by_request: dict[str, int] = {}
    cache_creation_by_request: dict[str, int] = {}
    last_service_tier = ""

    for evt in events:
        etype = evt.get("type", "")

        if etype == "user" and not evt.get("isMeta"):
            content = evt.get("message", {}).get("content", "")
            if isinstance(content, str) and content and not content.startswith("<"):
                stats["user_messages"] += 1
                stats["turns"] += 1
            elif isinstance(content, list):
                has_tool_result = any(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in content
                )
                if not has_tool_result:
                    stats["user_messages"] += 1
                    stats["turns"] += 1
                # Count errors from tool results
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                        stats["errors"] += 1

        elif etype == "assistant":
            rid = evt.get("requestId", evt.get("uuid", ""))
            if rid not in seen_request_ids:
                seen_request_ids.add(rid)
                stats["assistant_messages"] += 1

            msg = evt.get("message", {})
            usage = msg.get("usage", {})
            ot = usage.get("output_tokens", 0)
            if ot:
                token_by_request[rid] = ot
            it = usage.get("input_tokens", 0)
            if it:
                input_by_request[rid] = it
            cr = usage.get("cache_read_input_tokens", 0)
            if cr:
                cache_read_by_request[rid] = cr
            cc = usage.get("cache_creation_input_tokens", 0)
            if cc:
                cache_creation_by_request[rid] = cc
            st = usage.get("service_tier", "")
            if st:
                last_service_tier = st

            # Count tool calls
            for block in msg.get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tn = block.get("name", "unknown")
                    stats["tool_calls"][tn] = stats["tool_calls"].get(tn, 0) + 1

    stats["total_output_tokens"] = sum(token_by_request.values())
    stats["total_input_tokens"] = sum(input_by_request.values())
    stats["cache_read_tokens"] = sum(cache_read_by_request.values())
    stats["cache_creation_tokens"] = sum(cache_creation_by_request.values())
    stats["total_tool_calls"] = sum(stats["tool_calls"].values())
    stats["service_tier"] = last_service_tier
    return stats
