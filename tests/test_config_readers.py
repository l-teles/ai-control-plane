"""Tests for AI tool configuration readers."""

from __future__ import annotations

import json

from ai_ctrl_plane.config_readers._common import mask_dict, mask_secret, mask_value, sanitize_url
from ai_ctrl_plane.config_readers.claude_config import read_claude_config
from ai_ctrl_plane.config_readers.copilot_config import read_copilot_config
from ai_ctrl_plane.config_readers.vscode_config import read_vscode_config

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
    from ai_ctrl_plane.config_readers._common import read_skills

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
    from ai_ctrl_plane.config_readers._common import read_skills

    result = read_skills(tmp_path / "nonexistent")
    assert result == []


def test_read_skills_tolerates_non_string_description(tmp_path):
    """YAML allows scalars / nulls / lists where we expect a string. The
    skill loader must not crash on ``.strip()`` if ``description`` decodes
    to something other than a string. Proactive sweep for PR #27."""
    from ai_ctrl_plane.config_readers._common import read_skills

    skills_dir = tmp_path / "skills"
    for i, raw in enumerate(("description: 42", "description:", "description: [a, b]", "name: 99\ndescription: ok")):
        skill = skills_dir / f"weird-{i}"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"---\n{raw}\n---\nBody.\n")

    result = read_skills(skills_dir)
    assert len(result) == 4
    # Non-string description values become "" rather than raising.
    descs = sorted(s["description"] for s in result)
    assert descs == ["", "", "", "ok"]
    # Non-string ``name`` falls back to the directory name.
    names = {s["name"] for s in result}
    assert "weird-3" in names  # numeric ``name: 99`` was rejected → dir name


def test_read_skills_no_metadata(tmp_path):
    """read_skills works with minimal frontmatter (no metadata block)."""
    from ai_ctrl_plane.config_readers._common import read_skills

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


# ---------------------------------------------------------------------------
# Claude projects reader
# ---------------------------------------------------------------------------


def test_read_claude_projects_basic(tmp_path):
    """read_claude_projects should discover projects and memory files."""
    from ai_ctrl_plane.config_readers.claude_config import read_claude_projects

    claude_home = tmp_path / ".claude"
    claude_home.mkdir()

    # Create project dirs
    proj = claude_home / "projects" / "-Users-test-myproject"
    proj.mkdir(parents=True)
    (proj / "abc.jsonl").write_text('{"type":"user"}\n')
    (proj / "def.jsonl").write_text('{"type":"user"}\n')

    mem = proj / "memory"
    mem.mkdir()
    (mem / "notes.md").write_text("# Notes\nSome content")

    # Create .claude.json with project metadata
    (claude_home / ".claude.json").write_text(
        json.dumps(
            {
                "projects": {
                    "/Users/test/myproject": {
                        "lastCost": 2.50,
                        "hasTrustDialogAccepted": True,
                        "allowedTools": ["Read"],
                    }
                }
            }
        )
    )

    result = read_claude_projects(claude_home)
    assert len(result["projects"]) == 1
    p = result["projects"][0]
    assert p["encoded_name"] == "-Users-test-myproject"
    assert p["session_count"] == 2
    assert p["memory_file_count"] == 1
    assert p["last_cost"] == 2.50
    assert p["has_trust_accepted"] is True
    assert result["global_stats"]["total_projects"] == 1
    assert result["global_stats"]["aggregate_cost"] == 2.50


def test_read_claude_projects_empty(tmp_path):
    """read_claude_projects returns empty for missing projects dir."""
    from ai_ctrl_plane.config_readers.claude_config import read_claude_projects

    result = read_claude_projects(tmp_path / "nonexistent")
    assert result["projects"] == []
    assert result["global_stats"]["total_projects"] == 0


def test_read_claude_projects_masks_secrets(tmp_path):
    """MCP server tokens in project metadata should be masked."""
    from ai_ctrl_plane.config_readers.claude_config import read_claude_projects

    claude_home = tmp_path / ".claude"
    (claude_home / "projects" / "-proj").mkdir(parents=True)
    (claude_home / ".claude.json").write_text(
        json.dumps(
            {
                "projects": {
                    "/proj": {"mcpServers": {"my-srv": {"env": {"TOKEN": "ghp_abcdefghijklmnopqrstuvwxyz1234567890"}}}}
                }
            }
        )
    )

    result = read_claude_projects(claude_home)
    p = result["projects"][0]
    token = p["metadata"]["mcpServers"]["my-srv"]["env"]["TOKEN"]
    assert "ghp_abcdefghijklmnop" not in token
    assert "****" in token


# ---------------------------------------------------------------------------
# Claude Desktop config reader
# ---------------------------------------------------------------------------


