"""Claude Code configuration reader."""

from __future__ import annotations

import sys
from pathlib import Path

from ._common import (
    mask_dict,
    parse_yaml_frontmatter,
    read_skills,
    safe_read_json,
    safe_read_text,
    safe_read_yaml,
)


def _default_managed_dir() -> Path:
    """Return the system-wide Claude Code managed config directory for this OS.

    - macOS:       /Library/Application Support/ClaudeCode/
    - Windows:     %PROGRAMFILES%\\ClaudeCode\\
    - Linux / WSL: /etc/claude-code/
    """
    if sys.platform == "darwin":
        return Path("/Library/Application Support/ClaudeCode")
    if sys.platform == "win32":
        import os as _os

        prog_files = _os.environ.get("PROGRAMFILES", r"C:\Program Files")
        return Path(prog_files) / "ClaudeCode"
    return Path("/etc/claude-code")


def _default_claude_home() -> Path:
    """Return the platform-default Claude home directory.

    On Windows, prefers ``%LOCALAPPDATA%\\claude`` (standard installer).  Falls
    back to ``%USERPROFILE%\\.claude`` only when that directory exists; otherwise
    returns the primary path as the reported default even if it is absent.
    """
    if sys.platform == "win32":
        import os

        localappdata = os.environ.get("LOCALAPPDATA", "")
        primary = Path(localappdata) / "claude" if localappdata else None
        if primary and primary.is_dir():
            return primary
        fallback = Path.home() / ".claude"
        if fallback.is_dir():
            return fallback
        return primary if primary else fallback
    return Path.home() / ".claude"


def _default_global_config_path() -> Path:
    """Return the platform-default global Claude config path.

    On Windows, prefers ``%LOCALAPPDATA%\\claude\\.claude.json``.  Falls back to
    ``%USERPROFILE%\\.claude.json`` only when that file exists; otherwise returns
    the primary path as the reported default even if it is absent.
    """
    if sys.platform == "win32":
        import os

        localappdata = os.environ.get("LOCALAPPDATA", "")
        primary = Path(localappdata) / "claude" / ".claude.json" if localappdata else None
        if primary and primary.is_file():
            return primary
        fallback = Path.home() / ".claude.json"
        if fallback.is_file():
            return fallback
        return primary if primary else fallback
    return Path.home() / ".claude.json"


def _read_plugins(plugins_dir: Path) -> tuple[list[dict], list[dict]]:
    """Read installed and external plugins from the plugins directory."""
    plugins: list[dict] = []
    external_plugins: list[dict] = []

    for market_dir in sorted(plugins_dir.glob("marketplaces/*")):
        # Official plugins
        official_dir = market_dir / "plugins"
        if official_dir.is_dir():
            for p in sorted(official_dir.iterdir()):
                if p.is_dir():
                    plugins.append(_read_single_plugin(p, external=False))

        # External plugins
        ext_dir = market_dir / "external_plugins"
        if ext_dir.is_dir():
            for p in sorted(ext_dir.iterdir()):
                if p.is_dir():
                    external_plugins.append(_read_single_plugin(p, external=True))

    return plugins, external_plugins


def _extract_readme_description(plugin_dir: Path) -> str:
    """Extract the first paragraph from a plugin's README.md."""
    content = safe_read_text(plugin_dir / "README.md", max_bytes=2000)
    if not content:
        return ""
    # Find the first non-empty, non-heading line
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return ""


def _read_single_plugin(plugin_dir: Path, *, external: bool) -> dict:
    """Read a single plugin directory."""
    manifest = safe_read_json(plugin_dir / "manifest.json") or {}
    plugin_yaml = safe_read_yaml(plugin_dir / "plugin.yaml") or {}

    has_hooks = (plugin_dir / "hooks").is_dir()
    has_agents = bool(list(plugin_dir.glob("agents/*")))
    has_commands = bool(list(plugin_dir.glob("commands/*")))

    description = (
        manifest.get("description") or plugin_yaml.get("description") or _extract_readme_description(plugin_dir)
    )

    return {
        "name": manifest.get("name") or plugin_yaml.get("name") or plugin_dir.name,
        "description": description,
        "type": "external" if external else "official",
        "has_hooks": has_hooks,
        "has_agents": has_agents,
        "has_commands": has_commands,
        "path": str(plugin_dir),
    }


