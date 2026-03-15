"""GitHub Copilot CLI configuration reader."""

from __future__ import annotations

import sys
from pathlib import Path

from ._common import mask_dict, read_skills, safe_read_json


def _default_copilot_home() -> Path:
    """Return the platform-default Copilot home directory."""
    if sys.platform == "win32":
        import os

        localappdata = os.environ.get("LOCALAPPDATA", "")
        if localappdata:
            return Path(localappdata) / "github-copilot"
    return Path.home() / ".copilot"


def read_copilot_config(copilot_home: Path | None = None) -> dict:
    """Read GitHub Copilot CLI configuration.

    Parameters
    ----------
    copilot_home:
        Override for the Copilot home directory (useful for testing).
    """
    home = copilot_home or _default_copilot_home()
    result: dict = {
        "installed": home.is_dir(),
        "home_dir": str(home),
        "config": {},
        "mcp_servers": [],
        "recent_commands": [],
        "skills": [],
        "session_count": 0,
    }

    if not home.is_dir():
        return result

    # Main config
    config = safe_read_json(home / "config.json")
    if config:
        result["config"] = mask_dict(config)

    # MCP servers
    mcp_cfg = safe_read_json(home / "mcp-config.json") or {}
    servers_dict = mcp_cfg.get("mcpServers", mcp_cfg.get("servers", {}))
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

    # Recent commands
    cmd_history = safe_read_json(home / "command-history-state.json") or {}
    commands = cmd_history.get("commands", cmd_history.get("history", []))
    if isinstance(commands, list):
        result["recent_commands"] = commands[-20:]

    # Session count
    session_dir = home / "session-state"
    if session_dir.is_dir():
        result["session_count"] = sum(1 for d in session_dir.iterdir() if d.is_dir())

    # Skills (~/.copilot/skills/)
    result["skills"] = read_skills(home / "skills")

    return result
