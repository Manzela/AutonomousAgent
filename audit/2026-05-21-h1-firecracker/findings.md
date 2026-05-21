# H1 Firecracker Sandbox — Findings (Pass 1, codebase-only)

**Audit date:** 2026-05-21
**Scope:** What needs to change to move untrusted code execution from outsourced microVM providers (Modal/Daytona) to a self-hosted Firecracker pool on GCP, *and* to bring the gVisor/Firecracker/WASM tier distinctions explicitly into our routing.
**Cross-refs:**
- `docs/architecture/sandbox-tiers.md` — current five-tier taxonomy + the "when this matters" trigger list
- `docs/decisions/0003-tiered-sandboxing-strategy.md` — ADR-0003 (use-case-centric tier decision)
- `config/toolsets.yaml:6-12` — tier definitions (`cloud_sandbox` is the slot Firecracker will fill)
- `lib/toolset_router.py:44-49` — first-match-wins router (already data-driven; no code change to add a tier)
- `audit/2026-05-20-architecture-research-gap-analysis/findings.md` §Component 9 — original gap that surfaced the H1 question

---

## 1. Current state (what's actually in the repo)

### 1.1 Tier routing exists and is data-driven

`lib/toolset_router.py` resolves tier from `config/toolsets.yaml` with first-match-wins glob matching. **No code changes are needed to add a `firecracker_sandbox` tier** — just a new `match:` route. That's the only piece of the H1 plan that's free.

### 1.2 `cloud_sandbox` is the slot Firecracker fills — and it has zero callers today

The current `cloud_sandbox` routes are:

```yaml
- match: ["run_python", "run_javascript", "exec_code", "code_interpreter"]
  tier: cloud_sandbox
  evaluate_after: true
```

Verified that **none of these tool names exist in the codebase yet** (`grep -rln "run_python\|exec_code\|code_interpreter" lib/ hermes-agent/ 2>/dev/null` → empty). The route is defensive — it exists so that if the model emits `run_python`, the router does not silently fall through to `shell_sandbox`. There is no actual code-execution surface live today.

**Implication for H1.** Firecracker scope is greenfield. We are not migrating an existing Modal/Daytona pool — we are deciding what the *first* untrusted-code execution surface looks like. This is dramatically lower-risk than the framing of "lift-and-shift from Modal" suggests, but also means we have to decide what triggers H1 to *activate* (i.e., what brings the first `run_python` call into existence). See §3.

### 1.3 Shell-sandbox already shows the operational shape

`deploy/sandboxes/Dockerfile.shell-sandbox` is the closest existing analog: Docker container, `--cap-drop=ALL`, `--network=none`, RO host FS, writable `/workspace` only. The control loop (build → ship → exec → tear down) is solved for the shell tier. Firecracker re-uses that operator muscle memory but swaps the kernel boundary from Linux namespaces to KVM-virtualized vCPU.

### 1.4 GCP migration is in flight (Phase 0a)

Per `audit/2026-05-20-state-of-repo-v2/` and project memory, Phase 0a moves the substrate from local Docker to GCP (Compute Engine VM, Artifact Registry, IAP). **Firecracker fits naturally into this migration** — GCP Compute Engine N2 instances support nested KVM virtualization (`--enable-nested-virtualization`), which is the prerequisite for running Firecracker microVMs inside a VM. This is confirmed by GCP docs and called out in `gcp-confirm.md`.

## 2. The gap from research doc to this repo

The research doc enumerates **isolation technologies** (in-process, Docker, gVisor, Firecracker, WASM). This repo enumerates **use cases** (`in_process`, `shell_sandbox`, `browser_sandbox`, `external_https`, `cloud_sandbox`). For 4 of 5 tiers the mapping is clean; the gap is that `cloud_sandbox` is a *placeholder* slot where the research doc would want to see an explicit isolation choice.

The two "when this matters" triggers from `sandbox-tiers.md:42-46` are still latent:

