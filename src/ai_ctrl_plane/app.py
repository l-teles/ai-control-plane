"""Flask application for the AI Control Plane."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import markdown
import nh3
from flask import Flask, Response, abort, jsonify, render_template, request
from markupsafe import Markup
from werkzeug.utils import secure_filename

from . import claude_parser, vscode_parser
from .config_readers import read_all_configs
from .config_readers.claude_config import read_claude_config
from .config_readers.copilot_config import read_copilot_config
from .config_readers.vscode_config import read_vscode_config
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


def _safe_copilot_dir(base: Path, session_id: str) -> Path:
    """Build and validate a Copilot session path, preventing traversal."""
    safe_name = secure_filename(session_id)
    if not safe_name or safe_name != session_id:
        abort(400)
    resolved = (base / safe_name).resolve()
    try:
        resolved.relative_to(base.resolve())
    except ValueError:
        abort(403)
    return resolved


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

_SAFE_TAGS = {
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "br",
    "hr",
    "ul",
    "ol",
    "li",
    "pre",
    "code",
    "blockquote",
    "strong",
    "em",
    "del",
    "a",
    "img",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "div",
    "span",
    "dl",
    "dt",
    "dd",
    "sub",
    "sup",
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
        matches = [s for src in ("claude", "copilot", "vscode") if (s := idx.get(f"{src}:{session_id}"))]
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
        configs = read_all_configs()
        c = configs["claude"]
        cp = configs["copilot"]
        v = configs["vscode"]
        return render_template(
            "index.html",
            sessions=sessions,
            copilot_dir=str(copilot_path),
            claude_dir=str(claude_path),
            vscode_dir=str(vscode_path),
            copilot_count=copilot_count,
            claude_count=claude_count,
            vscode_count=vscode_count,
            configs=configs,
            total_sessions=len(sessions),
            total_mcp_servers=len(c.get("mcp_servers", []))
            + len(cp.get("mcp_servers", []))
            + len(v.get("mcp_servers", [])),
            total_plugins=len(c.get("plugins", [])) + len(c.get("external_plugins", [])),
            total_agents=len(c.get("agents", [])) + len(v.get("agents", [])),
            total_hooks=len(c.get("hooks", [])),
            total_commands=len(c.get("commands", [])),
            total_feature_flags=len(c.get("feature_flags", {})) + len(c.get("growthbook_flags", {})),
            total_skills=len(c.get("skills", [])) + len(cp.get("skills", [])) + len(v.get("skills", [])),
        )

    @app.route("/sessions")
    def sessions_view():
        force = request.args.get("refresh") == "1"
        sessions, _ = _build_session_index(force=force)
        copilot_count = sum(1 for s in sessions if s.get("source") == "copilot")
        claude_count = sum(1 for s in sessions if s.get("source") == "claude")
        vscode_count = sum(1 for s in sessions if s.get("source") == "vscode")
        return render_template(
            "sessions.html",
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
            conv_events = claude_parser.parse_events_for_conversation(session_file)
            conversation = claude_parser.build_conversation(conv_events)
            stats = claude_parser.compute_stats(events)
            ws = claude_parser.extract_workspace(events)
            snapshots: dict = {}
        elif source == "vscode":
            session_file = Path(session_info["path"])
            events = vscode_parser.parse_events(session_file)
            conversation = vscode_parser.build_conversation(events)
            stats = vscode_parser.compute_stats(events)
            ws = vscode_parser.extract_workspace(events)
            snapshots = {}
        else:
            session_dir = _safe_copilot_dir(copilot_path, session_id)
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

    # -- Tools configuration routes ------------------------------------------

    _VALID_TOOLS = {"claude", "copilot", "vscode"}

    def _get_tool_config(tool: str) -> dict:
        if tool == "claude":
            return read_claude_config()
        elif tool == "copilot":
            return read_copilot_config()
        elif tool == "vscode":
            return read_vscode_config()
        abort(404)

    @app.route("/agents")
    def agents_view():
        configs = read_all_configs()
        agents: list[dict] = []
        for a in configs["claude"].get("agents", []):
            agents.append({**a, "source": "claude"})
        for a in configs["vscode"].get("agents", []):
            agents.append({**a, "source": "vscode"})
        claude_agent_count = sum(1 for a in agents if a["source"] == "claude")
        vscode_agent_count = sum(1 for a in agents if a["source"] == "vscode")
        return render_template(
            "agents.html",
            agents=agents,
            claude_agent_count=claude_agent_count,
            vscode_agent_count=vscode_agent_count,
        )

    def _collect_skills() -> list[dict]:
        """Collect skills from all tools, deduplicated by name.

        When the same skill is installed in multiple tools, merge into a
        single entry with a ``sources`` list.
        """
        configs = read_all_configs()
        by_name: dict[str, dict] = {}
        for source in ("claude", "copilot", "vscode"):
            for s in configs[source].get("skills", []):
                name = s["name"]
                if name in by_name:
                    if source not in by_name[name]["sources"]:
                        by_name[name]["sources"].append(source)
                else:
                    by_name[name] = {**s, "sources": [source]}
        return sorted(by_name.values(), key=lambda s: s["name"])

    @app.route("/skills")
    def skills_view():
        skills = _collect_skills()
        claude_skill_count = sum(1 for s in skills if "claude" in s["sources"])
        copilot_skill_count = sum(1 for s in skills if "copilot" in s["sources"])
        vscode_skill_count = sum(1 for s in skills if "vscode" in s["sources"])
        return render_template(
            "skills.html",
            skills=skills,
            claude_skill_count=claude_skill_count,
            copilot_skill_count=copilot_skill_count,
            vscode_skill_count=vscode_skill_count,
        )

    @app.route("/skills/<skill_name>")
    def skill_detail_view(skill_name: str):
        skills = _collect_skills()
        skill = next((s for s in skills if s["name"] == skill_name), None)
        if not skill:
            abort(404)
        return render_template(
            "skill_detail.html",
            skill=skill,
            md_to_html=md_to_html,
        )

    @app.route("/tools")
    def tools_overview():
        configs = read_all_configs()
        # Compute shared MCP servers (present in 2+ tools)
        server_sources: dict[str, list[str]] = {}
        for source in ("claude", "copilot", "vscode"):
            for srv in configs[source].get("mcp_servers", []):
                server_sources.setdefault(srv["name"], []).append(source)
        shared_servers = [
            {"name": name, "sources": sources} for name, sources in sorted(server_sources.items()) if len(sources) > 1
        ]
        return render_template("tools.html", configs=configs, shared_servers=shared_servers)

    # Claude settings.json key descriptions (from code.claude.com/docs/en/settings)
    _CLAUDE_SETTINGS_META: dict[str, dict] = {
        "apiKeyHelper": {"desc": "Custom script to generate an auth value for API requests", "type": "string"},
        "autoMemoryDirectory": {"desc": "Custom directory for auto memory storage", "type": "path"},
        "cleanupPeriodDays": {"desc": "Days before inactive sessions are deleted (default: 30)", "type": "number"},
        "companyAnnouncements": {"desc": "Announcements displayed to users at startup", "type": "array"},
        "env": {"desc": "Environment variables applied to every session", "type": "object"},
        "attribution": {"desc": "Customize attribution for git commits and pull requests", "type": "object"},
        "includeCoAuthoredBy": {
            "desc": "Include Claude co-author byline (deprecated, use attribution)",
            "type": "bool",
        },
        "includeGitInstructions": {"desc": "Include commit/PR workflow instructions in system prompt", "type": "bool"},
        "permissions": {"desc": "Permission rules: allow, ask, and deny lists for tool access", "type": "object"},
        "hooks": {"desc": "Custom commands that run at lifecycle events", "type": "object"},
        "disableAllHooks": {"desc": "Disable all hooks and custom status line", "type": "bool"},
        "allowManagedHooksOnly": {"desc": "Only allow managed hooks (managed settings only)", "type": "bool"},
        "allowedHttpHookUrls": {"desc": "URL patterns that HTTP hooks may target", "type": "array"},
        "httpHookAllowedEnvVars": {
            "desc": "Environment variables HTTP hooks may interpolate into headers",
            "type": "array",
        },
        "allowManagedPermissionRulesOnly": {
            "desc": "Only managed permission rules apply (managed settings only)",
            "type": "bool",
        },
        "allowManagedMcpServersOnly": {
            "desc": "Only admin-defined MCP server allowlist applies (managed settings only)",
            "type": "bool",
        },
        "model": {"desc": "Override the default model for Claude Code", "type": "string"},
        "availableModels": {"desc": "Restrict which models users can select", "type": "array"},
        "modelOverrides": {"desc": "Map Anthropic model IDs to provider-specific model IDs", "type": "object"},
        "effortLevel": {"desc": "Persist effort level across sessions (low/medium/high)", "type": "string"},
        "otelHeadersHelper": {"desc": "Script to generate dynamic OpenTelemetry headers", "type": "string"},
        "statusLine": {"desc": "Custom status line command or configuration", "type": "object"},
        "fileSuggestion": {"desc": "Custom script for @ file autocomplete", "type": "object"},
        "respectGitignore": {"desc": "Whether the @ file picker respects .gitignore patterns", "type": "bool"},
        "outputStyle": {"desc": "Output style to adjust system prompt behavior", "type": "string"},
        "forceLoginMethod": {"desc": "Restrict login to claudeai or console accounts", "type": "string"},
        "forceLoginOrgUUID": {"desc": "Auto-select organization during login", "type": "string"},
        "enableAllProjectMcpServers": {"desc": "Auto-approve all MCP servers in project .mcp.json", "type": "bool"},
        "enabledMcpjsonServers": {"desc": "Specific MCP servers from .mcp.json to approve", "type": "array"},
        "disabledMcpjsonServers": {"desc": "Specific MCP servers from .mcp.json to reject", "type": "array"},
        "allowedMcpServers": {"desc": "Allowlist of MCP servers users can configure (managed only)", "type": "array"},
        "deniedMcpServers": {"desc": "Denylist of explicitly blocked MCP servers (managed only)", "type": "array"},
        "strictKnownMarketplaces": {
            "desc": "Allowlist of plugin marketplaces users can add (managed only)",
            "type": "array",
        },
        "blockedMarketplaces": {"desc": "Blocklist of marketplace sources (managed only)", "type": "array"},
        "pluginTrustMessage": {
            "desc": "Custom message appended to plugin trust warning (managed only)",
            "type": "string",
        },
        "awsAuthRefresh": {"desc": "Custom script to refresh AWS credentials", "type": "string"},
        "awsCredentialExport": {"desc": "Custom script that outputs JSON with AWS credentials", "type": "string"},
        "alwaysThinkingEnabled": {"desc": "Enable extended thinking by default for all sessions", "type": "bool"},
        "plansDirectory": {"desc": "Custom directory for plan file storage", "type": "path"},
        "showTurnDuration": {"desc": "Show turn duration messages after responses", "type": "bool"},
        "spinnerVerbs": {"desc": "Customize action verbs in spinner and duration messages", "type": "object"},
        "language": {"desc": "Preferred response language", "type": "string"},
        "autoUpdatesChannel": {"desc": "Release channel: stable (week-old) or latest (default)", "type": "string"},
        "spinnerTipsEnabled": {"desc": "Show tips in spinner while working", "type": "bool"},
        "spinnerTipsOverride": {"desc": "Override spinner tips with custom strings", "type": "object"},
        "terminalProgressBarEnabled": {"desc": "Enable terminal progress bar in supported terminals", "type": "bool"},
        "prefersReducedMotion": {"desc": "Reduce or disable UI animations for accessibility", "type": "bool"},
        "fastModePerSessionOptIn": {"desc": "Require per-session opt-in for fast mode", "type": "bool"},
        "teammateMode": {"desc": "How agent team teammates display (auto/in-process/tmux)", "type": "string"},
        "feedbackSurveyRate": {"desc": "Probability (0-1) that session quality survey appears", "type": "number"},
        "worktree.symlinkDirectories": {
            "desc": "Directories to symlink into worktrees to save disk space",
            "type": "array",
        },
        "worktree.sparsePaths": {
            "desc": "Directories to check out via git sparse-checkout in worktrees",
            "type": "array",
        },
        "sandbox.enabled": {"desc": "Enable bash sandboxing", "type": "bool"},
        "sandbox.autoAllowBashIfSandboxed": {"desc": "Auto-approve bash commands when sandboxed", "type": "bool"},
        "sandbox.excludedCommands": {"desc": "Commands that run outside the sandbox", "type": "array"},
        "sandbox.allowUnsandboxedCommands": {
            "desc": "Allow commands to bypass sandbox via dangerouslyDisableSandbox",
            "type": "bool",
        },
        "sandbox.filesystem.allowWrite": {"desc": "Additional writable paths for sandboxed commands", "type": "array"},
        "sandbox.filesystem.denyWrite": {"desc": "Paths where sandboxed commands cannot write", "type": "array"},
        "sandbox.filesystem.denyRead": {"desc": "Paths where sandboxed commands cannot read", "type": "array"},
        "sandbox.network.allowUnixSockets": {"desc": "Unix socket paths accessible in sandbox", "type": "array"},
        "sandbox.network.allowAllUnixSockets": {"desc": "Allow all Unix socket connections in sandbox", "type": "bool"},
        "sandbox.network.allowLocalBinding": {"desc": "Allow binding to localhost ports (macOS only)", "type": "bool"},
        "sandbox.network.allowedDomains": {"desc": "Domains allowed for outbound network traffic", "type": "array"},
        "sandbox.network.allowManagedDomainsOnly": {
            "desc": "Only managed network domain allowlists apply",
            "type": "bool",
        },
        "sandbox.network.httpProxyPort": {"desc": "HTTP proxy port for sandbox", "type": "number"},
        "sandbox.network.socksProxyPort": {"desc": "SOCKS5 proxy port for sandbox", "type": "number"},
        "sandbox.enableWeakerNestedSandbox": {
            "desc": "Weaker sandbox for unprivileged Docker (reduces security)",
            "type": "bool",
        },
        "sandbox.enableWeakerNetworkIsolation": {
            "desc": "Allow TLS trust service access in sandbox (reduces security)",
            "type": "bool",
        },
    }

    def _parse_claude_settings(settings: dict) -> list[dict]:
        """Parse Claude settings into annotated list with descriptions."""
        result = []
        for key, value in sorted(settings.items()):
            meta = _CLAUDE_SETTINGS_META.get(key, {})
            result.append(
                {
                    "key": key,
                    "value": value,
                    "desc": meta.get("desc", ""),
                    "type": meta.get("type", "unknown"),
                }
            )
        return result

    @app.route("/tools/<tool>")
    def tool_detail(tool: str):
        if tool not in _VALID_TOOLS:
            abort(404)
        config = _get_tool_config(tool)
        parsed_settings = []
        if tool == "claude" and config.get("settings"):
            parsed_settings = _parse_claude_settings(config["settings"])
        return render_template(
            "tool_detail.html",
            tool=tool,
            config=config,
            json=json,
            parsed_settings=parsed_settings,
        )

    @app.route("/api/tools")
    def api_tools():
        return jsonify(read_all_configs())

    @app.route("/api/tools/<tool>")
    def api_tool(tool: str):
        if tool not in _VALID_TOOLS:
            abort(404)
        return jsonify(_get_tool_config(tool))

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
            session_dir = _safe_copilot_dir(copilot_path, session_id)
            if not session_dir.is_dir():
                abort(404)
            events = copilot_parse_events(session_dir)

        return jsonify(events)

    @app.route("/api/session/<session_id>/backup/<backup_hash>")
    def api_backup(session_id: str, backup_hash: str):
        _validate_session_id(session_id)
        _validate_backup_hash(backup_hash)
        session_dir = _safe_copilot_dir(copilot_path, session_id)
        safe_hash = secure_filename(backup_hash)
        if not safe_hash or safe_hash != backup_hash:
            abort(400)
        backup_file = (
            session_dir / "rewind-snapshots" / "backups" / safe_hash
        )
        resolved = backup_file.resolve()
        try:
            resolved.relative_to(copilot_path.resolve())
        except ValueError:
            abort(403)
        if not resolved.is_file():
            abort(404)
        content = resolved.read_text(errors="replace")
        return content, 200, {"Content-Type": "text/plain; charset=utf-8"}

    return app
