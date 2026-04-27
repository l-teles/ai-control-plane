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

# Slash-command markup that Claude Code injects into user messages when the
# user runs ``/commandname`` from the prompt. Each tag is optional.
_SLASH_TAGS = ("command-name", "command-message", "command-args", "local-command-stdout", "local-command-stderr")


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


def _parse_slash_command(content: str) -> dict | None:
    """Parse Claude Code slash-command markup into structured fields.

    Returns ``{"name", "message", "args", "stdout", "stderr"}`` when the
    content contains a ``<command-name>`` tag, else ``None``.
    """
    if "<command-name>" not in content:
        return None
    parts: dict[str, str] = {}
    for tag in _SLASH_TAGS:
        m = re.search(rf"<{tag}>(.*?)</{tag}>", content, re.DOTALL)
        if m:
            parts[tag] = m.group(1).strip()
    if "command-name" not in parts:
        return None
    return {
        "name": parts.get("command-name", ""),
        "message": parts.get("command-message", ""),
        "args": parts.get("command-args", ""),
        "stdout": parts.get("local-command-stdout", ""),
        "stderr": parts.get("local-command-stderr", ""),
    }


def _emit_xml_user_content(
    content: str,
    ts: str,
    perm: str,
    sidechain: bool,
    conversation: list[dict],
) -> None:
    """Emit conversation items for a user message containing XML markup.

    Slash commands (``<command-name>…``) are surfaced as a structured
    ``slash_command`` item; other XML context becomes a notification with
    any trailing user text emitted as a separate user_message.
    """
    sc = _parse_slash_command(content)
    if sc:
        conversation.append(
            {
                "kind": "slash_command",
                "timestamp": ts,
                "command": sc["name"],
                "message": sc["message"],
                "args": sc["args"],
                "stdout": sc["stdout"],
                "stderr": sc["stderr"],
                "permission_mode": perm,
                "is_sidechain": sidechain,
            }
        )
        return
    notif_text, user_text = _split_xml_and_text(content)
    if notif_text:
        conversation.append(
            {
                "kind": "notification",
                "timestamp": ts,
                "message": notif_text[:500],
            }
        )
    if user_text:
        conversation.append(
            {
                "kind": "user_message",
                "timestamp": ts,
                "content": user_text,
                "attachments": [],
                "permission_mode": perm,
                "is_sidechain": sidechain,
            }
        )


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

_SKIP_TYPES = frozenset({"queue-operation"})
# Types to skip during metadata discovery (still parsed for conversation building)
_DISCOVERY_SKIP_TYPES = frozenset({"file-history-snapshot", "queue-operation", "progress"})


def _load_events(jsonl_path: Path, skip: frozenset[str]) -> list[dict]:
    """Load events from a JSONL file, dropping types in *skip*.

    Non-dict JSON values at the line level (corrupted ``null`` /
    ``[]`` / scalar lines) are filtered out so every consumer downstream
    can safely assume each event is a dict.
    """
    if not jsonl_path.is_file():
        return []
    events: list[dict] = []
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(evt, dict):
                    continue
                if evt.get("type") in skip:
                    continue
                events.append(evt)
    except (OSError, UnicodeDecodeError):
        pass
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


# Cap per-session indexed content so a few multi-megabyte sessions don't
# blow the cache size out (FTS5 handles big content fine, but holding the
# whole transcript in memory for 100s of sessions during a full rebuild
# does add up). 500 KB is comfortably more than any reasonable session.
_FTS_CONTENT_LIMIT = 500_000

# Tags that Claude Code injects into transcript text — slash commands,
# IDE context, system markers, etc. Only these get scrubbed from the
# FTS-indexed content; arbitrary ``<...>`` like ``List<int>`` or HTML
# samples are left alone so they remain searchable.
_CLAUDE_CONTEXT_TAGS_RE = re.compile(
    r"</?(?:"
    r"command-name|command-message|command-args|command-stdout|command-stderr|"
    r"local-command-stdout|local-command-stderr|local-command-stdin|local-command-caveat|"
    r"ide_opened_file|ide_selection|"
    r"system-reminder|user-prompt-submit-hook|"
    r"file-history-snapshot|"
    r"bash-input|bash-output|bash-stderr"
    r")(?:\s[^>]*)?>",
    re.IGNORECASE,
)