def _read_hooks(plugins_dir: Path) -> list[dict]:
    """Read hooks from all plugins."""
    hooks: list[dict] = []
    for hook_dir in sorted(plugins_dir.glob("marketplaces/*/plugins/*/hooks/*")):
        if hook_dir.is_dir():
            manifest = safe_read_json(hook_dir / "manifest.json") or {}
            hooks.append(
                {
                    "name": manifest.get("name") or hook_dir.name,
                    "event": hook_dir.parent.parent.name if hook_dir.parent.name == "hooks" else hook_dir.name,
                    "plugin": hook_dir.parent.parent.name,
                    "command": manifest.get("command", ""),
                }
            )
    # Also check external plugins
    for hook_dir in sorted(plugins_dir.glob("marketplaces/*/external_plugins/*/hooks/*")):
        if hook_dir.is_dir():
            manifest = safe_read_json(hook_dir / "manifest.json") or {}
            hooks.append(
                {
                    "name": manifest.get("name") or hook_dir.name,
                    "event": hook_dir.name,
                    "plugin": hook_dir.parent.parent.name,
                    "command": manifest.get("command", ""),
                }
            )
    return hooks


def _read_agents(plugins_dir: Path) -> list[dict]:
    """Read agents from all plugins."""
    agents: list[dict] = []
    for agent_file in sorted(plugins_dir.glob("marketplaces/*/plugins/*/agents/*")):
        if agent_file.is_file():
            fm = parse_yaml_frontmatter(agent_file) or {}
            agents.append(
                {
                    "name": fm.get("name") or agent_file.stem,
                    "plugin": agent_file.parent.parent.name,
                    "description": fm.get("description", ""),
                    "model": fm.get("model", ""),
                }
            )
    return agents


def _read_commands(plugins_dir: Path) -> list[dict]:
    """Read slash commands from all plugins (official + external)."""
    commands: list[dict] = []
    for pattern in (
        "marketplaces/*/plugins/*/commands/*.md",
        "marketplaces/*/external_plugins/*/commands/*.md",
    ):
        for cmd_file in sorted(plugins_dir.glob(pattern)):
            if not cmd_file.is_file():
                continue
            fm = parse_yaml_frontmatter(cmd_file) or {}
            # Plugin name is 2 levels up from the command file
            plugin_name = cmd_file.parent.parent.name
            commands.append(
                {
                    "name": cmd_file.stem,
                    "plugin": plugin_name,
                    "description": fm.get("description", ""),
                }
            )
    return commands


