# Audit Decisions — 2026-05-27

Decisions confirmed by the user on 2026-05-27 to govern W0 + W1 execution. **These override anything in `findings.md` or `audit-plan.md` that conflicts.**

---

## D-1. A2A audience contract = **SA email** (option A)

- **Sender** (e.g. hermes calling canary) mints `aud=<peer SA email>` in the outbound JWT.
- **Receiver** (`lib/a2a/auth.py:276`) verifies `aud == <our SA email>`. **No code change needed in `auth.py:276`.**
- Update `config/a2a/peers.yaml:25`: `audience: agent-canary@autonomous-agent-2026.iam.gserviceaccount.com` (replace the URL).
- Startup-time assertion: if any configured peer audience does not look like an email (lacks `@`), log a `WARNING` and refuse to start in `ENVIRONMENT=production`.
- Per-peer integration test (`lib/a2a/tests/test_audience_contract.py`) covers both the green path (SA-email matches) and the red path (URL is rejected).

## D-2. Model strategy = **multi-vendor per-task routing**, NOT single-default

This supersedes audit-plan §W0.8 ("default to Sonnet 4.6"). The new policy is a **routing matrix keyed by task intent**, with the orchestrator using a frontier reasoning model and coding executions routed by domain.

### D-2.a Tier matrix (canonical)

| Tier | Intent / Use-case | Model | Provider | Notes |
|---|---|---|---|---|
| **orchestrator** | Top-level reasoning, planning, intent classification, judge-panel orchestration | **Gemini 3.1 Pro (High)** | Vertex AI | `vertex_ai/gemini-3-1-pro-preview` on `global` endpoint; `max_tokens ≥ 2048`; thinking-mode `HIGH`. Per memory `gemini_3_1_pro_preview_quirks.md`. |
| **architect** | Architecture, planning, code review, complex refactor design | **Claude Opus 4.7** | Vertex AI | `vertex_ai/claude-opus-4-7`. Highest-quality logic; reserve for safety-critical / design decisions. |
| **fast-engineer** | Autocomplete, small fixes, tight inner-loop edits, file-by-file mechanical changes | **Gemini 3.5 Flash** | Vertex AI | `vertex_ai/gemini-3-5-flash`. Released 2026-05-19. Verify Vertex availability at integration time. |
| **researcher / large-context** | Legacy code ingestion, massive docs, multi-file reading, doc-grounded answers | **Gemini 3.1 Pro** | Vertex AI | Same model ID as orchestrator; can share quota pool. Different `intent_category` for routing+telemetry. |
| **deep-math / algorithmic** | Performance tuning, complex math, algorithmic optimization, data-science | **DeepSeek R1** | LiteLLM direct provider | LiteLLM canonical id: `deepseek/deepseek-reasoner` (NOT `deepseek-r1` — that name is not a valid LiteLLM identifier; the R1 reasoning model is exposed as `deepseek-reasoner`). API base: `https://api.deepseek.com`. Requires API key in `secrets/deepseek.env.sops`. Reference: https://docs.litellm.ai/docs/providers/deepseek (verified 2026-05-27). |
| **privacy / self-host** | Self-hosted code execution where data must not leave a controlled boundary | **Qwen3-Coder-30B-A3B-Instruct** | Self-hosted (vLLM) | LiteLLM canonical id: `hosted_vllm/Qwen/Qwen3-Coder-30B-A3B-Instruct`. **User decision 2026-05-28**: switched from Qwen3.5-35B-A3B MoE back to Qwen3-Coder because coder specialization directly matches the use-case (local code execution, data stays inside boundary). Qwen3 generation; purpose-built for agentic coding; dedicated `qwen3_coder` tool-call format; same single-A100 80GB footprint. The LiteLLM prefix is `hosted_vllm/` (NOT `openai/`; the `vllm/` prefix is deprecated). **No `Qwen3.5-Coder` variant exists** — coder-tuning lives only on the Qwen3 generation. Alternatives stubbed in `config/hermes/model-tiers.yaml` as commented blocks: (alt-b) `Qwen/Qwen3.5-35B-A3B` — newer-gen general-instruct MoE (Feb-Mar 2026), 256 experts / 3B active, 256K context, same single-A100 footprint; (alt-c) `Qwen/Qwen3.5-27B` (dense) — strongest published code bench (SWE-Bench Verified 72.4, LiveCodeBench v6 80.7) but requires **8× A100 80GB** tensor-parallel serving (~10× cost). Default is OFF until vLLM cluster provisioned. Reference: https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct + https://docs.litellm.ai/docs/providers/vllm (verified 2026-05-27). |

### D-2.b Routing rules

1. Every Hermes invocation declares a `task_intent` ∈ {orchestrator, architect, fast-engineer, researcher, deep-math, privacy}.
2. The router in `lib/router/intent_router.py` (NEW — see W1.J below) maps `task_intent` → model id per the table above.
3. If `task_intent` is missing or invalid, fail-CLOSED to `orchestrator` (Gemini 3.1 Pro). Do NOT silently default to Opus.
4. Per-tier daily cost cap configured in `config/budget-policy.yaml`; enforced at LiteLLM proxy via per-tag `max_budget`. Cascade per W1.I.

### D-2.c W0 vs W1 split for D-2

