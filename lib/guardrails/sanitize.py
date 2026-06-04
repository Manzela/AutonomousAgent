"""Output sanitization utilities for XSS, markdown, and command injection defense.

This module provides deterministic sanitizers that are run on untrusted inputs
and outbound agent messages before they are rendered on platforms (Kanban board,
Telegram) or written to execution files, providing defence-in-depth even if
downstream renderers fail to escape raw content.
"""

from __future__ import annotations

import html
import re
from typing import Any, Sequence

# Matches markdown links [label](url) and images ![label](url) with support for one level of nested parentheses
_MARKDOWN_LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(([^()]*(?:\([^()]*\)[^()]*)*)\)")

# Safe schemes for URLs reflected in markdown or links
_SAFE_SCHEMES = ("http://", "https://", "mailto:", "tel:", "/")

# Metacharacters dangerous in shell command execution contexts
_SHELL_METACHEM_RE = re.compile(r"([;`$&|*?~<>^\(\)\[\]\{\}\n\r])")


def sanitize_html(text: str) -> str:
    """Escape HTML metacharacters to prevent XSS (cross-site scripting) attacks.

    Converts characters like <, >, &, ", and ' to their safe HTML entity representations.
    """
    if not text:
        return ""
    return html.escape(text, quote=True)


def sanitize_markdown(text: str) -> str:
    """Sanitize markdown payloads to prevent XSS and malicious URL injection.

    Steps:
      1. Escapes raw HTML tags to prevent XSS in HTML-aware markdown renderers.
      2. Validates link/image URLs, neutralizing javascript: and other unsafe protocols.
    """
    if not text:
        return ""

    # Step 1: Escape HTML tags to prevent tag injection/XSS.
    # Note: We escape HTML character by character using html.escape, but to keep markdown
    # links from being broken, we process the text, neutralizing raw HTML characters.
    # A simple and safe approach is to escape all < and > characters that are not part
    # of valid markdown structures, or simply escape all HTML characters first.
    # Escaping & first, then <, >, ", ' is safe.
    escaped = html.escape(text, quote=False)

    # Step 2: Neutralize unsafe markdown links (e.g. [click](javascript:alert(1)))
    def _replace_link(match: re.Match) -> str:
        is_image = match.group(1)
        label = match.group(2)
        url = match.group(3).strip()

        # Check if URL starts with a safe scheme.
        url_lower = url.lower()
        is_safe = False
        if any(url_lower.startswith(scheme) for scheme in _SAFE_SCHEMES):
            is_safe = True
        elif ":" not in url_lower:  # Relative path or query string without scheme
            is_safe = True

        if not is_safe:
            # Neutralize unsafe URL by replacing with a safe placeholder
            url = "#unsafe-url"

        return f"{is_image}[{label}]({url})"

    return _MARKDOWN_LINK_RE.sub(_replace_link, escaped)


def sanitize_shell_command(command_str: str) -> str:
    """Sanitize a raw shell command string to prevent command injection.

    Escapes dangerous metacharacters (e.g., semicolons, backticks, pipes, etc.)
    with a backslash so they are interpreted literally in shell contexts.
    """
    if not command_str:
        return ""
    # Escape dangerous shell characters by prefixing with backslash
    return _SHELL_METACHEM_RE.sub(r"\\\1", command_str)


def sanitize_command_args(args: Sequence[str]) -> list[str]:
    """Sanitize a list of command arguments.

    Returns a new list of arguments with control characters neutralized.
    """
    return [sanitize_shell_command(arg) for arg in args]


def generate_provenance_metadata(
    output_data: Any, model_version: str = "unknown"
) -> dict[str, Any]:
    """Generate cryptographic provenance metadata for an output payload."""
    import hashlib
    import datetime

    serialized = str(output_data)
    h = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "model_version": model_version,
        "integrity_hash": h,
        "origin": "autonomous-agent-sandbox",
    }