def test_read_claude_desktop_config_basic(tmp_path):
    """read_claude_desktop_config should read MCP servers and preferences."""
    from ai_ctrl_plane.config_readers.claude_config import read_claude_desktop_config

    desktop_dir = tmp_path / "Claude"
    desktop_dir.mkdir()

    (desktop_dir / "claude_desktop_config.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "filesystem": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                    }
                },
                "preferences": {"coworkScheduledTasksEnabled": True},
            }
        )
    )

    (desktop_dir / "config.json").write_text(
        json.dumps(
            {
                "locale": "en-US",
                "userThemeMode": "system",
                "oauthAccount": {"should": "be excluded"},
            }
        )
    )

    result = read_claude_desktop_config(desktop_dir)
    assert result["installed"] is True
    assert len(result["mcp_servers"]) == 1
    assert result["mcp_servers"][0]["name"] == "filesystem"
    assert result["preferences"]["coworkScheduledTasksEnabled"] is True
    assert result["ui_config"]["locale"] == "en-US"
    # Sensitive fields excluded
    assert "oauthAccount" not in result["ui_config"]


def test_read_claude_desktop_config_missing(tmp_path):
    """read_claude_desktop_config returns installed=False for missing dir."""
    from ai_ctrl_plane.config_readers.claude_config import read_claude_desktop_config

    result = read_claude_desktop_config(tmp_path / "nonexistent")
    assert result["installed"] is False
    assert result["mcp_servers"] == []


def test_read_claude_desktop_masks_oauth(tmp_path):
    """OAuth token cache should be excluded from config.json."""
    from ai_ctrl_plane.config_readers.claude_config import read_claude_desktop_config

    desktop_dir = tmp_path / "Claude"
    desktop_dir.mkdir()
    (desktop_dir / "config.json").write_text(
        json.dumps(
            {
                "locale": "en-US",
                "oauth:tokenCache": {"access_token": "secret123456"},
                "oauthAccount": {"id": "user-123"},
            }
        )
    )

    result = read_claude_desktop_config(desktop_dir)
    assert "oauth:tokenCache" not in result["ui_config"]
    assert "oauthAccount" not in result["ui_config"]
    assert result["ui_config"]["locale"] == "en-US"


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


# ---------------------------------------------------------------------------
# Claude Desktop skills + cowork plugins tests
# ---------------------------------------------------------------------------


def test_read_claude_desktop_config_reads_skills(tmp_path):
    """read_claude_desktop_config should read skills from skills-plugin sessions dir."""
    from ai_ctrl_plane.config_readers.claude_config import read_claude_desktop_config

    desktop_dir = tmp_path / "Claude"
    desktop_dir.mkdir()
    inner = desktop_dir / "local-agent-mode-sessions" / "skills-plugin" / "aaaa-1111" / "bbbb-2222"
    skill_dir = inner / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: my-skill\ndescription: Test skill\n---\nBody\n")
    (inner / "manifest.json").write_text(
        json.dumps({"skills": [{"skillId": "skill_abc", "name": "my-skill", "creatorType": "user", "enabled": True}]})
    )

    result = read_claude_desktop_config(desktop_dir)
    assert len(result["skills"]) == 1
    s = result["skills"][0]
    assert s["name"] == "my-skill"
    assert s["enabled"] is True
    assert s["creator_type"] == "user"


def test_read_claude_desktop_config_empty_when_no_sessions(tmp_path):
    """read_claude_desktop_config returns empty lists when no session dirs exist."""
    from ai_ctrl_plane.config_readers.claude_config import read_claude_desktop_config

    desktop_dir = tmp_path / "Claude"
    desktop_dir.mkdir()

    result = read_claude_desktop_config(desktop_dir)
    assert result["skills"] == []
    assert result["cowork_plugins"] == []


def test_read_claude_desktop_config_reads_cowork_plugins(tmp_path):
    """read_claude_desktop_config should read Cowork plugins from sessions dir."""
    from ai_ctrl_plane.config_readers.claude_config import read_claude_desktop_config

    desktop_dir = tmp_path / "Claude"
    desktop_dir.mkdir()
    inner = desktop_dir / "local-agent-mode-sessions" / "sess-1" / "inner-1"
    plugin_cache = inner / "cowork_plugins" / "cache" / "my-market" / "my-plugin" / "1.0.0"
    plugin_cache.mkdir(parents=True)
    (plugin_cache / ".claude-plugin").mkdir()
    (plugin_cache / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "my-plugin", "version": "1.0.0", "description": "A plugin", "author": {"name": "Acme"}})
    )
    cowork_dir = inner / "cowork_plugins"
    (cowork_dir / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {"my-plugin@my-market": [{"installPath": str(plugin_cache), "version": "1.0.0"}]},
            }
        )
    )
    (cowork_dir / "cowork_settings.json").write_text(json.dumps({"enabledPlugins": {"my-plugin@my-market": True}}))

    result = read_claude_desktop_config(desktop_dir)
    assert len(result["cowork_plugins"]) == 1
    p = result["cowork_plugins"][0]
    assert p["name"] == "my-plugin"
    assert p["enabled"] is True
    assert p["author"] == "Acme"


