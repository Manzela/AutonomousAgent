"""Standalone CLI entry point for the anchors pre_tool_call hook.

Invoked by Claude Code's ``PreToolUse`` hook via::

    python -m lib.anchors.hooks pre_tool_call "$TOOL_NAME"

Exit codes:
    0  — allow the tool call (no veto)
    2  — veto the tool call (hook prints the reason to stdout)

Any unhandled exception exits 0 (fail-open) so a broken hook never blocks
Claude Code sessions — especially outside the Hermes container where
dependencies like the spec-store may be unavailable.
"""

from __future__ import annotations

import json
import sys


def main() -> None:
    if len(sys.argv) < 3:
        # Malformed invocation — fail-open.
        sys.exit(0)

    _action = sys.argv[1]  # "pre_tool_call"
    tool_name = sys.argv[2]

    try:
        from lib.anchors import _on_pre_tool_call

        result = _on_pre_tool_call(tool_name=tool_name)
    except Exception as exc:  # noqa: BLE001 — fail-open
        # Outside the container, imports may fail (no hermes, no spec store,
        # no /data volume). Log to stderr and allow the tool call.
        print(f"anchors.hooks: fail-open — {exc}", file=sys.stderr)
        sys.exit(0)

    if result is not None and result.get("veto"):
        # Veto: print reason so Claude Code shows it to the user, exit 2.
        print(json.dumps(result, indent=2))
        sys.exit(2)

    # Allow.
    sys.exit(0)


if __name__ == "__main__":
    main()
