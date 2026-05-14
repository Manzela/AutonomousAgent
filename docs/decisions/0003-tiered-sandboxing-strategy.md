# 0003. Tiered sandboxing strategy

**Status:** Accepted
**Date:** 2026-05-14
**Decision-makers:** Daniel Manzela

## Context

The agent invokes a wide variety of tools — some safe (file reads), some risky (arbitrary shell), some very risky (model-generated Python execution). A single sandbox tier is either too lax (security risk) or too restrictive (performance and capability cost).

## Decision

We will route tool calls to one of five sandbox tiers based on risk class, defined in `config/toolsets.yaml`:

| Tier | Tools | Boundary |
|---|---|---|
| `in_process` | file reads, grep, ls | runs in agent process; host FS read-only |
| `shell_sandbox` | shell, git, jq | Docker container, `--cap-drop=ALL`, `--network=none`, RO host FS, writable `/workspace` only |
| `browser_sandbox` | Playwright actions | Docker container, network allowlisted per call |
| `external_https` | GitHub MCP, Context7 MCP | in-process httpx with egress allowlist enforcement |
| `cloud_sandbox` | arbitrary code execution | Modal/Daytona ephemeral microVM, network restricted |

First-match wins; unknown tools fall through to `shell_sandbox` (default-deny).

## Consequences

### Positive
- Fast path for safe operations
- Strong isolation for risky operations
- Routing is data, not code — adding new tools requires no code changes
- Per-tier capability limits enforced at container boundary, not in app code

### Negative
- Five tiers to maintain instead of one
- More compose services, more healthchecks, more ops surface
- Cloud sandbox tier requires external accounts (Modal/Daytona) and network egress

### Neutral
- Tier choice is observable in OTel spans (`sandbox.tier=...`)

## Alternatives considered

### Option A: Single Docker sandbox for everything
- Pros: Simpler; one boundary to reason about
- Cons: Slow path for safe reads; over-restrictive for browser; under-restrictive for arbitrary code (no microVM)
- Why rejected: Single tier optimizes neither performance nor security

### Option B: Full cloud sandbox for everything (Modal/Daytona only)
- Pros: Maximum isolation; physical separation
- Cons: Latency; cost; outage of Modal/Daytona blocks all tools including reads
- Why rejected: Cost-prohibitive at the always-on level

## References

- Spec §5.3 (sandboxing detail table)
- `config/toolsets.yaml`
- `lib/toolset_router.py`
