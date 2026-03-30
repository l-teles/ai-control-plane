# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and
this project adheres to [Semantic Versioning](https://semver.org/).

## [v0.6.0]

### Added
- **Global memory files** — reads `~/.claude/memory/*.md` and surfaces content
  on the Claude Code detail page.
- **Remote settings** — reads `~/.claude/remote-settings.json` (remotely-pushed
  permissions: deny/ask rules, env overrides); sensitive values masked.
- **Usage stats** — reads `~/.claude/stats-cache.json`; shows session and
  message counts plus per-model token and cost breakdown.
- **MCP auth cache** — reads `~/.claude/mcp-needs-auth-cache.json`; shows which
  MCP servers require authentication.
- **Plugin marketplace data** — reads `plugins/known_marketplaces.json` and
  `plugins/install-counts-cache.json`; shows registered marketplaces and
  community install counts per plugin.
- **Enterprise managed config — all platforms** — new `_default_managed_dir()`
  helper reads `managed-settings.json` and `managed-mcp.json` from the
  system-wide directory on every platform: macOS
  (`/Library/Application Support/ClaudeCode/`), Linux/WSL (`/etc/claude-code/`),
  and Windows (`%PROGRAMFILES%\ClaudeCode\`). Legacy Windows path
  (`%PROGRAMDATA%\ClaudeCode\`, deprecated since v2.1.75) still read and shown
  with a deprecation notice. Managed MCP servers rendered in a dedicated table.
- **VS Code Insiders** — sessions auto-discovered from `Code - Insiders/User/`
  alongside Stable; config (MCP servers, AI settings, skills, language models)
  exposed under `insiders_*` keys; dedicated Insiders section on the tool detail
  page.

### Fixed
- `plugins/blocklist.json` silently ignored — real file format is
  `{fetchedAt, plugins:[…]}`, not a bare list. Handles both formats; table now
  shows plugin name, reason, and date.

## [0.5.2] - 2026-03-17

### Fixed
- Explicit UTF-8 encoding on all file reads to prevent `UnicodeDecodeError` crash
  on Windows systems using cp1252 as the default encoding.

## [0.5.1] - 2026-03-17

### Fixed
- Windows path fallbacks for Claude Code (`%LOCALAPPDATA%\claude`), GitHub
  Copilot (`%LOCALAPPDATA%\github-copilot`), and Claude Desktop
  (`%APPDATA%\Claude` → MSIX glob fallback) — each prefers the standard
  installer path and falls back to the legacy location when it exists.

## [0.5.0] - 2026-03-17

### Added
- **Claude Desktop as first-class tool** — promoted from a subsection of the
  Claude Code page to its own route, dashboard card, and detail page.
- **Claude Desktop skills** — reads from `local-agent-mode-sessions/skills-plugin/`
  enriched with `enabled`, `creator_type`, `updated_at`, and `skill_id` from the
  manifest index.
- **Cowork plugins** — reads installed plugins and their versioned metadata,
  enabled state, and bundled skills from `local-agent-mode-sessions/<uuid>/cowork_plugins/`.
- **Cowork plugin modal** — each plugin card opens a modal with full details and
  rendered SKILL.md content for each bundled skill.

### Fixed
- "Rebuild Cache" endpoint was not passing `desktop_path`, silently excluding
  Claude Desktop data from manual cache rebuilds.

## [0.4.0] - 2026-03-16

### Added
- **SQLite cache layer** — all data (sessions, tool configs, projects, memory
  files) is stored in a local SQLite database built in a background thread on
  startup, replacing per-request filesystem scans.
- **Claude Projects page** (`/projects`) — lists all Claude Code projects with
  session count, memory files, cost, and token usage; detail page shows rendered
  memory files and linked sessions.
- **Claude Desktop settings** — reads MCP servers, preferences, and UI config
  from Claude Desktop config files (cross-platform) and displays them on the
  Claude tool detail page.
- **Settings page** (`/settings`) — shows cache status, database size, rebuild
  button, and all data directories the app reads from.
- **Dashboard updates** — Projects and Memory Files metric cards; session counts
  on all three tool cards.
- **Loading indicator** — animated stripe bar while the cache is building, with
  auto-polling that hides it when ready.

## [0.3.1] - 2026-03-15

### Changed
- Package renamed from `ai-control-plane` to `ai-ctrl-plane` on PyPI (repository
  name unchanged).

## [0.3.0] - 2026-03-15

### Added
- **"AI Control Plane" rebrand** — new name, dashboard homepage with aggregated
  metrics (MCP servers, plugins, agents, commands, hooks, feature flags, sessions),
  tool cards, session breakdown bars, and recent sessions.
- **Shared base template** — extracted common CSS, navbar, theme toggle, and footer
  into `base.html`; all pages now extend it via Jinja2 template inheritance.
- **Dedicated sessions page** (`/sessions`) — full session list with search and
  source filter buttons, separate from the dashboard.
- **Tool configuration pages** — `/tools` overview and `/tools/<tool>` detail
  pages for Claude Code, GitHub Copilot, and VS Code Chat with vertical sidebar
  navigation and collapsible sections.
- **Slash commands** — reads and displays Claude Code plugin slash commands
  (from `commands/*.md` files) with name, plugin, and description.
- **Feature flags** — now reads GrowthBook cached feature flags in addition to
  top-level boolean flags from `~/.claude.json`.
- **Brand SVG icons** — official Claude, GitHub Copilot, and VS Code icons
  used throughout the UI via Jinja2 macros.
- **Subagent expandable info** — sub-agent start events show the agent prompt
  on expand; sub-agent complete events show the result.
- **Skills page** (`/skills`) — deduplicated list of all installed skills
  across tools, with source badges and clickable filter pills. Skills with
  the same name installed in multiple tools are merged into a single card.
- **Skill detail page** (`/skills/<name>`) — full rendered SKILL.md content
  with metadata sidebar (author, version, license, tools used, homepage).
- **Skills data** — reads SKILL.md files (YAML frontmatter + markdown body)
  for all three tools: Claude Code (standalone + plugin-bundled), GitHub
  Copilot, and VS Code Chat.
- **Agents page** (`/agents`) — aggregated view of agents across all tools
  with clickable source filter pills (Claude / VS Code).
- **SVG favicon** — inline data URI favicon matching the new logo.
- **New logo** — slider/mixing console design with three source-colored bars.
- **GrowthBook flags** — separated from user feature flags in the Claude Code
  tool detail page, collapsed by default with explanation of what GrowthBook is.
- **Improved text wrapping** — `overflow-wrap: break-word` applied consistently
  across tool details, session info, and key-value grids.

### Changed
- Dashboard homepage now shows tool configs and cross-tool metrics instead of
  just a session list.
- All templates refactored to extend `base.html`, removing ~200 lines of
  duplicated CSS.
- Tool detail tabs changed from horizontal to vertical sidebar navigation.
- Policy & Limits section uses single-column layout for readability.
- Feature Flags section uses compact multi-column grid.
- Unicode HTML entities (`&#9654;`, `&#10003;`, etc.) replaced with SVG icons
  throughout session timelines and tool detail pages.

### Fixed
- Sub-agent count always showing 0 — `compute_stats()` now counts Agent and
  dispatch_agent tool calls.
- Sub-agent timeline events now properly emit `subagent_start`/`subagent_complete`
  instead of generic tool events.
- VS Code `globalStorage` path resolution — skills and agents now correctly
  found under the User directory.
- Claude skills now include plugin-bundled skills (from `plugins/marketplaces/`),
  not just standalone `~/.claude/skills/`.
- Agents page navbar highlight — now correctly highlights "Agents" instead of
  "Tools".

## [0.2.0] - 2026-03-13

### Added
- **Claude timeline extras** — hook/progress events, file history snapshots,
  last-prompt markers, permission mode and sidechain indicators on messages.
- **VS Code timeline extras** — agent mode badges (Edit/Chat/Agent),
  follow-up suggestion chips, progress task markers, past-tense tool
  summaries, time-spent-waiting display.
- **Stats sidebar enhancements** — service tier, cache read/creation token
  breakdown, prompt token details (stacked bar), cost estimates with
  multiplier support.
- **Pending-edits warning** on VS Code session cards in the index.

### Fixed

- Double-slash typo in backup API endpoint path.
- Tool name parser for Copilot sessions.

## [0.1.0] - 2026-03-12

### Added

- Initial release.
- Session index page listing all discovered Copilot agent sessions.
- Session detail view with timeline conversation, statistics sidebar, and
  rewind snapshots panel.
- Event type filtering (User, Assistant, Tools, Sub-Agents, Errors).
- Expandable tool call arguments and results.
- Expandable assistant reasoning blocks.
- Markdown rendering for assistant messages.
- JSON API endpoints (`/api/sessions`, `/api/session/<id>/events`,
  `//api/session/<id>/backup/<hash>`).
- CLI entry point (`copilot-log-viewer`) with `--port`, `--host`, and
  `--debug` options.
- Security hardening: UUID validation, backup-hash validation, path-traversal
  protection, Content-Security-Policy, and secure default headers.