# ---------------------------------------------------------------------------
# Windows path fallback tests
# ---------------------------------------------------------------------------


def test_default_global_config_path_prefers_localappdata(tmp_path, monkeypatch):
    """On Windows, _default_global_config_path() prefers %LOCALAPPDATA%\\claude\\.claude.json when it exists."""
    from ai_ctrl_plane.config_readers.claude_config import _default_global_config_path

    localappdata_dir = tmp_path / "Local"
    primary = localappdata_dir / "claude" / ".claude.json"
    primary.parent.mkdir(parents=True)
    primary.touch()
    userprofile_dir = tmp_path / "Users" / "user"
    userprofile_dir.mkdir(parents=True)

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata_dir))
    monkeypatch.setattr("pathlib.Path.home", lambda: userprofile_dir)

    result = _default_global_config_path()
    assert result == primary


def test_default_global_config_path_falls_back_to_userprofile(tmp_path, monkeypatch):
    """On Windows, _default_global_config_path() falls back to %USERPROFILE%\\.claude.json
    when %LOCALAPPDATA%\\claude\\.claude.json does not exist."""
    from ai_ctrl_plane.config_readers.claude_config import _default_global_config_path

    localappdata_dir = tmp_path / "Local"
    localappdata_dir.mkdir(parents=True)
    # primary (%LOCALAPPDATA%\claude\.claude.json) does NOT exist
    userprofile_dir = tmp_path / "Users" / "user"
    fallback = userprofile_dir / ".claude.json"
    fallback.parent.mkdir(parents=True)
    fallback.touch()

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata_dir))
    monkeypatch.setattr("pathlib.Path.home", lambda: userprofile_dir)

    result = _default_global_config_path()
    assert result == fallback


def test_default_claude_home_prefers_localappdata(tmp_path, monkeypatch):
    """On Windows, _default_claude_home() prefers %LOCALAPPDATA%\\claude when it exists."""
    from ai_ctrl_plane.config_readers.claude_config import _default_claude_home

    localappdata_dir = tmp_path / "Local"
    primary = localappdata_dir / "claude"
    primary.mkdir(parents=True)
    userprofile_dir = tmp_path / "Users" / "user"
    userprofile_dir.mkdir(parents=True)

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata_dir))
    monkeypatch.setattr("pathlib.Path.home", lambda: userprofile_dir)

    result = _default_claude_home()
    assert result == primary


def test_default_claude_home_falls_back_to_userprofile(tmp_path, monkeypatch):
    """On Windows, _default_claude_home() falls back to %USERPROFILE%\\.claude
    when %LOCALAPPDATA%\\claude does not exist."""
    from ai_ctrl_plane.config_readers.claude_config import _default_claude_home

    localappdata_dir = tmp_path / "Local"
    localappdata_dir.mkdir(parents=True)
    # primary (LOCALAPPDATA\claude) does NOT exist
    userprofile_dir = tmp_path / "Users" / "user"
    fallback = userprofile_dir / ".claude"
    fallback.mkdir(parents=True)

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata_dir))
    monkeypatch.setattr("pathlib.Path.home", lambda: userprofile_dir)

    result = _default_claude_home()
    assert result == fallback


def test_default_copilot_home_prefers_localappdata(tmp_path, monkeypatch):
    """On Windows, _default_copilot_home() prefers %LOCALAPPDATA%\\github-copilot when it exists."""
    from ai_ctrl_plane.config_readers.copilot_config import _default_copilot_home

    localappdata_dir = tmp_path / "Local"
    primary = localappdata_dir / "github-copilot"
    primary.mkdir(parents=True)
    userprofile_dir = tmp_path / "Users" / "user"
    userprofile_dir.mkdir(parents=True)

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata_dir))
    monkeypatch.setattr("pathlib.Path.home", lambda: userprofile_dir)

    result = _default_copilot_home()
    assert result == primary


