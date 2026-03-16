"""Entry point: ``ai-ctrl-plane`` or ``python -m ai_ctrl_plane``."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import __version__
from .app import create_app
from .claude_parser import _default_claude_dir
from .claude_parser import discover_sessions as claude_discover
from .parser import _default_copilot_dir
from .parser import discover_sessions as copilot_discover
from .vscode_parser import _default_vscode_dir
from .vscode_parser import discover_sessions as vscode_discover


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="ai-ctrl-plane",
        description="Browse AI agent session logs (GitHub Copilot, Claude Code, and VS Code Chat) in a local web UI.",
    )
    parser.add_argument(
        "log_dir",
        nargs="?",
        default=None,
        help=f"Directory containing Copilot session log folders (default: {_default_copilot_dir()})",
    )
    parser.add_argument(
        "--copilot-dir",
        default=None,
        help="Directory containing Copilot session log folders (overrides positional arg)",
    )
    parser.add_argument(
        "--claude-dir",
        default=None,
        help=f"Directory containing Claude Code session logs (default: {_default_claude_dir()})",
    )
    parser.add_argument(
        "--vscode-dir",
        default=None,
        help=f"Directory containing VS Code Chat session logs (default: {_default_vscode_dir()})",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="Directory for the SQLite cache database (default: platform-specific cache dir)",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=5000,
        help="Port to listen on (default: 5000)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Run in Flask debug mode (do NOT use in production)",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    args = parser.parse_args(argv)

    # Resolve Copilot directory
    copilot_dir = args.copilot_dir or args.log_dir
    if copilot_dir is None:
        default = _default_copilot_dir()
        copilot_dir = str(default) if default.is_dir() else "."
    copilot_path = Path(copilot_dir).resolve()

    # Resolve Claude directory
    claude_dir = args.claude_dir
    if claude_dir is None:
        claude_dir = str(_default_claude_dir())
    claude_path = Path(claude_dir).resolve()

    # Resolve VS Code directory
    vscode_dir = args.vscode_dir
    if vscode_dir is None:
        vscode_dir = str(_default_vscode_dir())
    vscode_path = Path(vscode_dir).resolve()

    copilot_sessions = copilot_discover(copilot_path) if copilot_path.is_dir() else []
    claude_sessions = claude_discover(claude_path) if claude_path.is_dir() else []
    vscode_sessions = vscode_discover(vscode_path) if vscode_path.is_dir() else []

    print(f"AI Control Plane v{__version__}")
    print()
    print(f"Copilot:     {copilot_path} ({len(copilot_sessions)} sessions)")
    for s in copilot_sessions[:5]:
        print(f"  - {s['summary']} ({s['id'][:8]}...)")
    if len(copilot_sessions) > 5:
        print(f"  ... and {len(copilot_sessions) - 5} more")
    print()
    print(f"Claude:      {claude_path} ({len(claude_sessions)} sessions)")
    for s in claude_sessions[:5]:
        print(f"  - {s['summary']} ({s['id'][:8]}...)")
    if len(claude_sessions) > 5:
        print(f"  ... and {len(claude_sessions) - 5} more")
    print()
    print(f"VS Code:     {vscode_path} ({len(vscode_sessions)} sessions)")
    for s in vscode_sessions[:5]:
        print(f"  - {s['summary']} ({s['id'][:8]}...)")
    if len(vscode_sessions) > 5:
        print(f"  ... and {len(vscode_sessions) - 5} more")
    print()
    print(f"Open http://{args.host}:{args.port} in your browser")
    print()

    app = create_app(copilot_path, claude_path, vscode_path, cache_dir=args.cache_dir)
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
