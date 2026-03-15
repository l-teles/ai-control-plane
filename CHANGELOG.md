# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and
this project adheres to [Semantic Versioning](https://semver.org/).

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