def test_default_copilot_home_falls_back_to_userprofile(tmp_path, monkeypatch):
    """On Windows, _default_copilot_home() falls back to %USERPROFILE%\\.copilot
    when %LOCALAPPDATA%\\github-copilot does not exist."""
    from ai_ctrl_plane.config_readers.copilot_config import _default_copilot_home

    localappdata_dir = tmp_path / "Local"
    localappdata_dir.mkdir(parents=True)
    # primary (LOCALAPPDATA\github-copilot) does NOT exist
    userprofile_dir = tmp_path / "Users" / "user"
    fallback = userprofile_dir / ".copilot"
    fallback.mkdir(parents=True)

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata_dir))
    monkeypatch.setattr("pathlib.Path.home", lambda: userprofile_dir)

    result = _default_copilot_home()
    assert result == fallback


def test_default_claude_desktop_dir_prefers_standard_on_windows(tmp_path, monkeypatch):
    """On Windows, _default_claude_desktop_dir() prefers %APPDATA%\\Claude when it exists."""
    from ai_ctrl_plane.config_readers.claude_config import _default_claude_desktop_dir

    appdata_dir = tmp_path / "Roaming"
    standard = appdata_dir / "Claude"
    standard.mkdir(parents=True)
    localappdata_dir = tmp_path / "Local"
    localappdata_dir.mkdir(parents=True)

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("APPDATA", str(appdata_dir))
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata_dir))

    result = _default_claude_desktop_dir()
    assert result == standard


def test_default_claude_desktop_dir_falls_back_to_msix(tmp_path, monkeypatch):
    """On Windows, _default_claude_desktop_dir() falls back to the known MSIX path when standard is absent."""
    from ai_ctrl_plane.config_readers.claude_config import _default_claude_desktop_dir

    appdata_dir = tmp_path / "Roaming"
    appdata_dir.mkdir(parents=True)
    # standard (%APPDATA%\Claude) does NOT exist
    localappdata_dir = tmp_path / "Local"
    msix_path = localappdata_dir / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache" / "Roaming" / "Claude"
    msix_path.mkdir(parents=True)

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("APPDATA", str(appdata_dir))
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata_dir))

    result = _default_claude_desktop_dir()
    assert result == msix_path


def test_default_claude_desktop_dir_glob_fallback(tmp_path, monkeypatch):
    """On Windows, _default_claude_desktop_dir() uses glob fallback for unknown MSIX publisher IDs."""
    from ai_ctrl_plane.config_readers.claude_config import _default_claude_desktop_dir

    appdata_dir = tmp_path / "Roaming"
    appdata_dir.mkdir(parents=True)
    # standard (%APPDATA%\Claude) does NOT exist
    localappdata_dir = tmp_path / "Local"
    # Use a different publisher ID (not the known one) to trigger glob fallback
    glob_path = localappdata_dir / "Packages" / "Claude_unknownpublisher" / "LocalCache" / "Roaming" / "Claude"
    glob_path.mkdir(parents=True)

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("APPDATA", str(appdata_dir))
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata_dir))

    result = _default_claude_desktop_dir()
    assert result == glob_path


# ---------------------------------------------------------------------------
# UTF-8 encoding tests (regression for Windows cp1252 crash)
# ---------------------------------------------------------------------------


def test_read_skills_utf8_content(tmp_path):
    """read_skills must handle UTF-8 content (accents, CJK, emoji) without UnicodeDecodeError."""
    from ai_ctrl_plane.config_readers._common import read_skills

    skills_dir = tmp_path / "skills"
    skill = skills_dir / "utf8-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: utf8-skill\ndescription: Ünïcödé skill — 日本語 🎉\nauthor: René\n---\n"
        "# Héllo\n\nBody with emojis 🚀 and kanji 漢字.\n",
        encoding="utf-8",
    )

    result = read_skills(skills_dir)
    assert len(result) == 1
    assert result[0]["name"] == "utf8-skill"
    assert "日本語" in result[0]["description"]
    assert "漢字" in result[0]["body"]


def test_safe_read_json_utf8(tmp_path):
    """safe_read_json must read UTF-8 JSON without UnicodeDecodeError."""
    from ai_ctrl_plane.config_readers._common import safe_read_json

    cfg = tmp_path / "config.json"
    cfg.write_text('{"name": "René", "note": "日本語 🎉"}', encoding="utf-8")

    result = safe_read_json(cfg)
    assert result is not None
    assert result["name"] == "René"
    assert "日本語" in result["note"]