def extract_searchable_text(jsonl_path: Path) -> str:
    """Concatenate user + assistant text content from a Claude JSONL.

    Used to populate the FTS index so search hits actual conversation
    content, not just the session summary and first message.  XML markup
    (``<command-name>``, ``<ide_opened_file>``, etc.) is stripped — those
    tags are noise for term-frequency-based ranking.
    """
    if not jsonl_path.is_file():
        return ""
    parts: list[str] = []
    total = 0
    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if total >= _FTS_CONTENT_LIMIT:
                    break
                if not line.strip():
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(evt, dict):
                    continue
                t = evt.get("type", "")
                if t not in ("user", "assistant", "summary"):
                    continue

                # Helper: append only when value is a usable string.
                # A malformed transcript with ``{"text": null}`` /
                # ``{"text": 42}`` would otherwise crash ``len(txt)``
                # below and break FTS indexing for the entire session.
                def _append(value: object) -> None:
                    nonlocal total
                    if isinstance(value, str) and value:
                        parts.append(value)
                        total += len(value)

                if t == "summary":
                    _append(evt.get("summary"))
                    continue
                msg = evt.get("message")
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content", "")
                if isinstance(content, str):
                    _append(content)
                elif isinstance(content, list):
                    for b in content:
                        if not isinstance(b, dict):
                            continue
                        bt = b.get("type")
                        if bt == "text":
                            _append(b.get("text"))
                        elif bt == "thinking":
                            _append(b.get("thinking"))
                        elif bt == "tool_result":
                            # Tool output text — useful for searches like
                            # "where did `npm install` fail?".  We skip
                            # nested image / structured blocks.
                            tc = b.get("content", "")
                            if isinstance(tc, str):
                                _append(tc)
                            elif isinstance(tc, list):
                                for inner in tc:
                                    if isinstance(inner, dict) and inner.get("type") == "text":
                                        _append(inner.get("text"))
    except OSError:
        return ""
    text = "\n".join(parts)[:_FTS_CONTENT_LIMIT]
    # Scrub only known Claude-injected context tags. A blanket
    # ``<[^>]+>`` strip would also wipe out legitimate angle-bracketed
    # content (``List<int>``, ``Function<T>``, HTML / JSX samples,
    # algebraic types, etc.) that users genuinely want to search for.
    text = _CLAUDE_CONTEXT_TAGS_RE.sub(" ", text)
    return text


def parse_subagent_transcripts(session_jsonl: Path) -> dict[str, dict]:
    """Load subagent transcripts that accompany a Claude session.

    Claude Code 2.1.2+ stores each Task/Agent subagent invocation in a
    sibling directory at ``<session_uuid>/subagents/agent-<id>.jsonl`` with
    a matching ``agent-<id>.meta.json`` containing the agent's ``agentType``
    and ``description``.

    Returns ``{description: {"agent_type", "description", "events", "path"}}``
    keyed by description, which is what the main thread's ``Agent`` /
    ``dispatch_agent`` tool_use carries in ``input.description``.  Empty
    dict if the session has no subagent directory.
    """
    out: dict[str, dict] = {}
    subagent_dir = session_jsonl.parent / session_jsonl.stem / "subagents"
    if not subagent_dir.is_dir():
        return out

    for meta_path in sorted(subagent_dir.glob("agent-*.meta.json")):
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.loads(f.read())
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        # JSON root can legally be a list / scalar / null; we only
        # handle dict-shaped subagent meta files.
        if not isinstance(meta, dict):
            continue
        events_path = meta_path.with_suffix("").with_suffix(".jsonl")
        if not events_path.is_file():
            continue
        events = _load_events(events_path, _SKIP_TYPES)
        desc_val = meta.get("description")
        description = desc_val.strip() if isinstance(desc_val, str) else ""
        if not description:
            continue
        agent_type_val = meta.get("agentType", "")
        out[description] = {
            "agent_type": agent_type_val if isinstance(agent_type_val, str) else "",
            "description": description,
            "events": events,
            "path": str(events_path),
        }
    return out


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------


