"""Flask application for the AI Session Log Viewer."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import markdown
import nh3
from flask import Flask, Response, abort, jsonify, render_template, request
from markupsafe import Markup

from . import claude_parser, vscode_parser
from .parser import (
    _default_copilot_dir,
    duration_between,
    parse_snapshots,
    parse_workspace,
    ts_display,
)
from .parser import (
    build_conversation as copilot_build_conversation,
)
from .parser import (
    compute_stats as copilot_compute_stats,
)
from .parser import (
    discover_sessions as copilot_discover,
)
from .parser import (
    parse_events as copilot_parse_events,
)

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

# Session IDs are UUIDs — enforce that to prevent path traversal.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Backup hash filenames: hex-timestamp
_BACKUP_HASH_RE = re.compile(r"^[0-9a-f]{16}-\d{13}$")


def _validate_session_id(session_id: str) -> None:
    if not _UUID_RE.match(session_id):
        abort(400, description="Invalid session ID format")


def _validate_backup_hash(backup_hash: str) -> None:
    if not _BACKUP_HASH_RE.match(backup_hash):
        abort(400, description="Invalid backup hash format")


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

_SAFE_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr",
    "ul", "ol", "li",
    "pre", "code", "blockquote",
    "strong", "em", "del", "a", "img",
    "table", "thead", "tbody", "tr", "th", "td",
    "div", "span",
    "dl", "dt", "dd",
    "sub", "sup",
}
_SAFE_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
    "code": {"class"},
    "div": {"class"},
    "span": {"class", "style"},
    "td": {"align"},
    "th": {"align"},
}
_SAFE_URL_SCHEMES = {"http", "https", "mailto"}


def md_to_html(text: str) -> str:
    if not text:
        return ""
    html = markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "codehilite", "nl2br"],
        extension_configs={"codehilite": {"css_class": "codehilite", "guess_lang": False}},
    )
    return nh3.clean(
        html,
        tags=_SAFE_TAGS,
        attributes=_SAFE_ATTRS,
        url_schemes=_SAFE_URL_SCHEMES,
    )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(
    log_dir: str | Path | None = None,
    claude_dir: str | Path | None = None,
    vscode_dir: str | Path | None = None,
) -> Flask:
    """Create and configure the Flask application.

    Parameters
    ----------
    log_dir:
        Root directory containing Copilot session folders.
        Falls back to ``COPILOT_LOG_DIR`` env var, then ``"."``.
    claude_dir:
        Root directory containing Claude Code project/session logs.
        Falls back to ``CLAUDE_LOG_DIR`` env var, then ``~/.claude/projects/``.
    vscode_dir:
        Root directory containing VS Code Chat session logs.
        Falls back to ``VSCODE_LOG_DIR`` env var, then platform default.
    """
    import os

    if log_dir is None:
        log_dir = os.environ.get("COPILOT_LOG_DIR", str(_default_copilot_dir()))
    copilot_path = Path(log_dir).resolve()

    if claude_dir is None:
        claude_dir = os.environ.get("CLAUDE_LOG_DIR", str(claude_parser._default_claude_dir()))
    claude_path = Path(claude_dir).resolve()

    if vscode_dir is None:
        vscode_dir = os.environ.get("VSCODE_LOG_DIR", str(vscode_parser._default_vscode_dir()))
    vscode_path = Path(vscode_dir).resolve()

    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
    )

    # -- Security configuration ----------------------------------------------
    app.config["DEBUG"] = False
    app.config["TESTING"] = False
    app.config["SECRET_KEY"] = os.urandom(32)
    app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB

    # -- Unified session index (cached) ---------------------------------------
    # Maps session_id -> {"source": "copilot"|"claude"|"vscode", "path": str, ...}
    _cache: dict = {"sessions": None, "index": None, "ts": 0.0}
    _CACHE_TTL = 30  # seconds

    def _build_session_index(*, force: bool = False) -> tuple[list[dict], dict[str, dict]]:
        now = time.monotonic()
        if not force and _cache["sessions"] is not None and (now - _cache["ts"]) < _CACHE_TTL:
            return _cache["sessions"], _cache["index"]

        copilot_sessions = copilot_discover(copilot_path)
        for s in copilot_sessions:
            s.setdefault("source", "copilot")

        claude_sessions = claude_parser.discover_sessions(claude_path)
        # claude sessions already have source="claude"

        vscode_sessions = vscode_parser.discover_sessions(vscode_path)
        # vscode sessions already have source="vscode"

        all_sessions = copilot_sessions + claude_sessions + vscode_sessions
        all_sessions.sort(key=lambda s: s.get("created_at", ""), reverse=True)

        index = {}
        for s in all_sessions:
            key = f"{s['source']}:{s['id']}"
            index[key] = s

        _cache["sessions"] = all_sessions
        _cache["index"] = index
        _cache["ts"] = now
        return all_sessions, index

    def _lookup_session(session_id: str) -> dict | None:
        """Look up a session by UUID or composite ``source:uuid`` key.

        For bare UUIDs, returns the unique match or aborts with 400 if the
        same UUID exists in multiple sources (ambiguous).
        """
        _, idx = _build_session_index()

        # Composite key (e.g. "claude:abc-123")
        if ":" in session_id:
            return idx.get(session_id)

        # Bare UUID — collect all matches and detect ambiguity
        matches = [s for src in ("claude", "copilot", "vscode")
                   if (s := idx.get(f"{src}:{session_id}"))]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            abort(400, description="Ambiguous session ID; specify source prefix (e.g. claude:<id>)")
        return None

    # -- Security headers (after every response) -----------------------------
    @app.after_request
    def _security_headers(response: Response) -> Response:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "frame-ancestors 'none'"
        )
        return response

    # -- Routes --------------------------------------------------------------

    @app.route("/")
    def index():
        force = request.args.get("refresh") == "1"
        sessions, _ = _build_session_index(force=force)
        copilot_count = sum(1 for s in sessions if s.get("source") == "copilot")
        claude_count = sum(1 for s in sessions if s.get("source") == "claude")
        vscode_count = sum(1 for s in sessions if s.get("source") == "vscode")
        return render_template(
            "index.html",
            sessions=sessions,
            copilot_dir=str(copilot_path),
            claude_dir=str(claude_path),
            vscode_dir=str(vscode_path),
            copilot_count=copilot_count,
            claude_count=claude_count,
            vscode_count=vscode_count,
        )

    @app.route("/session/<session_id>")
    def session_view(session_id: str):
        _validate_session_id(session_id)

        session_info = _lookup_session(session_id)
        if not session_info:
            abort(404)

        source = session_info.get("source", "copilot")

        if source == "claude":
            session_file = Path(session_info["path"])
            events = claude_parser.parse_events(session_file)
            conversation = claude_parser.build_conversation(events)
            stats = claude_parser.compute_stats(events)
            ws = claude_parser.extract_workspace(events)
            snapshots = {}
        elif source == "vscode":
            session_file = Path(session_info["path"])
            events = vscode_parser.parse_events(session_file)
            conversation = vscode_parser.build_conversation(events)
            stats = vscode_parser.compute_stats(events)
            ws = vscode_parser.extract_workspace(events)
            snapshots = {}
        else:
            session_dir = copilot_path / session_id
            if not session_dir.is_dir():
                abort(404)
            ws = parse_workspace(session_dir)
            events = copilot_parse_events(session_dir)
            conversation = copilot_build_conversation(events)
            stats = copilot_compute_stats(events)
            snapshots = parse_snapshots(session_dir)

        return render_template(
            "session.html",
            ws=ws,
            session_id=session_id,
            conversation=conversation,
            stats=stats,
            snapshots=snapshots,
            source=source,
            ts_display=ts_display,
            duration_between=duration_between,
            md_to_html=md_to_html,
            json=json,
            isinstance=isinstance,
            str=str,
            len=len,
            list=list,
            dict=dict,
            Markup=Markup,
        )

    # -- JSON API ------------------------------------------------------------

    @app.route("/api/sessions")
    def api_sessions():
        sessions, _ = _build_session_index()
        return jsonify(sessions)

    @app.route("/api/session/<session_id>/events")
    def api_events(session_id: str):
        _validate_session_id(session_id)

        session_info = _lookup_session(session_id)
        if not session_info:
            abort(404)

        source = session_info.get("source", "copilot")
        if source == "claude":
            events = claude_parser.parse_events(Path(session_info["path"]))
        elif source == "vscode":
            events = vscode_parser.parse_events(Path(session_info["path"]))
        else:
            session_dir = copilot_path / session_id
            if not session_dir.is_dir():
                abort(404)
            events = copilot_parse_events(session_dir)

        return jsonify(events)

    @app.route("/api/session/<session_id>/backup/<backup_hash>")
    def api_backup(session_id: str, backup_hash: str):
        _validate_session_id(session_id)
        _validate_backup_hash(backup_hash)
        backup_file = copilot_path / session_id / "rewind-snapshots" / "backups" / backup_hash
        # Resolve and verify the path stays within the log directory.
        resolved = backup_file.resolve()
        try:
            resolved.relative_to(copilot_path)
        except ValueError:
            abort(403)
        if not resolved.is_file():
            abort(404)
        content = resolved.read_text(errors="replace")
        return content, 200, {"Content-Type": "text/plain; charset=utf-8"}

    return app
