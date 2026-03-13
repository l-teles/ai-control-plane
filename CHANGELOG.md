# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and
this project adheres to [Semantic Versioning](https://semver.org/).

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