def _first_metadata(jsonl_path: Path) -> dict:
    """Read just enough of a JSONL to extract session metadata."""
    meta: dict = {}
    first_user_content: str = ""
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(evt, dict):
                    continue
                if evt.get("type") in _DISCOVERY_SKIP_TYPES:
                    continue
                if evt.get("isMeta"):
                    continue

                if not meta.get("sessionId"):
                    meta["sessionId"] = evt.get("sessionId", "")
                if not meta.get("cwd") and evt.get("cwd"):
                    meta["cwd"] = evt["cwd"]
                if not meta.get("gitBranch") and evt.get("gitBranch"):
                    meta["gitBranch"] = evt["gitBranch"]
                if not meta.get("version") and evt.get("version"):
                    meta["version"] = evt["version"]
                if not meta.get("created_at") and evt.get("timestamp"):
                    meta["created_at"] = evt["timestamp"]

                if evt.get("slug") and not meta.get("slug"):
                    meta["slug"] = evt["slug"]

                if evt.get("type") == "assistant":
                    msg = evt.get("message", {})
                    if msg.get("model") and not meta.get("model"):
                        meta["model"] = msg["model"]

                if evt.get("type") == "user" and not first_user_content:
                    _msg = evt.get("message")
                    content = _msg.get("content", "") if isinstance(_msg, dict) else ""
                    if isinstance(content, list):
                        # Extract text from content blocks
                        parts = [
                            b["text"]
                            for b in content
                            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
                        ]
                        content = " ".join(parts)
                    if isinstance(content, str) and content:
                        _, user_text = _split_xml_and_text(content)
                        if user_text:
                            first_user_content = user_text[:120]

                # Once we have everything, stop reading
                if meta.get("sessionId") and meta.get("model") and (meta.get("slug") or first_user_content):
                    break
    except (OSError, UnicodeDecodeError):
        pass
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
        except json.JSONDecodeError:
            continue
        if not isinstance(evt, dict):
            continue
        ts = evt.get("timestamp", "")
        if ts:
            return ts

    # Fallback: full forward scan (handles lines longer than 4 KB)
    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(evt, dict):
                    continue
                ts = evt.get("timestamp", "")
                if ts:
                    last_ts = ts
    except OSError:
        pass
    return last_ts


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

