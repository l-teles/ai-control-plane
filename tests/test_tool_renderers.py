"""Tests for per-tool renderers and tool_result normalisation."""

from __future__ import annotations

import json

from ai_ctrl_plane.tool_renderers import (
    extract_images_from_result,
    render_tool,
    render_tool_result,
)


def test_bash_renderer_strips_ansi_into_html() -> None:
    rendered = render_tool("Bash", {"command": "ls -la", "description": "list"}, "\x1b[31mERROR\x1b[0m foo")
    assert rendered["tool"] == "Bash"
    assert rendered["command"] == "ls -la"
    assert rendered["description"] == "list"
    assert "ansi-fg-red" in rendered["output_html"]
    assert "ERROR" in rendered["output_html"]
    assert "\x1b" not in rendered["output_html"]
    assert rendered["title"] == "ls -la"


def test_read_renderer_extracts_path_and_basename() -> None:
    rendered = render_tool("Read", {"file_path": "/repo/src/foo.py", "offset": 10, "limit": 50}, "1: import os\n")
    assert rendered["title"] == "foo.py"
    assert rendered["subtitle"] == "/repo/src/foo.py"
    assert rendered["offset"] == 10
    assert rendered["limit"] == 50


def test_edit_renderer_carries_old_and_new() -> None:
    rendered = render_tool(
        "Edit",
        {"file_path": "/x.py", "old_string": "foo", "new_string": "bar", "replace_all": True},
        "edited",
    )
    assert rendered["old_string"] == "foo"
    assert rendered["new_string"] == "bar"
    assert rendered["replace_all"] is True


def test_grep_renderer_carries_pattern_and_path() -> None:
    rendered = render_tool("Grep", {"pattern": "TODO", "path": "src/", "type": "py"}, "src/foo.py:12: # TODO")
    assert rendered["pattern"] == "TODO"
    assert rendered["path"] == "src/"
    assert rendered["type"] == "py"
    assert rendered["title"] == "TODO"


def test_glob_renderer_splits_match_lines() -> None:
    rendered = render_tool("Glob", {"pattern": "*.py", "path": "/tmp"}, "/tmp/a.py\n/tmp/b.py\n")
    assert rendered["match_count"] == 2
    assert "/tmp/a.py" in rendered["matches"]


def test_webfetch_renderer_carries_url_and_prompt() -> None:
    rendered = render_tool(
        "WebFetch",
        {"url": "https://example.com", "prompt": "Summarise"},
        "Page summary",
    )
    assert rendered["url"] == "https://example.com"
    assert rendered["url_display"] == "https://example.com"
    assert rendered["prompt"] == "Summarise"
    assert rendered["content"] == "Page summary"


def test_webfetch_renderer_strips_unsafe_url_scheme() -> None:
    """A ``javascript:`` URL would execute on click if rendered into an
    ``<a href>``. The renderer empties ``url`` (so the template falls
    back to non-clickable text) but keeps the original on
    ``url_display`` for visibility. Regression for PR #27 review #20."""
    rendered = render_tool(
        "WebFetch",
        {"url": "javascript:alert(1)", "prompt": "x"},
        "",
    )
    assert rendered["url"] == ""  # blocked
    assert rendered["url_display"] == "javascript:alert(1)"


def test_webfetch_renderer_passes_through_safe_schemes() -> None:
    for scheme in ("http", "https", "mailto"):
        url = f"{scheme}:foo@example.com" if scheme == "mailto" else f"{scheme}://example.com/path"
        rendered = render_tool("WebFetch", {"url": url, "prompt": ""}, "")
        assert rendered["url"] == url, f"expected {scheme} to pass through"


def test_websearch_renderer_parses_structured_results() -> None:
    payload = json.dumps(
        [
            {"title": "Hit 1", "url": "https://a.example", "snippet": "one"},
            {"title": "Hit 2", "url": "https://b.example", "description": "two"},
        ]
    )
    rendered = render_tool("WebSearch", {"query": "python"}, payload)
    assert rendered["query"] == "python"
    assert len(rendered["results"]) == 2
    assert rendered["results"][0]["url"] == "https://a.example"
    assert rendered["results"][1]["snippet"] == "two"


