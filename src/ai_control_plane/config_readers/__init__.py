"""AI tool configuration readers."""

from __future__ import annotations

from .claude_config import read_claude_config
from .copilot_config import read_copilot_config
from .vscode_config import read_vscode_config


def read_all_configs() -> dict:
    """Read configuration from all supported AI tools."""
    return {
        "claude": read_claude_config(),
        "copilot": read_copilot_config(),
        "vscode": read_vscode_config(),
    }
