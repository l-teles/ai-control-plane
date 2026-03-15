<p align="center">
  <img src="https://raw.githubusercontent.com/l-teles/ai-control-plane/main/assets/logo.svg" alt="AI Control Plane logo" width="80">
</p>

<h1 align="center">AI Control Plane</h1>

<p align="center">
  A local web UI for browsing <strong>GitHub Copilot</strong>, <strong>Claude Code</strong>, and <strong>VS Code Chat</strong> agent session logs and tool configurations — all in one place.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.13%2B-blue" alt="Python 3.13+">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT">
</p>

AI coding agents produce rich session logs and configuration files. This tool turns
those raw files into a readable, interactive dashboard so you can review sessions
(prompts, reasoning, tool calls, sub-agents, errors) and inspect tool configurations
(MCP servers, plugins, agents, skills, slash commands, hooks, feature flags) — all in one place.

<table>
  <tr>
    <td align="center"><strong>Dashboard</strong></td>
    <td align="center"><strong>Session Timeline</strong></td>
  </tr>
  <tr>
    <td>
      <a href="https://raw.githubusercontent.com/l-teles/ai-control-plane/main/assets/screenshot_dark_home.png">
        <picture>
          <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/l-teles/ai-control-plane/main/assets/screenshot_dark_home.png">
          <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/l-teles/ai-control-plane/main/assets/screenshot_light_home.png">
          <img src="https://raw.githubusercontent.com/l-teles/ai-control-plane/main/assets/screenshot_dark_home.png" alt="Dashboard homepage" width="400">
        </picture>
      </a>
    </td>
    <td>
      <a href="https://raw.githubusercontent.com/l-teles/ai-control-plane/main/assets/screenshot_dark_session.png">
        <picture>
          <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/l-teles/ai-control-plane/main/assets/screenshot_dark_session.png">
          <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/l-teles/ai-control-plane/main/assets/screenshot_light_session.png">
          <img src="https://raw.githubusercontent.com/l-teles/ai-control-plane/main/assets/screenshot_dark_session.png" alt="Session timeline view" width="400">
        </picture>
      </a>
    </td>
  </tr>
  <tr>
    <td align="center"><strong>Tool Configuration</strong></td>
    <td align="center"><strong>Agents</strong></td>
  </tr>
  <tr>
    <td>
      <a href="https://raw.githubusercontent.com/l-teles/ai-control-plane/main/assets/screenshot_dark_tools.png">
        <picture>
          <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/l-teles/ai-control-plane/main/assets/screenshot_dark_tools.png">
          <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/l-teles/ai-control-plane/main/assets/screenshot_light_tools.png">
          <img src="https://raw.githubusercontent.com/l-teles/ai-control-plane/main/assets/screenshot_dark_tools.png" alt="Tool configuration page" width="400">
        </picture>
      </a>
    </td>
    <td>
      <a href="https://raw.githubusercontent.com/l-teles/ai-control-plane/main/assets/screenshot_dark_agents.png">
        <picture>
          <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/l-teles/ai-control-plane/main/assets/screenshot_dark_agents.png">
          <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/l-teles/ai-control-plane/main/assets/screenshot_light_agents.png">
          <img src="https://raw.githubusercontent.com/l-teles/ai-control-plane/main/assets/screenshot_dark_agents.png" alt="Agents page" width="400">
        </picture>
      </a>
    </td>
  </tr>
</table>


---

## Features

### Dashboard & Navigation
- **Metrics dashboard** — aggregated counts for MCP servers, plugins, agents,
  slash commands, hooks, feature flags, and sessions across all tools.
- **Tool cards** — at-a-glance status for Claude Code, GitHub Copilot, and
  VS Code Chat with key stats per tool.
- **Shared navbar** — sticky navigation with brand SVG icons, breadcrumbs,
  and dark/light theme toggle.

### Session Browser
- **Multi-source support** — browse sessions from GitHub Copilot, Claude Code, and
  VS Code Chat side by side, with color-coded source badges.
- **Search & filters** — full-text search across session names, branches, directories,
  and models; filter by source on the sessions page, by event type in timelines.
- **Interactive timeline** — color-coded conversation view with:
  - User messages (with file attachments)
  - Assistant responses (rendered Markdown, expandable reasoning/thinking)
  - Tool calls & results (expandable arguments / output)
  - Sub-agent launches & completions (expandable prompt / result)
  - System notifications, hooks, file snapshots, model changes
  - Errors and warnings
- **Statistics sidebar** — message counts, token usage (input/output/cache),
  tool breakdown with visual bars, MCP tool usage, rewind snapshots.

### Tool Configuration Inspector
- **Claude Code** — MCP servers, plugins (official + external), agents, skills
  (standalone + plugin-bundled), slash commands, hooks, feature flags (including
  GrowthBook server-side flags with toggle), settings, policy limits.
- **GitHub Copilot** — MCP servers, configuration, recent commands, skills.
- **VS Code Chat** — MCP servers, agents, skills, AI settings, language models.
- **Vertical tool navigation** — switch between tools from the sidebar.

### Agents
- **Aggregated agents page** — view all agents across Claude Code and VS Code
  in one place.
- **Clickable source filters** — filter agents by tool (Claude / VS Code) with
  pill-style toggle buttons.

### Skills
- **Skills browser** (`/skills`) — deduplicated list of all installed skills
  across Claude Code, GitHub Copilot, and VS Code Chat, with source badges
  showing which tools each skill is installed in.
