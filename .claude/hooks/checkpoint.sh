#!/usr/bin/env bash
# Session checkpoint hook — runs on SubagentStop and Stop events.
# Invokes the durability checkpoint writer so session state is persisted
# even when the agent exits unexpectedly.
set -euo pipefail
SESSION_ID="${CC_SESSION_ID:-unknown}"
python -m lib.durability.checkpoint --session "$SESSION_ID" 2>/dev/null || true
