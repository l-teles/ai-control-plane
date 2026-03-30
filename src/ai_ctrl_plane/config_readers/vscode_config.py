"""VS Code Chat configuration reader."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from ._common import mask_dict, read_skills, safe_read_json, safe_read_text

# Settings keys relevant to AI/Copilot features
_AI_SETTINGS_RE = re.compile(
    r"(copilot|chat|mcp|ai|github\.copilot|inlineChat|languageModel)",
    re.IGNORECASE,
)


def _default_vscode_user_dir() -> Path:
    """Return the platform-default VS Code (Stable) User directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Code" / "User"
    elif sys.platform == "win32":
        import os

        appdata = os.environ.get("APPDATA", "")
        if appdata:
            return Path(appdata) / "Code" / "User"
        return Path.home() / "Code" / "User"
    else:
        return Path.home() / ".config" / "Code" / "User"


def _default_vscode_insiders_user_dir() -> Path:
    """Return the platform-default VS Code Insiders User directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Code - Insiders" / "User"
    elif sys.platform == "win32":
        import os

        appdata = os.environ.get("APPDATA", "")
        if appdata:
            return Path(appdata) / "Code - Insiders" / "User"
        return Path.home() / "Code - Insiders" / "User"
    else:
        return Path.home() / ".config" / "Code - Insiders" / "User"


def _read_agents(global_storage: Path) -> list[dict]:
    """Read agent definitions from globalStorage."""
    agents: list[dict] = []
    copilot_chat_dir = global_storage / "github.copilot-chat"
    if not copilot_chat_dir.is_dir():
        return agents

    for agent_dir in sorted(copilot_chat_dir.glob("*-agent")):
        if agent_dir.is_dir():
            # Try to read the agent's README or manifest
            desc = ""
            for readme_name in ("README.md", "readme.md", "description.md"):
                content = safe_read_text(agent_dir / readme_name, max_bytes=2000)
                if content:
                    desc = content[:200].strip()
                    break
            if not desc:
                # Try manifest
                manifest = safe_read_json(agent_dir / "manifest.json") or {}
                desc = manifest.get("description", "")

            agents.append(
                {
                    "name": agent_dir.name.replace("-agent", ""),
                    "description": desc,
                    "path": str(agent_dir),
                }
            )
    return agents


def _read_vscode_dir(user_dir: Path) -> dict:
    """Read VS Code config from a single User directory (stable or Insiders)."""
    data: dict = {
        "mcp_servers": [],
        "copilot_settings": {},
        "agents": [],
        "skills": [],
        "language_models": [],
    }

    # MCP servers (mcp.json)
    mcp_raw = safe_read_json(user_dir / "mcp.json")
    mcp_cfg = mcp_raw if isinstance(mcp_raw, dict) else {}
    servers_dict = mcp_cfg.get("servers", mcp_cfg.get("mcpServers", {}))
    if not isinstance(servers_dict, dict):
        servers_dict = {}
    data["mcp_servers"] = [
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

    # Settings (filtered to AI-related keys)
    settings = safe_read_json(user_dir / "settings.json") or {}
    ai_settings = {k: v for k, v in settings.items() if _AI_SETTINGS_RE.search(k)}
    data["copilot_settings"] = mask_dict(ai_settings)

    # Agents from globalStorage
    global_storage = user_dir / "globalStorage"
    data["agents"] = _read_agents(global_storage)

    # Skills from globalStorage/github.copilot-chat/skills/
    copilot_chat_dir = global_storage / "github.copilot-chat"
    if copilot_chat_dir.is_dir():
        data["skills"] = read_skills(copilot_chat_dir / "skills")

    # Language models
    models = safe_read_json(user_dir / "chatLanguageModels.json")
    if models:
        if isinstance(models, list):
            data["language_models"] = models
        elif isinstance(models, dict):
            data["language_models"] = models.get("models", [])

    return data


def read_vscode_config(vscode_user_dir: Path | None = None) -> dict:
    """Read VS Code Chat configuration for both Stable and Insiders editions.

    Parameters
    ----------
    vscode_user_dir:
        Override for the VS Code Stable User directory (useful for testing).
        When provided, Insiders scanning is skipped.
    """
    user_dir = vscode_user_dir or _default_vscode_user_dir()
    result: dict = {
        "installed": user_dir.is_dir(),
        "user_dir": str(user_dir),
        "mcp_servers": [],
        "copilot_settings": {},
        "agents": [],
        "skills": [],
        "language_models": [],
        "insiders_installed": False,
        "insiders_user_dir": "",
        "insiders_mcp_servers": [],
        "insiders_copilot_settings": {},
        "insiders_skills": [],
        "insiders_language_models": [],
    }

    if user_dir.is_dir():
        stable = _read_vscode_dir(user_dir)
        result.update(stable)

    # VS Code Insiders (only when not overridden — override implies test/custom path)
    if vscode_user_dir is None:
        insiders_dir = _default_vscode_insiders_user_dir()
        result["insiders_user_dir"] = str(insiders_dir)
        result["insiders_installed"] = insiders_dir.is_dir()
        if insiders_dir.is_dir():
            insiders = _read_vscode_dir(insiders_dir)
            result["insiders_mcp_servers"] = insiders["mcp_servers"]
            result["insiders_copilot_settings"] = insiders["copilot_settings"]
            result["insiders_skills"] = insiders["skills"]
            result["insiders_language_models"] = insiders["language_models"]

    return result