def read_claude_config(claude_home: Path | None = None) -> dict:
    """Read Claude Code configuration.

    Parameters
    ----------
    claude_home:
        Override for the Claude home directory (useful for testing).
    """
    home = claude_home or _default_claude_home()
    result: dict = {
        "installed": home.is_dir(),
        "home_dir": str(home),
        "main_settings": {},
        "settings": {},
        "mcp_servers": [],
        "policy_limits": {},
        "plugins": [],
        "external_plugins": [],
        "plugin_blocklist": [],
        "agents": [],
        "hooks": [],
        "commands": [],
        "skills": [],
        "memory_files": [],
        "mcp_auth_cache": {},
        "remote_settings": {},
        "stats": {},
        "known_marketplaces": {},
        "install_counts_cache": {},
        "managed_settings": {},
        "managed_settings_legacy": {},
        "managed_mcp_servers": [],
        "managed_mcp_servers_legacy": [],
        "feature_flags": {},
        "growthbook_flags": {},
    }

    # Managed settings and MCP servers (enterprise / MDM — all platforms)
    # Read independently of whether ~/.claude/ exists so enterprise policy is
    # always surfaced even on machines where Claude Code has never been run.
    managed_dir = _default_managed_dir()
    managed_raw = safe_read_json(managed_dir / "managed-settings.json")
    if managed_raw and isinstance(managed_raw, dict):
        result["managed_settings"] = dict(mask_dict(managed_raw))

    mcp_raw = safe_read_json(managed_dir / "managed-mcp.json")
    if mcp_raw and isinstance(mcp_raw, dict):
        servers_dict = mcp_raw.get("mcpServers", {})
        if isinstance(servers_dict, dict):
            result["managed_mcp_servers"] = [
                {
                    "name": name,
                    "type": cfg.get("type", "stdio"),
                    "command": cfg.get("command", ""),
                    "args": cfg.get("args", []),
                    "url": cfg.get("url", ""),
                }
                for name, cfg in mask_dict(servers_dict).items()  # type: ignore[union-attr]
                if isinstance(cfg, dict)
            ]

    # Legacy Windows path (deprecated since v2.1.75)
    if sys.platform == "win32":
        import os as _os

        programdata = _os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        legacy_dir = Path(programdata) / "ClaudeCode"
        legacy_raw = safe_read_json(legacy_dir / "managed-settings.json")
        if legacy_raw and isinstance(legacy_raw, dict):
            result["managed_settings_legacy"] = dict(mask_dict(legacy_raw))

        legacy_mcp_raw = safe_read_json(legacy_dir / "managed-mcp.json")
        if legacy_mcp_raw and isinstance(legacy_mcp_raw, dict):
            servers = legacy_mcp_raw.get("mcpServers", {})
            if isinstance(servers, dict):
                result["managed_mcp_servers_legacy"] = [
                    {
                        "name": name,
                        "type": cfg.get("type", "stdio"),
                        "command": cfg.get("command", ""),
                        "args": cfg.get("args", []),
                        "url": cfg.get("url", ""),
                    }
                    for name, cfg in mask_dict(servers).items()  # type: ignore[union-attr]
                    if isinstance(cfg, dict)
                ]

    if not home.is_dir():
        return result

    # Global config (~/.claude.json)
    global_path = home / ".claude.json"
    if not global_path.is_file():
        global_path = _default_global_config_path()
    global_cfg = safe_read_json(global_path) or {}
    result["main_settings"] = mask_dict(
        {
            k: v
            for k, v in global_cfg.items()
            if k
            in {
                "numStartups",
                "installMethod",
                "autoUpdaterStatus",
                "hasCompletedOnboarding",
                "lastOnboardingVersion",
            }
        }
    )
    # Feature flags: top-level booleans (user-visible settings)
    result["feature_flags"] = {k: v for k, v in global_cfg.items() if isinstance(v, bool)}
    # GrowthBook flags: server-side feature flags cached by Claude Code
    growthbook = global_cfg.get("cachedGrowthBookFeatures", {})
    if isinstance(growthbook, dict):
        result["growthbook_flags"] = {k: v for k, v in growthbook.items() if isinstance(v, bool)}

    # MCP servers (claude_code_config.json)
    mcp_cfg = safe_read_json(home / "claude_code_config.json") or {}
    servers_dict = mcp_cfg.get("mcpServers", {})
    result["mcp_servers"] = [
        {
            "name": name,
            "type": cfg.get("type", "stdio"),
            "command": cfg.get("command", ""),
            "args": cfg.get("args", []),
            "url": cfg.get("url", ""),
        }
        for name, cfg in mask_dict(servers_dict).items()  # type: ignore[union-attr]
        if isinstance(cfg, dict)
    ]

    # Settings (settings.json)
    settings = safe_read_json(home / "settings.json")
    if settings:
        result["settings"] = mask_dict(settings)

    # Policy limits
    policy = safe_read_json(home / "policy-limits.json")
    if policy:
        result["policy_limits"] = policy

    # Plugins
    plugins_dir = home / "plugins"
    if plugins_dir.is_dir():
        result["plugins"], result["external_plugins"] = _read_plugins(plugins_dir)
        result["hooks"] = _read_hooks(plugins_dir)
        result["agents"] = _read_agents(plugins_dir)
        result["commands"] = _read_commands(plugins_dir)

        blocklist = safe_read_json(plugins_dir / "blocklist.json")
        if isinstance(blocklist, list):
            result["plugin_blocklist"] = blocklist
        elif isinstance(blocklist, dict):
            plugins_value = blocklist.get("plugins")
            result["plugin_blocklist"] = plugins_value if isinstance(plugins_value, list) else []

        known_marketplaces = safe_read_json(plugins_dir / "known_marketplaces.json")
        if known_marketplaces and isinstance(known_marketplaces, dict):
            result["known_marketplaces"] = known_marketplaces

        install_counts = safe_read_json(plugins_dir / "install-counts-cache.json")
        if install_counts and isinstance(install_counts, dict):
            result["install_counts_cache"] = install_counts

    # Skills — from ~/.claude/skills/ AND from plugin skills directories
    all_skills = read_skills(home / "skills")
    plugins_dir = home / "plugins"
    if plugins_dir.is_dir():
        for pattern in (
            "marketplaces/*/plugins/*/skills",
            "marketplaces/*/external_plugins/*/skills",
        ):
            for skills_dir in sorted(plugins_dir.glob(pattern)):
                if skills_dir.is_dir():
                    all_skills.extend(read_skills(skills_dir))
    result["skills"] = all_skills

    # MCP auth cache (~/.claude/mcp-needs-auth-cache.json)
    auth_cache = safe_read_json(home / "mcp-needs-auth-cache.json")
    if auth_cache and isinstance(auth_cache, dict):
        result["mcp_auth_cache"] = auth_cache

    # Remote settings (~/.claude/remote-settings.json) — may contain sensitive env vars
    remote = safe_read_json(home / "remote-settings.json")
    if remote and isinstance(remote, dict):
        result["remote_settings"] = dict(mask_dict(remote))

    # Stats cache (~/.claude/stats-cache.json)
    stats = safe_read_json(home / "stats-cache.json")
    if stats and isinstance(stats, dict):
        result["stats"] = stats

    # Global memory — ~/.claude/memory/*.md
    memory_dir = home / "memory"
    memory_files: list[dict] = []
    if memory_dir.is_dir():
        for mf in sorted(memory_dir.iterdir()):
            if mf.is_file() and mf.suffix == ".md":
                content = safe_read_text(mf, max_bytes=100_000)
                if content:
                    memory_files.append({"filename": mf.name, "content": content})
    result["memory_files"] = memory_files

    return result