- **Skill detail page** (`/skills/<name>`) — full rendered SKILL.md content
  with metadata sidebar (author, version, license, tools, homepage).

### General
- **Dark / Light mode** — toggle with one click; persisted in `localStorage`.
- **JSON API** — programmatic access at `/api/sessions`, `/api/session/<id>/events`,
  `/api/tools`, and `/api/tools/<tool>`.
- **Security** — UUID & backup-hash validation, path-traversal protection,
  Content-Security-Policy headers, localhost-only by default.

## Quick start

### Install from source

```bash
git clone https://github.com/l-teles/ai-control-plane.git
cd ai-control-plane
pip install .
```

### Install from PyPI

```bash
pip install ai-control-plane
```

### Run

```bash
# Auto-detect default directories for all sources
ai-control-plane

# Specify directories explicitly
ai-control-plane --copilot-dir ~/.copilot/session-state/ --claude-dir ~/.claude/projects/ --vscode-dir "~/Library/Application Support/Code/User/"

# Or use the module directly
python -m ai_control_plane
```

Then open **http://127.0.0.1:5000** in your browser.

### Default directories

| Source         | macOS / Linux default                                 | Windows default                          | Override flag    | Env variable      |
|----------------|-------------------------------------------------------|------------------------------------------|------------------|--------------------|
| GitHub Copilot | `~/.copilot/session-state/`                           | `%LOCALAPPDATA%\github-copilot\session-state` | `--copilot-dir`  | `COPILOT_LOG_DIR`  |
| Claude Code    | `~/.claude/projects/`                                 | `%LOCALAPPDATA%\claude\projects`         | `--claude-dir`   | `CLAUDE_LOG_DIR`   |
| VS Code Chat   | `~/Library/Application Support/Code/User/` (macOS) / `~/.config/Code/User/` (Linux) | `%APPDATA%\Code\User`  | `--vscode-dir`   | `VSCODE_LOG_DIR`   |

### Options

```
usage: ai-control-plane [-h] [--copilot-dir DIR] [--claude-dir DIR]
                             [--vscode-dir DIR] [-p PORT] [--host HOST]
                          [--debug] [-V] [log_dir]

positional arguments:
  log_dir               Directory containing Copilot session log folders
                        (default: ~/.copilot/session-state/)

options:
  --copilot-dir DIR     Directory containing Copilot session log folders
                        (overrides positional arg)
  --claude-dir DIR      Directory containing Claude Code session logs
                        (default: ~/.claude/projects/)
  --vscode-dir DIR      Directory containing VS Code Chat session logs
                        (default: platform-dependent)
  -p, --port PORT       Port to listen on (default: 5000)
  --host HOST           Host to bind to (default: 127.0.0.1)
  --debug               Run in Flask debug mode (local development only)
  -V, --version         Show version and exit
```

## Expected directory layouts

### GitHub Copilot (`~/.copilot/session-state/`)

```
session-state/
├── 4e71aaa0-f131-41fd-aeee-8bcaa5efb315/
│   ├── workspace.yaml          # Session metadata
│   ├── events.jsonl            # Conversation event stream
│   ├── checkpoints/
│   │   └── index.md
│   └── rewind-snapshots/
│       ├── index.json          # Snapshot manifest
│       └── backups/            # File content snapshots
│           ├── ff627b50b0554488-1773312027139
│           └── ...
├── 8b3c9d7d-60f7-4e4c-a442-eb2ee7ee68e2/
│   └── ...
└── ...
```

### Claude Code (`~/.claude/projects/`)

```
projects/
├── -Users-you-project-alpha/
│   ├── a1b2c3d4-e5f6-7890-abcd-ef1234567890.jsonl
│   ├── d4c3b2a1-f6e5-0987-dcba-0987654321fe.jsonl
│   └── ...
├── -Users-you-project-beta/
│   └── ...
└── ...
```

Each `.jsonl` file is a single session containing user/assistant/system events
with tool calls, thinking blocks, and usage metadata.

### VS Code Chat (`~/Library/Application Support/Code/User/`)

```
User/
├── workspaceStorage/
│   ├── abc123hash/
│   │   ├── workspace.json            # Maps to project folder
│   │   └── chatSessions/
│   │       ├── 88ca1adb-bf72-4478-9982-6886cb99785e.json
│   │       └── ...
│   └── ...
└── globalStorage/
    └── emptyWindowChatSessions/
        ├── 3a9ae123-2cbe-4c1d-b5af-3d4cd1f0ad2e.jsonl
        └── ...
```

Each `.json` file is a single chat session with user messages, assistant responses,
tool call rounds, and timing metadata.

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Lint
ruff check src/ tests/

# Test
pytest
```

## Security

- **Localhost only** — binds to `127.0.0.1` by default; never expose to the public
  internet without authentication.
- **Input validation** — session IDs must be valid UUIDs; backup hashes are validated
  against a strict pattern.
- **Path-traversal protection** — resolved file paths are verified via
  `Path.relative_to()` to stay within the configured log directory.
- **HTML sanitization** — Markdown output is sanitized to strip dangerous tags
  (`<script>`, `<iframe>`, etc.) and event handler attributes.
- **Security headers** — `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, and `Content-Security-Policy` are set on every response.
- **No debug in production** — debug mode is off by default and must be explicitly
  enabled via `--debug`.
- **Dependency monitoring** — Dependabot is configured for automated security updates.

## License

[MIT](LICENSE)