def test_websearch_renderer_strips_unsafe_url_schemes_in_results() -> None:
    """Per-hit URLs go through the same sanitiser as WebFetch — a
    ``javascript:`` payload from a malicious search result would be
    rendered into ``<a href>`` and execute on click without this.
    Regression for PR #27 review #21."""
    payload = json.dumps(
        [
            {"title": "Safe", "url": "https://safe.example", "snippet": "ok"},
            {"title": "Malicious", "url": "javascript:alert(1)", "snippet": "evil"},
        ]
    )
    rendered = render_tool("WebSearch", {"query": "x"}, payload)
    safe, evil = rendered["results"]
    assert safe["url"] == "https://safe.example"
    assert evil["url"] == ""  # blocked
    assert evil["url_display"] == "javascript:alert(1)"  # but visible as text


def test_websearch_renderer_handles_non_json_result() -> None:
    rendered = render_tool("WebSearch", {"query": "x"}, "not json at all")
    assert rendered["query"] == "x"
    assert rendered["results"] == []
    assert rendered["raw"] == "not json at all"


def test_websearch_renderer_parses_payload_larger_than_legacy_truncation_cap() -> None:
    """Tool results used to be truncated to MAX_RESULT_CHARS (10_000)
    BEFORE being passed to render_tool, which broke WebSearch when its
    JSON payload exceeded that — it'd hit a parse error and lose the
    structured cards. Renderers now get the full text. Regression for
    PR #27 review comment 11."""
    # Build a JSON payload that's well over 10_000 chars.
    big_payload = json.dumps(
        [{"title": f"Hit {i}", "url": f"https://example.com/{i}", "snippet": "x" * 200} for i in range(60)]
    )
    assert len(big_payload) > 10_000  # sanity
    rendered = render_tool("WebSearch", {"query": "python"}, big_payload)
    # Parsing succeeded — structured results came through, not the raw fallback.
    assert len(rendered["results"]) == 60
    assert rendered["results"][0]["url"] == "https://example.com/0"
    assert rendered["results"][-1]["url"] == "https://example.com/59"


def test_ask_user_question_renderer() -> None:
    rendered = render_tool(
        "AskUserQuestion",
        {"question": "Pick one", "options": ["a", "b", "c"]},
        "a",
    )
    assert rendered["question"] == "Pick one"
    assert rendered["options"] == ["a", "b", "c"]
    assert rendered["answer"] == "a"


def test_unknown_tool_falls_through_to_generic() -> None:
    rendered = render_tool("CustomTool", {"file_path": "/x", "extra": True}, "result text")
    assert rendered["tool"] == "CustomTool"
    assert rendered["title"] == "/x"
    assert "extra" in rendered["input_json"]
    assert rendered["output"] == "result text"


def test_extract_images_from_base64_block() -> None:
    blocks = [
        {"type": "text", "text": "Here is the screenshot:"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo"}},
    ]
    images = extract_images_from_result(blocks)
    assert len(images) == 1
    assert images[0]["src"].startswith("data:image/png;base64,")


def test_extract_images_from_data_url_in_text() -> None:
    images = extract_images_from_result("data:image/jpeg;base64,/9j/4AAQ")
    assert len(images) == 1
    assert images[0]["src"] == "data:image/jpeg;base64,/9j/4AAQ"


def test_render_tool_result_returns_text_and_images_separately() -> None:
    blocks = [
        {"type": "text", "text": "Look:"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}},
        {"type": "text", "text": "Done"},
    ]
    text, images = render_tool_result(blocks)
    assert "Look:" in text and "Done" in text
    assert len(images) == 1


def test_render_tool_result_drops_text_when_string_is_pure_image() -> None:
    text, images = render_tool_result("data:image/png;base64,AAA")
    assert text == ""
    assert len(images) == 1


def test_render_tool_result_handles_null_text_block() -> None:
    """A tool_result text block with ``"text": null`` shouldn't crash —
    Python's ``"\\n".join`` raises ``TypeError`` on a None element."""
    blocks = [
        {"type": "text", "text": None},
        {"type": "text", "text": "real content"},
    ]
    text, images = render_tool_result(blocks)
    assert text == "\nreal content"
    assert images == []


