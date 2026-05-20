# Phase 0A RCA: Hermes Exit-137 Reproduction

**Status**: NON-REPRODUCTION
**Branch**: `feat/phase-0a-gcp-migration`
**Commit**: `07d2d63`
**Reproduced by**: Claude Opus 4.7 (af4a4612493cfa381)
**Date**: 2026-05-20T12:04:06Z

## Executive Summary

Attempted to reproduce the intermittent hermes exit-137 issue reported in audit findings (PR #98 context, 2026-05-19 observations). **The issue did not reproduce** in the baseline test run on `feat/phase-0a-gcp-migration` at commit `07d2d63`.

## Test Configuration

- **Docker daemon**: Verified running before test
- **Stack state**: Pre-existing containers from 3h prior session were torn down with `down -v` to ensure clean baseline
- **Observation window**: 60 seconds post-`up -d` (as specified in audit-plan.md)
- **Environment**: macOS (Darwin 25.5.0), Docker Desktop

## Results

### Container State at t=60s

```
autonomous-agent-hermes-1   Up About a minute (healthy)
```

**Key findings**:
- **ExitCode**: 0 (still running)
- **OOMKilled**: false
- **Status**: healthy
- **Memory usage**: 122MiB / 7.75GiB (1.54%)
- **StartedAt**: 2026-05-20T12:03:05Z

### Logs Analysis

Hermes started successfully and completed plugin discovery without errors:
- 27 plugins discovered, 24 enabled
- All user plugins loaded: anchors, durability, evaluators, kanban, memory, observability
- Bundled plugins loaded: disk-cleanup, image_gen/*, video_gen/*, web/*, etc.
- Platform adapters: google_chat, irc, line, teams

**Warnings observed** (non-fatal):
- MCP server connection failures: `github` (401 Unauthorized), `context7` (Session terminated)
- Telegram adapter unavailable (python-telegram-bot not installed)
- Gateway continued with cron-only mode (no platform adapters active)

No SIGKILL, no OOM, no plugin guard kills, no crash-related log entries.

## Hypothesis Re-Evaluation

Given the **non-reproduction**, the original hypotheses require re-assessment:

### Hypothesis A: Submodule Regression (hermes a7aa850 → 254056e)
**Status**: Less likely
**Reasoning**: If the regression were deterministic, it should have reproduced in a clean 60s run. The issue may be:
- Non-deterministic (race condition, load-dependent)
- Environment-specific (GCP vs. local Docker Desktop)
- Interaction-dependent (requires specific MCP tool calls or session activity)

### Hypothesis B: tmpfs /tmp Mount (PR #98)
**Status**: Less likely
**Reasoning**: Container is not OOMKilled, memory usage is nominal (122MiB). tmpfs pressure is not evident in this baseline.

### Hypothesis C: disk_cleanup Plugin Guard Kill
**Status**: Most likely conditional trigger
**Reasoning**:
- Plugin loaded successfully
- Registered hooks: `post_tool_call`, `on_session_end`
- No guard kill in this run, but this was a **passive observation** (no active session, no tool calls, no disk writes to trigger cleanup logic)
- Guard kill may only trigger under specific conditions (e.g., large file writes, rapid tmpfs growth, aggressive cleanup thresholds)

## Implications for Audit Plan

1. **Baseline test (Phase A, Task 1)**: COMPLETE with non-reproduction result.
2. **Bisect (Task 2)**: Deprioritize unless the issue is reliably reproducible in another environment.
3. **tmpfs test (Task 3)**: Deprioritize unless memory pressure is observed in production logs.
4. **disk_cleanup test (Task 4)**: **Elevate priority** — requires active session with disk I/O to trigger guard logic.
5. **Soak test (Task 5)**: Remains valuable to detect non-deterministic issues, but may need to run in GCP environment (not local Docker Desktop).

## Recommended Next Steps

1. **Gather production logs** from the GCP environment where exit-137 was observed (if available).
2. **Simulate active session**: Run hermes with actual MCP tool calls, file writes, and session activity to trigger disk_cleanup guard logic.
3. **Review disk_cleanup plugin source**: Inspect cleanup thresholds, guard kill conditions, and tmpfs monitoring logic.
4. **Test in GCP environment**: Reproduce baseline test on the target deployment environment to rule out Docker Desktop vs. GCP differences.

## Artifacts

All diagnostic outputs in `audit/2026-05-20-state-of-the-repo/p0a-rca/`:
- `run0-pre-state.log`: Container states before tear-down (hermes + 4 other services running)
- `run1-baseline-up.log`: Stack startup output
- `run1-baseline-ps.log`: Container states at t=60s
- `run1-baseline-logs.log`: Last 200 lines of hermes logs
- `run1-baseline-stats.log`: Memory/CPU stats snapshot
- `run1-baseline-inspect.json`: Full `docker inspect` output (not committed — pre-commit hook false positives on container IDs/SHAs; available locally for deep-dive analysis)
