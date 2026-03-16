"""Tests for /tools routes."""

from __future__ import annotations

from ai_ctrl_plane.app import create_app


def _client(tmp_path):
    app = create_app(
        log_dir=str(tmp_path / "copilot"),
        claude_dir=str(tmp_path / "claude"),
        vscode_dir=str(tmp_path / "vscode"),
        cache_dir=str(tmp_path / "cache"),
    )
    app.config["TESTING"] = True
    return app.test_client()


def test_tools_overview_200(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/tools")
    assert resp.status_code == 200
    assert b"Tool Configuration" in resp.data


def test_tool_detail_claude_200(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/tools/claude")
    assert resp.status_code == 200
    assert b"Claude Code" in resp.data


def test_tool_detail_copilot_200(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/tools/copilot")
    assert resp.status_code == 200
    assert b"GitHub Copilot" in resp.data


def test_tool_detail_vscode_200(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/tools/vscode")
    assert resp.status_code == 200
    assert b"VS Code Chat" in resp.data


def test_tool_detail_invalid_404(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/tools/invalid")
    assert resp.status_code == 404


def test_api_tools_json(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/api/tools")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "claude" in data
    assert "copilot" in data
    assert "vscode" in data


def test_api_tool_claude_json(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/api/tools/claude")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "installed" in data
    assert "mcp_servers" in data


def test_api_tool_invalid_404(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/api/tools/invalid")
    assert resp.status_code == 404


def test_sessions_route_200(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/sessions")
    assert resp.status_code == 200
    assert b"Sessions" in resp.data


def test_dashboard_has_tool_configs(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"AI Control Plane" in resp.data
    assert b"MCP Servers" in resp.data


def test_agents_route_200(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/agents")
    assert resp.status_code == 200
    assert b"Agents" in resp.data


def test_dashboard_has_brand_icons(tmp_path):
    """Dashboard tool cards should include brand SVG icons."""
    client = _client(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    # Check that SVG icons are rendered (from macros)
    assert b"<svg" in resp.data
    assert b"Claude Code" in resp.data
    assert b"GitHub Copilot" in resp.data
    assert b"VS Code Chat" in resp.data


def test_tools_page_has_brand_icons(tmp_path):
    """Tools page should include brand SVG icons."""
    client = _client(tmp_path)
    resp = client.get("/tools")
    assert resp.status_code == 200
    assert b"<svg" in resp.data


def test_tool_detail_has_tab_icons(tmp_path):
    """Tool detail page tabs should include SVG brand icons."""
    client = _client(tmp_path)
    resp = client.get("/tools/claude")
    assert resp.status_code == 200
    # Tab bar should have all three tool links with icons
    assert b"tab-link" in resp.data
    assert b"Claude Code" in resp.data
    assert b"GitHub Copilot" in resp.data
    assert b"VS Code Chat" in resp.data


def test_navbar_present_on_all_pages(tmp_path):
    """Navbar should be rendered on every page."""
    client = _client(tmp_path)
    for path in ["/", "/sessions", "/tools", "/agents"]:
        resp = client.get(path)
        assert resp.status_code == 200, f"Failed for {path}"
        assert b"navbar" in resp.data, f"No navbar on {path}"
        assert b"AI Control Plane" in resp.data, f"No title on {path}"


def test_theme_toggle_present(tmp_path):
    """Theme toggle button should be present."""
    client = _client(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"theme-toggle" in resp.data
    assert b"toggleTheme" in resp.data


# ---------------------------------------------------------------------------
# Skills routes
# ---------------------------------------------------------------------------


def test_skills_route_200(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/skills")
    assert resp.status_code == 200
    assert b"Skills" in resp.data


def test_skills_route_has_filter_pills(tmp_path):
    """Skills page should have filter pill UI."""
    client = _client(tmp_path)
    resp = client.get("/skills")
    assert resp.status_code == 200
    assert b"filterSkills" in resp.data


def test_skill_detail_404_for_missing(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/skills/nonexistent-skill")
    assert resp.status_code == 404


def test_skills_deduplication(tmp_path):
    """Same skill installed in multiple tools should appear once."""
    # Create a skill in both "claude" and "copilot" locations
    claude_home = tmp_path / "claude_home"
    skill_dir = claude_home / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: test-skill\ndescription: A test skill\n---\nBody here.\n")

    copilot_home = tmp_path / "copilot_home"
    skill_dir2 = copilot_home / "skills" / "test-skill"
    skill_dir2.mkdir(parents=True)
    (skill_dir2 / "SKILL.md").write_text("---\nname: test-skill\ndescription: A test skill\n---\nBody here.\n")

    from unittest.mock import patch

    from ai_ctrl_plane.config_readers.claude_config import read_claude_config
    from ai_ctrl_plane.config_readers.copilot_config import read_copilot_config

    with (
        patch(
            "ai_ctrl_plane.config_readers.claude_config._default_claude_home",
            return_value=claude_home,
        ),
        patch(
            "ai_ctrl_plane.config_readers.copilot_config._default_copilot_home",
            return_value=copilot_home,
        ),
    ):
        c_cfg = read_claude_config(claude_home)
        cp_cfg = read_copilot_config(copilot_home)
        assert len(c_cfg["skills"]) == 1
        assert len(cp_cfg["skills"]) == 1
        assert c_cfg["skills"][0]["name"] == "test-skill"
        assert cp_cfg["skills"][0]["name"] == "test-skill"


def test_skill_detail_renders_body(tmp_path):
    """Skill detail page should render the markdown body."""
    claude_home = tmp_path / "claude_home"
    skill_dir = claude_home / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: My skill\n"
        "metadata:\n  author: TestAuthor\n  version: '1.0'\n  homepage: https://example.com\n"
        "license: MIT\n---\n# Hello World\n\nThis is the body.\n"
    )

    from unittest.mock import patch

    app = create_app(
        log_dir=str(tmp_path / "copilot"),
        claude_dir=str(tmp_path / "claude"),
        vscode_dir=str(tmp_path / "vscode"),
        cache_dir=str(tmp_path / "cache"),
    )
    app.config["TESTING"] = True

    with patch(
        "ai_ctrl_plane.config_readers.claude_config._default_claude_home",
        return_value=claude_home,
    ):
        with app.test_client() as client:
            resp = client.get("/skills/my-skill")
            assert resp.status_code == 200
            assert b"Hello World" in resp.data
            assert b"TestAuthor" in resp.data
            assert b"MIT" in resp.data
            # Homepage URL is rendered (sanitize_url allows https)
            assert b"Homepage" in resp.data


# ---------------------------------------------------------------------------
# Navbar / Active state
# ---------------------------------------------------------------------------


def test_agents_page_highlights_agents_nav(tmp_path):
    """Agents page should highlight the Agents nav item, not Tools."""
    client = _client(tmp_path)
    resp = client.get("/agents")
    assert resp.status_code == 200
    # The active nav should be agents, not tools
    html = resp.data.decode()
    # Find the agents nav link - it should have the 'active' class
    import re

    agents_link = re.search(r'href="/agents"[^>]*class="nav-item([^"]*)"', html)
    assert agents_link, "Agents nav link not found"
    assert "active" in agents_link.group(1)


def test_skills_page_highlights_skills_nav(tmp_path):
    """Skills page should highlight the Skills nav item."""
    client = _client(tmp_path)
    resp = client.get("/skills")
    assert resp.status_code == 200
    html = resp.data.decode()
    import re

    skills_link = re.search(r'href="/skills"[^>]*class="nav-item([^"]*)"', html)
    assert skills_link, "Skills nav link not found"
    assert "active" in skills_link.group(1)


def test_navbar_has_all_nav_items(tmp_path):
    """Navbar should include all navigation items."""
    client = _client(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    for item in [b"Dashboard", b"Sessions", b"Tools", b"Agents", b"Skills"]:
        assert item in resp.data, f"Missing nav item: {item}"


def test_favicon_present(tmp_path):
    """Pages should include an SVG favicon."""
    client = _client(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b'rel="icon"' in resp.data
    assert b"image/svg+xml" in resp.data


# ---------------------------------------------------------------------------
# Dashboard metrics
# ---------------------------------------------------------------------------


def test_dashboard_has_skills_metric(tmp_path):
    """Dashboard should show total skills count."""
    client = _client(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Skills" in resp.data  # metric label


def test_dashboard_has_project_metrics(tmp_path):
    """Dashboard should show Projects and Memory Files metric cards."""
    client = _client(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Projects" in resp.data
    assert b"Memory Files" in resp.data


# ---------------------------------------------------------------------------
# Projects routes
# ---------------------------------------------------------------------------


def test_projects_route_200(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/projects")
    assert resp.status_code == 200
    assert b"Claude Projects" in resp.data


def test_project_detail_404(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/projects/nonexistent-project")
    assert resp.status_code == 404


def test_project_name_traversal_rejected(tmp_path):
    """Path traversal in project name should be rejected."""
    client = _client(tmp_path)
    resp = client.get("/projects/../../etc/passwd")
    # Flask normalizes the URL, but the regex validation catches bad names
    assert resp.status_code in (400, 404)


def test_api_projects_json(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "projects" in data
    assert "stats" in data


def test_api_project_detail_404(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/api/projects/nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Settings routes
# ---------------------------------------------------------------------------


def test_settings_route_200(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert b"Settings" in resp.data
    assert b"Cache Status" in resp.data


def test_rebuild_cache_post(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/settings/rebuild-cache")
    # Should redirect to /settings
    assert resp.status_code == 302
    assert "/settings" in resp.headers["Location"]


# ---------------------------------------------------------------------------
# Cache status API
# ---------------------------------------------------------------------------


def test_cache_status_api(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/api/cache-status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "status" in data
    assert "db_path" in data


# ---------------------------------------------------------------------------
# Navbar updates
# ---------------------------------------------------------------------------


def test_navbar_has_projects_link(tmp_path):
    """Navbar should include Projects link."""
    client = _client(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Projects" in resp.data
    assert b"/projects" in resp.data


def test_navbar_has_settings_link(tmp_path):
    """Navbar should include settings gear icon."""
    client = _client(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"/settings" in resp.data


def test_cache_loading_stripe_in_base(tmp_path):
    """Base template should include the cache loading stripe div."""
    client = _client(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"cache-loading" in resp.data
    assert b"cache-stripe" in resp.data