def test_read_repo_permissions_handles_non_dict_permissions(tmp_path):
    """A corrupted settings file with ``"permissions": []`` (or any
    non-dict value) used to crash ``perms.get(...)``. Regression for
    PR #27 review #51."""
    from ai_ctrl_plane.config_readers.claude_config import _read_repo_permissions

    repo = tmp_path / "repo"
    claude_dir = repo / ".claude"
    claude_dir.mkdir(parents=True)

    for bad_perms_payload in (
        '{"permissions": []}',
        '{"permissions": "not-a-dict"}',
        '{"permissions": 42}',
        '{"permissions": null}',
    ):
        (claude_dir / "settings.json").write_text(bad_perms_payload, encoding="utf-8")
        result = _read_repo_permissions(str(repo))
        assert result == {"allow": [], "deny": [], "ask": []}, (
            f"payload {bad_perms_payload!r} should yield empty rule lists"
        )

    # Valid permissions still work.
    (claude_dir / "settings.json").write_text(
        '{"permissions": {"allow": ["Read"], "deny": ["Bash"]}}', encoding="utf-8"
    )
    result = _read_repo_permissions(str(repo))
    assert result["allow"] == ["Read"]
    assert result["deny"] == ["Bash"]


def test_safe_read_json_returns_none_for_non_dict_root(tmp_path):
    """The declared return type is ``dict | None`` but ``json.load`` can
    return any JSON value at the root. Non-dict roots must be coerced
    to ``None`` so callers using the ``safe_read_json(...) or {}``
    pattern can't crash on ``data.get(...)``. Regression for PR #27
    reviews #47 and #48 (the systemic fix that protects every caller
    in the project at once)."""
    from ai_ctrl_plane.config_readers._common import safe_read_json

    for payload in ('["a", "b"]', '"just a string"', "42", "null", "true"):
        cfg = tmp_path / "x.json"
        cfg.write_text(payload, encoding="utf-8")
        assert safe_read_json(cfg) is None, f"payload {payload!r} should return None"

    # Sanity: a real dict still passes through.
    cfg.write_text('{"k": "v"}', encoding="utf-8")
    assert safe_read_json(cfg) == {"k": "v"}


def test_copilot_parse_events_utf8(tmp_path):
    """Copilot parser must read UTF-8 JSONL session files without UnicodeDecodeError."""
    from ai_ctrl_plane.parser import parse_events

    session_dir = tmp_path / "sess"
    session_dir.mkdir()
    events_jsonl = session_dir / "events.jsonl"
    events_jsonl.write_text(
        '{"type": "user.sent", "data": {"message": "Héllo — 日本語 🎉"}, "timestamp": "2026-01-01T00:00:00Z"}\n',
        encoding="utf-8",
    )
    (session_dir / "workspace.yaml").write_text(
        "id: test\ncwd: /tmp\nrepository: org/repo\nbranch: main\nsummary: s\n"
        "created_at: 2026-01-01T00:00:00Z\nupdated_at: 2026-01-01T00:00:00Z\n",
        encoding="utf-8",
    )

    events = parse_events(session_dir)
    assert any("Héllo" in str(e) or "日本語" in str(e) for e in events)


def test_claude_parser_utf8(tmp_path):
    """Claude parser must read UTF-8 JSONL files without UnicodeDecodeError."""
    from ai_ctrl_plane.claude_parser import parse_events

    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text(
        '{"type": "user", "message": {"role": "user", "content": "Héllo — 日本語 🎉"},'
        ' "uuid": "u1", "timestamp": "2026-01-01T00:00:00Z",'
        ' "sessionId": "s1", "cwd": "/tmp", "version": "2.0", "gitBranch": "main"}\n',
        encoding="utf-8",
    )

    events = parse_events(jsonl)
    assert len(events) == 1
    assert "Héllo" in str(events[0])


# ---------------------------------------------------------------------------
# Global memory files
# ---------------------------------------------------------------------------


def test_claude_config_reads_global_memory(tmp_path):
    """read_claude_config() returns memory files from ~/.claude/memory/."""
    home = tmp_path / ".claude"
    home.mkdir()
    (home / ".claude.json").write_text("{}", encoding="utf-8")
    memory_dir = home / "memory"
    memory_dir.mkdir()
    (memory_dir / "user_role.md").write_text("---\nname: role\n---\nI am a developer.", encoding="utf-8")
    (memory_dir / "feedback.md").write_text("---\nname: feedback\n---\nBe concise.", encoding="utf-8")

    from ai_ctrl_plane.config_readers.claude_config import read_claude_config

    cfg = read_claude_config(claude_home=home)
    assert len(cfg["memory_files"]) == 2
    filenames = [m["filename"] for m in cfg["memory_files"]]
    assert "user_role.md" in filenames
    assert "feedback.md" in filenames
    contents = [m["content"] for m in cfg["memory_files"]]
    assert any("developer" in c for c in contents)


def test_claude_config_empty_memory_when_no_dir(tmp_path):
    """read_claude_config() returns empty memory_files when directory is absent."""
    home = tmp_path / ".claude"
    home.mkdir()
    (home / ".claude.json").write_text("{}", encoding="utf-8")

    from ai_ctrl_plane.config_readers.claude_config import read_claude_config

    cfg = read_claude_config(claude_home=home)
    assert cfg["memory_files"] == []