def test_extract_images_rejects_svg_mime_type() -> None:
    """SVG image blocks would render JS when placed in <img>; reject."""
    blocks = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/svg+xml", "data": "PHN2Zw=="},
        },
    ]
    images = extract_images_from_result(blocks)
    assert images == []


def test_extract_images_rejects_unknown_mime_type() -> None:
    """Anything outside the safe-raster allowlist gets dropped."""
    blocks = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "text/html", "data": "PGh0bWw+"},
        },
    ]
    assert extract_images_from_result(blocks) == []


def test_extract_images_accepts_safe_raster_types() -> None:
    for media in ("image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"):
        blocks = [{"type": "image", "source": {"type": "base64", "media_type": media, "data": "AAA"}}]
        images = extract_images_from_result(blocks)
        assert len(images) == 1, f"expected {media} to be accepted"
        assert images[0]["src"].startswith(f"data:{media};base64,")


def test_extract_images_drops_oversized_block_image() -> None:
    """An image block whose base64 payload exceeds the cap is dropped
    silently to keep the HTML response from ballooning. Regression for
    PR #27 review comment 15."""
    huge = "A" * 2_000_001  # > _MAX_IMAGE_BASE64_SIZE
    blocks = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": huge}}]
    assert extract_images_from_result(blocks) == []


def test_extract_images_drops_oversized_data_url_in_text_block() -> None:
    """Same cap applies to data URLs that arrive inside a text block."""
    huge_data_url = "data:image/png;base64," + ("A" * 2_000_001)
    blocks = [{"type": "text", "text": huge_data_url}]
    assert extract_images_from_result(blocks) == []


def test_extract_images_handles_non_dict_source() -> None:
    """A tool_result image block with a non-dict ``source`` (list /
    string / null / int) used to crash ``source.get(...)`` because
    ``block.get('source') or {}`` returns the raw value when it's
    truthy non-dict. Regression for PR #27 review #57."""
    bad_blocks = [
        {"type": "image", "source": ["not", "a", "dict"]},
        {"type": "image", "source": "string"},
        {"type": "image", "source": None},
        {"type": "image", "source": 42},
    ]
    for block in bad_blocks:
        assert extract_images_from_result([block]) == [], f"block {block!r} should be skipped"


def test_extract_images_drops_oversized_data_url_string() -> None:
    """And to results that are a bare data URL string."""
    huge_data_url = "data:image/png;base64," + ("A" * 2_000_001)
    assert extract_images_from_result(huge_data_url) == []


def test_extract_images_data_url_size_cap_matches_block_image_cap() -> None:
    """The size cap should be applied to the *base64 payload*, not the
    full ``data:image/...;base64,DATA`` URL — otherwise the data-URL
    path is ~30 bytes stricter than the block-image path. A payload
    *exactly* at the cap should pass through both. Regression for PR
    #27 review #46."""
    # 2_000_000 base64 chars → exactly at the cap. Must accept.
    at_cap = "A" * 2_000_000
    block_form = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": at_cap}}
    ]
    string_form = "data:image/png;base64," + at_cap
    text_block_form = [{"type": "text", "text": string_form}]

    assert len(extract_images_from_result(block_form)) == 1
    assert len(extract_images_from_result(string_form)) == 1
    assert len(extract_images_from_result(text_block_form)) == 1

    # 2_000_001 base64 chars → just over the cap. Must reject in all
    # three forms.
    over_cap = "A" * 2_000_001
    assert extract_images_from_result(
        [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": over_cap}}]
    ) == []
    assert extract_images_from_result("data:image/png;base64," + over_cap) == []
    assert extract_images_from_result([{"type": "text", "text": "data:image/png;base64," + over_cap}]) == []


def test_extract_images_handles_malformed_block_payload() -> None:
    """Image blocks with non-string ``media_type`` / ``data`` (e.g. lists,
    None, ints from a malformed MCP tool result) must not crash the
    whole conversation render — skip the block silently. Regression for
    PR #27 review #25."""
    bad_blocks = [
        # media_type is a list — would raise on ``in`` against a frozenset
        {"type": "image", "source": {"type": "base64", "media_type": ["image/png"], "data": "AAA"}},
        # media_type is None
        {"type": "image", "source": {"type": "base64", "media_type": None, "data": "AAA"}},
        # data is an int — would raise on ``len()``
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": 42}},
        # data is None
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": None}},
        # text block with non-string ``text`` field
        {"type": "text", "text": 42},
        {"type": "text", "text": None},
    ]
    for block in bad_blocks:
        assert extract_images_from_result([block]) == [], f"block {block!r} should be skipped"

    # And a mix of bad + good still surfaces the good one.
    mixed = [
        {"type": "image", "source": {"type": "base64", "media_type": ["broken"], "data": "x"}},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}},
    ]
    images = extract_images_from_result(mixed)
    assert len(images) == 1
    assert images[0]["src"].startswith("data:image/png;base64,")


