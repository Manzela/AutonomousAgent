# Ground-Truth Audit — Remediation Plan

**Date:** 2026-05-27
**Status:** APPROVAL-GATE PENDING (per `/audit` skill Pass 3)
**Branch:** `fix/p2-self-correction-pass` (HEAD `1585ee0`)
**Authoritative inputs (read-order):**
1. `audit/2026-05-27-ground-truth/decisions.md` — user-confirmed decisions; **overrides anything below that conflicts**
2. `audit/2026-05-27-ground-truth/findings.md` — re-verified findings (self-corrected on 2026-05-27 to drop 2 false-positive P0s)
3. This file — sequenced remediation plan + handoff packet
4. `CLAUDE.md` (project) + `~/.claude/CLAUDE.md` (global) — must remain in effect verbatim

**Handoff target:** Antigravity Claude Opus 4.6 Thinking (per user's side-comment in decisions message).

---

## Scope summary

| Wave | Time-box | Parallelism | Deliverables |
|---|---|---|---|
| **W0 — Crisis pass** | ≤48h wall-clock | Sequential (one PR per step, each ≤300 lines) | Close **all 8 active P0s** (delivered as 7 PRs because W0.4 combines P0-5 + P0-6 into a single adapter-stub PR). The exit criterion is "the running binary stops lying about its safety guarantees and stops shipping unsigned code to prod." |
| **W1 — Tier-1 hardening** | ~12–18 eng-days | 10 parallel work-streams (A–J + WIF migration sub-item) | Close all 51 P1s (C×7, S×3, SC×6, I×7, O×9, T×5, A×4, SB×4, CC×6). Plus stand up multi-vendor LLM router (D-2.J). Plus WIF migration (D-4.I-8). |
| **W2 — Polish** | 3 sprints (~6 weeks) | Backlog-style | Close P2/P3 finding list. |

**Explicit non-scope (per D-5):** no edits to `hermes-agent/` submodule; no edits to `docs/`. Anti-hallucination exclusion mechanism for those paths is documented in decisions.md D-5.b — **mechanism choice (a / b / c) is pending user confirmation** and is the only gating item between this plan and Antigravity execution.

---

## W0 — Crisis pass (7 PRs, ~48h, sequential)

Each W0 PR is independently mergeable. They are ordered by **dependency**, not severity — every W0 PR after W0.1 either depends on a prior or is cleanly orthogonal (the audience-contract PR is orthogonal to judges but conflicts with peers.yaml editing windows, so it lands second). PR titles use conventional commits.

### W0.1 — Real judge consensus (closes P0-2)

- **PR title:** `fix(evaluators): replace stub verdict with 4-judge LiteLLM panel + 5th-judge tiebreak`
- **Why first:** the entire safety contract of the agent rests on this loop. Every subsequent fix is dead weight if the agent can still take any tool call unchallenged.
- **Files touched (anticipated):**
  - `lib/evaluators/__init__.py` — replace stub at `_on_post_tool_call:36–48` with real dispatch
  - `lib/evaluators/judge_panel.py` — NEW; per-axis prompt templates + LiteLLM call + consensus computation (75% threshold per `config/limits.yaml:153-156`)
  - `lib/evaluators/orchestrator_hook.py` — wire `queue_judge_dispatch` to actually invoke the panel from a background thread (eligibility lookup via `toolset_router.is_evaluation_eligible()`)
  - `lib/evaluators/tests/test_judge_consensus.py` — NEW; integration test: deliberately bad tool call → assert `verdict == "reject"`, axes_failed populated, feedback queued
  - `lib/evaluators/tests/test_5th_judge_tiebreak.py` — NEW; 2-accept / 2-reject → 5th judge dispatched → final verdict
- **Definition of done:**
  - `_on_post_tool_call` no longer returns `None` unconditionally; dispatches a real panel for evaluation-eligible toolsets.
  - At least one integration test in CI proves a real LiteLLM call produces a rejection with axes annotated.
  - `lib/evaluators/judge_events.record_consensus_event` is invoked from the panel (closes P1.B S-2 free).
  - Cost-cap: each panel call charged against the per-tier daily cap (see W0.7 + W1.I).
- **Risk:** false-rejection rate from LLM judges. Mitigation: start with `evaluate_after` allow-list of 3 toolsets (Edit, Write, Bash) before broadening.

### W0.2 — A2A audience contract (closes P0-4)

- **PR title:** `fix(a2a): peers.yaml audience = receiver SA email (option A); reject URL-formed audience at startup`
- **Decision basis:** `decisions.md` D-1 (option A confirmed).
- **Files touched:**
  - `config/a2a/peers.yaml:25` — replace `audience: https://agent-canary.example.test` with `audience: agent-canary@autonomous-agent-2026.iam.gserviceaccount.com`
  - `lib/a2a/auth.py` — **no code change required** (already verifies `aud == our_sa`); add a startup-time validator
  - `lib/a2a/audience_validator.py` — NEW; iterates `peers.yaml`, asserts each `audience` contains `@`, raises `RuntimeError` in `ENVIRONMENT=production`
  - `lib/a2a/server.py` — call `audience_validator.validate_all_peers()` during FastAPI startup event
  - `lib/a2a/tests/test_audience_contract.py` — NEW; per-peer green path (SA-email accepted) + red path (URL form rejected with explicit error message)
- **Definition of done:**
  - Every entry in `peers.yaml` validates at startup.
  - Production startup with a URL-form audience aborts with a clear operator message.
  - End-to-end auth handshake (`hermes → canary → hermes`) passes the integration test.
- **Coordination:** before merge, update Antigravity's deploy runbook to call out the breaking change for any operator who hand-edited `peers.yaml`.

### W0.3 — REJECTED-inject revival (closes P0-3 with corrected root cause)

- **PR title:** `fix(durability): revive REJECTED.md inject by removing dead ctx-only branch`
- **Decision basis:** `findings.md` P0-3 (self-corrected). The original "rename the call" patch is wrong — the function signature was always right; the calling branch is unreachable.
- **Files touched:**
  - `lib/durability/__init__.py:200–290` — replace the `ctx`-keyed dead branch with a `session_id`-keyed path that does NOT require a Hermes `ctx` surface; resolve `intent_category` from the active TaskSpec snapshot via `lib/anchors/__init__.py:138–162` (TaskSpec is materialized by `on_session_start` per Hermes contract)
  - `lib/durability/tests/test_rejected_inject_e2e.py` — NEW; previously-rejected approach surfaces as a `role:system` message at next session start; assert via `lib/evaluators/orchestrator_hook` queue inspection
  - `lib/memory/rejected.py:232` — no change (the function is correct)
- **Definition of done:**
  - `_rej.load_active_entries(intent_category=resolved, max_entries=DEFAULT_MAX_INJECT)` actually runs on a fresh session start.
  - The entries are formatted (reuse `lib/evaluators/orchestrator_hook.format_feedback_message`) and prepended to the next-turn prompt.
  - End-to-end test: reject approach X → start new session → assert X appears in the system message.
- **Coordination:** this PR depends on W0.1 conceptually (REJECTED entries are written by judges that don't exist yet) — but the inject path can be tested independently against pre-seeded fixture entries, so it merges in parallel-after-W0.1.

### W0.4 — Sandbox + Embedder GCP adapters land as fail-closed stubs (closes P0-5, P0-6)

- **PR title:** `feat(adapters/gcp): FirecrackerSandbox + VertexEmbeddingsEmbedder fail-closed stubs + production-grade gate`
- **Decision basis:** `decisions.md` D-3 (Firecracker stub approved).
- **Files touched:**
  - `app/adapters/gcp/sandbox.py` — NEW; `FirecrackerSandbox(AbstractSandbox)` with `is_production_grade=True` and `__init__` raising `NotImplementedError("H1: Firecracker tier not yet provisioned — file issue per docs/architecture/h1-firecracker-provision.md")`. Production startup with this stub aborts cleanly with operator-actionable error.
  - `app/adapters/gcp/embedder.py` — NEW; `VertexEmbeddingsEmbedder(AbstractEmbedder)` — real implementation using `aiplatform.gapic.PredictionServiceClient` against `text-embedding-005` on `autonomous-agent-2026` project. 256-dim output to match phase2 spec (project memory `phase2_postgres_tier.md`). Includes retry+backoff + per-call latency span.
  - `app/core/orchestrator.py` — NEW gate: refuse to start when `sandbox.is_production_grade=False` AND `ENVIRONMENT=production`. Log a startup banner naming the active sandbox + embedder adapter class.
  - `app/adapters/inmemory/sandbox.py:21` — keep `is_production_grade=False` flag (already correct).
  - `app/adapters/inmemory/tests/test_orchestrator_gate.py` — NEW; assert `OrchestratorConfig.validate()` rejects inmemory sandbox in production.
  - `app/adapters/gcp/tests/test_vertex_embedder.py` — NEW; mocked Vertex call asserts 256-dim output + retry semantics.
- **Definition of done:**
  - `from app.adapters.gcp import sandbox, embedder` succeeds.
  - Production startup with the Firecracker stub aborts with the documented error string and exit code.
  - CI deploys green against `adapters/inmemory/`; staging deploys green against `adapters/gcp/` (embedder real, sandbox raises until H1 lands).
  - `docs/architecture/h1-firecracker-provision.md` referenced from the error message (DOES NOT need to exist as a doc edit — the error string can name the path; the doc is owned by the deferred H1 ticket).

### W0.5 — Cosign image-signing + verify-attestation gate (closes P0-7)

- **PR title:** `fix(supply-chain): cosign sign + verify image (not just SBOM blob); SLSA L2 provenance + GHCR push`
- **Files touched:**
  - `.github/workflows/sbom-cosign.yml:47–74` — pin to `sigstore/cosign-installer@<sha256-pin>` (cosign v3.0.6 minimum per https://github.com/sigstore/cosign/releases/tag/v3.0.6, released 2026-04-09) + add `docker push` of image to GHCR + `cosign sign --yes $IMAGE_DIGEST` (keyless OIDC) + `cosign attest --yes --predicate sbom.spdx.json --type spdxjson $IMAGE_DIGEST`. **v3.x breaking changes:** `--type spdx` was renamed to `--type spdxjson`; `--yes` is required to disable interactive confirmation prompts in CI; use the digest (`$IMAGE@sha256:...`), not the tag, to prevent mutation.
  - `.github/workflows/phase-0a-deploy.yml:146` (hermes build-push) **AND** `:162` (shell-sandbox build-push) — add `provenance: mode=max` and `sbom: true` to both `docker/build-push-action@v6` `with:` blocks (per https://docs.docker.com/build/ci/github-actions/attestations/). This emits SLSA L2 provenance + SBOM as image attestations. The previous audit cite of `:152-154` pointed at the `tags:` block; the actual buildx invocations are the two `uses: docker/build-push-action@...` steps at `:146` and `:162`.
  - `.github/workflows/phase-0a-deploy.yml:271–272` — insert a `cosign verify-attestation` gate **before** `docker compose pull`. Pin the certificate-identity to the repo's GitHub Actions OIDC subject. Fail-closed.
  - `deploy/docker-compose.yml` — change all `image:` lines from tag-only to digest-pinned (closes P1.SC-3 free).
  - `.github/workflows/sbom-cosign.yml` — change trigger from `tags: ['v*']` only to also include `main` pushes (closes P1.SC-6 free; every deployed `main` SHA gets an SBOM).
- **Definition of done:**
  - `cosign verify $IMAGE_DIGEST --certificate-identity-regexp '^https://github.com/Manzela/AutonomousAgent/' --certificate-oidc-issuer https://token.actions.githubusercontent.com` returns exit 0 and prints `Verified OK` against a freshly-pushed image.
  - `cosign verify-attestation --type spdxjson --certificate-identity-regexp '^https://github.com/Manzela/AutonomousAgent/' --certificate-oidc-issuer https://token.actions.githubusercontent.com $IMAGE_DIGEST` returns the SBOM bound to the image (exit 0).
  - `slsa-verifier verify-image $IMAGE_DIGEST --source-uri github.com/Manzela/AutonomousAgent --source-tag <tag>` (slsa-verifier v2.7.1 per https://github.com/slsa-framework/slsa-verifier/releases/tag/v2.7.1) returns exit 0.
  - The deploy job's verify step blocks promotion if the image is unsigned (fail-CLOSED, never `|| true`).
  - SLSA Build Level 2 achievable on the next release; L3 path documented (requires hermetic builder — tracked as P1.SC-4 separately).
- **Risk:** GHCR push needs `packages: write` permission on the workflow. Mitigation: scope to the workflow, not org-wide.

### W0.6 — Per-service SA keys + drop host gcloud bind (closes P0-8)

- **PR title:** `fix(deploy): per-service SOPS-encrypted SA keys; remove host gcloud bind-mount from all containers`
- **Decision basis:** `decisions.md` D-4 (per-service SA keys in W0; WIF migration in W1.D.I-8).
- **Files touched:**
  - `secrets/sa-keys/litellm-proxy.json.sops` — NEW; minimum-scope SA (`roles/aiplatform.user` only)
  - `secrets/sa-keys/cloud-sql-proxy.json.sops` — NEW; minimum-scope SA (`roles/cloudsql.client` only)
  - `secrets/sa-keys/snapshot-watchdog.json.sops` — NEW; minimum-scope SA (`roles/storage.objectAdmin` scoped to the snapshot bucket only)
  - `terraform/phase-0a-gcp/sa-keys.tf` — NEW; defines the 3 SAs + IAM bindings + key rotation policy (90d)
  - `deploy/docker-compose.yml:138, :519, :606` — replace `${HOME}/.config/gcloud:/...:ro` with `./secrets/sa-keys/<service>.json:/secrets/sa-key.json:ro` and update `GOOGLE_APPLICATION_CREDENTIALS` to `/secrets/sa-key.json` in each container's `environment:`
  - `deploy/docker-compose.yml` — add a startup check (entrypoint shim) that asserts `/secrets/sa-key.json` exists and is non-empty; else exit 1
  - `scripts/rotate-sa-keys.sh` — NEW; SOPS re-encrypt + service restart helper
- **Definition of done:**
  - `grep -r "/.config/gcloud" deploy/` returns zero matches.
  - Each container has exactly one SOPS-decrypted SA key bound at `/secrets/sa-key.json` via a per-service ro mount.
  - The keys live SOPS-encrypted at rest with age recipients (per CLAUDE.md security constraints).
  - Smoke test: pulling the host gcloud config off the box does NOT compromise the containers' ability to call GCP.
- **W1.D.I-8 deferral:** WIF migration replaces all of this with metadata-server identity tokens; tracked separately.

### W0.7 — Multi-vendor LLM tier router (closes P0-9, **supersedes D-2.c W0.8**)

- **PR title:** `feat(router): multi-vendor tier matrix per task_intent; default tier = orchestrator (Gemini 3.1 Pro)`
- **Decision basis:** `decisions.md` D-2 (full table). Per D-2.c, W0 wires only the three Vertex tiers; DeepSeek + Qwen are stubbed and lit up in W1.J.
- **Files touched:**
  - `config/hermes/cli-config.yaml:22` — change default from `vertex_ai/claude-opus-4-7` to `vertex_ai/gemini-3-1-pro-preview` (orchestrator tier default). Inline comment cites decisions.md D-2.
  - `config/hermes/model-tiers.yaml` — NEW; full tier matrix:
    ```yaml
    # Source: audit/2026-05-27-ground-truth/decisions.md D-2.a
    # Quirks reference: ~/.claude/.../memory/gemini_3_1_pro_preview_quirks.md
    tiers:
      orchestrator:
        model: vertex_ai/gemini-3-1-pro-preview
        endpoint: global
        thinking_budget: high
        max_tokens: 8192
        daily_cost_cap_usd: 200
      architect:
        model: vertex_ai/claude-opus-4-7
        max_tokens: 8192
        daily_cost_cap_usd: 150
      fast-engineer:
        model: vertex_ai/gemini-3-5-flash
        max_tokens: 4096
        daily_cost_cap_usd: 50
      researcher:
        model: vertex_ai/gemini-3-1-pro-preview
        endpoint: global
        max_tokens: 16384
        daily_cost_cap_usd: 100
      deep-math:
        # CORRECTED 2026-05-27: LiteLLM canonical id is `deepseek/deepseek-reasoner`
        # (DeepSeek R1 reasoning model). The audit-plan draft incorrectly listed
        # `deepseek-r1` which is not a valid LiteLLM provider/model identifier.
        # Reference: https://docs.litellm.ai/docs/providers/deepseek (verified 2026-05-27)
        model: deepseek/deepseek-reasoner
        api_base: https://api.deepseek.com
        provider: deepseek
        status: stub-until-w1j
        fallback_tier: orchestrator
      privacy:
        # CORRECTED 2026-05-27 (second pass): Qwen3.5 IS released (Feb-Mar 2026,
        # post-prior-cutoff); the prior "never released" claim was stale training
        # data. Selected default = Qwen3.5-35B-A3B (Instruct MoE), the newer
        # generation at same single-A100 footprint as the originally-considered
        # Qwen3-30B-A3B-Instruct-2507. 36B total / 3B active params; 256 experts
        # (8 routed + 1 shared); hybrid Gated DeltaNet+Attention+FFN; 256K context.
        # vLLM ≥0.21.0 supports it. LiteLLM prefix `hosted_vllm/` (NOT `openai/`
        # or the deprecated `vllm/`). Reference:
        # https://huggingface.co/Qwen/Qwen3.5-35B-A3B (verified 2026-05-27)
        # https://huggingface.co/collections/Qwen/qwen35
        # https://docs.litellm.ai/docs/providers/vllm
        #
        # ALTERNATIVES (commented; operator may un-comment + comment out the
        # default block if their workload warrants):
        #   (alt-a) hosted_vllm/Qwen/Qwen3-Coder-30B-A3B-Instruct
        #     Older-gen Qwen3 BUT purpose-built for agentic coding; same single-
        #     A100 footprint; `qwen3_coder` tool-call format. Pick if coder
        #     specialization > generational newness for the workload.
        #   (alt-c) hosted_vllm/Qwen/Qwen3.5-27B
        #     Dense 28B; strongest published code benchmarks (SWE-Bench Verified
        #     72.4, LiveCodeBench v6 80.7); REQUIRES 8× A100 80GB tensor-parallel
        #     (`a2-ultragpu-8g`); ~10× cost (~$20K/mo on-demand). Re-escalate to
        #     human before flipping — exceeds default budget envelope.
        model: hosted_vllm/Qwen/Qwen3.5-35B-A3B
        api_base: http://qwen-vllm.autonomous-agent-2026.svc.cluster.local:8000/v1
        provider: self-host
        status: stub-until-w1j
        fallback_tier: orchestrator
    routing:
      fallback_tier: orchestrator
      require_explicit_intent: true   # missing/invalid intent → fail CLOSED to orchestrator (not silent default to Opus)
      total_daily_cap_usd: 500
    ```
  - `lib/router/intent_router.py` — NEW; `resolve_model(task_intent: str) -> ModelSpec`. Reads `model-tiers.yaml`. Stubbed tiers raise `NotImplementedError("W1.J pending — see audit/2026-05-27-ground-truth/decisions.md D-2.c")`.
  - `lib/router/tests/test_intent_routing.py` — NEW; covers each tier mapping + the "missing intent fails closed to orchestrator" contract + the "deep-math/privacy raise until W1.J" contract.
  - `lib/hermes_bridge.py` (or wherever the CLI invocation site is) — every Hermes invocation declares `task_intent ∈ {orchestrator, architect, fast-engineer, researcher, deep-math, privacy}`. The router selects model + cost-cap tag.
  - `deploy/litellm/config.yaml` — add per-tier `tags:` + `max_budget:` per the table above. Wire to LiteLLM's per-tag budget enforcement.
- **Definition of done:**
  - Every Hermes invocation routes through `intent_router.resolve_model`.
  - Default invocation (no explicit intent) routes to orchestrator (Gemini 3.1 Pro), NOT Opus.
  - Per-tier daily caps enforced at the LiteLLM proxy.
  - `pytest lib/router/` green; CI fails if `model-tiers.yaml` is edited without updating the router schema.
- **Quirks (per `gemini_3_1_pro_preview_quirks.md`):**
  - Model ID has `-preview` suffix (not `gemini-3-1-pro`).
  - Only available on `global` endpoint (not regional).
  - Thinking model requires `max_tokens ≥ 2048`; we use 8192 to give headroom.

---

## W0 sequencing summary

```
W0.1 (judges)    ── blocks W0.3 conceptually (but tests independently)
W0.2 (audience)  ── orthogonal to W0.1; conflicts only with peers.yaml editing
W0.3 (rejected)  ── independent of W0.4-W0.7
W0.4 (adapters)  ── independent
W0.5 (cosign)    ── independent
W0.6 (SA keys)   ── independent (terraform apply needed before merge)
W0.7 (router)    ── independent
```

Net: a single coordinator can land W0.1 → W0.2 → W0.3 → W0.4 → W0.5 → W0.6 → W0.7 sequentially in ~36h. **PR size budget: ≤300 lines/PR (each is independently reviewable and revertible).**

---

## W1 — Tier-1 hardening (~12–18 eng-days, 10 parallel work-streams)

W1 takes the codebase from "doesn't lie about itself" to "operates 24/7 unattended at Tier-1 SRE standards." Each work-stream is independently ownable.

### W1.A — Concurrency / async hygiene

Closes P1.A (7 items): sync-in-async fixes, httpx pooling, retry jitter, DSN-keyed pool singleton, distributed task registry, chunked-encoding body-size cap. Per-item:
- **C-1** (`lib/a2a/auth.py:389-404` + `lib/a2a/agent_card.py:75`): wrap `credentials.refresh(Request())` in `asyncio.to_thread`.
- **C-2** (`lib/a2a/auth.py:296` vs `:300`): pick fail-OPEN OR fail-CLOSED, align docstring with code. Recommendation: fail-CLOSED in prod (current code default).
- **C-3** (`lib/a2a/client.py:271,309,341`): hoist `httpx.AsyncClient(http2=True, limits=...)` to module-level singleton with explicit shutdown hook.
- **C-4** (`lib/a2a/client.py:116-131`): add jitter `delay += random.uniform(0, delay * 0.2)`.
- **C-5** (`app/adapters/gcp/memory.py:65-100`): key `_get_pool()` singleton by DSN.
- **C-6** (`lib/a2a/server.py:84` `_TASK_REGISTRY` **AND** `lib/a2a/auth.py:73` `_JTI_L1_FALLBACK`): both are `cachetools.TTLCache` (good — bounded) but **process-local**. Multi-replica → ghost tasks (a `tasks/get` on replica B for a task created on replica A returns 404) and JTI replay window divergence. The auth-layer JTI cache is already a fallback layer for the distributed Redis JTI store (see PR #152) — the gap is on the server-layer task registry. Use Cloud SQL or Redis when scaling beyond single replica. Document the trade-off; default stays in-process for single-replica deploys.
- **C-7** (`lib/a2a/server.py:376-384`): count bytes from ASGI `receive()` instead of `Content-Length` header to defeat chunked-encoding bypass.

### W1.B — Safety machinery follow-up

Closes P1.B (3 items, now mostly tail-end of W0.1/W0.3):
- **S-1**: covered by W0.3 above.
- **S-2**: covered by W0.1 above (judge_events.record_consensus_event wired).
- **S-3**: validated by W0.1 — drain path was already correct; with real judges it becomes a real signal.

### W1.C — Supply-chain hardening (beyond W0.5)

- **SC-1**: switch CI from `uv pip install -e ".[dev]"` to `uv sync --frozen --extra dev --extra gcp --extra a2a`.
- **SC-2**: change Dependabot cadence in `.github/dependabot.yml:21,41,65,78` from `monthly` to `weekly` (OpenSSF Scorecard baseline).
- **SC-3**: covered by W0.5 (digest-pinning).
- **SC-4**: replace `curl -LsSf https://astral.sh/uv/install.sh | sh` with `pip install uv==X.Y.Z` + SHA-256 verify; OR mirror the binary.
- **SC-5**: add `.github/workflows/scorecard.yml` using `ossf/scorecard-action@v2` on weekly schedule.
- **SC-6**: covered by W0.5 (trigger on `main` pushes).

### W1.D — Container / secrets / infra (beyond W0.6)

Closes the residual P1.D items + **introduces W1.D.I-8: WIF migration** (per `decisions.md` D-4).

- **W1.D.I-8 (NEW, per D-4)**: deploy hermes + sidecars to Cloud Run (or GKE Workload Identity), drop SA keys entirely, use metadata-server identity tokens. Tracked as a sub-deliverable; ~3 eng-days.
  - Deliverables: terraform module for Cloud Run service + WIF pool; service deploys with no `GOOGLE_APPLICATION_CREDENTIALS` env var; old SOPS SA-key files removed from git (W0.6 kept them as a stepping-stone).
- Plus the remaining P1.D items per findings.md (container hardening, secret rotation, drop CAP_NET_BIND).

### W1.E — Observability + CI smoke gates

Closes P1.E + **adds W1.E-new: A2A import smoke gate**.

- **W1.E-new (NEW, ex-W0.1 from original 8-PR sequencing)**: add `python -c "import lib.a2a.server"` + `python -c "import lib.evaluators"` + `python -c "import lib.durability"` to CI as a pre-test gate. Catches the kind of NameError that P0-1 was mis-claimed to be (defensive hygiene; not a deploy-blocker, but a cheap safety net).
- Plus remaining P1.E items: structured logging, trace propagation through judges, GenAI span labels.

### W1.F — Test coverage gaps

Closes P1.F: integration tests for `tasks/get`, `tasks/cancel`, JTI replay across replicas, REJECTED-inject e2e (already covered by W0.3), judge-consensus stability (already covered by W0.1).

### W1.G — App-layer abstract-adapter contract (beyond W0.4)

Closes P1.G: all six AbstractEmbedder/AbstractSandbox/AbstractMemoryStore/AbstractMoERouter/Judge/AbstractIntrinsicRewardModel surfaces have green `inmemory` + `gcp` adapters; CI runs both adapter suites; staging runs `gcp` adapters by default. Per project memory `seed_orchestrator_research_2026-05-21.md`, **do not collapse the abstract base classes** under any circumstance.

### W1.H — Sandbox hardening (beyond W0.4)

Closes the sandbox-specific P1 items (process/fs/network/rlimit/seccomp layer enforcement in `LocalSubprocessSandbox`; production-grade flag enforcement at boot — done by W0.4 + this).

### W1.I — Cost machinery + budget watchdog

Closes P1.I (now including the reinstated **CC-6** F21 sentinel gap — see findings.md R-1 retraction). The previous draft of this plan claimed "the read-side is already live per `lib/anchors/__init__.py:196`" — that claim was a hallucination. Direct verification (`grep -n F21 lib/anchors/__init__.py` returns zero matches; `:196` is `return None` inside `_on_pre_tool_call`) confirms **no anchor reads any filesystem HALT_F21 sentinel**. The F21 dispatcher in `lib/durability/handlers.py:119-187` writes a Telegram alert and snapshots the failure-card to BLOCKED, but writes no filesystem sentinel; no module polls `/data/HALT_F21`.

W1.I deliverables:
- Per-tier daily cost-cap accounting (W0.7 hooks the per-tag budgets at the proxy).
- **CC-6 decision required** — pick ONE of:
  - **(α) Cardinal sufficient:** treat the BLOCKED card + Telegram alert as the authoritative halt signal; remove all HALT_F21 references from docs/code; document the human-operator runbook for the alert.
  - **(β) Add filesystem sentinel:** `halt_alert_snapshot` writes `/data/HALT_F21` (chmod 600) **AND** `lib/anchors/__init__.py` `_on_pre_tool_call` checks the sentinel and short-circuits tool execution with an actionable error.
  - The CC-6 entry in findings.md P1.I prefers (β) for defence-in-depth; the W1.I PR body must call out which path was taken and why.
- Cost-cascade lint that flags any new tier introduced without a daily cap (CI check in `.github/workflows/lint-cost-tiers.yml`).
- Reconciliation across the three caps (`deploy/litellm/config.yaml:65` $100/day proxy, `config/limits.yaml:2` $500/day runtime, `terraform/phase-0a-gcp/billing.tf:14` ~$250/day GCP) — see findings.md CC-2.

### W1.J — Provision DeepSeek R1 + (optional) Qwen3.5-35B-A3B (Instruct MoE) [NEW per D-2.c]

Per `decisions.md` D-2.c, this is a **new work-stream not present in the original 8-PR plan**.

- **DeepSeek R1 provisioning:**
  - `secrets/deepseek.env.sops` — NEW; SOPS-encrypted API key
  - `deploy/litellm/config.yaml` — add DeepSeek as a LiteLLM provider with explicit cost-tag and per-tier budget
  - `lib/router/intent_router.py` — un-stub the `deep-math` tier; remove the `NotImplementedError`; add provider-failover fallback to `orchestrator` on 5xx
  - `lib/router/tests/test_deepseek_routing.py` — NEW; mocked DeepSeek response → assert routing + cost accounting
  - Observability: per-provider latency + error-rate dashboard (Grafana or BigQuery view); per-provider cost cap alert at 80% of daily limit
- **Qwen3.5-35B-A3B Instruct MoE (optional, OFF by default until vLLM cluster provisioned; supersedes the originally-considered Qwen3-30B-A3B-Instruct-2507 — see decisions.md D-2.a):**
  - `infra/qwen-vllm/terraform/` — NEW; single-A100-80GB GCP `a2-ultragpu-1g` GPU module (~$400/mo spot, ~$2.6K/mo on-demand)
  - `deploy/litellm/config.yaml` — Qwen provider entry (`hosted_vllm/Qwen/Qwen3.5-35B-A3B`); `enabled: false` by default
  - `config/hermes/model-tiers.yaml` — privacy tier default = `hosted_vllm/Qwen/Qwen3.5-35B-A3B`; **stub two commented alternative blocks** with cost + benchmark notes for operator override:
    - `(alt-a) hosted_vllm/Qwen/Qwen3-Coder-30B-A3B-Instruct` — older-gen Qwen3, purpose-built for agentic coding, same single-A100 footprint, `qwen3_coder` tool-call format
    - `(alt-c) hosted_vllm/Qwen/Qwen3.5-27B` — dense, strongest published code benchmarks (SWE-Bench 72.4, LiveCodeBench v6 80.7), **requires 8× A100 80GB tensor-parallel** (`a2-ultragpu-8g`, ~10× cost)
  - `lib/router/intent_router.py` — un-stub the `privacy` tier; gated by `config/feature-flags.yaml: qwen_self_host: false`
- **Definition of done:**
  - `pytest lib/router/test_{deepseek,qwen}_routing.py` green
  - A task tagged `task_intent: deep-math` actually hits DeepSeek (verified by integration test that asserts the provider tag in the LiteLLM response metadata)
  - Daily cost report shows DeepSeek as a separate line item
  - Qwen stays OFF until the cluster is provisioned (do not enable in W1)
- **Effort estimate:** ~3 eng-days for DeepSeek, ~3-5 eng-days for Qwen vLLM provision. Qwen can slip to W2 if pressed.

---

## W2 — P2/P3 polish (3 sprints)

Closes the 27 P2 + 11 P3 items in findings.md. Backlog-managed via GitHub issues. Not blocking on anything.

---

## Anti-hallucination mechanism for `docs/` and `hermes-agent/` (D-5.b — RESOLVED 2026-05-27)

**Status: APPLIED.** User selected option (b). `.claudeignore` written at repo root with `docs/**` + `hermes-agent/**` patterns. Both paths remain on disk, in git, and in deploys (hermes-agent/ is a runtime-active submodule); only the Claude Code / Antigravity Claude context loader skips them. See `decisions.md` D-5.b for full rationale.

Original options considered (kept for audit traceability):

| Option | What it does | Side-effects |
|---|---|---|
| **(a) `.dockerignore` only** | Excludes `docs/` + `hermes-agent/` from deploy images. | Does NOT solve LLM-context pollution; only solves "shipping docs to prod containers." Useless for the stated goal. |
| **(b) `.claudeignore` / `settings.json` contextExclude** | Tells Claude Code (and Antigravity's Claude session) to skip `docs/` + `hermes-agent/` when loading codebase context. | Solves LLM hallucination from stale docs. Repo history preserved. Does nothing for human readers. **Recommended for the audit handoff.** |
| **(c) Physical move to `archive/`** | `git mv docs/ archive/docs/` etc. Top-level `docs/` becomes a 1-line "STALE — see archive/" pointer. | Most aggressive. **DO NOT apply to `hermes-agent/` — it is a runtime-active submodule imported by the orchestrator; physical move would break the agent.** Safe for `docs/`. |

**Recommendation:** `(b)` for both `docs/` and `hermes-agent/`, with optional `(c)` for `docs/` only if the user wants maximum context hygiene at the cost of operator-doc churn. **Antigravity should NOT begin code execution until the user confirms which option to apply.**

**Concrete patch for option (b)** (ready to apply once confirmed):
```jsonc
// .claude/settings.json — add to contextExclude
{
  "contextExclude": [
    "docs/**",
    "hermes-agent/**",
    "audit/**/findings.md.bak"  // keep audit/ visible but not editor swapfiles
  ]
}
```

---

## Handoff packet definition (for Antigravity Claude Opus 4.6 Thinking)

### Read-order (must be ingested in this order before any code change)

1. `~/.claude/CLAUDE.md` (global) — sets working style, GitHub via `gh`, no force-push to main
2. `CLAUDE.md` (project) — GCP migration MUST be respected; use `autonomous-agent-2026`, never `i-for-ai`
3. `~/.claude/projects/<this-project>/memory/MEMORY.md` — index of project-state memories
4. `audit/2026-05-27-ground-truth/decisions.md` — **user-confirmed decisions; overrides everything below**
5. `audit/2026-05-27-ground-truth/findings.md` — re-verified findings (self-corrected on 2026-05-27)
6. `audit/2026-05-27-ground-truth/audit-plan.md` — this file
7. `docs/research/autonomous-agent-seed-orchestrator/04-gcp-native-adapter-plan.md` — REQUIRED reading before any work on `app/` adapters (per project CLAUDE.md)

### What Antigravity is authorized to do without further user confirmation

- Land W0.1 through W0.7 in the sequenced order described above.
- Each W0 PR must be ≤300 lines and pass all CI checks before merge.
- Each PR opens with a body that cites: the finding ID (e.g. `P0-2`), the file:line targeted, and the verification test that proves the fix.
- Squash-merge to main (per repo workflow). Conventional commit titles. Never `--no-verify`. Never `git push --force` to main.
- Stage files by name, never `git add -A` (per security constraints).

### What Antigravity MUST escalate (do not execute without explicit human OK)

1. **Anti-hallucination mechanism for D-5.b** — choose option (a/b/c) — gating ALL of W0.
2. **W0.6 terraform apply** — `terraform/phase-0a-gcp/sa-keys.tf` requires a real GCP IAM grant in `autonomous-agent-2026`; needs human-in-loop for the apply step.
3. **W0.5 GHCR push** — needs the GitHub org's `packages: write` permission; first push must be observed by a human.
4. **W1.D.I-8 WIF migration** — Cloud Run deploy is a substantial infra change; defer until W0 is complete and stable.
5. **W1.J Qwen provisioning** — GPU spend for `Qwen/Qwen3.5-35B-A3B` (default privacy tier): ~$400/mo single-A100 spot, ~$2.6K/mo on-demand; needs explicit user OK before terraform apply. If operator flips the `(alt-c) Qwen3.5-27B` dense alternative in `model-tiers.yaml`, cost jumps to ~$20K/mo on-demand (8× A100 80GB) — re-escalate before flipping.

### Decision-log requirement

For every PR Antigravity opens, the body MUST include a "Decisions consulted" section listing which entries in `decisions.md` were applied. This forces grounded execution and prevents silent drift.

---

## Open clarifications (before Antigravity starts)

| # | Question | Default if no answer |
|---|---|---|
| Q1 | D-5.b mechanism (a / b / c)? | (b) — context-exclusion for both `docs/` and `hermes-agent/` |
| Q2 | Should W1.J include Qwen vLLM provisioning, or defer to W2? | Defer to W2 (Qwen OFF by default); DeepSeek lights up in W1 |
| Q3 | Is the existing `secrets/` SOPS recipient list already updated for the new per-service SA keys? | If not, Antigravity must add a step W0.6.0 to rotate age recipients before encrypting the SA keys |
| Q4 | Branch naming for the W0 PRs — keep `fix/p2-self-correction-pass` chain, or rebase onto a fresh `fix/audit-2026-05-27-w0/<step>`? | Fresh per-step branches; cleaner squash history |

---

## "Are we ready to hand off?" — honest assessment (UPDATED 2026-05-27)

**Yes — packet is complete.** D-5.b resolved (option (b) applied via `.claudeignore`). Remaining items are non-blocking for W0.1 start:

1. ~~**D-5.b mechanism choice**~~ — **RESOLVED.** `.claudeignore` in place.
2. **MEMORY.md hygiene** (soft) — entries dated 2026-05-17 / 2026-05-19 / 2026-05-20 are stale on the cli-config default-model field (still reference "Opus default"). After W0.7 lands, MEMORY.md must be updated in the same commit to point to the new multi-vendor tier. **Not blocking W0.1; will block W0.7 close-out.**
3. **Open clarifications Q2–Q4** (soft) — defaults provided. Antigravity should apply the defaults and call them out in the W0.1 PR body for ratification, not block on them.

**Antigravity Claude Opus 4.6 Thinking is cleared to begin W0.1 (judge consensus) on user "proceed" signal.**

---

## Verification Appendix — Antigravity self-verification protocol (MANDATORY)

This appendix is the operational contract Antigravity (Claude Opus 4.6 Thinking) must execute for every wave. **The audit's authority skill is `superpowers:verification-before-completion`** ("Iron Law: NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE"). Antigravity MUST treat every "Definition of done" item above as an *intent*; the commands below are the *evidence* that intent was met.

### Verification grammar (applies to every item, W0/W1/W2)

For each PR, Antigravity must produce — **in the PR body, copy-pasted from real terminal output** — evidence for all 5 layers:

1. **Pre-condition gate** — the broken state must be reproducible *before* the fix. (Proves the audit's diagnosis was correct, not a hallucination.)
2. **Implementation gate** — local checks during PR construction (lint, typecheck, unit tests against the new code path).
3. **Post-condition gate** — the broken state is gone *after* the fix. (Proves the fix landed.)
4. **Regression test (TDD red-green)** — a new test must fail before the fix, pass after, fail again when the fix is reverted, pass again when restored. (Proves the test actually catches the regression, not just the symptom.)
5. **Smoke test** — end-to-end probe against the running binary (local `docker compose` for app-layer; against `agent-canary` for A2A; against `--dry-run` for terraform).

**On any failure:** STOP. Do NOT mark the PR ready. Open a follow-up finding back into `audit/2026-05-27-ground-truth/findings.md` under the `## Iterative discoveries (post-handoff)` heading and tag the user.

**Forbidden shortcuts (per Iron Law):**
- "Should pass" / "looks correct" / "previous run was green" — **always re-run in the PR head's CI**
- `pytest -x -k '<name>'` alone — **always run the full file plus the affected integration suite**
- `|| true` in any gate command — **fail-CLOSED only**
- Trusting `gh pr checks --watch` as the sole signal — **read the actual workflow log**

---

### W0.1 — Real judge consensus (P0-2)

| Layer | Command | Expected evidence |
|---|---|---|
| **Pre-condition** | `pytest lib/evaluators/tests/test_judge_consensus.py::test_bad_tool_call_is_rejected -x 2>&1 \| tee /tmp/pre.log; grep -c FAILED /tmp/pre.log` | `1` (test must FAIL because stub returns `None` for every call). |
| **Implementation** | `pytest lib/evaluators/ -x --tb=short` AND `mypy lib/evaluators/` | Exit 0 on both. Coverage on `judge_panel.py` ≥85%. |
| **Post-condition** | `pytest lib/evaluators/tests/test_judge_consensus.py -x 2>&1 \| tee /tmp/post.log; grep -c "passed" /tmp/post.log` | `1` AND `grep -c FAILED /tmp/post.log` returns `0`. |
| **Regression (TDD red-green)** | 1. `pytest lib/evaluators/tests/test_5th_judge_tiebreak.py::test_2_2_split_escalates -x` → PASS. 2. `git stash` the panel code; re-run → FAIL. 3. `git stash pop` → re-run → PASS. | The 3-step transcript pasted into PR body. |
| **Smoke (local)** | `docker compose -f deploy/docker-compose.yml up -d hermes litellm-proxy && docker compose exec hermes python -c "import asyncio; from lib.evaluators import judge_panel; print(asyncio.run(judge_panel.evaluate({'tool':'Bash','args':{'command':'rm -rf /'}})))"` | Output contains `'verdict': 'reject'` and `'axes_failed': [...]`. |
| **Cost guard** | `grep -A2 daily_cost_cap_usd config/hermes/model-tiers.yaml \| head -20` | Each tier has a numeric cap; no `null`. |

**Acceptance test for P0-2:** `_on_post_tool_call` for an Edit/Write/Bash invocation must hit the real LiteLLM panel and persist a `consensus_event` row. Verify with `sqlite3 ./data/judges.db "SELECT count(*) FROM consensus_events WHERE created_at > datetime('now','-1 minute')"` returns `>0` after the smoke test.

---

### W0.2 — A2A audience contract (P0-4)

| Layer | Command | Expected evidence |
|---|---|---|
| **Pre-condition** | `grep -n 'audience:' config/a2a/peers.yaml` | At least one entry with a `https://` URL (the bug). |
| **Implementation** | `pytest lib/a2a/tests/test_audience_contract.py -v` | All cases pass, including `test_url_form_audience_rejected_in_production`. |
| **Post-condition** | `grep -nE 'audience:\s*[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}' config/a2a/peers.yaml \| wc -l` AND `grep -cE 'audience:\s*https?://' config/a2a/peers.yaml` | First returns N (one per peer); second returns `0`. |
| **Regression** | 1. Re-introduce a URL audience in `peers.yaml`. 2. `ENVIRONMENT=production python -c "from lib.a2a.audience_validator import validate_all_peers; validate_all_peers()"` → expect `RuntimeError`. 3. Revert. 4. Re-run → expect exit 0. | 4-step transcript in PR body. |
| **Smoke (E2E)** | Bring up canary + hermes locally (`docker compose up -d agent-canary hermes`), then `docker compose exec hermes curl -sS -X POST http://agent-canary:8081/.well-known/agent.json -H "Authorization: Bearer $(cat /secrets/canary-token)"` | HTTP 200 with `agent.json` body; no `401 invalid_aud` in logs. |
| **Startup banner** | `docker compose logs hermes \| grep -i audience` | A startup line like `audience_validator: validated 1 peer(s), all email-form`. |

---

### W0.3 — REJECTED-inject revival (P0-3)

| Layer | Command | Expected evidence |
|---|---|---|
| **Pre-condition** | `pytest lib/durability/tests/test_rejected_inject_e2e.py::test_previously_rejected_surfaces_in_next_session -x` | FAIL (because `_inject_rejected_entries` early-returns on `ctx is None`). |
| **Implementation** | `pytest lib/durability/ lib/memory/ -x --tb=short` AND `mypy lib/durability lib/memory` | Exit 0 on both. |
| **Post-condition** | `pytest lib/durability/tests/test_rejected_inject_e2e.py -x` AND `python -c "from lib.durability import _resolve_intent_category_from_taskspec; print('ok')"` | Test passes; import succeeds. |
| **Regression** | 1. `pytest lib/durability/tests/test_rejected_inject_e2e.py -x` → PASS. 2. In `lib/durability/__init__.py`, restore the `if ctx is None: return` early-return at line ~200. 3. Re-run → FAIL. 4. Revert. 5. Re-run → PASS. | 5-step transcript. |
| **Smoke (E2E)** | Local Hermes session: (a) call Bash with a known-bad command, (b) wait for judge dispatch (W0.1), (c) restart Hermes, (d) verify the next session's system-message contains the rejected approach by inspecting `data/checkpoints/step_0.json`. | `step_0.json` contains the rejected-approach text under a `system` role. |
| **Integration with W0.1** | `pytest lib/evaluators/tests/test_rejected_persistence.py -x` (NEW test) | PASS — judge rejection writes a row; durability injects it on next session. |

---

### W0.4 — Sandbox + Embedder GCP adapters fail-closed stubs (P0-5, P0-6)

| Layer | Command | Expected evidence |
|---|---|---|
| **Pre-condition** | `python -c "from app.adapters.gcp import sandbox" 2>&1; echo "exit=$?"` | `exit=1` with `ModuleNotFoundError` (because the file does not exist). |
| **Implementation** | `python -c "from app.adapters.gcp import sandbox, embedder; print(sandbox.__file__, embedder.__file__)"` AND `pytest app/adapters/gcp/tests/test_vertex_embedder.py app/adapters/inmemory/tests/test_orchestrator_gate.py -x` | Imports print real paths; tests exit 0. |
| **Post-condition** | `ENVIRONMENT=production python -c "from app.adapters.gcp.sandbox import FirecrackerSandbox; FirecrackerSandbox()" 2>&1 \| grep -c 'NotImplementedError.*H1'` | `1` (raises the documented operator error). |
| **Regression** | 1. `pytest app/adapters/inmemory/tests/test_orchestrator_gate.py::test_rejects_inmemory_in_production -x` → PASS. 2. In `app/core/orchestrator.py`, remove the `is_production_grade` gate. 3. Re-run → FAIL. 4. Revert. 5. Re-run → PASS. | 5-step transcript. |
| **Smoke (Vertex live)** | `ENVIRONMENT=staging python -c "import asyncio; from app.adapters.gcp.embedder import VertexEmbeddingsEmbedder; e = VertexEmbeddingsEmbedder(); v = asyncio.run(e.embed('hello world')); assert len(v) == 256, f'wrong dim: {len(v)}'; print('ok')"` | Prints `ok` (256-dim embedding from `text-embedding-005` on `autonomous-agent-2026`). |
| **Production gate** | `ENVIRONMENT=production python app/core/orchestrator.py --validate-startup 2>&1 \| grep -c 'firecracker.*not yet provisioned'` | `1`. |

---

### W0.5 — Cosign image signing + verify-attestation (P0-7)

| Layer | Command | Expected evidence |
|---|---|---|
| **Pre-condition** | `gh workflow view sbom-cosign.yml --yaml \| grep -E 'cosign sign[^\-]'` AND `gh workflow view phase-0a-deploy.yml --yaml \| grep -E 'cosign verify'` | First grep returns `cosign sign-blob` only (no `cosign sign <IMAGE>`); second returns nothing (no verify gate). |
| **Implementation** | `gh workflow run sbom-cosign.yml --ref <feature-branch>` then `gh run watch` | Exit 0; workflow log includes `Verified OK` from `cosign verify` self-check. |
| **Post-condition** | `IMG=$(gh api repos/Manzela/AutonomousAgent/packages/container/hermes/versions --jq '.[0].metadata.container.tags[0]'); cosign verify ghcr.io/manzela/autonomousagent/hermes@sha256:<digest> --certificate-identity-regexp '^https://github.com/Manzela/AutonomousAgent/' --certificate-oidc-issuer https://token.actions.githubusercontent.com` | Prints `Verified OK`. |
| **SBOM attestation** | `cosign verify-attestation --type spdxjson --certificate-identity-regexp '^https://github.com/Manzela/AutonomousAgent/' --certificate-oidc-issuer https://token.actions.githubusercontent.com ghcr.io/manzela/autonomousagent/hermes@sha256:<digest> \| jq -r '.payload \| @base64d \| fromjson \| .predicate.name'` | Prints `hermes` (SBOM bound to the image). |
| **SLSA provenance** | `slsa-verifier verify-image ghcr.io/manzela/autonomousagent/hermes@sha256:<digest> --source-uri github.com/Manzela/AutonomousAgent --source-tag <tag>` | Exit 0; prints `Verifying image ... PASSED: SLSA verification passed`. |
| **Regression (fail-CLOSED)** | 1. Push an unsigned image manually: `docker tag hello-world:latest ghcr.io/manzela/autonomousagent/hermes:rogue && docker push ghcr.io/manzela/autonomousagent/hermes:rogue`. 2. Edit `deploy/docker-compose.yml` to use `:rogue`. 3. Trigger deploy workflow → expect FAIL at the cosign-verify step. 4. Revert + delete rogue tag. | Workflow log shows `cosign verify ... error: no matching signatures`. |
| **Tool version pins** | `gh workflow view sbom-cosign.yml --yaml \| grep -E 'sigstore/cosign-installer@[a-f0-9]{40}'` | Match — SHA-pinned, not `@v3` tag-only. Cosign installer ≥v3.0.6. |

---

### W0.6 — Per-service SA keys + drop host gcloud bind (P0-8)

| Layer | Command | Expected evidence |
|---|---|---|
| **Pre-condition** | `grep -rn '\.config/gcloud' deploy/docker-compose.yml` | 3 matches (litellm-proxy, cloud-sql-proxy, snapshot-watchdog). |
| **Implementation** | `bash scripts/decrypt-sops.sh secrets/sa-keys/litellm-proxy.json.sops && terraform -chdir=terraform/phase-0a-gcp plan -target=google_service_account.litellm_proxy` | Decrypt succeeds (age key resolves); terraform plan shows the new SA + IAM binding. |
| **Post-condition** | `grep -c '\.config/gcloud' deploy/docker-compose.yml` AND `grep -c '/secrets/sa-key.json' deploy/docker-compose.yml` | `0` and `≥3` respectively. |
| **Startup entrypoint check** | `docker compose up -d litellm-proxy && sleep 2 && docker compose logs litellm-proxy \| grep -c 'sa-key.*not found or empty'` | `0` (key was bound; entrypoint passed). |
| **Negative startup** | 1. `mv secrets/sa-keys/litellm-proxy.json.sops /tmp/`. 2. `docker compose up litellm-proxy` → expect exit code != 0 within 5s. 3. Restore. | Container fails fast; logs show the entrypoint assertion. |
| **Smoke (GCP call)** | `docker compose exec litellm-proxy python -c "from google.cloud import aiplatform; aiplatform.init(project='autonomous-agent-2026'); print(list(aiplatform.Model.list(filter='display_name=text-embedding-005'))[:1])"` | Returns a model reference (Vertex call succeeded with the per-service key). |
| **Host-gcloud isolation** | 1. `chmod 000 ~/.config/gcloud`. 2. Re-run the smoke. 3. `chmod 700 ~/.config/gcloud`. | Smoke still succeeds (the container does NOT depend on host gcloud). |
| **Permission scope audit** | `gcloud projects get-iam-policy autonomous-agent-2026 --flatten='bindings[].members' --format='table(bindings.role,bindings.members)' --filter='bindings.members:litellm-proxy@autonomous-agent-2026.iam.gserviceaccount.com'` | Single role: `roles/aiplatform.user`. NO `roles/owner`, NO `roles/editor`. |

---

### W0.7 — Multi-vendor LLM tier router (P0-9, supersedes D-2.c W0.8)

| Layer | Command | Expected evidence |
|---|---|---|
| **Pre-condition** | `grep -E '^\s*default:\s*' config/hermes/cli-config.yaml` AND `test -f config/hermes/model-tiers.yaml; echo $?` | First prints `default: "vertex_ai/claude-opus-4-7"`; second prints `1` (file does not yet exist). |
| **Implementation** | `pytest lib/router/ -v` AND `python -c "import yaml; m = yaml.safe_load(open('config/hermes/model-tiers.yaml')); assert set(m['tiers'].keys()) == {'orchestrator','architect','fast-engineer','researcher','deep-math','privacy'}, m['tiers'].keys()"` | All router tests pass; tier-set assertion passes. |
| **Post-condition** | `grep '^\s*default:\s*' config/hermes/cli-config.yaml` AND `python -c "from lib.router.intent_router import resolve_model; print(resolve_model('orchestrator').model)"` | First prints `vertex_ai/gemini-3-1-pro-preview`; second prints same. |
| **Fail-CLOSED contract** | `python -c "from lib.router.intent_router import resolve_model; print(resolve_model(None).model); print(resolve_model('').model); print(resolve_model('bogus').model)"` | All three print `vertex_ai/gemini-3-1-pro-preview` (NOT `claude-opus-4-7`). |
| **Stubbed tier contract** | `python -c "from lib.router.intent_router import resolve_model; resolve_model('deep-math')" 2>&1 \| grep -c 'W1.J pending'` | `1` (raises documented `NotImplementedError`). |
| **Regression** | 1. `pytest lib/router/tests/test_intent_routing.py::test_missing_intent_fails_closed_to_orchestrator -x` → PASS. 2. In `intent_router.py`, change the fallback to return `architect`. 3. Re-run → FAIL. 4. Revert. 5. Re-run → PASS. | 5-step transcript. |
| **Smoke (LiteLLM proxy budget tags)** | `curl -sS http://localhost:4000/budget/info -H "Authorization: Bearer $LITELLM_MASTER_KEY" \| jq -r '.tags[] \| select(.tag == "orchestrator") \| .max_budget'` | `200` (matches `daily_cost_cap_usd` in YAML). |
| **Quirks check (Gemini 3.1 Pro)** | `python -c "import yaml; m = yaml.safe_load(open('config/hermes/model-tiers.yaml')); o = m['tiers']['orchestrator']; assert o['endpoint'] == 'global' and o['max_tokens'] >= 2048 and 'preview' in o['model'], o"` | Exit 0 (per `gemini_3_1_pro_preview_quirks.md`). |
| **MEMORY.md hygiene gate** | `grep -l 'Opus default' ~/.claude/projects/-Users-danielmanzela-RX-Research-Project-AutonomousAgent/memory/*.md` | Empty (all stale Opus-default mentions updated to multi-vendor tier in the same PR). |

---

### W0 — cross-PR gate (must pass before W0 is closed)

| Check | Command | Expected |
|---|---|---|
| **All 7 W0 PRs merged** | `gh pr list --state merged --search 'audit-2026-05-27-w0' --json number,title,mergedAt --jq '.[] \| select(.mergedAt != null) \| .number' \| wc -l` | `7` |
| **All findings.md P0s closed** | `grep -nE '^### P0-[0-9]' audit/2026-05-27-ground-truth/findings.md \| wc -l` AND `git log --oneline main..HEAD \| grep -cE 'closes? P0-[0-9]'` | first `7`; second `≥7` |
| **No new P0 introduced** | `pytest -x` against `main` HEAD (full suite, not subset) | Exit 0 |
| **No `i-for-ai` in W0 code** | `git diff main...HEAD -- ':!docs/' ':!*.md' \| grep -c 'i-for-ai'` | `0` |
| **Pre-commit hooks not bypassed** | `git log --pretty=full main..HEAD \| grep -c 'no-verify'` | `0` |
| **All commits signed** | `git log --pretty='%G?' main..HEAD \| grep -cE '^[GU]$'` AND `git log --pretty='%G?' main..HEAD \| wc -l` | Equal (all `G` or `U`, no `N`/`E`). |
| **Branch protection enforced** | `gh api repos/Manzela/AutonomousAgent/branches/main/protection --jq '.required_status_checks.contexts'` | Includes `ci`, `mypy`, `pre-commit`, and the new `cosign-verify` gate from W0.5. |

---

### W1.A — Concurrency / async hygiene (7 items C-1..C-7)

| Sub | Command | Expected |
|---|---|---|
| **C-1 (sync-in-async)** | Pre: `pytest lib/a2a/tests/test_async_credentials.py::test_refresh_does_not_block_event_loop` → expect timeout under load. Post: same test → exit 0. Regression: revert the `asyncio.to_thread` wrap → test FAILs. | Latency p99 under 50ms with 100 concurrent refreshes. |
| **C-2 (fail-open/closed docstring align)** | `grep -nE 'A2A_JTI_FAIL_MODE' lib/a2a/auth.py` then `grep -nE 'fail-(open\|closed)' lib/a2a/auth.py` | Code default and docstring agree. |
| **C-3 (httpx singleton)** | `pytest lib/a2a/tests/test_httpx_pooling.py::test_single_client_per_module -x` AND `python -c "from lib.a2a.client import _client; from lib.a2a.client import _client as c2; assert _client is c2"` | Both pass. |
| **C-4 (jitter)** | `pytest lib/a2a/tests/test_retry_jitter.py::test_no_thundering_herd -x` (100 simulated replicas; assert delay std-dev > 0). | Pass. |
| **C-5 (DSN-keyed pool)** | `pytest app/adapters/gcp/tests/test_memory_pool_isolation.py::test_two_dsns_two_pools -x` | Pass. |
| **C-6 (distributed registry)** | `pytest lib/a2a/tests/test_task_registry_redis.py::test_cross_replica_get -x` (uses fakeredis + real Redis if `REDIS_URL` set). | Pass. |
| **C-7 (chunked body cap)** | `pytest lib/a2a/tests/test_body_size_cap.py::test_chunked_encoding_cannot_bypass -x` | Pass. |
| **Regression for all** | For each C-*, `git revert` the relevant commit on a scratch branch → re-run its test → expect FAIL. | 7 red-green transcripts in the W1.A PR body. |

---

### W1.B — Safety machinery follow-up

| Sub | Command | Expected |
|---|---|---|
| **S-1** | Covered by W0.3 cross-check: `pytest lib/durability/tests/test_rejected_inject_e2e.py -x` | Pass. |
| **S-2** | `pytest lib/evaluators/tests/test_judge_events_recorded.py::test_consensus_event_persisted -x` AND `sqlite3 ./data/judges.db "SELECT count(*) FROM consensus_events"` | Test pass; row count > 0 after a real judge dispatch. |
| **S-3** | `pytest lib/evaluators/tests/test_drain_with_real_judges.py -x` | Pass (drain delivers `reject` verdicts with `axes_failed`). |

---

### W1.C — Supply-chain hardening

| Sub | Command | Expected |
|---|---|---|
| **SC-1 (uv sync --frozen)** | `grep -c 'uv pip install -e' .github/workflows/ci.yml` AND `grep -c 'uv sync --frozen' .github/workflows/ci.yml` | `0` then `≥1` |
| **SC-2 (dependabot weekly)** | `grep -A1 -E 'interval:' .github/dependabot.yml \| grep -c 'weekly'` AND `grep -c 'monthly' .github/dependabot.yml` | `≥4`; `0` |
| **SC-3** | Covered by W0.5 digest-pinning. Re-verify: `grep -nE 'image:.*@sha256:' deploy/docker-compose.yml \| wc -l` | Equals total image-line count from `grep -c '^\s*image:' deploy/docker-compose.yml`. |
| **SC-4 (uv install hermetic)** | `grep -c 'curl.*astral.sh/uv' deploy/Dockerfile.hermes` AND `grep -cE 'pip install uv==[0-9]' deploy/Dockerfile.hermes` | `0` then `≥1`. |
| **SC-5 (Scorecard workflow)** | `test -f .github/workflows/scorecard.yml; echo $?` AND `gh workflow run scorecard.yml; sleep 60; gh run list --workflow=scorecard.yml --limit 1 --json conclusion --jq '.[0].conclusion'` | `0` then `success`. |
| **SC-6** | Covered by W0.5 trigger expansion. Verify: `gh api repos/Manzela/AutonomousAgent/actions/workflows/sbom-cosign.yml --jq '.path' && gh workflow view sbom-cosign.yml --yaml \| grep -A3 ^on:` | Both `tags:` and `branches: [main]` present. |
| **Scorecard score baseline** | After first run: `gh run list --workflow=scorecard.yml --limit 1 --json url --jq '.[0].url'` → open and read the score. | ≥7.0/10 (OpenSSF Scorecard 2025 baseline for production projects). |

---

### W1.D — Container / secrets / infra (incl. W1.D.I-8 WIF migration)

| Sub | Command | Expected |
|---|---|---|
| **I-1 (sops siblings)** | `find secrets/ -name '*.env' -not -name '*.sops*' \| wc -l` | `0` |
| **I-2 (canary hardening)** | `grep -E '(user:\|cap_drop:\|read_only:\|no-new-privileges)' deploy/docker-compose.yml \| grep -c canary` | `≥4` |
| **I-3 (migration atomic)** | `pytest scripts/tests/test_migrate_cloud_sql_atomic.py::test_partial_failure_rolls_back -x` | Pass. |
| **I-4 (litellm hardening)** | `docker compose config \| yq '.services.litellm-proxy.cap_drop'` | `[ALL]`. |
| **I-5 (egress allowlist)** | `terraform -chdir=terraform/phase-0a-gcp plan \| grep -A5 'allow_egress' \| grep -c '0\.0\.0\.0/0'` | `0` after the change. |
| **I-6 (Memorystore AUTH)** | `terraform -chdir=terraform/phase-0a-gcp/memorystore plan \| grep auth_enabled` | `true`. |
| **I-7 (HNSW in prod)** | `grep -n 'CREATE INDEX.*USING hnsw' scripts/migrate_cloud_sql.py` AND `psql "$DSN" -c "\\d+ memory_records" \| grep -c hnsw` (after running migration in staging) | First `≥1`; second `≥1`. |
| **W1.D.I-8 (WIF migration)** | Use `google-github-actions/auth@v3` (NOT v2 — v3 was released Sept 2025). `grep -E 'google-github-actions/auth@v[0-9]' .github/workflows/*.yml` | Every match is `@v3` or higher. |
| **WIF smoke** | After deploy: `gcloud run services describe hermes --region=us-central1 --format='value(spec.template.spec.serviceAccountName)'` AND `gcloud run services describe hermes --region=us-central1 --format='json' \| jq '.spec.template.spec.containers[].env[] \| select(.name == "GOOGLE_APPLICATION_CREDENTIALS")'` | First prints `hermes@autonomous-agent-2026.iam.gserviceaccount.com`; second prints `null` (no env var — WIF uses metadata server). |
| **WIF revoke test** | 1. Delete the WIF pool IAM binding. 2. Issue a Vertex call from hermes → expect 401. 3. Restore. | Demonstrates SA-key removal isn't masked by stale tokens. |

---

### W1.E — Observability + CI smoke gates

| Sub | Command | Expected |
|---|---|---|
| **W1.E-new (import smoke gate)** | `grep -E 'python -c "import lib\.(a2a\|evaluators\|durability)' .github/workflows/ci.yml \| wc -l` | `≥3` |
| **O-1 (OTel meters)** | `grep -rn 'meter\.create_(counter\|histogram\|gauge)' lib/ app/ \| wc -l` | ≥10 after fix (was 1). |
| **O-2 (spanmetrics)** | `grep -c 'spanmetrics' deploy/otel/collector.prod.yaml` | `≥1` |
| **O-3 (real healthcheck)** | `docker compose exec hermes python -c "from lib.healthcheck import run_checks; print(run_checks())"` | All deps probed; output JSON has `vertex`, `honcho`, `chroma`, `cloud_sql`, `memorystore` keys. |
| **O-4 (a2a /health real)** | `curl -sS http://localhost:8080/health \| jq` | JSON with `jti_cache`, `agent_card_signer`, `task_registry` keys (not just `{"status":"ok"}`). |
| **O-5 (watchdog counters)** | `curl -sS http://localhost:8888/metrics \| grep -c 'watchdog_ticks_total'` | `≥1` per loop. |
| **O-6 (structured logs)** | `docker compose logs hermes --tail 50 \| head -5 \| jq -r '.severity'` | Each line parses as JSON. |
| **O-7 (logging.Filter for PII)** | `python -c "import logging; from lib.scrubber import ScrubFilter; l = logging.getLogger('test'); l.addFilter(ScrubFilter()); l.info('jwt=eyJ...token...'); " 2>&1` | Output does NOT contain `eyJ`. |
| **O-8 (long-term log sink)** | `gcloud logging sinks list --project=autonomous-agent-2026 \| grep -c forensic-archive` | `≥1` |
| **O-9 (collector mem limits)** | `grep -E 'limit_mib:' deploy/otel/collector.prod.yaml` | Absolute numeric value, not percentage. |

---

### W1.F — Test coverage gaps

| Sub | Command | Expected |
|---|---|---|
| **T-1 (mypy required)** | `gh api repos/Manzela/AutonomousAgent/branches/main/protection --jq '.required_status_checks.contexts' \| jq -r '.[]' \| grep -c mypy` | `≥1` |
| **T-2 (5 skip tests)** | `grep -rn '@pytest.mark.skip' lib/tests/ app/tests/ \| grep -cE 'chroma_outage\|full_turn\|secret_leak\|budget_cap\|skill_creation'` | `0` after fix. |
| **T-3 (real Redis JTI integ)** | `REDIS_URL=redis://localhost:6379 pytest lib/a2a/tests/test_replay_cache_real_redis.py -x` | Pass. |
| **T-4 (branch protection)** | `gh api repos/Manzela/AutonomousAgent/branches/main/protection --jq '{enforce_admins: .enforce_admins.enabled, reviewers: .required_pull_request_reviews.required_approving_review_count, signed: .required_signatures.enabled}'` | `enforce_admins: true`, `reviewers: ≥1`, `signed: true` |
| **T-5 (secret-scan weekly)** | `grep -A2 schedule: .github/workflows/secret-scan.yml \| grep -c 'cron.*\* \* [0-6]'` | `≥1` (weekly cadence). |

---

### W1.G — App-layer adapter contract

| Sub | Command | Expected |
|---|---|---|
| **A-1 (Orchestrator class exists)** | `python -c "from app.core.orchestrator import Orchestrator; o = Orchestrator(); [getattr(o,m) for m in ['submit','breaker_window','fitness_ema','trajectory_buffer','gc_loop']]"` | No `AttributeError`. |
| **A-2 (no app→lib direct import)** | `grep -rnE '^from lib\.(a2a\|durability)' app/core/` | `0` matches. |
| **A-3 (missing surfaces present)** | `python -c "from app.core import router, judge, reward; [c.__name__ for c in (router.AbstractMoERouter, judge.Judge, reward.AbstractIntrinsicRewardModel)]"` | Prints all 3 names; no `ModuleNotFoundError`. |
| **A-4 (composite index order)** | `psql "$DSN" -c "\\di+ memory_records*" \| grep -E 'tier.*project_id'` | Index column order is `(tier, project_id)`, not `(project_id, tier)`. |

---

### W1.H — Sandbox hardening

| Sub | Command | Expected |
|---|---|---|
| **SB-1 (rlimit fail-closed)** | `pytest app/adapters/inmemory/tests/test_rlimit_fail_closed.py -x` | Pass (an `OSError(EPERM)` on `setrlimit` raises, not warns). |
| **SB-2 (network unshare)** | `pytest app/adapters/inmemory/tests/test_network_blocked.py::test_urlopen_blocked_when_disallowed -x` | Pass — `urllib.request.urlopen('https://example.com')` raises `OSError` (network unreachable). |
| **SB-3 (no build-essential)** | `docker run --rm ghcr.io/manzela/autonomousagent/shell-sandbox:latest which gcc make ld; echo "exit=$?"` | `exit=1` for all three (none present). |
| **SB-4 (seccomp/no-new-privileges)** | `docker compose config \| yq '.services.shell-sandbox.security_opt'` | Includes `no-new-privileges:true` and a `seccomp:` profile path. |

---

### W1.I — Cost machinery + budget watchdog (incl. CC-6 sentinel decision)

| Sub | Command | Expected |
|---|---|---|
| **CC-1 (poll ≤30s + proxy enforcement)** | `grep -E 'POLL_INTERVAL\s*=' scripts/budget_watchdog_loop.py` AND `curl -sS http://localhost:4000/budget/info -H "Authorization: Bearer $LITELLM_MASTER_KEY" \| jq -r '.tags[].max_budget'` | First ≤30; second prints per-tier caps. |
| **CC-2 (cascade single source-of-truth)** | `python scripts/lint_cost_tiers.py --strict` (NEW lint) | Exit 0 — all three caps consistent. |
| **CC-3 (per-task token caps)** | `python -c "import yaml; l = yaml.safe_load(open('config/limits.yaml')); assert l['per_task_input_tokens'] is not None, l"` | Pass. |
| **CC-4 (alert webhook)** | `grep -A1 alert_to_webhook_url deploy/litellm/config.yaml \| grep -cE 'https://'` | `≥1` (no empty string). |
| **CC-5 (always_ask additions)** | `python -c "import yaml; p = yaml.safe_load(open('config/limits.yaml'))['approval']['always_ask_patterns']; [p.index(x) for x in ('gh api -X DELETE','gh repo delete','bq rm','gsutil rm -r')]"` | No `ValueError`. |
| **CC-6 (sentinel decision)** | PR body must declare path α or β. If β: `python -c "from lib.durability.handlers import halt_alert_snapshot; import asyncio; asyncio.run(halt_alert_snapshot(card_id='test', context={}, dispatcher=None)); import os; assert os.path.exists('/data/HALT_F21'), 'sentinel not written'"` AND `pytest lib/anchors/tests/test_halt_sentinel_blocks_tool.py -x`. If α: `grep -rln HALT_F21 lib/ app/ scripts/ docs/ \| wc -l` returns `0` (all references removed). | Per chosen path. |

---

### W1.J — Provision DeepSeek R1 + (optional) Qwen3-30B-A3B [NEW per D-2.c]

| Sub | Command | Expected |
|---|---|---|
| **DeepSeek provider registered** | `curl -sS http://localhost:4000/model/info -H "Authorization: Bearer $LITELLM_MASTER_KEY" \| jq -r '.data[] \| select(.model_name == "deepseek-reasoner") \| .litellm_params.model'` | `deepseek/deepseek-reasoner` |
| **DeepSeek live call** | `curl -sS http://localhost:4000/v1/chat/completions -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H "Content-Type: application/json" -d '{"model":"deepseek-reasoner","messages":[{"role":"user","content":"2+2"}],"max_tokens":16}' \| jq -r '.choices[0].message.content'` | Numeric string containing `4`. |
| **Router un-stub** | `python -c "from lib.router.intent_router import resolve_model; print(resolve_model('deep-math').model)"` | `deepseek/deepseek-reasoner` (NO `NotImplementedError`). |
| **DeepSeek cost tag** | `curl -sS http://localhost:4000/spend/tags -H "Authorization: Bearer $LITELLM_MASTER_KEY" \| jq -r '.[] \| select(.tag == "deep-math") \| .total_spend'` | Numeric value > 0 after the live call. |
| **DeepSeek failover** | Mock a 5xx response in `lib/router/tests/test_deepseek_failover.py::test_falls_back_to_orchestrator_on_5xx` | Pass. |
| **Qwen (if enabled)** | `kubectl get pods -n qwen-vllm -l app=qwen` AND `curl -sS http://qwen-vllm.autonomous-agent-2026.svc.cluster.local:8000/v1/models \| jq -r '.data[0].id'` | Pod `Running`; model id = `Qwen/Qwen3.5-35B-A3B` (or `Qwen/Qwen3-Coder-30B-A3B-Instruct` / `Qwen/Qwen3.5-27B` if operator flipped the alt block). |
| **Qwen privacy tier (when enabled)** | `python -c "from lib.router.intent_router import resolve_model; print(resolve_model('privacy').model)"` (requires `qwen_self_host: true` in feature flags) | `hosted_vllm/Qwen/Qwen3.5-35B-A3B` (or the un-commented alternative — must match the served model). |
| **Qwen disabled by default** | `python -c "import yaml; print(yaml.safe_load(open('config/feature-flags.yaml'))['qwen_self_host'])"` | `False` (until cluster provisioned). |

---

### W1 — cross-stream gate

| Check | Command | Expected |
|---|---|---|
| **All 51 P1s closed** | `grep -cE '^\| (C\|S\|SC\|I\|O\|T\|A\|SB\|CC)-[0-9]+' audit/2026-05-27-ground-truth/findings.md` (P1 row count) AND `git log --oneline main..HEAD \| grep -cE 'closes? (C\|S\|SC\|I\|O\|T\|A\|SB\|CC)-[0-9]+'` | First `51`; second `≥51` (CC-6 reinstated; per-prefix breakdown: C=7, S=3, SC=6, I=7, O=9, T=5, A=4, SB=4, CC=6). |
| **No P0 reintroduced** | `pytest tests/regression/test_no_p0_regression.py -x` (NEW; locks the W0 acceptance tests) | Exit 0 |
| **Per-tier cost telemetry live** | `curl -sS https://monitoring.googleapis.com/v3/projects/autonomous-agent-2026/timeSeries?filter='metric.type="custom.googleapis.com/litellm/tag_spend"' --header "Authorization: Bearer $(gcloud auth print-access-token)" \| jq -r '.timeSeries \| length'` | `≥4` (orchestrator/architect/fast-engineer/researcher tags emitting). |
| **Scorecard score** | `gh run list --workflow=scorecard.yml --limit 1 --json url --jq '.[0].url'` → open in browser | `≥8.0/10` after W1 (was unknown). |
| **MEMORY.md fully reconciled** | `grep -lE 'Opus default\|gemini-3-1-pro$\|deepseek-r1\|qwen-3-5' ~/.claude/projects/-Users-danielmanzela-RX-Research-Project-AutonomousAgent/memory/*.md \| wc -l` | `0` (all model-id drift removed). |

---

### W2 — Backlog verification template

W2 covers the 27 P2 + 11 P3 items in findings.md. Antigravity must NOT batch these into a single PR; each P2 gets its own GitHub issue + branch + PR. The per-issue verification template (must be copied into every W2 issue body):

```markdown
## Verification protocol for P2-N / P3-N

- [ ] **Pre-condition:** Paste the output of the command that reproduces the bug (or the assertion that demonstrates the gap).
- [ ] **Implementation:** Paste the output of `pytest <new test path>` showing the new test PASS.
- [ ] **Post-condition:** Paste the output of the command from "Pre-condition" — it must now show the bug is gone.
- [ ] **Regression (TDD red-green):**
  1. Apply fix → run new test → expect PASS
  2. `git revert HEAD --no-commit` the fix → re-run → expect FAIL
  3. Restore → re-run → expect PASS
- [ ] **Smoke:** Paste end-to-end probe output (compose up + curl / pytest -m integration).
- [ ] **Decisions consulted:** `decisions.md` entries D-N applied.
- [ ] **Cross-reference:** Findings cite (`file:line`) re-verified against current HEAD (no stale line numbers).
```

**W2 batch gates (run weekly):**

| Check | Command | Expected |
|---|---|---|
| **P2/P3 burn-down** | `gh issue list --label P2 --state closed --search "closed:>2026-05-27" --json number \| jq length` AND `gh issue list --label P2 --state open --json number \| jq length` | First trends up; second trends down. |
| **No regression** | `pytest tests/regression/test_no_p0_regression.py tests/regression/test_no_p1_regression.py -x` (NEW) | Exit 0 |
| **Cite-freshness scan** | `python scripts/lint_audit_cites.py audit/2026-05-27-ground-truth/findings.md` (NEW lint that re-resolves every `file:line` cite and asserts the file still exists and the line matches the captured snippet) | Exit 0 |

---

### Verification meta-rules (per `superpowers:verification-before-completion` Iron Law)

1. **No paraphrased success.** Antigravity must NOT write "should pass", "looks correct", "tests will pass". Either the command output is in the PR body or the PR is not ready.
2. **Fresh evidence per PR.** Antigravity cannot reuse evidence from a prior PR — every PR runs its own verification, even if a sibling PR ran the same command earlier.
3. **Failure path is mandatory.** Every PR body must include at least one "this is what failure looks like" — typically the regression test in step 4 of the TDD red-green sequence.
4. **No `|| true` anywhere.** Any verification command suffixed with `|| true`, `2>/dev/null`, or `--continue-on-error` is grounds for PR rejection.
5. **Tool versions are pinned, not loose.** `cosign@v3.0.6`, `slsa-verifier@v2.7.1`, `google-github-actions/auth@v3`, `actions/checkout@v4`, `ossf/scorecard-action@v2.4.3`. Major-version drift between this plan and the live workflow must be flagged in the PR body.
6. **Drift detection.** Before opening any PR, Antigravity must re-grep the cited `file:line` against HEAD and update the cite in the PR body if it has drifted. The corrected line cites in this plan (`config/limits.yaml:153-156`, `lib/a2a/server.py:84`, `phase-0a-deploy.yml:146/:162`) were themselves a real-world drift discovery during this audit's self-review.

---

## Definition of "done" for the audit pass

- [x] Pass 1: codebase-only ground-truth draft landed (`findings.md`).
- [x] Pass 2: 8-subagent fan-out re-verification (synthesis in `findings.md`).
- [x] Self-correction: 2 false-positive P0s identified and retracted in `findings.md`.
- [x] Decisions captured in `decisions.md`.
- [x] Remediation plan in `audit-plan.md` (this file).
- [x] D-5.b mechanism confirmed (option (b)) and applied (`.claudeignore` written).
- [x] Pass 3 approval gate — **user signaled "proceed" 2026-05-27.**
- [x] Drift-purge pass — stale counts (P0/P1) and R-1 retraction wording replaced with authoritative values (`8 active P0` / `51 P1` / `104 TOTAL`); verified 2026-05-27 via grep against patched files.
- [x] Qwen model correction pass (2026-05-27, second pass) — the prior "Qwen 3.5 was never released" claim was a stale-training-data hallucination. Qwen3.5 IS released (Feb-Mar 2026). Privacy tier swapped to `hosted_vllm/Qwen/Qwen3.5-35B-A3B` (newer-gen MoE at same single-A100 footprint). Stubbed two commented alternatives in `model-tiers.yaml` for operator override: `Qwen3-Coder-30B-A3B-Instruct` (coder-tuned, same cost) and `Qwen3.5-27B` (strongest code bench, 8× hardware, must re-escalate). Verified 2026-05-27 via grep — zero residual stale model-id references except in superseded-by context.
- [ ] Handoff to Antigravity Claude Opus 4.6 Thinking — **active step (this brief).**

Per the `/audit` skill governing this workflow: **approval gate satisfied 2026-05-27. Antigravity Claude Opus 4.6 Thinking is authorized to begin W0.1 (judge consensus, closes P0-2) per the read-order and authorized-actions list in `## Handoff packet definition` above.**