# ---------------------------------------------------------------------------
# Windows managed-settings
# ---------------------------------------------------------------------------


def test_claude_config_reads_managed_settings_windows(tmp_path, monkeypatch):
    """On Windows, reads managed-settings.json from %PROGRAMFILES%\\ClaudeCode\\."""
    home = tmp_path / ".claude"
    home.mkdir()
    (home / ".claude.json").write_text("{}", encoding="utf-8")

    prog_files = tmp_path / "ProgramFiles"
    managed_dir = prog_files / "ClaudeCode"
    managed_dir.mkdir(parents=True)
    (managed_dir / "managed-settings.json").write_text(
        '{"disableAutoUpdate": true, "allowedTools": ["bash"]}', encoding="utf-8"
    )

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("PROGRAMFILES", str(prog_files))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "ProgramData"))  # does not exist

    cfg = read_claude_config(claude_home=home)
    assert cfg["managed_settings"].get("disableAutoUpdate") is True
    assert cfg["managed_settings_legacy"] == {}


def test_claude_config_reads_managed_settings_legacy_windows(tmp_path, monkeypatch):
    """On Windows, reads legacy managed-settings.json from %PROGRAMDATA%\\ClaudeCode\\."""
    home = tmp_path / ".claude"
    home.mkdir()
    (home / ".claude.json").write_text("{}", encoding="utf-8")

    programdata = tmp_path / "ProgramData"
    legacy_dir = programdata / "ClaudeCode"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "managed-settings.json").write_text(
        '{"legacyPolicy": true}', encoding="utf-8"
    )

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "ProgramFiles"))  # does not exist
    monkeypatch.setenv("PROGRAMDATA", str(programdata))

    cfg = read_claude_config(claude_home=home)
    assert cfg["managed_settings"] == {}
    assert cfg["managed_settings_legacy"].get("legacyPolicy") is True


# ---------------------------------------------------------------------------
# Cross-platform managed-settings (macOS / Linux via _default_managed_dir patch)
# ---------------------------------------------------------------------------


def test_claude_config_reads_managed_settings_macos(tmp_path, monkeypatch):
    """Reads managed-settings.json from macOS system dir via _default_managed_dir."""
    home = tmp_path / ".claude"
    home.mkdir()
    (home / ".claude.json").write_text("{}", encoding="utf-8")
    managed_dir = tmp_path / "managed"
    managed_dir.mkdir()
    (managed_dir / "managed-settings.json").write_text(
        '{"disableTelemetry": true}', encoding="utf-8"
    )

    import ai_ctrl_plane.config_readers.claude_config as cc
    monkeypatch.setattr(cc, "_default_managed_dir", lambda: managed_dir)

    cfg = cc.read_claude_config(claude_home=home)
    assert cfg["managed_settings"].get("disableTelemetry") is True
    assert cfg["managed_settings_legacy"] == {}


def test_claude_config_reads_managed_settings_linux(tmp_path, monkeypatch):
    """Reads managed-settings.json from Linux system dir via _default_managed_dir."""
    home = tmp_path / ".claude"
    home.mkdir()
    (home / ".claude.json").write_text("{}", encoding="utf-8")
    managed_dir = tmp_path / "etc-claude-code"
    managed_dir.mkdir()
    (managed_dir / "managed-settings.json").write_text(
        '{"maxThinkingTokens": 8000}', encoding="utf-8"
    )

    import ai_ctrl_plane.config_readers.claude_config as cc
    monkeypatch.setattr(cc, "_default_managed_dir", lambda: managed_dir)

    cfg = cc.read_claude_config(claude_home=home)
    assert cfg["managed_settings"].get("maxThinkingTokens") == 8000


def test_claude_config_reads_managed_mcp(tmp_path, monkeypatch):
    """Reads managed-mcp.json and returns structured managed_mcp_servers list."""
    home = tmp_path / ".claude"
    home.mkdir()
    (home / ".claude.json").write_text("{}", encoding="utf-8")
    managed_dir = tmp_path / "managed"
    managed_dir.mkdir()
    (managed_dir / "managed-mcp.json").write_text(
        '{"mcpServers": {"corp-tools": {"command": "node", "args": ["index.js"], "type": "stdio"}}}',
        encoding="utf-8",
    )

    import ai_ctrl_plane.config_readers.claude_config as cc
    monkeypatch.setattr(cc, "_default_managed_dir", lambda: managed_dir)

    cfg = cc.read_claude_config(claude_home=home)
    assert len(cfg["managed_mcp_servers"]) == 1
    srv = cfg["managed_mcp_servers"][0]
    assert srv["name"] == "corp-tools"
    assert srv["type"] == "stdio"
    assert srv["command"] == "node"
    assert srv["args"] == ["index.js"]