# ---------------------------------------------------------------------------
# Claude projects
# ---------------------------------------------------------------------------


def _encode_project_path(path: str) -> str:
    """Encode a filesystem path to the directory-name format Claude uses.

    Claude replaces ``/``, ``\\``, spaces, underscores, and dots with hyphens.
    ``/Users/foo/.my_project`` → ``-Users-foo--my-project``
    """
    return path.replace("/", "-").replace("\\", "-").replace(" ", "-").replace("_", "-").replace(".", "-")


def _extract_cwd_from_jsonl(project_dir: Path) -> str:
    """Extract the working directory from the first JSONL session file.

    Falls back to empty string if no session files or no cwd found.
    """
    import json

    for jsonl in project_dir.glob("*.jsonl"):
        try:
            with open(jsonl, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"cwd"' not in line:
                        continue
                    obj = json.loads(line)
                    cwd = obj.get("cwd", "")
                    if cwd:
                        return cwd
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
    return ""


def _read_repo_permissions(real_path: str) -> dict:
    """Read permission rules from repo-local Claude settings.

    Merges ``<cwd>/.claude/settings.json`` (committed) and
    ``<cwd>/.claude/settings.local.json`` (local, user-specific).
    Returns a dict with ``allow``, ``deny``, and ``ask`` lists.
    """
    result: dict[str, list[str]] = {"allow": [], "deny": [], "ask": []}
    repo = Path(real_path)
    for name in ("settings.json", "settings.local.json"):
        cfg = safe_read_json(repo / ".claude" / name) or {}
        perms = cfg.get("permissions", {})
        # ``permissions`` is supposed to be a dict but a corrupted
        # settings file could put a list / scalar there; the inner
        # ``perms.get(...)`` would crash.
        if not isinstance(perms, dict):
            continue
        for key in ("allow", "deny", "ask"):
            items = perms.get(key, [])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, str) and item not in result[key]:
                        result[key].append(item)
    return result


