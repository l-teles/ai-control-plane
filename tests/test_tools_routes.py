"""Tests for /tools routes."""

from __future__ import annotations

from ai_log_viewer.app import create_app


def _client(tmp_path):
    app = create_app(
        log_dir=str(tmp_path / "copilot"),
        claude_dir=str(tmp_path / "claude"),
        vscode_dir=str(tmp_path / "vscode"),
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