def test_claude_config_managed_mcp_empty_when_no_file(tmp_path, monkeypatch):
    """managed_mcp_servers is empty when managed-mcp.json is absent."""
    home = tmp_path / ".claude"
    home.mkdir()
    (home / ".claude.json").write_text("{}", encoding="utf-8")
    managed_dir = tmp_path / "managed"
    managed_dir.mkdir()  # dir exists but no managed-mcp.json

    import ai_ctrl_plane.config_readers.claude_config as cc
    monkeypatch.setattr(cc, "_default_managed_dir", lambda: managed_dir)

    cfg = cc.read_claude_config(claude_home=home)
    assert cfg["managed_mcp_servers"] == []


def test_claude_config_reads_managed_mcp_legacy_windows(tmp_path, monkeypatch):
    """On Windows, reads legacy managed-mcp.json from %PROGRAMDATA%\\ClaudeCode\\."""
    home = tmp_path / ".claude"
    home.mkdir()
    (home / ".claude.json").write_text("{}", encoding="utf-8")

    programdata = tmp_path / "ProgramData"
    legacy_dir = programdata / "ClaudeCode"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "managed-mcp.json").write_text(
        '{"mcpServers": {"legacy-tool": {"command": "python", "args": ["-m", "tool"], "type": "stdio"}}}',
        encoding="utf-8",
    )

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "ProgramFiles"))  # does not exist
    monkeypatch.setenv("PROGRAMDATA", str(programdata))

    import ai_ctrl_plane.config_readers.claude_config as cc
    monkeypatch.setattr(cc, "_default_managed_dir", lambda: tmp_path / "ProgramFiles" / "ClaudeCode")

    cfg = cc.read_claude_config(claude_home=home)
    assert cfg["managed_mcp_servers"] == []
    assert len(cfg["managed_mcp_servers_legacy"]) == 1
    srv = cfg["managed_mcp_servers_legacy"][0]
    assert srv["name"] == "legacy-tool"
    assert srv["command"] == "python"
    assert srv["args"] == ["-m", "tool"]


# ---------------------------------------------------------------------------
# VS Code Insiders
# ---------------------------------------------------------------------------


def test_vscode_config_reads_insiders(tmp_path, monkeypatch):
    """read_vscode_config() reads Insiders MCP servers when insiders dir exists."""
    stable_dir = tmp_path / "Code" / "User"
    stable_dir.mkdir(parents=True)

    insiders_dir = tmp_path / "Code - Insiders" / "User"
    insiders_dir.mkdir(parents=True)
    (insiders_dir / "mcp.json").write_text(
        '{"servers": {"insiders-server": {"command": "npx", "args": ["-y", "server"], "type": "stdio"}}}',
        encoding="utf-8",
    )

    from ai_ctrl_plane.config_readers import vscode_config
    monkeypatch.setattr(vscode_config, "_default_vscode_insiders_user_dir", lambda: insiders_dir)

    cfg = vscode_config.read_vscode_config(vscode_user_dir=stable_dir)
    # When vscode_user_dir is explicitly passed, Insiders scanning is skipped
    assert cfg["insiders_mcp_servers"] == []

    # Without override — Insiders is auto-discovered
    monkeypatch.setattr(vscode_config, "_default_vscode_user_dir", lambda: stable_dir)
    cfg2 = vscode_config.read_vscode_config()
    assert cfg2["insiders_installed"] is True
    assert len(cfg2["insiders_mcp_servers"]) == 1
    assert cfg2["insiders_mcp_servers"][0]["name"] == "insiders-server"


def test_vscode_config_insiders_not_installed(tmp_path, monkeypatch):
    """insiders_installed is False when the Insiders directory does not exist."""
    stable_dir = tmp_path / "Code" / "User"
    stable_dir.mkdir(parents=True)
    insiders_dir = tmp_path / "Code - Insiders" / "User"  # does not exist

    from ai_ctrl_plane.config_readers import vscode_config
    monkeypatch.setattr(vscode_config, "_default_vscode_user_dir", lambda: stable_dir)
    monkeypatch.setattr(vscode_config, "_default_vscode_insiders_user_dir", lambda: insiders_dir)

    cfg = vscode_config.read_vscode_config()
    assert cfg["insiders_installed"] is False
    assert cfg["insiders_mcp_servers"] == []


# ---------------------------------------------------------------------------
# Additional Claude Code config files
# ---------------------------------------------------------------------------