def read_claude_projects(claude_home: Path | None = None) -> dict:
    """Read Claude Code per-project data.

    Parameters
    ----------
    claude_home:
        The ``~/.claude`` directory (NOT the ``projects/`` subdirectory).
    """
    home = claude_home or _default_claude_home()
    projects_dir = home / "projects"
    empty: dict = {"projects": [], "global_stats": _empty_global_stats()}

    if not projects_dir.is_dir():
        return empty

    # Read .claude.json for per-project metadata
    # Try inside claude_home first (works for tests), then fall back to the
    # real default location (~/.claude.json lives at the user home root).
    global_path = home / ".claude.json"
    if not global_path.is_file():
        global_path = _default_global_config_path()
    global_cfg = safe_read_json(global_path) or {}
    project_meta: dict = global_cfg.get("projects", {})

    # Build lookup: encoded dir name → real path
    encoded_to_path: dict[str, str] = {}
    for real_path in project_meta:
        encoded = _encode_project_path(real_path)
        encoded_to_path[encoded] = real_path

    projects: list[dict] = []
    total_cost = 0.0
    total_sessions = 0
    total_memory = 0

    for d in sorted(projects_dir.iterdir()):
        if not d.is_dir():
            continue
        encoded_name = d.name
        real_path = encoded_to_path.get(encoded_name, "")

        # Fallback: extract cwd from the first JSONL session file
        if not real_path:
            real_path = _extract_cwd_from_jsonl(d)

        meta = project_meta.get(real_path, {}) if real_path else {}
        masked = mask_dict(meta) if meta else {}
        meta = masked if isinstance(masked, dict) else {}

        # Count JSONL session files
        jsonl_files = list(d.glob("*.jsonl"))
        session_count = len(jsonl_files)

        # Read memory files
        memory_dir = d / "memory"
        memory_files: list[dict] = []
        if memory_dir.is_dir():
            for mf in sorted(memory_dir.iterdir()):
                if mf.is_file() and mf.suffix == ".md":
                    content = safe_read_text(mf, max_bytes=100_000)
                    if content:
                        memory_files.append({"filename": mf.name, "content": content})

        # ``bool`` is a subclass of ``int`` in Python; reject it so a
        # malformed ``lastCost: true`` doesn't render as $1.00.
        _raw_cost = meta.get("lastCost")
        last_cost = _raw_cost if isinstance(_raw_cost, int | float) and not isinstance(_raw_cost, bool) else None

        # Read repo-local permissions from <cwd>/.claude/settings.local.json
        # and committed settings from <cwd>/.claude/settings.json
        local_permissions = _read_repo_permissions(real_path) if real_path else {}

        project = {
            "encoded_name": encoded_name,
            "path": real_path,
            "name": Path(real_path).name if real_path else encoded_name,
            "session_count": session_count,
            "memory_file_count": len(memory_files),
            "memory_files": memory_files,
            "last_cost": last_cost,
            "last_session_id": meta.get("lastSessionId"),
            "last_input_tokens": meta.get("lastTotalInputTokens"),
            "last_output_tokens": meta.get("lastTotalOutputTokens"),
            "last_cache_creation_tokens": meta.get("lastTotalCacheCreationInputTokens"),
            "last_cache_read_tokens": meta.get("lastTotalCacheReadInputTokens"),
            "last_model_usage": meta.get("lastModelUsage", {}),
            "has_trust_accepted": bool(meta.get("hasTrustDialogAccepted")),
            "onboarding_seen_count": meta.get("projectOnboardingSeenCount", 0),
            "allowed_tools": meta.get("allowedTools", []),
            "mcp_servers": meta.get("mcpServers", {}),
            "example_files": meta.get("exampleFiles", []),
            "permissions": local_permissions,
            "metadata": {**meta, "permissions": local_permissions},
        }
        projects.append(project)
        if last_cost is not None:
            total_cost += last_cost
        total_sessions += session_count
        total_memory += len(memory_files)

    return {
        "projects": projects,
        "global_stats": {
            "total_projects": len(projects),
            "total_sessions": total_sessions,
            "total_memory_files": total_memory,
            "aggregate_cost": round(total_cost, 2),
        },
    }


def _empty_global_stats() -> dict:
    return {
        "total_projects": 0,
        "total_sessions": 0,
        "total_memory_files": 0,
        "aggregate_cost": 0.0,
    }


# ---------------------------------------------------------------------------
# Claude Desktop
# ---------------------------------------------------------------------------


