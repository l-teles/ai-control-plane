"""Convert ANSI-coloured terminal output to safe HTML.

Tools like ``ls``, ``grep --color``, ``rg``, and many language compilers
emit ANSI escape sequences that show up in Claude Bash tool output. This
module parses the SGR colour subset into ``<span class="ansi-…">`` tags
and strips cursor / screen-control sequences so they don't leak through
as garbled characters.

Output is HTML-escaped and intended to be inserted inside a ``<pre>``
block.  No external dependencies — single-pass scanner.
"""

from __future__ import annotations

import html
import re

# Cursor and screen control: ESC [ <params> {ABCDEFGHJKSTfimnsulh}
# We strip these (we only render the final state of the buffer, not the
# ANSI animation).  The 'm' (SGR) form is intercepted *before* this regex.
_CURSOR_CONTROL_RE = re.compile(r"\x1b\[[0-9;?]*[ABCDEFGHJKSTfilnsuh]")
# Other escape sequences (bell, OSC, DCS, single-shift G2/G3) — strip wholesale.
# The ESC N / ESC O single-shift sequences carry a single argument byte from
# the G2/G3 character set; we don't render those alternate sets, so we drop
# the prefix and let the argument byte (if any) fall through as plain text.
# DOTALL so the ``.*?`` in the DCS/SOS/PM/APC branch can match payloads
# that legally contain newlines (the terminator is ``ESC \``, not the end
# of line).
_OTHER_ESC_RE = re.compile(r"\x1b\][^\x07]*\x07|\x1b[PX^_].*?\x1b\\|\x1b[NO]", re.DOTALL)
# SGR (Select Graphic Rendition) — ESC [ <params> m
_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")

# 0-7 foreground / 30-37 maps to standard colours.
_BASIC_FG = {30: "black", 31: "red", 32: "green", 33: "yellow", 34: "blue", 35: "magenta", 36: "cyan", 37: "white"}
_BASIC_BG = {40: "black", 41: "red", 42: "green", 43: "yellow", 44: "blue", 45: "magenta", 46: "cyan", 47: "white"}
_BRIGHT_FG = {
    90: "bright-black",
    91: "bright-red",
    92: "bright-green",
    93: "bright-yellow",
    94: "bright-blue",
    95: "bright-magenta",
    96: "bright-cyan",
    97: "bright-white",
}
_BRIGHT_BG = {
    100: "bright-black",
    101: "bright-red",
    102: "bright-green",
    103: "bright-yellow",
    104: "bright-blue",
    105: "bright-magenta",
    106: "bright-cyan",
    107: "bright-white",
}


class _State:
    """Mutable rendering state for the ANSI scanner."""

    __slots__ = ("fg", "bg", "bold", "italic", "underline", "dim")

    def __init__(self) -> None:
        self.fg: str = ""
        self.bg: str = ""
        self.bold: bool = False
        self.italic: bool = False
        self.underline: bool = False
        self.dim: bool = False

    def reset(self) -> None:
        self.fg = ""
        self.bg = ""
        self.bold = False
        self.italic = False
        self.underline = False
        self.dim = False

    def is_default(self) -> bool:
        return not (self.fg or self.bg or self.bold or self.italic or self.underline or self.dim)

    def classes(self) -> str:
        parts: list[str] = []
        if self.fg:
            parts.append(f"ansi-fg-{self.fg}")
        if self.bg:
            parts.append(f"ansi-bg-{self.bg}")
        if self.bold:
            parts.append("ansi-bold")
        if self.italic:
            parts.append("ansi-italic")
        if self.underline:
            parts.append("ansi-underline")
        if self.dim:
            parts.append("ansi-dim")
        return " ".join(parts)


def _apply_codes(state: _State, codes: list[int]) -> None:
    """Apply SGR codes to *state*, mutating it in place."""
    if not codes:
        codes = [0]  # Empty SGR = reset
    i = 0
    while i < len(codes):
        c = codes[i]
        if c == 0:
            state.reset()
        elif c == 1:
            state.bold = True
        elif c == 2:
            state.dim = True
        elif c == 3:
            state.italic = True
        elif c == 4:
            state.underline = True
        elif c == 22:
            state.bold = False
            state.dim = False
        elif c == 23:
            state.italic = False
        elif c == 24:
            state.underline = False
        elif c == 39:
            state.fg = ""
        elif c == 49:
            state.bg = ""
        elif c in _BASIC_FG:
            state.fg = _BASIC_FG[c]
        elif c in _BASIC_BG:
            state.bg = _BASIC_BG[c]
        elif c in _BRIGHT_FG:
            state.fg = _BRIGHT_FG[c]
        elif c in _BRIGHT_BG:
            state.bg = _BRIGHT_BG[c]
        elif c == 38 and i + 2 < len(codes) and codes[i + 1] == 5:
            # 256-colour foreground: 38;5;<n>
            state.fg = f"256-{codes[i + 2]}"
            i += 2
        elif c == 48 and i + 2 < len(codes) and codes[i + 1] == 5:
            state.bg = f"256-{codes[i + 2]}"
            i += 2
        # Truecolour (38;2;r;g;b) and unknown codes are skipped — keeping
        # the output plain rather than smuggling raw RGB into class names.
        i += 1


def ansi_to_html(text: str) -> str:
    """Convert ANSI-coloured *text* to HTML-safe markup with span classes.

    Cursor / screen control sequences are stripped. Output is plain HTML
    text (``<span class="ansi-…">…</span>``) — caller is responsible for
    placing it in a ``<pre>`` block for whitespace preservation.
    """
    if not text:
        return ""

    # Strip cursor/screen control and other non-SGR sequences first.
    text = _CURSOR_CONTROL_RE.sub("", text)
    text = _OTHER_ESC_RE.sub("", text)

    state = _State()
    out: list[str] = []
    last_end = 0
    span_open = False

    def open_span() -> str:
        cls = state.classes()
        return f'<span class="{cls}">' if cls else ""

    for m in _SGR_RE.finditer(text):
        # Plain text before this escape — emit, possibly inside the
        # current span.
        chunk = text[last_end : m.start()]
        if chunk:
            if span_open:
                out.append(html.escape(chunk))
            elif not state.is_default():
                opener = open_span()
                if opener:
                    out.append(opener)
                    span_open = True
                    out.append(html.escape(chunk))
                else:
                    out.append(html.escape(chunk))
            else:
                out.append(html.escape(chunk))

        # Close any open span before applying the new SGR code.
        if span_open:
            out.append("</span>")
            span_open = False

        params = m.group(1)
        codes: list[int] = []
        if params:
            for piece in params.split(";"):
                if piece.isdigit():
                    codes.append(int(piece))
        _apply_codes(state, codes)
        last_end = m.end()

    # Trailing text after the last escape.
    tail = text[last_end:]
    if tail:
        if not state.is_default():
            opener = open_span()
            if opener:
                out.append(opener)
                out.append(html.escape(tail))
                out.append("</span>")
            else:
                out.append(html.escape(tail))
        else:
            out.append(html.escape(tail))

    return "".join(out)


def strip_ansi(text: str) -> str:
    """Return *text* with all ANSI escape sequences removed (no HTML)."""
    if not text:
        return ""
    text = _CURSOR_CONTROL_RE.sub("", text)
    text = _OTHER_ESC_RE.sub("", text)
    text = _SGR_RE.sub("", text)
    return text
