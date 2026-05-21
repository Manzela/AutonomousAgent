# Sandbox Tiers — Two Taxonomies, One Boundary

The architecture-research doc and this repo describe sandboxing with
**different vocabularies**. Both are valid; neither is wrong. This note
exists so a future reader doesn't conclude "we need to swap to gVisor" —
the gap is documentary, not architectural.

## TL;DR

- **Research doc** classifies by *isolation technology*: in-process, Docker, gVisor, Firecracker, WASM.
- **This repo** classifies by *use case and trust boundary*: `in_process`, `shell_sandbox`, `browser_sandbox`, `external_https`, `cloud_sandbox` (`config/toolsets.yaml:6-12`).
- Routing is data, not code (`lib/toolset_router.py:44-49`); both taxonomies fit the same boundary contract.

## The repo's five tiers

Defined in `config/toolsets.yaml:6-12` and enforced by `lib/toolset_router.py`. Each tier maps to a distinct threat model:

| Tier | What it runs | Threat model | Implementation today |
|---|---|---|---|
| `in_process` | read-only host I/O (`ls`, `read_file`, `grep`, `rg`) | None — these can only *read* host state | Native Python in agent process |
| `shell_sandbox` | shell, git, jq, generic commands | A trusted-but-fallible operator running rm-rf or leaking env vars | Docker container, `--cap-drop=ALL`, `--network=none`, RO host FS, writable `/workspace` only (`deploy/sandboxes/Dockerfile.shell-sandbox:1-29`, ADR-0003 §Decision) |
| `browser_sandbox` | Playwright actions, screenshots, scrape | Browser-driven exfil; supply-chain via NPM-loaded sites | Playwright container with per-call network allowlist |
| `external_https` | GitHub MCP, Context7 MCP | Outbound exfil to non-allowlisted hosts | In-process `httpx` with egress allowlist (no separate process) |
| `cloud_sandbox` | model-generated code (`run_python`, `exec_code`) | **Adversarial code** — the prompt itself may be malicious | Ephemeral microVM (Modal/Daytona), network-restricted — "Phase 2 onward" per `config/toolsets.yaml:11` |

Unknown tools fall through to `default_tier: shell_sandbox` — **default-deny** (`config/toolsets.yaml:18`).

## How this reconciles with the research doc

| Research-doc tier | Maps to repo tier | Notes |
|---|---|---|
| in-process | `in_process` | Identical |
| Docker | `shell_sandbox`, `browser_sandbox` | Repo splits Docker into two by use case, since the network/FS posture differs |
| gVisor | (none — not implemented) | `cloud_sandbox` provides equivalent isolation via microVM (Modal/Daytona) |
| Firecracker | (none — not implemented) | Same: subsumed by `cloud_sandbox`'s microVM tier |
| WASM | (none — not implemented) | Not needed for current tool mix; would be a fourth option *if* CPU-bound untrusted code becomes common |

**The repo has 5 tiers**, just not the same 5. The research doc's gVisor/Firecracker/WASM all collapse into the use-case-centric `cloud_sandbox` tier, which today is implemented by an external microVM provider — equivalent isolation, different vendor surface.

## When the tech-centric taxonomy would start to matter

The repo's use-case taxonomy is sufficient **as long as `cloud_sandbox` is rare and outsourced.** It would need re-evaluation if any of the following becomes true:

1. **Self-hosted untrusted-code execution.** Today `cloud_sandbox` is Modal/Daytona, who own the microVM kernel boundary. If we bring that boundary in-house, gVisor vs Firecracker vs Kata becomes an actual implementation choice.
2. **High-frequency untrusted code.** Modal/Daytona is cost-prohibitive at sustained QPS. A self-hosted Firecracker pool would be cheaper at scale.
3. **WASM-eligible payloads.** If we ever sandbox pure-CPU, no-I/O computations (e.g., evaluating untrusted regex), WASM is dramatically faster than microVM. Not on the roadmap.

None of these are true today.

## References

- `config/toolsets.yaml:6-12` — tier definitions
- `lib/toolset_router.py:44-49` — first-match-wins resolver
- `deploy/sandboxes/Dockerfile.shell-sandbox` — shell-sandbox container image
- `docs/decisions/0003-tiered-sandboxing-strategy.md` — ADR (consequences + alternatives)
- `audit/2026-05-20-architecture-research-gap-analysis/findings.md` §Component 9 — gap analysis source
