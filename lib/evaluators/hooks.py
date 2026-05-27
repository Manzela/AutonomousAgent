"""Standalone CLI entry point for the evaluators post_tool_call hook.

Invoked by Claude Code's ``PostToolUse`` hook via::

    python -m lib.evaluators.hooks post_tool_call "$TOOL_NAME"

Exit codes:
    0  — always (post_tool_call is observational, never blocks).

The judge dispatch runs in a background thread inside the Hermes container.
Outside the container (local Claude Code sessions), the dispatch will fail
because litellm-proxy isn't reachable — this is expected and logged at DEBUG.
The hook is unconditionally fail-open: it ALWAYS exits 0.
"""

from __future__ import annotations

import sys


def main() -> None:
    if len(sys.argv) < 3:
        # Malformed invocation — exit 0 (observational hook, never blocks).
        sys.exit(0)

    _action = sys.argv[1]  # "post_tool_call"
    tool_name = sys.argv[2]

    try:
        from lib.evaluators import _on_post_tool_call

        # _on_post_tool_call spawns a daemon thread and returns immediately.
        # In local sessions (outside container), the thread will fail to reach
        # litellm-proxy — that's fine, the thread is fail-open internally.
        _on_post_tool_call(tool_name=tool_name)
    except Exception as exc:  # noqa: BLE001 — unconditionally fail-open
        print(f"evaluators.hooks: fail-open — {exc}", file=sys.stderr)

    # Observational hook: always exit 0.
    sys.exit(0)


if __name__ == "__main__":
    main()