1. **Self-hosted untrusted-code execution** — true the moment we run our first `run_python` against agent-generated code in-house (not outsourced).
2. **High-frequency untrusted code** — true once sustained QPS makes Modal cost > self-hosted ops cost (rough breakeven: ~50 invocations/sec with average 30s job duration; below that, outsource).
3. **WASM-eligible payloads** — currently no driver. Not on the H1 plan.

H1 is the materialization of trigger #1 (and a hedge for #2). Trigger #3 stays explicitly out of scope.

## 3. What activates the first Firecracker call (the inception question)

Three plausible candidates seen across the research doc + roadmap:

1. **Phase 3 Governor's policy-eval sandbox** — Governor will evaluate operator-authored policies that may contain arbitrary expressions. If those expressions can call user-defined functions, that's untrusted code → Firecracker.
2. **J3 trajectory shipper's record-transform stage** — if shippers ever apply operator-supplied transformation scripts before upload, those scripts are untrusted operator input → Firecracker. (Today's J3 shipper design does NOT include transforms; this is a "next iteration" risk surface.)
3. **Future tool: `run_python` for the agent itself** — the agent is *currently trusted* (it's a Claude instance under our prompt). But if we ever delegate Python execution to a subagent of an external A2A peer, that subagent is untrusted relative to our process → Firecracker.

Candidate 3 (A2A subagent code execution) is **already on the horizon** per the A2A spike plan (#38). This is the realistic trigger for the first production Firecracker invocation: **the first time we accept a tool call from a non-Anthropic A2A peer and need to execute its code locally.**

## 4. What's missing from the repo today

| Missing artifact | Severity | Where it would live |
|---|---|---|
| Firecracker image build pipeline (`deploy/sandboxes/Dockerfile.firecracker-host` + rootfs builder) | Blocks H1 P1 | `deploy/sandboxes/firecracker/` |
| Jailer config + kernel pinning manifest | Blocks H1 P1 | `deploy/sandboxes/firecracker/jailer/` |
| `firecracker_sandbox` tier in `config/toolsets.yaml` | One-line; trivial | `config/toolsets.yaml` |
| Tier routing tests (`tests/unit/test_toolset_router.py` already covers shape — needs new case) | One-test addition | `tests/unit/test_toolset_router.py` |
| GCP nested-virt enablement on the Phase 0a VM | Blocks live invocation | `terraform/phase-0a-gcp/main.tf` (advanced_machine_features.enable_nested_virtualization) |
| Operator runbook (boot, drain, kill-switch) | Blocks H1 P2 | `audit/2026-05-21-h1-firecracker/runbook.md` (future) |
| ADR-0009 (formal decision to add Firecracker tier) | Should land before code | `docs/decisions/0009-firecracker-sandbox.md` (future) |

## 5. What's already correct and does NOT need re-doing

- The router shape (`lib/toolset_router.py` is fine; adding a tier is config-only).
- The use-case taxonomy. ADR-0003's decision to taxonomize by use case, not by isolation tech, is correct — Firecracker becomes *the implementation* of `cloud_sandbox` (or a new tier alongside), not a replacement for the taxonomy.
- The default-deny posture (`config/toolsets.yaml:18` `default_tier: shell_sandbox` is intentionally tighter than `firecracker_sandbox` — unknown tools should NOT auto-escalate to the heaviest tier).
- The shell-sandbox operational template (the build/run/tear-down loop transfers).

## 6. Open questions surfaced — answered in sibling files

- "Should Firecracker replace `cloud_sandbox` or be a separate tier?" → **Separate tier.** See `architecture.md` §2.
- "Self-managed VM pool or Cloud Run jobs?" → **Self-managed VM pool with a Cloud Run wrapper for short jobs.** See `architecture.md` §3.
- "What's the cost envelope vs Modal?" → **~$120/mo for 10 vCPU pool, breakeven ~50 inv/sec sustained.** See `architecture.md` §6 + `risks-and-open-questions.md` §R3.
- "Does GCP support nested virtualization?" → **Yes, on N2/N2D with `--enable-nested-virtualization`.** See `gcp-confirm.md`.
