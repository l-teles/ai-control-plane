"""Tests for AI tool configuration readers."""

from __future__ import annotations

import json

from ai_control_plane.config_readers._common import mask_dict, mask_secret, mask_value, sanitize_url
from ai_control_plane.config_readers.claude_config import read_claude_config
from ai_control_plane.config_readers.copilot_config import read_copilot_config
from ai_control_plane.config_readers.vscode_config import read_vscode_config

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


def test_mask_dict_masks_secrets_in_lists():
    """Strings in lists that look like secrets should be masked."""
    data = {
        "args": ["-y", "safe-arg", "ghp_abcdefghijklmnopqrstuvwxyz1234567890"],
    }
    result = mask_dict(data)
    assert result["args"][0] == "-y"
    assert result["args"][1] == "safe-arg"
    assert result["args"][2].endswith("****")
    assert "ghp_abcdefghijklmnop" not in result["args"][2]


# ---------------------------------------------------------------------------
# sanitize_url tests
# ---------------------------------------------------------------------------


def test_sanitize_url_safe_schemes():
    assert sanitize_url("https://example.com") == "https://example.com"
    assert sanitize_url("http://example.com") == "http://example.com"
    assert sanitize_url("mailto:user@example.com") == "mailto:user@example.com"


def test_sanitize_url_unsafe_schemes():
    assert sanitize_url("javascript:alert(1)") == ""
    assert sanitize_url("data:text/html,<h1>hi</h1>") == ""
    assert sanitize_url("vbscript:foo") == ""


def test_sanitize_url_empty():
    assert sanitize_url("") == ""
    assert sanitize_url(None) == ""


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
    (claude_home / "settings.json").write_text(json.dumps({"env": {"FOO": "bar"}, "telemetry": False}))

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

    (copilot_home / "command-history-state.json").write_text(json.dumps({"commands": ["help", "explain", "fix"]}))

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


# ---------------------------------------------------------------------------
# Skills reading tests
# ---------------------------------------------------------------------------


def test_read_skills_basic(tmp_path):
    """read_skills should parse SKILL.md with YAML frontmatter."""
    from ai_control_plane.config_readers._common import read_skills

    skills_dir = tmp_path / "skills"
    skill = skills_dir / "test-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: A test skill\n"
        "license: MIT\nmetadata:\n  author: TestCo\n  version: '2.0'\n"
        "  homepage: https://example.com\ntools: Read, Grep\n---\n"
        "# Hello\n\nBody content.\n"
    )

    result = read_skills(skills_dir)
    assert len(result) == 1
    s = result[0]
    assert s["name"] == "test-skill"
    assert s["description"] == "A test skill"
    assert s["author"] == "TestCo"
    assert s["version"] == "2.0"
    assert s["license"] == "MIT"
    assert s["homepage"] == "https://example.com"
    assert s["tools"] == "Read, Grep"
    assert "Hello" in s["body"]
    assert "Body content" in s["body"]


def test_read_skills_empty_dir(tmp_path):
    """read_skills returns empty list for nonexistent dir."""
    from ai_control_plane.config_readers._common import read_skills

    result = read_skills(tmp_path / "nonexistent")
    assert result == []


def test_read_skills_no_metadata(tmp_path):
    """read_skills works with minimal frontmatter (no metadata block)."""
    from ai_control_plane.config_readers._common import read_skills

    skills_dir = tmp_path / "skills"
    skill = skills_dir / "simple"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: simple\ndescription: Simple skill\n---\nJust a body.\n")

    result = read_skills(skills_dir)
    assert len(result) == 1
    assert result[0]["name"] == "simple"
    assert result[0]["author"] == ""
    assert result[0]["version"] == ""
    assert result[0]["homepage"] == ""
    assert "Just a body" in result[0]["body"]


def test_claude_config_reads_skills(tmp_path):
    """Claude config should read skills from ~/.claude/skills/."""
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    skill_dir = claude_home / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: my-skill\ndescription: Test\n---\nBody\n")

    result = read_claude_config(claude_home)
    assert len(result["skills"]) == 1
    assert result["skills"][0]["name"] == "my-skill"


def test_claude_config_reads_plugin_skills(tmp_path):
    """Claude config should also read skills from plugin directories."""
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    plugin_skill = (
        claude_home / "plugins" / "marketplaces" / "official" / "plugins" / "my-plugin" / "skills" / "plugin-skill"
    )
    plugin_skill.mkdir(parents=True)
    (plugin_skill / "SKILL.md").write_text("---\nname: plugin-skill\ndescription: From plugin\n---\nPlugin body\n")

    result = read_claude_config(claude_home)
    assert any(s["name"] == "plugin-skill" for s in result["skills"])


def test_copilot_config_reads_skills(tmp_path):
    """Copilot config should read skills from ~/.copilot/skills/."""
    copilot_home = tmp_path / ".copilot"
    copilot_home.mkdir()
    skill_dir = copilot_home / "skills" / "cop-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: cop-skill\ndescription: Copilot skill\n---\nBody\n")

    result = read_copilot_config(copilot_home)
    assert len(result["skills"]) == 1
    assert result["skills"][0]["name"] == "cop-skill"


def test_vscode_config_reads_skills(tmp_path):
    """VS Code config should read skills from globalStorage."""
    user_dir = tmp_path / "Code" / "User"
    user_dir.mkdir(parents=True)
    skill_dir = user_dir / "globalStorage" / "github.copilot-chat" / "skills" / "vs-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: vs-skill\ndescription: VS Code skill\n---\nBody\n")

    result = read_vscode_config(user_dir)
    assert len(result["skills"]) == 1
    assert result["skills"][0]["name"] == "vs-skill"
