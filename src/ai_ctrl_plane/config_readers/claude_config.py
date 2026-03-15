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
    global_path = (claude_home / ".claude.json") if claude_home else _default_global_config_path()
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