The full matrix is too much for a 48-h crisis pass. Split:

- **W0.8 (in crisis pass):** Switch `config/hermes/cli-config.yaml:22` default model from `vertex_ai/claude-opus-4-7` → `vertex_ai/gemini-3-1-pro-preview`. Add the model-tier YAML (`config/hermes/model-tiers.yaml`) with the matrix above, **but only wire the three Vertex tiers** (orchestrator, architect, fast-engineer). DeepSeek and Qwen are stubbed in the YAML for documentation; the router falls back to orchestrator if those tiers are requested.
- **W1.J (new work-stream, ~3 days):** Provision DeepSeek R1 + (optionally) `Qwen/Qwen3-Coder-30B-A3B-Instruct` (user decision 2026-05-28: coder-specialization preferred for privacy tier; see D-2.a for alternatives); add per-provider secret + cost-cap + observability; un-stub the router's deep-math and privacy paths; add provider-failover policy.

### D-2.d Cost cascade implication

Gemini 3.1 Pro (orchestrator) is materially cheaper than Opus 4.7 per million tokens, but generates longer responses on reasoning tasks. Net daily projection: **~2–4× cheaper than current Opus default**, but the cost cascade lint in W1.I must include per-tier rate-cap accounting, not just total $/day.

## D-3. FirecrackerSandbox = **fail-closed stub now + real H1 later** (approved)

- `app/adapters/gcp/sandbox.py` lands in W0.5 as `FirecrackerSandbox(AbstractSandbox)` with `is_production_grade=True` and `__init__` raises `NotImplementedError("H1: Firecracker tier not yet provisioned — file issue per docs/architecture/h1-firecracker-provision.md")`.
- Production startup with this stub aborts cleanly with an operator-actionable error.
- A separate ticket "H1 Firecracker provision" tracks the ~$265/mo GCP N2 nested-virt buildout. **Not a W0 or W1 deliverable.**

## D-4. Workload Identity Federation = **per-service SA keys in W0; WIF migration in W1** (approved)

- W0.7 ships per-service SA keys (`secrets/sa-keys/<service>.json.sops`).
- Each container reads `GOOGLE_APPLICATION_CREDENTIALS=/secrets/sa-key.json` from a per-service RO bind.
- W1.D follow-up adds a WIF migration: deploy hermes + sidecars to Cloud Run or GKE, drop SA keys entirely, use metadata-server identity tokens. Tracked as `W1.D.I-8` (new sub-item).

## D-5. Scope = **do not touch `hermes-agent/` submodule or `docs/`**; prevent stale content from polluting future LLM context

### D-5.a Scope exclusion (confirmed)

- W0 + W1 do NOT modify files under `hermes-agent/` or `docs/`.
- All audit findings related to those paths are deferred to a separate workstream owned by Hermes maintainers.

### D-5.b Anti-hallucination mechanism (CONFIRMED 2026-05-27: option (b))

User instruction: "Add them to gitignore and don't deploy them so we don't have knowledge cut-offs and hallucinations next time an LLM or reviewer is reading the docs and files. It must be clean."

**Resolution:** User selected **option (b) — `.claudeignore` context-exclusion**. Applied at repo root in commit accompanying this update. `docs/` and `hermes-agent/` remain tracked in git and shipped in deploys (hermes-agent/ is a runtime-active submodule); they are excluded ONLY from the LLM context loader.

Literal `.gitignore` would have been wrong (would un-track files actively in use). The three mechanisms considered:

| Option | What it does | Cost | Side-effects |
|---|---|---|---|
| **(a) `.dockerignore` exclusion** | `docs/` and `hermes-agent/` are excluded from deploy images. Repo history preserved. Future LLMs still see them when reading the repo. | Trivial. | Does NOT solve LLM-context pollution; only solves "shipping docs to prod containers." |
| **(b) `.claudeignore` / settings.json contextExclude** | Tell Claude Code (and Antigravity's Claude session) to skip `docs/` + `hermes-agent/` when loading codebase context. Repo history preserved. | Trivial. | Solves LLM hallucination from stale docs. Does nothing for human readers. **RECOMMENDED for the audit handoff.** |
| **(c) Physical move to `archive/`** | `git mv docs/ archive/docs/` + `git mv hermes-agent/ archive/hermes-agent/`. Top-level `docs/` becomes a 1-file "STALE — see archive/" pointer. | Higher: breaks any existing absolute paths in CI/code. | Most aggressive; safest for context hygiene. **Risky for `hermes-agent/` if it's a runtime dependency (which it likely is).** |

**Caveat on `hermes-agent/`:** this is a **runtime-active submodule** (the Hermes driver is imported by the orchestrator). It cannot be physically moved or excluded from deploy without breaking the agent. The anti-hallucination mechanism for `hermes-agent/` is option (b) only — context-exclusion, not deploy-exclusion.

**Applied:** `.claudeignore` at repo root (see file for the exact patterns + rationale comment). No `.dockerignore` change; no physical move. Antigravity's Claude session will skip `docs/` and `hermes-agent/` automatically.

---

## Handoff target

These decisions + `findings.md` + `audit-plan.md` are the input packet for **Antigravity Claude Opus 4.6 Thinking** to execute W0.

See chat reply for handoff readiness assessment.
