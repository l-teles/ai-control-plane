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
    assert rendered["prompt"] == "Summarise"
    assert rendered["content"] == "Page summary"


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


def test_websearch_renderer_handles_non_json_result() -> None:
    rendered = render_tool("WebSearch", {"query": "x"}, "not json at all")
    assert rendered["query"] == "x"
    assert rendered["results"] == []
    assert rendered["raw"] == "not json at all"


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