def test_renderers_defend_non_string_tool_input_fields() -> None:
    """Every renderer that takes a ``tool_input`` field must coerce
    non-string values (None, int, list, dict) without raising. A
    malformed transcript or quirky MCP tool can put unexpected types
    in any field; renderers should fail soft. Sweep regression for
    PR #27 review #27.
    """
    bad_input = {
        "command": 42,
        "description": ["not", "a", "string"],
        "file_path": None,
        "content": 123,
        "old_string": [],
        "new_string": {"x": 1},
        "pattern": None,
        "path": [],
        "url": 5,
        "prompt": None,
        "query": [],
        "question": {},
        "options": "not-a-list-but-OK",
        "todos": "also-not-a-list",
    }

    # Each of these used to crash on non-string fields. Now they all
    # produce a structured dict with safe defaults and don't raise.
    for tool in (
        "Bash", "Read", "Write", "Edit", "Grep", "Glob",
        "WebFetch", "WebSearch", "AskUserQuestion", "TodoWrite",
    ):
        rendered = render_tool(tool, bad_input, None)  # also non-string result
        assert isinstance(rendered, dict)
        assert rendered["tool"] == tool


def test_todowrite_renderer_filters_non_dict_items() -> None:
    """The template calls ``t.get('status')`` / ``t.get('content')`` on
    each todo, which crashes on ints / strings / None from a malformed
    transcript. Filter to dict items only. Regression for PR #27
    review #34."""
    rendered = render_tool(
        "TodoWrite",
        {
            "todos": [
                {"content": "real task", "status": "pending"},
                "not a dict",
                42,
                None,
                {"content": "another task", "status": "completed"},
            ]
        },
        "",
    )
    # Only the two dict items survive.
    assert len(rendered["todos"]) == 2
    assert rendered["todos"][0]["content"] == "real task"
    assert rendered["todos"][1]["content"] == "another task"
    # Title reflects the post-filter count, not the raw count.
    assert rendered["title"] == "2 tasks"


def test_render_tool_dispatcher_coerces_non_dict_input() -> None:
    """``render_tool`` itself must defend the dispatcher boundary —
    a non-dict ``tool_input`` (e.g. list / str / None from a malformed
    transcript) should be coerced to ``{}`` rather than passed through
    to a renderer that calls ``.get`` on it. Regression for PR #27
    review #29."""
    for bad_input in (None, [], "", 0, ["a", "b"]):
        rendered = render_tool("Bash", bad_input, "")
        assert rendered["tool"] == "Bash"
        assert rendered["command"] == ""


def test_websearch_renderer_handles_non_string_fields_in_payload() -> None:
    """A WebSearch JSON payload from a malformed/malicious MCP server
    might include non-string ``title`` / ``url`` / ``snippet`` values.
    The renderer should coerce / drop rather than crash. Sweep follow-up
    to PR #27 review #25."""
    payload = json.dumps(
        [
            {"title": 42, "url": ["https://x.example"], "snippet": None},
            {"title": "ok", "url": "https://safe.example", "snippet": 99},
        ]
    )
    rendered = render_tool("WebSearch", {"query": "x"}, payload)
    assert len(rendered["results"]) == 2
    bad, good = rendered["results"]
    # Bad entry coerced to safe defaults — no crash.
    assert bad["title"] == ""
    assert bad["url"] == ""
    assert bad["snippet"] == ""
    # Good entry passes through.
    assert good["title"] == "ok"
    assert good["url"] == "https://safe.example"
    assert good["snippet"] == ""  # snippet was 99, an int — coerced to ""
