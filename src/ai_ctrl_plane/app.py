"""Flask application for the AI Control Plane."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import markdown
import nh3
from flask import Flask, Response, abort, jsonify, redirect, render_template, request, url_for
from markupsafe import Markup
from werkzeug.utils import secure_filename

from . import claude_parser, vscode_parser
from .config_readers.claude_config import _default_claude_desktop_dir, read_claude_config, read_claude_desktop_config
from .config_readers.copilot_config import read_copilot_config
from .config_readers.vscode_config import read_vscode_config
from .db import CacheDB, default_cache_dir, start_background_build
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

# Project encoded names: only safe characters (no slashes, no ..)
# Allow colon for Windows drive letters (e.g. C:), reject ".." for path traversal
_PROJECT_NAME_RE = re.compile(r"^[-a-zA-Z0-9_.: ]+$")


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
    cache_dir: str | Path | None = None,
    desktop_dir: str | Path | None = None,
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
    cache_dir:
        Directory for the SQLite cache database.
        Falls back to platform-specific cache dir.
    desktop_dir:
        Claude Desktop config directory.
        Falls back to ``CLAUDE_DESKTOP_DIR`` env var, then platform default.
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

    if cache_dir is None:
        cache_dir = default_cache_dir()
    cache_path = Path(cache_dir)

    if desktop_dir is None:
        desktop_dir = os.environ.get("CLAUDE_DESKTOP_DIR", str(_default_claude_desktop_dir()))
    desktop_path = Path(desktop_dir).resolve()

    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
    )

    # -- Security configuration ----------------------------------------------
    app.config["DEBUG"] = False
    app.config["TESTING"] = False
    app.config["SECRET_KEY"] = os.urandom(32)
    app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB

    # -- SQLite cache --------------------------------------------------------
    db = CacheDB(cache_path / "cache.db")
    app.config["cache_db"] = db

    # Close DB at process exit (connection is reused across requests)
    import atexit

    atexit.register(db.close)

    # Start background cache build if not already ready
    if db.status != "ready":
        start_background_build(db, copilot_path, claude_path, vscode_path, desktop_path)

    # -- Unified session index (cached) ---------------------------------------
    # Reads from DB when available, falls back to filesystem scan
    _cache: dict = {"sessions": None, "index": None, "ts": 0.0}
    _CACHE_TTL = 30  # seconds

    def _build_session_index(*, force: bool = False) -> tuple[list[dict], dict[str, dict]]:
        # Try DB first (also use partial data while building)
        if db.status in ("ready", "building") and not force:
            sessions = db.get_sessions()
            if sessions:
                idx = {f"{s['source']}:{s['id']}": s for s in sessions}
                return sessions, idx

        now = time.monotonic()
        if not force and _cache["sessions"] is not None and (now - _cache["ts"]) < _CACHE_TTL:
            return _cache["sessions"], _cache["index"]

        copilot_sessions = copilot_discover(copilot_path)
        for s in copilot_sessions:
            s.setdefault("source", "copilot")

        claude_sessions = claude_parser.discover_sessions(claude_path)
        vscode_sessions = vscode_parser.discover_sessions(vscode_path)

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
        configs = _get_all_configs()
        c = configs["claude"]
        cp = configs["copilot"]
        v = configs["vscode"]
        project_stats = db.get_project_global_stats()
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
            total_projects=project_stats["total_projects"],
            total_memory_files=project_stats["total_memory_files"],
            aggregate_cost=project_stats["aggregate_cost"],
            cache_status=db.status,
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

    _VALID_TOOLS = {"claude", "copilot", "vscode", "claude_desktop"}

    def _get_all_configs() -> dict[str, dict]:
        """Read all tool configs, using cached data where available.

        During a background build the DB may hold only a subset of tools.
        We use whatever is cached and do a direct filesystem read only for
        any tool that is still missing, avoiding a full FS scan on every request
        while the cache is being populated.
        """
        result: dict[str, dict] = {}
        if db.status in ("ready", "building"):
            cached = db.get_all_tool_configs()
            if cached:
                result = dict(cached)
        if "claude" not in result:
            result["claude"] = read_claude_config()
        if "copilot" not in result:
            result["copilot"] = read_copilot_config()
        if "vscode" not in result:
            result["vscode"] = read_vscode_config()
        if "claude_desktop" not in result:
            result["claude_desktop"] = read_claude_desktop_config(desktop_dir=desktop_path)
        return result

    def _get_tool_config(tool: str) -> dict:
        if tool not in _VALID_TOOLS:
            abort(404)
        if db.status in ("ready", "building"):
            cached = db.get_tool_config(tool)
            if cached:
                return cached
        if tool == "claude":
            return read_claude_config()
        elif tool == "copilot":
            return read_copilot_config()
        elif tool == "vscode":
            return read_vscode_config()
        elif tool == "claude_desktop":
            return read_claude_desktop_config(desktop_dir=desktop_path)
        abort(404)

    @app.route("/agents")
    def agents_view():
        configs = _get_all_configs()
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
        configs = _get_all_configs()
        by_name: dict[str, dict] = {}
        for source in ("claude", "copilot", "vscode", "claude_desktop"):
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
        claude_desktop_skill_count = sum(1 for s in skills if "claude_desktop" in s["sources"])
        return render_template(
            "skills.html",
            skills=skills,
            claude_skill_count=claude_skill_count,
            copilot_skill_count=copilot_skill_count,
            vscode_skill_count=vscode_skill_count,
            claude_desktop_skill_count=claude_desktop_skill_count,
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
        configs = _get_all_configs()
        # Compute shared MCP servers (present in 2+ tools)
        server_sources: dict[str, list[str]] = {}
        for source in ("claude", "copilot", "vscode", "claude_desktop"):
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

    _DESKTOP_PREFS_META: dict[str, dict] = {
        "coworkScheduledTasksEnabled": {
            "desc": "Enable background scheduled tasks for Cowork",
            "type": "bool",
        },
        "ccdScheduledTasksEnabled": {
            "desc": "Enable background tasks for Claude Code Desktop",
            "type": "bool",
        },
        "sidebarMode": {"desc": "Default sidebar layout mode", "type": "string"},
        "coworkWebSearchEnabled": {"desc": "Enable web search in Cowork", "type": "bool"},
        "autoUpdates": {"desc": "Automatically install application updates", "type": "bool"},
        "telemetry": {"desc": "Send anonymous usage telemetry to Anthropic", "type": "bool"},
        "launchAtLogin": {"desc": "Start Claude Desktop when you log in", "type": "bool"},
        "notificationsEnabled": {"desc": "Show desktop notifications", "type": "bool"},
    }

    def _parse_desktop_preferences(prefs: dict) -> list[dict]:
        """Parse Claude Desktop preferences into annotated list with descriptions."""
        result = []
        for key, value in sorted(prefs.items()):
            meta = _DESKTOP_PREFS_META.get(key, {})
            result.append(
                {
                    "key": key,
                    "value": value,
                    "desc": meta.get("desc", ""),
                    "type": meta.get("type", "unknown"),
                }
            )
        return result

    # Filesystem paths read by the app, per tool and platform — shown in "Data Sources" section
    _TOOL_PATHS: dict[str, dict[str, list[str]]] = {
        "claude": {
            "macOS / Linux": [
                "~/.claude/ (home directory)",
                "~/.claude.json (global config)",
                "~/.claude/claude_code_config.json (MCP servers)",
                "~/.claude/settings.json",
                "~/.claude/policy-limits.json",
                "~/.claude/plugins/ (plugins, agents, hooks, commands, skills)",
                "~/.claude/skills/ (user skills)",
                "~/.claude/projects/ (session logs)",
            ],
            "Windows": [
                "%LOCALAPPDATA%\\claude\\ (home directory)",
                "%LOCALAPPDATA%\\claude\\.claude.json",
                "%LOCALAPPDATA%\\claude\\claude_code_config.json",
                "%LOCALAPPDATA%\\claude\\settings.json",
                "%LOCALAPPDATA%\\claude\\policy-limits.json",
                "%LOCALAPPDATA%\\claude\\plugins\\",
                "%LOCALAPPDATA%\\claude\\skills\\",
                "%LOCALAPPDATA%\\claude\\projects\\",
            ],
        },
        "claude_desktop": {
            "macOS": [
                "~/Library/Application Support/Claude/claude_desktop_config.json (MCP servers, preferences)",
                "~/Library/Application Support/Claude/config.json (UI settings)",
                "~/Library/Application Support/Claude/local-agent-mode-sessions/"
                "skills-plugin/<uuid>/<uuid>/manifest.json (skill index)",
                "~/Library/Application Support/Claude/local-agent-mode-sessions/"
                "skills-plugin/<uuid>/<uuid>/skills/ (user skills)",
                "~/Library/Application Support/Claude/local-agent-mode-sessions/"
                "<uuid>/<uuid>/cowork_plugins/ (Cowork plugins)",
            ],
            "Windows": [
                "%APPDATA%\\Claude\\claude_desktop_config.json",
                "%APPDATA%\\Claude\\config.json",
                "%APPDATA%\\Claude\\local-agent-mode-sessions\\skills-plugin\\<uuid>\\<uuid>\\skills\\",
                "%APPDATA%\\Claude\\local-agent-mode-sessions\\<uuid>\\<uuid>\\cowork_plugins\\",
            ],
            "Linux": [
                "~/.config/Claude/claude_desktop_config.json",
                "~/.config/Claude/config.json",
                "~/.config/Claude/local-agent-mode-sessions/skills-plugin/<uuid>/<uuid>/skills/",
                "~/.config/Claude/local-agent-mode-sessions/<uuid>/<uuid>/cowork_plugins/",
            ],
        },
        "copilot": {
            "macOS / Linux": [
                "~/.copilot/config.json",
                "~/.copilot/mcp-config.json (MCP servers)",
                "~/.copilot/command-history-state.json",
                "~/.copilot/session-state/ (session logs)",
                "~/.copilot/skills/ (skills)",
            ],
            "Windows": [
                "%LOCALAPPDATA%\\github-copilot\\config.json",
                "%LOCALAPPDATA%\\github-copilot\\mcp-config.json",
                "%LOCALAPPDATA%\\github-copilot\\session-state\\",
                "%LOCALAPPDATA%\\github-copilot\\skills\\",
            ],
        },
        "vscode": {
            "macOS": [
                "~/Library/Application Support/Code/User/mcp.json (MCP servers)",
                "~/Library/Application Support/Code/User/settings.json",
                "~/Library/Application Support/Code/User/chatLanguageModels.json",
                "~/Library/Application Support/Code/User/workspaceStorage/<hash>/chatSessions/ (sessions)",
                "~/Library/Application Support/Code/User/globalStorage/github.copilot-chat/skills/",
            ],
            "Windows": [
                "%APPDATA%\\Code\\User\\mcp.json",
                "%APPDATA%\\Code\\User\\settings.json",
                "%APPDATA%\\Code\\User\\workspaceStorage\\<hash>\\chatSessions\\",
                "%APPDATA%\\Code\\User\\globalStorage\\github.copilot-chat\\skills\\",
            ],
            "Linux": ["~/.config/Code/User/ (same structure as macOS)"],
        },
    }

    @app.route("/tools/<tool>")
    def tool_detail(tool: str):
        if tool not in _VALID_TOOLS:
            abort(404)
        config = _get_tool_config(tool)
        parsed_settings = []
        if tool == "claude" and config.get("settings"):
            parsed_settings = _parse_claude_settings(config["settings"])
        parsed_preferences = []
        if tool == "claude_desktop" and config.get("preferences"):
            parsed_preferences = _parse_desktop_preferences(config["preferences"])
        return render_template(
            "tool_detail.html",
            tool=tool,
            config=config,
            json=json,
            parsed_settings=parsed_settings,
            parsed_preferences=parsed_preferences,
            md_to_html=md_to_html,
        )

    @app.route("/api/tools")
    def api_tools():
        return jsonify(_get_all_configs())

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
        backup_file = session_dir / "rewind-snapshots" / "backups" / safe_hash
        resolved = backup_file.resolve()
        try:
            resolved.relative_to(copilot_path.resolve())
        except ValueError:
            abort(403)
        if not resolved.is_file():
            abort(404)
        content = resolved.read_text(errors="replace")
        return content, 200, {"Content-Type": "text/plain; charset=utf-8"}

    # -- Cache status API ----------------------------------------------------

    @app.route("/api/cache-status")
    def api_cache_status():
        return jsonify(db.cache_status())

    # -- Projects routes -----------------------------------------------------

    @app.route("/projects")
    def projects_view():
        projects = db.get_projects()
        stats = db.get_project_global_stats()
        return render_template(
            "projects.html",
            projects=projects,
            stats=stats,
            cache_status=db.status,
        )

    @app.route("/projects/<encoded_name>")
    def project_detail_view(encoded_name: str):
        if not _PROJECT_NAME_RE.match(encoded_name) or ".." in encoded_name:
            abort(400, description="Invalid project name")
        project = db.get_project(encoded_name)
        if not project:
            abort(404)
        memory_files = db.get_project_memory(encoded_name)
        # Render memory markdown to HTML
        for mf in memory_files:
            mf["html"] = md_to_html(mf.get("content", ""))
        # Get sessions linked to this project by cwd
        project_sessions = []
        project_cost = {"input_tokens": 0, "output_tokens": 0, "estimated_cost": 0}
        if project.get("path"):
            project_sessions = db.get_project_sessions(project["path"])
            project_cost = db.get_project_cost(project["path"])
        desktop_config = db.get_tool_config("claude_desktop")
        return render_template(
            "project_detail.html",
            project=project,
            memory_files=memory_files,
            project_sessions=project_sessions,
            project_cost=project_cost,
            desktop_config=desktop_config,
            ts_display=ts_display,
            json=json,
        )

    @app.route("/api/projects")
    def api_projects():
        return jsonify(
            {
                "projects": db.get_projects(),
                "stats": db.get_project_global_stats(),
            }
        )

    @app.route("/api/projects/<encoded_name>")
    def api_project(encoded_name: str):
        if not _PROJECT_NAME_RE.match(encoded_name) or ".." in encoded_name:
            abort(400, description="Invalid project name")
        project = db.get_project(encoded_name)
        if not project:
            abort(404)
        project["memory_files"] = db.get_project_memory(encoded_name)
        if project.get("path"):
            project["sessions"] = db.get_project_sessions(project["path"])
        return jsonify(project)

    # -- Settings routes -----------------------------------------------------

    @app.route("/settings")
    def settings_view():
        from .config_readers.claude_config import (
            _default_claude_desktop_dir,
            _default_claude_home,
            _default_global_config_path,
        )
        from .config_readers.copilot_config import _default_copilot_home
        from .config_readers.vscode_config import _default_vscode_user_dir

        db_size = None
        db_file = cache_path / "cache.db"
        if db_file.exists():
            db_size = db_file.stat().st_size

        data_dirs = [
            ("Claude Code projects & sessions", str(claude_path)),
            ("Claude Code config", str(_default_claude_home())),
            ("Claude global config", str(_default_global_config_path())),
            ("Claude Desktop", str(_default_claude_desktop_dir())),
            ("GitHub Copilot sessions", str(copilot_path)),
            ("GitHub Copilot config", str(_default_copilot_home())),
            ("VS Code Chat sessions", str(vscode_path)),
            ("VS Code config", str(_default_vscode_user_dir())),
            ("Cache directory", str(cache_path)),
        ]
        return render_template(
            "settings.html",
            cache_status=db.cache_status(),
            db_size=db_size,
            data_dirs=data_dirs,
            tool_paths=_TOOL_PATHS,
        )

    @app.route("/settings/rebuild-cache", methods=["POST"])
    def rebuild_cache():
        if db.status != "building":
            start_background_build(db, copilot_path, claude_path, vscode_path, desktop_path)
        return redirect(url_for("settings_view"))

    return app
