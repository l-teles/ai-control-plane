"""Tests for the ANSI -> HTML converter."""

from __future__ import annotations

from ai_ctrl_plane.ansi import ansi_to_html, strip_ansi


def test_plain_text_passes_through_with_html_escape() -> None:
    assert ansi_to_html("hello & <world>") == "hello &amp; &lt;world&gt;"


def test_basic_foreground_color() -> None:
    out = ansi_to_html("\x1b[31mERROR\x1b[0m")
    assert '<span class="ansi-fg-red">ERROR</span>' in out


def test_compound_attributes() -> None:
    out = ansi_to_html("\x1b[1;32mok\x1b[0m")
    # Bold + green
    assert "ansi-bold" in out
    assert "ansi-fg-green" in out


def test_reset_closes_span_and_returns_to_default() -> None:
    out = ansi_to_html("\x1b[31mred\x1b[0m plain \x1b[34mblue\x1b[0m")
    assert out.count("<span") == 2
    assert out.count("</span>") == 2
    assert "ansi-fg-red" in out
    assert "ansi-fg-blue" in out


def test_implicit_reset_on_color_change() -> None:
    """Switching colour mid-stream should close the previous span and open a new one."""
    out = ansi_to_html("\x1b[31mred\x1b[32mgreen\x1b[0m")
    assert out.count("<span") == 2
    assert out.count("</span>") == 2


def test_cursor_control_sequences_are_stripped() -> None:
    """\x1b[2K (clear line) and \x1b[A (cursor up) shouldn't leak into output."""
    out = ansi_to_html("before\x1b[2K\x1b[Aafter")
    assert "before" in out
    assert "after" in out
    assert "\x1b" not in out
    assert "[2K" not in out


def test_strip_ansi_returns_clean_text() -> None:
    assert strip_ansi("\x1b[31mhi\x1b[0m") == "hi"
    assert strip_ansi("plain") == "plain"
    assert strip_ansi("") == ""


def test_unknown_codes_dont_produce_spans_or_break() -> None:
    """A truecolour (38;2;r;g;b) sequence — we don't render it, but we mustn't crash."""
    out = ansi_to_html("\x1b[38;2;255;100;0morange?\x1b[0m")
    assert "orange?" in out


def test_256_color_class_name() -> None:
    out = ansi_to_html("\x1b[38;5;208mfancy\x1b[0m")
    # Class encodes the colour index so CSS / a future palette lookup can
    # paint it consistently.
    assert "ansi-fg-256-208" in out


def test_unclosed_span_at_end_of_string_gets_closed() -> None:
    out = ansi_to_html("\x1b[31mtrailing")
    assert out.endswith("</span>")
    assert "trailing" in out


def test_html_escapes_inside_span() -> None:
    out = ansi_to_html("\x1b[31m<bad>\x1b[0m")
    assert "&lt;bad&gt;" in out
    assert "<bad>" not in out
