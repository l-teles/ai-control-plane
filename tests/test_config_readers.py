"""Tests for AI tool configuration readers."""

from __future__ import annotations

import json

from ai_log_viewer.config_readers._common import mask_dict, mask_secret, mask_value
from ai_log_viewer.config_readers.claude_config import read_claude_config
from ai_log_viewer.config_readers.copilot_config import read_copilot_config
from ai_log_viewer.config_readers.vscode_config import read_vscode_config

# ---------------------------------------------------------------------------
# mask_secret / mask_value tests
# ---------------------------------------------------------------------------


def test_mask_value_short():
    assert mask_value("abc") == "****"


def test_mask_value_long():
    assert mask_value("sk-1234abcd") == "sk-1****"


def test_mask_secret_by_key():
    assert mask_secret("apiKey", "my-secret-key-12345") == "my-s****"
    assert mask_secret("token", "ghp_abcdef1234567890") == "ghp_****"
    assert mask_secret("password", "hunter2") == "hunt****"
    assert mask_secret("connectionString", "Server=foo") == "Serv****"


def test_mask_secret_by_value_pattern():
    # Bearer token
    assert mask_secret("header", "Bearer eyJhbGciOiJIUzI1NiJ9.test") == "Bear****"
    # GitHub token
    assert mask_secret("x", "ghp_abcdefghijklmnopqrstuvwxyz1234567890") == "ghp_****"
    # OpenAI key
    assert mask_secret("x", "sk-abcdefghijklmnopqrstuvwxyz1234567890") == "sk-a****"


def test_mask_secret_normal_value_untouched():
    assert mask_secret("name", "my-server") == "my-server"
    assert mask_secret("command", "npx") == "npx"
    assert mask_secret("debug", "true") == "true"


def test_mask_secret_url_credentials():
    url = "postgres://admin:s3cret@localhost:5432/db"
    result = mask_secret("url", url)
    assert "s3cret" not in result
    assert "****" in result
    assert "localhost" in result


def test_mask_secret_non_string():
    assert mask_secret("count", 42) == 42
    assert mask_secret("enabled", True) is True
    assert mask_secret("items", [1, 2]) == [1, 2]


# ---------------------------------------------------------------------------
# mask_dict tests
# ---------------------------------------------------------------------------


def test_mask_dict_recursive():
    data = {
        "name": "test",
        "token": "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
        "nested": {
            "apiKey": "sk-abcdefghijklmnopqrstuvwxyz1234567890",
            "safe": "hello",
        },
        "items": [
            {"password": "hunter2hunter2", "label": "ok"},
        ],
    }
    result = mask_dict(data)
    assert result["name"] == "test"
    assert result["token"].endswith("****")
    assert result["nested"]["apiKey"].endswith("****")
    assert result["nested"]["safe"] == "hello"
    assert result["items"][0]["password"].endswith("****")
    assert result["items"][0]["label"] == "ok"


# ---------------------------------------------------------------------------
# Claude config reader
# ---------------------------------------------------------------------------


def test_claude_config_reads_all(tmp_path):
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()

    # Global config
    (claude_home / ".claude.json").write_text(
        json.dumps(
            {
                "numStartups": 42,
                "installMethod": "pip",
                "hasCompletedOnboarding": True,
                "someFlag": True,
                "anotherFlag": False,
            }
        )
    )

    # MCP servers
    (claude_home / "claude_code_config.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "my-server": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "my-server"],
                    }
                }
            }
        )
    )

    # Settings
    (claude_home / "settings.json").write_text(
        json.dumps({"env": {"FOO": "bar"}, "telemetry": False})
    )

    result = read_claude_config(claude_home)
    assert result["installed"] is True
    assert result["main_settings"]["numStartups"] == 42
    assert len(result["mcp_servers"]) == 1
    assert result["mcp_servers"][0]["name"] == "my-server"
    assert result["settings"]["telemetry"] is False
    assert result["feature_flags"]["someFlag"] is True
    assert result["feature_flags"]["anotherFlag"] is False


def test_claude_config_missing_dir(tmp_path):
    result = read_claude_config(tmp_path / "nonexistent")
    assert result["installed"] is False
    assert result["mcp_servers"] == []


# ---------------------------------------------------------------------------
# Copilot config reader
# ---------------------------------------------------------------------------


def test_copilot_config_reads_all(tmp_path):
    copilot_home = tmp_path / ".copilot"
    copilot_home.mkdir()

    (copilot_home / "config.json").write_text(
        json.dumps({"user": "test", "token": "ghp_abcdefghijklmnop1234567890123456"})
    )

    (copilot_home / "mcp-config.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "server-a": {"command": "node", "args": ["index.js"]},
                }
            }
        )
    )

    (copilot_home / "command-history-state.json").write_text(
        json.dumps({"commands": ["help", "explain", "fix"]})
    )

    session_dir = copilot_home / "session-state"
    session_dir.mkdir()
    (session_dir / "sess-1").mkdir()
    (session_dir / "sess-2").mkdir()

    result = read_copilot_config(copilot_home)
    assert result["installed"] is True
    assert result["config"]["token"].endswith("****")
    assert len(result["mcp_servers"]) == 1
    assert result["recent_commands"] == ["help", "explain", "fix"]
    assert result["session_count"] == 2


def test_copilot_config_missing_dir(tmp_path):
    result = read_copilot_config(tmp_path / "nonexistent")
    assert result["installed"] is False
    assert result["session_count"] == 0


# ---------------------------------------------------------------------------
# VS Code config reader
# ---------------------------------------------------------------------------


def test_vscode_config_reads_all(tmp_path):
    user_dir = tmp_path / "Code" / "User"
    user_dir.mkdir(parents=True)

    (user_dir / "mcp.json").write_text(
        json.dumps(
            {
                "servers": {
                    "fs-server": {"type": "stdio", "command": "fs-mcp"},
                }
            }
        )
    )

    (user_dir / "settings.json").write_text(
        json.dumps(
            {
                "editor.fontSize": 14,
                "github.copilot.enable": True,
                "chat.editor.fontSize": 13,
                "unrelated.setting": "ignored",
            }
        )
    )

    result = read_vscode_config(user_dir)
    assert result["installed"] is True
    assert len(result["mcp_servers"]) == 1
    assert result["mcp_servers"][0]["name"] == "fs-server"
    # Only AI-related settings should be included
    assert "github.copilot.enable" in result["copilot_settings"]
    assert "chat.editor.fontSize" in result["copilot_settings"]
    assert "editor.fontSize" not in result["copilot_settings"]
    assert "unrelated.setting" not in result["copilot_settings"]


def test_vscode_config_missing_dir(tmp_path):
    result = read_vscode_config(tmp_path / "nonexistent")
    assert result["installed"] is False
    assert result["mcp_servers"] == []