def _default_claude_desktop_dir() -> Path:
    """Return the platform-default Claude Desktop config directory.

    On Windows, checks paths in priority order:
    1. ``%APPDATA%\\Claude``  — standard NSIS/EXE installer
    2. ``%LOCALAPPDATA%\\Packages\\Claude_pzs8sxrjxfjjc\\LocalCache\\Roaming\\Claude``
       — MSIX / Windows Store install (publisher ID is deterministic, derived from
       Anthropic's signing certificate)
    3. Glob over ``%LOCALAPPDATA%\\Packages\\Claude_*\\...`` as a fallback in case
       the publisher ID changes in a future release
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude"
    if sys.platform == "win32":
        import os

        appdata = os.environ.get("APPDATA", "")
        standard = Path(appdata) / "Claude" if appdata else None
        if standard and standard.is_dir():
            return standard

        localappdata = os.environ.get("LOCALAPPDATA", "")
        if localappdata:
            known_msix = (
                Path(localappdata)
                / "Packages"
                / "Claude_pzs8sxrjxfjjc"
                / "LocalCache"
                / "Roaming"
                / "Claude"
            )
            if known_msix.is_dir():
                return known_msix
            packages = Path(localappdata) / "Packages"
            if packages.is_dir():
                for candidate in sorted(packages.glob("Claude_*/LocalCache/Roaming/Claude")):
                    if candidate.is_dir():
                        return candidate

        return standard if standard else Path.home() / "AppData" / "Roaming" / "Claude"
    return Path.home() / ".config" / "Claude"


# Keys to exclude entirely from Claude Desktop config.json (sensitive)
_DESKTOP_EXCLUDED_KEYS = frozenset(
    {
        "oauthAccount",
        "oauth:tokenCache",
        "lastSyncedAccountCacheLifetimeMs",
    }
)


def _read_desktop_skills(desktop_dir: Path) -> list[dict]:
    """Read skills from Claude Desktop's skills-plugin session directory.

    Skills are stored under:
      <desktop_dir>/local-agent-mode-sessions/skills-plugin/<outer-uuid>/<inner-uuid>/
        manifest.json   – index of all skills (name, skillId, creatorType, enabled, …)
        skills/<skill-name>/SKILL.md

    The manifest ``name`` field matches the directory name.  ``skillId`` uses a
    different format for user-created skills and is NOT used as the lookup key.
    """
    sessions_dir = desktop_dir / "local-agent-mode-sessions" / "skills-plugin"
    if not sessions_dir.is_dir():
        return []

    skills_dir: Path | None = None
    manifest_data: dict = {}
    for outer in sorted(sessions_dir.iterdir()):
        if not outer.is_dir():
            continue
        for inner in sorted(outer.iterdir()):
            if not inner.is_dir():
                continue
            candidate = inner / "skills"
            if candidate.is_dir() or (inner / "manifest.json").is_file():
                skills_dir = candidate
                manifest_data = safe_read_json(inner / "manifest.json") or {}
                break
        if skills_dir is not None:
            break

    if not skills_dir or not skills_dir.is_dir():
        return []

    # Build manifest lookup keyed by skill name (= directory name)
    manifest_index: dict[str, dict] = {}
    for entry in manifest_data.get("skills", []):
        skill_name = entry.get("name", "")
        if skill_name:
            manifest_index[skill_name] = {
                "enabled": entry.get("enabled", True),
                "creator_type": entry.get("creatorType", ""),
                "updated_at": entry.get("updatedAt", ""),
                "skill_id": entry.get("skillId", ""),
            }

    skills = read_skills(skills_dir)
    for skill in skills:
        if skill["name"] in manifest_index:
            skill.update(manifest_index[skill["name"]])
        else:
            skill.setdefault("enabled", True)
    return skills


def _read_cowork_plugins(desktop_dir: Path) -> list[dict]:
    """Read Cowork plugin info from Claude Desktop's agent session directory.

    Plugins are stored under a UUID-named session directory (not ``skills-plugin``):
      local-agent-mode-sessions/<uuid>/<uuid>/cowork_plugins/
        installed_plugins.json   – installed plugin list with install paths
        cowork_settings.json     – enabled/disabled state per plugin key
        cache/<marketplace>/<plugin>/<version>/
          .claude-plugin/plugin.json   – name, version, description, author
          skills/<skill-name>/SKILL.md – plugin skills
    """
    sessions_dir = desktop_dir / "local-agent-mode-sessions"
    if not sessions_dir.is_dir():
        return []

    for session_dir in sorted(sessions_dir.iterdir()):
        if not session_dir.is_dir() or session_dir.name == "skills-plugin":
            continue
        for inner in sorted(session_dir.iterdir()):
            if not inner.is_dir():
                continue
            installed_path = inner / "cowork_plugins" / "installed_plugins.json"
            if not installed_path.is_file():
                continue

            installed = safe_read_json(installed_path) or {}
            settings = safe_read_json(inner / "cowork_plugins" / "cowork_settings.json") or {}
            enabled_plugins = settings.get("enabledPlugins", {})
            if not isinstance(enabled_plugins, dict):
                enabled_plugins = {}

            plugins: list[dict] = []
            installed_plugins = installed.get("plugins", {})
            if not isinstance(installed_plugins, dict):
                installed_plugins = {}
            for plugin_key, installs in installed_plugins.items():
                if not isinstance(installs, list):
                    continue
                for inst in installs:
                    if not isinstance(inst, dict):
                        continue
                    # ``installPath`` is meant to be a string but a hand-edited
                    # or partially-written ``installed_plugins.json`` can put
                    # ``None`` / a number there, which crashes ``Path()``.
                    install_path = inst.get("installPath", "")
                    if not isinstance(install_path, str) or not install_path:
                        continue
                    cache_path = Path(install_path)
                    if not cache_path.is_dir():
                        continue
                    plugin_json = safe_read_json(cache_path / ".claude-plugin" / "plugin.json") or {}
                    plugin_skills = read_skills(cache_path / "skills")
                    author_obj = plugin_json.get("author") or {}
                    author_name = author_obj.get("name", "") if isinstance(author_obj, dict) else ""
                    plugins.append(
                        {
                            "key": plugin_key,
                            "name": plugin_json.get("name", plugin_key),
                            "version": inst.get("version", ""),
                            "description": plugin_json.get("description", ""),
                            "author": author_name,
                            "enabled": bool(enabled_plugins.get(plugin_key, False)),
                            "installed_at": inst.get("installedAt", ""),
                            "last_updated": inst.get("lastUpdated", ""),
                            "skills": plugin_skills,
                        }
                    )
            return plugins

    return []


def read_claude_desktop_config(desktop_dir: Path | None = None) -> dict:
    """Read Claude Desktop configuration.

    Parameters
    ----------
    desktop_dir:
        Override for the Claude Desktop config directory (useful for testing).
    """
    home = desktop_dir or _default_claude_desktop_dir()
    result: dict = {
        "installed": home.is_dir(),
        "desktop_dir": str(home),
        "mcp_servers": [],
        "preferences": {},
        "ui_config": {},
        "skills": [],
        "cowork_plugins": [],
    }

    if not home.is_dir():
        return result

    # claude_desktop_config.json — MCP servers + preferences
    desktop_cfg = safe_read_json(home / "claude_desktop_config.json") or {}

    servers_dict = desktop_cfg.get("mcpServers", {})
    if isinstance(servers_dict, dict):
        result["mcp_servers"] = [
            {
                "name": name,
                "type": cfg.get("type", "stdio"),
                "command": cfg.get("command", ""),
                "args": cfg.get("args", []),
                "url": cfg.get("url", ""),
            }
            for name, cfg in mask_dict(servers_dict).items()  # type: ignore[union-attr]
            if isinstance(cfg, dict)
        ]

    prefs = desktop_cfg.get("preferences", {})
    if isinstance(prefs, dict):
        result["preferences"] = prefs

    # config.json — UI settings (exclude sensitive fields)
    ui_cfg = safe_read_json(home / "config.json") or {}
    filtered = {k: v for k, v in ui_cfg.items() if k not in _DESKTOP_EXCLUDED_KEYS}
    result["ui_config"] = mask_dict(filtered)

    # Skills from the skills-plugin session directory
    result["skills"] = _read_desktop_skills(home)

    # Cowork plugins
    result["cowork_plugins"] = _read_cowork_plugins(home)

    return result
