"""Shared utilities for AI tool configuration readers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

# Keys whose values should be masked
_SECRET_KEY_RE = re.compile(
    r"(token|secret|password|apiKey|api_key|authorization|connectionString|credential)",
    re.IGNORECASE,
)

# Values that look like secrets
_SECRET_VALUE_RE = re.compile(
    r"^(Bearer\s+\S{10,}|ghp_\w{10,}|ghu_\w{10,}|ghs_\w{10,}"
    r"|sk-\w{10,}|xoxb-\w{10,}|xoxp-\w{10,}"
    r"|[A-Za-z0-9+/=_-]{40,})$"
)

# URL with embedded credentials: scheme://user:pass@host
_URL_CRED_RE = re.compile(r"(://[^:]+:)[^@]+(@)")


def mask_value(value: str) -> str:
    """Mask a string value, keeping first 4 chars visible."""
    if len(value) <= 4:
        return "****"
    return value[:4] + "****"


def mask_secret(key: str, value: Any) -> Any:
    """Mask a value if the key or value pattern suggests it's sensitive."""
    if not isinstance(value, str):
        return value
    # Check key name
    if _SECRET_KEY_RE.search(key):
        return mask_value(value)
    # Check value pattern
    if _SECRET_VALUE_RE.match(value):
        return mask_value(value)
    # Mask embedded URL credentials
    if "://" in value and "@" in value:
        return _URL_CRED_RE.sub(r"\1****\2", value)
    return value


def _mask_bare_value(value: Any) -> Any:
    """Mask a standalone string if its value looks like a secret."""
    if not isinstance(value, str):
        return value
    if _SECRET_VALUE_RE.match(value):
        return mask_value(value)
    if "://" in value and "@" in value:
        return _URL_CRED_RE.sub(r"\1****\2", value)
    return value


def mask_dict(d: dict | list | Any) -> dict | list | Any:
    """Recursively mask secrets in a dict or list."""
    if isinstance(d, dict):
        return {k: mask_secret(k, mask_dict(v)) for k, v in d.items()}
    if isinstance(d, list):
        return [_mask_bare_value(mask_dict(item)) for item in d]
    return d


_SAFE_URL_SCHEMES = frozenset({"http", "https", "mailto"})


def sanitize_url(url: str) -> str:
    """Return the URL only if it uses a safe scheme, otherwise empty string."""
    if not url or not isinstance(url, str):
        return ""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme in _SAFE_URL_SCHEMES:
        return parsed.geturl()
    return ""


def safe_read_json(path: Path) -> dict | None:
    """Read a JSON file, returning None if missing or invalid."""
    try:
        if not path.is_file():
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


def safe_read_yaml(path: Path) -> dict | None:
    """Read a YAML file, returning None if missing or invalid."""
    try:
        if not path.is_file():
            return None
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return None


def parse_yaml_frontmatter(path: Path) -> dict | None:
    """Extract YAML frontmatter from a markdown file (between --- delimiters).

    Falls back to simple ``key: value`` line parsing when the frontmatter
    contains values that aren't valid YAML (e.g. unquoted colons).
    """
    try:
        if not path.is_file():
            return None
        with open(path, encoding="utf-8") as f:
            content = f.read(50_000)
        if not content.startswith("---"):
            return None
        end = content.find("\n---", 3)
        if end == -1:
            return None
        raw = content[3:end].strip()
        if not raw:
            return None
        try:
            result = yaml.safe_load(raw)
            if isinstance(result, dict):
                return result
        except yaml.YAMLError:
            pass
        # Fallback: simple key: value parsing (first colon splits key/value)
        data: dict = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            colon = line.find(":")
            if colon > 0:
                key = line[:colon].strip()
                val = line[colon + 1 :].strip()
                # Strip surrounding quotes
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                    val = val[1:-1]
                data[key] = val
        return data or None
    except (OSError, UnicodeDecodeError):
        return None


def _extract_skill_body(path: Path) -> str:
    """Extract the markdown body after YAML frontmatter."""
    try:
        if not path.is_file():
            return ""
        with open(path, encoding="utf-8") as f:
            content = f.read(100_000)
        if not content.startswith("---"):
            return content
        end = content.find("\n---", 3)
        if end == -1:
            return content
        return content[end + 4 :].strip()
    except (OSError, UnicodeDecodeError):
        return ""


def read_skills(skills_dir: Path) -> list[dict]:
    """Read SKILL.md files from a skills directory.

    Each skill is a subdirectory containing a ``SKILL.md`` with YAML frontmatter.
    """
    skills: list[dict] = []
    if not skills_dir.is_dir():
        return skills
    for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
        fm = parse_yaml_frontmatter(skill_file) or {}
        # Extract homepage URL from metadata.homepage or top-level homepage
        metadata = fm.get("metadata", {})
        homepage = ""
        author = ""
        version = ""
        if isinstance(metadata, dict):
            homepage = metadata.get("homepage", "")
            author = metadata.get("author", "")
            version = metadata.get("version", "")
        if not homepage:
            homepage = fm.get("homepage", "")
        if not author:
            author = fm.get("author", "")
        skills.append(
            {
                "name": fm.get("name") or skill_file.parent.name,
                "description": fm.get("description", "").strip()[:200],
                "path": str(skill_file.parent),
                "homepage": sanitize_url(str(homepage)) if homepage else "",
                "author": str(author) if author else "",
                "version": str(version) if version else "",
                "license": str(fm.get("license", "")) or "",
                "tools": fm.get("tools", ""),
                "body": _extract_skill_body(skill_file),
            }
        )
    return skills


def safe_read_text(path: Path, max_bytes: int = 50_000) -> str | None:
    """Read a text file with size limit, returning None if missing."""
    try:
        if not path.is_file():
            return None
        with open(path, encoding="utf-8") as f:
            return f.read(max_bytes)
    except (OSError, UnicodeDecodeError):
        return None