def test_claude_config_blocklist_dict_format(tmp_path):
    """plugin_blocklist is populated when blocklist.json uses the dict format."""
    home = tmp_path / ".claude"
    home.mkdir()
    (home / ".claude.json").write_text("{}", encoding="utf-8")
    plugins_dir = home / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "blocklist.json").write_text(
        '{"fetchedAt": "2026-01-01T00:00:00Z",'
        ' "plugins": [{"plugin": "bad@mkt", "reason": "security", "added_at": "2026-01-01"}]}',
        encoding="utf-8",
    )

    from ai_ctrl_plane.config_readers.claude_config import read_claude_config

    cfg = read_claude_config(claude_home=home)
    assert len(cfg["plugin_blocklist"]) == 1
    assert cfg["plugin_blocklist"][0]["plugin"] == "bad@mkt"


def test_claude_config_reads_mcp_auth_cache(tmp_path):
    """read_claude_config() reads mcp-needs-auth-cache.json."""
    home = tmp_path / ".claude"
    home.mkdir()
    (home / ".claude.json").write_text("{}", encoding="utf-8")
    (home / "mcp-needs-auth-cache.json").write_text(
        '{"claude.ai Slack": {"timestamp": 1234567890}}', encoding="utf-8"
    )

    from ai_ctrl_plane.config_readers.claude_config import read_claude_config

    cfg = read_claude_config(claude_home=home)
    assert "claude.ai Slack" in cfg["mcp_auth_cache"]


def test_claude_config_reads_remote_settings(tmp_path):
    """read_claude_config() reads remote-settings.json and masks env values."""
    home = tmp_path / ".claude"
    home.mkdir()
    (home / ".claude.json").write_text("{}", encoding="utf-8")
    (home / "remote-settings.json").write_text(
        '{"cleanupPeriodDays": 7, "env": {"SECRET_TOKEN": "abc123"}, "permissions": {"deny": ["Bash(sudo:*)"]}}',
        encoding="utf-8",
    )

    from ai_ctrl_plane.config_readers.claude_config import read_claude_config

    cfg = read_claude_config(claude_home=home)
    assert cfg["remote_settings"].get("cleanupPeriodDays") == 7
    # env contains a key that mask_dict should mask (it has "token" in the name)
    env = cfg["remote_settings"].get("env", {})
    assert isinstance(env, dict)
    assert "SECRET_TOKEN" in env
    assert env["SECRET_TOKEN"] != "abc123"
    assert isinstance(env["SECRET_TOKEN"], str)
    assert env["SECRET_TOKEN"].endswith("****")
    # permissions deny list should be present
    perms = cfg["remote_settings"].get("permissions", {})
    assert "Bash(sudo:*)" in perms.get("deny", [])


def test_claude_config_reads_stats_cache(tmp_path):
    """read_claude_config() reads stats-cache.json."""
    home = tmp_path / ".claude"
    home.mkdir()
    (home / ".claude.json").write_text("{}", encoding="utf-8")
    (home / "stats-cache.json").write_text(
        '{"version": 2, "totalSessions": 5, "totalMessages": 20, "modelUsage": {}}',
        encoding="utf-8",
    )

    from ai_ctrl_plane.config_readers.claude_config import read_claude_config

    cfg = read_claude_config(claude_home=home)
    assert cfg["stats"].get("totalSessions") == 5
    assert cfg["stats"].get("totalMessages") == 20


def test_claude_config_reads_known_marketplaces(tmp_path):
    """read_claude_config() reads plugins/known_marketplaces.json."""
    home = tmp_path / ".claude"
    home.mkdir()
    (home / ".claude.json").write_text("{}", encoding="utf-8")
    plugins_dir = home / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "known_marketplaces.json").write_text(
        '{"my-mkt": {"source": {"source": "github", "repo": "acme/mkt"}, "lastUpdated": "2026-01-01T00:00:00Z"}}',
        encoding="utf-8",
    )

    from ai_ctrl_plane.config_readers.claude_config import read_claude_config

    cfg = read_claude_config(claude_home=home)
    assert "my-mkt" in cfg["known_marketplaces"]


def test_claude_config_reads_install_counts(tmp_path):
    """read_claude_config() reads plugins/install-counts-cache.json."""
    home = tmp_path / ".claude"
    home.mkdir()
    (home / ".claude.json").write_text("{}", encoding="utf-8")
    plugins_dir = home / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "install-counts-cache.json").write_text(
        '{"version": 1, "fetchedAt": "2026-01-01T00:00:00Z",'
        ' "counts": [{"plugin": "foo@mkt", "unique_installs": 999}]}',
        encoding="utf-8",
    )

    from ai_ctrl_plane.config_readers.claude_config import read_claude_config

    cfg = read_claude_config(claude_home=home)
    counts = cfg["install_counts_cache"].get("counts", [])
    assert len(counts) == 1
    assert counts[0]["plugin"] == "foo@mkt"
