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


def _default_claude_home() -> Path:
    """Return the platform-default Claude home directory."""
    if sys.platform == "win32":
        import os

        localappdata = os.environ.get("LOCALAPPDATA", "")
        if localappdata:
            return Path(localappdata) / "claude"
    return Path.home() / ".claude"


def _default_global_config_path() -> Path:
    """Return the platform-default global Claude config path."""
    if sys.platform == "win32":
        import os

        localappdata = os.environ.get("LOCALAPPDATA", "")
        if localappdata:
            return Path(localappdata) / "claude" / ".claude.json"
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
        "feature_flags": {},
        "growthbook_flags": {},
    }

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
        if blocklist and isinstance(blocklist, list):
            result["plugin_blocklist"] = blocklist

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

        last_cost = meta.get("lastCost") if isinstance(meta.get("lastCost"), int | float) else None

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
            "metadata": meta,
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
    """Return the platform-default Claude Desktop config directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude"
    if sys.platform == "win32":
        import os

        appdata = os.environ.get("APPDATA", "")
        if appdata:
            return Path(appdata) / "Claude"
    return Path.home() / ".config" / "Claude"


# Keys to exclude entirely from Claude Desktop config.json (sensitive)
_DESKTOP_EXCLUDED_KEYS = frozenset(
    {
        "oauthAccount",
        "oauth:tokenCache",
        "lastSyncedAccountCacheLifetimeMs",
    }
)


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

    return result
