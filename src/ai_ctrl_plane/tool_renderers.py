"""Per-tool enrichment of conversation items.

The Claude conversation builder produces generic ``tool_start`` and
``tool_complete`` items that carry the raw ``input`` dict and ``result``
string.  This module turns those into structured fields that the
template can render with a tool-specific layout — a ``Bash`` block shows
the command and ANSI-rendered output, a ``Read`` block shows the file
path and a code excerpt, a ``WebSearch`` block shows the query and a
list of result entries, etc.

Each renderer takes a tool's input/result and returns a dict of named
fields the template will use; the dict is merged into the conversation
item under a ``rendered`` key so the original raw fields stay available
as a fallback.

Unknown tools fall through to a generic renderer.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from .ansi import ansi_to_html
from .config_readers._common import sanitize_url

# How many characters of file/output content to inline before truncating.
# When the limit is hit, the helper returns ``was_truncated=True`` and the
# template renders a "… truncated" indicator below the preview; the full
# text remains in the raw ``result`` field on the conversation item so
# the user can fall back to it via the JSON API.
_PREVIEW_CHARS = 4_000


def _truncate(text: str, limit: int = _PREVIEW_CHARS) -> tuple[str, bool]:
    """Return (truncated_text, was_truncated). The caller / template is
    responsible for surfacing a visual cue when ``was_truncated`` is True
    (no ``...`` is appended here so the preview can be safely embedded in
    HTML attributes etc.)."""
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _coerce_str(value: Any, default: str = "") -> str:
    """Defensive coercion to ``str``.

    Renderers slice / strip / count on ``tool_input`` fields that are
    *expected* to be strings (file_path, command, content, url, …) but
    that come from arbitrary MCP tool payloads.  A renderer that
    crashes on ``"foo".count('\\n')`` against ``None``/``int``/``list``
    would tank the whole conversation render; coerce instead.
    """
    return value if isinstance(value, str) else default


def _file_basename(path: str) -> str:
    if not path:
        return ""
    return os.path.basename(path) or path


# ---------------------------------------------------------------------------
# Image extraction (used by tool_result blocks)
# ---------------------------------------------------------------------------

_DATA_URL_RE = re.compile(r"^data:(image/(?:png|jpe?g|gif|webp));base64,([A-Za-z0-9+/=]+)$")
# Allow only inert raster formats. Notably excludes ``image/svg+xml`` —
# inline SVG executes scripts when rendered in ``<img>`` (it's an XML
# document, not an opaque pixel buffer), which would defeat the CSP.
_SAFE_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"})
# Cap base64 image payloads to ~1.5 MB decoded (2 MB base64 ≈ 1.5 MB
# raw). Larger images get dropped — they'd otherwise inflate the HTML
# response, push memory pressure on the browser, and stall page paint.
_MAX_IMAGE_BASE64_SIZE = 2_000_000


def extract_images_from_result(result: Any) -> list[dict[str, str]]:
    """Find inline images in a Claude tool_result content payload.

    Tool results may be a string, a list of content blocks, or structured
    JSON. Image blocks look like ``{"type": "image", "source": {"type":
    "base64", "media_type": "image/png", "data": "iVBOR..."}}``.

    Returns a list of ``{"src": "data:image/png;base64,…", "alt": "…"}``
    dicts; empty list when there are no images.
    """
    out: list[dict[str, str]] = []
    if isinstance(result, list):
        for block in result:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "image":
                # ``source`` is supposed to be a dict but a malformed
                # tool_result could put a list / scalar here; the
                # subsequent ``source.get(...)`` would crash.
                source = block.get("source")
                if not isinstance(source, dict):
                    continue
                if source.get("type") == "base64":
                    media = source.get("media_type", "image/png")
                    data = source.get("data", "")
                    # Defend against malformed payloads (lists, ints, None,
                    # etc.) — ``in`` on a frozenset of strings raises on
                    # unhashable types, ``len`` raises on non-sized values.
                    # Skip the block silently rather than tank the whole
                    # conversation render.
                    if not isinstance(media, str) or not isinstance(data, str):
                        continue
                    if media not in _SAFE_IMAGE_MIME_TYPES:
                        continue  # reject SVG and other potentially active formats
                    if not data or len(data) > _MAX_IMAGE_BASE64_SIZE:
                        continue  # drop oversized payloads (don't bloat HTML)
                    out.append({"src": f"data:{media};base64,{data}", "alt": "tool result image"})
            elif block.get("type") == "text":
                # A text block may contain a data URL — pick those up too.
                text = block.get("text", "")
                if not isinstance(text, str):
                    continue
                stripped = text.strip()
                m = _DATA_URL_RE.match(stripped)
                if m and len(m.group(2)) <= _MAX_IMAGE_BASE64_SIZE:
                    # Apply the cap to the captured base64 data (group 2)
                    # rather than the whole ``data:...;base64,...`` URL,
                    # so the threshold matches what the block-image path
                    # measures (raw base64 bytes, not URL framing).
                    out.append({"src": stripped, "alt": "tool result image"})
    elif isinstance(result, str):
        stripped = result.strip()
        m = _DATA_URL_RE.match(stripped)
        if m and len(m.group(2)) <= _MAX_IMAGE_BASE64_SIZE:
            out.append({"src": stripped, "alt": "tool result image"})
    return out


# ---------------------------------------------------------------------------
# Per-tool renderers
# ---------------------------------------------------------------------------


def render_bash(tool_input: dict, result: str) -> dict:
    cmd = _coerce_str(tool_input.get("command")).strip()
    description = _coerce_str(tool_input.get("description")).strip()
    output, truncated = _truncate(_coerce_str(result))
    return {
        "tool": "Bash",
        "title": cmd[:80] if cmd else "(empty command)",
        "subtitle": description,
        "command": cmd,
        "description": description,
        "output_html": ansi_to_html(output),
        "truncated": truncated,
    }


def render_read(tool_input: dict, result: str) -> dict:
    path = _coerce_str(tool_input.get("file_path"))
    offset = tool_input.get("offset")
    limit = tool_input.get("limit")
    body, truncated = _truncate(_coerce_str(result))
    return {
        "tool": "Read",
        "title": _file_basename(path),
        "subtitle": path,
        "file_path": path,
        "offset": offset,
        "limit": limit,
        "content": body,
        "truncated": truncated,
    }


def render_write(tool_input: dict, result: str) -> dict:
    path = _coerce_str(tool_input.get("file_path"))
    content = _coerce_str(tool_input.get("content"))
    body, truncated = _truncate(content)
    return {
        "tool": "Write",
        "title": _file_basename(path),
        "subtitle": path,
        "file_path": path,
        "content": body,
        "lines": content.count("\n") + 1 if content else 0,
        "truncated": truncated,
    }


def render_edit(tool_input: dict, result: str) -> dict:
    path = _coerce_str(tool_input.get("file_path"))
    return {
        "tool": "Edit",
        "title": _file_basename(path),
        "subtitle": path,
        "file_path": path,
        "old_string": _coerce_str(tool_input.get("old_string"))[:_PREVIEW_CHARS],
        "new_string": _coerce_str(tool_input.get("new_string"))[:_PREVIEW_CHARS],
        "replace_all": bool(tool_input.get("replace_all")),
    }


def render_grep(tool_input: dict, result: str) -> dict:
    pattern = _coerce_str(tool_input.get("pattern"))
    path = _coerce_str(tool_input.get("path"))
    output, truncated = _truncate(_coerce_str(result))
    return {
        "tool": "Grep",
        "title": pattern[:60],
        "subtitle": f"in {path}" if path else "",
        "pattern": pattern,
        "path": path,
        "glob": _coerce_str(tool_input.get("glob")),
        "type": _coerce_str(tool_input.get("type")),
        "output": output,
        "truncated": truncated,
    }


def render_glob(tool_input: dict, result: str) -> dict:
    pat = _coerce_str(tool_input.get("pattern"))
    output, truncated = _truncate(_coerce_str(result))
    matches: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if line:
            matches.append(line)
    return {
        "tool": "Glob",
        "title": pat,
        "subtitle": _coerce_str(tool_input.get("path")),
        "pattern": pat,
        "matches": matches,
        "match_count": len(matches),
        "truncated": truncated,
    }


def render_webfetch(tool_input: dict, result: str) -> dict:
    raw_url = _coerce_str(tool_input.get("url"))
    safe_url = sanitize_url(raw_url) if raw_url else ""
    prompt = _coerce_str(tool_input.get("prompt"))
    body, truncated = _truncate(_coerce_str(result))
    return {
        "tool": "WebFetch",
        "title": (safe_url or raw_url)[:80],  # title is text-only; show even unsafe URL
        "subtitle": prompt[:120],
        # ``url`` is the link target — empty when the scheme isn't safe,
        # so the template can render plain text instead of a clickable
        # link for ``javascript:`` etc.
        "url": safe_url,
        # ``url_display`` is what we *show* to the user, regardless of
        # safety; the template uses this for the visible link text.
        "url_display": raw_url,
        "prompt": prompt,
        "content": body,
        "truncated": truncated,
    }


def render_websearch(tool_input: dict, result: str) -> dict:
    query = _coerce_str(tool_input.get("query"))
    # Try to parse structured results from the result string. Claude
    # WebSearch typically returns a JSON-ish payload listing
    # ``{"title", "url", "snippet"}`` entries.
    results: list[dict] = []
    if result:
        try:
            parsed = json.loads(result)
            if isinstance(parsed, list):
                for r in parsed:
                    if isinstance(r, dict):
                        raw_url = r.get("url", "")
                        title = r.get("title", "")
                        snippet = r.get("snippet") or r.get("description") or ""
                        results.append(
                            {
                                # Coerce to str defensively — JSON values
                                # parsed from arbitrary MCP tool output
                                # may not be the type we expect.
                                "title": title if isinstance(title, str) else "",
                                # Sanitise scheme — a ``javascript:`` URL
                                # rendered into ``<a href>`` would execute
                                # on click. Empty string when unsafe; the
                                # template falls back to plain text.
                                "url": sanitize_url(raw_url) if isinstance(raw_url, str) else "",
                                "url_display": raw_url if isinstance(raw_url, str) else "",
                                "snippet": snippet[:300] if isinstance(snippet, str) else "",
                            }
                        )
        except (json.JSONDecodeError, TypeError):
            pass
    body, truncated = _truncate(_coerce_str(result))
    return {
        "tool": "WebSearch",
        "title": query[:80],
        "subtitle": f"{len(results)} results" if results else "",
        "query": query,
        "results": results,
        "raw": body,
        "truncated": truncated,
    }


def render_ask_user_question(tool_input: dict, result: str) -> dict:
    question = _coerce_str(tool_input.get("question")) or _coerce_str(tool_input.get("prompt"))
    options = tool_input.get("options") or tool_input.get("choices") or []
    if not isinstance(options, list):
        options = [str(options)]
    return {
        "tool": "AskUserQuestion",
        "title": question[:80],
        "subtitle": f"{len(options)} option{'s' if len(options) != 1 else ''}" if options else "",
        "question": question,
        "options": [str(o) for o in options],
        "answer": _coerce_str(result).strip()[:500],
    }


def render_todowrite(tool_input: dict, result: str) -> dict:
    raw_todos = tool_input.get("todos") or []
    if not isinstance(raw_todos, list):
        raw_todos = []
    # Filter to dict items only — the template calls ``t.get("status")``
    # / ``t.get("content")`` and would crash on a non-dict (int, str,
    # None) entry from a malformed transcript.
    todos = [t for t in raw_todos if isinstance(t, dict)]
    return {
        "tool": "TodoWrite",
        "title": f"{len(todos)} task{'s' if len(todos) != 1 else ''}",
        "subtitle": "",
        "todos": todos,
    }


def render_generic(tool_name: str, tool_input: dict, result: str) -> dict:
    """Fallback renderer for tools without a dedicated layout."""
    body, truncated = _truncate(_coerce_str(result))
    title = ""
    for key in ("file_path", "path", "url", "command", "pattern", "query"):
        v = tool_input.get(key)
        if isinstance(v, str) and v:
            title = v[:80]
            break
    return {
        "tool": tool_name,
        "title": title,
        "subtitle": "",
        "input_json": json.dumps(tool_input, indent=2, default=str)[:_PREVIEW_CHARS],
        "output": body,
        "truncated": truncated,
    }


_RENDERERS = {
    "Bash": render_bash,
    "Read": render_read,
    "Write": render_write,
    "Edit": render_edit,
    "MultiEdit": render_edit,
    "Grep": render_grep,
    "Glob": render_glob,
    "WebFetch": render_webfetch,
    "WebSearch": render_websearch,
    "AskUserQuestion": render_ask_user_question,
    "TodoWrite": render_todowrite,
}


def render_tool(tool_name: str, tool_input: Any, result: Any) -> dict:
    """Dispatch to a tool-specific renderer or fall through to generic.

    Coerces ``tool_input`` to a dict and ``result`` to a string defensively
    — a malformed transcript could put a list/string/None in either slot,
    and renderers assume both are well-typed.
    """
    if not isinstance(tool_input, dict):
        tool_input = {}
    if not isinstance(result, str):
        result = "" if result is None else str(result)
    fn = _RENDERERS.get(tool_name)
    if fn is None:
        return render_generic(tool_name, tool_input, result)
    return fn(tool_input, result)


def render_tool_result(result: Any) -> tuple[str, list[dict[str, str]]]:
    """Normalise a tool_result content payload into (text, images).

    - String results pass through (after image-URL detection).
    - List-of-blocks results have their text blocks concatenated and
      image blocks extracted into a list of base64 data URLs.
    - Other types are stringified.
    """
    images = extract_images_from_result(result)
    if isinstance(result, str):
        # If the entire string is a data URL, the image is already in
        # `images`; suppress the textual representation.
        if images and result.strip() == images[0]["src"]:
            return "", images
        return result, images
    if isinstance(result, list):
        text_parts: list[str] = []
        for block in result:
            if isinstance(block, dict) and block.get("type") == "text":
                # ``"text": null`` is valid JSON and shows up in some tool
                # results — coerce so ``"\n".join`` doesn't TypeError.
                text = block.get("text") or ""
                if isinstance(text, str):
                    text_parts.append(text)
        return "\n".join(text_parts), images
    return str(result), images


