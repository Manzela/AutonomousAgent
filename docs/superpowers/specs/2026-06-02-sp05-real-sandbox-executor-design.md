# SP-05 (slice 1) — real per-node sandbox executor

**Status:** in build (2026-06-02) · **Driver:** P/V · **PRD:** §5.1, §6 SP-05 row, §8 tier rule
**Branch:** `feat/sp05-real-sandbox` off `main` (`bd439acf`)

## Problem (PRD §5.1 honest baseline — verified STILL TRUE on current main)

`app/core/orchestrator.py:295` runs the capability **in-process**:
`result = await asyncio.wait_for(capability.invoke(request), …)`. The
`OrchestratorConfig.sandbox` field (`orchestrator.py:52`) is a **dead brick** —
`AbstractSandbox.run()` has **zero callers** across `app/`/`lib/`. So model/agent
work runs in the harness PID with the full harness env (live secrets) on open
egress — the lethal-trifecta prompt-injection exfil surface SP-05 closes.

## Scope of THIS slice (closes SP-05 acceptance (1), + smallest (3))

Make `AbstractSandbox.run()` **callgraph-reachable from the spine `execute` leaf**,
so the executor runs agent work in a **real subprocess** (different PID) with a
**default-deny scrubbed env** (no `*KEY/*TOKEN/*SECRET`) and **no network**, running
deterministically in CI against `app/adapters/inmemory/LocalSubprocessSandbox`.

**Deferred to later SP-05 slices / SP-05b/c/d (explicitly OUT of this slice):**
sentinel-control (2), workspace manifest + turn-0 (4), per-role tool allowlist (5),
per-phase egress allowlist BUILD=SP-00g (6), full Hermes drive (wire
`lib/hermes_bridge.py`), worktree-per-node (SP-05b), `CloudRunJobSandbox` gVisor
(SP-05c), lifecycle reaper + `/panic` teardown (SP-05d).

## Design (minimal, back-compat, hybrid-adapter-rule-preserving)

- **Do NOT collapse the ABC.** `AbstractSandbox` stays abstract in `app/core/`; the
  concretion stays `LocalSubprocessSandbox` in `app/adapters/inmemory/`. The wiring
  lives in `orchestrator.py` + `graph.py`.
- `orchestrator.execute(request, capability, *, sandbox=None, …)` routing:
  1. `capability.peer_endpoint` set → `_execute_via_a2a` (unchanged).
  2. **`sandbox is not None` → `_execute_sandboxed`** (NEW) — runs
     `request.constraints["sandbox_cmd"]` (a `list[str]`) via
     `sandbox.run(cmd=…, env=_scrubbed_sandbox_env(…), network_allowed=False,
     timeout_s=…)`; maps `returncode == 0 → COMPLETED else FAILED`; surfaces
     stdout/stderr/returncode in `ExecutionResult.output`. **Never calls
     `capability.invoke`.**
  3. else → `_execute_local` (legacy in-process `invoke`; preserved for
     SP-01/SP-04 spine back-compat when no sandbox is injected).
- `_scrubbed_sandbox_env`: **default-deny** — a small allowlist (`PATH/HOME/LANG/
  LC_ALL/TMPDIR/TZ/PWD`) plus optional non-secret `constraints["sandbox_env"]`
  extras; any key matching `KEY|TOKEN|SECRET|PASSWORD|PASSWD|CRED|PRIVATE` is
  dropped (the child cannot inherit harness secrets — `LocalSubprocessSandbox`
  passes `env=` straight through, and `env=None` would leak `os.environ`).
- `app/core/graph.py`: `build_spine(saver, *, capability=None, sandbox=None)` →
  `_build_nodes(capability, sandbox)`; the `execute` leaf passes the injected
  `sandbox` to `orchestrate(req, capability, sandbox=sandbox)` and, when a sandbox
  is present, runs a real subprocess probe (`python -c "print(os.getpid())"`) so the
  skeleton's execute node demonstrably runs in a child PID. Default `sandbox=None`
  keeps the merged spine tests untouched.

## Acceptance · Proof

`audit/acceptance/SP-05.yaml` (sha registered in `SHA256SUMS` via a separate
different-actor commit — C8/C10). Red-green oracles in
`tests/unit/test_sp05_executor_sandbox.py`, all hermetic:

1. `sandbox.run()` awaited exactly once on the execute path (C4 reachability; 0 on main = RED).
2. agent-code `os.getpid()` via `SandboxResult.stdout` ≠ harness PID (no-in-process-exec).
3. a `*KEY/*TOKEN/*SECRET` planted in the harness env is empty in the child (no-secrets).
4. `LocalSubprocessSandbox.run(network_allowed=True)` raises; execute requests `network_allowed=False` (honest isolation).
5. `returncode` 0→COMPLETED, 1→FAILED (consumes the real exit, no fabricated status).
6. sandboxed branch does NOT call `capability.invoke`; `sandbox=None` DOES (back-compat).
7. SP-01/SP-04 spine suites stay green with `sandbox=None` (additive, no regression).

**Reviewer:** different model class (C9) — Gemini 3.1 Pro (executor = Claude Opus).