# Pricing per million tokens (USD) — updated for Claude 4.x / 3.5 models
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # (input_per_mtok, output_per_mtok)
    "claude-opus-4-6": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
    # Older models
    "claude-sonnet-4-5-20250514": (3.0, 15.0),
    "claude-3-5-sonnet-20241022": (3.0, 15.0),
    "claude-3-5-haiku-20241022": (0.80, 4.0),
    "claude-3-opus-20240229": (15.0, 75.0),
}
# Default pricing (Sonnet-class) for unknown models
_DEFAULT_PRICING = (3.0, 15.0)


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost from token counts and model name."""
    # Match by prefix for versioned model IDs
    pricing = _DEFAULT_PRICING
    for model_id, p in _MODEL_PRICING.items():
        if model.startswith(model_id.rsplit("-", 1)[0]) or model == model_id:
            pricing = p
            break
    input_cost = input_tokens * pricing[0] / 1_000_000
    output_cost = output_tokens * pricing[1] / 1_000_000
    return round(input_cost + output_cost, 4)


def _scan_token_usage(jsonl_path: Path) -> dict:
    """Lightweight scan of a JSONL for aggregate token usage.

    Only reads 'assistant' events with usage data, skipping full content parsing.
    Returns dict with input_tokens, output_tokens, cache_read_tokens,
    cache_creation_tokens, and estimated_cost.
    """
    input_by_req: dict[str, int] = {}
    output_by_req: dict[str, int] = {}
    cache_read_by_req: dict[str, int] = {}
    cache_creation_by_req: dict[str, int] = {}
    model = ""

    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                # Quick filter: skip lines that don't look like assistant events with usage
                if '"usage"' not in line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(evt, dict):
                    continue
                if evt.get("type") != "assistant":
                    continue
                msg = evt.get("message")
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage", {})
                if not isinstance(usage, dict):
                    continue
                if not usage:
                    continue
                rid_val = evt.get("requestId") or evt.get("uuid") or ""
                rid = rid_val if isinstance(rid_val, str) else ""
                if not model:
                    model_val = msg.get("model", "")
                    model = model_val if isinstance(model_val, str) else ""
                ot = usage.get("output_tokens", 0)
                if ot:
                    output_by_req[rid] = ot
                it = usage.get("input_tokens", 0)
                if it:
                    input_by_req[rid] = it
                cr = usage.get("cache_read_input_tokens", 0)
                if cr:
                    cache_read_by_req[rid] = cr
                cc = usage.get("cache_creation_input_tokens", 0)
                if cc:
                    cache_creation_by_req[rid] = cc
    except OSError:
        pass

    total_input = sum(input_by_req.values())
    total_output = sum(output_by_req.values())
    return {
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cache_read_tokens": sum(cache_read_by_req.values()),
        "cache_creation_tokens": sum(cache_creation_by_req.values()),
        "estimated_cost": _estimate_cost(model, total_input, total_output),
    }


def _scan_summaries(jsonl_path: Path) -> list[tuple[str, str]]:
    """Scan a JSONL for ``type=summary`` entries.

    Returns a list of ``(leaf_uuid, summary_text)`` in file order.  Summary
    entries are emitted asynchronously by Claude Code when it auto-generates
    a description of a thread, and are useful as a richer label than the
    first user message for the session list.

    The scan filters lines by string match before JSON-decoding so it stays
    cheap on long sessions where 99 % of lines aren't summaries.
    """
    out: list[tuple[str, str]] = []
    if not jsonl_path.is_file():
        return out
    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                # Cheap pre-filter that's tolerant of whitespace in the
                # JSON serialiser. Production Claude files have no spaces;
                # test fixtures may.
                if '"summary"' not in line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(evt, dict):
                    continue
                if evt.get("type") != "summary":
                    continue
                summary_val = evt.get("summary")
                summary = summary_val.strip() if isinstance(summary_val, str) else ""
                leaf_val = evt.get("leafUuid")
                leaf = leaf_val if isinstance(leaf_val, str) else ""
                if summary:
                    out.append((leaf, summary))
    except OSError:
        pass
    return out


def _count_permissions(cwd: str) -> dict[str, int]:
    """Count permission rules from repo-local Claude settings.

    Delegates to :func:`config_readers.claude_config._read_repo_permissions`
    so there's a single source of truth for parsing
    ``<cwd>/.claude/settings.json`` + ``settings.local.json``. This
    function just turns the rule lists into counts.
    """
    counts: dict[str, int] = {"allow": 0, "deny": 0, "ask": 0}
    # ``cwd`` comes from a JSONL event field that ``extract_workspace`` /
    # ``_first_metadata`` don't yet type-check. A non-string ``cwd``
    # (None / int / dict from a malformed event) would crash ``Path()``
    # inside the helper.
    if not isinstance(cwd, str) or not cwd:
        return counts
    from .config_readers.claude_config import _read_repo_permissions

    rules = _read_repo_permissions(cwd)
    for key in counts:
        counts[key] = len(rules.get(key, []))
    return counts


def _count_memory_files(project_dir: Path) -> int:
    """Count markdown files in a project's memory subdirectory."""
    memory_dir = project_dir / "memory"
    if not memory_dir.is_dir():
        return 0
    return sum(1 for f in memory_dir.iterdir() if f.is_file() and f.suffix == ".md")


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

        memory_count = _count_memory_files(project_dir)
        permission_counts: dict[str, int] | None = None  # lazy, computed from first session's cwd

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

            # If Claude auto-generated a summary for this session, use the
            # most recent one — it's a curated description of what the
            # thread was about, more useful than the first user message.
            file_summaries = _scan_summaries(jsonl_file)
            if file_summaries:
                summary = file_summaries[-1][1]

            # Compute permission counts once per project from first session's cwd
            if permission_counts is None and meta.get("cwd"):
                permission_counts = _count_permissions(meta["cwd"])

            updated_at = _last_timestamp(jsonl_file)

            # Token usage and cost estimation
            tokens = _scan_token_usage(jsonl_file)

            try:
                source_mtime = jsonl_file.stat().st_mtime
            except OSError:
                source_mtime = 0.0

            session_entry: dict = {
                "id": session_id,
                "path": str(jsonl_file),
                "source_path": str(jsonl_file),
                "source_mtime": source_mtime,
                "summary": summary,
                "repository": "",
                "branch": meta.get("gitBranch", ""),
                "cwd": meta.get("cwd", ""),
                "created_at": meta.get("created_at", ""),
                "updated_at": updated_at or meta.get("created_at", ""),
                "source": "claude",
                "model": meta.get("model", ""),
                "input_tokens": tokens["input_tokens"],
                "output_tokens": tokens["output_tokens"],
                "cache_read_tokens": tokens["cache_read_tokens"],
                "cache_creation_tokens": tokens["cache_creation_tokens"],
                "estimated_cost": tokens["estimated_cost"],
                "memory_count": memory_count,
                "project_name": project_dir.name,
                "permission_counts": permission_counts or {},
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
            _msg = evt.get("message")
            content = _msg.get("content", "") if isinstance(_msg, dict) else ""
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


def build_conversation(
    events: list[dict],
    subagent_transcripts: dict[str, dict] | None = None,
) -> list[dict]:
    """Build a conversation view from Claude JSONL events.

    Produces items with the same ``kind`` values as the Copilot parser so the
    templates can render them identically.

    Events are reordered into DAG (parent_uuid) order before the rest of
    the pipeline runs, so resumed sessions and parallel-Task transcripts
    render with each child immediately following its parent regardless of
    the order in which lines were appended to the JSONL file.

    If ``subagent_transcripts`` is provided (typically from
    :func:`parse_subagent_transcripts`), each ``Agent`` / ``dispatch_agent``
    tool call is inlined with its inner transcript attached to the
    ``subagent_start`` item under a ``transcript`` field.
    """
    from .dag import order_by_dag
    from .models import parse_entries

    typed = parse_entries(events)
    typed = order_by_dag(typed)
    events = [e.raw for e in typed]

    sub_lookup = dict(subagent_transcripts or {})

    # Tool name + input lookup so `tool_complete` events can dispatch to
    # the right renderer using the original tool_use's call site.
    from .tool_renderers import render_tool, render_tool_result

    tool_name_by_id: dict[str, str] = {}
    tool_input_by_id: dict[str, dict] = {}
    for evt in events:
        if evt.get("type") != "assistant":
            continue
        # Defend ``message`` and ``content`` shapes too — a malformed
        # transcript could have either as a non-dict / non-list and we
        # don't want a per-event TypeError to tank the whole render.
        msg = evt.get("message")
        if not isinstance(msg, dict):
            continue
        blocks = msg.get("content")
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tu_id = block.get("id", "")
                if not isinstance(tu_id, str) or not tu_id:
                    continue
                name = block.get("name", "unknown")
                tool_name_by_id[tu_id] = name if isinstance(name, str) else "unknown"
                # ``input`` is supposed to be the tool's structured args
                # dict, but malformed / non-Anthropic MCP servers can
                # emit lists or strings here. Coerce to ``{}`` so the
                # renderer dispatcher gets the type it expects.
                inp = block.get("input")
                tool_input_by_id[tu_id] = inp if isinstance(inp, dict) else {}

    conversation: list[dict] = []
    subagent_tool_ids: set[str] = set()  # track Agent tool_use IDs

    # Synthesize session_start from first meaningful event
    for evt in events:
        if evt.get("type") in _DISCOVERY_SKIP_TYPES or evt.get("isMeta"):
            continue
        conversation.append(
            {
                "kind": "session_start",
                "timestamp": evt.get("timestamp", ""),
                "version": evt.get("version", ""),
                "repo": "",
                "branch": evt.get("gitBranch", ""),
                "cwd": evt.get("cwd", ""),
            }
        )
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
                has_tool_result = any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
                if has_tool_result:
                    for block in content:
                        if not isinstance(block, dict) or block.get("type") != "tool_result":
                            continue
                        raw_result = block.get("content", "")
                        # Split text from images. The tool-specific renderer
                        # gets the *full* text so it can parse structured
                        # payloads (e.g. WebSearch JSON) before applying its
                        # own preview truncation; only the legacy fallback
                        # display gets the MAX_RESULT_CHARS cap.
                        text_result, images = render_tool_result(raw_result)
                        if not isinstance(text_result, str):
                            text_result = str(text_result)

                        # Keep a JSON-stringified, length-capped copy for the
                        # legacy template fallback (the ``result`` field that
                        # renders when a tool has no dedicated layout).
                        if isinstance(raw_result, list):
                            legacy_result = json.dumps(raw_result, indent=2, default=str)[:MAX_RESULT_CHARS]
                        else:
                            legacy_result = text_result[:MAX_RESULT_CHARS]

                        tc_id = block.get("tool_use_id", "")
                        if tc_id in subagent_tool_ids:
                            conversation.append(
                                {
                                    "kind": "subagent_complete",
                                    "timestamp": ts,
                                    "agent_name": "",
                                    "tool_call_id": tc_id,
                                    "result": legacy_result,
                                    "images": images,
                                }
                            )
                        else:
                            tool_name = tool_name_by_id.get(tc_id, "unknown")
                            tool_input = tool_input_by_id.get(tc_id, {})
                            rendered = render_tool(tool_name, tool_input, text_result)
                            conversation.append(
                                {
                                    "kind": "tool_complete",
                                    "timestamp": ts,
                                    "tool_call_id": tc_id,
                                    "tool_name": tool_name,
                                    "success": not block.get("is_error", False),
                                    "result": legacy_result,
                                    "rendered": rendered,
                                    "images": images,
                                }
                            )
                    continue

                # Array of text blocks (non-tool-result)
                texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                if texts:
                    joined = "\n".join(texts)
                    if joined.startswith("<"):
                        _emit_xml_user_content(joined, ts, _perm, _sidechain, conversation)
                    else:
                        conversation.append(
                            {
                                "kind": "user_message",
                                "timestamp": ts,
                                "content": joined,
                                "attachments": [],
                                "permission_mode": _perm,
                                "is_sidechain": _sidechain,
                            }
                        )
                continue

            # String content — user message
            if isinstance(content, str) and content:
                # System-injected XML context (e.g. <command-name>, <ide_opened_file>,
                # <local-command-stderr>) — split into slash_command / notification
                # / user_message via the helper.
                if content.startswith("<"):
                    _emit_xml_user_content(content, ts, _perm, _sidechain, conversation)
                    continue
                conversation.append(
                    {
                        "kind": "user_message",
                        "timestamp": ts,
                        "content": content,
                        "attachments": [],
                        "permission_mode": _perm,
                        "is_sidechain": _sidechain,
                    }
                )

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
                conversation.append(
                    {
                        "kind": "assistant_message",
                        "timestamp": info["timestamp"],
                        "content": "\n\n".join(text_parts),
                        "reasoning": reasoning,
                        "tool_requests": [
                            {"toolCallId": tu["id"], "toolName": tu.get("name", "unknown")} for tu in tool_uses
                        ],
                        "parent_tool_call_id": None,
                        "output_tokens": output_tokens,
                        "stop_reason": stop_reason,
                        "is_sidechain": is_sidechain,
                    }
                )
            elif tool_uses:
                # No text — just emit a minimal assistant message with tool requests
                conversation.append(
                    {
                        "kind": "assistant_message",
                        "timestamp": info["timestamp"],
                        "content": "",
                        "reasoning": reasoning,
                        "tool_requests": [
                            {"toolCallId": tu["id"], "toolName": tu.get("name", "unknown")} for tu in tool_uses
                        ],
                        "parent_tool_call_id": None,
                        "output_tokens": output_tokens,
                        "stop_reason": stop_reason,
                        "is_sidechain": is_sidechain,
                    }
                )

            # Emit tool_start for each tool_use (and subagent_start for Agent tools)
            for tu in tool_uses:
                tu_name = tu.get("name", "unknown")
                if tu_name in ("Agent", "dispatch_agent"):
                    # ``input`` should be a dict but a malformed transcript or
                    # an MCP server emitting a list/string would crash the
                    # ``.get`` chain.  Coerce to ``{}`` first.
                    agent_input = tu.get("input")
                    if not isinstance(agent_input, dict):
                        agent_input = {}
                    raw_desc = agent_input.get("description", "")
                    # ``description`` is used as a dict key into
                    # ``sub_lookup`` below; a non-hashable value (list / dict)
                    # would raise ``TypeError`` there. Coerce to str.
                    description = raw_desc if isinstance(raw_desc, str) else ""
                    raw_prompt = agent_input.get("prompt", "")
                    prompt_str = raw_prompt if isinstance(raw_prompt, str) else ""
                    agent_name = description or prompt_str or tu_name
                    agent_prompt = prompt_str
                    subagent_tool_ids.add(tu["id"])
                    item: dict = {
                        "kind": "subagent_start",
                        "timestamp": info["timestamp"],
                        "agent_name": str(agent_name)[:120],
                        "agent_prompt": str(agent_prompt)[:2000],
                        "tool_call_id": tu["id"],
                    }
                    sub = sub_lookup.get(description) if description else None
                    if sub:
                        item["agent_type"] = sub.get("agent_type", "")
                        # Recurse to render the inner transcript with the
                        # same conversation kinds as the parent.
                        item["transcript"] = build_conversation(sub.get("events", []))
                    conversation.append(item)
                else:
                    raw_input = tu.get("input")
                    tu_input = raw_input if isinstance(raw_input, dict) else {}
                    rendered_start = render_tool(tu_name, tu_input, "")
                    conversation.append(
                        {
                            "kind": "tool_start",
                            "timestamp": info["timestamp"],
                            "tool_call_id": tu["id"],
                            "tool_name": tu_name,
                            "arguments": tu_input,
                            "rendered": rendered_start,
                        }
                    )

        elif etype == "system":
            _msg = evt.get("message")
            content = _msg.get("content", "") if isinstance(_msg, dict) else ""
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
                )
            if content:
                conversation.append(
                    {
                        "kind": "notification",
                        "timestamp": ts,
                        "message": str(content)[:500],
                    }
                )

        elif etype == "progress":
            data = evt.get("data", {})
            if data.get("type") == "hook_progress":
                conversation.append(
                    {
                        "kind": "hook",
                        "timestamp": ts,
                        "hook_event": data.get("hookEvent", ""),
                        "hook_name": data.get("hookName", ""),
                        "command": data.get("command", ""),
                    }
                )

        elif etype == "file-history-snapshot":
            snapshot = evt.get("snapshot", {})
            backups = snapshot.get("trackedFileBackups", {})
            if backups:
                conversation.append(
                    {
                        "kind": "file_snapshot",
                        "timestamp": ts,
                        "file_count": len(backups),
                        "files": list(backups.keys())[:5],
                    }
                )

        elif etype == "last-prompt":
            prompt = evt.get("lastPrompt", "")
            if prompt:
                conversation.append(
                    {
                        "kind": "last_prompt",
                        "timestamp": ts,
                        "content": prompt[:500],
                    }
                )

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
            _msg = evt.get("message")
            content = _msg.get("content", "") if isinstance(_msg, dict) else ""
            if isinstance(content, str) and content and not content.startswith("<"):
                stats["user_messages"] += 1
                stats["turns"] += 1
            elif isinstance(content, list):
                has_tool_result = any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
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
                    if tn in ("Agent", "dispatch_agent"):
                        stats["subagents"] += 1

    stats["total_output_tokens"] = sum(token_by_request.values())
    stats["total_input_tokens"] = sum(input_by_request.values())
    stats["cache_read_tokens"] = sum(cache_read_by_request.values())
    stats["cache_creation_tokens"] = sum(cache_creation_by_request.values())
    stats["total_tool_calls"] = sum(stats["tool_calls"].values())
    stats["service_tier"] = last_service_tier
    return stats
